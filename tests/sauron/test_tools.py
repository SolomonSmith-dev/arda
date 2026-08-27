from __future__ import annotations

from typing import ClassVar

import pytest

from agents.base import BaseAgent
from agents.sauron.tools import (
    EARENDIL_TOOL,
    FINROD_TOOL,
    SAURON_TOOLS,
    TOMBOMBADIL_TOOL,
    TOOL_NAME_TO_SPECIALIST,
    UnknownToolError,
    dispatch_tool,
)
from core.models import AgentResult, AgentTask, TaskStatus


class _Recorder(BaseAgent):
    tier: ClassVar[str] = "executor"
    name: ClassVar[str] = "recorder"

    def __init__(self):
        self.received: list[AgentTask] = []

    async def run(self, task: AgentTask) -> AgentResult:
        self.received.append(task)
        return AgentResult(
            task_id=task.task_id,
            agent=self.name,
            status=TaskStatus.COMPLETED,
            result={"echo": task.payload},
        )


def test_tool_schemas_use_anthropic_native_shape():
    for tool in SAURON_TOOLS:
        assert set(tool.keys()) == {"name", "description", "input_schema"}
        assert tool["input_schema"]["type"] == "object"
        assert "properties" in tool["input_schema"]
        assert "required" in tool["input_schema"]


def test_tool_names_map_to_specialists():
    assert TOOL_NAME_TO_SPECIALIST[EARENDIL_TOOL["name"]] == "earendil"
    assert TOOL_NAME_TO_SPECIALIST[FINROD_TOOL["name"]] == "finrod"
    assert TOOL_NAME_TO_SPECIALIST[TOMBOMBADIL_TOOL["name"]] == "tombombadil"


@pytest.mark.asyncio
async def test_dispatch_tool_routes_earendil_execute():
    rec = _Recorder()
    result = await dispatch_tool(
        name="earendil_execute",
        tool_input={"message": "uptime"},
        specialists={"earendil": rec},
        parent_task_id="parent-123",
    )
    assert result.status == TaskStatus.COMPLETED
    assert len(rec.received) == 1
    assert rec.received[0].agent == "earendil"
    assert rec.received[0].type == "sauron_tool_dispatch"
    assert rec.received[0].payload["message"] == "uptime"
    assert rec.received[0].payload["parent_task_id"] == "parent-123"


@pytest.mark.asyncio
async def test_dispatch_tool_routes_finrod_query():
    rec = _Recorder()
    await dispatch_tool(
        name="finrod_query",
        tool_input={"question": "what is ARDA"},
        specialists={"finrod": rec},
        parent_task_id="p",
    )
    assert rec.received[0].agent == "finrod"
    assert rec.received[0].payload["question"] == "what is ARDA"


@pytest.mark.asyncio
async def test_dispatch_tool_routes_tombombadil_chat():
    rec = _Recorder()
    await dispatch_tool(
        name="tombombadil_chat",
        tool_input={"message": "Film: Ran\nRating: 9"},
        specialists={"tombombadil": rec},
        parent_task_id="p",
    )
    assert rec.received[0].agent == "tombombadil"
    assert "Ran" in rec.received[0].payload["message"]


@pytest.mark.asyncio
async def test_dispatch_tool_unknown_name_raises():
    with pytest.raises(UnknownToolError, match="unknown tool"):
        await dispatch_tool(
            name="palantir_lookup",
            tool_input={},
            specialists={},
            parent_task_id="p",
        )


@pytest.mark.asyncio
async def test_dispatch_tool_unregistered_specialist_raises():
    with pytest.raises(UnknownToolError, match="not registered"):
        await dispatch_tool(
            name="earendil_execute",
            tool_input={"message": "uptime"},
            specialists={},
            parent_task_id="p",
        )


def test_sauron_tools_derived_from_specialist_map():
    from agents.sauron.tools import SAURON_TOOLS, SPECIALIST_TOOL_MAP, TOOL_NAME_TO_SPECIALIST

    expected_schemas = list(SPECIALIST_TOOL_MAP.values())
    assert expected_schemas == SAURON_TOOLS, (
        "SAURON_TOOLS must be derived from SPECIALIST_TOOL_MAP, not defined independently"
    )

    for specialist_name, schema in SPECIALIST_TOOL_MAP.items():
        assert TOOL_NAME_TO_SPECIALIST[schema["name"]] == specialist_name


def test_build_sauron_graph_uses_only_registered_specialists():
    from unittest.mock import MagicMock

    from langgraph.checkpoint.memory import MemorySaver

    from agents.earendil.agent import Earendil
    from agents.sauron.graph import build_sauron_graph

    specialists = {"earendil": Earendil()}
    graph = build_sauron_graph(
        specialists=specialists,
        client=MagicMock(),
        checkpointer=MemorySaver(),
        model="mock",
    )
    assert graph is not None
