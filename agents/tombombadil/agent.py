from __future__ import annotations

import re
from typing import Any, ClassVar

from agents.base import BaseAgent
from agents.conduct import CONDUCT_PROMPT
from agents.tombombadil import draft_store, memory, metrics
from agents.tombombadil.fact_extractor import ExtractedFacts
from agents.tombombadil.fact_extractor import extract as extract_facts
from agents.tombombadil.film_knowledge import FilmKnowledge
from agents.tombombadil.identity import Tier, Viewer, all_known
from agents.tombombadil.identity import resolve as resolve_viewer
from agents.tombombadil.llm import build_chat_client
from core.config import Tier as ConfigTier
from core.config import settings
from core.logging import get_logger
from core.models import AgentResult, AgentTask, TaskStatus
from core.redis_client import get_redis_sync

log = get_logger("agents.tombombadil.agent")

MAX_REPLY_TOKENS = 1024
LLM_TIMEOUT_SECONDS = 30
# Tom is a chat persona, but he quotes recorded film ratings verbatim. High
# temperature makes him embellish them (9/10 becomes 10/10). Keep sampling
# tight; the personality comes from the system prompt, not from sampling.
CHAT_TEMPERATURE = 0.2

# Pre-V6 / imitation leaks: assistant text starting with ``[Name] `` or
# ``[@Name] ``. Negative lookahead skips ``[mock...]`` (dev mock replies).
_SPEAKER_PREFIX_RE = re.compile(r"^\[@?(?!mock\b)[^\]]{1,64}\]\s+")


_llm = build_chat_client()
_film_knowledge = FilmKnowledge()


_SELF_DESCRIPTION = (
    "You are Tom Bombadil, a specialist agent in the ARDA stack.\n"
    "- Sauron (Claude-backed, LangGraph + native tool_use) is the orchestrator "
    "that routes messages by intent.\n"
    "- You handle film and club topics.\n"
    "- Finrod provides long-term retrieval via LlamaIndex-backed RAG\n"
    "  (in-memory by default; Milvus available under the `[full]` extra).\n"
    "- Galadriel runs cron jobs and watch-party reminders.\n"
    "- Gwaihir is the Telegram bot for ops messages.\n"
    "- You run in Docker on a Linux home server, share Redis with the rest of\n"
    "  the stack, reply via discord.py, and use Claude Haiku via the anthropic\n"
    f"  SDK directly (model: {settings.specialist_model}).\n"
    "When asked about your architecture, answer with these specifics, not\n"
    "generic AI-101."
)


def _suppress_films(prefs: dict[str, str]) -> bool:
    return prefs.get("suppress_films") == "1"


def _identity_block(viewer: Viewer) -> str:
    name = viewer.canonical_name or viewer.discord_name
    known = ", ".join(all_known()) or "no one configured yet"
    return (
        f"You are speaking with {name} (tier={viewer.tier.value}, "
        f"discord_id={viewer.discord_id}). Other recognised club members: {known}. "
        "When messages from multiple people appear in conversation history, "
        "each one is prefixed with [name]; attribute opinions to the speaker "
        "in the bracket, not to whoever you are replying to right now."
    )


def _club_roster_block() -> str | None:
    """Compact roster of every viewer in ``FILM_DATABASE['people']``.

    Without this, Tom's per-viewer film summary leaves him blind when a
    user asks about ANOTHER club member ("How did Anthony rate his
    films?"). The roster gives the LLM enough context to answer those
    questions from seed data without inventing ratings.
    """
    if not _film_knowledge.people:
        return None
    lines: list[str] = ["Other recognised club members and their profiles:"]
    for name, profile in _film_knowledge.people.items():
        avg = profile.get("avg_rating", 0)
        watched = ", ".join(profile.get("films_watched") or []) or "none yet"
        themes = ", ".join(profile.get("preferred_themes") or [])
        line = f"- {name} (avg {avg}; watched: {watched}"
        if themes:
            line += f"; prefers: {themes}"
        line += ")"
        lines.append(line)
    return "\n".join(lines)


_LETTERBOXD_PREAMBLE = (
    "FILM RATINGS LOOKUP -- STRICT RULES:\n"
    "1. The list below is the ONLY source of truth for the viewer's ratings. "
    "Every rated film is in it.\n"
    "2. When asked about a specific film, find it and quote the rating EXACTLY "
    "(e.g. '9/10', not '10/10').\n"
    "3. If a film is NOT in the list, say 'I don't see it in your ratings'. "
    "Do NOT guess, do NOT invent a score.\n"
    "4. When asked for 'top N' or 'highest rated', use the Top-rated line "
    "verbatim. Do NOT pad the list with films that aren't there.\n"
    "5. Watch dates are NOT in this data. Say so if asked.\n"
    "6. Never claim to be 'checking Letterboxd live' -- you have a snapshot.\n\n"
)


def _verified_film_facts(text: str, viewer_name: str) -> str | None:
    """Pre-resolve ratings for every known title named in ``text``.

    Tom confabulates ratings even with the full list in context: the
    summary is a ~7K-token haystack and attention over it is unreliable.
    Doing the lookup deterministically here and handing the model a short
    block for the *current* query leaves it nothing to guess at.

    Returns None when the message names no known film, so the block is
    only spent when it is load-bearing.
    """
    text_lower = text.lower()
    facts: list[str] = []
    seen: set[str] = set()
    for film in _film_knowledge.films:
        title = (film.get("title") or "").strip()
        if not title or title.lower() in seen:
            continue
        # Films are stored under the full "Title: Subtitle" name, but people
        # say the short form ("Ghost Dog"). Match either.
        candidates = {title.lower(), title.split(":", 1)[0].strip().lower()}
        if not any(c and re.search(rf"\b{re.escape(c)}\b", text_lower) for c in candidates):
            continue
        seen.add(title.lower())
        watcher = next(
            (w for w in film.get("watchers", []) if w.get("name") == viewer_name),
            None,
        )
        if watcher is None:
            facts.append(f"- {title}: {viewer_name} has NOT rated this")
        elif watcher.get("rating") is None:
            facts.append(f"- {title}: {viewer_name} watched but did not rate")
        else:
            facts.append(
                f"- {title}: {viewer_name} rated this {float(watcher['rating']):g}/10"
            )
    if not facts:
        return None
    return (
        "VERIFIED RATINGS FOR THIS QUERY -- use these EXACTLY, do NOT round, "
        "do NOT contradict:\n" + "\n".join(facts)
    )


def _film_summary_block(viewer: Viewer, prefs: dict[str, str]) -> str | None:
    if _suppress_films(prefs):
        metrics.PREF_SUPPRESSED.inc()
        return (
            f"{viewer.canonical_name or viewer.discord_name} has asked you NOT "
            "to bring up films unprompted. Only discuss films if they explicitly "
            "mention one in this message."
        )
    if viewer.tier is Tier.STRANGER or not viewer.canonical_name:
        return (
            f"{viewer.discord_name} has no film history yet. Ask, don't fabricate. "
            "Never invent ratings or favorites for someone whose data you don't have."
        )
    summary = _film_knowledge.get_user_summary(viewer.canonical_name)
    if not summary:
        return (
            f"{viewer.canonical_name} is a recognised club member but has no "
            "recorded ratings yet. Ask, don't fabricate."
        )
    return f"{_LETTERBOXD_PREAMBLE}{summary}"


def _prefs_block(prefs: dict[str, str]) -> str | None:
    relevant = {k: v for k, v in prefs.items() if v}
    if not relevant:
        return None
    lines: list[str] = ["This user has prior preferences on file:"]
    if relevant.get("suppress_films") == "1":
        lines.append("- Do not bring up films or movies unless they raise the topic.")
    if relevant.get("preferred_tone"):
        lines.append(f"- Preferred tone: {relevant['preferred_tone']}.")
    if relevant.get("do_not_log") == "1":
        lines.append("- Do not persist new notes or facts about this user.")
    return "\n".join(lines) if len(lines) > 1 else None


def _recalled_block(facts: list[str]) -> str | None:
    if not facts:
        return None
    bullets = "\n".join(f"- {f}" for f in facts)
    return (
        "Relevant things this user told you in earlier sessions (lower-weight "
        f"than the current message, but use them if helpful):\n{bullets}"
    )


def _build_system_prompt(
    viewer: Viewer,
    prefs: dict[str, str],
    recalled_facts: list[str],
    text: str = "",
) -> str:
    """Concatenate every system-level instruction into a single string
    for Anthropic's top-level ``system`` parameter. Blocks are joined
    by blank lines so the model sees them as discrete sections."""
    blocks: list[str] = [
        CONDUCT_PROMPT,
        _SELF_DESCRIPTION,
        _identity_block(viewer),
    ]
    if not _suppress_films(prefs):
        roster = _club_roster_block()
        if roster:
            blocks.append(roster)
    film_block = _film_summary_block(viewer, prefs)
    if film_block:
        blocks.append(film_block)
    if text and not _suppress_films(prefs) and viewer.canonical_name:
        verified = _verified_film_facts(text, viewer.canonical_name)
        if verified:
            blocks.append(verified)
    prefs_block = _prefs_block(prefs)
    if prefs_block:
        blocks.append(prefs_block)
    recalled = _recalled_block(recalled_facts)
    if recalled:
        blocks.append(recalled)
    return "\n\n".join(blocks)


def _strip_leaked_speaker_prefix(content: str) -> str:
    """Remove a leading ``[Name] `` / ``[@Name] `` tag from assistant text (D1 / V6).

    New turns are stored clean; this heals pre-V6 Redis entries and any
    fresh LLM imitation leak before history reinjection or persistence.
    Leaves ``[mock...]`` alone so mock-mode replies stay identifiable.
    """
    if not content:
        return content
    cleaned = content
    # Strip at most a couple of stacked prefixes (pathological history).
    for _ in range(3):
        nxt = _SPEAKER_PREFIX_RE.sub("", cleaned, count=1)
        if nxt == cleaned:
            break
        cleaned = nxt
    return cleaned


def _history_messages(turns: list[memory.Turn]) -> list[dict[str, Any]]:
    """Render persisted turns as Anthropic-shaped messages
    (``{role, content}`` dicts).

    Only USER turns get the ``[viewer] ...`` speaker prefix -- the LLM
    needs that to disambiguate who said what in multi-user channels.
    ASSISTANT turns must NOT be prefixed: when the model sees its own
    prior replies wrapped in ``[Solomon Smith] ...`` it imitates that
    shape on the next response (live regression: bot replied
    ``[@Solomon Smith] Hello Patrick!``). Stale prefixed assistant
    entries are stripped here so they cannot re-seed the leak (D1).
    """
    msgs: list[dict[str, Any]] = []
    for t in turns:
        if t.role == "user":
            content = f"[{t.viewer}] {t.content}" if t.viewer else t.content
            msgs.append({"role": "user", "content": content})
        else:
            msgs.append({"role": "assistant", "content": _strip_leaked_speaker_prefix(t.content)})
    return msgs


def _extract_text(response: Any) -> str:
    """Pull plain text out of an Anthropic Message response. Skips
    non-text content blocks (none expected for our chat flow, but the
    SDK can return tool_use blocks in general). Empty string on no
    text blocks; caller decides the fallback message."""
    pieces: list[str] = []
    for block in getattr(response, "content", []) or []:
        block_type = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        if block_type == "text":
            text = getattr(block, "text", None) or (
                block.get("text") if isinstance(block, dict) else ""
            )
            if text:
                pieces.append(text)
    return "".join(pieces).strip()


def _stranger_onboarding_reply(viewer: Viewer) -> str:
    """Spec 5.1 first-contact template for ``Tier.STRANGER``.

    Templated (not LLM) so MockLLM cannot break the contract and so
    every first-time stranger gets a deterministic greeting + next
    action (D7).
    """
    name = viewer.discord_name or "there"
    return (
        f"Hey {name} — haven't seen you here before. "
        "I run the film club's notes here: ratings, recommendations, "
        "and club chatter. "
        "Try /whoami to see how I see you, or just tell me what you've "
        "been watching."
    )


async def get_response(
    scope_key: str,
    text: str,
    viewer: Viewer,
    redis_client=None,
    *,
    offer_stranger_onboarding: bool = False,
) -> str:
    """Generate a reply for ``viewer``'s message.

    Side effects (best-effort, swallowed on failure):
    - Appends both turns to the per-scope short-term history.
    - Runs the rule-based fact extractor and persists prefs / notes /
      free-facts. The fact extractor runs *after* the reply is sent so
      response latency is unchanged.

    ``offer_stranger_onboarding`` is True only for the Discord bot path
    (spec 5.1 / D7). Sauron/API dispatches synthesise a stranger viewer
    and must keep the normal LLM path.
    """
    if not text or not text.strip():
        return "Please provide a message"

    redis_client = redis_client or get_redis_sync()
    log.info(
        "llm_request_start",
        scope=scope_key,
        viewer=viewer.canonical_name or viewer.discord_name,
        tier=viewer.tier.value,
        text_length=len(text),
    )

    prefs = memory.get_prefs(redis_client, viewer.discord_id)
    recalled = await memory.recall_facts(viewer, text)
    history = memory.recent_turns(redis_client, scope_key)

    # Spec 5.1 / D7: first-contact Discord strangers get a templated
    # onboarding paragraph. Subsequent turns (and all Sauron/API calls)
    # fall through to the normal LLM path.
    if (
        offer_stranger_onboarding
        and viewer.tier is Tier.STRANGER
        and not history
    ):
        onboarding_reply = _stranger_onboarding_reply(viewer)
        log.info("stranger_onboarding_reply", scope=scope_key, viewer=viewer.discord_name)
        try:
            memory.append_turn(redis_client, scope_key, viewer, "user", text)
            memory.append_turn(redis_client, scope_key, viewer, "assistant", onboarding_reply)
        except Exception as e:
            log.warning("memory_append_failed", scope=scope_key, exc=str(e))
        return onboarding_reply

    system_prompt = _build_system_prompt(viewer, prefs, recalled, text)
    messages: list[dict[str, Any]] = [
        *_history_messages(history),
        {"role": "user", "content": text},
    ]

    import asyncio
    import random

    # D8: up to 2 retries with jitter before surfacing the canned error.
    max_attempts = 3
    reply: str | None = None
    last_timeout = False
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            response = await asyncio.wait_for(
                _llm.messages.create(
                    model=settings.specialist_model,
                    system=system_prompt,
                    messages=messages,
                    max_tokens=MAX_REPLY_TOKENS,
                    temperature=CHAT_TEMPERATURE,
                ),
                timeout=LLM_TIMEOUT_SECONDS,
            )
            reply = _extract_text(response) or "No response generated"
            if reply == "No response generated":
                log.warning("llm_empty_response", scope=scope_key)
                return reply
            log.info(
                "llm_response_success",
                scope=scope_key,
                response_length=len(reply),
                attempt=attempt + 1,
            )
            break
        except TimeoutError as e:
            last_timeout = True
            last_exc = e
            log.warning("llm_timeout_retry", scope=scope_key, attempt=attempt + 1)
        except Exception as e:
            last_timeout = False
            last_exc = e
            log.warning(
                "llm_request_retry",
                scope=scope_key,
                attempt=attempt + 1,
                exception=str(e),
                exception_type=type(e).__name__,
            )
        if attempt < max_attempts - 1:
            await asyncio.sleep(0.25 + random.random() * 0.75)

    if reply is None:
        if last_timeout:
            log.error("llm_timeout", scope=scope_key)
            return "LLM timeout, try again"
        log.error(
            "llm_request_failed",
            scope=scope_key,
            exception=str(last_exc),
            exception_type=type(last_exc).__name__ if last_exc else None,
        )
        return "Error processing your request"

    # V6 / D1: never persist or surface a leaked speaker prefix.
    reply = _strip_leaked_speaker_prefix(reply)

    try:
        memory.append_turn(redis_client, scope_key, viewer, "user", text)
        memory.append_turn(redis_client, scope_key, viewer, "assistant", reply)
    except Exception as e:
        log.warning("memory_append_failed", scope=scope_key, exc=str(e))

    if prefs.get("do_not_log") != "1":
        try:
            facts = extract_facts(text, reply, viewer)
            await _persist_facts(redis_client, viewer, facts, scope_key)
        except Exception as e:
            log.warning("fact_extraction_failed", scope=scope_key, exc=str(e))

    return reply


async def _persist_facts(
    redis_client,
    viewer: Viewer,
    facts: ExtractedFacts,
    scope_key: str,
) -> None:
    if facts.empty:
        return
    for key, value in facts.prefs.items():
        try:
            memory.set_pref(redis_client, viewer.discord_id, key, value)
        except ValueError:
            continue
    for note in facts.notes:
        # Drafts are queued for reaction-confirmed save (PR 2 flow).
        # bot.on_message pops, posts a confirmation message, and binds
        # the draft to that message's id.
        draft_store.push_pending(redis_client, scope_key, note)
    for fact in facts.free_facts:
        ok = await memory.remember_fact(viewer, fact, source_channel=scope_key)
        if ok:
            metrics.FACTS_INGESTED.inc()


class TomBombadil(BaseAgent):
    """Discord film club specialist.

    Sauron-dispatched messages don't carry a Discord author by default,
    so we synthesise a stranger viewer unless the payload includes
    ``viewer_discord_id`` and ``viewer_discord_name`` (deferred wiring
    in Sauron, see plan).
    """

    tier: ClassVar[ConfigTier] = "specialist"
    name: ClassVar[str] = "tombombadil"

    async def run(self, task: AgentTask) -> AgentResult:
        message = task.payload.get("message")
        if not message:
            return AgentResult(
                task_id=task.task_id,
                agent=self.name,
                status=TaskStatus.FAILED,
                error="payload.message is required",
            )

        try:
            viewer = resolve_viewer(
                str(task.payload.get("viewer_discord_id", "sauron-dispatch")),
                str(task.payload.get("viewer_discord_name", "sauron-dispatch")),
            )
            scope_key = f"tom:hist:ch:{task.payload.get('channel_id', 'sauron-dispatch')}"
            reply = await get_response(scope_key, message, viewer)

            return AgentResult(
                task_id=task.task_id,
                agent=self.name,
                status=TaskStatus.COMPLETED,
                result={"reply": reply},
            )
        except Exception as e:
            log.error("tombombadil_run_failed", agent_task_id=task.task_id, exception=str(e))
            return AgentResult(
                task_id=task.task_id,
                agent=self.name,
                status=TaskStatus.FAILED,
                error=str(e),
            )
