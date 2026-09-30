from __future__ import annotations

import copy
import logging
import re
from typing import Any


_SENSITIVE_KEY = re.compile(r"authorization|cookie|secret|token|key", re.IGNORECASE)
_INLINE_SECRET = re.compile(
    r"(?i)\b(authorization|cookie|secret|token|key)\b\s*([=:])\s*[^\s,;]+"
)
_MAX_EXTERNAL_VALUE = 300


def _redact(value: Any, *, sensitive: bool = False) -> Any:
    if sensitive:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            key: _redact(item, sensitive=bool(_SENSITIVE_KEY.search(str(key))))
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(_redact(item) for item in value)
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str) and len(value) > _MAX_EXTERNAL_VALUE:
        return f"{value[:_MAX_EXTERNAL_VALUE]}…"
    return value


class RedactingFormatter(logging.Formatter):
    """Redact credentials before a log record reaches any configured sink."""

    def format(self, record: logging.LogRecord) -> str:
        safe_record = copy.copy(record)
        safe_record.msg = _redact(record.msg)
        safe_record.args = _redact(record.args)
        safe_record.exc_info = None
        rendered = super().format(safe_record)
        return _INLINE_SECRET.sub(r"\1\2[REDACTED]", rendered)
