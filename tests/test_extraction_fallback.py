import io
from types import SimpleNamespace
from unittest.mock import MagicMock

from PIL import Image

from app.extraction import extract_invoice
from app.review_router import route


def _tiny_png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color="white").save(buf, format="PNG")
    return buf.getvalue()


def _fake_completion(parsed=None, refusal=None, prompt_tokens=100, completion_tokens=50):
    message = SimpleNamespace(parsed=parsed, refusal=refusal)
    choice = SimpleNamespace(message=message)
    usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    return SimpleNamespace(choices=[choice], usage=usage)


def test_extract_invoice_falls_back_when_model_refuses(monkeypatch):
    """A non-receipt image (e.g. a selfie) can cause the model to refuse
    rather than return a parsed InvoiceSchema. This must not crash --
    it should fall back to a zero-confidence stub so the document still
    flows into the review queue instead of blowing up with a 500."""
    fake_client = MagicMock()
    fake_client.beta.chat.completions.parse.return_value = _fake_completion(
        parsed=None, refusal="This image does not appear to be a receipt or invoice."
    )

    invoice, cost = extract_invoice(_tiny_png_bytes(), client=fake_client)

    assert invoice is not None
    assert invoice.overall_confidence == 0.0
    assert invoice.vendor == "Unrecognized document"


def test_extract_invoice_falls_back_when_parsed_is_none_without_refusal_text(monkeypatch):
    """Same fallback path, but covering the case where parsed is None and
    there's no refusal message at all (still must not crash)."""
    fake_client = MagicMock()
    fake_client.beta.chat.completions.parse.return_value = _fake_completion(parsed=None, refusal=None)

    invoice, cost = extract_invoice(_tiny_png_bytes(), client=fake_client)

    assert invoice is not None
    assert invoice.overall_confidence == 0.0


def test_fallback_invoice_routes_to_pending_review():
    """The zero-confidence fallback must flow through the normal
    confidence router into pending_review, exactly like any other
    low-confidence extraction -- preserving prior behavior instead of
    crashing the request."""
    fake_client = MagicMock()
    fake_client.beta.chat.completions.parse.return_value = _fake_completion(parsed=None)

    invoice, _ = extract_invoice(_tiny_png_bytes(), client=fake_client)
    status, flagged = route(invoice, threshold=0.75)

    assert status == "pending_review"
    assert len(flagged) >= 1


def test_extract_invoice_still_returns_real_parsed_result_when_present():
    """Sanity check: the happy path (model successfully parses a real
    receipt) is untouched by this fix."""
    from app.schemas import InvoiceSchema, LineItem

    real_invoice = InvoiceSchema(
        vendor="Acme",
        invoice_number="1",
        date="2026-01-01",
        currency="USD",
        subtotal=10,
        tax=1,
        total=11,
        line_items=[LineItem(description="x", quantity=1, unit_price=10, amount=10, confidence=0.95)],
        overall_confidence=0.95,
    )
    fake_client = MagicMock()
    fake_client.beta.chat.completions.parse.return_value = _fake_completion(parsed=real_invoice)

    invoice, _ = extract_invoice(_tiny_png_bytes(), client=fake_client)

    assert invoice.vendor == "Acme"
    assert invoice.overall_confidence == 0.95


def test_extract_invoice_falls_back_on_schema_validation_error(monkeypatch):
    """Reproduces the real bug: the model returns JSON that IS schema-shaped
    but fails one of our own field validators -- e.g. date="" for a
    non-receipt image, which our date_not_empty validator rejects. The
    OpenAI SDK raises this as a pydantic ValidationError from INSIDE
    .parse() itself, before message.parsed is ever reachable, so it bypasses
    the "parsed is None" fallback entirely and must be caught separately."""
    from pydantic import ValidationError
    from app.schemas import InvoiceSchema as RealInvoiceSchema

    fake_client = MagicMock()

    def raise_validation_error(*args, **kwargs):
        try:
            RealInvoiceSchema(
                vendor="",
                invoice_number=None,
                date="",  # triggers date_not_empty validator
                currency="",
                subtotal=0,
                tax=0,
                total=0,
                line_items=[],
                overall_confidence=0.5,
            )
        except ValidationError as exc:
            raise exc

    fake_client.beta.chat.completions.parse.side_effect = raise_validation_error

    invoice, cost = extract_invoice(_tiny_png_bytes(), client=fake_client)

    assert invoice is not None
    assert invoice.overall_confidence == 0.0
    assert invoice.vendor == "Unrecognized document"
    assert cost == 0.0


def test_schema_validation_fallback_routes_to_pending_review():
    """The schema-validation-error fallback must also flow into the review
    queue, exactly like the refusal fallback and any other low-confidence
    extraction -- this is the actual fix for the reported bug end to end."""
    from pydantic import ValidationError
    from app.schemas import InvoiceSchema as RealInvoiceSchema

    fake_client = MagicMock()

    def raise_validation_error(*args, **kwargs):
        RealInvoiceSchema(
            vendor="x", invoice_number=None, date="", currency="USD",
            subtotal=0, tax=0, total=0, line_items=[], overall_confidence=0.5,
        )

    fake_client.beta.chat.completions.parse.side_effect = raise_validation_error

    invoice, _ = extract_invoice(_tiny_png_bytes(), client=fake_client)
    status, flagged = route(invoice, threshold=0.75)

    assert status == "pending_review"
