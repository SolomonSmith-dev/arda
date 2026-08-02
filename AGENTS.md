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
