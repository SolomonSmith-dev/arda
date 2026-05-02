# ADR 0004: Drop `tombombadil/memory.py` from Sub-Pass 1 Migration

**Date:** 2026-05-01
**Status:** Accepted
**Amends:** ADR 0002 (Phase 2 Sub-Pass 1)

## Context

ADR 0002 row 3 (Tom Bombadil mapping) said `memory.py` would be migrated with `google.generativeai` removed because the import was "type hint only per inventory." Re-reading the file during sub-pass 1 execution shows the inventory was wrong:

```python
# tombombadil/memory.py
from google import genai

class MemoryManager:
    def append(self, channel_id: int, role: str, text: str):
        history.append(
            genai.types.Content(role=role, parts=[genai.types.Part(text=text)])
        )
```

The Gemini SDK is **structurally** used — `MemoryManager` builds `genai.types.Content` objects as its data model, not just as a type hint. Stripping the import would break the class.

Also: `grep -rn "from memory\|import memory\|MemoryManager" tombombadil/` returns only the class definition itself. **No file in the legacy Tom Bombadil imports it.** It is dead code.

## Decision

Skip `memory.py` migration entirely. Do not port `agents/tombombadil/memory.py`.

If Tom Bombadil ever needs per-channel chat history, build it in sub-pass 2 (or later) on top of LangChain memory primitives (`langchain_core.chat_history.BaseChatMessageHistory`). Those work cleanly across the Groq + Gemini providers ARDA already depends on, and don't couple the agent to one SDK's data model.

## Alternatives considered

- **Port + rewrite to use plain dicts.** Rejected. The class is unused; rewriting dead code is wasted effort and creates a maintenance surface that nothing exercises. If we eventually need history, LangChain's primitives are better starting material than a hand-rolled deque.
- **Leave `memory.py` in `tombombadil/` legacy and pretend it doesn't exist.** That's already the outcome of skipping it — calling it out here so a future reader sees the deliberate non-migration in the ADR record.

## Consequences

- One fewer file in `agents/tombombadil/` than ADR 0002 mapped.
- ADR 0002 row 3 is amended: `memory.py` row should be read as "skip, see ADR 0004."
- When chat history work begins, the implementer reads this ADR + the LangChain memory docs, and does not look to the deleted file for guidance.

## Verification

`grep -rn "from memory\|import memory\|MemoryManager" agents/ tests/` returns empty after sub-pass 1 lands.
