import time
from collections import deque
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.config import UPLOADS_DIR, REVIEW_THRESHOLD
from app.db import init_db, get_db, Document, ReviewQueueItem, new_id
from app.schemas import (
    IngestResponse,
    DocumentRecord,
    ApproveRequest,
    RejectRequest,
)
from app.moderation import screen_image
from app.extraction import extract_invoice
from app.review_router import route
from app.watermark import apply_watermark
from app.pii import redact, configure_redacted_logging
from app.metrics import (
    registry,
    documents_ingested_total,
    documents_reviewed_total,
    auto_approval_rate,
    throughput_docs_per_minute,
)
from prometheus_client import generate_latest

logger = configure_redacted_logging()

app = FastAPI(title="LedgerLens", version="0.1.0")

# rolling window of ingest timestamps + auto-approval outcomes, for the
# throughput / auto-approval-rate gauges (in-memory, fine for MVP/single
# replica; see README for the multi-replica caveat)
_recent_ingest_times: deque[float] = deque(maxlen=500)
_recent_outcomes: deque[bool] = deque(maxlen=200)  # True = auto_approved


@app.on_event("startup")
def on_startup():
    init_db()
    logger.info("LedgerLens API started, Prometheus registry ready")


def _update_rolling_metrics():
    now = time.monotonic()
    _recent_ingest_times.append(now)
    window = [t for t in _recent_ingest_times if now - t <= 60]
    throughput_docs_per_minute.set(len(window))

    if _recent_outcomes:
        auto_approval_rate.set(sum(_recent_outcomes) / len(_recent_outcomes))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(registry), media_type="text/plain; version=0.0.4")


@app.post("/ingest", response_model=IngestResponse)
async def ingest(file: UploadFile = File(...), db: Session = Depends(get_db)):
    image_bytes = await file.read()
    doc_id = new_id()

    # 1. MODERATION GATE - always runs before any vision-model call.
    try:
        moderation = screen_image(image_bytes)
    except Exception as exc:  # pragma: no cover - network/SDK/auth failure path
        logger.error("Moderation call failed for upload %s: %s", doc_id, str(exc))
        raise HTTPException(
            status_code=502,
            detail=f"Moderation check failed: {type(exc).__name__}: {exc}",
        ) from exc

    if moderation.verdict == "block":
        logger.info("Document %s blocked by moderation: %s", doc_id, moderation.reason)
        doc = Document(
            id=doc_id,
            filename=redact(file.filename or "unknown"),
            status="blocked",
            moderation_verdict=moderation.verdict,
            moderation_max_score=moderation.max_score,
        )
        db.add(doc)
        db.commit()
        documents_ingested_total.labels(status="blocked").inc()
        _update_rolling_metrics()
        raise HTTPException(
            status_code=422,
            detail={"blocked_reason": moderation.reason, "document_id": doc_id},
        )

    # 2. VISION EXTRACTION
    try:
        invoice, cost_usd = extract_invoice(image_bytes)
    except Exception as exc:  # pragma: no cover - network/SDK failure path
        logger.error("Extraction failed for %s: %s", doc_id, str(exc))
        raise HTTPException(
            status_code=502,
            detail=f"Extraction failed: {type(exc).__name__}: {exc}",
        ) from exc

    # 3. CONFIDENCE ROUTING
    status, flagged_fields = route(invoice, REVIEW_THRESHOLD)

    # 4. WATERMARK + STORE
    watermarked = apply_watermark(image_bytes, doc_id)
    doc_dir = Path(UPLOADS_DIR) / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)
    image_path = doc_dir / "watermarked.png"
    image_path.write_bytes(watermarked)

    doc = Document(
        id=doc_id,
        filename=redact(file.filename or "unknown"),
        status=status,
        vendor=invoice.vendor,
        total=invoice.total,
        currency=invoice.currency,
        moderation_verdict=moderation.verdict,
        moderation_max_score=moderation.max_score,
        extracted_json=redact(invoice.model_dump_json()),
        cost_usd=cost_usd,
        image_path=str(image_path),
    )
    db.add(doc)

    for field in flagged_fields:
        db.add(
            ReviewQueueItem(
                document_id=doc_id,
                field_path=field["field"],
                original_confidence=field["confidence"],
            )
        )

    db.commit()

    documents_ingested_total.labels(status=status).inc()
    _recent_outcomes.append(status == "auto_approved")
    _update_rolling_metrics()

    logger.info("Document %s ingested with status=%s cost=$%.4f", doc_id, status, cost_usd)

    return IngestResponse(
        document_id=doc_id,
        status=status,
        extracted=invoice,
        flagged_fields=flagged_fields,
        moderation=moderation,
        cost_usd=cost_usd,
    )


@app.get("/review")
def get_review_queue(db: Session = Depends(get_db)):
    """Return all documents currently pending human review, with their
    flagged fields, for the Streamlit reviewer UI."""
    docs = db.query(Document).filter(Document.status == "pending_review").all()
    result = []
    for doc in docs:
        items = (
            db.query(ReviewQueueItem)
            .filter(ReviewQueueItem.document_id == doc.id, ReviewQueueItem.status == "pending")
            .all()
        )
        result.append(
            {
                "document_id": doc.id,
                "filename": doc.filename,
                "vendor": doc.vendor,
                "total": doc.total,
                "currency": doc.currency,
                "moderation_verdict": doc.moderation_verdict,
                "created_at": doc.created_at.isoformat() if doc.created_at else None,
                "extracted_json": doc.extracted_json,
                "image_path": doc.image_path,
                "flagged_fields": [
                    {"field_path": i.field_path, "confidence": i.original_confidence}
                    for i in items
                ],
            }
        )
    return result


@app.get("/documents/{document_id}", response_model=DocumentRecord)
def get_document(document_id: str, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return DocumentRecord(
        id=doc.id,
        filename=doc.filename,
        status=doc.status,
        vendor=doc.vendor,
        total=doc.total,
        currency=doc.currency,
        moderation_verdict=doc.moderation_verdict,
        extracted_json=doc.extracted_json,
        reviewed_json=doc.reviewed_json,
        created_at=doc.created_at,
        image_path=doc.image_path,
    )


@app.post("/approve")
def approve(request: ApproveRequest, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == request.document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    import json

    record = json.loads(doc.extracted_json) if doc.extracted_json else {}
    for update in request.field_updates:
        # Supports top-level fields (e.g. "vendor") and dotted line-item
        # paths as produced by low_confidence_fields(); simple top-level
        # correction is applied directly, nested corrections are logged
        # for the reviewer audit trail.
        if "." not in update.field_path and update.field_path in record:
            record[update.field_path] = update.corrected_value
        record.setdefault("_reviewer_corrections", []).append(
            {"field": update.field_path, "corrected_value": redact(update.corrected_value)}
        )

    doc.reviewed_json = redact(json.dumps(record))
    doc.status = "approved"
    db.commit()

    db.query(ReviewQueueItem).filter(
        ReviewQueueItem.document_id == request.document_id
    ).update({"status": "resolved"})
    db.commit()

    documents_reviewed_total.labels(outcome="approved").inc()

    logger.info("Document %s approved by reviewer=%s", request.document_id, request.reviewer or "unknown")

    return {"document_id": request.document_id, "status": "approved"}


@app.post("/reject")
def reject(request: RejectRequest, db: Session = Depends(get_db)):
    """Reject a document sitting in the review queue. Since /review only
    returns documents with status == "pending_review", setting the status
    to "rejected" here removes it from the queue immediately."""
    doc = db.query(Document).filter(Document.id == request.document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    doc.status = "rejected"
    db.commit()

    db.query(ReviewQueueItem).filter(
        ReviewQueueItem.document_id == request.document_id
    ).update({"status": "resolved"})
    db.commit()

    documents_reviewed_total.labels(outcome="rejected").inc()

    logger.info(
        "Document %s rejected by reviewer=%s reason=%s",
        request.document_id,
        request.reviewer or "unknown",
        redact(request.reason or "not specified"),
    )

    return {"document_id": request.document_id, "status": "rejected"}
