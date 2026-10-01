"""Rúmil agent (#68, #69): snapshot -> narrative -> Finrod -> reply."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from llama_index.core.llms import MockLLM

from agents._anthropic_mock import MockAnthropicChatClient
from agents._llama_index_mock import HashEmbedding
from agents.finrod.agent import Finrod
from agents.rumil.agent import Rumil
from agents.rumil.github import GitHubRateLimitError, GitHubSnapshot, PullRequest
from agents.rumil.report import MAX_MESSAGE_CHARS, format_stats
from core.config import settings
from core.models import AgentTask, TaskStatus

END = datetime(2026, 9, 26, 15, 0, tzinfo=UTC)


def _snapshot(**overrides) -> GitHubSnapshot:
    base = dict(
        username="SolomonSmith-dev",
        window_start=END - timedelta(hours=24),
        window_end=END,
        commit_count=4,
        commits_by_repo={"SolomonSmith-dev/arda": 3, "SolomonSmith-dev/ai-memory": 1},
        prs_opened=[PullRequest("SolomonSmith-dev/arda", 91, "five open bugs", "u")],
        prs_merged=[PullRequest("SolomonSmith-dev/arda", 88, "pin anthropic", "u")],
        contributions_today=5,
        streak_days=3,
    )
    base.update(overrides)
    return GitHubSnapshot(**base)


def _quiet() -> GitHubSnapshot:
    return _snapshot(
        commit_count=0, commits_by_repo={}, prs_opened=[], prs_merged=[],
        contributions_today=0, streak_days=0,
    )


class _Fetcher:
    def __init__(self, result):
        self.result = result
        self.calls: list[dict] = []

    def __call__(self, username, token, **kwargs):
        self.calls.append({"username": username, "token": token, **kwargs})
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _ExplodingLLM:
    class messages:  # noqa: N801 - mirrors the SDK attribute
        @staticmethod
        async def create(**_kwargs):
            raise RuntimeError("anthropic 529 overloaded")


@pytest.fixture
def finrod() -> Finrod:
    return Finrod(llm=MockLLM(max_tokens=32), embed_model=HashEmbedding())


def _task(**payload) -> AgentTask:
    return AgentTask(agent="rumil", type="github_audit", payload={"action": "audit", **payload})


def _rumil(finrod, fetcher, llm=None) -> Rumil:
    return Rumil(
        finrod=finrod,
        fetcher=fetcher,
        llm=llm or MockAnthropicChatClient(model="haiku"),
        username="SolomonSmith-dev",
        token="tok",
    )


@pytest.mark.asyncio
async def test_audit_returns_narrative_and_stats_and_stores_in_finrod(finrod):
    llm = MockAnthropicChatClient(model="haiku")
    result = await _rumil(finrod, _Fetcher(_snapshot()), llm).run(_task())

    assert result.status == TaskStatus.COMPLETED
    body = result.result
    assert body["snapshot"]["commit_count"] == 4
    assert body["narrative"].startswith(f"[mock:{settings.specialist_model}]")
    assert "4 commits across 2 repos" in body["reply"]
    assert "arda#91" in body["reply"]
    assert body["doc_id"] == "github-audit:SolomonSmith-dev:2026-09-26"
    # The narrative prompt carries the facts, and the model is the cheap tier.
    call = llm.calls[-1]
    assert call["model"] == settings.specialist_model
    assert "4 commits" in call["messages"][-1]["content"]
    stored = {n.metadata.get("doc_id") for n in finrod._index.docstore.docs.values()}
    assert stored == {"github-audit:SolomonSmith-dev:2026-09-26"}


@pytest.mark.asyncio
async def test_snapshot_is_recallable_from_finrod_afterwards(finrod):
    await _rumil(finrod, _Fetcher(_snapshot())).run(_task())
    answer = await finrod.run(
        AgentTask(agent="finrod", type="query", payload={"question": "what did I ship in arda"})
    )
    doc_ids = {s["metadata"]["doc_id"] for s in answer.result["sources"]}
    assert "github-audit:SolomonSmith-dev:2026-09-26" in doc_ids


@pytest.mark.asyncio
async def test_rerunning_the_same_day_replaces_the_snapshot(finrod):
    rumil = _rumil(finrod, _Fetcher(_snapshot()))
    await rumil.run(_task())
    before = finrod.node_count()
    await rumil.run(_task())
    assert finrod.node_count() == before


@pytest.mark.asyncio
async def test_quiet_day_skips_the_llm(finrod):
    llm = MockAnthropicChatClient(model="haiku")
    result = await _rumil(finrod, _Fetcher(_quiet()), llm).run(_task())
    assert result.status == TaskStatus.COMPLETED
    assert llm.calls == []
    assert "No GitHub activity" in result.result["reply"]


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_stats_only(finrod):
    result = await _rumil(finrod, _Fetcher(_snapshot()), _ExplodingLLM()).run(_task())
    assert result.status == TaskStatus.COMPLETED
    assert result.result["narrative"] == ""
    assert "4 commits" in result.result["reply"]


@pytest.mark.asyncio
async def test_fetch_failure_is_a_failed_result_with_a_readable_error(finrod):
    fetcher = _Fetcher(GitHubRateLimitError("GitHub rate limit hit (429)"))
    result = await _rumil(finrod, fetcher).run(_task())
    assert result.status == TaskStatus.FAILED
    assert result.error == "GitHub rate limit hit (429)"
    assert finrod.node_count() == 0


@pytest.mark.asyncio
async def test_window_hours_is_forwarded_and_clamped(finrod):
    fetcher = _Fetcher(_snapshot())
    rumil = _rumil(finrod, fetcher)
    await rumil.run(_task(window_hours=72))
    await rumil.run(_task(window_hours=100000))
    await rumil.run(_task(window_hours="nonsense"))
    assert [c["window_hours"] for c in fetcher.calls] == [72, 24 * 30, 24]


@pytest.mark.asyncio
async def test_works_without_finrod(finrod):
    rumil = Rumil(
        finrod=None, fetcher=_Fetcher(_snapshot()), llm=MockAnthropicChatClient(),
        username="u", token="t",
    )
    result = await rumil.run(_task())
    assert result.status == TaskStatus.COMPLETED
    assert result.result["doc_id"] is None


@pytest.mark.asyncio
async def test_unknown_action_fails(finrod):
    result = await _rumil(finrod, _Fetcher(_snapshot())).run(_task(action="explode"))
    assert result.status == TaskStatus.FAILED


def test_stats_are_capped_for_telegram():
    many = [PullRequest("o/r", i, "x" * 200, "u") for i in range(200)]
    text = format_stats(_snapshot(prs_opened=many, prs_merged=many))
    assert len(text) <= MAX_MESSAGE_CHARS
    assert "more" in text
