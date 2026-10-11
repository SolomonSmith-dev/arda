"""Judge layer: mock-mode skip, retry loop, lessons capture. No network: the judge
client is a fake that returns canned tool_use verdicts."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from agents.base import BaseAgent
from core import judge as judge_mod
from core.config import settings
from core.logging import set_trace_id
from core.models import AgentResult, AgentTask, TaskStatus

CHECKS = {"answers_question": "The output answers the question.", "cites_source": "It cites one."}


class EchoAgent(BaseAgent):
    tier = "specialist"
    name = "echo"

    def __init__(self) -> None:
        self.tasks: list[AgentTask] = []

    async def run(self, task: AgentTask) -> AgentResult:
        self.tasks.append(task)
        return AgentResult(
            task_id=task.task_id, agent="echo", status=TaskStatus.COMPLETED,
            result={"answer": "42"},
        )


class FakeJudgeClient:
    def __init__(self, verdicts: list[dict[str, Any]]) -> None:
        self._verdicts = list(verdicts)
        self.requests: list[dict[str, Any]] = []
        self.messages = self

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        block = SimpleNamespace(type="tool_use", name="record_evaluation", input=self._verdicts.pop(0))
        return SimpleNamespace(content=[block])


FAIL = {"checks": {"answers_question": True, "cites_source": False}, "notes": "no\nsource"}
PASS = {"checks": {"answers_question": True, "cites_source": True}, "notes": ""}


@pytest.fixture
def judge_on(monkeypatch):
    monkeypatch.setattr(settings, "use_mock_llm", False)
    monkeypatch.setattr(settings, "judge_enabled", True)
    set_trace_id("trace-abc")
    yield
    set_trace_id(None)


def _task() -> AgentTask:
    return AgentTask(agent="echo", type="ask", payload={"message": "meaning of life?"})


def test_evaluation_passes_only_when_every_check_is_true():
    assert judge_mod.Evaluation(checks={"a": True, "b": True}).passed
    assert not judge_mod.Evaluation(checks={"a": True, "b": False}).passed
    assert not judge_mod.Evaluation(checks={}).passed


async def test_mock_mode_skips_the_judge(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "use_mock_llm", True)
    monkeypatch.setattr(settings, "judge_enabled", True)
    client, agent, lessons = FakeJudgeClient([]), EchoAgent(), tmp_path / "lessons.md"
    out = await judge_mod.run_with_judge(agent, _task(), CHECKS, client=client, lessons_path=lessons)
    assert out.evaluation is None and out.iterations == 1
    assert client.requests == [] and len(agent.tasks) == 1 and not lessons.exists()


async def test_disabled_judge_is_skipped_even_with_live_llm(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "use_mock_llm", False)
    monkeypatch.setattr(settings, "judge_enabled", False)
    client = FakeJudgeClient([])
    out = await judge_mod.run_with_judge(
        EchoAgent(), _task(), CHECKS, client=client, lessons_path=tmp_path / "l.md"
    )
    assert out.evaluation is None and client.requests == []


async def test_retries_with_feedback_until_pass_and_logs_each_failed_check(judge_on, tmp_path):
    client, agent, lessons = FakeJudgeClient([FAIL, PASS]), EchoAgent(), tmp_path / "m" / "l.md"
    out = await judge_mod.run_with_judge(agent, _task(), CHECKS, client=client, lessons_path=lessons)
    assert out.evaluation is not None and out.evaluation.passed and out.iterations == 2
    assert "judge_feedback" not in agent.tasks[0].payload
    assert agent.tasks[1].payload["judge_feedback"]["failed_checks"] == ["cites_source"]
    lines = lessons.read_text().splitlines()
    assert len(lines) == 1
    assert "trace_id=trace-abc" in lines[0] and "check=cites_source" in lines[0]
    assert "iteration=1" in lines[0] and lines[0].endswith("| no source")


async def test_stops_after_three_iterations(judge_on, tmp_path):
    client, agent, lessons = FakeJudgeClient([FAIL] * 5), EchoAgent(), tmp_path / "l.md"
    out = await judge_mod.run_with_judge(agent, _task(), CHECKS, client=client, lessons_path=lessons)
    assert out.iterations == judge_mod.MAX_ITERATIONS == 3
    assert out.evaluation is not None and not out.evaluation.passed
    assert len(agent.tasks) == 3 and len(client.requests) == 3
    assert len(lessons.read_text().splitlines()) == 3


async def test_judge_sees_task_and_output_only(judge_on, tmp_path):
    client = FakeJudgeClient([FAIL, PASS])
    await judge_mod.run_with_judge(
        EchoAgent(), _task(), CHECKS, client=client, lessons_path=tmp_path / "l.md"
    )
    req = client.requests[1]
    assert req["model"] == settings.judge_model
    assert req["tool_choice"] == {"type": "tool", "name": "record_evaluation"}
    content = req["messages"][0]["content"]
    view = json.loads(content.split("Task and output:\n", 1)[1])
    assert set(view) == {"task", "output"}
    # The retry's feedback is the worker's input, not the judge's.
    assert view["task"]["payload"] == {"message": "meaning of life?"}
    assert all(name in content for name in CHECKS)


async def test_missing_check_counts_as_failed(judge_on):
    client = FakeJudgeClient([{"checks": {"answers_question": True, "made_up": True}, "notes": ""}])
    task = _task()
    result = await EchoAgent().run(task)
    ev = await judge_mod.judge(client, "m", task, result, CHECKS)
    assert ev.checks == {"answers_question": True, "cites_source": False}
