# ARDA

Multi-agent system. One unified codebase, one FastAPI entry point, four named agents working behind it.

Named for Tolkien's world. Agents are First Age characters.

```
User / Claude Code / MCP Client
          v
  FastAPI (unified API)  <->  MCP server
          v
  Sauron (orchestrator)
   plans, decomposes, routes
     v          v          v
 Earendil    Finrod    Tom Bombadil
 (executor)  (RAG)     (Discord film bot)
     v          v
  Redis      Milvus
```

## The Four Agents

| Agent | Role | Tier | Model | Status |
|---|---|---|---|---|
| **Sauron** | Orchestrator -- receives NL requests, plans/decomposes, routes to specialists, aggregates results | `orchestrator` | Gemini 2.5 Flash | Pending sub-pass 2 |
| **Earendil** | Executor -- runs shell commands and infrastructure ops via a Redis-backed task queue | `executor` | Groq + Llama 4 Scout | Migrated (sub-pass 1) |
| **Finrod** | Retriever -- RAG over Milvus; ingests docs, embeds, retrieves grounded answers | `retriever` | Groq + Llama 4 Scout (gen) + sentence-transformers (embed) | Pending sub-pass 2 |
| **Tom Bombadil** | Specialist -- Discord film club bot; parses notes, tracks ratings, recommends films | `specialist` | Groq + Llama 4 Scout | Migrated (sub-pass 1) |

All agents fall back to `agents._mock_llm.MockLLM` when `USE_MOCK_LLM=true`, so the system runs end-to-end with no API keys during development.

## Repo Layout

```
agents/         Four agent subpackages -- one per agent
  earendil/     Executor: agent.py + worker.py + context_trimmer.py
  tombombadil/  Specialist: agent.py + bot.py + film_*.py + tmdb_client.py
  sauron/       Orchestrator (sub-pass 2)
  finrod/       Retriever (sub-pass 2)
  base.py       BaseAgent ABC -- tier, name, async run(), health()
  _mock_llm.py  Drop-in LangChain Runnable replacement for dev mode

core/           Shared foundation -- imported by every agent
  config.py     Pydantic Settings -- env vars, model router by tier
  redis_client.py
  milvus_client.py
  models.py     AgentTask, AgentResult, HealthStatus, TaskStatus
  logging.py    structlog setup, trace ID injection

api/            Unified FastAPI server (Phase 3)
mcp/            FastMCP server (Phase 3)
legacy_api/     Verbatim copy of earendil_api.py -- reference only,
                excluded from the wheel, deleted at Phase 3.
docs/decisions/ ADRs -- 0001 format, 0002 sub-pass 1, 0003 mock LLM,
                0004 memory.py skip, 0005 legacy HTTP tests deferred
tests/          pytest suite -- core, mock LLM, per-agent smoke tests
scripts/        dev.sh, ingest.py (Phase 4)
```

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env       # USE_MOCK_LLM=true -- no API keys needed
pytest tests/ -v
```

Real API keys (Groq, Gemini, Discord, TMDB) are wired in once `USE_MOCK_LLM=false`. The model router lives in `core/config.py` -- swap providers via env vars without touching agent code.

## Build Phases

1. **Foundation** -- `core/`, `agents/base.py`, `pyproject.toml`. **Done.**
2. **Agents** -- migrate Earendil + Tom Bombadil; build Sauron + Finrod. **Sub-pass 1 done; sub-pass 2 in progress.**
3. **Unified API** -- `api/main.py`, routes, X-API-Key middleware.
4. **Infrastructure** -- Docker Compose (Redis + Milvus + API + worker), tests, ingest pipeline.
5. **Portfolio polish** -- README expansion, cost model, Mermaid diagram, tag v1.0.0.

Full scope and decisions: `ARDA_SCOPE.md` and `docs/decisions/`.

## Conventions

- All code targets Python 3.12+. `from __future__ import annotations` everywhere.
- LLM calls go through `core.config.settings` and the `use_mock_llm` gate. Never construct `ChatGroq` / `ChatGoogleGenerativeAI` without checking the flag first (see ADR 0003).
- Logging is `core.logging.get_logger(name)`. No `print()`, no `logging.basicConfig` in agent code.
- Redis access is through `core.redis_client.get_redis_sync()` / `get_redis_async()`. Never construct `redis.Redis(...)` inline.
- Decisions worth recording become numbered ADRs in `docs/decisions/`. Existing ADRs are immutable -- supersede or amend by writing a new ADR that references the old one.
