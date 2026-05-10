from __future__ import annotations

from functools import lru_cache
from typing import Any

import structlog

from core.config import settings

log = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_milvus_client() -> Any | None:
    """Return a pymilvus MilvusClient or None if Milvus is unreachable.

    Callers should check for None and fall back to an in-memory store.
    Catches every exception class because pymilvus pulls numpy + pandas,
    which can fail at import time on older CPUs (e.g. NumPy 2.x dropping
    support for hosts without the X86_V2 baseline).
    """
    try:
        from pymilvus import MilvusClient
    except Exception as e:
        log.warning("pymilvus_unavailable", error=str(e))
        return None

    uri = f"http://{settings.milvus_host}:{settings.milvus_port}"
    try:
        client = MilvusClient(uri=uri)
        client.list_collections()
        return client
    except Exception as e:
        log.warning("milvus_unreachable", uri=uri, error=str(e))
        return None


def ensure_collection(client: Any, name: str, dim: int) -> bool:
    if client is None:
        return False
    if client.has_collection(name):
        return True
    client.create_collection(collection_name=name, dimension=dim, auto_id=False)
    return True


def insert_embeddings(
    client: Any,
    collection: str,
    ids: list[str],
    vectors: list[list[float]],
    metadata: list[dict] | None = None,
) -> int:
    if client is None:
        return 0
    rows = []
    for i, (rid, vec) in enumerate(zip(ids, vectors, strict=True)):
        row = {"id": rid, "vector": vec}
        if metadata and i < len(metadata):
            row.update(metadata[i])
        rows.append(row)
    result = client.insert(collection_name=collection, data=rows)
    return int(result.get("insert_count", 0))


def search(
    client: Any,
    collection: str,
    query_vector: list[float],
    top_k: int = 5,
    output_fields: list[str] | None = None,
) -> list[dict]:
    if client is None:
        return []
    results = client.search(
        collection_name=collection,
        data=[query_vector],
        limit=top_k,
        output_fields=output_fields or ["*"],
    )
    if not results:
        return []
    return list(results[0])


def delete_by_expr(client: Any, collection: str, expr: str) -> int:
    """Delete rows from ``collection`` matching the Milvus boolean
    expression ``expr`` (e.g. ``viewer == "Solomon Smith" && kind == "tom_fact"``).

    Returns the number of rows removed. Logs and returns 0 on error so
    callers stay non-fatal -- ``/forget`` tolerates a partial sweep.
    """
    if client is None or not expr:
        return 0
    try:
        result = client.delete(collection_name=collection, filter=expr)
        return int(result.get("delete_count", 0))
    except Exception as e:
        log.warning("milvus_delete_failed", collection=collection, expr=expr, error=str(e))
        return 0
