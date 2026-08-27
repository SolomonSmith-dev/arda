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

### Deploy host reality (verified 2026-08-27)

`home-server` (`100.112.3.116`, `/home/solomon/Code/arda-stack/arda`) **is now
on `main`** and rebuilt. It had been on `claude/pr-6-hardening` for two months;
that branch is preserved at `origin/claude/pr-6-hardening` and locally as
`prod-backup-20260827`. Nothing unique was lost -- every prod-only commit was
either superseded by `main` or ported (#60, #62).

Verified live after the deploy:

- `/agents/health` reports `sauron: claude-opus-5/anthropic`,
  `earendil: none/none`, `finrod` and `tombombadil` on
  `claude-haiku-4-5-20251001/anthropic`. Before the deploy it reported
  `gemini-2.5-flash/google` and `meta-llama/llama-4-scout/groq`.
- The Letterboxd export merges: `entries: 903, rated: 900` -> `films: 903`.
  It had **never** run here, because `LETTERBOXD_EXPORT_DIR` was unset, so Tom
  had been answering from the 4-film seed catalogue.
- `cron:job:tom_letterboxd_sync` is seeded. D4 checks pass.

### The deploy host CPU is pre-2010 -- this constrains dependencies

**Intel Core 2 Duo P8600 (2008 Penryn), 7.5 GiB RAM.** Measured flags:

| flag | present |
|---|---|
| `sse4_2` | **no** |
| `popcnt` | **no** |
| `avx` | **no** |
| `avx2` | **no** |

Consequences a future agent must not "helpfully" undo:

- **`numpy<2` is pinned in `pyproject.toml` and must stay pinned** while this
  host is in use. NumPy 2.x ships wheels built for the `x86-64-v2` baseline,
  which needs `popcnt` + `sse4_2`. Importing it here is a hard
  `RuntimeError: NumPy was built with baseline optimizations: (X86_V2)`, and it
  crash-looped the API on the first deploy of `main`. numpy arrives
  transitively via `llama-index-core`, which is why the pre-LlamaIndex branch
  never hit it.
- **Milvus documents SSE4.2 as a hard minimum.** This CPU does not have it, so
  #22 (D5) is very likely **not achievable on this hardware at all** --
  it is a machine problem, not a config problem. Confirm before spending time
  on `MILVUS_HOST` / `USE_MOCK_EMBEDDER` changes.
- Real embeddings (`[full]`, sentence-transformers) pull torch, which has no
  AVX to use here. Treat `USE_MOCK_EMBEDDER=false` as unproven on this host.

If the deploy host is ever replaced with anything post-2010, revisit all three.

### Other host facts

- The deploy host has **no GitHub credentials** -- it cannot push. It *can*
  fetch: the repo is public, so `git fetch https://github.com/SolomonSmith-dev/arda.git main`
  needs no auth.
- Reaching it needs Tailscale up. Use `/usr/bin/ssh`; Homebrew's `ssh` rejects
  `UseKeychain`.
- `scripts/reconcile-deploy-host.sh` automates the switch with a `.env`
  preflight. On a host's **first** reconcile the script is not there yet (it
  ships in the commits being deployed), so pipe it in:
  `cat scripts/reconcile-deploy-host.sh | ssh HOST 'bash -s -- --repo <path> --dry-run'`.
- `./scripts/verify-d4-d5.sh` races API startup. The Letterboxd cron job is
  seeded by the API lifespan, so run it a minute *after* `docker compose up`,
  not immediately.

### Remaining work (operator-only)

| Priority | Item | Type | Notes |
|---|---|---|---|
| 1 | [#22 D5](https://github.com/SolomonSmith-dev/arda/issues/22) Milvus | **Operator** | Blocked on hardware, probably permanently. See the CPU section above before attempting. |

D4 (#21) is closed: verified on the host 2026-08-27. No code-side Tom audit
deltas are open. Cloud VMs without Docker cannot close D5 either.

### Do not redo

- Anthropic/LangGraph pivot (done). Groq/Gemini are gone from live code.
- `mcp/` rename — package is `mcp_server/` (ADR 0006; #30 closed via #44).
- Earendil LLM planner — intentional keyword/regex executor.
- **Unpinning `numpy<2`.** It is not a stale pin. See the CPU section above.
- Reconciling the deploy host — done 2026-08-27, it is on `main`.

### Key references

- Commands / architecture: `CLAUDE.md`, `README.md`
- Tom behavior + audit: `docs/superpowers/specs/2026-05-10-tom-bombadil-behavior-spec.md`, `...-audit.md`
- Operator profiles: `docs/cutover.md`, `docs/tombombadil-memory.md`, `scripts/verify-d4-d5.sh`
- Decisions: `docs/decisions/` (esp. ADR 0006)

### Suggested first prompt for a successor agent

> The deploy host is on `main` and healthy as of 2026-08-27; D4 is closed. Open work is in GitHub issues — #22 (D5/Milvus, likely blocked by the host CPU: read "The deploy host CPU is pre-2010" first) and the #66 epic (daily GitHub audit to Telegram, prerequisites already merged). Do not unpin `numpy<2`, reopen Groq/Gemini, or rename `mcp_server/`.
