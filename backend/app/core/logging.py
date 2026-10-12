"""Structured logging with correlation ids.

Every log line carries the correlation id of the request or agent run that produced
it, so an error response can be traced to the exact sequence of events behind it.

Sensitive values never reach a log line: :func:`redact_processor` strips known-risky
keys, and callers pass values through ``app.domain.redaction`` before logging them.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from contextvars import ContextVar
from typing import Any, cast
from uuid import uuid4

import structlog

# Set per request by CorrelationIdMiddleware, and per job by the worker runner.
_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)

# Keys whose values are dropped before anything is written. Passwords, tokens,
# cookies and session state must never appear in logs. See docs/SAFETY.md.
_REDACTED_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "new_password",
        "current_password",
        "secret",
        "secret_key",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "api_key",
        "llm_api_key",
        "cookie",
        "cookies",
        "session",
        "session_state",
        "storage_state",
        "ssn",
        "date_of_birth",
    }
)

_REDACTED = "[redacted]"


def new_correlation_id() -> str:
    return uuid4().hex


def set_correlation_id(value: str | None) -> str:
    cid = value or new_correlation_id()
    _correlation_id.set(cid)
    return cid


def get_correlation_id() -> str:
    """The current correlation id, creating one if this context has none."""
    cid = _correlation_id.get()
    if cid is None:
        cid = new_correlation_id()
        _correlation_id.set(cid)
    return cid


def correlation_processor(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    cid = _correlation_id.get()
    if cid is not None:
        event_dict.setdefault("correlation_id", cid)
    return event_dict


def redact_processor(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Drop the values of known-sensitive keys, at any nesting depth."""

    def scrub(value: Any, depth: int = 0) -> Any:
        if depth > 6:
            return value
        if isinstance(value, dict):
            return {
                key: (
                    _REDACTED
                    if isinstance(key, str) and key.lower() in _REDACTED_KEYS
                    else scrub(inner, depth + 1)
                )
                for key, inner in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [scrub(item, depth + 1) for item in value]
        return value

    return cast(MutableMapping[str, Any], scrub(dict(event_dict)))


def configure_logging(*, level: str = "INFO", fmt: str = "console") -> None:
    """Configure structlog and route the standard library through the same renderer.

    structlog is wired *through* the stdlib via ``ProcessorFormatter``. That is more
    indirect than printing to stderr directly, but it is what makes uvicorn's and
    SQLAlchemy's own loggers come out in the same shape as ours instead of as a
    second, differently-formatted stream interleaved with it.

    Safe to call more than once: the root handler is replaced rather than appended, so
    the CLI configuring and then the app factory configuring again does not duplicate
    every line.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # Applied to every record, whether it originated in structlog or the stdlib.
    shared: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        correlation_processor,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
        redact_processor,
    ]

    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        processors=[
            *shared,
            # Hands the event dict to the stdlib handler below, which applies the
            # renderer. One output path for structlog and stdlib records alike.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        # Real stdlib loggers, so `add_logger_name` has a name to read and
        # `logging.getLogger(...).setLevel(...)` behaves as expected.
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=renderer,
            foreign_pre_chain=shared,
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(numeric_level)

    # uvicorn installs its own handlers; clearing them makes its records come through
    # ours instead of being printed twice in two different shapes.
    for noisy in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        noisy_logger = logging.getLogger(noisy)
        noisy_logger.handlers = []
        noisy_logger.propagate = True

    # pgserver narrates its handle bookkeeping, which is not interesting at INFO.
    logging.getLogger("pgserver").setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
