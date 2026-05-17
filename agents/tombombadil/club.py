"""Group features for Tom Bombadil.

Two responsibilities:

1. **recommend_for_group(viewers)** -- blend taste profiles. Returns
   the film that best matches the intersection of preferred themes
   across the group, falling back to a union when the intersection is
   empty.
2. **schedule_watch_party / weekly cron seed** -- thin wrappers over
   :mod:`agents.galadriel.store.save_job` so the slash command and any
   bring-up script use the same job shape.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from agents.galadriel.models import Job, JobDelivery, JobPayload, JobSchedule
from agents.galadriel.scheduler import Schedule, next_run_ms
from agents.galadriel.store import save_job
from agents.tombombadil.film_knowledge import FilmKnowledge
from core.logging import get_logger

log = get_logger("agents.tombombadil.club")

WEEKLY_NIGHT_JOB_ID = "tom_weekly_club_night"


def _ratings(film: dict, name: str) -> list[float]:
    return [
        w["rating"]
        for w in film.get("watchers", [])
        if (w.get("name") or "").lower() == name.lower() and w.get("rating") is not None
    ]


def recommend_for_group(
    knowledge: FilmKnowledge,
    viewer_names: list[str],
) -> str:
    """Return a Markdown-formatted recommendation that blends taste
    profiles across ``viewer_names``.

    Algorithm:
    1. Collect each viewer's preferred themes from ``self.people``.
    2. ``shared = intersection``; if empty, fall back to the union.
    3. Pick the film with the most theme overlap that none of the
       viewers has watched yet. Ties broken by total ratings count.
    """
    if not viewer_names:
        return "Pass one or more names so I know who I'm blending for."

    profiles = []
    missing: list[str] = []
    for raw_name in viewer_names:
        name = raw_name.strip()
        profile = knowledge.get_person_profile(name)
        if profile:
            profiles.append((name, set(profile.get("preferred_themes", [])),
                              set(profile.get("films_watched", []))))
        else:
            missing.append(name)

    if not profiles:
        return f"I don't know any of: {', '.join(viewer_names)}."

    theme_sets = [t for _, t, _ in profiles]
    watched_union: set[str] = set().union(*(w for _, _, w in profiles))

    shared = set.intersection(*theme_sets) if theme_sets else set()
    fallback = "union" if not shared else "intersection"
    target_themes = shared if shared else set().union(*theme_sets)

    best: dict | None = None
    best_overlap = 0
    for film in knowledge.films:
        if film["title"] in watched_union:
            continue
        overlap = len(set(film.get("themes", [])) & target_themes)
        if overlap > best_overlap:
            best = film
            best_overlap = overlap

    if best is None:
        return (
            "Everyone in the group has already watched the catalog's overlap, "
            "or there are no themes in common yet."
        )

    names = ", ".join(n for n, _, _ in profiles)
    themes = ", ".join(sorted(set(best.get("themes", []))[:4]))
    lines = [
        f"**For {names}** (blend strategy: {fallback}):",
        f"Try **{best['title']}** ({best.get('year', 'n.d.')}).",
        f"**Why:** overlap on {themes}.",
        f"**Group take:** {best.get('group_consensus', '(no group consensus yet)')}",
    ]
    if missing:
        lines.append(f"_Skipped (unknown): {', '.join(missing)}._")
    return "\n".join(lines)


def schedule_watch_party(
    redis,
    *,
    film: str,
    when_iso: str,
    channel_id: str,
    organizer: str,
) -> Job:
    """Register a Galadriel ``at`` job that posts a club-night reminder
    in ``channel_id`` when ``when_iso`` fires.

    The Discord-delivery transport (``mode="discord"``) lands the
    reminder via Redis pub/sub; see
    :mod:`agents.tombombadil.delivery` for the subscriber half.
    """
    # Validate ISO timestamp early.
    datetime.fromisoformat(when_iso)

    sched = Schedule(kind="at", at_iso=when_iso, tz="UTC")
    next_at = next_run_ms(sched)
    now_ms = int(datetime.now().timestamp() * 1000)

    job = Job(
        id=f"watch_party_{uuid4().hex[:8]}",
        name=f"Watch party: {film}",
        schedule=JobSchedule(kind="at", at_iso=when_iso),
        payload=JobPayload(
            kind="agentTurn",
            message=(
                f"Announce: Club night tonight. We're watching **{film}**. "
                f"Hosted by {organizer}. Drop reactions if you're in."
            ),
            timeout_seconds=30,
        ),
        delivery=JobDelivery(mode="discord", to=str(channel_id)),
        delete_after_run=True,
        enabled=True,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        next_run_at_ms=next_at,
    )
    save_job(redis, job)
    log.info("watch_party_scheduled", job_id=job.id, film=film, when=when_iso, channel=channel_id)
    return job


def ensure_weekly_club_night(
    redis,
    *,
    channel_id: str,
    cron_expr: str = "0 19 * * 5",
    tz: str = "America/Los_Angeles",
) -> Job:
    """Idempotently install the weekly Friday-7pm "what are we
    watching?" reminder. Safe to call on every bot startup -- save_job
    overwrites by id.
    """
    sched = Schedule(kind="cron", expr=cron_expr, tz=tz)
    now_ms = int(datetime.now().timestamp() * 1000)
    job = Job(
        id=WEEKLY_NIGHT_JOB_ID,
        name="Weekly club night reminder",
        schedule=JobSchedule(kind="cron", expr=cron_expr, tz=tz),
        payload=JobPayload(
            kind="agentTurn",
            message="It's Friday night. What are we watching this week?",
            timeout_seconds=30,
        ),
        delivery=JobDelivery(mode="discord", to=str(channel_id)),
        delete_after_run=False,
        enabled=True,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        next_run_at_ms=next_run_ms(sched),
    )
    save_job(redis, job)
    log.info("weekly_club_night_ensured", job_id=job.id, cron=cron_expr, tz=tz, channel=channel_id)
    return job


def cmd_club_recommend(knowledge: FilmKnowledge, names_arg: str) -> str:
    """Helper for ``/club recommend``: parse a comma-separated list of
    viewer names and call :func:`recommend_for_group`.
    """
    names = [n.strip() for n in (names_arg or "").split(",") if n.strip()]
    return recommend_for_group(knowledge, names)


def cmd_club_schedule(
    redis,
    knowledge: FilmKnowledge,
    *,
    film: str,
    when_iso: str,
    channel_id: str | int,
    organizer: str,
) -> str:
    """Helper for ``/club schedule``: validates inputs and registers the
    Galadriel job. Returns a confirmation string suitable for the slash
    reply.
    """
    film = (film or "").strip()
    when_iso = (when_iso or "").strip()
    if not film:
        return "Film is required."
    try:
        datetime.fromisoformat(when_iso)
    except ValueError:
        return "When must be an ISO 8601 timestamp, e.g. 2026-05-15T19:00:00."
    if not knowledge.get_film(film):
        # Allow scheduling films that aren't in the catalog yet, but warn.
        warn = f" (Heads up: **{film}** isn't in the catalog yet.)"
    else:
        warn = ""
    job = schedule_watch_party(
        redis,
        film=film,
        when_iso=when_iso,
        channel_id=str(channel_id),
        organizer=organizer,
    )
    return f"Scheduled watch party for **{film}** at {when_iso} (job `{job.id}`).{warn}"


def discord_announce_payload(job: Job, result: dict) -> dict[str, Any]:
    """Shape the JSON payload Galadriel publishes for ``discord`` delivery.

    Kept here (rather than in Galadriel) so the bot subscriber and the
    publisher agree on the shape.
    """
    inner = result.get("result")
    text = None
    if isinstance(inner, dict):
        text = inner.get("reply") or inner.get("text")
    elif isinstance(inner, str):
        text = inner
    text = text or job.payload.text or job.name
    return {
        "job_id": job.id,
        "channel_id": job.delivery.to,
        "text": text,
    }
