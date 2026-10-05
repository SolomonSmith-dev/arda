# ARDA

<!-- badges:start -->
[![CI](https://github.com/SolomonSmith-dev/arda/actions/workflows/ci.yml/badge.svg)](https://github.com/SolomonSmith-dev/arda/actions/workflows/ci.yml) ![Python](https://img.shields.io/badge/python-3.12+-blue.svg) ![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg) ![routing accuracy (mock)](https://img.shields.io/badge/routing_accuracy_%28mock%29-75.9%25-lightgrey)
<!-- badges:end -->

**One LangGraph orchestrator routes each request to a shell executor, a RAG retriever or a film-club specialist, behind a single FastAPI API and one MCP server.**

**Live demo:** _coming soon, deploy runbook in [`docs/deploy-demo.md`](docs/deploy-demo.md)_

![ARDA demo page in mock mode: a question, the retrieved answer, and a trace of specialist, tool calls, latency and tokens](docs/img/demo-page.png)

*The public demo page (`DEMO_MODE=true`), shown in mock mode. Shell execution is refused in the router, the agent and the worker.*

## Results

<!-- results:start -->
| Metric | Mock baseline | Live |
|---|---|---|
| Routing accuracy | 75.9% (n=54) | not run yet |
| Prompt-injection block rate | 25.0% (n=16) | not run yet |
| Retrieval recall@5 | 81.6% | not run yet |
| Retrieval MRR@10 | 0.725 | not run yet |
| Routing latency p50 / p95 | 2.31 / 2.57 ms | not run yet |
| Cost per request | $0.0000 | not run yet |

Sources: mock: [`evals/results/2026-10-05-mock.json`](evals/results/2026-10-05-mock.json).

**How to read this.** The mock column measures the offline test doubles: a keyword router that cannot answer "none", and a lexical bag-of-words embedder. It is a regression baseline that CI checks for determinism, not a claim about Claude. The low injection block rate is the point: on the default stack the only guard against a shell-bound prompt is the router. Public demo mode closes that gap structurally. It refuses shell execution at the API router, inside the Earendil agent and in the worker, and `tests/test_demo_mode.py` checks every route. The chance floor for retrieval on this corpus is 26.3% recall@5. The live column fills in when a run against the real API is committed (`scripts/run_evals.py --mode live`, hard spend cap, see [`evals/README.md`](evals/README.md)).
<!-- results:end -->

## Architecture

```mermaid
flowchart TD
    Client[User / Claude Code / MCP Client]
    Client -->|x-api-key| API[FastAPI<br/>api/main.py]
    Client -.->|stdio| MCP[MCP Server<br/>mcp_server/server.py]
    MCP -->|HTTP| API

    API --> Sauron[Sauron<br/>orchestrator<br/>LangGraph + Claude Opus 5]
    Sauron -->|tool_use| Earendil[Earendil<br/>executor<br/>shell via Redis queue]
    Sauron -->|tool_use| Finrod[Finrod<br/>retriever<br/>LlamaIndex + Claude Haiku 4.5]
    Sauron -->|tool_use| Tom[Tom Bombadil<br/>specialist<br/>Claude Haiku 4.5]

    Earendil <-->|task queue| Redis[(Redis)]
    Worker[Worker<br/>agents/earendil/worker.py] <-->|pop / store| Redis
    Finrod <-->|VectorStoreIndex| Store[(SimpleVectorStore<br/>or Milvus)]
    Tom <-->|chat history + facts| Redis
    Tom -.->|optional| Discord[Discord]
    Sauron <-->|checkpointer| Checkpoint[(MemorySaver dev<br/>AsyncSqliteSaver prod)]
```

Sauron is a real LangGraph `StateGraph`: Claude picks a specialist through native `tool_use`, the graph runs it, and loops until Claude stops calling tools. Each specialist is a `BaseAgent` with an async `run(AgentTask) -> AgentResult` contract. In demo mode the executor is swapped for a stub that refuses, and Finrod is sealed to a fixed corpus.

## What I'd do next

- Run the eval suite against Claude and commit it, so the Results table reports the model and not the mock, and compare Haiku with Opus on routing cost and accuracy.
- Replace Earendil's pass-through planner with an allowlisted command schema, so shell safety stops depending on the router alone.
- Merge the two finished branches (Finrod index persistence, GitHub activity audit) and move retrieval from the lexical baseline to MiniLM embeddings on a host that can run them.

## The agents

| Agent | Tier | Role | Default model |
|---|---|---|---|
| **Sauron** | `orchestrator` | LangGraph `StateGraph`: classifies intent via Claude's tool_use, dispatches to one specialist as a tool call, loops until Claude emits a terminal `text` block. Cross-turn memory via the checkpointer keyed by `thread_id`. | `claude-opus-5` |
| **Earendil** | `executor` | Plans + enqueues shell commands to a Redis-backed task queue. A separate worker process drains it and writes results back to Redis. No LLM in the agent itself - regex-based plan_task. | n/a |
| **Finrod** | `retriever` | RAG via LlamaIndex `VectorStoreIndex`. Default in-memory `SimpleVectorStore`; `MilvusVectorStore` under the `[full]` extra. LLM + embed model + vector store are constructor-injected. | `claude-haiku-4-5-20251001` |
| **Tom Bombadil** | `specialist` | Discord film-club bot. Conversational chat via Anthropic SDK directly; rule-based fact extractor + Finrod-backed long-term memory; reaction-confirmed note drafts. | `claude-haiku-4-5-20251001` |
| **Galadriel** | infra | Cron scheduler + worker. Calls the unified API for watch-party reminders and Letterboxd sync jobs. | n/a |
| **Gwaihir** | infra | Telegram ops bot. Sends/receives messages on an allowlisted chat ID. | n/a |

`USE_MOCK_LLM=true` (the default) swaps every LLM for a deterministic mock - `MockAnthropicClient` for Sauron's tool_use loop, `MockAnthropicChatClient` for Tom Bombadil's chat, LlamaIndex `MockLLM` for Finrod. The test suite runs end-to-end with **zero API keys**. `USE_MOCK_EMBEDDER=true` opts into LlamaIndex `MockEmbedding` independently, so you can flip on real LLM calls without paying the ~1GB torch + sentence-transformers footprint.

## API

All routes require `X-API-Key: <ARDA_API_KEY>` except `/health` and `/metrics`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness check (no auth). Returns `{status, agent, version}`. |
| `GET` | `/metrics` | Prometheus scrape (no auth). |
| `POST` | `/plan` | Run the regex planner only. Returns `{intent, subtasks}`. |
| `POST` | `/execute` | NL → plan → dispatch. Always returns a poll-able `task_id`. Shell intents enqueue to the worker queue; non-shell intents resolve via Sauron and persist the result to Redis under the same `task_id`. |
| `POST` | `/execute/wait` | Same as `/execute` but blocks until results land or `WAIT_TIMEOUT_SECONDS` (15s) elapses. |
| `POST` | `/execute/result` | Aggregate status across multiple `task_id`s. |
| `GET` | `/result/{task_id}` | Poll a single task's result from Redis. |
| `POST` | `/task` | Submit a structured task directly (`{type, action, payload}`). Bypasses Sauron. Used by the MCP `arda_execute` tool. |
| `POST` | `/agents/{name}/run` | Direct agent invocation. Bypasses Sauron entirely. |
| `GET` | `/agents/health` | Per-agent `HealthStatus`. |
| `POST` | `/memory/ingest` | Push a document into Finrod's vector store. |
| `POST` | `/memory/query` | Semantic search + LLM synthesis. |
| `POST` | `/query` | Read-only Redis / system inspection. Returns the legacy six-key `system_status` shape the MCP server reads. |
| `POST` | `/cron`, `GET` `/cron`, `GET` `/cron/{id}`, `DELETE` `/cron/{id}` | Galadriel job management. |

### Example

```bash
export ARDA_API_KEY=replace-me-generate-with-openssl-rand-hex-32   # required, no default; set in .env for prod

curl -s http://localhost:5000/health
# {"status":"online","agent":"earendil","version":"0.3.0"}

curl -s -X POST http://localhost:5000/execute/wait \
  -H "x-api-key: $ARDA_API_KEY" -H "content-type: application/json" \
  -d '{"message":"uptime"}'
# {"status":"completed","results":[{"output":"03:31:40 up 28 days, ..."}], ...}

curl -s -X POST http://localhost:5000/memory/ingest \
  -H "x-api-key: $ARDA_API_KEY" -H "content-type: application/json" \
  -d '{"doc_id":"arda","text":"ARDA is a multi-agent system. Sauron orchestrates via LangGraph."}'

curl -s -X POST http://localhost:5000/memory/query \
  -H "x-api-key: $ARDA_API_KEY" -H "content-type: application/json" \
  -d '{"message":"Who orchestrates in ARDA?"}'
# {"result":{"answer":"Sauron.", ...}}
```

## Run it

### Local dev (mock LLM, zero API keys)

```bash
uv sync --extra dev                # Python 3.12, slim install (~5s, no torch)
cp .env.example .env               # ships with USE_MOCK_LLM=true
uv run pytest tests/ -q            # full suite, no network
uv run uvicorn api.main:app        # needs Redis on localhost:6379
```

### Docker (production)

```bash
cp .env.example .env
# edit .env: set USE_MOCK_LLM=false, ANTHROPIC_API_KEY, ARDA_API_KEY
docker compose up -d
curl http://localhost:5000/health
```

On macOS, AirPlay Receiver already listens on port 5000, so the `api` container fails with `address already in use`. Either turn off AirPlay Receiver (System Settings > General > AirDrop & Handoff) or publish on another port: `ARDA_PORT=5050 docker compose up -d`, then use `localhost:5050`. `ARDA_PORT` can also live in `.env`.

The default Docker image is intentionally slim: no `torch`, no `pymilvus`, no `sentence-transformers`. Finrod uses `MockEmbedding` + the in-memory `SimpleVectorStore` by default. To get real semantic embeddings via `sentence-transformers/all-MiniLM-L6-v2` and Milvus, install with the `[full]` extra (`uv sync --extra dev --extra full`) and set `USE_MOCK_EMBEDDER=false` + `MILVUS_HOST`.

See `docs/cutover.md` for the deployment runbook.

## Repository layout

```
agents/             One package per agent
  base.py           BaseAgent ABC: tier, name, async run(), async health()
  conduct.py        Shared CONDUCT_PROMPT used by Tom Bombadil's system message
  _anthropic_mock.py    Anthropic-shaped mocks (tool_use + chat-only)
  _llama_index_mock.py  Deterministic hash-embedding for Finrod tests
  sauron/           Orchestrator: agent.py + graph.py + state.py +
                    tools.py + llm.py + planner.py (regex /plan helper)
  earendil/         Executor: agent.py + worker.py + context_trimmer.py
  finrod/           Retriever: agent.py + embeddings.py + store.py + llm.py
  tombombadil/      Specialist: agent.py + bot.py + commands.py +
                    fact_extractor.py + memory.py + draft_store.py +
                    film_knowledge.py + identity.py + ... (Discord, Letterboxd)
  galadriel/        Cron scheduler + worker
  gwaihir/          Telegram ops bot

api/                Unified FastAPI server
  main.py           App factory + lifespan + _make_checkpointer
  middleware/       X-API-Key auth
  routes/           health, tasks, agents, memory, query, cron

core/               Shared foundation imported by every agent
  config.py         Pydantic Settings + per-tier model/provider routing
  redis_client.py   Sync + async Redis singletons; task_queue keys
  milvus_client.py  Optional pymilvus connection helper ([full] only)
  models.py         AgentTask, AgentResult, TaskStatus, HealthStatus
  logging.py        structlog with trace-id injection

mcp_server/         FastMCP server exposing arda_execute / arda_plan /
                    arda_query / arda_status as Claude Code tools,
                    wired to the unified api/main.py over HTTP

docs/               ADRs + cutover and demo-deploy runbooks + Tom Bombadil specs
tests/              pytest suite -- mock-by-default, runs without keys
evals/              Routing + retrieval eval sets, runner library, committed results
deploy/demo/        Compose override + cloudflared template for the public demo
scripts/            dev.sh, ingest.py, ingest_brain_db.py, run_evals.py, update_readme.py
.github/workflows/  CI: uv + ruff + pytest on every PR
```

## Cost model

Anthropic is the only LLM provider:

| Tier | Default model | Volume | Notes |
|---|---|---|---|
| Orchestrator (Sauron) | `claude-opus-5` | ~1 call per user message | Tool-calling loop; usually 2-3 round trips per request |
| Retriever (Finrod) | `claude-haiku-4-5-20251001` | per `/memory/query` call | Synthesis only; retrieval is local |
| Specialist (Tom Bombadil) | `claude-haiku-4-5-20251001` | per Discord turn | Conversational chat |
| Executor (Earendil) | n/a | - | Regex planner, no LLM |
| Embeddings | `MockEmbedding` (slim) / `sentence-transformers/all-MiniLM-L6-v2` ([full]) | local | $0 either way |
| Dev / testing | All mocks | local | $0 |

See [Anthropic pricing](https://docs.anthropic.com/en/docs/about-claude/pricing) for current rates. The slim test path uses zero external calls.

## Conventions

- Python 3.12+, `from __future__ import annotations` everywhere.
- LLM calls flow through each agent's `llm.py` factory (`agents/sauron/llm.py`, `agents/finrod/llm.py`, `agents/tombombadil/llm.py`) which honors `USE_MOCK_LLM`. Never construct an Anthropic client inline.
- The `BaseAgent.run(AgentTask) -> AgentResult` contract is load-bearing - Sauron's tool_dispatch, the `/agents/{name}/run` route, and the e2e tests all depend on the result envelope shape.
- LlamaIndex's `Settings` global is **never** mutated - Finrod passes `llm`/`embed_model` explicitly to `VectorStoreIndex` so concurrent test instances stay isolated.
- Logging is `core.logging.get_logger(name)`. No `print()`, no `logging.basicConfig` in agent code.
- Redis access goes through `core.redis_client.get_redis_sync()` / `get_redis_async()`. Never construct `redis.Redis(...)` inline.
- Ruff is authoritative (`E,F,I,B,UP,N,SIM`, line length 100). CI runs `ruff check .` + `pytest tests/ -q` on every PR.
- Decisions worth recording become numbered ADRs in `docs/decisions/`. Existing ADRs are immutable; supersede or amend with a new ADR referencing the old one.

## Build history

1. **Foundation** (`core/`, `agents/base.py`, slim install).
2. **Agents** - initial Earendil + Tom Bombadil, then Sauron + Finrod scaffolded.
3. **Unified API** (`api/main.py` + routers + auth) + MCP rewire onto the unified API.
4. **Architecture pivot to provider SDKs + LangGraph patterns** (2026):
   - Sauron rebuilt as a real LangGraph `StateGraph` with native Anthropic tool_use and a durable checkpointer (`#7`, `#33`).
   - Finrod migrated to LlamaIndex (`VectorStoreIndex` + injectable components) (`#35`).
   - Tom Bombadil moved off `langchain-groq` onto the anthropic SDK directly (`#38`).
   - Dead LangChain-anthropic intent classifier + orphaned Groq/Gemini config removed (`#39`).

Full scope: [`docs/history/ARDA_SCOPE.md`](docs/history/ARDA_SCOPE.md). Decisions: [`docs/decisions/`](docs/decisions/). Cutover runbook: [`docs/cutover.md`](docs/cutover.md). Agent guidance for Claude Code: [`CLAUDE.md`](CLAUDE.md).
