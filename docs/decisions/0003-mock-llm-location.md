# ADR 0003: MockLLM as a Single Shared Helper

**Date:** 2026-04-30
**Status:** Accepted

## Context

Every Arda agent that calls an LLM (Sauron, Earendil, Finrod, Tom Bombadil) needs a stand-in when `settings.use_mock_llm == True`. Without one, dev-mode and CI runs would either require real API keys or have to monkeypatch the LLM at every call site. We need to choose between **one shared MockLLM** at `agents/_mock_llm.py` or **per-agent mocks** (`agents/sauron/_mock.py`, etc.).

## Decision

Single shared mock at `agents/_mock_llm.py`. Every agent imports the same class.

```python
# agents/_mock_llm.py
class MockLLM:
    """Drop-in replacement for langchain_groq.ChatGroq / ChatGoogleGenerativeAI in dev mode.

    Implements the LangChain Runnable interface (.invoke / .ainvoke).
    Returns a deterministic stub so tests are reproducible.
    """

    def __init__(self, model: str = "mock", **_kwargs):
        self.model = model

    def invoke(self, prompt, **_kwargs):
        text = prompt if isinstance(prompt, str) else str(prompt)
        return type("AIMessage", (), {"content": f"[mock:{self.model}] {text[:120]}"})()

    async def ainvoke(self, prompt, **_kwargs):
        return self.invoke(prompt)
```

Each agent picks between MockLLM and the real LLM at construction time:

```python
# inside agents/<name>/agent.py
from core.config import settings

if settings.use_mock_llm:
    from agents._mock_llm import MockLLM
    llm = MockLLM(model=settings.specialist_model)
else:
    from langchain_groq import ChatGroq
    llm = ChatGroq(model=settings.specialist_model, api_key=settings.groq_api_key)
```

## Alternatives considered

- **Per-agent mock files** (`agents/sauron/_mock.py`, `agents/tombombadil/_mock.py`, ...): rejected. At MVP scale all four agents have the same LLM contract — string in, content-bearing object out. Splitting would create four nearly-identical files. The premise for splitting (each agent needs a uniquely shaped fake) is a hypothetical future requirement; current code does not justify it.
- **Use `langchain_core.language_models.fake.FakeListLLM`** (LangChain's built-in fake): rejected for now. It requires preloading a list of canned responses, which is great for unit tests but awkward for the dev-mode loop where Solomon types arbitrary prompts. Our `MockLLM` echoes the prompt back — better dev ergonomics. We can still use `FakeListLLM` inside specific unit tests when we want exact output control.
- **Monkeypatch `ChatGroq` itself in `conftest.py`** with a fake: rejected. That works for tests but does nothing for `USE_MOCK_LLM=true` runtime mode, which is the primary use case (running the system without API keys). We need a runtime swap, not just a test swap.

## Consequences

- One file to maintain: `agents/_mock_llm.py`. ~20 LOC.
- Every agent has identical mock behavior. If/when Sauron needs JSON-shaped mock output and Tom Bombadil needs prose, that divergence is the trigger to split (not before).
- The mock implements the LangChain Runnable interface (`invoke`, `ainvoke`). It does *not* implement streaming or tool-calling — those are out of scope until an agent actually needs them.
- Tests use `MockLLM` directly when they want a generic fake; they reach for `FakeListLLM` when they need exact-output assertions.

## When to revisit

Split into per-agent mocks when **two or more agents** require different mock output shapes that can't share a single `if/elif` block in `agents/_mock_llm.py`. That is the unambiguous signal. One agent diverging = add a parameter. Two = split.
