"""
Confidence threshold router.

Iterates every extracted field's confidence score; anything below
REVIEW_THRESHOLD is written to the review queue with status="pending"
rather than being silently trusted. Everything else is auto-approved.
"""
from app.config import REVIEW_THRESHOLD
from app.schemas import InvoiceSchema


def route(invoice: InvoiceSchema, threshold: float = REVIEW_THRESHOLD) -> tuple[str, list[dict]]:
    """Return (status, flagged_fields).

    status is one of "auto_approved" | "pending_review".
    flagged_fields is a list of {"field": str, "confidence": float}.
    """
    flagged = invoice.low_confidence_fields(threshold)
    status = "pending_review" if flagged else "auto_approved"
    return status, flagged
