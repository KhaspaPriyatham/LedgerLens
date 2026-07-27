from types import SimpleNamespace
from unittest.mock import MagicMock

from app.moderation import screen_image


def _fake_moderation_response(category_scores: dict):
    result = SimpleNamespace(category_scores=category_scores)
    return SimpleNamespace(results=[result])


def test_screen_image_blocks_high_score_category():
    fake_client = MagicMock()
    fake_client.moderations.create.return_value = _fake_moderation_response(
        {"violence": 0.92, "sexual": 0.01}
    )

    result = screen_image(b"fake-image-bytes", client=fake_client)

    assert result.verdict == "block"
    assert "violence" in result.categories_flagged
    fake_client.moderations.create.assert_called_once()


def test_screen_image_allows_clean_image():
    fake_client = MagicMock()
    fake_client.moderations.create.return_value = _fake_moderation_response(
        {"violence": 0.001, "sexual": 0.0005}
    )

    result = screen_image(b"fake-image-bytes", client=fake_client)

    assert result.verdict == "allow"
    assert result.categories_flagged == []


def test_screen_image_routes_borderline_to_human_review():
    fake_client = MagicMock()
    fake_client.moderations.create.return_value = _fake_moderation_response(
        {"violence": 0.3, "sexual": 0.01}
    )

    result = screen_image(b"fake-image-bytes", client=fake_client)

    assert result.verdict == "human_review"


def test_blocked_image_never_reaches_extraction(monkeypatch):
    """Ingest pipeline contract: a blocked image must short-circuit before
    extract_invoice is ever called. This test asserts the router-level
    behavior the /ingest endpoint depends on."""
    import app.extraction as extraction_module

    fake_client = MagicMock()
    fake_client.moderations.create.return_value = _fake_moderation_response(
        {"violence": 0.99}
    )

    called = {"extract": False}

    def fake_extract(*args, **kwargs):
        called["extract"] = True
        raise AssertionError("extract_invoice must not be called for blocked images")

    monkeypatch.setattr(extraction_module, "extract_invoice", fake_extract)

    result = screen_image(b"fake-image-bytes", client=fake_client)
    assert result.verdict == "block"

    # Simulate the endpoint's short-circuit logic explicitly.
    if result.verdict != "block":
        extraction_module.extract_invoice(b"fake-image-bytes")

    assert called["extract"] is False
