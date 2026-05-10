from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Chunk:
    id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0


class VectorStore(Protocol):
    def add(
        self,
        ids: list[str],
        texts: list[str],
        vectors: list[list[float]],
        metadata: list[dict] | None = None,
    ) -> int: ...

    def search(self, query_vector: list[float], top_k: int = 5) -> list[Chunk]: ...

    def count(self) -> int: ...

    def delete_by_metadata(self, predicate: dict[str, Any]) -> int: ...


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


class InMemoryStore:
    """List-backed store with cosine similarity. Always available.

    Vectors are assumed L2-normalized (Embedder contract), so cosine
    reduces to a dot product.
    """

    def __init__(self):
        self._items: list[tuple[str, str, list[float], dict]] = []

    def add(
        self,
        ids: list[str],
        texts: list[str],
        vectors: list[list[float]],
        metadata: list[dict] | None = None,
    ) -> int:
        meta = metadata or [{} for _ in ids]
        for cid, text, vec, m in zip(ids, texts, vectors, meta, strict=True):
            self._items.append((cid, text, vec, m))
        return len(ids)

    def search(self, query_vector: list[float], top_k: int = 5) -> list[Chunk]:
        scored = [
            Chunk(id=cid, text=text, metadata=m, score=_cosine(query_vector, vec))
            for cid, text, vec, m in self._items
        ]
        scored.sort(key=lambda c: c.score, reverse=True)
        return scored[:top_k]

    def count(self) -> int:
        return len(self._items)

    def delete_by_metadata(self, predicate: dict[str, Any]) -> int:
        """Remove items whose metadata matches every key/value in
        ``predicate``. Returns the number deleted.
        """
        if not predicate:
            return 0
        before = len(self._items)
        self._items = [
            item for item in self._items
            if not all(item[3].get(k) == v for k, v in predicate.items())
        ]
        return before - len(self._items)


class MilvusStore:
    """pymilvus-backed store. Created from a connected MilvusClient.

    Use get_store() to construct -- it handles the unreachable-Milvus
    fallback to InMemoryStore.
    """

    def __init__(self, client: Any, collection: str = "finrod_docs", dim: int = 384):
        from core.milvus_client import ensure_collection

        self._client = client
        self.collection = collection
        self.dim = dim
        ensure_collection(client, collection, dim)

    def add(
        self,
        ids: list[str],
        texts: list[str],
        vectors: list[list[float]],
        metadata: list[dict] | None = None,
    ) -> int:
        from core.milvus_client import insert_embeddings

        meta = metadata or [{}] * len(ids)
        rows_meta = [{"text": t, **m} for t, m in zip(texts, meta, strict=True)]
        return insert_embeddings(self._client, self.collection, ids, vectors, rows_meta)

    def search(self, query_vector: list[float], top_k: int = 5) -> list[Chunk]:
        from core.milvus_client import search as milvus_search

        rows = milvus_search(self._client, self.collection, query_vector, top_k=top_k)
        out: list[Chunk] = []
        for row in rows:
            entity = row.get("entity", row)
            out.append(
                Chunk(
                    id=str(row.get("id", entity.get("id", ""))),
                    text=entity.get("text", ""),
                    metadata={k: v for k, v in entity.items() if k not in ("text", "id")},
                    score=float(row.get("distance", row.get("score", 0.0))),
                )
            )
        return out

    def count(self) -> int:
        try:
            stats = self._client.get_collection_stats(self.collection)
            return int(stats.get("row_count", 0))
        except Exception:
            return 0

    def delete_by_metadata(self, predicate: dict[str, Any]) -> int:
        """Translate the predicate into a Milvus boolean expression
        (``k == "v" && ...``) and dispatch through
        :func:`core.milvus_client.delete_by_expr`. Returns 0 when the
        predicate is empty (we never delete the whole collection).
        """
        from core.milvus_client import delete_by_expr

        if not predicate:
            return 0
        clauses: list[str] = []
        for k, v in predicate.items():
            if isinstance(v, str):
                escaped = v.replace('"', '\\"')
                clauses.append(f'{k} == "{escaped}"')
            elif isinstance(v, bool):
                clauses.append(f"{k} == {str(v).lower()}")
            elif isinstance(v, int | float):
                clauses.append(f"{k} == {v}")
            else:
                continue
        if not clauses:
            return 0
        expr = " && ".join(clauses)
        return delete_by_expr(self._client, self.collection, expr)


def get_store() -> VectorStore:
    """Try Milvus first; fall back to in-memory if unreachable."""
    from core.milvus_client import get_milvus_client

    client = get_milvus_client()
    if client is None:
        return InMemoryStore()
    return MilvusStore(client)
