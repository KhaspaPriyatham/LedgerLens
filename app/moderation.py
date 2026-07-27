"""
Moderation gate.

Every uploaded image must pass through here BEFORE any vision-model call
is made. Image screening requires model="omni-moderation-latest" and a
structured content list; the default text-only moderation endpoint does
not accept images.
"""
import base64
import time

from openai import OpenAI

from app.config import (
    MODERATION_MODEL,
    MODERATION_BLOCK_THRESHOLD,
    MODERATION_REVIEW_THRESHOLD,
)
from app.schemas import ModerationResult
from app.metrics import moderation_latency_seconds

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


def screen_image(image_bytes: bytes, client: OpenAI | None = None) -> ModerationResult:
    """Run the OpenAI Moderation API against an image and return a routed verdict.

    Routing:
      - any category score > MODERATION_BLOCK_THRESHOLD  -> "block"
      - any category score > MODERATION_REVIEW_THRESHOLD -> "human_review"
      - otherwise                                          -> "allow"
    """
    client = client or get_client()
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    data_uri = f"data:image/png;base64,{b64}"

    start = time.monotonic()
    try:
        response = client.moderations.create(
            model=MODERATION_MODEL,
            input=[{"type": "image_url", "image_url": {"url": data_uri}}],
        )
    finally:
        moderation_latency_seconds.observe(time.monotonic() - start)

    result = response.results[0]
    category_scores = dict(result.category_scores)
    max_score = max(category_scores.values()) if category_scores else 0.0
    flagged = [cat for cat, score in category_scores.items() if score > MODERATION_REVIEW_THRESHOLD]

    if max_score > MODERATION_BLOCK_THRESHOLD:
        verdict = "block"
        reason = f"Content flagged for: {', '.join(flagged) or 'policy violation'}"
    elif max_score > MODERATION_REVIEW_THRESHOLD:
        verdict = "human_review"
        reason = f"Borderline content in categories: {', '.join(flagged)}"
    else:
        verdict = "allow"
        reason = None

    return ModerationResult(
        verdict=verdict,
        max_score=max_score,
        categories_flagged=flagged,
        reason=reason,
    )
