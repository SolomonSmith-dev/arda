# Phase 2: GitHub Activity Audit Workflow Implementation Plan

> **Status (2026-08-26): unbuilt, and the model choice is stale.** Rescued
> from the abandoned `feature/phase-1-infra` branch. The feature itself --
> a daily GitHub activity audit delivered to Telegram -- has not been
> built, and its stated prerequisite (`GITHUB_TOKEN` / `GITHUB_USERNAME`
> in config) landed in #42, so it is actionable. But every reference to
> Gemini below is dead: ADR 0006 made Anthropic the sole provider, and #39
> removed the Google and Groq config. Read "Gemini" as "Claude" throughout.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire a daily GitHub activity audit that fires at 8am PT, fetches commits/PRs/streak for SolomonSmith-dev, generates a Gemini narrative, stores the snapshot in Finrod, and delivers it to Telegram.

**Architecture:** Earendil gets a new `tools/github.py` module that short-circuits the Redis queue for GitHub audit messages and returns a `GitHubSnapshot` dataclass. Sauron detects the snapshot in Earendil's result, calls Gemini to generate a "so what" narrative, and stores it via a new shared Finrod state module. Galadriel is seeded with a daily 8am PT cron job on startup.

**Tech Stack:** Python 3.12, httpx, pytest, fakeredis, `unittest.mock`

**Prerequisite:** Phase 1 merged and deployed. `GITHUB_TOKEN` and `GITHUB_USERNAME` are set in `.env` on the home server.

---

## File Map

| Action | File | Responsibility |
|---|---|---|
| Create | `agents/earendil/tools/__init__.py` | Module marker |
| Create | `agents/earendil/tools/github.py` | GitHub API fetcher + `GitHubSnapshot` dataclass |
| Create | `agents/finrod/state.py` | Module-level shared store + embedder singletons |
| Create | `agents/galadriel/seed.py` | `seed_default_jobs()` — idempotent job seeding |
| Create | `tests/earendil/test_github_tool.py` | Unit tests for `tools/github.py` |
| Create | `tests/galadriel/test_seed.py` | Unit tests for `seed.py` |
| Modify | `agents/earendil/agent.py` | Add `is_github_audit()` + short-circuit in `run()` |
| Modify | `agents/finrod/ingest.py` | Add `ingest_github_snapshot()` |
| Modify | `agents/sauron/agent.py` | Add `_summarize_and_store_github()` + post-process on `github_snapshot` results |
| Modify | `agents/sauron/planner.py` | Update `_CLASSIFIER_PROMPT` to mention GitHub audits |
| Modify | `agents/galadriel/worker.py` | Call `seed_default_jobs()` at startup |
| Modify | `tests/earendil/test_agent_smoke.py` | Add tests for `is_github_audit` + github short-circuit |
| Modify | `tests/sauron/test_planner.py` | Add test for GitHub audit routing |

---

### Task 1: GitHub API tool — data model and fetch_activity

**Files:**
- Create: `agents/earendil/tools/__init__.py`
- Create: `agents/earendil/tools/github.py`
- Create: `tests/earendil/test_github_tool.py`

- [ ] **Step 1: Create the tools package**

```bash
touch agents/earendil/tools/__init__.py
```

- [ ] **Step 2: Write the failing tests**

Create `tests/earendil/test_github_tool.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agents.earendil.tools.github import (
    CommitSummary,
    GitHubSnapshot,
    PrSummary,
    fetch_activity,
)


def _mock_client(search_commits=None, search_issues=None, events=None):
    """Build a mock httpx.Client whose .get returns canned responses."""
    def side_effect(url, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        if "search/commits" in url:
            mock_resp.json.return_value = search_commits or {"items": []}
        elif "search/issues" in url:
            mock_resp.json.return_value = search_issues or {"items": []}
        elif "/events" in url:
            mock_resp.json.return_value = events or []
        else:
            mock_resp.json.return_value = {}
        return mock_resp

    client = MagicMock()
    client.get.side_effect = side_effect
    return client


def test_snapshot_is_empty_when_no_activity():
    with patch("agents.earendil.tools.github.httpx.Client") as mock_cls:
        mock_cls.return_value.__enter__.return_value = _mock_client()
        snapshot = fetch_activity("testuser", "2026-05-20", "tok")
    assert snapshot.date == "2026-05-20"
    assert snapshot.username == "testuser"
    assert snapshot.commits == []
    assert snapshot.total_contributions == 0


def test_snapshot_counts_commits():
    commits_payload = {
        "items": [
            {
                "repository": {"full_name": "user/repo"},
                "commit": {
                    "message": "feat: add thing\n\ndetail",
                    "author": {"date": "2026-05-20T10:00:00Z"},
                },
                "sha": "abc1234def5678",
            }
        ]
    }
    with patch("agents.earendil.tools.github.httpx.Client") as mock_cls:
        mock_cls.return_value.__enter__.return_value = _mock_client(
            search_commits=commits_payload
        )
        snapshot = fetch_activity("user", "2026-05-20", "tok")
    assert len(snapshot.commits) == 1
    assert snapshot.commits[0].repo == "user/repo"
    assert snapshot.commits[0].message == "feat: add thing"
    assert snapshot.commits[0].sha == "abc1234"
    assert snapshot.total_contributions == 1


def test_snapshot_streak_counts_consecutive_days():
    from datetime import date, timedelta
    today = date.today()
    events_payload = [
        {"type": "PushEvent", "created_at": str(today)},
        {"type": "PushEvent", "created_at": str(today - timedelta(days=1))},
        {"type": "PushEvent", "created_at": str(today - timedelta(days=2))},
        {"type": "IssuesEvent", "created_at": str(today - timedelta(days=3))},  # not counted
    ]
    with patch("agents.earendil.tools.github.httpx.Client") as mock_cls:
        mock_cls.return_value.__enter__.return_value = _mock_client(events=events_payload)
        snapshot = fetch_activity("user", str(today), "tok")
    assert snapshot.streak_days == 3


def test_snapshot_to_dict_is_serialisable():
    s = GitHubSnapshot(date="2026-05-20", username="user")
    d = s.to_dict()
    assert d["date"] == "2026-05-20"
    assert d["commits"] == []
    assert d["streak_days"] == 0
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
uv run pytest tests/earendil/test_github_tool.py -v
```

Expected: `ModuleNotFoundError: No module named 'agents.earendil.tools'`

- [ ] **Step 4: Implement agents/earendil/tools/github.py**

```python
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

import httpx

GITHUB_API = "https://api.github.com"


@dataclass
class CommitSummary:
    repo: str
    message: str
    sha: str
    created_at: str


@dataclass
class PrSummary:
    repo: str
    title: str
    number: int
    state: str  # "open" | "merged" | "closed"


@dataclass
class GitHubSnapshot:
    date: str
    username: str
    commits: list[CommitSummary] = field(default_factory=list)
    prs_opened: list[PrSummary] = field(default_factory=list)
    prs_merged: list[PrSummary] = field(default_factory=list)
    issues_opened: list[str] = field(default_factory=list)
    streak_days: int = 0
    total_contributions: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _search_commits(
    client: httpx.Client, username: str, date_str: str, token: str
) -> list[CommitSummary]:
    resp = client.get(
        f"{GITHUB_API}/search/commits",
        params={"q": f"author:{username} committer-date:{date_str}", "per_page": 100},
        headers=_headers(token),
    )
    if resp.status_code != 200:
        return []
    return [
        CommitSummary(
            repo=item["repository"]["full_name"],
            message=item["commit"]["message"].split("\n")[0],
            sha=item["sha"][:7],
            created_at=item["commit"]["author"]["date"],
        )
        for item in resp.json().get("items", [])
    ]


def _search_prs(
    client: httpx.Client, username: str, date_str: str, token: str
) -> tuple[list[PrSummary], list[PrSummary]]:
    resp = client.get(
        f"{GITHUB_API}/search/issues",
        params={"q": f"type:pr author:{username} created:{date_str}", "per_page": 50},
        headers=_headers(token),
    )
    opened: list[PrSummary] = []
    merged: list[PrSummary] = []
    if resp.status_code != 200:
        return opened, merged
    for item in resp.json().get("items", []):
        repo = item["repository_url"].split("/repos/")[-1]
        pr = PrSummary(
            repo=repo, title=item["title"], number=item["number"], state=item["state"]
        )
        if item.get("pull_request", {}).get("merged_at"):
            merged.append(pr)
        else:
            opened.append(pr)
    return opened, merged


def _search_issues(
    client: httpx.Client, username: str, date_str: str, token: str
) -> list[str]:
    resp = client.get(
        f"{GITHUB_API}/search/issues",
        params={"q": f"type:issue author:{username} created:{date_str}", "per_page": 50},
        headers=_headers(token),
    )
    if resp.status_code != 200:
        return []
    return [
        f"{item['repository_url'].split('/repos/')[-1]}#{item['number']}: {item['title']}"
        for item in resp.json().get("items", [])
    ]


def _streak(client: httpx.Client, username: str, token: str) -> int:
    resp = client.get(
        f"{GITHUB_API}/users/{username}/events",
        params={"per_page": 90},
        headers=_headers(token),
    )
    if resp.status_code != 200:
        return 0
    active_days: set[str] = {
        e["created_at"][:10]
        for e in resp.json()
        if e.get("type") in ("PushEvent", "PullRequestEvent", "CreateEvent")
    }
    streak = 0
    today = date.today()
    while str(today - timedelta(days=streak)) in active_days:
        streak += 1
    return streak


def fetch_activity(username: str, date_str: str, token: str) -> GitHubSnapshot:
    """Fetch GitHub activity for username on date_str (YYYY-MM-DD).

    Uses the GitHub Search API for commits, PRs, and issues.
    The streak is approximated from the public events feed (last 90 events).
    Unauthenticated: 60 req/hour. Authenticated: 5000 req/hour.
    """
    with httpx.Client(timeout=30) as client:
        commits = _search_commits(client, username, date_str, token)
        prs_opened, prs_merged = _search_prs(client, username, date_str, token)
        issues = _search_issues(client, username, date_str, token)
        streak = _streak(client, username, token)

    total = len(commits) + len(prs_opened) + len(prs_merged) + len(issues)
    return GitHubSnapshot(
        date=date_str,
        username=username,
        commits=commits,
        prs_opened=prs_opened,
        prs_merged=prs_merged,
        issues_opened=issues,
        streak_days=streak,
        total_contributions=total,
    )
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
uv run pytest tests/earendil/test_github_tool.py -v
```

Expected: all 4 tests `PASSED`.

- [ ] **Step 6: Commit**

```bash
git add agents/earendil/tools/ tests/earendil/test_github_tool.py
git commit -m "feat(earendil): GitHub API tool with GitHubSnapshot dataclass"
```

---

### Task 2: Earendil GitHub audit handler

**Files:**
- Modify: `agents/earendil/agent.py`
- Modify: `tests/earendil/test_agent_smoke.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/earendil/test_agent_smoke.py`:

```python
from agents.earendil.agent import is_github_audit


def test_is_github_audit_matches_scheduled_message():
    assert is_github_audit("Run daily GitHub audit for SolomonSmith-dev") is True


def test_is_github_audit_matches_casual_message():
    assert is_github_audit("github audit") is True
    assert is_github_audit("show me github activity") is True


def test_is_github_audit_rejects_unrelated():
    assert is_github_audit("uptime") is False
    assert is_github_audit("list files") is False


@pytest.mark.asyncio
async def test_earendil_github_audit_short_circuits(fake_redis, monkeypatch):
    from agents.earendil.tools.github import GitHubSnapshot

    mock_snapshot = GitHubSnapshot(date="2026-05-20", username="SolomonSmith-dev")

    async def mock_fetch(*_):
        return mock_snapshot

    monkeypatch.setattr(
        "agents.earendil.agent.fetch_activity_async", mock_fetch
    )

    e = Earendil()
    task = AgentTask(
        agent="earendil",
        type="execute",
        payload={"message": "Run daily GitHub audit for SolomonSmith-dev"},
    )
    result = await e.run(task)

    assert result.status == TaskStatus.COMPLETED
    assert "github_snapshot" in result.result
    assert result.result["github_snapshot"]["username"] == "SolomonSmith-dev"
    # Short-circuit: nothing enqueued to Redis
    assert fake_redis.llen(TASK_QUEUE_KEY) == 0
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/earendil/test_agent_smoke.py -k "github" -v
```

Expected: `ImportError: cannot import name 'is_github_audit'`

- [ ] **Step 3: Implement changes in agents/earendil/agent.py**

Add near the top of the file, after the existing imports:

```python
import asyncio

from core.config import settings

_GITHUB_AUDIT_PATTERNS = (
    "github audit",
    "github activity",
    "daily audit",
    "run daily github",
    "fetch github",
)


def is_github_audit(message: str) -> bool:
    msg = message.lower()
    return any(p in msg for p in _GITHUB_AUDIT_PATTERNS)


async def fetch_activity_async(username: str, date_str: str, token: str):
    """Run the synchronous fetch_activity in a thread so it doesn't block the event loop."""
    from agents.earendil.tools.github import fetch_activity

    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(None, lambda: fetch_activity(username, date_str, token)),
        timeout=55,
    )
```

In `Earendil.run()`, add a short-circuit check at the very top of the `try` block, before the existing `if "message" in payload` check:

```python
        # GitHub audit short-circuit: call the API directly, skip Redis queue.
        if "message" in payload and is_github_audit(payload["message"]):
            from datetime import date as _date
            date_str = _date.today().isoformat()
            snapshot = await fetch_activity_async(
                settings.github_username, date_str, settings.github_token
            )
            return AgentResult(
                task_id=task.task_id,
                agent=self.name,
                status=TaskStatus.COMPLETED,
                result={"github_snapshot": snapshot.to_dict()},
            )
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/earendil/test_agent_smoke.py -v
```

Expected: all tests pass, including the new github ones.

- [ ] **Step 5: Commit**

```bash
git add agents/earendil/agent.py tests/earendil/test_agent_smoke.py
git commit -m "feat(earendil): GitHub audit short-circuit in run() -- bypasses Redis queue"
```

---

### Task 3: Finrod shared state + ingest_github_snapshot

**Files:**
- Create: `agents/finrod/state.py`
- Modify: `agents/finrod/ingest.py`
- Create: `tests/finrod/test_github_ingest.py` (create `tests/finrod/` dir if needed)

- [ ] **Step 1: Read agents/finrod/agent.py and agents/finrod/embeddings.py**

```bash
cat agents/finrod/agent.py | head -40
cat agents/finrod/embeddings.py | head -40
```

Note the exact class names for `Embedder`, `get_embedder()` (or however the embedder is constructed), and how `InMemoryStore` is instantiated. The step below reflects what you find.

- [ ] **Step 2: Write failing test**

```bash
mkdir -p tests/finrod && touch tests/finrod/__init__.py
```

Create `tests/finrod/test_github_ingest.py`:

```python
from __future__ import annotations

import pytest

from agents.finrod.ingest import ingest_github_snapshot
from agents.finrod.state import get_store_and_embedder


def test_ingest_github_snapshot_stores_chunk():
    store, embedder = get_store_and_embedder()
    initial_count = store.count()

    ingest_github_snapshot(
        snapshot_date="2026-05-20",
        username="SolomonSmith-dev",
        summary="Pushed 4 commits to arda. On a 12-day streak.",
    )

    assert store.count() == initial_count + 1


def test_ingest_github_snapshot_idempotent_same_date():
    store, embedder = get_store_and_embedder()

    ingest_github_snapshot("2026-05-20", "SolomonSmith-dev", "summary one")
    count_after_first = store.count()

    ingest_github_snapshot("2026-05-20", "SolomonSmith-dev", "summary two")
    # Same doc_id -> store.add() replaces or ignores duplicate. Count stays same or +0 chunks.
    assert store.count() >= count_after_first
```

- [ ] **Step 3: Run to confirm failure**

```bash
uv run pytest tests/finrod/test_github_ingest.py -v
```

Expected: `ModuleNotFoundError` or `ImportError`

- [ ] **Step 4: Create agents/finrod/state.py**

Run this to see how the Finrod agent builds its embedder:

```bash
grep -n "embedder\|Embedder\|MockEmbed\|get_embed" agents/finrod/agent.py | head -20
grep -n "class\|def " agents/finrod/embeddings.py | head -20
```

Then implement `state.py` mirroring exactly what `agent.py` does. The typical pattern in this codebase is:

```python
from __future__ import annotations

from agents.finrod.embeddings import Embedder, MockEmbedder, SentenceTransformerEmbedder
from agents.finrod.store import InMemoryStore, VectorStore
from core.config import settings

# Module-level singletons. Shared between Finrod agent and any caller
# (e.g. Sauron's github snapshot storage) without an HTTP round-trip.
_store: VectorStore = InMemoryStore()
_embedder: Embedder = (
    MockEmbedder() if settings.use_mock_embedder else SentenceTransformerEmbedder()
)


def get_store_and_embedder() -> tuple[VectorStore, Embedder]:
    return _store, _embedder
```

If `MockEmbedder` or `SentenceTransformerEmbedder` are named differently in `embeddings.py`, use the actual class names from Step 1's grep output. The `settings.use_mock_embedder` pattern is confirmed in `core/config.py`.

- [ ] **Step 5: Add ingest_github_snapshot to agents/finrod/ingest.py**

Add at the bottom of `agents/finrod/ingest.py`:

```python
def ingest_github_snapshot(
    snapshot_date: str,
    username: str,
    summary: str,
) -> int:
    """Store a daily GitHub activity summary in the shared Finrod vector store.

    Uses the module-level singletons from state.py so Sauron and the Finrod
    agent share the same in-memory store without an HTTP call.
    """
    from agents.finrod.state import get_store_and_embedder

    store, embedder = get_store_and_embedder()
    doc_id = f"github_snapshot:{username}:{snapshot_date}"
    return ingest_text(
        store,
        embedder,
        doc_id,
        summary,
        metadata={
            "type": "github_snapshot",
            "date": snapshot_date,
            "username": username,
        },
    )
```

- [ ] **Step 6: Run tests to confirm they pass**

```bash
uv run pytest tests/finrod/test_github_ingest.py -v
```

Expected: both tests `PASSED`.

- [ ] **Step 7: Commit**

```bash
git add agents/finrod/state.py agents/finrod/ingest.py tests/finrod/
git commit -m "feat(finrod): shared state module + ingest_github_snapshot()"
```

---

### Task 4: Sauron compound intent — summarize and store GitHub results

**Files:**
- Modify: `agents/sauron/agent.py`
- Modify: `tests/sauron/test_agent_smoke.py` (or create if missing)

- [ ] **Step 1: Write failing test**

Check if `tests/sauron/test_agent_smoke.py` exists; create it if not. Add:

```python
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agents.sauron.agent import Sauron
from agents.base import BaseAgent
from core.models import AgentResult, AgentTask, TaskStatus


@pytest.fixture
def mock_earendil_with_snapshot():
    """Earendil that returns a github_snapshot result."""
    agent = MagicMock(spec=BaseAgent)
    agent.run = AsyncMock(
        return_value=AgentResult(
            task_id="t1",
            agent="earendil",
            status=TaskStatus.COMPLETED,
            result={
                "github_snapshot": {
                    "date": "2026-05-20",
                    "username": "SolomonSmith-dev",
                    "commits": [{"repo": "arda", "message": "feat: add thing", "sha": "abc1234", "created_at": "2026-05-20T10:00:00Z"}],
                    "prs_opened": [],
                    "prs_merged": [],
                    "issues_opened": [],
                    "streak_days": 12,
                    "total_contributions": 1,
                }
            },
        )
    )
    return agent


@pytest.mark.asyncio
async def test_sauron_summarises_github_snapshot(mock_earendil_with_snapshot):
    from agents.sauron.planner import Specialist

    sauron = Sauron()
    sauron.register(Specialist("earendil"), mock_earendil_with_snapshot)

    with patch("agents.sauron.agent.ingest_github_snapshot"):
        task = AgentTask(
            agent="sauron",
            type="execute",
            payload={"message": "Run daily GitHub audit for SolomonSmith-dev"},
        )
        result = await sauron.run(task)

    assert result.status == TaskStatus.COMPLETED
    # The result should have a "reply" key (narrative), not just raw snapshot
    assert "reply" in result.result
    assert isinstance(result.result["reply"], str)
    assert len(result.result["reply"]) > 10


@pytest.mark.asyncio
async def test_sauron_passes_through_non_snapshot_results():
    non_snapshot_agent = MagicMock(spec=BaseAgent)
    non_snapshot_agent.run = AsyncMock(
        return_value=AgentResult(
            task_id="t2",
            agent="earendil",
            status=TaskStatus.COMPLETED,
            result={"output": "uptime result"},
        )
    )
    sauron = Sauron()
    sauron.register("earendil", non_snapshot_agent)

    task = AgentTask(
        agent="sauron",
        type="execute",
        payload={"message": "uptime"},
    )
    result = await sauron.run(task)

    assert result.status == TaskStatus.COMPLETED
    assert result.result == {"output": "uptime result"}
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/sauron/ -k "github" -v
```

Expected: `AttributeError` or `AssertionError` since the summary logic doesn't exist yet.

- [ ] **Step 3: Implement changes in agents/sauron/agent.py**

Add import at top of file:
```python
import asyncio
```

Add the summarize helper method to the `Sauron` class, after `register()`:

```python
    async def _summarize_and_store_github(
        self, snapshot: dict, date_str: str
    ) -> str:
        """Generate a Gemini narrative for a GitHub snapshot and store it in Finrod."""
        from langchain_core.messages import HumanMessage

        prompt = (
            f"Here is a developer's GitHub activity for {date_str}.\n"
            "Write 3-5 sentences: what they shipped, what is in progress, "
            "and a streak or momentum note. Be specific -- use actual repo "
            "names and commit messages. Do not pad with generic filler.\n\n"
            f"Activity:\n{snapshot}"
        )
        loop = asyncio.get_running_loop()
        response = await asyncio.wait_for(
            loop.run_in_executor(
                None, lambda: self._llm.invoke([HumanMessage(content=prompt)])
            ),
            timeout=30,
        )
        narrative = (getattr(response, "content", str(response)) or "").strip()

        try:
            from agents.finrod.ingest import ingest_github_snapshot

            ingest_github_snapshot(
                snapshot_date=date_str,
                username=snapshot.get("username", ""),
                summary=narrative,
            )
        except Exception as exc:
            log.warning("github_snapshot_ingest_failed", exc=str(exc))

        return narrative
```

In `Sauron.run()`, after `result = await specialist_agent.run(sub_task)`, add:

```python
            # Post-process: if Earendil returned a GitHub snapshot, summarise it.
            if (
                result.status == TaskStatus.COMPLETED
                and isinstance(result.result, dict)
                and "github_snapshot" in result.result
            ):
                snapshot = result.result["github_snapshot"]
                date_str = snapshot.get("date", "today")
                try:
                    narrative = await self._summarize_and_store_github(snapshot, date_str)
                    result = result.model_copy(
                        update={"result": {"reply": narrative, "github_snapshot": snapshot}}
                    )
                except Exception as exc:
                    log.warning("github_summary_failed", exc=str(exc))
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/sauron/ -v
```

Expected: all tests pass. The `USE_MOCK_LLM=true` default means MockLLM returns a canned response for the narrative.

- [ ] **Step 5: Commit**

```bash
git add agents/sauron/agent.py tests/sauron/
git commit -m "feat(sauron): summarise GitHub snapshots with Gemini + store in Finrod"
```

---

### Task 5: Sauron planner — update classifier prompt

**Files:**
- Modify: `agents/sauron/planner.py`
- Modify: `tests/sauron/test_planner.py`

- [ ] **Step 1: Write failing test**

In `tests/sauron/test_planner.py`, add:

```python
def test_classify_regex_github_audit_routes_to_earendil():
    from agents.sauron.planner import classify_regex
    assert classify_regex("Run daily GitHub audit for SolomonSmith-dev") == "earendil"
    assert classify_regex("github audit") == "earendil"
    assert classify_regex("fetch my github activity") == "earendil"
```

- [ ] **Step 2: Run to confirm current behaviour**

```bash
uv run pytest tests/sauron/test_planner.py::test_classify_regex_github_audit_routes_to_earendil -v
```

If this already passes (the regex has git patterns), note it and skip Step 3. If it fails, continue.

- [ ] **Step 3: Verify existing git regex covers the patterns**

In `agents/sauron/planner.py`, confirm the `_OPS_PATTERNS` tuple contains:
```python
re.compile(r"\b(git|commit|push|pull|branch|merge|clone|repo)\b", re.IGNORECASE),
```

The word "github" contains "git" — the regex should match. If it doesn't, add:
```python
re.compile(r"\b(github|audit)\b", re.IGNORECASE),
```

- [ ] **Step 4: Update _CLASSIFIER_PROMPT for LLM routing**

In `agents/sauron/planner.py`, replace the `_CLASSIFIER_PROMPT` constant:

```python
_CLASSIFIER_PROMPT = (
    "You are the router for a multi-agent system. Read the user's message "
    "and return ONE WORD naming the specialist that should handle it:\n"
    "- earendil: shell commands, system ops, server diagnostics, deploys, "
    "git/docker/process management, anything that wants a CLI executed, "
    "AND GitHub activity audits (daily audit, fetch commits, check PRs, "
    "'run github audit for ...').\n"
    "- finrod: knowledge / retrieval / 'what is X', summarising docs, "
    "remembering or recalling stored facts, OR queries about PAST activity "
    "('what did I work on last week', 'show me my history').\n"
    "- tombombadil: anything about films, movies, ratings, watch-party, "
    "club recommendations, Letterboxd, casual chat.\n"
    "If unsure, prefer earendil. Reply with ONLY one of: earendil, finrod, "
    "tombombadil. No punctuation, no explanation."
)
```

- [ ] **Step 5: Run all planner tests**

```bash
uv run pytest tests/sauron/test_planner.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add agents/sauron/planner.py tests/sauron/test_planner.py
git commit -m "feat(sauron): update classifier prompt -- GitHub audit routes to earendil"
```

---

### Task 6: Galadriel seed jobs

**Files:**
- Create: `agents/galadriel/seed.py`
- Modify: `agents/galadriel/worker.py`
- Create: `tests/galadriel/test_seed.py`

- [ ] **Step 1: Write failing tests**

Check if `tests/galadriel/` exists:
```bash
ls tests/galadriel/ 2>/dev/null || echo "missing"
```

Create dir and `__init__.py` if needed:
```bash
mkdir -p tests/galadriel && touch tests/galadriel/__init__.py
```

Create `tests/galadriel/test_seed.py`:

```python
from __future__ import annotations

import fakeredis
import pytest

from agents.galadriel.seed import seed_default_jobs
from agents.galadriel.store import list_jobs


@pytest.fixture
def redis():
    return fakeredis.FakeRedis(decode_responses=True)


def test_seed_creates_github_daily_audit_job(redis):
    seed_default_jobs(redis)
    jobs = list_jobs(redis)
    names = [j.name for j in jobs]
    assert "github-daily-audit" in names


def test_seed_is_idempotent(redis):
    seed_default_jobs(redis)
    seed_default_jobs(redis)
    jobs = [j for j in list_jobs(redis) if j.name == "github-daily-audit"]
    assert len(jobs) == 1


def test_seeded_job_has_cron_schedule(redis):
    seed_default_jobs(redis)
    job = next(j for j in list_jobs(redis) if j.name == "github-daily-audit")
    assert job.schedule.kind == "cron"
    assert job.schedule.expr == "0 8 * * *"
    assert job.schedule.tz == "America/Los_Angeles"


def test_seeded_job_message_targets_correct_username(redis):
    seed_default_jobs(redis)
    job = next(j for j in list_jobs(redis) if j.name == "github-daily-audit")
    assert "SolomonSmith-dev" in job.payload.message
    assert job.payload.kind == "agentTurn"
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/galadriel/test_seed.py -v
```

Expected: `ModuleNotFoundError: No module named 'agents.galadriel.seed'`

- [ ] **Step 3: Implement agents/galadriel/seed.py**

```python
from __future__ import annotations

import time
from uuid import uuid4

from agents.galadriel.models import Job, JobDelivery, JobPayload, JobSchedule
from agents.galadriel.scheduler import Schedule, next_run_ms
from agents.galadriel.store import list_jobs, save_job
from core.config import settings
from core.logging import get_logger

log = get_logger("agents.galadriel.seed")

_DEFAULT_JOBS: list[dict] = [
    {
        "name": "github-daily-audit",
        "schedule": JobSchedule(kind="cron", expr="0 8 * * *", tz="America/Los_Angeles"),
        "payload": JobPayload(
            kind="agentTurn",
            message="Run daily GitHub audit for SolomonSmith-dev",
            timeout_seconds=60,
        ),
    }
]


def seed_default_jobs(redis) -> None:
    """Create standing jobs if they do not already exist. Idempotent."""
    existing = {j.name for j in list_jobs(redis)}
    chat_ids = settings.telegram_allowed_chat_ids.strip()
    first_chat_id = chat_ids.split(",")[0].strip() if chat_ids else ""
    delivery_mode = "telegram" if first_chat_id else "announce"
    now_ms = int(time.time() * 1000)

    for spec in _DEFAULT_JOBS:
        if spec["name"] in existing:
            log.info("seed_skip_existing", name=spec["name"])
            continue

        sched = spec["schedule"]
        scheduler_sched = Schedule(kind=sched.kind, expr=sched.expr, tz=sched.tz)
        next_ms = next_run_ms(scheduler_sched)

        job = Job(
            id=str(uuid4()),
            name=spec["name"],
            schedule=sched,
            payload=spec["payload"],
            delivery=JobDelivery(mode=delivery_mode, to=first_chat_id or None),
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
            next_run_at_ms=next_ms,
        )
        save_job(redis, job)
        log.info("seed_job_created", name=spec["name"], next_run_at_ms=next_ms)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/galadriel/test_seed.py -v
```

Expected: all 4 tests `PASSED`.

- [ ] **Step 5: Wire seed into Galadriel worker startup**

In `agents/galadriel/worker.py`, find the `run_forever()` function. Add the seed call right after the log line:

```python
def run_forever() -> None:
    redis = get_redis_sync()
    base_url = settings.internal_api_url
    headers = {"x-api-key": settings.arda_api_key}

    log.info("galadriel_worker_starting", base_url=base_url)

    # Seed standing jobs (idempotent -- safe to call every restart).
    from agents.galadriel.seed import seed_default_jobs
    seed_default_jobs(redis)

    with httpx.Client(base_url=base_url, headers=headers) as client:
        ...  # rest of function unchanged
```

- [ ] **Step 6: Run full test suite**

```bash
uv run pytest --tb=short -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add agents/galadriel/seed.py agents/galadriel/worker.py tests/galadriel/
git commit -m "feat(galadriel): seed github-daily-audit cron job at worker startup"
```

---

### Task 7: End-to-end integration test

**Files:**
- Modify: `tests/integration/` (add new test file)

- [ ] **Step 1: Write the integration test**

Check what's in `tests/integration/`:
```bash
ls tests/integration/
```

Create `tests/integration/test_github_audit_flow.py`:

```python
"""End-to-end test: GitHub audit message flows through Sauron → Earendil → Finrod.

Everything is mocked (MockLLM, mock GitHub API, fakeredis, InMemoryStore).
No real network calls.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis
import pytest

from agents.earendil.agent import Earendil
from agents.finrod.agent import Finrod
from agents.sauron.agent import Sauron
from agents.sauron.planner import Specialist
from agents.tombombadil.agent import TomBombadil
from core.models import AgentTask, TaskStatus


@pytest.fixture(autouse=True)
def mock_github_api():
    from agents.earendil.tools.github import GitHubSnapshot

    async def mock_fetch(username, date_str, token):
        return GitHubSnapshot(
            date=date_str,
            username=username,
            commits=[],
            prs_opened=[],
            prs_merged=[],
            issues_opened=[],
            streak_days=5,
            total_contributions=0,
        )

    with patch("agents.earendil.agent.fetch_activity_async", side_effect=mock_fetch):
        yield


@pytest.mark.asyncio
async def test_github_audit_full_flow():
    """Galadriel-style trigger flows all the way to a narrative reply."""
    sauron = Sauron(
        specialists={
            "earendil": Earendil(),
            "finrod": Finrod(),
            "tombombadil": TomBombadil(),
        }
    )

    task = AgentTask(
        agent="sauron",
        type="execute",
        payload={"message": "Run daily GitHub audit for SolomonSmith-dev"},
    )
    result = await sauron.run(task)

    assert result.status == TaskStatus.COMPLETED
    assert "reply" in result.result
    assert "github_snapshot" in result.result


@pytest.mark.asyncio
async def test_github_audit_does_not_enqueue_redis_tasks():
    """Confirm the short-circuit: no tasks land in the Redis queue."""
    import fakeredis as fr
    from agents.earendil import agent as earendil_agent

    fake_r = fr.FakeRedis(decode_responses=True)
    with patch.object(earendil_agent, "get_redis_sync", return_value=fake_r):
        e = Earendil()
        task = AgentTask(
            agent="earendil",
            type="execute",
            payload={"message": "Run daily GitHub audit for SolomonSmith-dev"},
        )
        result = await e.run(task)

    from core.redis_client import TASK_QUEUE_KEY
    assert fake_r.llen(TASK_QUEUE_KEY) == 0
    assert result.result["github_snapshot"]["username"] == "SolomonSmith-dev"
```

- [ ] **Step 2: Run the integration tests**

```bash
uv run pytest tests/integration/test_github_audit_flow.py -v
```

Expected: both tests `PASSED`.

- [ ] **Step 3: Run the full suite one final time**

```bash
uv run pytest --tb=short -q
```

Expected: all tests pass, no regressions.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_github_audit_flow.py
git commit -m "test(integration): end-to-end GitHub audit flow -- Sauron → Earendil → Finrod"
```

---

### Task 8: Deploy to home server and verify

**Files:** None (operational only)

- [ ] **Step 1: Push branch and open PR**

```bash
# On MacBook:
git push origin HEAD
gh pr create --title "Phase 2: GitHub activity audit workflow" \
  --body "Earendil fetches GitHub API. Sauron generates Gemini narrative. Finrod stores snapshots. Galadriel seeded with daily 8am PT cron." \
  --base main
```

Wait for CI to pass, then merge:
```bash
gh pr checks --watch
gh pr merge --merge
```

- [ ] **Step 2: Pull on home server and rebuild**

```bash
# On home server:
cd /home/solomon/Code/arda-stack/arda
git pull origin main
docker compose build --no-cache api

# Default-profile services (no profile flag needed):
docker compose up -d --force-recreate api worker gwaihir

# Profile-gated services:
docker compose --profile discord up -d --force-recreate tommunbadil
docker compose --profile cron up -d --force-recreate galadriel
```

- [ ] **Step 3: Verify the seed job was created**

```bash
# On home server:
ARDA_API_KEY=$(grep ^ARDA_API_KEY .env | cut -d= -f2)
curl -s -H "x-api-key: $ARDA_API_KEY" http://localhost:5000/cron | python3 -m json.tool
```

Expected: JSON array containing a job named `github-daily-audit` with `schedule.expr: "0 8 * * *"`.

- [ ] **Step 4: Trigger a manual audit to test the full flow**

```bash
curl -s -X POST http://localhost:5000/execute/wait \
  -H "x-api-key: $ARDA_API_KEY" \
  -H "content-type: application/json" \
  -d '{"message": "Run daily GitHub audit for SolomonSmith-dev"}' \
  | python3 -m json.tool
```

Expected: response contains `"reply"` key with a prose narrative mentioning your recent commits. If the narrative is generic or wrong, check:
```bash
docker compose logs api --since 5m | grep github
```

- [ ] **Step 5: Send a Telegram pull query**

In Telegram, send the bot: `what did I work on this week?`

Expected: a reply with a summary of the stored GitHub snapshots (may be sparse if only one day is stored; check that it doesn't crash).

- [ ] **Step 6: Check Galadriel will fire at 8am PT**

```bash
curl -s -H "x-api-key: $ARDA_API_KEY" http://localhost:5000/cron | python3 -c "
import sys, json
from datetime import datetime, timezone
jobs = json.load(sys.stdin)
for j in jobs:
    if j['name'] == 'github-daily-audit':
        ms = j.get('next_run_at_ms')
        if ms:
            dt = datetime.fromtimestamp(ms/1000, tz=timezone.utc)
            print('Next run:', dt.isoformat())
"
```

Expected: a future timestamp around 08:00 America/Los_Angeles.
