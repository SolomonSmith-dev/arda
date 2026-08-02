# 0006 — Anthropic + LangGraph is the canonical LLM stack

- **Status:** Accepted
- **Date:** 2026-08-02
- **Supersedes:** [0003-mock-llm-location.md](0003-mock-llm-location.md) (mock location + LangChain providers)
- **Related:** [0004-tombombadil-memory-deferred.md](0004-tombombadil-memory-deferred.md) (deferred Gemini memory; later rebuilt on Redis + Finrod)

## Context

The original scope (`ARDA_SCOPE.md`) and early ADRs prescribed a Gemini
(orchestrator) + Groq (executor/specialist) stack on LangChain, with
mocks living at `agents/_mock_llm.py` and the MCP package at `mcp/`.

That plan was replaced in-tree by the Anthropic + LangGraph pivot
(PRs #7, #33, #35, #38, #39, #40). Operator docs and live-smoke tests
still referenced Groq/Gemini after the code moved on.

Separately, issue #30 treated `mcp/server.py` vs `mcp_server/server.py`
as a blocked migration step. The package has been `mcp_server/` since
the unified API landed; there is no remaining rename.

## Decision

1. **Providers.** Anthropic is the only live LLM provider. Sauron uses
   Claude Opus via LangGraph tool_use; Finrod and Tom Bombadil use
   Claude Haiku. Earendil stays keyword/regex (no LLM).
2. **Mocks.** Runtime mocks live at `agents/_anthropic_mock.py` (and
   LlamaIndex `MockLLM` / `MockEmbedding` for Finrod). There is no
   `agents/_mock_llm.py`.
3. **MCP path.** Canonical package is `mcp_server/` (`python -m
   mcp_server.server`). Checklists that say `mcp/server.py` are wrong.
4. **Scope doc.** `ARDA_SCOPE.md` is historical intent, not the source
   of truth. README + CLAUDE.md + ADRs win.

## Consequences

- Cutover / migration / Tom docs and `tests/test_live_smoke.py` gate on
  `ANTHROPIC_API_KEY`, not Gemini/Groq keys.
- Issue #30 can be closed as a checklist naming error.
- ADR 0003 remains in the log for history but is no longer authoritative
  for mock placement or LangChain providers.
