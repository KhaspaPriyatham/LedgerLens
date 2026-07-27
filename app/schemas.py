"""
Pydantic schema contracts for LedgerLens.

These models are passed directly as `response_format` to the OpenAI
chat.completions.create() call, so the SDK enforces the output shape.
No manual JSON parsing / retry loop is needed anywhere in the pipeline.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class LineItem(BaseModel):
    """A single line item extracted from an invoice/receipt."""

    description: str = Field(..., description="Item or service description")
    quantity: float = Field(..., ge=0, description="Quantity purchased")
    unit_price: float = Field(..., ge=0, description="Price per unit")
    amount: float = Field(..., ge=0, description="Line total (quantity * unit_price)")
    confidence: float = Field(
        ..., ge=0, le=1, description="Model's confidence in this line item, 0-1"
    )


class InvoiceSchema(BaseModel):
    """Top-level structured extraction contract for one document."""

    vendor: str = Field(..., description="Vendor / merchant name")
    invoice_number: Optional[str] = Field(None, description="Invoice or receipt number, if present")
    date: str = Field(..., description="Document date, ISO 8601 (YYYY-MM-DD) if determinable")
    currency: str = Field(..., description="ISO 4217 currency code, e.g. USD, INR")
    subtotal: float = Field(..., ge=0)
    tax: float = Field(..., ge=0)
    total: float = Field(..., ge=0)
    line_items: list[LineItem] = Field(default_factory=list)
    overall_confidence: float = Field(
        ..., ge=0, le=1, description="Aggregate confidence across all fields"
    )

    @field_validator("date")
    @classmethod
    def date_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("date must not be empty")
        return v

    def min_field_confidence(self) -> float:
        """The lowest confidence across overall + every line item.

        Used by the review router: a document is only as trustworthy as
        its least-confident field.
        """
        confidences = [self.overall_confidence] + [li.confidence for li in self.line_items]
        return min(confidences) if confidences else self.overall_confidence

    def low_confidence_fields(self, threshold: float) -> list[dict]:
        """Return a list of {field, confidence} dicts under the threshold.

        Line-item fields use a machine-parseable "line_items[<idx>].description"
        path -- rather than embedding the item's own (possibly low-confidence,
        possibly punctuation-containing) description text -- so that /approve
        can parse the index back out and write reviewer corrections into the
        structured line_items array, not just an audit-trail log entry.
        """
        flagged = []
        if self.overall_confidence < threshold:
            flagged.append({"field": "overall", "confidence": self.overall_confidence})
        for idx, li in enumerate(self.line_items):
            if li.confidence < threshold:
                flagged.append(
                    {
                        "field": f"line_items[{idx}].description",
                        "confidence": li.confidence,
                    }
                )
        return flagged


class ModerationResult(BaseModel):
    verdict: str  # "allow" | "block" | "human_review"
    max_score: float
    categories_flagged: list[str] = Field(default_factory=list)
    reason: Optional[str] = None


class ReviewFieldUpdate(BaseModel):
    """A reviewer's correction to a single flagged field."""

    field_path: str
    corrected_value: str


class ApproveRequest(BaseModel):
    document_id: str
    field_updates: list[ReviewFieldUpdate] = Field(default_factory=list)
    reviewer: Optional[str] = None


class RejectRequest(BaseModel):
    document_id: str
    reviewer: Optional[str] = None
    reason: Optional[str] = None


class RejectRequest(BaseModel):
    document_id: str
    reviewer: Optional[str] = None
    reason: Optional[str] = None


class DocumentRecord(BaseModel):
    """API response shape for a stored document."""

    id: str
    filename: str
    status: str  # "auto_approved" | "pending_review" | "approved" | "blocked"
    vendor: Optional[str] = None
    total: Optional[float] = None
    currency: Optional[str] = None
    moderation_verdict: Optional[str] = None
    extracted_json: Optional[str] = None
    reviewed_json: Optional[str] = None
    created_at: datetime
    image_path: Optional[str] = None


class IngestResponse(BaseModel):
    document_id: str
    status: str
    extracted: Optional[InvoiceSchema] = None
    flagged_fields: list[dict] = Field(default_factory=list)
    moderation: ModerationResult
    cost_usd: float = 0.0
