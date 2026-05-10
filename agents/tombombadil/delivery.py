"""Discord delivery transport for Galadriel-scheduled jobs.

Galadriel runs in its own container and can't import discord.py / hold
a bot session. When a scheduled job has ``delivery.mode = "discord"``
Galadriel pushes the rendered text + target channel id to the
``tom:announce:queue`` Redis list; this module runs as a background
task inside the ``tombombadil`` container, BLPOPs the queue, and posts
to the named channel via the live bot session.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from core.logging import get_logger
from core.redis_client import get_redis_async, get_redis_sync

if TYPE_CHECKING:
    from discord.ext import commands as discord_commands


log = get_logger("agents.tombombadil.delivery")

QUEUE_KEY = "tom:announce:queue"
BLPOP_TIMEOUT_SECONDS = 5


def publish(channel_id: str | int, text: str, *, redis=None) -> None:
    """Enqueue a one-shot announcement for the Discord bot to post.

    Synchronous so Galadriel (sync worker) can call it without
    awaiting. The bot picks it up via the async subscriber below.
    """
    r = redis or get_redis_sync()
    payload = json.dumps({"channel_id": str(channel_id), "text": text})
    r.rpush(QUEUE_KEY, payload)
    log.info("discord_announce_enqueued", channel_id=str(channel_id), text_preview=text[:80])


async def _post_one(bot: discord_commands.Bot, payload: dict) -> None:
    channel_id = payload.get("channel_id")
    text = payload.get("text") or ""
    if not channel_id or not text:
        log.warning("delivery_dropped_invalid", payload_keys=list(payload.keys()))
        return
    try:
        channel = bot.get_channel(int(channel_id)) or await bot.fetch_channel(int(channel_id))
    except Exception as exc:
        log.warning("delivery_channel_unreachable", channel_id=channel_id, exc=str(exc))
        return
    try:
        await channel.send(text)
        log.info("discord_announce_posted", channel_id=channel_id, text_preview=text[:80])
    except Exception as exc:
        log.warning("delivery_post_failed", channel_id=channel_id, exc=str(exc))


async def subscriber_loop(bot: discord_commands.Bot) -> None:
    """Long-running background task. BLPOPs the announce queue and
    posts each message via the bot session.
    """
    r = get_redis_async()
    log.info("delivery_subscriber_started", queue=QUEUE_KEY)
    while True:
        try:
            item = await r.blpop([QUEUE_KEY], timeout=BLPOP_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            log.info("delivery_subscriber_cancelled")
            raise
        except Exception as exc:
            log.warning("delivery_blpop_failed", exc=str(exc))
            await asyncio.sleep(2)
            continue
        if item is None:
            continue
        _, raw = item
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("delivery_payload_unparseable", raw_preview=raw[:120])
            continue
        await _post_one(bot, payload)


def start_subscriber(bot: discord_commands.Bot) -> asyncio.Task:
    """Schedule the subscriber on the bot's running loop. Idempotent --
    repeated calls return the existing task.
    """
    if getattr(bot, "_tom_delivery_task", None) is not None and not bot._tom_delivery_task.done():
        return bot._tom_delivery_task
    task = asyncio.create_task(subscriber_loop(bot))
    bot._tom_delivery_task = task  # type: ignore[attr-defined]
    return task
