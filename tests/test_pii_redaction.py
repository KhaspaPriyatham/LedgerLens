import logging

from app.pii import redact, PIIRedactingFilter


def test_redacts_ssn():
    text = "Customer SSN is 123-45-6789 on file."
    out = redact(text)
    assert "123-45-6789" not in out
    assert "[REDACTED_SSN]" in out


def test_redacts_email():
    text = "Contact billing@acme.com for questions."
    out = redact(text)
    assert "billing@acme.com" not in out
    assert "[REDACTED_EMAIL]" in out


def test_redacts_phone():
    text = "Call us at +1 415-555-0198 anytime."
    out = redact(text)
    assert "415-555-0198" not in out
    assert "[REDACTED_PHONE]" in out


def test_redacts_multiple_pii_types_together():
    text = "Reach John at john@acme.com or 415-555-0198, SSN 987-65-4321."
    out = redact(text)
    assert "john@acme.com" not in out
    assert "415-555-0198" not in out
    assert "987-65-4321" not in out


def test_non_pii_text_passes_through_unchanged():
    text = "Vendor: Acme Corp, Total: $110.00"
    assert redact(text) == text


def test_logging_filter_redacts_log_record_message():
    logger = logging.getLogger("test_pii_logger")
    logger.addFilter(PIIRedactingFilter())

    record = logging.LogRecord(
        name="test_pii_logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="User email is jane@example.com",
        args=(),
        exc_info=None,
    )

    for f in logger.filters:
        f.filter(record)

    assert "jane@example.com" not in record.msg
    assert "[REDACTED_EMAIL]" in record.msg
