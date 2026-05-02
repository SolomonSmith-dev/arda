"""End-to-end Sauron orchestration test.

Wires the real Sauron + Earendil + Finrod + TomBombadil agents (with
mock LLM, fakeredis, in-memory store) and asserts the full request ->
orchestrator -> specialist -> result flow.
"""

from __future__ import annotations

import fakeredis
import pytest

from agents.earendil import agent as earendil_module
from agents.earendil.agent import Earendil
from agents.finrod.agent import Finrod
from agents.finrod.embeddings import MockEmbedder
from agents.finrod.store import InMemoryStore
from agents.sauron.agent import Sauron
from agents.tombombadil import agent as tombombadil_module
from agents.tombombadil.agent import TomBombadil
from core.models import AgentTask, TaskStatus


@pytest.fixture
def fake_redis(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(earendil_module, "get_redis_sync", lambda: r)
    monkeypatch.setattr(tombombadil_module, "get_redis_sync", lambda: r)
    return r


@pytest.fixture
def sauron(fake_redis) -> Sauron:
    finrod = Finrod(store=InMemoryStore(), embedder=MockEmbedder())
    return Sauron(specialists={
        "earendil": Earendil(),
        "finrod": finrod,
        "tombombadil": TomBombadil(),
    })


@pytest.mark.asyncio
async def test_shell_message_routes_to_earendil_and_enqueues(sauron: Sauron, fake_redis):
    task = AgentTask(agent="sauron", type="execute", payload={"message": "uptime"})
    result = await sauron.run(task)

    assert result.status == TaskStatus.QUEUED
    assert result.result["intent"] == "earendil"
    assert result.result["specialist"] == "earendil"
    sub = result.result["specialist_result"]
    assert sub["agent"] == "earendil"
    assert len(sub["result"]["task_ids"]) == 1
    assert fake_redis.llen("task_queue") == 1


@pytest.mark.asyncio
async def test_knowledge_query_routes_to_finrod(sauron: Sauron):
    ingest_task = AgentTask(
        agent="finrod",
        type="ingest",
        payload={
            "action": "ingest",
            "doc_id": "arda-overview",
            "text": "ARDA is the unified multi-agent system.",
        },
    )
    await sauron.specialists["finrod"].run(ingest_task)

    task = AgentTask(
        agent="sauron",
        type="execute",
        payload={"message": "explain ARDA"},
    )
    result = await sauron.run(task)

    assert result.status == TaskStatus.COMPLETED
    assert result.result["intent"] == "finrod"
    sub = result.result["specialist_result"]
    assert sub["agent"] == "finrod"
    assert "answer" in sub["result"]
    assert len(sub["result"]["sources"]) >= 1


@pytest.mark.asyncio
async def test_film_message_routes_to_tombombadil(sauron: Sauron, fake_redis):
    task = AgentTask(
        agent="sauron",
        type="execute",
        payload={"message": "Name: Solomon\nFilm: Ran\nRating: 9"},
    )
    result = await sauron.run(task)

    assert result.status == TaskStatus.COMPLETED
    assert result.result["intent"] == "tombombadil"
    sub = result.result["specialist_result"]
    assert sub["agent"] == "tombombadil"
    assert "Ran" in sub["result"]["reply"]
    assert fake_redis.sismember("films", "Ran")


@pytest.mark.asyncio
async def test_chat_message_routes_to_tombombadil_via_llm(sauron: Sauron):
    task = AgentTask(
        agent="sauron",
        type="execute",
        payload={"message": "what about kurosawa"},
    )
    result = await sauron.run(task)

    assert result.status == TaskStatus.COMPLETED
    sub = result.result["specialist_result"]
    assert "[mock:" in sub["result"]["reply"]
