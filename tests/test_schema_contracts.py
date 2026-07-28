import json

from app.schemas import InvoiceSchema, LineItem


def make_invoice(**overrides) -> InvoiceSchema:
    defaults = dict(
        vendor="Acme Ltd",
        invoice_number="INV-001",
        date="2026-07-01",
        currency="USD",
        subtotal=100.0,
        tax=10.0,
        total=110.0,
        line_items=[
            LineItem(description="Widget", quantity=2, unit_price=50.0, amount=100.0, confidence=0.95)
        ],
        overall_confidence=0.9,
    )
    defaults.update(overrides)
    return InvoiceSchema(**defaults)


def test_invoice_round_trips_through_json():
    invoice = make_invoice()
    serialized = invoice.model_dump_json()
    restored = InvoiceSchema.model_validate(json.loads(serialized))
    assert restored == invoice


def test_invoice_requires_non_empty_date():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        make_invoice(date="")


def test_confidence_bounds_enforced():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        make_invoice(overall_confidence=1.5)

    with pytest.raises(ValidationError):
        make_invoice(overall_confidence=-0.1)


def test_min_field_confidence_picks_lowest():
    invoice = make_invoice(
        overall_confidence=0.9,
        line_items=[
            LineItem(description="A", quantity=1, unit_price=1, amount=1, confidence=0.3),
            LineItem(description="B", quantity=1, unit_price=1, amount=1, confidence=0.99),
        ],
    )
    assert invoice.min_field_confidence() == 0.3


def test_low_confidence_fields_flags_correct_entries():
    invoice = make_invoice(
        overall_confidence=0.9,
        line_items=[
            LineItem(description="A", quantity=1, unit_price=1, amount=1, confidence=0.3),
            LineItem(description="B", quantity=1, unit_price=1, amount=1, confidence=0.99),
        ],
    )
    flagged = invoice.low_confidence_fields(threshold=0.75)
    assert len(flagged) == 1
    assert flagged[0]["field"] == "line_items[0].description"
