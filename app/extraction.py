"""
Vision extraction.

Uses GPT-4o vision with the InvoiceSchema Pydantic model bound directly
as response_format, so the SDK guarantees the returned JSON matches the
schema shape -- no manual json.loads + try/except + retry loop needed.
"""
import base64
import io
import logging
import time

from openai import OpenAI
from pydantic import ValidationError
from PIL import Image

from app.config import VISION_MODEL, MAX_IMAGE_DIMENSION_PX, COST_PER_1K_INPUT_TOKENS, COST_PER_1K_OUTPUT_TOKENS
from app.schemas import InvoiceSchema
from app.metrics import extraction_latency_seconds, token_cost_usd_total

logger = logging.getLogger("ledgerlens.extraction")

SYSTEM_PROMPT = (
    "You are an expert document-extraction assistant for receipts and invoices. "
    "Extract structured data exactly as it appears in the image. "
    "For every field and every line item, include a confidence score between 0 and 1 "
    "reflecting how certain you are the value is correct as read from the image. "
    "If a field is illegible, blurry, or absent, assign it a low confidence rather than "
    "guessing a plausible-looking value. Never fabricate a line item that is not visibly "
    "present in the image."
)

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


def preprocess_image(image_bytes: bytes) -> bytes:
    """Resize to control cost/latency, per the brief's suggested layer notes."""
    img = Image.open(io.BytesIO(image_bytes))
    img.thumbnail((MAX_IMAGE_DIMENSION_PX, MAX_IMAGE_DIMENSION_PX))
    buf = io.BytesIO()
    fmt = img.format or "PNG"
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def _fallback_invoice() -> InvoiceSchema:
    """Zero-confidence stub used whenever the model can't produce a valid
    InvoiceSchema for this image -- covers both "returned nothing" (refusal)
    and "returned JSON that fails our own schema validation" (e.g. an empty
    date, which is exactly the model's honest way of saying it can't read
    one off a non-receipt image). Both cases get identical treatment so the
    document still flows through the normal confidence router into the
    human review queue instead of crashing the request.
    """
    return InvoiceSchema(
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


def extract_invoice(image_bytes: bytes, client: OpenAI | None = None) -> tuple[InvoiceSchema, float]:
    """Run vision extraction. Returns (InvoiceSchema, cost_usd)."""
    client = client or get_client()
    processed = preprocess_image(image_bytes)
    b64 = base64.standard_b64encode(processed).decode("utf-8")
    data_uri = f"data:image/png;base64,{b64}"

    start = time.monotonic()
    try:
        completion = client.beta.chat.completions.parse(
            model=VISION_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Extract the structured invoice/receipt data from this image."},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                },
            ],
            response_format=InvoiceSchema,
        )
    except ValidationError as exc:
        # The model returned JSON that is schema-shaped but fails one of
        # our own field validators (e.g. date="" for a genuinely
        # unreadable/non-receipt image -- the model's honest way of saying
        # "I don't know"). This is a data-quality signal, not an
        # infrastructure failure, so it gets the same fallback treatment as
        # an outright refusal rather than crashing the request.
        # Deliberately NOT catching broader exceptions here: auth, network,
        # and rate-limit errors should keep propagating so they surface
        # clearly instead of being silently masked as a "low confidence
        # extraction".
        logger.warning("Model output failed InvoiceSchema validation, using fallback: %s", exc)
        return _fallback_invoice(), 0.0
    finally:
        extraction_latency_seconds.observe(time.monotonic() - start)

    parsed = completion.choices[0].message.parsed
    if parsed is None:
        # The model could not map this image to the invoice schema at all
        # (e.g. it isn't a receipt/invoice, or the model refused outright).
        refusal = getattr(completion.choices[0].message, "refusal", None)
        reason = refusal or "model could not extract invoice-shaped data from this image"
        logger.warning("Vision extraction returned no parsed result: %s", reason)
        parsed = _fallback_invoice()

    usage = completion.usage
    cost = 0.0
    if usage:
        cost = (
            usage.prompt_tokens / 1000 * COST_PER_1K_INPUT_TOKENS
            + usage.completion_tokens / 1000 * COST_PER_1K_OUTPUT_TOKENS
        )
        token_cost_usd_total.inc(cost)

    return parsed, cost
