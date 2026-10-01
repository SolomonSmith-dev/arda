"""Finrod survives a process restart when given a persist_dir.

Without this, Finrod's default SimpleVectorStore lived only in memory, and
the deploy host cannot run Milvus (#22): every API restart wiped the index,
including Tom Bombadil's long-term facts about club members.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from llama_index.core.llms import MockLLM

from agents._llama_index_mock import HashEmbedding
from agents.finrod import store
from agents.finrod.agent import Finrod
from core.config import settings
from core.models import AgentTask, TaskStatus


def _finrod(persist_dir: Path | None) -> Finrod:
    return Finrod(llm=MockLLM(max_tokens=32), embed_model=HashEmbedding(), persist_dir=persist_dir)


def _ingest(doc_id: str, text: str, **metadata) -> AgentTask:
    return AgentTask(
        agent="finrod",
        type="ingest",
        payload={"action": "ingest", "doc_id": doc_id, "text": text, "metadata": metadata},
    )


@pytest.mark.asyncio
async def test_ingested_documents_survive_a_restart(tmp_path):
    first = _finrod(tmp_path)
    result = await first.run(_ingest("snap-1", "Merged four pull requests into arda."))
    assert result.status == TaskStatus.COMPLETED

    second = _finrod(tmp_path)
    assert second.node_count() == first.node_count() >= 1
    answer = await second.run(
        AgentTask(agent="finrod", type="query", payload={"question": "pull requests into arda"})
    )
    assert answer.status == TaskStatus.COMPLETED
    assert "snap-1" in {s["metadata"]["doc_id"] for s in answer.result["sources"]}


@pytest.mark.asyncio
async def test_forget_is_persisted_too(tmp_path):
    first = _finrod(tmp_path)
    await first.run(_ingest("fact-a", "Brian loves Kurosawa.", viewer="Brian"))
    await first.run(_ingest("fact-b", "Gavin hates musicals.", viewer="Gavin"))
    assert await first.forget({"viewer": "Brian"}) >= 1

    second = _finrod(tmp_path)
    viewers = {n.metadata.get("viewer") for n in second._index.docstore.docs.values()}
    assert viewers == {"Gavin"}


@pytest.mark.asyncio
async def test_no_persist_dir_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    await _finrod(None).run(_ingest("x", "ephemeral"))
    assert list(tmp_path.iterdir()) == []


def test_default_persist_dir_is_off_on_the_mock_path(monkeypatch):
    """Mirrors the checkpointer: dev/test stays file-free."""
    monkeypatch.setattr(settings, "use_mock_llm", True)
    assert store.default_persist_dir() is None


def test_default_persist_dir_is_namespaced_by_embedder(monkeypatch):
    """Vectors from the mock embedder are meaningless to a real one, so
    flipping USE_MOCK_EMBEDDER (#85) must not load the old index."""
    monkeypatch.setattr(settings, "use_mock_llm", False)
    monkeypatch.setattr(settings, "finrod_persist_dir", "/data/finrod")
    monkeypatch.setattr(settings, "use_mock_embedder", True)
    mock_dir = store.default_persist_dir()
    monkeypatch.setattr(settings, "use_mock_embedder", False)
    real_dir = store.default_persist_dir()
    assert mock_dir is not None and real_dir is not None
    assert mock_dir.parent == real_dir.parent == Path("/data/finrod")
    assert mock_dir != real_dir
