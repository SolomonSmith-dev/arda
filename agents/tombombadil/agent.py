from __future__ import annotations

import asyncio
from typing import ClassVar

from agents.base import BaseAgent
from agents.tombombadil.film_parser import parse_film_note
from agents.tombombadil.persistent_memory import save_note
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


async def get_response(channel_id: str, text: str) -> str:
    if not text or not text.strip():
        return "Please provide a message"

    log.info("llm_request_start", channel=channel_id, text_length=len(text))

    try:
        loop = asyncio.get_running_loop()
        response = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: _llm.invoke(text)),
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
