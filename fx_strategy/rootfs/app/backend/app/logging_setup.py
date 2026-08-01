"""Structured JSON logging with credential scrubbing.

Every log record passes through :func:`scrub_processor`, which removes values
that look like secrets.  This is a defence in depth measure: the code is also
written never to log credentials in the first place.
"""

from __future__ import annotations

import logging
import re
import sys
from collections import deque
from collections.abc import MutableMapping
from typing import Any

import structlog

#: Keys whose values are replaced wholesale.
SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "api_token",
        "authorization",
        "client_secret",
        "mqtt_password",
        "password",
        "secret",
        "secret_key",
        "token",
        "wise_api_token",
        "x-api-key",
    }
)

REDACTED = "***redacted***"

#: Bearer tokens and long opaque strings embedded in free text.
_BEARER_RE = re.compile(r"(?i)\b(bearer|token|api[-_ ]?key)\s*[=:]?\s*([A-Za-z0-9._\-]{12,})")
_UUID_TOKEN_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE
)


def scrub_text(text: str) -> str:
    """Remove credential-looking substrings from free text."""
    text = _BEARER_RE.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    return _UUID_TOKEN_RE.sub(REDACTED, text)


def _scrub_value(key: str, value: Any) -> Any:
    if key.lower() in SENSITIVE_KEYS:
        return REDACTED if value not in (None, "") else value
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {k: _scrub_value(k, v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_value(key, v) for v in value]
    return value


def scrub_processor(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor that redacts sensitive values."""
    return {k: _scrub_value(k, v) for k, v in event_dict.items()}


class RingBufferHandler(logging.Handler):
    """Keeps the most recent log lines in memory for the diagnostics page."""

    def __init__(self, capacity: int = 500) -> None:
        super().__init__()
        self.records: deque[str] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append(scrub_text(self.format(record)))
        except Exception:  # pragma: no cover - logging must never raise
            self.handleError(record)

    def tail(self, limit: int = 200) -> list[str]:
        return list(self.records)[-limit:]


_ring_buffer = RingBufferHandler()


def get_ring_buffer() -> RingBufferHandler:
    return _ring_buffer


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog and the stdlib logging bridge."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        scrub_processor,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(stream)

    _ring_buffer.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(_ring_buffer)
    root.setLevel(numeric_level)

    # Route uvicorn through the same JSON handler instead of its own formatter,
    # so the add-on log is one consistent stream.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    # The access log is noisy behind Ingress and duplicates our request log.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
