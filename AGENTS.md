# AGENTS.md

Standard build/test/run commands live in `CLAUDE.md` and `README.md`. Dependencies
are managed with `uv` (Python 3.12); the default install is slim (no torch/pymilvus).

## Cursor Cloud specific instructions

The dev environment for the ARDA multi-agent system (one FastAPI app + Earendil
worker + Redis) is already provisioned on the VM. The startup update script runs
`uv sync --extra dev`; everything below is about running/testing, not installing.

- `uv` is installed at `~/.local/bin`. If `uv` is not found, prefix `PATH`:
  `export PATH="$HOME/.local/bin:$PATH"`.
- Tests and lint need no services and no API keys (mock-by-default, `fakeredis`):
  `uv run ruff check .` and `uv run pytest tests/ -q`.
- Running the live product needs three things, in order:
  1. Redis on `localhost:6379`. It is installed via `apt` (Docker is NOT available
     in this VM, so the `docker compose` path in the README does not apply here).
     Start it with `redis-server --daemonize yes --port 6379`.
  2. API: `uv run uvicorn api.main:app --host 0.0.0.0 --port 5000`. Bare
     `uvicorn api.main:app` binds `:8000` — always pass `--port 5000` so it matches
     the README, MCP `ARDA_API_URL`, and the auth examples.
  3. Earendil worker: `uv run python -m agents.earendil.worker`. Shell-execution
     intents (`POST /execute`, `POST /task`, MCP `arda_execute`) only complete when
     this worker is running; without it those tasks enqueue but never resolve.
- Create `.env` once with `cp .env.example .env`. `USE_MOCK_LLM=true` is the default,
  so no LLM keys are required. All routes except `/health` and `/metrics` require
  header `x-api-key: arda-dev-key-2026` (the `.env.example` default).
- In mock mode, `POST /memory/query` (Finrod) returns a placeholder `"text text ..."`
  answer — this is expected. Retrieval is real (the `sources`/`score` are genuine);
  only the LLM synthesis step is mocked.
- `pymilvus_unavailable` warned at API startup is expected on the slim install
  (Milvus lives behind the `[full]` extra); Finrod falls back to the in-memory store.

## Handoff — project status for successor agents

### Already on `main`

- Dev environment notes (this file) + cloud update script `uv sync --extra dev`.
- Audit code burn-down complete: #44 (D2), #45 (D7/D3), #46 (D6/D8/D9/D10), #48 (I1).
- ADR 0006 (Anthropic + `mcp_server/` canonical); `pyproject` 0.3.0.
- Core product works mock-by-default: Sauron / Earendil / Finrod / Tom / Galadriel / Gwaihir.
- `ARDA_SCOPE.md` is **historical** — trust README, CLAUDE.md, ADRs, and this file.

### Remaining work (operator-only)

| Priority | Item | Type | Notes |
|---|---|---|---|
| 1 | [#21 D4](https://github.com/SolomonSmith-dev/arda/issues/21) Galadriel cron | **Operator** | Deploy host: `docker compose --profile cron up -d`. Runbook + `./scripts/verify-d4-d5.sh` in `docs/cutover.md`. |
| 2 | [#22 D5](https://github.com/SolomonSmith-dev/arda/issues/22) Milvus | **Operator** | Deploy host: `docker compose --profile milvus up -d` + `[full]` + `USE_MOCK_EMBEDDER=false` + `MILVUS_HOST=milvus`. |

No further code-side Tom audit deltas are open. Cloud VMs without Docker cannot close D4/D5.

### Do not redo

- Anthropic/LangGraph pivot (done). Groq/Gemini are gone from live code.
- `mcp/` rename — package is `mcp_server/` (ADR 0006; #30 closed via #44).
- Earendil LLM planner — intentional keyword/regex executor.

### Key references

- Commands / architecture: `CLAUDE.md`, `README.md`
- Tom behavior + audit: `docs/superpowers/specs/2026-05-10-tom-bombadil-behavior-spec.md`, `...-audit.md`
- Operator profiles: `docs/cutover.md`, `docs/tombombadil-memory.md`, `scripts/verify-d4-d5.sh`
- Decisions: `docs/decisions/` (esp. ADR 0006)

### Suggested first prompt for a successor agent

> On the deploy host, enable and verify D4+D5 (`docker compose --profile cron` / `milvus`, then `./scripts/verify-d4-d5.sh`). Close #21/#22 when checks pass. Do not reopen Groq/Gemini or rename `mcp_server/`.
