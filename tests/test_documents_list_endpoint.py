from fastapi.testclient import TestClient


def _setup_client(tmp_path, suffix):
    import os

    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path}/test_list_{suffix}.db"
    os.environ["UPLOADS_DIR"] = str(tmp_path / f"uploads_list_{suffix}")

    import importlib
    import app.config as config_module
    import app.db as db_module
    import app.main as main_module

    importlib.reload(config_module)
    importlib.reload(db_module)
    importlib.reload(main_module)
    return main_module


def _tiny_png():
    import io
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (20, 20), color="white").save(buf, format="PNG")
    return buf.getvalue()


def test_documents_list_filters_by_status(tmp_path):
    """GET /documents backs the Accepted/Rejected UI views (VALIDATION_PLAN.md
    section 5.2): approved and rejected documents must actually be
    browsable, not just removed from /review."""
    main_module = _setup_client(tmp_path, "filter")

    from app.schemas import InvoiceSchema, ModerationResult
    from unittest.mock import patch

    with TestClient(main_module.app) as client:
        low_conf_invoice = InvoiceSchema(
            vendor="Vendor A", invoice_number=None, date="2026-01-01", currency="USD",
            subtotal=1.0, tax=0.0, total=1.0, line_items=[], overall_confidence=0.4,
        )

        with patch.object(
            main_module, "screen_image", return_value=ModerationResult(verdict="allow", max_score=0.01)
        ), patch.object(main_module, "extract_invoice", return_value=(low_conf_invoice, 0.001)):
            approve_target = client.post("/ingest", files={"file": ("a.png", _tiny_png(), "image/png")}).json()
            reject_target = client.post("/ingest", files={"file": ("b.png", _tiny_png(), "image/png")}).json()

        client.post("/approve", json={"document_id": approve_target["document_id"], "field_updates": []})
        client.post("/reject", json={"document_id": reject_target["document_id"]})

        approved = client.get("/documents", params={"status": "approved"}).json()
        rejected = client.get("/documents", params={"status": "rejected"}).json()
        everything = client.get("/documents").json()

        assert any(d["document_id"] == approve_target["document_id"] for d in approved)
        assert not any(d["document_id"] == reject_target["document_id"] for d in approved)

        assert any(d["document_id"] == reject_target["document_id"] for d in rejected)
        assert not any(d["document_id"] == approve_target["document_id"] for d in rejected)

        assert len(everything) >= 2


def test_documents_list_empty_when_no_documents(tmp_path):
    main_module = _setup_client(tmp_path, "empty")

    with TestClient(main_module.app) as client:
        assert client.get("/documents").json() == []
        assert client.get("/documents", params={"status": "approved"}).json() == []
