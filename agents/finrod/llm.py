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

    # No temperature. LlamaIndex does NOT translate this away -- it forwards
    # it verbatim to AsyncMessages.create, which the anthropic SDK 1.x
    # rejects. In production that surfaced as every /memory/query returning
    # "Empty Response" while retrieval worked fine, because LlamaIndex logs
    # the TypeError as a "Structured response error" and returns an empty
    # synthesis rather than raising. Same root cause as the Tom outage (#83).
    return Anthropic(
        model=settings.retriever_model,
        api_key=settings.anthropic_api_key,
    )
