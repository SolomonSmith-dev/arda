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
    with caplog.at_level(logging.INFO):
        with pytest.raises(httpx.HTTPStatusError):
            send_message(1, "x", client=client, token=token)

    assert token not in caplog.text
    assert f"/bot{token}/" not in caplog.text
