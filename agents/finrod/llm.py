"""LlamaIndex LLM builder for Finrod (retriever-tier RAG synthesis).

Returns an `Anthropic` LLM (claude-haiku-4-5 by default) when running for
real, or LlamaIndex's `MockLLM` when `USE_MOCK_LLM=true` (the dev/test
default). Mirrors `agents/sauron/llm.py`'s shape so the seam is uniform
across agents.

The graph treats the returned object as a LlamaIndex `LLM` — only the
methods used by `as_query_engine(...)` (`acomplete`, `complete`, etc.)
are actually exercised.
"""

from __future__ import annotations

from typing import Any


def build_llm() -> Any:
    from llama_index.core.llms import MockLLM

    from core.config import settings

    if settings.use_mock_llm or not settings.anthropic_api_key:
        # MockLLM echoes a deterministic stub; `max_tokens` keeps the
        # echo short so tests assert on substrings cheaply.
        return MockLLM(max_tokens=128)

    from llama_index.llms.anthropic import Anthropic

    # No explicit temperature: LlamaIndex's own default (0.1) is already
    # tighter than the 0.2 that used to be set here, and passing it added
    # nothing.
    #
    # Note that removing it does NOT make this safe on anthropic 1.x.
    # Anthropic.__init__ declares `temperature: float = 0.1` and forwards it
    # unconditionally, so the caller cannot suppress it -- which is why
    # pyproject pins anthropic<1. See the comment there.
    return Anthropic(
        model=settings.retriever_model,
        api_key=settings.anthropic_api_key,
    )
