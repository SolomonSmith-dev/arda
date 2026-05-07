from __future__ import annotations

from typing import Any, ClassVar

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

from agents.base import BaseAgent
from agents.sauron.graph import build_sauron_graph
from agents.sauron.llm import build_client
from agents.sauron.planner import Specialist
from agents.sauron.state import ArdaState
from core.config import Tier, settings
from core.logging import get_logger
from core.models import AgentResult, AgentTask, TaskStatus

log = get_logger("agents.sauron.agent")


class Sauron(BaseAgent):
    """Orchestrator. Routes user messages to specialists via a LangGraph
    StateGraph + Anthropic tool_use loop.

    The graph has two nodes:
      - `agent_step`: calls Claude with the registered tool schemas and
        captures any tool_use blocks (or terminal text).
      - `tool_dispatch`: invokes the matching specialist's BaseAgent.run
        and feeds the AgentResult back as a tool_result content block.

    Cross-turn memory is provided by a LangGraph checkpointer keyed by
    `thread_id`. Tests pass MemorySaver; production may pass a
    SqliteSaver/AsyncSqliteSaver.

    The public `Sauron.run(AgentTask) -> AgentResult` contract — including
    the result envelope shape `{intent, specialist, specialist_result}` —
    is unchanged so existing callers (MCP server, e2e tests) keep working.
    """

    tier: ClassVar[Tier] = "orchestrator"
    name: ClassVar[str] = "sauron"

    def __init__(
        self,
        specialists: dict[Specialist, BaseAgent] | None = None,
        *,
        client: Any | None = None,
        checkpointer: BaseCheckpointSaver | None = None,
        model: str | None = None,
    ):
        self.specialists: dict[Specialist, BaseAgent] = specialists or {}
        self._client = client or build_client()
        self._checkpointer: BaseCheckpointSaver = checkpointer or MemorySaver()
        self._model = model or settings.orchestrator_model
        self._graph = build_sauron_graph(
            specialists=self.specialists,
            client=self._client,
            checkpointer=self._checkpointer,
            model=self._model,
        )

    def register(self, specialist: Specialist, agent: BaseAgent) -> None:
        """Register a specialist post-init. Rebuilds the compiled graph
        so the new specialist is reachable via tool dispatch."""
        self.specialists[specialist] = agent
        self._graph = build_sauron_graph(
            specialists=self.specialists,
            client=self._client,
            checkpointer=self._checkpointer,
            model=self._model,
        )

    async def run(self, task: AgentTask) -> AgentResult:
        message = task.payload.get("message")
        if not message:
            return AgentResult(
                task_id=task.task_id,
                agent=self.name,
                status=TaskStatus.FAILED,
                error="payload.message is required",
            )

        thread_id = task.payload.get("thread_id") or task.task_id

        initial_state: ArdaState = {
            "thread_id": thread_id,
            "task_id": task.task_id,
            "user_message": message,
            "messages": [{"role": "user", "content": message}],
            "pending_tool_uses": [],
            "intent": None,
            "last_specialist_result": None,
            "final_text": None,
            "error": None,
            "iterations": 0,
        }

        try:
            final_state = await self._graph.ainvoke(
                initial_state,
                config={"configurable": {"thread_id": thread_id}},
            )
        except Exception as e:  # noqa: BLE001
            log.error("sauron_graph_failed", task_id=task.task_id, exception=str(e))
            return AgentResult(
                task_id=task.task_id,
                agent=self.name,
                status=TaskStatus.FAILED,
                error=str(e),
            )

        intent = final_state.get("intent")
        last_result = final_state.get("last_specialist_result")
        final_text = final_state.get("final_text")
        error = final_state.get("error")

        if last_result is not None:
            status_str = last_result.get("status")
            try:
                status = TaskStatus(status_str)
            except ValueError:
                status = TaskStatus.COMPLETED
        elif error:
            status = TaskStatus.FAILED
        elif final_text:
            status = TaskStatus.COMPLETED
        else:
            status = TaskStatus.FAILED
            error = error or "sauron produced no result"

        log.info(
            "sauron_run_complete",
            task_id=task.task_id,
            thread_id=thread_id,
            intent=intent,
            iterations=final_state.get("iterations"),
            status=status,
        )

        return AgentResult(
            task_id=task.task_id,
            agent=self.name,
            status=status,
            result={
                "intent": intent,
                "specialist": intent,
                "specialist_result": last_result,
                "final_text": final_text,
            },
            error=error or (last_result.get("error") if last_result else None),
        )
