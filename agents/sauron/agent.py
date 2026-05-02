from __future__ import annotations

from typing import ClassVar

from agents.base import BaseAgent
from agents.sauron.planner import Plan, Specialist, plan
from core.logging import get_logger
from core.models import AgentResult, AgentTask, TaskStatus

log = get_logger("agents.sauron.agent")


def _build_llm():
    from core.config import settings

    if settings.use_mock_llm:
        from agents._mock_llm import MockLLM
        return MockLLM(model=settings.orchestrator_model)
    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(
        model=settings.orchestrator_model,
        google_api_key=settings.gemini_api_key,
        temperature=0.3,
    )


class Sauron(BaseAgent):
    """Orchestrator. Classifies the NL message, dispatches to one
    specialist, returns the specialist's AgentResult wrapped in a
    Sauron envelope.

    Specialists are injected via the constructor so tests and Phase 3
    HTTP wiring can swap in mocks or remote-call adapters. The planner
    is keyword-based today (see agents.sauron.planner.classify) and
    upgraded to an LLM intent classifier in a later pass.
    """

    tier: ClassVar[str] = "orchestrator"
    name: ClassVar[str] = "sauron"

    def __init__(self, specialists: dict[Specialist, BaseAgent] | None = None):
        self.specialists: dict[Specialist, BaseAgent] = specialists or {}
        self._llm = _build_llm()

    def register(self, specialist: Specialist, agent: BaseAgent) -> None:
        self.specialists[specialist] = agent

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
            plan_result: Plan = plan(message)
            log.info(
                "sauron_plan",
                agent_task_id=task.task_id,
                intent=plan_result.intent,
                subtask_count=len(plan_result.subtasks),
            )

            subtask = plan_result.subtasks[0]
            specialist_agent = self.specialists.get(subtask.specialist)

            if specialist_agent is None:
                return AgentResult(
                    task_id=task.task_id,
                    agent=self.name,
                    status=TaskStatus.FAILED,
                    error=f"specialist '{subtask.specialist}' not registered",
                )

            sub_task = AgentTask(
                agent=subtask.specialist,
                type="sauron_dispatch",
                payload=subtask.payload,
            )
            sub_result = await specialist_agent.run(sub_task)

            return AgentResult(
                task_id=task.task_id,
                agent=self.name,
                status=sub_result.status,
                result={
                    "intent": plan_result.intent,
                    "specialist": subtask.specialist,
                    "specialist_result": sub_result.model_dump(mode="json"),
                },
                error=sub_result.error,
            )

        except Exception as e:
            log.error("sauron_run_failed", agent_task_id=task.task_id, exception=str(e))
            return AgentResult(
                task_id=task.task_id,
                agent=self.name,
                status=TaskStatus.FAILED,
                error=str(e),
            )
