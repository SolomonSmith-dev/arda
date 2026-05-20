"""LlamaIndex embedding builder for Finrod.

Slim install (default) returns LlamaIndex's `MockEmbedding` — deterministic
dummy vectors, no model download. With `[full]` installed and
`mock_embedder_enabled=False`, returns `HuggingFaceEmbedding` wrapping the
local sentence-transformers `all-MiniLM-L6-v2` model (384-dim, the same
dim the prior implementation used).

The `EMBED_DIM` constant is kept stable so any downstream code that
asserts on it (or sizes a Milvus collection) stays correct.
"""

from __future__ import annotations

from typing import Any

EMBED_DIM = 384
DEFAULT_HF_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def build_embed_model() -> Any:
    from llama_index.core import MockEmbedding

    from core.config import settings

    if settings.mock_embedder_enabled:
        return MockEmbedding(embed_dim=EMBED_DIM)

    # Lazy import: the HuggingFace integration lives in `[full]` and
    # transitively imports torch + transformers. We only reach here when
    # mock embeddings are explicitly disabled.
    from llama_index.embeddings.huggingface import HuggingFaceEmbedding
    return HuggingFaceEmbedding(model_name=DEFAULT_HF_MODEL)
