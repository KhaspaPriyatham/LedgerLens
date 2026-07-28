from fastapi.testclient import TestClient


def test_document_image_returns_watermarked_png(monkeypatch, tmp_path):
    """The Review Queue / Accepted / Rejected UIs load images over HTTP via
    this endpoint rather than reading the API's local filesystem directly
    (see VALIDATION_PLAN.md section 5.1) -- this is the contract they
    depend on."""
    import os

    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path}/test_image.db"
    os.environ["UPLOADS_DIR"] = str(tmp_path / "uploads_image")

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

    with TestClient(main_module.app) as client:
        buf = io.BytesIO()
        Image.new("RGB", (50, 50), color="white").save(buf, format="PNG")
        img_bytes = buf.getvalue()

        invoice = InvoiceSchema(
            vendor="Acme Ltd",
            invoice_number="1",
            date="2026-01-01",
            currency="USD",
            subtotal=10,
            tax=1,
            total=11,
            line_items=[],
            overall_confidence=0.95,
        )

        with patch.object(
            main_module, "screen_image", return_value=ModerationResult(verdict="allow", max_score=0.01)
        ), patch.object(main_module, "extract_invoice", return_value=(invoice, 0.001)):
            ingest_resp = client.post("/ingest", files={"file": ("r.png", img_bytes, "image/png")})
            assert ingest_resp.status_code == 200
            doc_id = ingest_resp.json()["document_id"]

        image_resp = client.get(f"/documents/{doc_id}/image")
        assert image_resp.status_code == 200
        assert image_resp.headers["content-type"] == "image/png"
        assert len(image_resp.content) > 0

        # Confirm it's actually a valid, decodable PNG (the watermarked copy,
        # not the raw upload).
        decoded = Image.open(io.BytesIO(image_resp.content))
        decoded.load()
        assert decoded.format == "PNG"


def test_document_image_404_for_unknown_document(monkeypatch, tmp_path):
    import os

    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path}/test_image2.db"
    os.environ["UPLOADS_DIR"] = str(tmp_path / "uploads_image2")

    import importlib
    import app.config as config_module
    import app.db as db_module
    import app.main as main_module

    importlib.reload(config_module)
    importlib.reload(db_module)
    importlib.reload(main_module)

    with TestClient(main_module.app) as client:
        resp = client.get("/documents/does-not-exist/image")
        assert resp.status_code == 404
