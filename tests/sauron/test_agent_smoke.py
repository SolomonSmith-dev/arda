from __future__ import annotations

from typing import ClassVar

import pytest

from agents.base import BaseAgent
from agents.sauron.agent import Sauron
from core.models import AgentResult, AgentTask, TaskStatus


class _RecordingSpecialist(BaseAgent):
    tier: ClassVar[str] = "executor"
    name: ClassVar[str] = "recorder"

    def __init__(self, status: TaskStatus = TaskStatus.COMPLETED, error: str | None = None):
        self.received: list[AgentTask] = []
        self.status = status
        self.error = error

    async def run(self, task: AgentTask) -> AgentResult:
        self.received.append(task)
        return AgentResult(
            task_id=task.task_id,
            agent=self.name,
            status=self.status,
            result={"echo": task.payload},
            error=self.error,
        )


@pytest.mark.asyncio
async def test_sauron_dispatches_shell_message_to_earendil_specialist():
    earendil = _RecordingSpecialist()
    sauron = Sauron(specialists={"earendil": earendil})

    task = AgentTask(agent="sauron", type="execute", payload={"message": "uptime"})
    result = await sauron.run(task)

    assert result.status == TaskStatus.COMPLETED
    assert result.result["intent"] == "earendil"
    assert result.result["specialist"] == "earendil"
    assert len(earendil.received) == 1
    assert earendil.received[0].payload["message"] == "uptime"
    assert earendil.received[0].payload["parent_task_id"] == task.task_id


@pytest.mark.asyncio
async def test_sauron_routes_film_message_to_tombombadil():
    tom = _RecordingSpecialist()
    earendil = _RecordingSpecialist()
    sauron = Sauron(specialists={"tombombadil": tom, "earendil": earendil})

    task = AgentTask(
        agent="sauron",
        type="execute",
        payload={"message": "Film: Ran\nRating: 9"},
    )
    result = await sauron.run(task)

    assert result.status == TaskStatus.COMPLETED
    assert len(tom.received) == 1
    assert len(earendil.received) == 0


@pytest.mark.asyncio
async def test_sauron_fails_when_specialist_not_registered():
    sauron = Sauron(specialists={})
    task = AgentTask(agent="sauron", type="execute", payload={"message": "uptime"})
    result = await sauron.run(task)

    assert result.status == TaskStatus.FAILED
    # After SPECIALIST_TOOL_MAP refactor: the graph advertises only registered
    # specialists' tools. With no specialists registered, the mock LLM's
    # tool_use is rejected as "unknown tool" before reaching the "not
    # registered" check inside dispatch_tool. Either signal is acceptable
    # evidence that Sauron failed because no specialist could service the
    # request.
    assert "earendil" in result.error
    assert "not registered" in result.error or "unknown tool" in result.error


@pytest.mark.asyncio
async def test_sauron_fails_on_missing_message():
    sauron = Sauron(specialists={})
    task = AgentTask(agent="sauron", type="execute", payload={})
    result = await sauron.run(task)

    assert result.status == TaskStatus.FAILED
    assert "message" in result.error


@pytest.mark.asyncio
async def test_sauron_propagates_specialist_failure():
    failing = _RecordingSpecialist(status=TaskStatus.FAILED, error="kaboom")
    sauron = Sauron(specialists={"earendil": failing})

    task = AgentTask(agent="sauron", type="execute", payload={"message": "uptime"})
    result = await sauron.run(task)

    assert result.status == TaskStatus.FAILED
    assert result.error == "kaboom"
    assert result.result["specialist"] == "earendil"


@pytest.mark.asyncio
async def test_sauron_register_after_init():
    sauron = Sauron()
    earendil = _RecordingSpecialist()
    sauron.register("earendil", earendil)

    task = AgentTask(agent="sauron", type="execute", payload={"message": "uptime"})
    result = await sauron.run(task)

    assert result.status == TaskStatus.COMPLETED
