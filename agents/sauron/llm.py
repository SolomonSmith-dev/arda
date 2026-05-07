"""Anthropic client builder for Sauron.

Returns the real `anthropic.AsyncAnthropic` in production, or the
`MockAnthropicClient` (deterministic, offline) when `USE_MOCK_LLM=true`
or no API key is set. Provides a single seam tests can override by
passing `client=...` into `Sauron(...)`.

The graph treats the client as duck-typed: it only ever calls
`await client.messages.create(...)`. Both `AsyncAnthropic` and
`MockAnthropicClient` satisfy that shape.
"""

from __future__ import annotations

from typing import Any


def build_client() -> Any:
    from core.config import settings

    if settings.use_mock_llm or not settings.anthropic_api_key:
        from agents._anthropic_mock import MockAnthropicClient
        return MockAnthropicClient(model=settings.orchestrator_model)

    import anthropic
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
