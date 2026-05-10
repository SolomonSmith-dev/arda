"""Gwaihir: Telegram long-poll bot.

Loop:
  1. ``getUpdates`` with ``timeout=30`` and ``offset=last_update_id+1``
  2. For each ``message`` update, drop unless ``chat.id`` is in the
     allowlist (silently — never reveal allowlist membership to outside
     callers).
  3. Forward the text to the API at ``/execute/wait``; reply with the
     resulting ``output``.

Started as a separate compose service: ``python -m agents.gwaihir.bot``.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from agents.gwaihir.notifier import send_message
from core.config import settings
from core.logging import get_logger, new_trace_id

log = get_logger("agents.gwaihir.bot")

TELEGRAM_API_BASE = "https://api.telegram.org"
LONG_POLL_TIMEOUT_SECONDS = 30
EXECUTE_TIMEOUT_SECONDS = 60
ERROR_BACKOFF_SECONDS = 5


def _get_updates(
    client: httpx.Client, token: str, offset: int | None
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"timeout": LONG_POLL_TIMEOUT_SECONDS}
    if offset is not None:
        params["offset"] = offset
    resp = client.get(
        f"{TELEGRAM_API_BASE}/bot{token}/getUpdates",
        params=params,
        timeout=LONG_POLL_TIMEOUT_SECONDS + 5,
    )
    resp.raise_for_status()
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError(f"getUpdates not ok: {body}")
    return body.get("result", [])


def _execute(api_client: httpx.Client, message: str) -> str:
    """POST to /execute/wait and return the agent's text output."""
    resp = api_client.post(
        "/execute/wait",
        json={"message": message},
        timeout=EXECUTE_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    body = resp.json()
    return _extract_reply(body)


def _format_output(output: Any, error: str = "") -> str:
    """Render one task's ``output`` (shell dict, structured dict, or string)."""
    if isinstance(output, dict):
        stdout = (output.get("stdout") or "").strip()
        stderr = (output.get("stderr") or "").strip()
        if stdout or stderr:
            if stdout and stderr:
                return f"{stdout}\n[stderr]\n{stderr}"
            return stdout or stderr
        for key in ("reply", "text", "message", "output"):
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return str(output)

    if isinstance(output, str) and output.strip():
        return output.strip()

    if error:
        return f"error: {error}"

    return ""


def _extract_reply(body: dict[str, Any]) -> str:
    """Pull a human-readable reply out of the /execute/wait response.

    The canonical shape is ``{"status": ..., "results": [{"output": ...}]}``
    for both shell and sync (Sauron) paths. We concatenate per-task
    outputs when a workflow returns multiple commands.
    """
    results = body.get("results") or []
    if results:
        chunks: list[str] = []
        for r in results:
            chunk = _format_output(r.get("output"), r.get("error") or "")
            if chunk:
                chunks.append(chunk)
        if chunks:
            return "\n\n".join(chunks)

    if "stdout" in body or "stderr" in body:
        return _format_output(body, body.get("error") or "")

    if "output" in body or "result" in body:
        return _format_output(
            body.get("output") if "output" in body else body.get("result"),
            body.get("error") or "",
        )

    return body.get("status", "unknown")


def _handle_update(
    api_client: httpx.Client, telegram_client: httpx.Client, token: str, update: dict
) -> None:
    new_trace_id()
    message = update.get("message")
    if not message:
        return

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()
    if not chat_id or not text:
        return

    allowlist = settings.telegram_chat_allowlist
    if allowlist and chat_id not in allowlist:
        log.warning(
            "telegram_chat_blocked",
            chat_id=chat_id,
            from_user=(message.get("from") or {}).get("username"),
        )
        return

    log.info(
        "telegram_message_received",
        chat_id=chat_id,
        text_preview=text[:100],
    )

    try:
        reply = _execute(api_client, text)
    except Exception as e:
        log.error("execute_failed", chat_id=chat_id, exc=str(e))
        reply = f"agent error: {e}"

    try:
        send_message(chat_id, reply, client=telegram_client, token=token)
    except Exception as e:
        log.error("telegram_send_failed", chat_id=chat_id, exc=str(e))


def run_forever() -> None:
    token = settings.telegram_bot_token
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN not configured")

    api_headers = {"x-api-key": settings.arda_api_key}
    last_update_id: int | None = None

    log.info(
        "gwaihir_starting",
        api=settings.internal_api_url,
        allowlist_size=len(settings.telegram_chat_allowlist),
    )

    with httpx.Client() as telegram_client, httpx.Client(
        base_url=settings.internal_api_url, headers=api_headers
    ) as api_client:
        while True:
            try:
                updates = _get_updates(telegram_client, token, last_update_id)
                for update in updates:
                    _handle_update(api_client, telegram_client, token, update)
                    last_update_id = max(
                        last_update_id or 0, update.get("update_id", 0) + 1
                    )
            except Exception as e:
                log.error("gwaihir_loop_error", exc=str(e))
                time.sleep(ERROR_BACKOFF_SECONDS)


if __name__ == "__main__":
    run_forever()
