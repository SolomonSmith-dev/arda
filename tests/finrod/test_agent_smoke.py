"""Behavior tests for the LlamaIndex-backed Finrod agent.

These tests assert on the AgentTask/AgentResult contract (ingest, query,
stats, forget) without touching real embedding models or LLM APIs --
LlamaIndex's `MockLLM` + `MockEmbedding` keep the suite fast and offline.
"""

from __future__ import annotations

import pytest
from llama_index.core import MockEmbedding
from llama_index.core.llms import MockLLM

from agents.finrod.agent import Finrod
from agents.finrod.embeddings import EMBED_DIM
from core.models import AgentTask, TaskStatus


@pytest.fixture
def finrod() -> Finrod:
    return Finrod(
        llm=MockLLM(max_tokens=64),
        embed_model=MockEmbedding(embed_dim=EMBED_DIM),
    )


@pytest.mark.asyncio
async def test_ingest_then_query_returns_grounded_answer(finrod: Finrod):
    ingest = AgentTask(
        agent="finrod",
        type="ingest",
        payload={
            "action": "ingest",
            "doc_id": "arda-scope",
            "text": "ARDA is the unified multi-agent system.",
        },
    )
    ingest_result = await finrod.run(ingest)
    assert ingest_result.status == TaskStatus.COMPLETED
    assert ingest_result.result["chunks_ingested"] >= 1
    assert ingest_result.result["doc_id"] == "arda-scope"

    query = AgentTask(
        agent="finrod",
        type="query",
        payload={"action": "query", "message": "ARDA is the unified multi-agent system."},
    )
    query_result = await finrod.run(query)
    assert query_result.status == TaskStatus.COMPLETED
    assert "answer" in query_result.result
    assert len(query_result.result["sources"]) >= 1
    source = query_result.result["sources"][0]
    assert source["id"]
    assert source["text"]
    assert isinstance(source["score"], float)
    assert source["metadata"]["doc_id"] == "arda-scope"


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


@pytest.mark.asyncio
async def test_forget_removes_nodes_matching_predicate(finrod: Finrod):
    """Predicate-based deletion: TomBombadil's `memory.forget_facts`
    depends on this contract for per-viewer cleanup."""
    await finrod.run(
        AgentTask(
            agent="finrod",
            type="ingest",
            payload={
                "action": "ingest",
                "doc_id": "fact-solomon",
                "text": "Solomon liked Stalker.",
                "metadata": {"kind": "tom_fact", "viewer": "Solomon"},
            },
        )
    )
    await finrod.run(
        AgentTask(
            agent="finrod",
            type="ingest",
            payload={
                "action": "ingest",
                "doc_id": "fact-brian",
                "text": "Brian liked Solaris.",
                "metadata": {"kind": "tom_fact", "viewer": "Brian"},
            },
        )
    )
    assert (await finrod.run(
        AgentTask(agent="finrod", type="stats", payload={"action": "stats"})
    )).result["chunk_count"] == 2

    deleted = await finrod.forget({"kind": "tom_fact", "viewer": "Solomon"})
    assert deleted == 1

    remaining = (await finrod.run(
        AgentTask(agent="finrod", type="stats", payload={"action": "stats"})
    )).result["chunk_count"]
    assert remaining == 1


@pytest.mark.asyncio
async def test_forget_empty_predicate_is_noop(finrod: Finrod):
    await finrod.run(
        AgentTask(
            agent="finrod",
            type="ingest",
            payload={"action": "ingest", "doc_id": "x", "text": "body"},
        )
    )
    deleted = await finrod.forget({})
    assert deleted == 0
    remaining = (await finrod.run(
        AgentTask(agent="finrod", type="stats", payload={"action": "stats"})
    )).result["chunk_count"]
    assert remaining == 1
