# ARDA

<!-- badges:start -->
[![CI](https://github.com/SolomonSmith-dev/arda/actions/workflows/ci.yml/badge.svg)](https://github.com/SolomonSmith-dev/arda/actions/workflows/ci.yml) ![Python](https://img.shields.io/badge/python-3.12+-blue.svg) ![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg) ![routing accuracy (mock)](https://img.shields.io/badge/routing_accuracy_%28mock%29-75.9%25-lightgrey)
<!-- badges:end -->

**I built ARDA to put a Claude orchestrator in front of a shell and find out, with numbers, where the routing fails before a command does.**

One LangGraph `StateGraph` (Sauron) classifies each request through native `tool_use` and dispatches it to a shell executor, a RAG retriever or a Discord film-club specialist, behind one FastAPI API and one MCP server. The hard part is not the routing, it is the shell-bound path: an injected prompt that reaches the executor runs a command. So the repo ships a labelled eval set with 16 injection cases, a committed baseline that shows the offline router leaking 12 of them, and a demo mode that closes the gap structurally: shell refused at the router, inside the agent and in the worker, with every route swept against the OpenAPI schema. Every LLM has a deterministic mock, so the whole suite runs with zero API keys.

## Evidence

- **Tests:** 582 collected, 577 passed, 5 skipped (the skips need a real `ANTHROPIC_API_KEY`). `uv run pytest tests/ -q`, run 2026-11-03 on this commit, 29s.
- **CI:** `.github/workflows/ci.yml` runs `scripts/check.sh` on every push to `main` and every PR: ruff, mypy on `agents` and `core`, pytest, mock evals twice (fails if the runs differ or drift from `evals/results/baseline-mock.json`), and a check that the Results table below matches the committed eval JSON.
- **Evals:** routing accuracy 75.9% (n=54), prompt-injection block rate 25.0% (n=16), retrieval recall@5 81.6%, MRR@10 0.725. Source: [`evals/results/2026-10-05-mock.json`](evals/results/2026-10-05-mock.json). Mock column only; the live run against Claude has not been committed yet. Gold sets: `evals/routing/gold.jsonl` (70 rows), `evals/retrieval/queries.jsonl` (38 queries).
- **Demo-mode safety:** 17 tests in [`tests/test_demo_mode.py`](tests/test_demo_mode.py), including a sweep that reads every operation from the OpenAPI schema and asserts each non-allowlisted route returns a refusal.
- **Surface:** 7 agents (`agents/`), 17 HTTP routes (15 behind `X-API-Key`, 2 open) plus 3 demo routes (`api/`), 4 MCP tools (`mcp_server/server.py`).
- **Project type:** solo project, 146 commits, MIT. Built with Claude Code as the coding agent; agent-authored commits are attributed in `git log`.

## How to run

Clean clone, no API keys, no Docker. Needs [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
git clone https://github.com/SolomonSmith-dev/arda && cd arda
uv sync --extra dev                # slim install: no torch, no pymilvus
cp .env.example .env               # ships with USE_MOCK_LLM=true
uv run pytest tests/ -q            # 577 passed, 5 skipped, no network
scripts/check.sh                   # the CI gate, same script CI runs
```

Public demo page, no Redis, shell refused everywhere:

```bash
DEMO_MODE=true uv run uvicorn api.main:app --port 5001
open http://localhost:5001/        # or curl localhost:5001/demo/status
```

Full stack (API + Earendil worker) needs Redis on `localhost:6379`:

```bash
redis-server --daemonize yes
uv run uvicorn api.main:app --port 5000      # bare uvicorn binds 8000; the examples below use 5000
uv run python -m agents.earendil.worker      # drains the shell task queue
```

Docker: `cp .env.example .env`, set `ARDA_API_KEY` (and `USE_MOCK_LLM=false` plus `ANTHROPIC_API_KEY` for real calls), then `docker compose up -d`. On macOS AirPlay Receiver owns port 5000; use `ARDA_PORT=5050 docker compose up -d` or turn it off. Deployment runbooks: [`docs/cutover.md`](docs/cutover.md) (production), [`docs/deploy-demo.md`](docs/deploy-demo.md) (public demo behind a Cloudflare Tunnel; not deployed at the time of writing).

![ARDA demo page in mock mode: a question, the retrieved answer, and a trace of specialist, tool calls, latency and tokens](docs/img/demo-page.png)

*The demo page (`DEMO_MODE=true`) in mock mode. Shell execution is refused in the router, the agent and the worker.*

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
    Sauron -->|tool_use| Rumil[Rúmil<br/>specialist<br/>GitHub audit + Claude Haiku 4.5]
    Rumil -->|stores snapshots| Finrod

    Earendil <-->|task queue| Redis[(Redis)]
    Worker[Worker<br/>agents/earendil/worker.py] <-->|pop / store| Redis
    Finrod <-->|VectorStoreIndex| Store[(SimpleVectorStore<br/>or Milvus)]
    Tom <-->|chat history + facts| Redis
    Tom -.->|optional| Discord[Discord]
    Sauron <-->|checkpointer| Checkpoint[(MemorySaver dev<br/>AsyncSqliteSaver prod)]
```

Sauron is a real LangGraph `StateGraph`: Claude picks a specialist through native `tool_use`, the graph runs it, and loops until Claude stops calling tools. Each specialist is a `BaseAgent` with an async `run(AgentTask) -> AgentResult` contract. In demo mode the executor is swapped for a stub that refuses, and Finrod is sealed to a fixed corpus. An optional judge layer (`core/judge.py`, off by default and skipped in mock mode) grades agent output against named checks through a forced tool call, retries up to 3 times, and appends each failed check to `memory/lessons.md`.

### The agents

| Agent | Tier | Role | Default model (`core/config.py`) |
|---|---|---|---|
| **Sauron** | `orchestrator` | LangGraph `StateGraph`: classifies intent via Claude's `tool_use`, dispatches to one specialist as a tool call, loops until Claude emits a terminal `text` block. Cross-turn memory via the checkpointer keyed by `thread_id`. | `claude-opus-5` |
| **Earendil** | `executor` | Plans and enqueues shell commands to a Redis-backed task queue. A separate worker process drains it and writes results back. No LLM; regex-based `plan_task`. | n/a |
| **Finrod** | `retriever` | RAG via LlamaIndex `VectorStoreIndex`. Default in-memory `SimpleVectorStore`; `MilvusVectorStore` under the `[full]` extra. LLM, embed model and vector store are constructor-injected. | `claude-haiku-4-5-20251001` |
| **Tom Bombadil** | `specialist` | Discord film-club bot. Conversational chat via the Anthropic SDK directly; rule-based fact extractor plus Finrod-backed long-term memory; reaction-confirmed note drafts. | `claude-haiku-4-5-20251001` |
| **Rúmil** | `specialist` | GitHub activity chronicler. Fetches a window of commits, PRs and contribution streak, writes a short summary, stores it in Finrod. Cron `0 8 * * *` America/Los_Angeles via Galadriel to Telegram, or on demand with `/audit [hours]`. | `claude-haiku-4-5-20251001` |
| **Galadriel** | infra | Cron scheduler + worker. Calls the unified API for watch-party reminders, Letterboxd sync and the GitHub audit. | n/a |
| **Gwaihir** | infra | Telegram ops bot. Sends and receives messages on an allowlisted chat ID. | n/a |

`USE_MOCK_LLM=true` (the default) swaps every LLM for a deterministic mock: `MockAnthropicClient` for Sauron's `tool_use` loop, `MockAnthropicChatClient` for Tom Bombadil's chat, LlamaIndex `MockLLM` for Finrod. `tests/test_mock_sdk_parity.py` introspects the installed Anthropic SDK so the mocks cannot accept a parameter the real client rejects. `USE_MOCK_EMBEDDER` opts into real embeddings independently, so you can flip on real LLM calls without installing torch.

## API

All routes require `X-API-Key: <ARDA_API_KEY>` except `/health` and `/metrics`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness check (no auth). Returns `{status, agent, version}`. |
| `GET` | `/metrics` | Prometheus scrape (no auth). |
| `POST` | `/plan` | Run the regex planner only. Returns `{intent, subtasks}`. |
| `POST` | `/execute` | NL to plan to dispatch. Always returns a poll-able `task_id`. Shell intents enqueue to the worker queue; non-shell intents resolve via Sauron and persist under the same `task_id`. |
| `POST` | `/execute/wait` | Same as `/execute` but blocks until results land or `WAIT_TIMEOUT_SECONDS` (15s) elapses. |
| `POST` | `/execute/result` | Aggregate status across multiple `task_id`s. |
| `GET` | `/result/{task_id}` | Poll a single task's result from Redis. |
| `POST` | `/task` | Submit a structured task directly (`{type, action, payload}`). Bypasses Sauron. Used by the MCP `arda_execute` tool. |
| `POST` | `/agents/{name}/run` | Direct agent invocation. Bypasses Sauron. |
| `GET` | `/agents/health` | Per-agent `HealthStatus`. |
| `POST` | `/memory/ingest` | Push a document into Finrod's vector store. |
| `POST` | `/memory/query` | Semantic search + LLM synthesis. |
| `POST` | `/query` | Read-only Redis / system inspection; the `system_status` shape the MCP server reads. |
| `POST` `GET` `GET` `DELETE` | `/cron`, `/cron`, `/cron/{id}`, `/cron/{id}` | Galadriel job management. |

Demo mode adds `GET /`, `GET /demo/status` and `POST /demo/ask` and refuses every other non-allowlisted route. The MCP server (`mcp_server/server.py`, FastMCP over stdio) exposes `arda_execute`, `arda_plan`, `arda_query` and `arda_status` and talks to the API over HTTP.

### Example

```bash
export ARDA_API_KEY=replace-me-generate-with-openssl-rand-hex-32   # required, no default; set in .env for prod

curl -s http://localhost:5000/health
# {"status":"online","agent":"earendil","version":"0.3.0"}

curl -s -X POST http://localhost:5000/execute/wait \
  -H "x-api-key: $ARDA_API_KEY" -H "content-type: application/json" \
  -d '{"message":"uptime"}'

curl -s -X POST http://localhost:5000/memory/ingest \
  -H "x-api-key: $ARDA_API_KEY" -H "content-type: application/json" \
  -d '{"doc_id":"arda","text":"ARDA is a multi-agent system. Sauron orchestrates via LangGraph."}'

curl -s -X POST http://localhost:5000/memory/query \
  -H "x-api-key: $ARDA_API_KEY" -H "content-type: application/json" \
  -d '{"message":"Who orchestrates in ARDA?"}'
```

In mock mode `/memory/query` returns real scored sources and a placeholder answer; only the synthesis step is mocked.

## Limitations

- The Results table measures the offline mocks, not Claude. A keyword router cannot answer "none", which is why chitchat scores 0% and 12 of 16 injections reach the executor stub. The live column stays empty until a run with `scripts/run_evals.py --mode live` is committed.
- Earendil's planner is regex, not a model, and it has no command allowlist outside demo mode. Shell safety on the default stack rests on the router.
- The slim install uses `MockEmbedding`, which carries no similarity structure. Real retrieval needs `uv sync --extra dev --extra embeddings` (sentence-transformers, about 1GB of torch) and `USE_MOCK_EMBEDDER=false`.
- Tom Bombadil's Discord and Letterboxd paths and Rúmil's GitHub path are tested against doubles; the `integration` marker covers the live-service tests and they are skipped without credentials.

## What I'd do next

- Run the eval suite against Claude and commit it, so the Results table reports the model and not the mock, then compare Haiku with Opus on routing cost and accuracy.
- Replace Earendil's pass-through planner with an allowlisted command schema, so shell safety stops depending on the router alone.
- Move the default retrieval path from the lexical baseline to MiniLM embeddings on a host that can run them.

## Repository layout

```
agents/             One package per agent
  base.py           BaseAgent ABC: tier, name, async run(), async health()
  _anthropic_mock.py    Anthropic-shaped mocks (tool_use + chat-only)
  _llama_index_mock.py  Deterministic hash-embedding for Finrod tests
  sauron/           Orchestrator: agent.py, graph.py, state.py, tools.py, llm.py, planner.py
  earendil/         Executor: agent.py, worker.py, context_trimmer.py
  finrod/           Retriever: agent.py, embeddings.py, store.py, llm.py
  tombombadil/      Specialist: Discord bot, fact extractor, memory, Letterboxd sync
  rumil/            Specialist: agent.py, github.py, report.py, cron.py
  galadriel/        Cron scheduler + worker
  gwaihir/          Telegram ops bot

api/                Unified FastAPI server: main.py, middleware/ (X-API-Key), routes/, demo.py
core/               Shared foundation: config.py (Pydantic Settings, per-tier model routing),
                    redis_client.py, models.py, logging.py (structlog + trace id), judge.py
mcp_server/         FastMCP server exposing arda_execute / arda_plan / arda_query / arda_status
evals/              Routing + retrieval gold sets, harness, committed results, pricing.json
tests/              pytest suite, mock-by-default, runs without keys
docs/               ADRs (docs/decisions/), cutover and demo-deploy runbooks, Tom Bombadil specs
deploy/demo/        Compose override + cloudflared template for the public demo
scripts/            check.sh (the gate), run_evals.py, update_readme.py, ingest.py, dev.sh
.github/workflows/  ci.yml (the gate on push and PR), code-review.yml (AI review on PRs)
```

## Conventions

- Python 3.12+, `from __future__ import annotations` everywhere. Ruff is authoritative (`E,F,I,B,UP,N,SIM`, line length 100).
- LLM calls flow through each agent's `llm.py` factory, which honors `USE_MOCK_LLM`. Never construct an Anthropic client inline.
- The `BaseAgent.run(AgentTask) -> AgentResult` contract is load-bearing: Sauron's tool dispatch, the `/agents/{name}/run` route and the e2e tests all depend on the result envelope.
- LlamaIndex's `Settings` global is never mutated; Finrod passes `llm` and `embed_model` explicitly so concurrent test instances stay isolated.
- Logging is `core.logging.get_logger(name)`. Redis access goes through `core.redis_client`. No inline `print()`, `logging.basicConfig` or `redis.Redis(...)`.
- Decisions worth recording become numbered ADRs in `docs/decisions/`. Existing ADRs are immutable; supersede with a new one.

## Build history

1. Foundation: `core/`, `agents/base.py`, slim install.
2. Agents: Earendil and Tom Bombadil first, then Sauron and Finrod.
3. Unified API (`api/main.py`, routers, auth) and the MCP server rewired onto it.
4. Pivot to provider SDKs and LangGraph: Sauron rebuilt as a `StateGraph` with native Anthropic `tool_use` and a durable checkpointer (#7, #33); Finrod migrated to LlamaIndex (#35); Tom Bombadil moved off `langchain-groq` onto the Anthropic SDK (#38); dead LangChain intent classifier and Groq/Gemini config removed (#39).
5. Evals, demo mode, Finrod index persistence (#93), Rúmil GitHub audit (#94), judge layer (#107).

Full scope: [`docs/history/ARDA_SCOPE.md`](docs/history/ARDA_SCOPE.md). Decisions: [`docs/decisions/`](docs/decisions/). Agent guidance for Claude Code: [`CLAUDE.md`](CLAUDE.md).
