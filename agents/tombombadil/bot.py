from __future__ import annotations

import discord
from discord.ext import commands

from agents.tombombadil import memory
from agents.tombombadil.agent import acknowledge_notes, get_response
from agents.tombombadil.identity import resolve as resolve_viewer
from core.config import settings
from core.logging import get_logger, new_trace_id
from core.redis_client import get_redis_sync

log = get_logger("agents.tombombadil.bot")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    log.info("bot_ready", user=str(bot.user))


@bot.event
async def on_message(message):
    new_trace_id()

    if message.author == bot.user:
        return

    viewer = resolve_viewer(str(message.author.id), str(message.author))
    log.info(
        "message_received",
        author=str(message.author),
        viewer=viewer.canonical_name or viewer.discord_name,
        tier=viewer.tier.value,
        channel=str(message.channel),
        content_preview=message.content[:100],
    )

    content_lower = message.content.lower()
    if "film:" in content_lower and "rating" in content_lower:
        log.info("auto_parse_notes", author=str(message.author))
        reply = acknowledge_notes(message.content, viewer=viewer)
        await message.reply(reply)
        log.info("note_processed", response_preview=reply[:100])
        return

    if bot.user.mentioned_in(message):
        content = message.content.replace(f"<@{bot.user.id}>", "").strip()
        scope_key = memory.history_scope_key(message)
        reply = await get_response(scope_key, content, viewer, get_redis_sync())
        await message.reply(reply)
        log.info("mention_response_sent", response_preview=reply[:100])

    await bot.process_commands(message)


def main() -> None:
    if not settings.discord_token:
        raise SystemExit("DISCORD_TOKEN not configured")
    bot.run(settings.discord_token)


if __name__ == "__main__":
    main()
