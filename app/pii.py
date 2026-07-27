"""
PII redaction.

Per the brief: "PII in extracted text (SSNs, email addresses, phone
numbers) is redacted via regex before any log line is written."

This module exposes:
  - redact(text): returns a redacted copy of a string
  - PIIRedactingFilter: a logging.Filter that redacts every log record's
    message before it is emitted, so redaction is applied uniformly
    rather than left to individual call sites.
"""
import logging
import re

SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# Matches standard phone groupings (3-3-4, optional country code, optional
# parens/dots/spaces as separators). Deliberately NOT a bare "10+ digits
# with separators" pattern, since that also matches ISO dates like
# 2026-07-20 -- a real false positive caught during integration testing.
PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)"
)

_PATTERNS = (
    (SSN_RE, "[REDACTED_SSN]"),
    (EMAIL_RE, "[REDACTED_EMAIL]"),
    (PHONE_RE, "[REDACTED_PHONE]"),
)


def redact(text: str) -> str:
    if not text:
        return text
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class PIIRedactingFilter(logging.Filter):
    """Attach to any logger/handler to redact PII from every emitted record."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(str(record.msg))
            if record.args:
                record.args = tuple(
                    redact(str(a)) if isinstance(a, str) else a for a in record.args
                )
        except Exception:
            # Never let redaction failure block logging entirely; fail open
            # on formatting only, not on the log itself.
            pass
        return True


def configure_redacted_logging(logger_name: str = "ledgerlens") -> logging.Logger:
    logger = logging.getLogger(logger_name)
    if not any(isinstance(f, PIIRedactingFilter) for f in logger.filters):
        logger.addFilter(PIIRedactingFilter())
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.addFilter(PIIRedactingFilter())
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
