from __future__ import annotations

import logging
import re

_REDACTED = "[REDACTED]"
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(
        r"(?i)\b(authorization|cookie|x-csrf-token|x-amz-security-token)\b"
        r"([\"']?\s*[:=]\s*[\"']?)[^\"',\s}]+"
    ),
)


def redact_log_value(value: object) -> object:
    if isinstance(value, str):
        redacted = value
        for pattern in _SECRET_PATTERNS:
            if pattern.groups:
                redacted = pattern.sub(
                    lambda match: f"{match.group(1)}{match.group(2)}{_REDACTED}",
                    redacted,
                )
            else:
                redacted = pattern.sub(_REDACTED, redacted)
        return redacted
    if isinstance(value, tuple):
        return tuple(redact_log_value(item) for item in value)
    if isinstance(value, list):
        return [redact_log_value(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_log_value(item) for key, item in value.items()}
    return value


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        record.msg = redact_log_value(message)
        record.args = ()
        return True


def install_log_redaction_filter(logger: logging.Logger | None = None) -> None:
    target = logger or logging.getLogger()
    if not any(isinstance(existing, SecretRedactionFilter) for existing in target.filters):
        target.addFilter(SecretRedactionFilter())
    for handler in target.handlers:
        if not any(isinstance(existing, SecretRedactionFilter) for existing in handler.filters):
            handler.addFilter(SecretRedactionFilter())
