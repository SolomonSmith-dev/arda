"""Slash-command handlers for Tom Bombadil.

Each ``cmd_*`` function is pure (takes the invoking viewer plus any
arguments, returns a string response). The discord-side wiring sits in
``register_commands(bot)`` so the testable logic is independent of
discord.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents.tombombadil import club, guards, memory, metrics
from agents.tombombadil.film_knowledge import FilmKnowledge
from agents.tombombadil.identity import Tier, Viewer, set_role_override
from agents.tombombadil.persistent_memory import delete_note, save_note
from core.logging import get_logger

if TYPE_CHECKING:
    from discord.ext import commands


log = get_logger("agents.tombombadil.commands")

_film_knowledge = FilmKnowledge()


def cmd_rate(redis_client, viewer: Viewer, film: str, rating: float) -> str:
    """``/rate film:<title> rating:<0-10>`` — direct save, bypasses the
    react-to-confirm draft flow. Strangers can't log notes (no canonical
    name to attribute them to).
    """
    if not viewer.canonical_name:
        return (
            "I don't have a canonical name for you yet, so I can't file "
            "this rating. Ask Solomon to add you to data/tombombadil/identity.yaml."
        )
    film = film.strip()
    if not film:
        return "Film is required."
    try:
        rating = float(rating)
    except (TypeError, ValueError):
        return "Rating must be numeric."
    if not 0 <= rating <= 10:
        return "Rating must be between 0 and 10."

    success, msg = save_note(
        redis_client,
        film=film,
        watcher=viewer.canonical_name,
        rating=rating,
        reaction="",
        themes="",
    )
    if success:
        return f"OK **{film}** ({rating:g}/10) logged for {viewer.canonical_name}."
    return msg


def cmd_recommend(viewer: Viewer, for_name: str | None = None) -> str:
    """``/recommend [for:<name>]`` — wrap ``FilmKnowledge.recommend_for_person``.
    Defaults to recommending for the invoking viewer.
    """
    target = (for_name or viewer.canonical_name or "").strip()
    if not target:
        return (
            "I don't have a name to recommend for. Pass `for:<name>` or "
            "ask Solomon to add you to the identity map first."
        )
    rec = _film_knowledge.recommend_for_person(target)
    if rec is None:
        return f"I don't know {target} well enough to recommend something yet."
    return rec


def cmd_club_stats() -> str:
    """``/club stats`` — quick aggregate over the in-memory film database."""
    films = _film_knowledge.films
    people = _film_knowledge.people
    if not films:
        return "No films in the club catalog yet."

    rated_films: list[tuple[str, float, int]] = []
    for f in films:
        ratings = [w["rating"] for w in f.get("watchers", []) if w.get("rating") is not None]
        if ratings:
            rated_films.append((f["title"], sum(ratings) / len(ratings), len(ratings)))

    rated_films.sort(key=lambda x: -x[1])
    top = rated_films[:5]

    most_watched = max(rated_films, key=lambda x: x[2], default=None)
    most_active = max(
        ((name, p.get("avg_rating", 0), len(p.get("films_watched", []))) for name, p in people.items()),
        key=lambda x: x[2],
        default=None,
    )

    lines = ["**Club stats**"]
    if top:
        lines.append("Top-rated:")
        lines.extend(f"- **{t}** -- avg {avg:.1f} ({n} watchers)" for t, avg, n in top)
    if most_watched:
        lines.append(f"Most-watched: **{most_watched[0]}** ({most_watched[2]} watchers)")
    if most_active:
        lines.append(f"Most-active reviewer: **{most_active[0]}** ({most_active[2]} films)")
    return "\n".join(lines)


async def cmd_forget(redis_client, viewer: Viewer, scope_key: str | None, scope: str) -> str:
    """``/forget scope:<short|long|prefs|all>`` -- wipe per-scope state
    for the invoking viewer.
    """
    scope = (scope or "").lower().strip()
    valid = {"short", "long", "prefs", "all"}
    if scope not in valid:
        return f"Scope must be one of: {', '.join(sorted(valid))}."

    removed_parts: list[str] = []
    if scope in ("short", "all") and scope_key:
        memory.clear_history(redis_client, scope_key)
        removed_parts.append("recent conversation history in this channel")
    if scope in ("prefs", "all"):
        memory.clear_prefs(redis_client, viewer.discord_id)
        removed_parts.append("your saved preferences")
    if scope in ("long", "all"):
        deleted = await memory.forget_facts(viewer)
        if deleted:
            removed_parts.append(f"{deleted} long-term fact chunk(s) attributed to you")
        else:
            removed_parts.append("long-term facts (nothing on file)")

    if not removed_parts:
        return "Nothing was cleared. Did you forget the scope?"
    return "Cleared: " + "; ".join(removed_parts) + "."


def cmd_whoami(viewer: Viewer) -> str:
    """``/whoami`` -- show how Tom sees the invoking user."""
    name = viewer.canonical_name or "(not yet mapped)"
    return (
        f"**Tier**: `{viewer.tier.value}`\n"
        f"**Canonical name**: {name}\n"
        f"**Discord**: {viewer.discord_name} (id `{viewer.discord_id}`)\n"
        f"**Owner**: {'yes' if viewer.tier is Tier.SOLOMON else 'no'}"
    )


def cmd_setpref(redis_client, viewer: Viewer, key: str, value: str) -> str:
    """``/setpref key:<name> value:<value>`` — explicit pref control (D6)."""
    key = (key or "").strip()
    value = (value or "").strip()
    if key not in memory.PREF_KEYS:
        allowed = ", ".join(sorted(memory.PREF_KEYS))
        return f"Unknown pref `{key}`. Allowed: {allowed}."
    if value.lower() in ("", "clear", "unset"):
        memory.clear_pref(redis_client, viewer.discord_id, key)
        return f"Cleared `{key}`."
    memory.set_pref(redis_client, viewer.discord_id, key, value)
    return f"Set `{key}` = `{value}`."


def cmd_unrate(redis_client, viewer: Viewer, film: str) -> str:
    """``/unrate film:<title>`` — delete the most recent note (D9)."""
    if not viewer.canonical_name:
        return (
            "I don't have a canonical name for you yet, so I can't remove "
            "a rating. Ask Solomon to add you to data/tombombadil/identity.yaml."
        )
    ok, msg = delete_note(redis_client, film=film, watcher=viewer.canonical_name)
    if ok:
        return f"Removed **{film.strip()}** for {viewer.canonical_name}."
    return msg


def _require_owner(viewer: Viewer) -> str | None:
    if viewer.is_owner:
        return None
    return "Owner only."


def cmd_ban(redis_client, viewer: Viewer, discord_id: str) -> str:
    """``/ban id:<discord_id>`` — owner-only (D10)."""
    if err := _require_owner(viewer):
        return err
    target = (discord_id or "").strip()
    if not target:
        return "Discord id is required."
    guards.ban(redis_client, target)
    return f"Banned `{target}`."


def cmd_unban(redis_client, viewer: Viewer, discord_id: str) -> str:
    """``/unban id:<discord_id>`` — owner-only (D10)."""
    if err := _require_owner(viewer):
        return err
    target = (discord_id or "").strip()
    if not target:
        return "Discord id is required."
    guards.unban(redis_client, target)
    return f"Unbanned `{target}`."


def cmd_sync(redis_client, viewer: Viewer) -> str:
    """``/sync`` — owner-only Letterboxd RSS sync + cron seed (D10)."""
    if err := _require_owner(viewer):
        return err
    from agents.tombombadil.sync_job import ensure_letterboxd_sync_cron, run_sync

    ensure_letterboxd_sync_cron(redis_client)
    result = run_sync(redis_client)
    return (
        f"Sync complete: fetched={result.fetched} new={result.new} "
        f"skipped={result.skipped} saved={result.saved} "
        f"errors={len(result.errors)}. Daily cron ensured."
    )


def cmd_setrole(
    redis_client,
    viewer: Viewer,
    discord_id: str,
    tier: str,
    canonical_name: str | None = None,
) -> str:
    """``/setrole id:<discord_id> tier:<solomon|regular|stranger>`` (D10)."""
    if err := _require_owner(viewer):
        return err
    target = (discord_id or "").strip()
    if not target:
        return "Discord id is required."
    tier_raw = (tier or "").strip().lower()
    try:
        new_tier = Tier(tier_raw)
    except ValueError:
        return "Tier must be one of: solomon, regular, stranger."
    name = (canonical_name or "").strip() or None
    if new_tier is not Tier.STRANGER and not name:
        return "canonical_name is required for solomon/regular tiers."
    set_role_override(
        redis_client,
        target,
        tier=new_tier,
        canonical_name=name,
    )
    if new_tier is Tier.STRANGER:
        return f"Set `{target}` → stranger (override)."
    return f"Set `{target}` → {new_tier.value} as **{name}** (override)."


def register_commands(bot: commands.Bot) -> None:
    """Bind ``cmd_*`` functions to the bot's app-command tree.

    Imported lazily by ``bot.py`` so unit tests can exercise the
    handlers without paying the discord.py import cost.
    """
    import discord
    from discord import app_commands

    from agents.tombombadil import memory as memory_mod
    from agents.tombombadil.identity import resolve as resolve_viewer
    from core.redis_client import get_redis_sync

    def _viewer(interaction: discord.Interaction) -> Viewer:
        redis = get_redis_sync()
        return resolve_viewer(str(interaction.user.id), str(interaction.user), redis=redis)

    @bot.tree.command(name="rate", description="Log a film rating to the club store")
    @app_commands.describe(film="Film title", rating="Rating 0-10 (decimals OK)")
    async def _rate(interaction: discord.Interaction, film: str, rating: float):
        metrics.SLASH_COMMANDS.labels(name="rate").inc()
        reply = cmd_rate(get_redis_sync(), _viewer(interaction), film, rating)
        await interaction.response.send_message(reply, ephemeral=False)

    @bot.tree.command(name="recommend", description="Get a film recommendation")
    @app_commands.describe(for_name="Recommend for someone else (optional)")
    async def _recommend(interaction: discord.Interaction, for_name: str | None = None):
        metrics.SLASH_COMMANDS.labels(name="recommend").inc()
        reply = cmd_recommend(_viewer(interaction), for_name)
        await interaction.response.send_message(reply, ephemeral=False)

    club_group = app_commands.Group(name="club", description="Film club aggregates and scheduling")

    @club_group.command(name="stats", description="Top-rated, most-watched, most-active reviewer")
    async def _club_stats(interaction: discord.Interaction):
        metrics.SLASH_COMMANDS.labels(name="club_stats").inc()
        reply = cmd_club_stats()
        await interaction.response.send_message(reply, ephemeral=False)

    @club_group.command(name="recommend", description="Blend tastes across multiple viewers")
    @app_commands.describe(names="Comma-separated viewer names (e.g. Solomon Smith, Brian)")
    async def _club_recommend(interaction: discord.Interaction, names: str):
        metrics.SLASH_COMMANDS.labels(name="club_recommend").inc()
        reply = club.cmd_club_recommend(_film_knowledge, names)
        await interaction.response.send_message(reply, ephemeral=False)

    @club_group.command(name="schedule", description="Schedule a watch party")
    @app_commands.describe(
        film="Title of the film",
        when="ISO 8601 timestamp, e.g. 2026-05-15T19:00:00",
    )
    async def _club_schedule(interaction: discord.Interaction, film: str, when: str):
        metrics.SLASH_COMMANDS.labels(name="club_schedule").inc()
        viewer = _viewer(interaction)
        reply = club.cmd_club_schedule(
            get_redis_sync(),
            _film_knowledge,
            film=film,
            when_iso=when,
            channel_id=interaction.channel_id,
            organizer=viewer.canonical_name or viewer.discord_name,
        )
        await interaction.response.send_message(reply, ephemeral=False)

    bot.tree.add_command(club_group)

    @bot.tree.command(name="forget", description="Wipe your stored state (short/long/prefs/all)")
    @app_commands.describe(scope="What to wipe: short | long | prefs | all")
    async def _forget(interaction: discord.Interaction, scope: str):
        metrics.SLASH_COMMANDS.labels(name="forget").inc()
        viewer = _viewer(interaction)
        scope_key = memory_mod.history_scope_key(interaction)
        reply = await cmd_forget(get_redis_sync(), viewer, scope_key, scope)
        await interaction.response.send_message(reply, ephemeral=True)

    @bot.tree.command(name="whoami", description="Show how Tom sees you")
    async def _whoami(interaction: discord.Interaction):
        metrics.SLASH_COMMANDS.labels(name="whoami").inc()
        await interaction.response.send_message(cmd_whoami(_viewer(interaction)), ephemeral=True)

    @bot.tree.command(name="setpref", description="Set a personal preference")
    @app_commands.describe(
        key="Preference key (suppress_films | preferred_tone | do_not_log)",
        value="Value to set, or 'clear' to unset",
    )
    async def _setpref(interaction: discord.Interaction, key: str, value: str):
        metrics.SLASH_COMMANDS.labels(name="setpref").inc()
        reply = cmd_setpref(get_redis_sync(), _viewer(interaction), key, value)
        await interaction.response.send_message(reply, ephemeral=True)

    @bot.tree.command(name="unrate", description="Remove your most recent rating for a film")
    @app_commands.describe(film="Film title to remove")
    async def _unrate(interaction: discord.Interaction, film: str):
        metrics.SLASH_COMMANDS.labels(name="unrate").inc()
        reply = cmd_unrate(get_redis_sync(), _viewer(interaction), film)
        await interaction.response.send_message(reply, ephemeral=True)

    @bot.tree.command(name="ban", description="Ban a Discord user from Tom (owner only)")
    @app_commands.describe(discord_id="Target Discord user id")
    async def _ban(interaction: discord.Interaction, discord_id: str):
        metrics.SLASH_COMMANDS.labels(name="ban").inc()
        reply = cmd_ban(get_redis_sync(), _viewer(interaction), discord_id)
        await interaction.response.send_message(reply, ephemeral=True)

    @bot.tree.command(name="unban", description="Unban a Discord user (owner only)")
    @app_commands.describe(discord_id="Target Discord user id")
    async def _unban(interaction: discord.Interaction, discord_id: str):
        metrics.SLASH_COMMANDS.labels(name="unban").inc()
        reply = cmd_unban(get_redis_sync(), _viewer(interaction), discord_id)
        await interaction.response.send_message(reply, ephemeral=True)

    @bot.tree.command(name="sync", description="Run Letterboxd RSS sync now (owner only)")
    async def _sync(interaction: discord.Interaction):
        metrics.SLASH_COMMANDS.labels(name="sync").inc()
        await interaction.response.defer(ephemeral=True)
        reply = cmd_sync(get_redis_sync(), _viewer(interaction))
        await interaction.followup.send(reply, ephemeral=True)

    @bot.tree.command(name="setrole", description="Override a user's club tier (owner only)")
    @app_commands.describe(
        discord_id="Target Discord user id",
        tier="solomon | regular | stranger",
        canonical_name="Canonical club name (required for solomon/regular)",
    )
    async def _setrole(
        interaction: discord.Interaction,
        discord_id: str,
        tier: str,
        canonical_name: str | None = None,
    ):
        metrics.SLASH_COMMANDS.labels(name="setrole").inc()
        reply = cmd_setrole(
            get_redis_sync(),
            _viewer(interaction),
            discord_id,
            tier,
            canonical_name,
        )
        await interaction.response.send_message(reply, ephemeral=True)

    log.info("slash_commands_registered", count=12)
