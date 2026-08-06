"""Structured JSON logging with secret redaction.

Configures structlog for JSON output, binds a per-run ``run_id``, and installs
a processor that strips any event key matching ``/key|token|secret|password/i``.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any, TextIO, cast
from uuid import uuid4

import structlog

_SECRET_KEY_RE = re.compile(r"key|token|secret|password", re.IGNORECASE)

_REDACTED = "***REDACTED***"


def redact_secrets(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Structlog processor: replace values whose keys look like secrets."""
    for key in list(event_dict):
        value = event_dict[key]
        if _SECRET_KEY_RE.search(str(key)):
            event_dict[key] = _REDACTED
        elif isinstance(value, MutableMapping):
            event_dict[key] = redact_secrets(_logger, _method_name, value)
    return event_dict


def configure_logging(
    level: str = "INFO",
    run_id: str | None = None,
    stream: TextIO | None = None,
) -> str:
    """Configure structlog + stdlib logging for JSON lines.

    Returns the bound ``run_id`` (generated if not provided).
    """
    resolved_run_id = run_id or uuid4().hex
    log_level = getattr(logging, level.upper(), logging.INFO)
    output = stream if stream is not None else sys.stdout

    logging.basicConfig(
        format="%(message)s",
        stream=output,
        level=log_level,
        force=True,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_secrets,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(run_id=resolved_run_id)
    return resolved_run_id


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger (call :func:`configure_logging` first)."""
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))
