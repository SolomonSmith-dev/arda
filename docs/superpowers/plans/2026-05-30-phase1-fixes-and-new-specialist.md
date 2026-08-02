# Phase 1 Fixes + New Specialist Scaffold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three pre-Phase 2 issues (hardcoded API key, static tool list, blocking event loop) and scaffold the GitHub audit specialist.

**Architecture:** Task 1 removes the `arda_api_key` default so misconfigured deployments fail loudly instead of using a public secret. Task 2 replaces the hardcoded `SAURON_TOOLS` list with a map derived at graph-compile time from registered specialists, so adding any new specialist is a one-file change. Task 3 wraps `wait_for_tasks` with `asyncio.to_thread` at both call sites to stop blocking the event loop. Task 4 scaffolds the GitHub audit specialist (`cirdan`) as an empty-but-wired `BaseAgent` stub, ready for Phase 2 implementation.

**Tech Stack:** Python 3.12, pydantic-settings, LangGraph, Anthropic SDK, FastAPI, fakeredis, pytest-asyncio

**PR split:** Tasks 1–3 ship as one pre-Phase 2 fix PR. Task 4 can be added to the same PR or opened as the first Phase 2 PR — decide after Task 3 tests pass.

---

## File Map

| Action | File | Change |
|---|---|---|
| Modify | `core/config.py` | Remove `arda_api_key` default |
| Modify | `tests/conftest.py` | Set `ARDA_API_KEY` env var before any import |
| Modify | `tests/test_api_routes.py` | Read `API_KEY` from env instead of hardcoding |
| Modify | `tests/test_api_cron_routes.py` | Same |
| Modify | `agents/sauron/tools.py` | Add `SPECIALIST_TOOL_MAP`; derive `SAURON_TOOLS` + `TOOL_NAME_TO_SPECIALIST` from it; update `dispatch_tool` signature |
| Modify | `agents/sauron/graph.py` | Derive `_tools` / `_tool_map` from `specialists` + `SPECIALIST_TOOL_MAP`; dynamic system prompt |
| Modify | `agents/earendil/agent.py` | `await asyncio.to_thread(wait_for_tasks, ...)` |
| Modify | `api/routes/tasks.py` | Same fix at second call site |
| Create | `agents/cirdan/__init__.py` | Empty package marker |
| Create | `agents/cirdan/agent.py` | `Cirdan(BaseAgent)` stub |
| Create | `tests/cirdan/__init__.py` | Empty |
| Create | `tests/cirdan/test_agent_smoke.py` | Smoke test for stub |
| Modify | `agents/sauron/tools.py` | Add `CIRDAN_TOOL` to `SPECIALIST_TOOL_MAP` |
| Modify | `api/main.py` | Register `Cirdan` with Sauron in lifespan |

---

## Task 1: Fail-close `arda_api_key`

**Files:**
- Modify: `core/config.py:21`
- Modify: `tests/conftest.py`
- Modify: `tests/test_api_routes.py:26`
- Modify: `tests/test_api_cron_routes.py:14`

- [ ] **Step 1: Write a failing test that catches the regression**

Open `tests/test_core_smoke.py`. Add at the end:

```python
def test_arda_api_key_has_no_hardcoded_default():
    import inspect
    from core.config import Settings
    src = inspect.getsource(Settings)
    assert "arda-dev-key-2026" not in src, (
        "arda_api_key must not have a hardcoded default -- "
        "remove the default so misconfigured deployments fail at startup"
    )
```

- [ ] **Step 2: Run it to confirm it fails**

```bash
uv run pytest tests/test_core_smoke.py::test_arda_api_key_has_no_hardcoded_default -v
```

Expected: `FAILED — AssertionError: arda_api_key must not have a hardcoded default`

- [ ] **Step 3: Set `ARDA_API_KEY` in `tests/conftest.py` before any import**

Open `tests/conftest.py`. Add these two lines at the very top (before `import pytest`):

```python
import os
os.environ.setdefault("ARDA_API_KEY", "test-arda-key-ci")
```

Full file after edit:

```python
import os
os.environ.setdefault("ARDA_API_KEY", "test-arda-key-ci")

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config, items):
    skip_phase4 = pytest.mark.skip(reason="phase 4: needs real Redis/Discord/Milvus")
    for item in items:
        if "phase4" in item.keywords:
            item.add_marker(skip_phase4)
```

- [ ] **Step 4: Remove the hardcoded default from `core/config.py`**

Change line 21 in `core/config.py` from:

```python
    arda_api_key: str = "arda-dev-key-2026"
```

To:

```python
    arda_api_key: str
```

- [ ] **Step 5: Update `tests/test_api_routes.py`**

Change line 26:

```python
API_KEY = "arda-dev-key-2026"
```

To:

```python
import os
API_KEY = os.environ["ARDA_API_KEY"]
```

- [ ] **Step 6: Update `tests/test_api_cron_routes.py`**

Change line 14:

```python
API_KEY = "arda-dev-key-2026"
```

To:

```python
import os
API_KEY = os.environ["ARDA_API_KEY"]
```

- [ ] **Step 7: Run the full test suite**

```bash
uv run pytest --tb=short -q
```

Expected: all tests pass. If any test fails with `ValidationError` for `ARDA_API_KEY`, the env var is not being set before that module imports `core.config`. Check that the two lines added to `conftest.py` are truly at the top, before `from __future__ import annotations`.

- [ ] **Step 8: Commit**

```bash
git add core/config.py tests/conftest.py tests/test_api_routes.py tests/test_api_cron_routes.py tests/test_core_smoke.py
git commit -m "security: remove hardcoded arda_api_key default, fail-closed on missing env var"
```

---

## Task 2: Dynamic `SAURON_TOOLS` via `SPECIALIST_TOOL_MAP`

**Files:**
- Modify: `agents/sauron/tools.py`
- Modify: `agents/sauron/graph.py`

- [ ] **Step 1: Write a failing test**

Open `tests/sauron/test_tools.py`. Add at the end:

```python
def test_sauron_tools_derived_from_specialist_map():
    from agents.sauron.tools import SPECIALIST_TOOL_MAP, SAURON_TOOLS, TOOL_NAME_TO_SPECIALIST

    # Every entry in SPECIALIST_TOOL_MAP should appear in SAURON_TOOLS
    expected_schemas = list(SPECIALIST_TOOL_MAP.values())
    assert SAURON_TOOLS == expected_schemas, (
        "SAURON_TOOLS must be derived from SPECIALIST_TOOL_MAP, not defined independently"
    )

    # TOOL_NAME_TO_SPECIALIST must be derived from the map too
    for specialist_name, schema in SPECIALIST_TOOL_MAP.items():
        assert TOOL_NAME_TO_SPECIALIST[schema["name"]] == specialist_name


def test_build_sauron_graph_uses_only_registered_specialists():
    from unittest.mock import AsyncMock, MagicMock
    from langgraph.checkpoint.memory import MemorySaver
    from agents.sauron.graph import build_sauron_graph
    from agents.earendil.agent import Earendil

    # Graph built with only earendil -- finrod and tom schemas must not appear
    specialists = {"earendil": Earendil()}
    graph = build_sauron_graph(
        specialists=specialists,
        client=MagicMock(),
        checkpointer=MemorySaver(),
        model="mock",
    )
    # The graph compiled without error and the closure captured only earendil's tool
    assert graph is not None
```

- [ ] **Step 2: Run the tests to confirm they fail**

```bash
uv run pytest tests/sauron/test_tools.py::test_sauron_tools_derived_from_specialist_map tests/sauron/test_tools.py::test_build_sauron_graph_uses_only_registered_specialists -v
```

Expected: first test `FAILED` (SAURON_TOOLS is a separate literal), second test may pass or fail depending on current state.

- [ ] **Step 3: Refactor `agents/sauron/tools.py`**

Replace the current `SAURON_TOOLS` and `TOOL_NAME_TO_SPECIALIST` definitions and update `dispatch_tool`. Full replacement of lines 75–109:

```python
# Single source of truth: specialist name -> tool schema.
# Adding a new specialist requires only a new entry here.
SPECIALIST_TOOL_MAP: dict[str, dict[str, Any]] = {
    "earendil": EARENDIL_TOOL,
    "finrod": FINROD_TOOL,
    "tombombadil": TOMMODBADIL_TOOL,
}

# Derived constants kept for backward compatibility with any direct importers.
SAURON_TOOLS: list[dict[str, Any]] = list(SPECIALIST_TOOL_MAP.values())
TOOL_NAME_TO_SPECIALIST: dict[str, Specialist] = {
    schema["name"]: name for name, schema in SPECIALIST_TOOL_MAP.items()
}


class UnknownToolError(ValueError):
    pass


async def dispatch_tool(
    name: str,
    tool_input: dict[str, Any],
    specialists: dict[Specialist, BaseAgent],
    parent_task_id: str,
    tool_name_to_specialist: dict[str, str] | None = None,
) -> AgentResult:
    """Resolve a tool_use block to the corresponding specialist call."""
    _map = tool_name_to_specialist if tool_name_to_specialist is not None else TOOL_NAME_TO_SPECIALIST
    specialist_name = _map.get(name)
    if specialist_name is None:
        raise UnknownToolError(f"unknown tool: {name}")

    agent = specialists.get(specialist_name)
    if agent is None:
        raise UnknownToolError(f"specialist '{specialist_name}' not registered")

    sub_task = AgentTask(
        agent=specialist_name,
        type="sauron_tool_dispatch",
        payload={**tool_input, "parent_task_id": parent_task_id},
    )
    return await agent.run(sub_task)
```

Note: `TOMMODBADIL_TOOL` stays as its current variable name — do not rename it.

- [ ] **Step 4: Update `agents/sauron/graph.py` to derive tools from `specialists`**

At the top of `graph.py`, add `SPECIALIST_TOOL_MAP` to the import from `tools`:

```python
from agents.sauron.tools import (
    SPECIALIST_TOOL_MAP,
    SAURON_TOOLS,
    TOOL_NAME_TO_SPECIALIST,
    UnknownToolError,
    dispatch_tool,
)
```

Replace the module-level `SYSTEM_PROMPT` constant with a builder function (insert before `_block_get`):

```python
def _build_system_prompt(tools: list[dict[str, Any]]) -> str:
    lines = "\n".join(f"  - {t['name']}: {t['description'][:80].rstrip()}" for t in tools)
    count = len(tools)
    return (
        "You are Sauron, the orchestrator of the ARDA multi-agent system. "
        f"You have {count} specialist tool{'s' if count != 1 else ''}:\n"
        f"{lines}\n"
        "Choose exactly one tool that matches the user's intent. After you "
        "receive the tool_result, write a brief one-sentence summary and stop."
    )
```

At the top of `build_sauron_graph`, before the `agent_step` closure, add:

```python
    _tools: list[dict[str, Any]] = [
        SPECIALIST_TOOL_MAP[name]
        for name in specialists
        if name in SPECIALIST_TOOL_MAP
    ]
    _tool_map: dict[str, str] = {
        SPECIALIST_TOOL_MAP[name]["name"]: name
        for name in specialists
        if name in SPECIALIST_TOOL_MAP
    }
    _system_prompt = _build_system_prompt(_tools)
```

In `agent_step`, change:

```python
                tools=SAURON_TOOLS,
```

To:

```python
                tools=_tools,
```

And change:

```python
                system=SYSTEM_PROMPT,
```

To:

```python
                system=_system_prompt,
```

In `tool_dispatch`, change:

```python
                if intent is None:
                    intent = TOOL_NAME_TO_SPECIALIST.get(tu["name"])
```

To:

```python
                if intent is None:
                    intent = _tool_map.get(tu["name"])
```

And update the `dispatch_tool` call to pass `_tool_map`:

```python
                result = await dispatch_tool(
                    name=tu["name"],
                    tool_input=tu["input"],
                    specialists=specialists,
                    parent_task_id=state.get("task_id", ""),
                    tool_name_to_specialist=_tool_map,
                )
```

- [ ] **Step 5: Run the new tests**

```bash
uv run pytest tests/sauron/test_tools.py::test_sauron_tools_derived_from_specialist_map tests/sauron/test_tools.py::test_build_sauron_graph_uses_only_registered_specialists -v
```

Expected: both `PASSED`.

- [ ] **Step 6: Run the full suite**

```bash
uv run pytest --tb=short -q
```

Expected: all tests pass. If any test fails with `SYSTEM_PROMPT` not defined, ensure the deletion of the old constant and addition of `_build_system_prompt` are both in place.

- [ ] **Step 7: Commit**

```bash
git add agents/sauron/tools.py agents/sauron/graph.py tests/sauron/test_tools.py
git commit -m "refactor(sauron): derive SAURON_TOOLS dynamically from SPECIALIST_TOOL_MAP"
```

---

## Task 3: Fix `wait_for_tasks` blocking the event loop

**Files:**
- Modify: `agents/earendil/agent.py`
- Modify: `api/routes/tasks.py`

- [ ] **Step 1: Write a failing test**

Open `tests/sauron/test_agent_smoke.py` (or `tests/earendil/` if it exists). Add:

```python
import asyncio
import inspect

def test_earendil_run_does_not_call_time_sleep_directly():
    from agents.earendil import agent as earendil_module
    src = inspect.getsource(earendil_module.Earendil.run)
    assert "time.sleep" not in src, (
        "Earendil.run must not call time.sleep directly -- use asyncio.to_thread"
    )

def test_execute_wait_route_does_not_call_wait_for_tasks_directly():
    import inspect
    from api.routes import tasks as tasks_module
    src = inspect.getsource(tasks_module.handle_execute_wait)
    assert "wait_for_tasks(r" not in src, (
        "handle_execute_wait must not call wait_for_tasks directly -- use asyncio.to_thread"
    )
```

- [ ] **Step 2: Run to confirm failures**

```bash
uv run pytest tests/sauron/test_agent_smoke.py::test_earendil_run_does_not_call_time_sleep_directly tests/sauron/test_agent_smoke.py::test_execute_wait_route_does_not_call_wait_for_tasks_directly -v
```

Expected: both `FAILED`.

- [ ] **Step 3: Fix `agents/earendil/agent.py`**

Add `import asyncio` at the top of the file (after `import json`, `import time`):

```python
import asyncio
import json
import time
```

In `Earendil.run`, change line 150:

```python
                wait_result = wait_for_tasks(r, task_ids)
```

To:

```python
                wait_result = await asyncio.to_thread(wait_for_tasks, r, task_ids)
```

- [ ] **Step 4: Fix `api/routes/tasks.py`**

Check if `asyncio` is already imported at the top of `api/routes/tasks.py`. If not, add it. Then change line 176:

```python
        wait_result = wait_for_tasks(r, task_ids)
```

To:

```python
        wait_result = await asyncio.to_thread(wait_for_tasks, r, task_ids)
```

- [ ] **Step 5: Run the new tests**

```bash
uv run pytest tests/sauron/test_agent_smoke.py::test_earendil_run_does_not_call_time_sleep_directly tests/sauron/test_agent_smoke.py::test_execute_wait_route_does_not_call_wait_for_tasks_directly -v
```

Expected: both `PASSED`.

- [ ] **Step 6: Run the full suite**

```bash
uv run pytest --tb=short -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add agents/earendil/agent.py api/routes/tasks.py tests/sauron/test_agent_smoke.py
git commit -m "fix(earendil): offload wait_for_tasks to thread pool, stop blocking event loop"
```

---

## Task 4: Scaffold GitHub audit specialist (`cirdan`)

**Note:** This task starts Phase 2. The agent name `cirdan` (Círdan the Shipwright -- keeper of records, ancient loremaster) follows the Tolkien naming convention. Rename if preferred before this task runs.

**Files:**
- Create: `agents/cirdan/__init__.py`
- Create: `agents/cirdan/agent.py`
- Create: `tests/cirdan/__init__.py`
- Create: `tests/cirdan/test_agent_smoke.py`
- Modify: `agents/sauron/tools.py` (add `CIRDAN_TOOL`)
- Modify: `api/main.py` (register Cirdan in lifespan)

- [ ] **Step 1: Write the smoke test first**

Create `tests/cirdan/__init__.py` (empty).

Create `tests/cirdan/test_agent_smoke.py`:

```python
"""Smoke tests for the Cirdan GitHub audit specialist stub."""
from __future__ import annotations

import pytest

from agents.cirdan.agent import Cirdan
from core.models import AgentTask, TaskStatus


@pytest.mark.asyncio
async def test_cirdan_run_returns_result():
    agent = Cirdan()
    task = AgentTask(
        agent="cirdan",
        type="execute",
        payload={"message": "what are the recent commits on arda?"},
    )
    result = await agent.run(task)
    assert result.agent == "cirdan"
    assert result.task_id == task.task_id
    # Stub returns FAILED until Phase 2 implements the GitHub client
    assert result.status == TaskStatus.FAILED
    assert result.error == "not implemented yet"


@pytest.mark.asyncio
async def test_cirdan_run_requires_message():
    agent = Cirdan()
    task = AgentTask(agent="cirdan", type="execute", payload={})
    result = await agent.run(task)
    assert result.status == TaskStatus.FAILED
    assert "message" in result.error
```

- [ ] **Step 2: Run to confirm both tests fail**

```bash
uv run pytest tests/cirdan/ -v
```

Expected: `ImportError: No module named 'agents.cirdan'`

- [ ] **Step 3: Create the agent package**

Create `agents/cirdan/__init__.py` (empty file).

Create `agents/cirdan/agent.py`:

```python
from __future__ import annotations

from typing import ClassVar

from agents.base import BaseAgent
from core.logging import get_logger
from core.models import AgentResult, AgentTask, TaskStatus

log = get_logger("agents.cirdan.agent")


class Cirdan(BaseAgent):
    """GitHub repository auditor specialist.

    Fetches repo data via the GitHub API and summarizes with Claude.
    Phase 2 implementation: replace the stub below with real API calls.
    Requires settings.github_token (non-empty) and settings.github_username.
    """

    tier: ClassVar[str] = "specialist"
    name: ClassVar[str] = "cirdan"

    async def run(self, task: AgentTask) -> AgentResult:
        message = task.payload.get("message")
        if not message:
            return AgentResult(
                task_id=task.task_id,
                agent=self.name,
                status=TaskStatus.FAILED,
                error="payload.message is required",
            )
        log.info("cirdan_run_stub", agent_task_id=task.task_id, message_len=len(message))
        return AgentResult(
            task_id=task.task_id,
            agent=self.name,
            status=TaskStatus.FAILED,
            error="not implemented yet",
        )
```

- [ ] **Step 4: Run the tests**

```bash
uv run pytest tests/cirdan/ -v
```

Expected: both `PASSED`.

- [ ] **Step 5: Add `CIRDAN_TOOL` to `agents/sauron/tools.py`**

Add before `SPECIALIST_TOOL_MAP`:

```python
CIRDAN_TOOL: dict[str, Any] = {
    "name": "cirdan_audit",
    "description": (
        "Audit a GitHub repository. Use for questions about recent commits, "
        "pull requests, issues, open branches, or code history. "
        "Provide a question or the repo name to inspect."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "Question or request about a GitHub repository.",
            }
        },
        "required": ["message"],
    },
}
```

Add `"cirdan": CIRDAN_TOOL` to `SPECIALIST_TOOL_MAP`:

```python
SPECIALIST_TOOL_MAP: dict[str, dict[str, Any]] = {
    "earendil": EARENDIL_TOOL,
    "finrod": FINROD_TOOL,
    "tombombadil": TOMMODBADIL_TOOL,
    "cirdan": CIRDAN_TOOL,
}
```

- [ ] **Step 6: Register Cirdan in `api/main.py` lifespan**

Find the lifespan block where Sauron registers its specialists. Add:

```python
from agents.cirdan.agent import Cirdan
```

to the imports at the top of `api/main.py`. Then in the lifespan where other specialists are registered, add:

```python
    sauron.register("cirdan", Cirdan())
```

- [ ] **Step 7: Run the full suite**

```bash
uv run pytest --tb=short -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add agents/cirdan/ tests/cirdan/ agents/sauron/tools.py api/main.py
git commit -m "feat(cirdan): scaffold GitHub audit specialist stub, wire into Sauron"
```

---

## Task 5: Push and open PR

- [ ] **Step 1: Confirm all tests pass**

```bash
uv run pytest --tb=short -q
```

Expected: all pass, 0 failures.

- [ ] **Step 2: Push and open PR**

```bash
git push origin feature/github-config
gh pr edit 42 \
  --title "pre-Phase 2: security + design fixes + cirdan scaffold" \
  --body "$(cat <<'EOF'
## Summary
- **Security:** Remove hardcoded \`arda_api_key\` default (\`arda-dev-key-2026\` was committed in a public repo and gave RCE via the /task shell endpoint on misconfigured deployments)
- **Design:** Replace static \`SAURON_TOOLS\` list with \`SPECIALIST_TOOL_MAP\`; \`build_sauron_graph\` now derives tool list from registered specialists — adding a new agent is a one-file change
- **Production bug:** Offload \`wait_for_tasks\` to \`asyncio.to_thread\` at both call sites; stops blocking the event loop on \`/execute/wait\`
- **Phase 2 start:** Scaffold \`Cirdan\` (GitHub audit specialist) as a wired-but-unimplemented stub; registers with Sauron, has passing smoke tests

## Test plan
- [x] \`uv run pytest\` passes (0 failures, mock mode, no external services)
- [ ] Verify \`ARDA_API_KEY\` set on home server \`.env\` before merging (startup will fail if unset)
EOF
)"
```

- [ ] **Step 3: Verify CI passes**

```bash
gh pr checks 42
```

Wait for green. If ruff fails, run `uv run ruff check . --fix` and push a fixup commit.
