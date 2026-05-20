# CLAUDE.md

Guidance for Claude Code working in this repo. Keep it accurate; update it when the architecture changes.

## What this is

ARDA — a multi-agent system behind one FastAPI entry point and one MCP surface. Tolkien-named agents, each a `BaseAgent` with an async `run(AgentTask) -> AgentResult` contract (`agents/base.py`, `core/models.py`).

## Commands

Dependencies are managed with **uv** (Python 3.12). The default install is slim — no torch/pymilvus.

- Install: `uv sync --extra dev`
- Test (full suite): `uv run pytest tests/ -q`
- Test (one area): `uv run pytest tests/sauron/ -q`
- Lint: `uv run ruff check .` (autofix: `--fix`)
- Type-check: `uv run mypy agents core` (pymilvus is `[full]`-only, so a missing-stub note there is expected on slim installs)
- Run the API: `uv run uvicorn api.main:app` (lifespan registers all agents on `app.state`)
- Heavy extras (real embeddings + Milvus): `uv sync --extra dev --extra full`

CI (`.github/workflows/ci.yml`) runs `ruff check .` + `pytest tests/ -q` on a slim install for every PR and push to `main`. Keep both green.

## Architecture

- **`agents/`** — one package per agent:
  - `sauron/` — orchestrator. A real LangGraph `StateGraph` (`graph.py`): `agent_step` calls Claude with the specialists exposed as native Anthropic tools (`tools.py`), `tool_dispatch` invokes the matching specialist's `BaseAgent.run`, looping until Claude stops emitting `tool_use`. Typed state in `state.py`; checkpointer gives `thread_id` cross-turn memory.
  - `earendil/` — executor. Shell tasks via a Redis queue + separate `worker.py`.
  - `finrod/` — retriever. LlamaIndex-backed RAG (`VectorStoreIndex` with `SimpleVectorStore` by default, `MilvusVectorStore` under `[full]`). LLM + embed model + vector store are constructor-injected; defaults use Anthropic Claude Haiku as the synthesis LLM and `MockEmbedding` (slim) / `HuggingFaceEmbedding` (`[full]`).
  - `tombombadil/` — Discord film-club specialist.
  - `galadriel/` — cron/scheduler. `gwaihir/` — Telegram ops bot.
  - `base.py` (the ABC), `_mock_llm.py` (LangChain-shaped mock), `_anthropic_mock.py` (Anthropic-shaped mock for Sauron).
- **`core/`** — `config.py` (pydantic-settings singleton; per-tier model/provider routing), `models.py` (`AgentTask`/`AgentResult`), `redis_client.py`, `milvus_client.py`, `logging.py` (structlog + trace IDs).
- **`api/`** — FastAPI app. `main.py` lifespan builds the agents; `_make_checkpointer` picks `MemorySaver` (mock/dev) vs durable `AsyncSqliteSaver` (prod). Generic `POST /agents/{name}/run` reaches any agent.
- **`mcp_server/`** — MCP tools (`arda_execute / _query / _plan / _status`) that call the unified API (`api/main.py`) at `settings.arda_api_url`.

## Conventions & gotchas

- **Mock-by-default.** `settings.use_mock_llm` defaults to `True`; tests need no API keys. The Sauron client builder returns `MockAnthropicClient` unless `use_mock_llm=False` *and* `anthropic_api_key` is set. `mock_embedder_enabled` mirrors `use_mock_llm` — flipping `use_mock_llm` globally in a test cascades into Finrod's embedder (real torch). Gate narrowly or use the existing helpers.
- **Preserve the `BaseAgent.run` contract.** Sauron's result envelope (`result.result["intent"|"specialist"|"specialist_result"]`) is depended on by the API and e2e tests — keep it stable when changing internals.
- **Checkpointer is durable in prod only.** Dev/test = `MemorySaver` (file-free); prod = `AsyncSqliteSaver` at `settings.checkpointer_db_path` (`.arda/`, gitignored).
- **Import-time side effects exist.** `agents/tombombadil/agent.py` builds a module-level `FilmKnowledge()` at import, which reads `LETTERBOXD_EXPORT_DIR` in `__init__`. Tests needing hermeticity must clear env *before* importing it (see `tests/integration/conftest.py`).
- **Ruff** is authoritative (`select = E,F,I,B,UP,N,SIM`, line length 100). A few legacy dirs (`earendil/`, `tombombadil/`, `earendil-mcp/`) are excluded — don't lint-chase them.
- **Tests:** `pytest-asyncio` auto mode. `phase4`/`integration` markers gate tests needing live services; `tests/conftest.py` skips `phase4`.

## Git

Develop on the branch designated for the session; commit with clear messages; open a PR only when asked. Don't push to `main` directly. Force-push only rebased feature branches, with `--force-with-lease`.
