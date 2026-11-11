from __future__ import annotations

import io
import logging

import pytest

from core.logging import configure_logging

TOKEN = "123456:FAKE-TOKEN-ABCDEF"
SECRET = "FAKE-TOKEN-ABCDEF"


class _Bad:
    def __str__(self) -> str:
        raise ValueError("broken")


class _Spy:
    stringified = 0

    def __str__(self) -> str:
        type(self).stringified += 1
        return "spy"


@pytest.fixture
def root_sandbox():
    root = logging.getLogger()
    saved, level = list(root.handlers), root.level
    root.handlers.clear()
    configure_logging()
    root.handlers.clear()
    yield root
    root.handlers[:] = saved
    root.setLevel(level)


def _late_handler(root, fmt="%(message)s"):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter(fmt))
    root.addHandler(handler)
    return stream


def test_unrelated_logger_args_are_never_stringified_without_emit():
    configure_logging()
    log = logging.getLogger("unrelated.application")
    log.propagate = False
    log.addHandler(logging.NullHandler())
    log.warning("ignored %s", _Bad())  # must not raise


def test_disabled_record_does_not_stringify_args(root_sandbox):
    _Spy.stringified = 0
    _late_handler(root_sandbox)
    root_sandbox.setLevel(logging.ERROR)
    logging.getLogger("unrelated.lazy").info("x %s", _Spy())
    assert _Spy.stringified == 0


def test_extra_token_is_redacted_for_late_handler(root_sandbox):
    stream = _late_handler(root_sandbox, "%(url)s")
    logging.getLogger("httpcore.http11").warning("request", extra={"url": f"/bot{TOKEN}/sendMessage"})
    assert SECRET not in stream.getvalue()
    assert "<redacted>" in stream.getvalue()


def test_late_handler_message_arg_still_redacted(root_sandbox):
    stream = _late_handler(root_sandbox)
    logging.getLogger("httpcore.http11").warning("/bot%s/sendMessage", TOKEN)
    assert SECRET not in stream.getvalue()


def test_redaction_failure_yields_placeholder_not_raw_or_exception(root_sandbox):
    stream = _late_handler(root_sandbox)
    logging.getLogger("x").warning("t=%s " + TOKEN, _Bad())
    out = stream.getvalue()
    assert SECRET not in out
