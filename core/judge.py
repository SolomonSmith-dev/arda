"""Judge layer: grade an agent's output against named checks, retry, record lessons.

The judge sees the task and the output only, never the worker's reasoning. It answers
through a forced tool call whose schema is ``Evaluation``, so the verdict is structured.
A run passes when every check is true. Each failed check becomes one line in
``settings.lessons_path`` with the trace id, for the nightly consolidation to read.

Skipped entirely in mock mode or when ``judge_enabled`` is off: the agent runs once and
no judge call is made.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from agents.base import BaseAgent
from core.config import settings
from core.logging import get_trace_id, new_trace_id
from core.models import AgentResult, AgentTask

MAX_ITERATIONS = 3
FEEDBACK_KEY = "judge_feedback"
_TOOL_NAME = "record_evaluation"

_SYSTEM = (
    "You grade one AI agent's output. You see the task it was given and the output it "
    "returned, nothing else. For every check listed, decide true (the output meets it) or "
    "false. Use exactly the check names given. Put a short reason for each false check in "
    "notes. Answer only by calling record_evaluation."
)


class Evaluation(BaseModel):
    checks: dict[str, bool]
    notes: str = ""

    @property
    def passed(self) -> bool:
        # An empty verdict is not a pass: the judge graded nothing.
        return bool(self.checks) and all(self.checks.values())

    def failed(self) -> list[str]:
        return [name for name, ok in self.checks.items() if not ok]


@dataclass
class JudgedResult:
    result: AgentResult
    evaluation: Evaluation | None
    iterations: int


def judge_active() -> bool:
    return settings.judge_enabled and not settings.use_mock_llm


def build_judge_client() -> Any:
    import anthropic

    kwargs: dict[str, Any] = {"api_key": settings.anthropic_api_key}
    if settings.judge_base_url:
        kwargs["base_url"] = settings.judge_base_url
    return anthropic.AsyncAnthropic(**kwargs)


def _judge_view(task: AgentTask, result: AgentResult) -> dict[str, Any]:
    payload = {k: v for k, v in task.payload.items() if k != FEEDBACK_KEY}
    return {
        "task": {"agent": task.agent, "type": task.type, "payload": payload},
        "output": {"status": str(result.status), "result": result.result, "error": result.error},
    }


async def judge(
    client: Any, model: str, task: AgentTask, result: AgentResult, checks: dict[str, str]
) -> Evaluation:
    """Ask the judge model for one verdict. ``checks`` maps check name to criterion."""
    listing = "\n".join(f"- {name}: {criterion}" for name, criterion in checks.items())
    content = (
        f"Checks:\n{listing}\n\n"
        f"Task and output:\n{json.dumps(_judge_view(task, result), default=str, indent=2)}"
    )
    resp = await client.messages.create(
        model=model,
        max_tokens=1024,
        system=_SYSTEM,
        messages=[{"role": "user", "content": content}],
        tools=[{
            "name": _TOOL_NAME,
            "description": "Record the verdict for every check.",
            "input_schema": Evaluation.model_json_schema(),
        }],
        tool_choice={"type": "tool", "name": _TOOL_NAME},
    )
    block = next(
        (b for b in resp.content if getattr(b, "type", None) == "tool_use"), None
    )
    if block is None:
        return Evaluation(checks={name: False for name in checks}, notes="judge returned no verdict")
    ev = Evaluation.model_validate(block.input)
    # A check the judge skipped counts as failed; names it invented are dropped.
    return Evaluation(
        checks={name: bool(ev.checks.get(name, False)) for name in checks}, notes=ev.notes
    )


def append_lessons(
    path: Path, task: AgentTask, evaluation: Evaluation, trace_id: str, iteration: int
) -> None:
    """Capture only: one line per failed check. Consolidation happens elsewhere."""
    failed = evaluation.failed()
    if not failed:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ")
    notes = " ".join(evaluation.notes.split()) or "no notes"
    with path.open("a") as f:
        for name in failed:
            f.write(
                f"- {stamp} | trace_id={trace_id} | agent={task.agent} | check={name} "
                f"| iteration={iteration} | {notes}\n"
            )


async def run_with_judge(
    agent: BaseAgent,
    task: AgentTask,
    checks: dict[str, str],
    *,
    client: Any | None = None,
    lessons_path: Path | None = None,
) -> JudgedResult:
    """Run ``agent``, judge it, and retry with the judge's notes up to MAX_ITERATIONS."""
    if not judge_active():
        return JudgedResult(result=await agent.run(task), evaluation=None, iterations=1)

    client = client or build_judge_client()
    path = lessons_path or Path(settings.lessons_path)
    trace_id = get_trace_id() or new_trace_id()
    current = task
    for iteration in range(1, MAX_ITERATIONS + 1):
        result = await agent.run(current)
        evaluation = await judge(client, settings.judge_model, task, result, checks)
        append_lessons(path, task, evaluation, trace_id, iteration)
        if evaluation.passed or iteration == MAX_ITERATIONS:
            return JudgedResult(result=result, evaluation=evaluation, iterations=iteration)
        feedback = {"failed_checks": evaluation.failed(), "notes": evaluation.notes}
        current = task.model_copy(update={"payload": {**task.payload, FEEDBACK_KEY: feedback}})
    raise AssertionError("unreachable")
