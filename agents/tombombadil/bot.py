from __future__ import annotations

import contextlib

import discord
from discord.ext import commands

from agents.tombombadil import delivery, draft_store, guards, memory, metrics
from agents.tombombadil.agent import get_response
from agents.tombombadil.commands import register_commands
from agents.tombombadil.identity import resolve as resolve_viewer
from agents.tombombadil.persistent_memory import save_note
from core.config import settings
from core.logging import get_logger, new_trace_id
from core.redis_client import get_redis_sync

log = get_logger("agents.tombombadil.bot")

CONFIRM_EMOJI = "✅"
REJECT_EMOJI = "❌"

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)
register_commands(bot)


@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        log.info("slash_commands_synced", count=len(synced))
    except Exception as exc:
        log.warning("slash_commands_sync_failed", exc=str(exc))
    delivery.start_subscriber(bot)
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

    if bot.user.mentioned_in(message):
        content = _resolve_mentions(message)
        scope_key = memory.history_scope_key(message)
        redis_client = get_redis_sync()

        refusal = _guard_check(redis_client, viewer, content)
        if refusal is not None:
            await message.reply(refusal)
            return

        reply = await get_response(scope_key, content, viewer, redis_client)
        sent = await message.reply(reply)
        metrics.REPLIES.labels(tier=viewer.tier.value).inc()
        log.info("mention_response_sent", response_preview=reply[:100])

        await _offer_pending_draft(message, sent, scope_key, viewer, redis_client)

    await bot.process_commands(message)


def _resolve_mentions(message) -> str:
    """Rewrite raw ``<@id>`` tokens to human-readable display names.

    The LLM ignores opaque mention tokens (or worse, addresses the
    wrong person) when a message references multiple Discord users.
    Substituting the display name keeps the instruction semantically
    clear: ``Say hello to <@1176...>`` becomes ``Say hello to Wes Prater``.
    The bot's own mention is stripped entirely.
    """
    content = message.content
    for mentioned in message.mentions:
        if mentioned == bot.user:
            content = content.replace(f"<@{mentioned.id}>", "")
            content = content.replace(f"<@!{mentioned.id}>", "")
            continue
        name = getattr(mentioned, "display_name", None) or str(mentioned)
        content = content.replace(f"<@{mentioned.id}>", name)
        content = content.replace(f"<@!{mentioned.id}>", name)
    return content.strip()


def _guard_check(redis_client, viewer, content: str) -> str | None:
    """Layered guards: ban list, prompt length, rate limit. Returns a
    refusal string when any trip; otherwise ``None`` to proceed.
    Solomon (owner tier) bypasses the rate limit but not the other two.
    """
    if guards.is_banned(redis_client, viewer.discord_id):
        metrics.GUARDS_TRIPPED.labels(kind="ban").inc()
        log.info("guard_ban", discord_id=viewer.discord_id)
        return "I've been asked not to engage with you. Sorry."
    too_long = guards.check_prompt_length(content)
    if too_long is not None:
        metrics.GUARDS_TRIPPED.labels(kind="prompt_too_long").inc()
        log.info("guard_prompt_too_long", discord_id=viewer.discord_id, length=len(content))
        return too_long
    rate_limited = guards.check_and_consume(
        redis_client, viewer.discord_id, is_owner=viewer.is_owner
    )
    if rate_limited is not None:
        metrics.GUARDS_TRIPPED.labels(kind="rate_limit").inc()
        log.info("guard_rate_limit", discord_id=viewer.discord_id)
        return rate_limited
    return None


async def _offer_pending_draft(original, sent, scope_key, viewer, redis_client) -> None:
    """If ``agent.get_response`` queued a NoteDraft, post a follow-up
    asking the message author to react ✅ to confirm the log.
    """
    draft = draft_store.pop_pending(redis_client, scope_key)
    if draft is None:
        return

    prompt = (
        f"React {CONFIRM_EMOJI} to log **{draft.film}** ({draft.rating:g}/10) "
        f"for {draft.viewer}, or {REJECT_EMOJI} to skip."
    )
    try:
        confirm_msg = await original.reply(prompt, mention_author=False)
        await confirm_msg.add_reaction(CONFIRM_EMOJI)
        await confirm_msg.add_reaction(REJECT_EMOJI)
        draft_store.bind_to_message(
            redis_client,
            confirm_msg.id,
            draft,
            requester_discord_id=viewer.discord_id,
            scope=scope_key,
        )
        metrics.DRAFTS_OFFERED.inc()
        log.info(
            "draft_offered",
            message_id=confirm_msg.id,
            film=draft.film,
            rating=draft.rating,
            viewer=draft.viewer,
        )
    except Exception as exc:
        log.warning("draft_offer_failed", exc=str(exc))


@bot.event
async def on_reaction_add(reaction, user):
    if user == bot.user:
        return
    if str(reaction.emoji) not in (CONFIRM_EMOJI, REJECT_EMOJI):
        return

    message_id = reaction.message.id
    redis_client = get_redis_sync()
    draft = draft_store.get_draft(redis_client, message_id)
    if not draft:
        return

    # Only the original requester can commit or skip; ignore others.
    if str(user.id) != draft.get("requester_discord_id"):
        return

    if str(reaction.emoji) == REJECT_EMOJI:
        draft_store.delete_draft(redis_client, message_id)
        metrics.DRAFTS_SKIPPED.inc()
        log.info("draft_skipped", message_id=message_id, film=draft.get("film"))
        with contextlib.suppress(Exception):
            await reaction.message.reply("Skipped.", mention_author=False, delete_after=10)
        return

    # CONFIRM_EMOJI path
    try:
        rating = float(draft.get("rating", "0"))
    except ValueError:
        rating = 0.0
    success, msg = save_note(
        redis_client,
        film=draft.get("film", ""),
        watcher=draft.get("viewer", "Unknown"),
        rating=rating,
        reaction="",
        themes="",
    )
    draft_store.delete_draft(redis_client, message_id)

    confirmation = (
        f"OK {draft.get('film')} ({rating:g}/10) logged" if success else msg
    )
    try:
        await reaction.message.reply(confirmation, mention_author=False)
    except Exception as exc:
        log.warning("draft_commit_reply_failed", exc=str(exc))

    if success:
        metrics.DRAFTS_COMMITTED.inc()
    log.info(
        "draft_committed" if success else "draft_commit_failed",
        message_id=message_id,
        film=draft.get("film"),
        viewer=draft.get("viewer"),
    )


def main() -> None:
    if not settings.discord_token:
        raise SystemExit("DISCORD_TOKEN not configured")
    bot.run(settings.discord_token)


if __name__ == "__main__":
    main()
