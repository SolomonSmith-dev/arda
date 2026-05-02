"""Shared mock LLM for dev mode and tests. See ADR 0003."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class _MockResponse:
    content: str


class MockLLM:
    """Drop-in replacement for ChatGroq / ChatGoogleGenerativeAI.

    Implements the LangChain Runnable surface used by Arda agents:
    `.invoke(prompt) -> obj.content` and `.ainvoke(prompt)`.
    Returns a deterministic stub so tests are reproducible.
    """

    def __init__(self, model: str = "mock", **_kwargs):
        self.model = model

    def _render(self, prompt) -> str:
        text = prompt if isinstance(prompt, str) else str(prompt)
        return f"[mock:{self.model}] {text[:120]}"

    def invoke(self, prompt, **_kwargs) -> _MockResponse:
        return _MockResponse(content=self._render(prompt))

    async def ainvoke(self, prompt, **_kwargs) -> _MockResponse:
        return self.invoke(prompt)
