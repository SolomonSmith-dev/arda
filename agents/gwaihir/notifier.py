"""Outbound Telegram delivery.

Pure ``sendMessage`` wrapper. The bot uses it for replies; Galadriel's
``announce`` hook uses it to deliver scheduled reminders.
"""

from __future__ import annotations

import httpx

from core.config import settings
from core.logging import get_logger

log = get_logger("agents.gwaihir.notifier")

TELEGRAM_API_BASE = "https://api.telegram.org"
SEND_TIMEOUT_SECONDS = 10


class TelegramNotConfiguredError(RuntimeError):
    """Raised when send_message is called without TELEGRAM_BOT_TOKEN set."""


def _api_url(token: str, method: str) -> str:
    return f"{TELEGRAM_API_BASE}/bot{token}/{method}"


def send_message(
    chat_id: int | str,
    text: str,
    *,
    client: httpx.Client | None = None,
    token: str | None = None,
) -> dict:
    """POST sendMessage and return the parsed JSON response.

    Raises :class:`TelegramNotConfiguredError` if no token is available.
    Raises :class:`httpx.HTTPStatusError` on non-2xx (e.g. invalid chat_id).
    """
    bot_token = token if token is not None else settings.telegram_bot_token
    if not bot_token:
        raise TelegramNotConfiguredError("TELEGRAM_BOT_TOKEN not set")

    payload = {"chat_id": chat_id, "text": text}
    url = _api_url(bot_token, "sendMessage")

    owns_client = client is None
    if owns_client:
        client = httpx.Client(timeout=SEND_TIMEOUT_SECONDS)
    try:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        body = resp.json()
        log.info(
            "telegram_send_ok",
            chat_id=chat_id,
            text_preview=text[:80],
            message_id=body.get("result", {}).get("message_id"),
        )
        return body
    finally:
        if owns_client:
            client.close()
