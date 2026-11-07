from __future__ import annotations

import logging
import re
import sys
from contextvars import ContextVar
from uuid import uuid4

import structlog

from core.config import settings

_trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)
_configured = False
_BOT_TOKEN_IN_URL = re.compile(r"/bot[^/\s]+/")


class _RedactTelegramBotTokenFilter(logging.Filter):
    """Strip Telegram bot tokens from stdlib log records (e.g. httpx INFO)."""

    def filter(self, record: logging.LogRecord) -> bool:
        token = settings.telegram_bot_token

        def scrub(text: str) -> str:
            if token:
                text = text.replace(token, "<redacted>")
            return _BOT_TOKEN_IN_URL.sub("/bot<redacted>/", text)

        if isinstance(record.msg, str) and (
            "/bot" in record.msg or (token and token in record.msg)
        ):
            record.msg = scrub(record.msg)

        args = record.args
        if isinstance(args, dict):
            record.args = {
                key: scrub(str(value))
                if "/bot" in str(value) or (token and token in str(value))
                else value
                for key, value in args.items()
            }
        elif args:
            scrubbed: list[object] = []
            for arg in args:
                text = str(arg)
                if "/bot" in text or (token and token in text):
                    scrubbed.append(scrub(text))
                else:
                    scrubbed.append(arg)
            record.args = tuple(scrubbed)
        return True


def _install_telegram_token_log_filter() -> None:
    filt = _RedactTelegramBotTokenFilter()
    for name in ("httpx", "httpcore"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, _RedactTelegramBotTokenFilter) for f in logger.filters):
            logger.addFilter(filt)


def set_trace_id(trace_id: str | None) -> None:
    _trace_id_var.set(trace_id)


def get_trace_id() -> str | None:
    return _trace_id_var.get()


def new_trace_id() -> str:
    tid = str(uuid4())
    _trace_id_var.set(tid)
    return tid


def _inject_trace_id(_logger, _name, event_dict):
    tid = _trace_id_var.get()
    if tid is not None:
        event_dict["trace_id"] = tid
    return event_dict


def configure_logging() -> None:
    global _configured
    if _configured:
        _install_telegram_token_log_filter()
        return

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
    )

    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _inject_trace_id,
    ]

    if settings.log_format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )
    _install_telegram_token_log_filter()
    _configured = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    if not _configured:
        configure_logging()
    else:
        _install_telegram_token_log_filter()
    return structlog.get_logger(name)
