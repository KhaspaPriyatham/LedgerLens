import json

from fastapi.testclient import TestClient


def test_approve_writes_line_item_correction_into_structured_data(tmp_path):
    """Regression test for VALIDATION_PLAN.md section 6.2: a reviewer's
    correction to a flagged line item must be written into the structured
    line_items array on the stored record, not just appended to the
    _reviewer_corrections audit trail."""
    import os

    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path}/test_line_item.db"
    os.environ["UPLOADS_DIR"] = str(tmp_path / "uploads_line_item")

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
        Image.new("RGB", (30, 30), color="white").save(buf, format="PNG")
        img_bytes = buf.getvalue()

        invoice = InvoiceSchema(
            vendor="Acme Ltd",
            invoice_number="INV-1",
            date="2026-01-01",
            currency="USD",
            subtotal=10.0,
            tax=1.0,
            total=11.0,
            line_items=[
                LineItem(description="Widgit", quantity=1, unit_price=10, amount=10, confidence=0.3),
            ],
            overall_confidence=0.9,
        )

        with patch.object(
            main_module, "screen_image", return_value=ModerationResult(verdict="allow", max_score=0.01)
        ), patch.object(main_module, "extract_invoice", return_value=(invoice, 0.001)):
            ingest_resp = client.post("/ingest", files={"file": ("r.png", img_bytes, "image/png")})
            assert ingest_resp.status_code == 200
            body = ingest_resp.json()
            doc_id = body["document_id"]
            assert body["status"] == "pending_review"
            assert body["flagged_fields"] == [{"field": "line_items[0].description", "confidence": 0.3}]

        approve_resp = client.post(
            "/approve",
            json={
                "document_id": doc_id,
                "field_updates": [
                    {"field_path": "line_items[0].description", "corrected_value": "Widget"},
                ],
            },
        )
        assert approve_resp.status_code == 200

        doc_resp = client.get(f"/documents/{doc_id}")
        record = json.loads(doc_resp.json()["reviewed_json"])

        # The structured line_items array itself must reflect the correction ...
        assert record["line_items"][0]["description"] == "Widget"
        # ... and the audit trail must still record it happened.
        assert any(
            c["field"] == "line_items[0].description" and c["corrected_value"] == "Widget"
            for c in record["_reviewer_corrections"]
        )


def test_approve_ignores_out_of_range_line_item_index(tmp_path):
    """A correction pointing at a line-item index that doesn't exist must
    not raise -- it should be a no-op on the structured data (still logged
    to the audit trail) rather than a 500."""
    import os

    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path}/test_line_item2.db"
    os.environ["UPLOADS_DIR"] = str(tmp_path / "uploads_line_item2")

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
        Image.new("RGB", (30, 30), color="white").save(buf, format="PNG")
        img_bytes = buf.getvalue()

        invoice = InvoiceSchema(
            vendor="Acme Ltd", invoice_number=None, date="2026-01-01", currency="USD",
            subtotal=0.0, tax=0.0, total=0.0, line_items=[], overall_confidence=0.4,
        )

        with patch.object(
            main_module, "screen_image", return_value=ModerationResult(verdict="allow", max_score=0.01)
        ), patch.object(main_module, "extract_invoice", return_value=(invoice, 0.001)):
            ingest_resp = client.post("/ingest", files={"file": ("r.png", img_bytes, "image/png")})
            doc_id = ingest_resp.json()["document_id"]

        approve_resp = client.post(
            "/approve",
            json={
                "document_id": doc_id,
                "field_updates": [
                    {"field_path": "line_items[0].description", "corrected_value": "Widget"},
                ],
            },
        )
        assert approve_resp.status_code == 200
