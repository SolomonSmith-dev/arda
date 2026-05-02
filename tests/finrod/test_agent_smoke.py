from __future__ import annotations

import pytest

from agents.finrod.agent import Finrod
from agents.finrod.embeddings import MockEmbedder
from agents.finrod.store import InMemoryStore
from core.models import AgentTask, TaskStatus


@pytest.fixture
def finrod() -> Finrod:
    return Finrod(store=InMemoryStore(), embedder=MockEmbedder())


@pytest.mark.asyncio
async def test_ingest_then_query_returns_grounded_answer(finrod: Finrod):
    ingest = AgentTask(
        agent="finrod",
        type="ingest",
        payload={"action": "ingest", "doc_id": "arda-scope", "text": "ARDA is the unified multi-agent system."},
    )
    ingest_result = await finrod.run(ingest)
    assert ingest_result.status == TaskStatus.COMPLETED
    assert ingest_result.result["chunks_ingested"] >= 1

    query = AgentTask(
        agent="finrod",
        type="query",
        payload={"action": "query", "message": "ARDA is the unified multi-agent system."},
    )
    query_result = await finrod.run(query)
    assert query_result.status == TaskStatus.COMPLETED
    assert "answer" in query_result.result
    assert len(query_result.result["sources"]) >= 1
    assert query_result.result["sources"][0]["id"].startswith("arda-scope:")


@pytest.mark.asyncio
async def test_ingest_missing_fields_fails(finrod: Finrod):
    task = AgentTask(agent="finrod", type="ingest", payload={"action": "ingest"})
    result = await finrod.run(task)
    assert result.status == TaskStatus.FAILED
    assert "doc_id" in result.error


@pytest.mark.asyncio
async def test_query_missing_message_fails(finrod: Finrod):
    task = AgentTask(agent="finrod", type="query", payload={"action": "query"})
    result = await finrod.run(task)
    assert result.status == TaskStatus.FAILED
    assert "message" in result.error or "question" in result.error


@pytest.mark.asyncio
async def test_query_empty_store_returns_no_context(finrod: Finrod):
    task = AgentTask(
        agent="finrod",
        type="query",
        payload={"action": "query", "message": "anything"},
    )
    result = await finrod.run(task)
    assert result.status == TaskStatus.COMPLETED
    assert result.result["answer"] == "No relevant context found."
    assert result.result["sources"] == []


@pytest.mark.asyncio
async def test_stats_action_returns_count(finrod: Finrod):
    await finrod.run(
        AgentTask(
            agent="finrod",
            type="ingest",
            payload={"action": "ingest", "doc_id": "x", "text": "body"},
        )
    )
    stats = await finrod.run(AgentTask(agent="finrod", type="stats", payload={"action": "stats"}))
    assert stats.status == TaskStatus.COMPLETED
    assert stats.result["chunk_count"] >= 1


@pytest.mark.asyncio
async def test_unknown_action_fails(finrod: Finrod):
    task = AgentTask(agent="finrod", type="x", payload={"action": "bogus"})
    result = await finrod.run(task)
    assert result.status == TaskStatus.FAILED
    assert "unknown action" in result.error
