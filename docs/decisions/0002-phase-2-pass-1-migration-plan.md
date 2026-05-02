# ADR 0002: Phase 2 Sub-Pass 1 — Mechanical Migration of Earendil and Tom Bombadil

**Date:** 2026-04-30
**Status:** Accepted (Solomon approved 2026-04-30)

## Context

Phase 2 of the ARDA scope is the largest phase: build/migrate four agents. To keep risk low, this is split into two sub-passes:

- **Sub-pass 1 (this ADR):** Mechanical migration only. Move existing `earendil/` and `tombombadil/` code under `agents/`, swap LLM imports, replace dual logging implementations with `core.logging`, and update Redis imports to `core.redis_client`. **No new logic.** If the move + swap is correct, the migrated agents should pass their existing tests.
- **Sub-pass 2 (next ADR, after sub-pass 1 lands):** New builds — Sauron orchestrator and Finrod retriever. These are greenfield because they did not exist before.

Inventory (from Explore agent survey):

| Source | Files | LOC |
|---|---|---|
| `earendil/` | `earendil_api.py` (473), `worker.py` (124), `context_trimmer.py` (121), `tests/` (4 files, ~700) | ~1500 |
| `tombombadil/` | `agent.py` (161), `bot.py` (57), `film_parser.py` (108), `persistent_memory.py` (122), `tmdb_client.py` (101), `film_knowledge.py` (215), `auto_parser.py` (131), `memory.py` (25), `logger.py` (87), `structured_logging.py` (54), 3 test files (~445) | ~1500 |

## Decision

### Mapping

| Source path | Destination path | Action |
|---|---|---|
| `earendil/earendil_api.py` | **split** → `agents/earendil/agent.py` (executor logic) + temporary `legacy_api/earendil_api.py` (preserved verbatim) | Split. The agent half becomes `agent.py`. The FastAPI half is preserved untouched until Phase 3 rewrites the API layer. |
| `earendil/worker.py` | `agents/earendil/worker.py` | Move. Update Redis import to `from core.redis_client import get_redis_sync, TASK_QUEUE_KEY, task_result_key`. No logic change. |
| `earendil/context_trimmer.py` | `agents/earendil/context_trimmer.py` | Move unchanged (stdlib only). |
| `earendil/tests/` (4 files) | `tests/earendil/` | Move. Update imports from `earendil.X` → `agents.earendil.X`. |
| `earendil/memory/` | **leave in place** for now | These markdown docs are Finrod's seed corpus. `scripts/ingest.py` will read from `earendil/memory/` on first ingest. Move physically to `data/seed-corpus/` only when convenient. |
| `earendil/AGENTS.md`, `IDENTITY.md`, etc. | **leave in place** | Same — seed corpus candidates. |
| `tombombadil/bot.py` | `agents/tombombadil/bot.py` | Move. Update `from agent import` → `from agents.tombombadil.agent import`. |
| `tombombadil/agent.py` | `agents/tombombadil/agent.py` | Move + **swap LLM**. Replace `import google.generativeai as genai; model = genai.GenerativeModel(...)` with `from langchain_groq import ChatGroq; llm = ChatGroq(model=settings.specialist_model, api_key=settings.groq_api_key)`. Swap `model.generate_content(text).text` for `llm.invoke(text).content`. |
| `tombombadil/memory.py` | `agents/tombombadil/memory.py` | Remove `google.generativeai` import (used only for type hint in current code per inventory). Otherwise keep. |
| `tombombadil/film_parser.py` | `agents/tombombadil/film_parser.py` | Move. Replace `from structured_logging import StructuredLogger` with `from core.logging import get_logger`. |
| `tombombadil/persistent_memory.py` | `agents/tombombadil/persistent_memory.py` | Move. Replace `import redis; r = redis.Redis(...)` with `from core.redis_client import get_redis_sync`. Replace `from structured_logging` with `from core.logging`. |
| `tombombadil/film_knowledge.py` | `agents/tombombadil/film_knowledge.py` | Move. Update Redis import. |
| `tombombadil/auto_parser.py` | `agents/tombombadil/auto_parser.py` | Move. Update Redis import. |
| `tombombadil/tmdb_client.py` | `agents/tombombadil/tmdb_client.py` | Move unchanged. |
| `tombombadil/logger.py` + `structured_logging.py` | **delete** | Replaced by `core.logging`. Anything that imported either now imports `from core.logging import get_logger`. |
| `tombombadil/test_*.py` (3 files at top level) | `tests/tombombadil/test_*.py` | Move + update imports. |
| `tombombadil/requirements.txt` | **delete** | Replaced by unified `pyproject.toml`. Verify no missing deps before deleting. |
| `tombombadil/film-notes/` | leave in place | Discord output / production data, not source code. |

### LLM swap details (Tom Bombadil)

This is the only non-mechanical edit in sub-pass 1. The contract:

```python
# BEFORE (tombombadil/agent.py)
import google.generativeai as genai
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-1.5-flash")
response = model.generate_content(prompt)
text = response.text

# AFTER (agents/tombombadil/agent.py)
from langchain_groq import ChatGroq
from core.config import settings

llm = ChatGroq(
    model=settings.specialist_model,
    api_key=settings.groq_api_key,
    temperature=0.7,
)
response = llm.invoke(prompt)
text = response.content
```

If `settings.use_mock_llm` is True, swap to a fake that returns a deterministic stub. We will write a tiny `agents/_mock_llm.py` helper:

```python
class MockLLM:
    def invoke(self, prompt: str | list):
        text = prompt if isinstance(prompt, str) else str(prompt)
        return type("Response", (), {"content": f"[mock] {text[:80]}"})()
```

`agent.py` chooses between `ChatGroq` and `MockLLM` based on `settings.use_mock_llm` at import time.

### Tests

Each migrated module needs at least one passing test in `tests/<agent>/`. For sub-pass 1 the test bar is:

- **earendil**: `tests/earendil/test_worker_smoke.py` — instantiate worker, push a task to a fake Redis (via `fakeredis`), assert worker drains it and writes a result.
- **tombombadil**: `tests/tombombadil/test_film_parser.py` — port the existing parser tests (largest, most isolated). Skip Discord and Redis integration tests for now; they need fixtures we don't have until Phase 4.

Both tests must pass with `USE_MOCK_LLM=true` and no real Redis (use `fakeredis`).

### What happens to `legacy_api/earendil_api.py`?

It stays as a reference until Phase 3 writes the new `api/main.py`. Then it's deleted. Keeping it untouched in sub-pass 1 means we don't break the only working HTTP entrypoint while we're refactoring around it — Solomon can still curl into it locally during the transition.

## Alternatives considered

- **Big-bang rewrite of `earendil_api.py` in sub-pass 1.** Rejected: pulls Phase 3 work forward and conflates "move code" with "redesign API." If the worker swap breaks anything, we want to debug *one* change, not two.
- **Delete `tombombadil/film-notes/`.** Rejected: production data, not source. Out of scope.
- **Keep `structured_logging.py` and `logger.py` as a thin shim around `core.logging`.** Rejected: shims rot. The whole point of unifying is one logger.
- **Move `earendil/memory/` markdown into the new repo immediately.** Rejected: ~30 files, plus subdirs (`archive/`, `audits/`, `claude-projects/`, etc.). The migration plan stays focused on code; corpus moves are Finrod's problem in sub-pass 2.

## Consequences

- After sub-pass 1: `agents/earendil/`, `agents/tombombadil/` exist and are import-clean. `tests/earendil/`, `tests/tombombadil/` pass. Old `earendil/` and `tombombadil/` directories at the repo root **still exist** but are now legacy reference material. They get deleted at the end of sub-pass 2 once everything points at the new paths.
- One new file: `agents/_mock_llm.py`.
- One temp directory: `legacy_api/` (to be deleted in Phase 3).
- Dependency on `fakeredis` (add to dev deps).
- ADR 0003 will be the LLM-swap-strategy decision (where MockLLM lives, how the import switch happens) if the discussion gets long enough to warrant its own record.

## Order of operations (execution checklist)

1. Add `fakeredis` to `pyproject.toml` `[dev]` extras, reinstall.
2. Write `agents/_mock_llm.py`.
3. Migrate `tombombadil/film_parser.py` → `agents/tombombadil/film_parser.py` + port test. (smallest, no LLM dep)
4. Migrate `tombombadil/persistent_memory.py` + `auto_parser.py` + `film_knowledge.py` (Redis swap only).
5. Migrate `tombombadil/tmdb_client.py` (unchanged).
6. Migrate `tombombadil/memory.py` + `agent.py` (LLM swap, biggest risk).
7. Migrate `tombombadil/bot.py` (Discord entrypoint, last in this agent).
8. Delete `tombombadil/logger.py` + `structured_logging.py`.
9. Migrate `earendil/worker.py` + `context_trimmer.py` + `earendil_api.py` (last splits into agent + legacy_api).
10. Migrate test files for both agents.
11. Run `pytest tests/ -v` — all green before commit.
12. Single commit: `Phase 2 sub-pass 1: migrate earendil + tombombadil`.

## Verification

- `ruff check agents/ tests/` clean.
- `pytest tests/ -v` — all smoke + migration tests pass.
- `python -c "from agents.tombombadil.agent import *"` — no ImportError.
- `python -c "from agents.earendil.worker import *"` — no ImportError.
- `grep -rn "google.generativeai\|import structured_logging\|from logger" agents/ tests/` — empty (no stragglers).

## Resolved questions

1. **`legacy_api/earendil_api.py` location:** in-repo at `legacy_api/`. Grep-ability during Phase 3 outweighs git-history cleanliness; cleanup happens at Phase 5 polish.
2. **Tom Bombadil's `test_redis.py` and `test_discord_integration.py`:** skipped via `pytest.mark.skip(reason="Phase 4 — needs Redis + Discord fixtures")`. Files moved to `tests/tombombadil/` so they're visible but not run.
3. **`MockLLM` location:** single shared file at `agents/_mock_llm.py`. See ADR 0003 for full reasoning.
