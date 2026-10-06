"""Rúmil wired end to end (#69, #70): Sauron tool, cron seed, Galadriel
dispatch, API registration. Mock-by-default, no keys, no network."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import fakeredis
import httpx
import pytest
from fastapi.testclient import TestClient
from llama_index.core.llms import MockLLM

from agents._anthropic_mock import MockAnthropicChatClient
from agents._llama_index_mock import HashEmbedding
from agents.earendil.agent import Earendil
from agents.finrod.agent import Finrod
from agents.galadriel.models import Job, JobDelivery, JobPayload, JobSchedule
from agents.galadriel.store import read_job, save_job
from agents.galadriel.worker import run_one
from agents.rumil.agent import Rumil
from agents.rumil.cron import GITHUB_AUDIT_JOB_ID, ensure_github_audit_cron
from agents.rumil.github import GitHubSnapshot
from agents.sauron.agent import Sauron
from core.config import settings
from core.models import AgentTask, TaskStatus

END = datetime(2026, 9, 26, 15, 0, tzinfo=UTC)


def _snap(username, token, **_kw) -> GitHubSnapshot:
    return GitHubSnapshot(
        username=username, window_start=END - timedelta(hours=24), window_end=END,
        commit_count=2, commits_by_repo={"SolomonSmith-dev/arda": 2},
        contributions_today=2, streak_days=1,
    )


def _rumil(finrod=None) -> Rumil:
    return Rumil(
        finrod=finrod, fetcher=_snap, llm=MockAnthropicChatClient(),
        username="SolomonSmith-dev", token="tok",
    )


@pytest.fixture
def r():
    return fakeredis.FakeRedis(decode_responses=True)


# ----- Sauron -----------------------------------------------------------


@pytest.mark.asyncio
async def test_sauron_routes_a_github_question_to_rumil_through_the_graph():
    finrod = Finrod(llm=MockLLM(max_tokens=32), embed_model=HashEmbedding())
    sauron = Sauron(specialists={"finrod": finrod, "rumil": _rumil(finrod)})
    result = await sauron.run(
        AgentTask(agent="sauron", type="execute", payload={"message": "how did my GitHub look today?"})
    )
    # The envelope is unchanged; Rúmil's output sits inside it.
    assert set(result.result) >= {"intent", "specialist", "specialist_result"}
    assert result.result["intent"] == "rumil"
    inner = result.result["specialist_result"]
    assert inner["status"] == TaskStatus.COMPLETED
    assert "2 commits across 1 repo" in inner["result"]["reply"]


@pytest.mark.asyncio
async def test_github_words_do_not_route_to_rumil_when_it_is_not_registered(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("agents.earendil.agent.get_redis_sync", lambda: fake)
    sauron = Sauron(specialists={"earendil": Earendil()})
    result = await sauron.run(
        AgentTask(agent="sauron", type="execute", payload={"message": "github audit please"})
    )
    assert result.result["intent"] == "earendil"


# ----- cron seed --------------------------------------------------------


def test_seed_installs_a_daily_8am_pacific_telegram_job(r, monkeypatch):
    monkeypatch.setattr(settings, "telegram_allowed_chat_ids", "111, 222")
    job = ensure_github_audit_cron(r)
    assert job.id == GITHUB_AUDIT_JOB_ID
    assert (job.schedule.expr, job.schedule.tz) == ("0 8 * * *", "America/Los_Angeles")
    assert (job.payload.kind, job.payload.text) == ("systemEvent", "github_audit")
    assert (job.delivery.mode, job.delivery.to) == ("telegram", "111")
    assert read_job(r, GITHUB_AUDIT_JOB_ID) is not None


def test_seed_is_idempotent_and_keeps_operator_edits(r, monkeypatch):
    monkeypatch.setattr(settings, "telegram_allowed_chat_ids", "111")
    job = ensure_github_audit_cron(r)
    save_job(r, job.model_copy(update={"enabled": False}))
    again = ensure_github_audit_cron(r)
    assert again.enabled is False
    assert len([k for k in r.scan_iter("cron:job:*")]) == 1


def test_seed_without_a_telegram_chat_delivers_nowhere(r, monkeypatch):
    monkeypatch.setattr(settings, "telegram_allowed_chat_ids", "")
    assert ensure_github_audit_cron(r).delivery.mode == "none"


# ----- Galadriel dispatch -------------------------------------------------


def _audit_job() -> Job:
    return Job(
        id=GITHUB_AUDIT_JOB_ID, name="GitHub daily audit",
        schedule=JobSchedule(kind="cron", expr="0 8 * * *", tz="America/Los_Angeles"),
        payload=JobPayload(kind="systemEvent", text="github_audit", timeout_seconds=60),
        delivery=JobDelivery(mode="telegram", to="111"),
        created_at_ms=0, updated_at_ms=0,
    )


def _api(status: str, **body) -> tuple[httpx.Client, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"status": status, **body})

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://api"), seen


def test_galadriel_runs_the_audit_via_the_api_and_sends_the_reply(r, monkeypatch):
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr("agents.gwaihir.notifier.send_message", lambda to, text: sent.append((to, text)))
    client, seen = _api("completed", result={"reply": "2 commits across 1 repo"})

    job = run_one(client, r, _audit_job())

    assert [req.url.path for req in seen] == ["/agents/rumil/run"]
    assert sent == [("111", "[GitHub daily audit] 2 commits across 1 repo")]
    assert job.last_status == "ok" and job.consecutive_errors == 0


def test_galadriel_records_a_failed_audit_and_says_so(r, monkeypatch):
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr("agents.gwaihir.notifier.send_message", lambda to, text: sent.append((to, text)))
    client, _ = _api("failed", error="GitHub rate limit hit (429)")

    job = run_one(client, r, _audit_job())

    assert job.last_status != "ok"
    assert job.consecutive_errors == 1
    assert job.last_error == "GitHub rate limit hit (429)"
    assert sent and "audit failed" in sent[0][1] and "429" in sent[0][1]


# ----- API --------------------------------------------------------------


def test_api_registers_rumil_and_seeds_its_cron(monkeypatch):
    from api import main as api_main

    seeded: list[str] = []
    monkeypatch.setattr(api_main, "redis_reachable", lambda: True)
    monkeypatch.setattr("agents.tombombadil.sync_job.ensure_letterboxd_sync_cron", lambda *_a, **_k: None)
    monkeypatch.setattr("agents.rumil.cron.ensure_github_audit_cron", lambda *_a, **_k: seeded.append("rumil"))
    monkeypatch.setattr(settings, "github_token", "")

    with TestClient(api_main.create_app()) as c:
        resp = c.post(
            "/agents/rumil/run",
            headers={"x-api-key": settings.arda_api_key},
            json={"payload": {"action": "audit"}},
        )
        health = c.get("/agents/health", headers={"x-api-key": settings.arda_api_key}).json()

    assert seeded == ["rumil"]
    body = resp.json()
    # No token on a test box: a clear failure, not a crash or a partial snapshot.
    assert resp.status_code == 200
    assert body["status"] == "failed"
    assert "GITHUB_TOKEN" in body["error"]
    assert "rumil" in {a["agent"] for a in health["agents"]}
