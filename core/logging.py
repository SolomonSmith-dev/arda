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
_REDACTED = "<redacted>"


def redact_telegram_token(text: str) -> str:
    """Strip Telegram bot tokens from text. Single source for logs and stored errors."""
    token = settings.telegram_bot_token
    if token:
        text = text.replace(token, _REDACTED)
    return _BOT_TOKEN_IN_URL.sub(f"/bot{_REDACTED}/", text)


def _scrub_value(value):
    if isinstance(value, str):
        return redact_telegram_token(value)
    if isinstance(value, dict):
        return {k: _scrub_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_scrub_value(v) for v in value)
    return value


def _scrub_arg(arg):
    """Log args may be non-str (httpx.URL); stringify only when a token shows."""
    text = str(arg)
    scrubbed = redact_telegram_token(text)
    return scrubbed if scrubbed != text else arg


class _RedactTelegramBotTokenFilter(logging.Filter):
    """Scrub bot tokens from stdlib log records (httpx INFO URLs, tracebacks)."""

    def filter(self, record: logging.LogRecord) -> bool:
        _scrub_record(record)
        return True


def _scrub_record(record: logging.LogRecord, always_render_exc: bool = True) -> None:
    # Redact the formatted message so a token split across template and args,
    # or a "/bot%s/" template, cannot break formatting or slip through.
    try:
        message = record.getMessage()
    except Exception:  # noqa: BLE001 - a malformed record must still be scrubbed
        message = None
    if message is not None:
        scrubbed = redact_telegram_token(message)
        if scrubbed != message:
            record.msg, record.args = scrubbed, None
    if isinstance(record.msg, str):
        record.msg = redact_telegram_token(record.msg)
    if isinstance(record.args, dict):
        record.args = {k: _scrub_arg(v) for k, v in record.args.items()}
    elif record.args:
        record.args = tuple(_scrub_arg(a) for a in record.args)
    if record.exc_info:
        # Handlers format exc_info after filtering, so render it here.
        rendered = logging.Formatter().formatException(record.exc_info)
        clean = redact_telegram_token(rendered)
        if always_render_exc or clean != rendered:
            record.exc_text = clean
            record.exc_info = None
    elif record.exc_text:
        record.exc_text = redact_telegram_token(record.exc_text)


def _install_record_factory() -> None:
    """Scrub at record creation (X2): redaction no longer depends on which handler or logger
    filter exists, so a handler added after configure_logging() still sees clean records.
    Unrelated records keep their exc_info; only a traceback that carried a token is flattened."""
    current = logging.getLogRecordFactory()
    if getattr(current, "_redacts_telegram_token", False):
        return

    def factory(*args, **kwargs):
        record = current(*args, **kwargs)
        _scrub_record(record, always_render_exc=False)
        return record

    factory._redacts_telegram_token = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(factory)


def _install_telegram_token_log_filter() -> None:
    _install_record_factory()
    filt = _RedactTelegramBotTokenFilter()
    for name in ("httpx", "httpcore"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, _RedactTelegramBotTokenFilter) for f in logger.filters):
            logger.addFilter(filt)
    # Logger filters skip records that child loggers (httpcore.http11) propagate up.
    # Handler filters see every record, so also guard each root handler (X1 G1).
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, _RedactTelegramBotTokenFilter) for f in handler.filters):
            handler.addFilter(filt)


def _redact_event_dict(_logger, _name, event_dict):
    """structlog processor: PrintLoggerFactory bypasses stdlib filters."""
    return {k: _scrub_value(v) for k, v in event_dict.items()}


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
        _redact_event_dict,
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
    return structlog.get_logger(name)
