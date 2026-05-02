from __future__ import annotations

from pathlib import Path

from agents.finrod.embeddings import MockEmbedder
from agents.finrod.ingest import chunk_text, ingest_directory, ingest_text
from agents.finrod.store import InMemoryStore


def test_chunk_text_short_returns_single():
    chunks = chunk_text("hello world")
    assert chunks == ["hello world"]


def test_chunk_text_empty_returns_empty():
    assert chunk_text("") == []


def test_chunk_text_long_splits_with_overlap():
    text = "x" * 1500
    chunks = chunk_text(text, size=512, overlap=64)
    assert len(chunks) >= 3
    assert all(len(c) <= 512 for c in chunks)


def test_ingest_text_inserts_and_returns_count():
    store = InMemoryStore()
    n = ingest_text(store, MockEmbedder(), "doc-1", "short body")
    assert n == 1
    assert store.count() == 1


def test_ingest_text_attaches_metadata():
    store = InMemoryStore()
    e = MockEmbedder()
    ingest_text(store, e, "doc-2", "metadata test", metadata={"author": "tolkien"})
    [hit] = store.search(e.embed(["metadata test"])[0], top_k=1)
    assert hit.metadata["doc_id"] == "doc-2"
    assert hit.metadata["author"] == "tolkien"


def test_ingest_directory_walks_glob(tmp_path: Path):
    (tmp_path / "a.md").write_text("alpha doc body", encoding="utf-8")
    (tmp_path / "b.md").write_text("beta doc body", encoding="utf-8")
    (tmp_path / "skip.txt").write_text("not markdown", encoding="utf-8")

    store = InMemoryStore()
    counts = ingest_directory(store, MockEmbedder(), tmp_path)

    assert "a.md" in counts
    assert "b.md" in counts
    assert "skip.txt" not in counts
    assert store.count() == counts["a.md"] + counts["b.md"]


def test_ingest_directory_handles_non_utf8(tmp_path: Path):
    (tmp_path / "bad.md").write_bytes(b"\x80\x81\x82\x83")
    (tmp_path / "good.md").write_text("readable", encoding="utf-8")

    store = InMemoryStore()
    counts = ingest_directory(store, MockEmbedder(), tmp_path)

    assert "bad.md" not in counts
    assert "good.md" in counts
