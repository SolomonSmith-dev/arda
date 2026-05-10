from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from agents.gwaihir.bot import _extract_reply, _handle_update


def _api_client_returning(body: dict) -> httpx.Client:
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=body))
    return httpx.Client(transport=transport, base_url="http://api")


def _api_client_capturing(captured: dict, body: dict) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = request.read().decode()
        return httpx.Response(200, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://api")


def test_extract_reply_prefers_stdout_for_shell_responses():
    body = {"stdout": "hello\n", "stderr": "", "status": "completed"}
    assert _extract_reply(body) == "hello"


def test_extract_reply_returns_stderr_when_stdout_empty():
    body = {"stdout": "", "stderr": "boom"}
    assert _extract_reply(body) == "boom"


def test_extract_reply_combines_when_both_present():
    body = {"stdout": "out", "stderr": "warn"}
    out = _extract_reply(body)
    assert "out" in out and "warn" in out


def test_extract_reply_pulls_structured_reply():
    body = {"status": "completed", "result": {"reply": "structured answer"}}
    assert _extract_reply(body) == "structured answer"


def test_extract_reply_falls_back_to_status_when_empty():
    body = {"status": "completed"}
    assert _extract_reply(body) == "completed"


def test_handle_update_drops_chat_outside_allowlist(monkeypatch):
    monkeypatch.setattr(
        "agents.gwaihir.bot.settings.telegram_allowed_chat_ids", "999"
    )
    api_client = MagicMock()
    telegram_client = MagicMock()

    update = {
        "update_id": 1,
        "message": {"chat": {"id": 123}, "text": "hello", "from": {"username": "evil"}},
    }
    _handle_update(api_client, telegram_client, "tok", update)

    api_client.post.assert_not_called()
    telegram_client.post.assert_not_called()


def test_handle_update_allows_empty_allowlist_through(monkeypatch):
    """Empty allowlist means open mode — used during dev only."""
    monkeypatch.setattr(
        "agents.gwaihir.bot.settings.telegram_allowed_chat_ids", ""
    )

    api_body = {"status": "completed", "result": {"reply": "pong"}}
    captured: dict = {}
    api_client = _api_client_capturing(captured, api_body)

    sent: dict = {}

    def telegram_handler(request: httpx.Request) -> httpx.Response:
        sent["url"] = str(request.url)
        sent["body"] = request.read().decode()
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    telegram_client = httpx.Client(transport=httpx.MockTransport(telegram_handler))

    update = {"update_id": 1, "message": {"chat": {"id": 42}, "text": "ping"}}
    _handle_update(api_client, telegram_client, "tok", update)

    assert captured["path"] == "/execute/wait"
    assert "ping" in captured["body"]
    assert "pong" in sent["body"]
    assert "42" in sent["body"]


def test_handle_update_allowlisted_chat_forwards_to_api(monkeypatch):
    monkeypatch.setattr(
        "agents.gwaihir.bot.settings.telegram_allowed_chat_ids", "42"
    )

    api_body = {"stdout": "ok\n", "stderr": ""}
    captured: dict = {}
    api_client = _api_client_capturing(captured, api_body)

    sent: dict = {}

    def telegram_handler(request: httpx.Request) -> httpx.Response:
        sent["body"] = request.read().decode()
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    telegram_client = httpx.Client(transport=httpx.MockTransport(telegram_handler))

    update = {"update_id": 7, "message": {"chat": {"id": 42}, "text": "whoami"}}
    _handle_update(api_client, telegram_client, "tok", update)

    assert captured["path"] == "/execute/wait"
    assert "whoami" in captured["body"]
    assert '"42"' in sent["body"] or '42' in sent["body"]
    assert "ok" in sent["body"]


def test_handle_update_ignores_non_message_updates():
    api_client = MagicMock()
    telegram_client = MagicMock()

    update = {"update_id": 1, "edited_message": {"chat": {"id": 1}, "text": "x"}}
    _handle_update(api_client, telegram_client, "tok", update)

    api_client.post.assert_not_called()


def test_handle_update_ignores_messages_without_text(monkeypatch):
    monkeypatch.setattr(
        "agents.gwaihir.bot.settings.telegram_allowed_chat_ids", ""
    )
    api_client = MagicMock()
    telegram_client = MagicMock()

    update = {"update_id": 1, "message": {"chat": {"id": 1}}}
    _handle_update(api_client, telegram_client, "tok", update)

    api_client.post.assert_not_called()


def test_handle_update_replies_with_error_when_api_fails(monkeypatch):
    monkeypatch.setattr(
        "agents.gwaihir.bot.settings.telegram_allowed_chat_ids", "42"
    )

    def api_handler(_):
        raise httpx.ConnectError("api down")

    api_client = httpx.Client(transport=httpx.MockTransport(api_handler), base_url="http://api")

    sent: dict = {}

    def telegram_handler(request: httpx.Request) -> httpx.Response:
        sent["body"] = request.read().decode()
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    telegram_client = httpx.Client(transport=httpx.MockTransport(telegram_handler))

    update = {"update_id": 1, "message": {"chat": {"id": 42}, "text": "hi"}}
    _handle_update(api_client, telegram_client, "tok", update)

    assert "agent error" in sent["body"]
