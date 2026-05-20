"""LlamaIndex vector store factory for Finrod.

Returns a `BasePydanticVectorStore` instance: the in-memory
`SimpleVectorStore` by default (slim install, dev/test), or a
`MilvusVectorStore` when `[full]` is installed and a Milvus endpoint
is reachable. Falls back to `SimpleVectorStore` if Milvus is configured
but unreachable, mirroring the prior store factory's behavior.

This module deliberately exposes nothing else -- there is no longer an
ad-hoc Chunk dataclass or VectorStore Protocol. Consumers that need to
reach into the store (e.g. for metadata-filtered deletion) should go
through `Finrod.forget(...)` instead.
"""

from __future__ import annotations

from typing import Any

from agents.finrod.embeddings import EMBED_DIM
from core.logging import get_logger

log = get_logger("agents.finrod.store")

MILVUS_COLLECTION = "finrod_docs"


def build_vector_store() -> Any:
    from llama_index.core.vector_stores import SimpleVectorStore

    try:
        from core.milvus_client import get_milvus_client
    except Exception:
        return SimpleVectorStore()

    client = get_milvus_client()
    if client is None:
        return SimpleVectorStore()

    try:
        from llama_index.vector_stores.milvus import MilvusVectorStore
    except ImportError:
        log.info("milvus_integration_unavailable", note="llama-index-vector-stores-milvus not installed; falling back to in-memory")
        return SimpleVectorStore()

    from core.config import settings

    uri = f"http://{settings.milvus_host}:{settings.milvus_port}"
    try:
        return MilvusVectorStore(
            uri=uri,
            collection_name=MILVUS_COLLECTION,
            dim=EMBED_DIM,
            similarity_metric="COSINE",
        )
    except Exception as e:
        log.warning("milvus_store_init_failed", exception=str(e))
        return SimpleVectorStore()
