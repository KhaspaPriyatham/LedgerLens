from fastapi.testclient import TestClient


def test_reject_removes_document_from_review_queue(monkeypatch, tmp_path):
    import os

    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path}/test.db"
    os.environ["UPLOADS_DIR"] = str(tmp_path / "uploads")

    # Re-import fresh so config/db pick up the tmp_path env vars.
    import importlib
    import app.config as config_module
    import app.db as db_module
    import app.main as main_module

    importlib.reload(config_module)
    importlib.reload(db_module)
    importlib.reload(main_module)

    from app.schemas import InvoiceSchema, LineItem, ModerationResult
    from unittest.mock import patch
    import io
    from PIL import Image

    with TestClient(main_module.app) as client:
        buf = io.BytesIO()
        Image.new("RGB", (50, 50), color="white").save(buf, format="PNG")
        img_bytes = buf.getvalue()

        low_conf_invoice = InvoiceSchema(
            vendor="Unrecognized document",
            invoice_number=None,
            date="unknown",
            currency="USD",
            subtotal=0.0,
            tax=0.0,
            total=0.0,
            line_items=[],
            overall_confidence=0.0,
        )

        with patch.object(
            main_module, "screen_image", return_value=ModerationResult(verdict="allow", max_score=0.01)
        ), patch.object(main_module, "extract_invoice", return_value=(low_conf_invoice, 0.001)):
            ingest_resp = client.post("/ingest", files={"file": ("weird.png", img_bytes, "image/png")})
            assert ingest_resp.status_code == 200
            doc_id = ingest_resp.json()["document_id"]
            assert ingest_resp.json()["status"] == "pending_review"

        # confirm it's in the review queue before rejecting
        review_resp = client.get("/review")
        assert any(d["document_id"] == doc_id for d in review_resp.json())

        # reject it
        reject_resp = client.post("/reject", json={"document_id": doc_id, "reviewer": "Alice"})
        assert reject_resp.status_code == 200
        assert reject_resp.json()["status"] == "rejected"

        # confirm the Prometheus counter for reviewer decisions incremented
        from prometheus_client import generate_latest
        metrics_text = generate_latest(main_module.registry).decode("utf-8")
        assert 'documents_reviewed_total{outcome="rejected"} 1.0' in metrics_text

        # confirm it's gone from the review queue
        review_resp_after = client.get("/review")
        assert not any(d["document_id"] == doc_id for d in review_resp_after.json())

        # confirm the document record itself reflects the rejection
        doc_resp = client.get(f"/documents/{doc_id}")
        assert doc_resp.json()["status"] == "rejected"


def test_reject_nonexistent_document_returns_404(monkeypatch, tmp_path):
    import os

    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path}/test2.db"
    os.environ["UPLOADS_DIR"] = str(tmp_path / "uploads2")

    import importlib
    import app.config as config_module
    import app.db as db_module
    import app.main as main_module

    importlib.reload(config_module)
    importlib.reload(db_module)
    importlib.reload(main_module)

    with TestClient(main_module.app) as client:
        resp = client.post("/reject", json={"document_id": "does-not-exist"})
        assert resp.status_code == 404
