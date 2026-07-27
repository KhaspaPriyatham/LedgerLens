from fastapi.testclient import TestClient


def test_approve_increments_reviewed_metric(monkeypatch, tmp_path):
    import os

    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path}/test3.db"
    os.environ["UPLOADS_DIR"] = str(tmp_path / "uploads3")

    import importlib
    import app.config as config_module
    import app.db as db_module
    import app.main as main_module

    importlib.reload(config_module)
    importlib.reload(db_module)
    importlib.reload(main_module)

    from app.schemas import InvoiceSchema, ModerationResult
    from unittest.mock import patch
    import io
    from PIL import Image
    from prometheus_client import generate_latest

    with TestClient(main_module.app) as client:
        buf = io.BytesIO()
        Image.new("RGB", (50, 50), color="white").save(buf, format="PNG")
        img_bytes = buf.getvalue()

        low_conf_invoice = InvoiceSchema(
            vendor="Cafe X",
            invoice_number=None,
            date="2026-01-01",
            currency="USD",
            subtotal=5.0,
            tax=0.5,
            total=5.5,
            line_items=[],
            overall_confidence=0.4,
        )

        with patch.object(
            main_module, "screen_image", return_value=ModerationResult(verdict="allow", max_score=0.01)
        ), patch.object(main_module, "extract_invoice", return_value=(low_conf_invoice, 0.001)):
            ingest_resp = client.post("/ingest", files={"file": ("r.png", img_bytes, "image/png")})
            doc_id = ingest_resp.json()["document_id"]
            assert ingest_resp.json()["status"] == "pending_review"

        approve_resp = client.post("/approve", json={"document_id": doc_id, "field_updates": [], "reviewer": "Bob"})
        assert approve_resp.status_code == 200
        assert approve_resp.json()["status"] == "approved"

        # confirm removed from queue
        review_resp = client.get("/review")
        assert not any(d["document_id"] == doc_id for d in review_resp.json())

        # confirm the Prometheus counter incremented
        metrics_text = generate_latest(main_module.registry).decode("utf-8")
        assert 'documents_reviewed_total{outcome="approved"} 1.0' in metrics_text
