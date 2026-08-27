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

### Deploy host reality (verified 2026-08-02)

**The deploy host does not run `main`.** `home-server` (`100.112.3.116`,
`/home/solomon/Code/arda-stack/arda`) is checked out on
`claude/pr-6-hardening`, **34 commits behind `main` and 6 ahead**. Containers
have been up ~2 months. Until that is reconciled, treat any statement in this
file about what is "live" as describing `main`, not production.

Consequences for the operator items below:

- The `cron` profile is **already up** — `galadriel` has been running for two
  months, so `docker compose --profile cron up -d` is a no-op that exits 0 and
  proves nothing.
- D4's verification criterion (cron job seeded by the API lifespan) and
  `scripts/verify-d4-d5.sh` both live in the 34 commits prod does not have.
  **D4/D5 cannot be closed without first deploying `main`.**
- The 6 prod-only commits are backed up to `origin/claude/pr-6-hardening`
  (pushed 2026-08-02; the remote branch did not previously exist). Most are
  superseded by `main`; see that branch before assuming a deploy is lossless.
- The deploy host has **no GitHub credentials** — it cannot push. Relay
  through a workstation with `git fetch ssh://solomon@100.112.3.116/...`.
- Reaching the host needs Tailscale up **and** an interactive Tailscale SSH
  browser check. Use `/usr/bin/ssh`; Homebrew's `ssh` rejects `UseKeychain`.

### Remaining work (operator-only)

| Priority | Item | Type | Notes |
|---|---|---|---|
| 0 | Reconcile prod onto `main` | **Operator** | Blocks #21 and #22. See "Deploy host reality" above. |
| 1 | [#21 D4](https://github.com/SolomonSmith-dev/arda/issues/21) Galadriel cron | **Operator** | Profile already up; needs `main` deployed before `./scripts/verify-d4-d5.sh` exists to verify it. |
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

> Reconcile the deploy host onto `main` (it runs `claude/pr-6-hardening`, 34 behind / 6 ahead — read "Deploy host reality" first). Only then can D4+D5 be verified with `./scripts/verify-d4-d5.sh` and #21/#22 closed. Do not reopen Groq/Gemini or rename `mcp_server/`.
