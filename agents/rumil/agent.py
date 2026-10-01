"""Rúmil -- GitHub activity chronicler.

Named for the Noldo who first devised letters: he keeps the record. One
action, ``audit``: fetch a window of GitHub activity, have the specialist
model write a short "so what", store the result in Finrod so it can be
recalled later, and return a Telegram-ready ``reply``.

Collaborators are constructor-injected (Finrod, fetcher, LLM client) so
tests run offline. ``api/main.py`` hands in the app's own Finrod, so a
snapshot stored here is the one ``finrod_query`` searches.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, ClassVar

from agents.base import BaseAgent
from agents.rumil.github import (
    DEFAULT_WINDOW_HOURS,
    GitHubAuditError,
    GitHubSnapshot,
    fetch_activity,
)
from agents.rumil.llm import build_chat_client
from agents.rumil.report import build_message, format_stats
from core.config import Tier, settings
from core.logging import get_logger
from core.models import AgentResult, AgentTask, TaskStatus

log = get_logger("agents.rumil.agent")

MAX_WINDOW_HOURS = 24 * 30
NARRATIVE_MAX_TOKENS = 220

NARRATIVE_SYSTEM_PROMPT = (
    "You summarise one developer's recent GitHub activity for a private daily "
    "Telegram message. Write two or three plain sentences: what the work was "
    "about and what it adds up to. Use only the facts given. No markdown, no "
    "bullet points, no greetings, no praise, no motivational language."
)


def _window_hours(raw: Any) -> int:
    try:
        hours = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_WINDOW_HOURS
    return max(1, min(hours, MAX_WINDOW_HOURS))


class Rumil(BaseAgent):
    tier: ClassVar[Tier] = "specialist"
    name: ClassVar[str] = "rumil"

    def __init__(
        self,
        *,
        finrod: BaseAgent | None = None,
        fetcher: Callable[..., GitHubSnapshot] = fetch_activity,
        llm: Any | None = None,
        username: str | None = None,
        token: str | None = None,
    ):
        self._finrod = finrod
        self._fetcher = fetcher
        self._llm = llm if llm is not None else build_chat_client()
        self._username = username if username is not None else settings.github_username
        self._token = token if token is not None else settings.github_token

    async def run(self, task: AgentTask) -> AgentResult:
        action = task.payload.get("action", "audit")
        if action != "audit":
            return self._failed(task, f"unknown action: {action}")

        hours = _window_hours(task.payload.get("window_hours", DEFAULT_WINDOW_HOURS))
        try:
            # The fetcher is synchronous httpx; keep it off the event loop.
            snap = await asyncio.to_thread(
                self._fetcher, self._username, self._token, window_hours=hours
            )
        except GitHubAuditError as e:
            log.warning("rumil_fetch_failed", error=str(e))
            return self._failed(task, str(e))

        narrative = await self._narrate(snap) if snap.has_activity else ""
        reply = build_message(snap, narrative)
        doc_id = await self._remember(snap, reply)

        log.info(
            "rumil_audit_complete",
            commits=snap.commit_count,
            prs_opened=len(snap.prs_opened),
            prs_merged=len(snap.prs_merged),
            narrated=bool(narrative),
            stored=doc_id is not None,
        )
        return AgentResult(
            task_id=task.task_id,
            agent=self.name,
            status=TaskStatus.COMPLETED,
            result={
                "snapshot": snap.to_dict(),
                "narrative": narrative,
                "reply": reply,
                "doc_id": doc_id,
            },
        )

    async def _narrate(self, snap: GitHubSnapshot) -> str:
        """Best effort: a failed model call degrades to stats only."""
        try:
            msg = await self._llm.messages.create(
                model=settings.specialist_model,
                system=NARRATIVE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": format_stats(snap)}],
                max_tokens=NARRATIVE_MAX_TOKENS,
            )
            parts = [getattr(b, "text", "") for b in msg.content]
            return " ".join(p.strip() for p in parts if p).strip()
        except Exception as e:  # noqa: BLE001 - any provider failure degrades
            log.warning("rumil_narrative_failed", error=str(e))
            return ""

    async def _remember(self, snap: GitHubSnapshot, text: str) -> str | None:
        """Store in Finrod under a per-day doc_id; a re-run replaces it."""
        if self._finrod is None:
            return None
        day = snap.window_end.date().isoformat()
        doc_id = f"github-audit:{snap.username}:{day}"
        forget = getattr(self._finrod, "forget", None)
        if forget is not None:
            await forget({"doc_id": doc_id})
        result = await self._finrod.run(
            AgentTask(
                agent="finrod",
                type="ingest",
                payload={
                    "action": "ingest",
                    "doc_id": doc_id,
                    "text": f"GitHub audit for {day}.\n{text}",
                    "metadata": {"kind": "github_audit", "date": day, "username": snap.username},
                },
            )
        )
        if result.status != TaskStatus.COMPLETED:
            log.warning("rumil_store_failed", error=result.error)
            return None
        return doc_id

    def _failed(self, task: AgentTask, error: str) -> AgentResult:
        return AgentResult(
            task_id=task.task_id, agent=self.name, status=TaskStatus.FAILED, error=error
        )
