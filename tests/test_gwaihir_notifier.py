from __future__ import annotations

import logging

import httpx
import pytest

from agents.gwaihir.notifier import TelegramNotConfiguredError, send_message
from core.logging import configure_logging


def _client_capturing(captured: dict, response_body: dict | None = None) -> httpx.Client:
    body = response_body or {"ok": True, "result": {"message_id": 42}}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = request.read().decode()
        return httpx.Response(200, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_send_message_posts_to_correct_url_and_payload():
    captured: dict = {}
    client = _client_capturing(captured)

    body = send_message(123456, "hello world", client=client, token="fake-token")

    assert captured["url"] == "https://api.telegram.org/botfake-token/sendMessage"
    assert "hello world" in captured["json"]
    assert "123456" in captured["json"]
    assert body["result"]["message_id"] == 42


def test_send_message_raises_when_no_token(monkeypatch):
    monkeypatch.setattr("agents.gwaihir.notifier.settings.telegram_bot_token", "")
    with pytest.raises(TelegramNotConfiguredError):
        send_message(1, "x")


def test_send_message_raises_on_http_error():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"ok": False, "description": "Forbidden"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        send_message(1, "x", client=client, token="fake-token")


def test_send_message_uses_settings_token_when_not_passed(monkeypatch):
    monkeypatch.setattr(
        "agents.gwaihir.notifier.settings.telegram_bot_token", "from-settings"
    )
    captured: dict = {}
    client = _client_capturing(captured)

    send_message(7, "ping", client=client)

    assert "from-settings" in captured["url"]


def test_send_message_http_error_does_not_log_bot_token(caplog, monkeypatch):
    token = "123456789:AAHexample-bot-token_secret"
    monkeypatch.setattr("agents.gwaihir.notifier.settings.telegram_bot_token", token)
    monkeypatch.setattr("core.logging.settings.telegram_bot_token", token)
    configure_logging()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"ok": False, "description": "Forbidden"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with (
        caplog.at_level(logging.INFO),
        pytest.raises(httpx.HTTPStatusError),
    ):
        send_message(1, "x", client=client, token=token)

    assert token not in caplog.text
    assert f"/bot{token}/" not in caplog.text


def test_log_filter_scrubs_traceback_and_structlog_events(caplog, monkeypatch, capsys):
    import sys

    from structlog.testing import capture_logs  # noqa: F401  (processor tested directly)

    from core.logging import _redact_event_dict, _RedactTelegramBotTokenFilter

    token = "123456789:FAKE-not-a-real-token-AAAAAAAAAAAAAAAAAAA"
    monkeypatch.setattr("core.logging.settings.telegram_bot_token", token)

    try:
        raise RuntimeError(f"https://api.telegram.org/bot{token}/sendMessage failed")
    except RuntimeError:
        record = logging.LogRecord("httpx", logging.ERROR, __file__, 1, "boom", None, sys.exc_info())
    _RedactTelegramBotTokenFilter().filter(record)
    assert token not in (record.exc_text or "")
    assert record.exc_info is None

    event = _redact_event_dict(None, "error", {"exc": f"bot{token}/x", "n": [f"/bot{token}/y"]})
    assert token not in repr(event)


def test_child_logger_record_is_redacted_at_the_handler(monkeypatch):
    """X1 G1: logger filters skip records from child loggers (httpcore.http11); the handler sees them all."""
    import io

    token = "123456789:AAHexample-bot-token_secret"
    monkeypatch.setattr("core.logging.settings.telegram_bot_token", token)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    root.addHandler(handler)
    old_level = root.level
    root.setLevel(logging.DEBUG)
    try:
        configure_logging()
        logging.getLogger("httpcore.http11").debug("send GET /bot%s/sendMessage", token)
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)

    out = stream.getvalue()
    assert "sendMessage" in out
    assert token not in out
    assert "<redacted>" in out


def test_late_root_handler_still_gets_redacted_records(monkeypatch):
    """X2: a root handler installed AFTER configure_logging() must not see a raw token."""
    import io

    token = "123456:FAKE-TOKEN-ABCDEF"
    stream = io.StringIO()
    root = logging.getLogger()
    saved = list(root.handlers)
    old_level = root.level
    root.handlers.clear()
    try:
        configure_logging()
        root.handlers.clear()  # reconfiguration: the handlers present at configure time are gone
        root.addHandler(logging.StreamHandler(stream))  # late handler, never got the filter
        root.setLevel(logging.DEBUG)
        logging.getLogger("httpcore.http11").warning("/bot%s/sendMessage", token)
        logging.getLogger("httpx").warning("GET https://api.telegram.org/bot%s/getMe", token)
    finally:
        root.handlers[:] = saved
        root.setLevel(old_level)

    out = stream.getvalue()
    assert "sendMessage" in out and "getMe" in out
    assert "FAKE-TOKEN-ABCDEF" not in out
    assert "<redacted>" in out
