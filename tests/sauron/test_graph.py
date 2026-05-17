"""StateGraph-level tests for the Sauron orchestrator.

Wires real graph nodes with the MockAnthropicClient and recording
specialists. Asserts the agent_step / tool_dispatch loop routes
correctly, terminates cleanly, and respects the iteration cap.
"""

from __future__ import annotations

from typing import ClassVar

import pytest
from langgraph.checkpoint.memory import MemorySaver

from agents._anthropic_mock import MockAnthropicClient, MockMessage, ToolUseBlock
from agents.base import BaseAgent
from agents.sauron.graph import build_sauron_graph
from agents.sauron.state import ArdaState
from core.models import AgentResult, AgentTask, TaskStatus


class _Recorder(BaseAgent):
    name: ClassVar[str] = "recorder"
    tier: ClassVar[str] = "executor"

    def __init__(self, label: str):
        self.label = label
        self.received: list[AgentTask] = []

    async def run(self, task: AgentTask) -> AgentResult:
        self.received.append(task)
        return AgentResult(
            task_id=task.task_id,
            agent=self.label,
            status=TaskStatus.COMPLETED,
            result={"echo": task.payload, "by": self.label},
        )


def _make_initial_state(message: str, *, thread_id: str = "t1") -> ArdaState:
    return {
        "thread_id": thread_id,
        "task_id": "task-x",
        "user_message": message,
        "messages": [{"role": "user", "content": message}],
        "pending_tool_uses": [],
        "intent": None,
        "last_specialist_result": None,
        "final_text": None,
        "error": None,
        "iterations": 0,
    }


def _build(specialists, client=None, max_iterations=6):
    return build_sauron_graph(
        specialists=specialists,
        client=client or MockAnthropicClient(),
        checkpointer=MemorySaver(),
        model="mock",
        max_iterations=max_iterations,
    )


@pytest.mark.asyncio
async def test_shell_message_routes_through_graph_to_earendil():
    earendil = _Recorder("earendil")
    graph = _build({"earendil": earendil})

    final = await graph.ainvoke(
        _make_initial_state("uptime"),
        config={"configurable": {"thread_id": "t1"}},
    )

    assert final["intent"] == "earendil"
    assert final["last_specialist_result"]["agent"] == "earendil"
    assert final["final_text"] is not None
    assert len(earendil.received) == 1
    assert earendil.received[0].payload["message"] == "uptime"


@pytest.mark.asyncio
async def test_knowledge_message_routes_through_graph_to_finrod():
    finrod = _Recorder("finrod")
    graph = _build({"finrod": finrod})

    final = await graph.ainvoke(
        _make_initial_state("explain ARDA"),
        config={"configurable": {"thread_id": "t2"}},
    )

    assert final["intent"] == "finrod"
    assert finrod.received[0].payload["question"] == "explain ARDA"


@pytest.mark.asyncio
async def test_film_message_routes_through_graph_to_tombombadil():
    tom = _Recorder("tombombadil")
    graph = _build({"tombombadil": tom})

    final = await graph.ainvoke(
        _make_initial_state("Film: Ran\nRating: 9"),
        config={"configurable": {"thread_id": "t3"}},
    )

    assert final["intent"] == "tombombadil"
    assert "Ran" in tom.received[0].payload["message"]


@pytest.mark.asyncio
async def test_graph_terminates_with_final_text_after_tool_result():
    earendil = _Recorder("earendil")
    graph = _build({"earendil": earendil})

    final = await graph.ainvoke(
        _make_initial_state("uptime"),
        config={"configurable": {"thread_id": "t4"}},
    )

    assert final["final_text"] is not None
    assert "[mock]" in final["final_text"]
    assert final["pending_tool_uses"] == []


@pytest.mark.asyncio
async def test_iterations_cap_prevents_infinite_loop():
    """A pathological mock that always emits tool_use must be halted by the cap."""

    class _LoopyClient:
        def __init__(self):
            self.calls = 0
            self.messages = self

        async def create(self, **_kwargs):
            self.calls += 1
            return MockMessage(
                content=[ToolUseBlock(id=f"tu_{self.calls}", name="earendil_execute", input={"message": "x"})],
                stop_reason="tool_use",
            )

    earendil = _Recorder("earendil")
    client = _LoopyClient()
    graph = _build({"earendil": earendil}, client=client, max_iterations=3)

    final = await graph.ainvoke(
        _make_initial_state("uptime"),
        config={"configurable": {"thread_id": "t5"}},
    )

    assert final["iterations"] == 3
    assert client.calls == 3
    # Tool dispatch ran once per agent_step that produced tool_use, but the
    # last agent_step still produces tool_use that we don't process (cap hit).
    assert len(earendil.received) == 2


@pytest.mark.asyncio
async def test_checkpointer_persists_messages_across_invocations():
    """Two ainvoke calls with the same thread_id share message history."""
    earendil = _Recorder("earendil")
    checkpointer = MemorySaver()
    graph = build_sauron_graph(
        specialists={"earendil": earendil},
        client=MockAnthropicClient(),
        checkpointer=checkpointer,
        model="mock",
    )

    cfg = {"configurable": {"thread_id": "shared"}}
    await graph.ainvoke(_make_initial_state("uptime", thread_id="shared"), config=cfg)

    snapshot = await graph.aget_state(cfg)
    msgs_after_turn1 = snapshot.values["messages"]
    # user, assistant(tool_use), user(tool_result), assistant(text)
    assert len(msgs_after_turn1) >= 4

    await graph.ainvoke(_make_initial_state("ls", thread_id="shared"), config=cfg)
    snapshot2 = await graph.aget_state(cfg)
    msgs_after_turn2 = snapshot2.values["messages"]
    assert len(msgs_after_turn2) > len(msgs_after_turn1)
