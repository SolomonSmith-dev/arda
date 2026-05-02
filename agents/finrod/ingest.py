from __future__ import annotations

import hashlib
from pathlib import Path

from agents.finrod.embeddings import Embedder
from agents.finrod.store import VectorStore

CHUNK_SIZE = 512
CHUNK_OVERLAP = 64


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    if not text:
        return []
    if len(text) <= size:
        return [text]

    chunks: list[str] = []
    step = max(1, size - overlap)
    for start in range(0, len(text), step):
        chunk = text[start : start + size]
        if not chunk:
            break
        chunks.append(chunk)
        if start + size >= len(text):
            break
    return chunks


def _chunk_id(doc_id: str, index: int, text: str) -> str:
    digest = hashlib.sha256(f"{doc_id}|{index}|{text}".encode()).hexdigest()[:16]
    return f"{doc_id}:{index}:{digest}"


def ingest_text(
    store: VectorStore,
    embedder: Embedder,
    doc_id: str,
    text: str,
    metadata: dict | None = None,
) -> int:
    chunks = chunk_text(text)
    if not chunks:
        return 0

    ids = [_chunk_id(doc_id, i, c) for i, c in enumerate(chunks)]
    vectors = embedder.embed(chunks)
    meta = [{"doc_id": doc_id, "chunk_index": i, **(metadata or {})} for i in range(len(chunks))]
    return store.add(ids, chunks, vectors, meta)


def ingest_directory(
    store: VectorStore,
    embedder: Embedder,
    path: Path,
    glob_pattern: str = "**/*.md",
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for file in sorted(path.glob(glob_pattern)):
        if not file.is_file():
            continue
        try:
            text = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        doc_id = str(file.relative_to(path))
        counts[doc_id] = ingest_text(store, embedder, doc_id, text, {"source": str(file)})
    return counts
