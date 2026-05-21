from __future__ import annotations

import asyncio
import re
from typing import ClassVar

from langchain_core.messages import HumanMessage, SystemMessage

from agents.base import BaseAgent
from agents.conduct import CONDUCT_PROMPT
from agents.tombombadil.film_knowledge import FilmKnowledge
from agents.tombombadil.film_parser import parse_film_note
from agents.tombombadil.persistent_memory import save_note
from core.config import settings
from core.logging import get_logger
from core.models import AgentResult, AgentTask, TaskStatus
from core.redis_client import get_redis_sync

log = get_logger("agents.tombombadil.agent")

# Bot-owner identity. Used to look up your Letterboxd profile so the
# LLM has your favorites + recent ratings as system-prompt context.
DEFAULT_VIEWER = "Solomon Smith"


def _build_llm():
    if settings.use_mock_llm:
        from agents._mock_llm import MockLLM
        return MockLLM(model=settings.specialist_model)
    from langchain_groq import ChatGroq
    # Low temperature: film-rating lookups are factual retrieval, not
    # creative writing. Llama 4 Scout at 0.7 confabulates "10/10" for
    # films it has the actual rating for.
    return ChatGroq(
        model=settings.specialist_model,
        api_key=settings.groq_api_key,
        temperature=0.2,
    )


_llm = _build_llm()
_film_knowledge = FilmKnowledge(letterboxd_viewer_name=DEFAULT_VIEWER)

_FILM_SUMMARY = _film_knowledge.get_user_summary(DEFAULT_VIEWER)
log.info(
    "film_summary_ready",
    viewer=DEFAULT_VIEWER,
    has_summary=_FILM_SUMMARY is not None,
    summary_chars=len(_FILM_SUMMARY) if _FILM_SUMMARY else 0,
    films_in_db=len(_film_knowledge.films),
    people_in_db=len(_film_knowledge.people),
)


_LETTERBOXD_PREAMBLE = (
    "FILM RATINGS LOOKUP — STRICT RULES:\n"
    "1. The list below is the ONLY source of truth for the user's ratings. "
    "Every rated film the user has watched is in it.\n"
    "2. When asked about a specific film, scan the alphabetical list and "
    "quote the rating EXACTLY as written (e.g. '9/10', not '10/10').\n"
    "3. If a film is NOT in the list, say 'I don't see it in your ratings' "
    "— do NOT guess, do NOT invent a score.\n"
    "4. When asked for 'top N' or 'highest rated', use the Highest-rated "
    "line below verbatim. Do NOT pad the list with films that aren't there.\n"
    "5. Watch dates are NOT in this data. If asked, say 'I don't have watch "
    "dates, only ratings.'\n"
    "6. Never claim to be 'checking Letterboxd live' — you have a snapshot "
    "embedded here, nothing more.\n\n"
)


def _direct_film_facts(text: str, viewer: str = DEFAULT_VIEWER) -> str | None:
    """Word-boundary scan for known film titles in ``text``; for each hit,
    look up the viewer's actual rating directly from the data structure.

    The LLM is unreliable at grepping a 7K-token alphabetical list — it
    confabulates "10/10" even with the data in front of it. Giving it a
    short, scoped block of verified facts for THIS query side-steps that.
    """
    text_lower = text.lower()
    facts: list[str] = []
    seen: set[str] = set()
    for film in _film_knowledge.films:
        title = (film.get("title") or "").strip()
        if not title or title.lower() in seen:
            continue
        if not re.search(rf"\b{re.escape(title.lower())}\b", text_lower):
            continue
        seen.add(title.lower())
        watcher = next(
            (w for w in film.get("watchers", []) if w.get("name") == viewer),
            None,
        )
        if watcher is None:
            facts.append(f"- {title}: {viewer} has NOT rated this")
        elif watcher.get("rating") is None:
            facts.append(f"- {title}: {viewer} watched but did not rate")
        else:
            facts.append(
                f"- {title}: {viewer} rated this {float(watcher['rating']):g}/10"
            )
    if not facts:
        return None
    return (
        "VERIFIED RATINGS FOR THIS QUERY — use these EXACTLY, do NOT round, "
        "do NOT contradict, do NOT mention the long list when these facts "
        "answer the question:\n" + "\n".join(facts) + "\n"
    )


def _system_messages(text: str = "") -> list[SystemMessage]:
    msgs = [SystemMessage(content=CONDUCT_PROMPT)]
    if _FILM_SUMMARY:
        msgs.append(
            SystemMessage(content=_LETTERBOXD_PREAMBLE + _FILM_SUMMARY)
        )
    if text:
        facts = _direct_film_facts(text)
        if facts:
            msgs.append(SystemMessage(content=facts))
    return msgs


async def get_response(channel_id: str, text: str) -> str:
    if not text or not text.strip():
        return "Please provide a message"

    log.info("llm_request_start", channel=channel_id, text_length=len(text))

    facts = _direct_film_facts(text)
    log.info(
        "film_facts_lookup",
        channel=channel_id,
        text_preview=text[:80],
        facts_found=facts is not None,
        facts_preview=(facts[:200] if facts else None),
    )

    messages = [*_system_messages(text), HumanMessage(content=text)]
    try:
        loop = asyncio.get_running_loop()
        response = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: _llm.invoke(messages)),
            timeout=10,
        )
        reply = (response.content or "").strip()

        if not reply:
            log.warning("llm_empty_response", channel=channel_id)
            return "No response generated"

        log.info("llm_response_success", channel=channel_id, response_length=len(reply))
        return reply

    except TimeoutError:
        log.error("llm_timeout", channel=channel_id)
        return "LLM timeout, try again"
    except Exception as e:
        log.error(
            "llm_request_failed",
            channel=channel_id,
            exception=str(e),
            exception_type=type(e).__name__,
        )
        return "Error processing your request"


def acknowledge_notes(text: str) -> str:
    log.debug("parse_film_notes_start", input_length=len(text))

    result = parse_film_note(text)

    if result["errors"]:
        log.warning("parse_errors", errors=result["errors"])
        return "\n".join(result["errors"])

    data = result["data"]
    watcher = data["name"] or "Unknown"

    log.info(
        "parse_successful",
        film=data["film"],
        watcher=watcher,
        rating=data["rating"],
    )

    success, msg = save_note(
        get_redis_sync(),
        film=data["film"],
        watcher=watcher,
        rating=data["rating"],
        reaction=data.get("reaction") or "",
        themes=data.get("themes") or "",
    )

    if not success:
        log.error("save_failed", reason=msg, film=data["film"])
        return msg

    response = f"OK {data['film']} ({data['rating']}/10) logged"
    log.info("note_saved", film=data["film"], watcher=watcher)
    return response


class TomBombadil(BaseAgent):
    """Discord film club specialist.

    Wraps the existing acknowledge_notes / get_response top-level
    functions in a BaseAgent surface so Sauron can dispatch to it.
    Payload routing:
      - {"message": "Film: ...\\nRating: ..."} -> acknowledge_notes
      - {"message": "anything else"}            -> get_response
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
            lower = message.lower()
            if "film:" in lower and "rating" in lower:
                reply = acknowledge_notes(message)
            else:
                channel_id = str(task.payload.get("channel_id", "sauron-dispatch"))
                reply = await get_response(channel_id, message)

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
