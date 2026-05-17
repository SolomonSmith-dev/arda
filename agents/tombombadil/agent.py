from __future__ import annotations

import asyncio
from typing import ClassVar

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agents.base import BaseAgent
from agents.conduct import CONDUCT_PROMPT
from agents.tombombadil import draft_store, memory, metrics
from agents.tombombadil.fact_extractor import ExtractedFacts
from agents.tombombadil.fact_extractor import extract as extract_facts
from agents.tombombadil.film_knowledge import FilmKnowledge
from agents.tombombadil.identity import Tier, Viewer, all_known
from agents.tombombadil.identity import resolve as resolve_viewer
from core.config import settings
from core.logging import get_logger
from core.models import AgentResult, AgentTask, TaskStatus
from core.redis_client import get_redis_sync

log = get_logger("agents.tombombadil.agent")


def _build_llm():
    if settings.use_mock_llm:
        from agents._mock_llm import MockLLM
        return MockLLM(model=settings.specialist_model)
    from langchain_groq import ChatGroq
    return ChatGroq(
        model=settings.specialist_model,
        api_key=settings.groq_api_key,
        temperature=0.7,
    )


_llm = _build_llm()
_film_knowledge = FilmKnowledge()


_SELF_DESCRIPTION = (
    "You are Tom Bombadil, a specialist agent in the ARDA stack.\n"
    "- Sauron (Gemini-backed) is the orchestrator that routes messages by intent.\n"
    "- You handle film and club topics.\n"
    "- Finrod provides long-term retrieval via sentence-transformers embeddings\n"
    "  over a vector store (in-memory today; Milvus standalone is planned).\n"
    "- Galadriel runs cron jobs and watch-party reminders.\n"
    "- Gwaihir is the Telegram bot for ops messages.\n"
    "- You run in Docker on a Linux home server, share Redis with the rest of\n"
    "  the stack, reply via discord.py, and use the Groq-hosted "
    f"{settings.specialist_model} model.\n"
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
    return (
        "You have access to the user's film history. Use it when asked about "
        "ratings, favorites, or recommendations. Never invent ratings — if a "
        f"film isn't listed, say so.\n\n{summary}"
    )


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


def _build_system_messages(
    viewer: Viewer,
    prefs: dict[str, str],
    recalled_facts: list[str],
) -> list[SystemMessage]:
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
    prefs_block = _prefs_block(prefs)
    if prefs_block:
        blocks.append(prefs_block)
    recalled = _recalled_block(recalled_facts)
    if recalled:
        blocks.append(recalled)
    return [SystemMessage(content=b) for b in blocks]


def _history_messages(turns: list[memory.Turn]) -> list:
    """Render persisted turns as LangChain messages.

    Only USER turns get the ``[viewer] ...`` speaker prefix -- the LLM
    needs that to disambiguate who said what in multi-user channels.
    ASSISTANT turns must NOT be prefixed: when the model sees its own
    prior replies wrapped in ``[Solomon Smith] ...`` it imitates that
    shape on the next response (live regression: bot replied
    ``[@Solomon Smith] Hello Patrick!``).
    """
    msgs = []
    for t in turns:
        if t.role == "user":
            content = f"[{t.viewer}] {t.content}" if t.viewer else t.content
            msgs.append(HumanMessage(content=content))
        else:
            msgs.append(AIMessage(content=t.content))
    return msgs


async def get_response(
    scope_key: str,
    text: str,
    viewer: Viewer,
    redis_client=None,
) -> str:
    """Generate a reply for ``viewer``'s message.

    Side effects (best-effort, swallowed on failure):
    - Appends both turns to the per-scope short-term history.
    - Runs the rule-based fact extractor and persists prefs / notes /
      free-facts. The fact extractor runs *after* the reply is sent so
      response latency is unchanged.
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

    messages = [
        *_build_system_messages(viewer, prefs, recalled),
        *_history_messages(history),
        HumanMessage(content=text),
    ]

    try:
        loop = asyncio.get_running_loop()
        response = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: _llm.invoke(messages)),
            timeout=10,
        )
        reply = (response.content or "").strip() or "No response generated"

        if reply == "No response generated":
            log.warning("llm_empty_response", scope=scope_key)
            return reply

        log.info("llm_response_success", scope=scope_key, response_length=len(reply))
    except TimeoutError:
        log.error("llm_timeout", scope=scope_key)
        return "LLM timeout, try again"
    except Exception as e:
        log.error(
            "llm_request_failed",
            scope=scope_key,
            exception=str(e),
            exception_type=type(e).__name__,
        )
        return "Error processing your request"

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

    tier: ClassVar[str] = "specialist"
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
