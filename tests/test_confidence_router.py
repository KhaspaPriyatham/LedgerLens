from app.schemas import InvoiceSchema, LineItem
from app.review_router import route


def make_invoice(overall_confidence, line_confidences):
    return InvoiceSchema(
        vendor="Test Vendor",
        invoice_number="X-1",
        date="2026-07-01",
        currency="USD",
        subtotal=10.0,
        tax=1.0,
        total=11.0,
        line_items=[
            LineItem(description=f"item{i}", quantity=1, unit_price=1, amount=1, confidence=c)
            for i, c in enumerate(line_confidences)
        ],
        overall_confidence=overall_confidence,
    )


def test_high_confidence_auto_approves():
    invoice = make_invoice(overall_confidence=0.95, line_confidences=[0.9, 0.99])
    status, flagged = route(invoice, threshold=0.75)
    assert status == "auto_approved"
    assert flagged == []


def test_low_confidence_field_routes_to_pending_review():
    invoice = make_invoice(overall_confidence=0.95, line_confidences=[0.5, 0.99])
    status, flagged = route(invoice, threshold=0.75)
    assert status == "pending_review"
    assert len(flagged) == 1
    # Machine-parseable path, not the item's own description text -- this is
    # what /approve parses back out to write corrections into the
    # structured line_items array (see app/schemas.py low_confidence_fields).
    assert flagged[0]["field"] == "line_items[0].description"


def test_low_overall_confidence_routes_to_pending_review():
    invoice = make_invoice(overall_confidence=0.4, line_confidences=[0.9])
    status, flagged = route(invoice, threshold=0.75)
    assert status == "pending_review"
    assert any(f["field"] == "overall" for f in flagged)


def test_threshold_boundary_is_exclusive_below():
    # confidence exactly at threshold should NOT be flagged (>= passes)
    invoice = make_invoice(overall_confidence=0.75, line_confidences=[0.75])
    status, flagged = route(invoice, threshold=0.75)
    assert status == "auto_approved"
    assert flagged == []
