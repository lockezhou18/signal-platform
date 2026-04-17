"""Structured logging configuration.

Every log event is a JSON object with timestamp, level, event, and
contextual fields. Bind the run_id on service start so every event
within a run is correlatable.
"""

from __future__ import annotations

import logging
import platform
import uuid

import structlog


def configure_logging(level: str = "INFO") -> None:
    """Initialize structlog + stdlib logging with JSON output to stderr."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        level=log_level,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def bind_run_context() -> str:
    """Generate and bind a run_id for the current execution scope.

    Call once per logical run (scheduler tick, CLI invocation, etc).
    All subsequent log events pick up the run_id automatically.
    """
    run_id = str(uuid.uuid4())
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        run_id=run_id,
        service="signal-platform",
        host=platform.node(),
    )
    return run_id


get_logger = structlog.get_logger
