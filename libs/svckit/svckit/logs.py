"""JSON-логи через structlog."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


def configure_logging(
    level: str,
    service: str | None = None,
    *,
    event_key: str = "message",
    force: bool = True,
) -> None:
    """Настраивает structlog на JSON в stdout.

    event_key — под каким ключом писать текст события ("event" оставляет как в structlog);
    service — если задан, попадает в каждую запись.
    """
    lvl = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    logging.basicConfig(level=lvl, format="%(message)s", stream=sys.stdout, force=force)
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.format_exc_info,
    ]
    if event_key != "event":
        processors.append(structlog.processors.EventRenamer(event_key))
    processors.append(structlog.processors.JSONRenderer(ensure_ascii=False))
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(lvl),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    if service:
        structlog.contextvars.bind_contextvars(service=service)


def get_logger(name: str | None = None) -> structlog.typing.FilteringBoundLogger:
    logger: structlog.typing.FilteringBoundLogger = (
        structlog.get_logger(name) if name else structlog.get_logger()
    )
    return logger
