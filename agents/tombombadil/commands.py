"""Slash-command handlers for Tom Bombadil.

Each ``cmd_*`` function is pure (takes the invoking viewer plus any
arguments, returns a string response). The discord-side wiring sits in
``register_commands(bot)`` so the testable logic is independent of
discord.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents.tombombadil import club, memory
from agents.tombombadil.film_knowledge import FilmKnowledge
from agents.tombombadil.identity import Tier, Viewer
from agents.tombombadil.persistent_memory import save_note
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


def cmd_forget(redis_client, viewer: Viewer, scope_key: str | None, scope: str) -> str:
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
        deleted = memory.forget_facts(viewer)
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

    @bot.tree.command(name="rate", description="Log a film rating to the club store")
    @app_commands.describe(film="Film title", rating="Rating 0-10 (decimals OK)")
    async def _rate(interaction: discord.Interaction, film: str, rating: float):
        viewer = resolve_viewer(str(interaction.user.id), str(interaction.user))
        reply = cmd_rate(get_redis_sync(), viewer, film, rating)
        await interaction.response.send_message(reply, ephemeral=False)

    @bot.tree.command(name="recommend", description="Get a film recommendation")
    @app_commands.describe(for_name="Recommend for someone else (optional)")
    async def _recommend(interaction: discord.Interaction, for_name: str | None = None):
        viewer = resolve_viewer(str(interaction.user.id), str(interaction.user))
        reply = cmd_recommend(viewer, for_name)
        await interaction.response.send_message(reply, ephemeral=False)

    club_group = app_commands.Group(name="club", description="Film club aggregates and scheduling")

    @club_group.command(name="stats", description="Top-rated, most-watched, most-active reviewer")
    async def _club_stats(interaction: discord.Interaction):
        reply = cmd_club_stats()
        await interaction.response.send_message(reply, ephemeral=False)

    @club_group.command(name="recommend", description="Blend tastes across multiple viewers")
    @app_commands.describe(names="Comma-separated viewer names (e.g. Solomon Smith, Brian)")
    async def _club_recommend(interaction: discord.Interaction, names: str):
        reply = club.cmd_club_recommend(_film_knowledge, names)
        await interaction.response.send_message(reply, ephemeral=False)

    @club_group.command(name="schedule", description="Schedule a watch party")
    @app_commands.describe(
        film="Title of the film",
        when="ISO 8601 timestamp, e.g. 2026-05-15T19:00:00",
    )
    async def _club_schedule(interaction: discord.Interaction, film: str, when: str):
        viewer = resolve_viewer(str(interaction.user.id), str(interaction.user))
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
        viewer = resolve_viewer(str(interaction.user.id), str(interaction.user))
        scope_key = memory_mod.history_scope_key(interaction)
        reply = cmd_forget(get_redis_sync(), viewer, scope_key, scope)
        await interaction.response.send_message(reply, ephemeral=True)

    @bot.tree.command(name="whoami", description="Show how Tom sees you")
    async def _whoami(interaction: discord.Interaction):
        viewer = resolve_viewer(str(interaction.user.id), str(interaction.user))
        await interaction.response.send_message(cmd_whoami(viewer), ephemeral=True)

    log.info("slash_commands_registered", count=5)
