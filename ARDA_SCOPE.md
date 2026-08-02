# ARDA — Multi-Agent System: Project Scope

**Version:** 1.0  
**Author:** Solomon Smith  
**Status:** Historical — this document captured the original pre-build intent
(Gemini/Groq/LangChain roster, `mcp/` path, Phase 1–5 plan). It is **not**
the source of truth anymore.

For current architecture, commands, and conventions see:
- [`README.md`](README.md) — product overview + API
- [`CLAUDE.md`](CLAUDE.md) — agent/dev guidance
- [`docs/decisions/`](docs/decisions/) — ADRs (including the Anthropic/LangGraph pivot)

The original five build phases are complete (tagged `v1.0.0`). Remaining work
is tracked in GitHub issues (Tom Bombadil audit deltas D2–D10, ops profiles).

---

## What This Is

**Arda** is a unified, portfolio-ready multi-agent system. It collapses three disconnected projects (`earendil/`, `earendil-mcp/`, `tombombadil/`) into one cohesive codebase with a shared foundation, defined agent roles, a single Docker Compose stack, and one FastAPI entry point.

Named after Tolkien's world. All agents are named after First Age characters.

> **Note:** Sections below retain the original Gemini/Groq/LangChain plan for
> historical context. Live code uses Anthropic (Sauron Opus, Finrod/Tom Haiku)
> + LangGraph + LlamaIndex, and the MCP package lives at `mcp_server/` (not
> `mcp/`). See README build history and ADR 0006.

---

## System Overview

```
User / Claude Code / MCP Client
          ↓
  FastAPI (Unified API)  ←→  MCP Server
          ↓
  ⚡ Sauron (Orchestrator)
     — plans, decomposes, routes —
     ↓           ↓           ↓
🌟 Earendil   [Retriever]  🌿 Tom Bombadil
 (Executor)   (RAG Agent)  (Film Club Bot)
     ↓           ↓
  Redis       Milvus
```

---

## Agent Roster

### ⚡ Sauron — Orchestrator
- **Status:** NEW (does not exist yet)
- **Role:** Receives natural language requests. Uses LangChain to plan, decompose tasks, and route to the correct agent. Maintains session state. Returns synthesized results.
- **Model:** Gemini 2.5 Flash (`gemini-2.5-flash`) via Google AI API
- **Tier:** `orchestrator`
- **Files:** `agents/sauron/agent.py`, `agents/sauron/planner.py`
- **Key behaviors:**
  - Receives POST `/execute` with a natural language message
  - Plans the task — breaks into subtasks if needed
  - Routes each subtask to the right agent via internal calls
  - Aggregates results and returns to caller
  - Falls back to mock LLM if no API key is set (dev mode)

### 🌟 Earendil — Executor
- **Status:** MIGRATED (exists as `earendil/earendil_api.py` + `earendil/worker.py`)
- **Role:** System execution layer. Runs shell commands, manages infrastructure ops, reports system state. The bridge between AI intent and actual system actions.
- **Model:** Groq + Llama 4 Scout (`llama-4-scout-17b-16e-instruct`) via Groq API
- **Tier:** `executor`
- **Files:** `agents/earendil/agent.py`, `agents/earendil/worker.py`
- **Migration notes:**
  - `earendil_api.py` → split between `api/routes/tasks.py` and `agents/earendil/agent.py`
  - `worker.py` → `agents/earendil/worker.py` (minimal changes, keep Redis queue logic)
  - Keyword-based planner replaced by Groq LLM call
  - Existing Redis task queue pattern preserved exactly
  - Falls back to mock LLM if no Groq API key

### [Retriever Agent] — Knowledge / RAG
- **Status:** NEW (does not exist yet)
- **Name:** TBD — First Age Tolkien character. Suggested: **Finrod** (Finrod Felagund, greatest lore-master of the Noldor, built Nargothrond — an underground vault of knowledge. Perfect metaphor for Milvus.)
- **Role:** RAG agent. Ingests documents, generates embeddings, stores in Milvus. Retrieves relevant chunks on query. Answers knowledge questions grounded in actual docs and data.
- **Model:** Groq + Llama 4 Scout for generation; `sentence-transformers` (local, free) for embeddings
- **Tier:** `retriever`
- **Files:** `agents/{name}/agent.py`, `agents/{name}/ingest.py`
- **Key behaviors:**
  - `/memory/ingest` endpoint — accepts text/file, embeds, stores in Milvus collection
  - `/memory/query` endpoint — semantic search → retrieved chunks → LLM synthesis
  - Initial knowledge base: ingest existing `earendil/memory/` docs on first run
  - Falls back to in-memory store if Milvus is not connected

### 🌿 Tom Bombadil — Film Club Bot
- **Status:** MIGRATED (exists as `tombombadil/`)
- **Role:** Discord bot for the film club. Parses film notes, tracks ratings and themes, makes recommendations, integrates with TMDB.
- **Model:** Groq + Llama 4 Scout (swap from current Gemini)
- **Tier:** `specialist`
- **Files:** `agents/tombombadil/bot.py`, `agents/tombombadil/film_parser.py`, `agents/tombombadil/persistent_memory.py`, etc.
- **Migration notes:**
  - Swap `google.generativeai` → `langchain_groq.ChatGroq` in `agent.py`
  - All other logic (film_parser, persistent_memory, tmdb_client, structured_logging) preserved as-is
  - Move entire `tombombadil/` directory into `agents/tombombadil/`
  - Update imports to reference `core.redis_client` instead of local redis setup

---

## Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| Orchestrator LLM | Gemini 2.5 Flash | $0.30/M, 2M context, strong reasoning, LangChain native |
| Executor/Specialist LLM | Groq + Llama 4 Scout | ~$0.11/M, 750 tok/sec, OpenAI-compatible, LangChain native |
| Embeddings | sentence-transformers (local) | Free, runs on MacBook M4, no API cost |
| LLM Framework | LangChain + LangGraph | Orchestration, tool calling, RAG chains, provider-agnostic |
| API Layer | FastAPI | Already in use, high performance, async |
| Task Queue | Redis | Already in use, proven, FIFO queue + result store |
| Vector Store | Milvus | Docker-native, production-grade, LangChain integration |
| Discord Bot | Discord.py | Already in use |
| Containerization | Docker Compose | Redis + Milvus + API + worker, one command startup |
| MCP Layer | FastMCP (mcp[cli]) | earendil-mcp relocated, cleaned |
| Config | Pydantic Settings | .env driven, type-safe, model router built in |
| Logging | structlog | Structured JSON, trace IDs, already partially in use |
| Testing | pytest + pytest-asyncio | Unit + integration + e2e |
| Local Dev LLM | Ollama (MacBook M4 only) | Free inference during development, no API burn |
| Package Management | pyproject.toml | Unified deps, replaces 3 separate requirements.txt files |

---

## LLM Model Router

All model selection is centralized in `core/config.py`. Agents declare a tier, config resolves the model. Swapping providers is an env var change — no agent code changes.

```python
# core/config.py

TIER_MODEL_MAP = {
    "orchestrator": os.getenv("ORCHESTRATOR_MODEL", "gemini-2.5-flash"),
    "executor":     os.getenv("EXECUTOR_MODEL", "llama-4-scout-17b-16e-instruct"),
    "retriever":    os.getenv("RETRIEVER_MODEL", "llama-4-scout-17b-16e-instruct"),
    "specialist":   os.getenv("SPECIALIST_MODEL", "llama-4-scout-17b-16e-instruct"),
}

TIER_PROVIDER_MAP = {
    "orchestrator": "google",   # Gemini API
    "executor":     "groq",
    "retriever":    "groq",
    "specialist":   "groq",
}

# Dev mode: if no API keys set, use mock LLM
USE_MOCK_LLM = os.getenv("USE_MOCK_LLM", "false").lower() == "true"
```

**Dev mode:** Set `USE_MOCK_LLM=true` in `.env` to run the entire system without any API keys. All LLM calls return deterministic mock responses. Required for testing and early development.

---

## Directory Structure

```
arda/                               ← renamed from Agents/
├── README.md                       ← portfolio-quality system overview
├── docker-compose.yml              ← Redis + Milvus + API service + worker
├── Dockerfile                      ← for the API service
├── pyproject.toml                  ← unified deps (replaces 3 requirements.txt)
├── Makefile                        ← make up, make dev, make test, make logs
├── .env.example                    ← all env vars documented
├── .env                            ← gitignored, actual secrets
├── .gitignore
│
├── core/                           ← shared foundation, imported by all agents
│   ├── __init__.py
│   ├── config.py                   ← Pydantic Settings, model_router(), env loading
│   ├── redis_client.py             ← single Redis connection (replaces 3 copies)
│   ├── milvus_client.py            ← Milvus connection + collection helpers
│   ├── models.py                   ← AgentTask, AgentResult, HealthStatus, TaskStatus
│   └── logging.py                  ← structlog setup, get_logger(), trace ID injection
│
├── agents/
│   ├── base.py                     ← BaseAgent ABC (all agents inherit this)
│   │
│   ├── sauron/                     ← NEW: orchestrator
│   │   ├── __init__.py
│   │   ├── agent.py                ← LangChain + Gemini 2.5 Flash, routes tasks
│   │   └── planner.py              ← task decomposition, subtask generation
│   │
│   ├── earendil/                   ← MIGRATED: executor
│   │   ├── __init__.py
│   │   ├── agent.py                ← executor logic, Groq LLM replaces keyword matching
│   │   └── worker.py               ← Redis queue worker (from earendil/worker.py)
│   │
│   ├── {retriever}/                ← NEW: RAG agent (name TBD, suggest: finrod)
│   │   ├── __init__.py
│   │   ├── agent.py                ← LangChain RAG chain over Milvus
│   │   └── ingest.py               ← document → embed → Milvus pipeline
│   │
│   └── tombombadil/                ← MIGRATED: Discord film club bot
│       ├── __init__.py
│       ├── bot.py                  ← Discord event handler + commands
│       ├── agent.py                ← response generation (Gemini → Groq)
│       ├── film_parser.py          ← keep as-is
│       ├── film_knowledge.py       ← keep as-is
│       ├── persistent_memory.py    ← update imports to use core.redis_client
│       ├── tmdb_client.py          ← keep as-is
│       ├── auto_parser.py          ← keep as-is
│       ├── memory.py               ← keep as-is
│       ├── logger.py               ← replace with core.logging
│       └── structured_logging.py   ← replace with core.logging
│
├── api/                            ← unified FastAPI (migrated from earendil_api.py)
│   ├── __init__.py
│   ├── main.py                     ← app factory, router registration, lifespan hooks
│   ├── middleware/
│   │   └── auth.py                 ← X-API-Key middleware (from earendil_api.py)
│   └── routes/
│       ├── tasks.py                ← POST /task, POST /execute, GET /result/{id}
│       ├── agents.py               ← POST /agents/{name}/run, GET /agents/health
│       ├── memory.py               ← POST /memory/ingest, POST /memory/query
│       └── health.py               ← GET /health
│
├── mcp/                            ← earendil-mcp/ relocated here
│   ├── __init__.py
│   └── server.py                   ← FastMCP tools pointing at unified API
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py                 ← fixtures: mock Redis, mock Milvus, mock LLM
│   ├── test_core.py                ← config, redis_client, milvus_client, models
│   ├── test_agents.py              ← agent unit tests with mocked LLMs
│   ├── test_api.py                 ← FastAPI routes with TestClient
│   └── test_e2e.py                 ← full pipeline: request → Sauron → agent → result
│
└── scripts/
    ├── dev.sh                      ← local dev startup without Docker
    └── ingest.py                   ← seed Milvus with earendil/memory/ docs
```

---

## API Design

All routes require `X-API-Key` header except `/health`.

### Core Execution

| Method | Route | Description |
|---|---|---|
| `POST` | `/execute` | Natural language → Sauron plans → routes to agents → returns result |
| `POST` | `/execute/wait` | Same as above but blocks until result is ready (sync) |
| `POST` | `/task` | Structured task → enqueue to Redis → worker picks up |
| `GET` | `/result/{task_id}` | Poll task result from Redis |

### Agent Direct Access

| Method | Route | Description |
|---|---|---|
| `POST` | `/agents/{name}/run` | Bypass Sauron, call agent directly |
| `GET` | `/agents/health` | Health status for all agents + infra |

### Memory / RAG

| Method | Route | Description |
|---|---|---|
| `POST` | `/memory/ingest` | Ingest document → embed → store in Milvus |
| `POST` | `/memory/query` | Semantic search → retrieved chunks → LLM synthesis |

### System

| Method | Route | Description |
|---|---|---|
| `GET` | `/health` | System health, no auth required |
| `POST` | `/query` | Read-only Redis/system state inspection (preserved from Earendil) |

---

## Shared Data Models (`core/models.py`)

```python
class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

class AgentTask(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid4()))
    agent: str                          # "sauron" | "earendil" | "finrod" | "tombombadil"
    type: str                           # "system" | "retrieval" | "plan" | "chat"
    payload: dict
    created_at: datetime = Field(default_factory=datetime.utcnow)

class AgentResult(BaseModel):
    task_id: str
    agent: str
    status: TaskStatus
    result: Optional[Any] = None
    error: Optional[str] = None
    duration_ms: Optional[int] = None

class HealthStatus(BaseModel):
    agent: str
    status: str                         # "healthy" | "degraded" | "offline"
    model: str
    provider: str
    latency_ms: Optional[int] = None
```

---

## BaseAgent Interface (`agents/base.py`)

```python
from abc import ABC, abstractmethod
from core.models import AgentTask, AgentResult, HealthStatus

class BaseAgent(ABC):
    tier: str           # "orchestrator" | "executor" | "retriever" | "specialist"
    name: str           # agent name (e.g., "earendil")

    @abstractmethod
    async def run(self, task: AgentTask) -> AgentResult:
        """Execute the agent's primary function."""
        ...

    async def health(self) -> HealthStatus:
        """Return current health status. Override to add LLM ping."""
        return HealthStatus(agent=self.name, status="healthy", model="unknown", provider="unknown")

    async def log_event(self, event: str, data: dict):
        """Structured logging with trace ID injection."""
        ...
```

---

## Docker Compose Services

```yaml
services:
  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  milvus:
    image: milvusdb/milvus:v2.4.0
    ports: ["19530:19530", "9091:9091"]
    environment:
      ETCD_ENDPOINTS: etcd:2379
      MINIO_ADDRESS: minio:9000

  api:
    build: .
    ports: ["5000:5000"]
    env_file: .env
    depends_on: [redis, milvus]
    command: uvicorn api.main:app --host 0.0.0.0 --port 5000

  worker:
    build: .
    env_file: .env
    depends_on: [redis]
    command: python -m agents.earendil.worker

  tombombadil:
    build: .
    env_file: .env
    depends_on: [redis]
    command: python -m agents.tombombadil.bot
```

---

## Environment Variables (`.env.example`)

```bash
# API Auth
ARDA_API_KEY=arda-dev-key-2026

# LLM Providers
GEMINI_API_KEY=           # Sauron (orchestrator) — get from aistudio.google.com
GROQ_API_KEY=             # Earendil, Finrod, Tom Bombadil — get from console.groq.com

# Dev Mode — set to true to skip all LLM calls during development
USE_MOCK_LLM=true

# Model Overrides (optional — defaults set in core/config.py)
ORCHESTRATOR_MODEL=gemini-2.5-flash
EXECUTOR_MODEL=llama-4-scout-17b-16e-instruct
RETRIEVER_MODEL=llama-4-scout-17b-16e-instruct
SPECIALIST_MODEL=llama-4-scout-17b-16e-instruct

# Infrastructure
REDIS_HOST=localhost
REDIS_PORT=6379
MILVUS_HOST=localhost
MILVUS_PORT=19530

# Discord (Tom Bombadil)
DISCORD_TOKEN=
TMDB_API_KEY=

# Earendil / MCP (Mac Mini connection)
EARENDIL_HOST=http://100.112.3.116:5000
EARENDIL_API_KEY=earendil-dev-key-2026
```

---

## Build Phases

### Phase 1 — Foundation (Core Library)
**Goal:** Everything that all agents depend on. Build this first, test it in isolation.

1. Scaffold new unified directory structure under `arda/`
2. Write `core/config.py` — Pydantic Settings, model_router(), USE_MOCK_LLM flag
3. Write `core/redis_client.py` — single async Redis connection
4. Write `core/milvus_client.py` — connection + collection helpers (create, insert, search)
5. Write `core/models.py` — all shared Pydantic models
6. Write `core/logging.py` — structlog setup, get_logger(), trace ID injection
7. Write `agents/base.py` — BaseAgent ABC
8. Write `pyproject.toml` — unified deps
9. Write `.env.example`

**Done when:** `from core.config import settings` works. `from agents.base import BaseAgent` works. No agents yet.

---

### Phase 2 — Agents (Build + Migrate)
**Goal:** All four agents implemented and unit-testable in isolation.

1. Build `agents/sauron/planner.py` — task decomposition, intent classification
2. Build `agents/sauron/agent.py` — LangChain + Gemini 2.5 Flash (mock when `USE_MOCK_LLM=true`)
3. Migrate `agents/earendil/worker.py` from `earendil/worker.py` — minimal changes
4. Build `agents/earendil/agent.py` — Groq LLM replaces keyword-based planner
5. Build `agents/{retriever}/ingest.py` — document → sentence-transformers → Milvus
6. Build `agents/{retriever}/agent.py` — LangChain RAG chain: query → Milvus → LLM → answer
7. Migrate `agents/tombombadil/` from `tombombadil/` — swap Gemini → Groq in `agent.py`
8. Update Tom Bombadil imports to use `core.redis_client` and `core.logging`
9. Relocate `earendil-mcp/` → `mcp/server.py`, update base URL to unified API

**Done when:** Each agent can be instantiated and `agent.run(mock_task)` returns an `AgentResult`.

---

### Phase 3 — Unified API
**Goal:** Single FastAPI server that exposes all agents behind one interface.

1. Write `api/main.py` — app factory, lifespan (connect Redis/Milvus on startup)
2. Write `api/middleware/auth.py` — migrate X-API-Key check from earendil_api.py
3. Write `api/routes/health.py` — `/health` endpoint
4. Write `api/routes/tasks.py` — `/task`, `/execute`, `/execute/wait`, `/result/{id}`
5. Write `api/routes/agents.py` — `/agents/{name}/run`, `/agents/health`
6. Write `api/routes/memory.py` — `/memory/ingest`, `/memory/query`
7. Wire Sauron into `POST /execute` — NL request flows through orchestrator to agents
8. Preserve all existing Earendil endpoints (`/plan`, `/query`, `/task`) for backward compat

**Done when:** `uvicorn api.main:app` starts. `POST /health` returns 200. `POST /execute` with mock LLM returns a result.

---

### Phase 4 — Infrastructure (Docker + Tests)
**Goal:** Everything runs in Docker. Tests pass. CI-ready.

1. Write `Dockerfile` for the API service
2. Write `docker-compose.yml` — Redis + Milvus + API + worker + tombombadil
3. Write `Makefile` with targets: `up`, `down`, `dev`, `test`, `logs`, `shell`
4. Write `tests/conftest.py` — fixtures: mock Redis, mock Milvus, mock LLM, TestClient
5. Write `tests/test_core.py` — config loading, model_router, Redis/Milvus client methods
6. Write `tests/test_agents.py` — BaseAgent, Sauron planner, Earendil executor (all mocked)
7. Write `tests/test_api.py` — all routes with TestClient, auth checks, error cases
8. Write `tests/test_e2e.py` — full pipeline test: POST /execute → Sauron → Earendil → result
9. Write `scripts/ingest.py` — seed Milvus with `earendil/memory/` docs on first run

**Done when:** `make up` starts all services. `make test` passes all tests.

---

### Phase 5 — Portfolio (README + Polish)
**Goal:** GitHub-ready. Someone can clone, run, and understand it in 10 minutes.

1. Write `README.md`:
   - System overview with architecture diagram (Mermaid)
   - Agent descriptions and responsibilities
   - Tech stack table
   - Setup instructions (local dev + Docker)
   - API endpoint reference with example `curl` commands
   - Cost model breakdown
2. Clean git history — squash WIP commits into logical checkpoints
3. Verify `.gitignore` — no `.env`, no API keys, no `__pycache__`
4. Tag `v1.0.0`
5. Push to GitHub

**Done when:** Someone can `git clone` → `cp .env.example .env` → `make up` → system runs.

---

## Cost Model

| Agent | Model | Input $/M | Output $/M |
|---|---|---|---|
| Sauron | Gemini 2.5 Flash | $0.30 | $1.00 |
| Earendil | Groq + Llama 4 Scout | $0.11 | $0.34 |
| [Retriever] | Groq + Llama 4 Scout | $0.11 | $0.34 |
| Tom Bombadil | Groq + Llama 4 Scout | $0.11 | $0.34 |
| Embeddings | sentence-transformers | $0 | $0 |
| Dev / testing | Ollama (MacBook M4) | $0 | $0 |

**Estimated monthly cost (personal use):**
- ~200 orchestrator calls/day × 1K tokens: ~$1.80/mo
- ~500 executor calls/day × 500 tokens: ~$0.83/mo
- ~100 RAG queries/day: ~$0.26/mo
- Discord bot: ~$0.05/mo
- **Total: < $3/month** (vs. $50-100/mo on Claude API for everything)

---

## Key Constraints and Decisions

### Non-Negotiable (from existing architecture)
- Redis task queue pattern is preserved exactly — FIFO queue, `task:{uuid}` result keys, 300s TTL
- API authentication is `X-API-Key` header — middleware checks `ARDA_API_KEY`
- Earendil worker runs as a separate process — API and worker stay separated (existing architecture doc is correct)
- Tom Bombadil film parsing logic (`film_parser.py`, `persistent_memory.py`) is preserved unchanged

### New Decisions
- **Dev mode first:** `USE_MOCK_LLM=true` lets the full system run without any API keys
- **Groq for most agents:** Earendil, the retriever, and Tom Bombadil all use Groq — consistent, cheap
- **Gemini only for Sauron:** Orchestrator needs stronger reasoning; Gemini 2.5 Flash is the best cost/quality trade-off
- **Local embeddings:** sentence-transformers runs locally, no embedding API cost
- **No Claude API in the agent stack** — too expensive at scale. Claude Code is the development tool; agents use Groq + Gemini

### Hardware Constraints
- **Mac Mini** (Core 2 Duo, 7.5GB RAM): Runs the Docker stack — Redis, Milvus, API, worker. Cannot run local LLMs.
- **MacBook M4**: Dev machine. Can run Ollama for local inference during development. Does not run production services.

---

## What Already Exists (Do Not Rewrite From Scratch)

| File | Location | Action |
|---|---|---|
| `earendil_api.py` | `earendil/` | Split into `api/routes/tasks.py` + `agents/earendil/agent.py` |
| `worker.py` | `earendil/` | Move to `agents/earendil/worker.py`, minimal changes |
| `bot.py` | `tombombadil/` | Move to `agents/tombombadil/bot.py` |
| `agent.py` | `tombombadil/` | Move, swap Gemini import for `langchain_groq.ChatGroq` |
| `film_parser.py` | `tombombadil/` | Move unchanged |
| `persistent_memory.py` | `tommodbadil/` | Move, update Redis import to `from core.redis_client import get_redis` |
| `tmdb_client.py` | `tombombadil/` | Move unchanged |
| `film_knowledge.py` | `tombombadil/` | Move unchanged |
| `auto_parser.py` | `tombombadil/` | Move unchanged |
| `earendil_mcp.py` | `earendil-mcp/` | Move to `mcp/server.py`, update `EARENDIL_HOST` to unified API |
| `tests/` | `earendil/` | Migrate to unified `tests/`, update imports |
| `memory/` docs | `earendil/` | Seed into Milvus via `scripts/ingest.py`, keep originals |

---

## Getting Started (for Claude Code)

When implementing this project, follow the phases in order. Phase 1 must be complete before Phase 2. Each phase has a clear "done when" checkpoint.

Start every session by reading this file. It is the source of truth.

**First task:** Scaffold the directory structure and implement Phase 1 (core library).

```bash
# Working directory
cd /path/to/arda

# Phase 1 target files
core/__init__.py
core/config.py
core/redis_client.py
core/milvus_client.py
core/models.py
core/logging.py
agents/__init__.py
agents/base.py
pyproject.toml
.env.example
.gitignore
```

All agents start with `USE_MOCK_LLM=true`. Real API keys (Groq, Gemini) are wired in during Phase 4 once the system is verified end-to-end.

---

## Auxiliary Mac Mini Services

Services that share the Mac Mini host with ARDA but are **not** ARDA agents — they run on schedule, write to local disk, and only touch the agent system if and when they need to send a notification through Tom Bombadil's Signal/Discord channel.

### sb34-watch (CSUSB ALPR Audit longitudinal monitor)

**Source repo:** `~/Projects/csusb-alpr-audit/monitor/` (separate repo, do not vendor into Agents).

**Purpose.** Daily fetch + diff of California Flock Safety transparency portals to produce a longitudinal record of portal presence/absence. Currently 5 portals seeded (CSUSB PD + San Bernardino CA PD + 3 peer CSU campus PDs); easy to expand.

**Architecture (already built):**
- `fetcher.py` — `curl_cffi` (Chrome impersonation) → `requests` fallback. **Important:** `requests`-only fetches return HTTP 403 from Cloudflare; `curl_cffi` is required for meaningful results.
- `storage.py` — SQLite (`monitor/data/snapshots.sqlite`) + content-addressed bodies under `monitor/data/bodies/<sha256>.html`.
- `differ.py` — 5 change states: `first_seen` / `unchanged` / `body_changed` / `became_absent` / `became_present`.
- `notifier.py` — currently a logging stub. Needs Signal/Discord wiring once the daily cadence is stable.
- `cli.py` — `python -m sb34_watch --once` entry point.

**Deployment plan on Mac Mini:**

1. **Clone or sync** `~/Projects/csusb-alpr-audit/` to the Mac Mini at the same path. Tailscale-only access; no public exposure.
2. **Python env.** `pyenv` 3.12.7 + `uv pip install -r monitor/requirements.txt` (deps: `curl-cffi`, `beautifulsoup4`, `lxml`, `requests`, `httpx`, `python-dotenv`, `pytest`).
3. **Verify.** `cd monitor && PYTHONPATH=src python3 -m pytest -q tests/` — must show 11/11 passing before scheduling.
4. **launchd plist** at `~/Library/LaunchAgents/com.solomon.sb34-watch.plist`:
   - Schedule: `StartCalendarInterval` daily at 08:00 PT.
   - Command: `cd ~/Projects/csusb-alpr-audit/monitor && PYTHONPATH=src /usr/bin/env python3 -m sb34_watch --once`.
   - `StandardOutPath` and `StandardErrorPath` to `~/Library/Logs/sb34-watch/{out,err}.log`.
   - `RunAtLoad=false`, `KeepAlive=false`.
5. **Notification handoff.** Once the daily cadence has 7+ clean days, swap `notifier.py` from logging-stub to a Tom-Bombadil-routed Signal post (or direct signal-cli call). The notifier interface (`notify(DiffResult) -> bool`) is stable, so this is a single-file change.
6. **Operational dashboard.** Optional: weekly `sqlite3 monitor/data/snapshots.sqlite` query summarizing per-portal change counts, posted to a single Tom Bombadil channel.

**Done when:**
- launchd loads the plist without errors (`launchctl bootstrap gui/$UID ...`).
- Daily run produces a new row per portal in `snapshots.sqlite`.
- A synthetic body change triggers `notifier.notify()` and surfaces in logs (and later, Signal).
- 14 consecutive days of `NoSuchKey` for CSUSB PD rules out the "transient outage" hypothesis in the audit's S3-portal-absent finding.

**Why this lives here.** The Mac Mini is the only host with a stable Tailscale identity, persistent disk, and the pipe to Tom Bombadil's notification channels. ARDA's Phase 4 Docker stack does not own this — the monitor is a small, scheduled, single-host service and should stay that way until there's a concrete reason to containerize it.

**Out of scope for this scope doc:**
- The legal / evidentiary work in `~/Projects/csusb-alpr-audit/`. This section covers deployment only.
- Wayback Machine fallback (deferred until baseline cadence is stable).
- Any cross-publishing of monitor output to a public dashboard.
