# Mac Mini Cutover Runbook

ARDA replaces the legacy Earendil API on the Mac Mini at
`http://100.112.3.116:5000`. The host and port stay the same; the
process behind them is now `api.main:app` running in Docker.

The MCP server (`mcp_server/server.py`) and any other clients hitting
that host keep working because every endpoint contract they read
from is preserved (see `api/routes/`).

## Prereqs on the Mac Mini

- Docker Desktop or OrbStack running (arm64).
- `git` access to this repo, or a tarball of the source tree.
- `.env` file populated with **`USE_MOCK_LLM=false`**, plus
  `ANTHROPIC_API_KEY` and `ARDA_API_KEY` (or
  `EARENDIL_API_KEY` to match the value the existing MCP client sends).

## Build

```bash
cd /path/to/arda
docker build -t arda:0.3.0 .
```

The default image skips `sentence-transformers` and torch, so Finrod
uses `MockEmbedding` and the image stays near 400 MB.

The host is `home-server`, Debian 12, x86_64, a Core 2 Duo with 7.5 GB
of RAM. Torch runs on that CPU. A torch 2.13.0 CPU wheel completed a
real matmul there, and the CPU has no AVX.

Real semantic embeddings need the `embeddings` extra. Set
`ARDA_EXTRAS=embeddings` and `USE_MOCK_EMBEDDER=false` in `.env`.
Rebuild with the command below. Compose passes `ARDA_EXTRAS` into the
image. The `docker build` command above stays slim, because it does not
read `.env`. The Dockerfile already selects extras from `ARDA_EXTRAS`.
The extra adds `sentence-transformers` and torch. Finrod keeps vectors
in the in-memory
`SimpleVectorStore`. Issue #85 records about 1 GB of image growth from
torch.

```bash
docker compose up -d --build
```

Issue #85 records about 4 GB free on this host. It also records
`all-MiniLM-L6-v2` at about 90 MB of weights and about 400 MB resident.
Every process that builds a Finrod loads a copy. Run `free -h` after
the first query. That query downloads the model from the Hugging Face
Hub. Without `HF_TOKEN`, the Hub warns about rate limits.

Keep the `numpy<2` pin in `pyproject.toml`. NumPy 2 wheels need
`popcnt` and `sse4_2`. This CPU has neither. Importing NumPy 2 raises
`RuntimeError` for baseline `X86_V2`. That import crash-looped the API
on the first deploy of `main`.

Milvus documents SSE4.2 as a hard minimum. This CPU does not have it,
so D5 is very likely not achievable on this hardware. Starting the
`milvus` profile was not tested, and it also risks running out of
memory. The `[full]` extra pulls `pymilvus` for that server. Real
embeddings use the `embeddings` extra, as above. See the Milvus note
below and AGENTS.md, "The deploy host CPU is pre-2010". Issue #85 lists
the host commands.

On the slim image, with `USE_MOCK_LLM=false`, set `USE_MOCK_EMBEDDER=true`.
When `USE_MOCK_EMBEDDER` is absent, it follows `USE_MOCK_LLM`.

## Cutover

1. **Stop the legacy stack.**

   ```bash
   sudo systemctl stop earendil-worker
   # also stop whatever supervises the legacy /home/.../earendil_api.py
   ```

   Confirm port 5000 is free: `sudo lsof -i :5000` should be empty.

2. **Bring up the new stack.**

   ```bash
   docker compose up -d
   docker compose logs -f api  # watch for "agents_registered"
   ```

3. **Smoke from the dev box.**

   ```bash
   curl -s http://100.112.3.116:5000/health
   # {"status":"online","agent":"earendil","version":"0.3.0"}

   curl -s -X POST http://100.112.3.116:5000/query \
     -H "x-api-key: $ARDA_API_KEY" \
     -H "content-type: application/json" \
     -d '{"type":"system","action":"status"}'
   # {"api":"online","redis":"connected","worker":"containerized",...}
   ```

4. **Exercise the MCP tools.** From a Claude Code session with the
   ARDA MCP server configured, run `arda_status`, `arda_execute`
   with `whoami`, and `arda_plan` with a film note. All four tools
   must return shapes identical to what the legacy API returned —
   the new server is a drop-in replacement.

## Rollback

```bash
docker compose down
sudo systemctl start earendil-worker
# restart the legacy FastAPI process the way you used to
```

The ``legacy_api/earendil_api.py`` rollback artifact has been removed; ``api/main.py`` is now canonical and the MCP server points at it via ``ARDA_API_URL`` (see ``.env.example``). Rollback via git revert if needed.

## Notes

- **Tom Bombadil bot** is gated behind the `discord` profile. Bring it up
  with `docker compose --profile discord up -d` after setting
  `DISCORD_TOKEN` in `.env`.
- **Galadriel cron (D4):** enable with `docker compose --profile cron up -d`.
  The API lifespan seeds `tom_letterboxd_sync` into Redis automatically;
  Galadriel must be running for the job to fire. Owner can also run
  `/sync` in Discord.
- **Real embeddings.** Set `ARDA_EXTRAS=embeddings` and
  `USE_MOCK_EMBEDDER=false`, then rebuild. Finrod uses
  `sentence-transformers` against the in-memory `SimpleVectorStore`.
- **Milvus (D5).** Milvus documents SSE4.2 as a hard minimum, and this
  CPU has none, so D5 is very likely not achievable here. Starting the
  `milvus` profile was not tested, and it also risks running out of
  memory. The `[full]` extra pulls `pymilvus` for that server. When
  Milvus is unreachable, Finrod uses the in-memory store, and those
  facts disappear when the container is recreated.

## Step 0: reconcile the host onto `main`

`home-server` has been running `claude/pr-6-hardening` for months, a tree
that predates the LangGraph orchestrator, the LlamaIndex Finrod migration,
and CI. **`scripts/verify-d4-d5.sh` does not exist on that branch**, so D4
and D5 cannot be verified until the host is on `main`. See AGENTS.md
"Deploy host reality".

On a host that already has the script:

```bash
ssh solomon@100.112.3.116          # /usr/bin/ssh; expect a Tailscale browser check
cd /home/solomon/Code/arda-stack/arda
./scripts/reconcile-deploy-host.sh --dry-run   # read the plan first
./scripts/reconcile-deploy-host.sh
```

**On a host's FIRST reconcile the script is not there yet** -- it ships in the
very commits being deployed. Copying it into the checkout does not help: it
would be an untracked file, and git refuses a checkout that would overwrite
one. Pipe it in and name the repo instead:

```bash
cat scripts/reconcile-deploy-host.sh \
  | ssh solomon@100.112.3.116 'bash -s -- --repo /home/solomon/Code/arda-stack/arda --dry-run'
```

The script backs up the current HEAD to a `prod-backup-<timestamp>` branch,
checks that `.env` has a non-empty `ARDA_API_KEY` (the API fails closed at
import without it), preserves whichever compose profiles are already
running, rebuilds, and health-checks. It prints a rollback command at every
step and never pushes, since the host has no GitHub credentials. The repo is
public, so its fetch needs none.

## Operator verification (D4 / D5)

On the **deploy host**, after step 0 (Docker is required; cloud dev VMs
without Docker cannot close these). From the repo root:

```bash
# Enable profiles
docker compose --profile cron --profile discord up -d          # D4 (+ Tom)
# D5 is very likely not achievable here. This CPU has no SSE4.2, and
# starting Milvus was not tested. The profile also risks running out of memory.
docker compose --profile milvus up -d                          # D5

# .env
# Real embeddings need ARDA_EXTRAS=embeddings and USE_MOCK_EMBEDDER=false,
# then a rebuild. The [full] extra pulls pymilvus. Milvus needs SSE4.2.
# Keep numpy<2 pinned.
# MILVUS_HOST=milvus
# MILVUS_PORT=19530

# Checklist script
./scripts/verify-d4-d5.sh
```

Manual acceptance checks (from issues #21 / #22):

| Delta | Check |
|---|---|
| D4 | `docker compose ps galadriel` shows running |
| D4 | `cron:queue` drains (due jobs disappear after their `at`/`cron` time) |
| D4 | Letterboxd sync runs once (or owner `/sync`); film DB updates |
| D5 | `docker compose ps milvus` shows running |
| D5 | API/Tom logs show Milvus connect, not `InMemoryStore` / `pymilvus_unavailable` fallback |
| D5 | Restart `tombombadil` / `api`; previously ingested long-term facts still recall |

After both pass on the deploy host, close #21 and #22.
