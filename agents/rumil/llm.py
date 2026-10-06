"""Anthropic client builder for Rúmil's narrative (specialist tier, Haiku).

Same seam as ``agents/tombombadil/llm.py``: the real ``AsyncAnthropic`` in
production, ``MockAnthropicChatClient`` when ``USE_MOCK_LLM=true`` or no key
is set. Rúmil only calls ``await client.messages.create(...)``.
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
