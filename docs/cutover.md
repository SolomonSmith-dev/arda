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
  `GEMINI_API_KEY`, `GROQ_API_KEY`, and `ARDA_API_KEY` (or
  `EARENDIL_API_KEY` to match the value the existing MCP client sends).

## Build

```bash
cd /path/to/arda
docker build -t arda:0.3.0 .
```

Default install skips `sentence-transformers` + torch (~1GB), so
Finrod uses the lightweight `MockEmbedder` (hash-based vectors).
Image lands around ~400MB. To enable real semantic embeddings on
a beefier host, edit the `Dockerfile` to use `pip install -e .[full]`
and unset `USE_MOCK_EMBEDDER`.

The host (`home-server`, Debian 12, x86_64, Core 2 Duo, 7.5GB RAM)
cannot run torch comfortably; keep the slim build.

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

- **Tom Bombadil bot** is not started in-process. Uncomment the
  `tombombadil` service in `docker-compose.yml` and provide a
  `DISCORD_TOKEN` to bring the bot up alongside the API.
- **Milvus** is deferred to v0.4. Finrod automatically falls back to
  the in-memory store when `MILVUS_HOST` is unreachable. Document
  ingest persists for the lifetime of the API container.
- **Image size** can be trimmed later by switching to a CPU-only
  torch wheel and dropping CUDA deps from the install set.
