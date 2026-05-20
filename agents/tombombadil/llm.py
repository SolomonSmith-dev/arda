"""Anthropic client builder for Tom Bombadil (specialist-tier chat).

Returns the real `anthropic.AsyncAnthropic` in production, or the
`MockAnthropicChatClient` (deterministic, offline) when
`USE_MOCK_LLM=true` or no API key is set. Same shape as
`agents/sauron/llm.py` and `agents/finrod/llm.py` -- one seam per
agent, uniform across the system.

Tom only ever calls `await client.messages.create(...)` -- no tool_use,
no streaming -- so both the real and mock clients satisfy a tiny
duck-typed contract.
"""

from __future__ import annotations

from typing import Any


def build_chat_client() -> Any:
    from core.config import settings

    if settings.use_mock_llm or not settings.anthropic_api_key:
        from agents._anthropic_mock import MockAnthropicChatClient
        return MockAnthropicChatClient(model=settings.specialist_model)

    import anthropic
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
