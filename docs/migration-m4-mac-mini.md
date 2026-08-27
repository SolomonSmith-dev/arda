# Migration plan: home-server → M4/M5 Mac Mini

ARDA currently runs on `home-server` (Debian 12, Intel Core 2 Duo P8600,
7.5GB RAM, x86_64) at Tailscale `100.112.3.116:5000`. This document is
the runbook for moving the deployment to a future M4/M5 Mac Mini
(macOS, Apple Silicon arm64, 16GB+).

The system was designed to make this move boring: there is almost no
state to migrate, and the Docker image is the unit of deployment. The
trickiest piece is the architecture change (x86_64 → arm64), which
needs one fresh image build, nothing more.

## What changes vs. what doesn't

**Doesn't change:**
- Repo, branch (`main`), `.env` schema, ARDA contract, MCP tool surface
- Tailscale IP (move the device association in the admin console — no
  code changes downstream)
- Port 5000

**Changes:**
- Architecture: x86_64 → arm64. Docker image must be rebuilt for
  `linux/arm64`. Default `docker build` on the new host does the right
  thing automatically; no buildx flags needed when building on the
  target.
- CPU baseline: NumPy 2.x / pandas / torch all work natively on M4. The
  X86_V2 fallback path in `core/milvus_client.py` becomes unnecessary
  but stays as defensive code.
- OS: Debian 12 → macOS. `systemctl` no longer exists, so the
  `/query system_status` "not in Docker" branch (`api/routes/query.py:30`)
  reports `check_failed` for `worker` and `openclaw_gateway`. Inside
  Docker (which is where ARDA actually runs) this is irrelevant —
  `_running_in_docker()` short-circuits to `containerized`.
- Headroom: 7.5GB → 16GB+. This unblocks Phase 6 work
  (real `sentence-transformers`, real Milvus, LLM-based planner).

## Pre-migration checklist (do this on home-server, not the new box)

1. **Snapshot current state.** Most ARDA data is ephemeral, but record
   what's there so we can verify parity post-cutover:

   ```bash
   ssh solomon@home-server <<'EOF'
   docker compose -f ~/Code/arda-stack/arda/docker-compose.yml ps
   docker exec arda-redis-1 redis-cli dbsize
   docker exec arda-redis-1 redis-cli --scan --pattern 'task:*' | wc -l
   docker exec arda-redis-1 redis-cli --scan --pattern 'film:*' | wc -l
   curl -s http://localhost:5000/health
   curl -s -H "x-api-key: $ARDA_API_KEY" http://localhost:5000/agents/health
   EOF
   ```

2. **Decide what state to migrate.** Default answer: nothing.
   - **Redis task queue + results**: ephemeral, 5-min TTL on every key.
     Do not migrate.
   - **Finrod vector store**: in-memory only on the slim deployment.
     Re-ingest from source files post-cutover via `scripts/ingest.py`.
     If there's no canonical source, dump what's in memory before
     teardown.
   - **Tom Bombadil film history** (Redis `film:*` keys): worth
     migrating if you've been logging films. Use `redis-cli --rdb` or
     `BGSAVE` + copy the dump file.
   - **`.env`**: the LLM keys move with you, but **regenerate
     `ARDA_API_KEY`** during cutover (rotation hygiene; the old key
     leaked into the proxy logs of a half-dozen services by now).

3. **Confirm the PR/main is the deploy target.** Tag `v1.0.0` is the
   anchor. Anything beyond that should already be on `main`.

## On the new Mac Mini

### 1. Prereqs

```bash
# OrbStack is significantly lighter than Docker Desktop on Apple
# Silicon and runs the same docker / docker compose CLI.
brew install orbstack
open -a OrbStack         # complete the first-run setup
docker --version          # should report podman/orb-backed docker

# Tailscale (if not already installed)
brew install --cask tailscale
open -a Tailscale         # log in, accept the device into the tailnet
# In the Tailscale admin console: take the 100.112.3.116 IP off the
# old home-server device and give it to the new Mac Mini, OR pick a
# new IP and update the MCP server config on the dev box.
```

### 2. Clone + configure

```bash
mkdir -p ~/Code && cd ~/Code
git clone https://github.com/SolomonSmith-dev/arda.git arda-stack
cd arda-stack
git checkout v1.0.0           # or main if you want HEAD
cp .env.example .env
chmod 600 .env

# Move secrets into Keychain (one-time)
security add-generic-password -a arda -s arda-api-key  -w "$(openssl rand -hex 32)"
security add-generic-password -a arda -s anthropic-api-key -w   # paste your key
# Optional: discord-token, tmdb-api-key

# Patch .env from Keychain
ARDA_KEY=$(security find-generic-password -a arda -s arda-api-key -w)
ANTHROPIC=$(security find-generic-password -a arda -s anthropic-api-key -w)
sed -i '' "s|^ARDA_API_KEY=.*|ARDA_API_KEY=${ARDA_KEY}|"         .env
sed -i '' "s|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=${ANTHROPIC}|" .env
sed -i '' "s|^USE_MOCK_LLM=.*|USE_MOCK_LLM=false|"               .env

# On the M4 we have headroom for real embeddings — flip USE_MOCK_EMBEDDER
# off and switch the Dockerfile install line to '.[full]':
echo "USE_MOCK_EMBEDDER=false" >> .env
```

The `[full]` extra (`pyproject.toml`) pulls `sentence-transformers` +
`torch` (~1GB) and `pymilvus`. On M4 these run natively in arm64 wheels
and are fast. Edit `Dockerfile` line 24 from `pip install -e .` to
`pip install -e '.[full]'`.

### 3. Build + smoke

```bash
docker compose build api          # ~5 min on M4 vs ~13 min on home-server
docker compose up -d
sleep 8
docker compose ps
docker compose logs api | tail -20
curl -s http://localhost:5000/health
```

Expect 4 containers (redis + api + worker + redis) all healthy. If
the Tailscale IP is the new Mac Mini's IP, you should also be able
to hit it from your dev box at `http://100.112.3.116:5000/health`.

### 4. Verify the contract holds

```bash
ARDA_API_KEY=$(security find-generic-password -a arda -s arda-api-key -w)
curl -s -H "x-api-key: $ARDA_API_KEY" http://localhost:5000/agents/health \
  | python3 -m json.tool
# Confirm: sauron reports model="claude-opus-5", finrod and
# tombombadil report model="claude-haiku-4-5-20251001", and earendil
# reports model="none" (regex planner, no LLM). provider is
# "anthropic" for the three LLM tiers and "none" for earendil --
# or "mock" everywhere when USE_MOCK_LLM=true.

curl -s -X POST http://localhost:5000/execute/wait \
  -H "x-api-key: $ARDA_API_KEY" -H "content-type: application/json" \
  -d '{"message":"uptime"}' | python3 -m json.tool
# Confirm: status=completed, output contains "load average"
```

### 5. Re-ingest knowledge (if migrating Finrod content)

```bash
scripts/ingest.py --dir ~/path/to/notes \
  --api-url http://localhost:5000 --api-key "$ARDA_API_KEY"
```

### 6. Update the MCP client on the dev box

If the Tailscale IP changed, edit your Claude Code MCP config:

```json
{
  "command": "python",
  "args": ["-m", "mcp_server.server"],
  "env": {
    "EARENDIL_HOST": "http://<new-tailscale-ip>:5000",
    "EARENDIL_API_KEY": "<new-arda-api-key-from-keychain>"
  }
}
```

Restart Claude Code. Run `arda_status` and confirm it returns the
six-key system_status shape.

### 7. Optional: bring up Tom Bombadil

Per `docs/tombombadil.md`:

```bash
DISCORD_TOKEN=$(security find-generic-password -a arda -s discord-token -w) \
  docker compose --profile discord up -d tombombadil
docker compose logs -f tombombadil   # wait for bot_ready
```

## On home-server (decommission)

Only after the new Mac Mini is verified end-to-end:

```bash
ssh solomon@home-server <<'EOF'
cd ~/Code/arda-stack/arda
docker compose --profile discord down
docker image rm arda:0.3.0
# Optional: remove the source if you're sure
# rm -rf ~/Code/arda-stack
EOF
```

The legacy `redis-server.service` on home-server can stay running if
other services on that host depend on it. ARDA's compose stack used
its own isolated Redis, so nothing on home-server depended on the
ARDA containers.

## Rollback

If the new Mac Mini deployment fails verification:

1. Reassign the `100.112.3.116` Tailscale IP back to home-server in
   the admin console.
2. `ssh solomon@home-server 'docker compose -f ~/Code/arda-stack/arda/docker-compose.yml up -d'`
   (the home-server stack is left intact during this migration —
   we only tear it down after verification).
3. MCP client on the dev box still points at the same IP, so no
   client-side changes needed.

## Post-migration upgrades unlocked

The M4's headroom enables three follow-ups that were impractical on
home-server:

1. **Real semantic embeddings**: `[full]` extra + `USE_MOCK_EMBEDDER=false`.
   Replaces hash-based MockEmbedder with `sentence-transformers/all-MiniLM-L6-v2`
   (384-dim). Already supported in code — `agents/finrod/embeddings.py`
   chooses based on `mock_embedder_enabled`.
2. **Real Milvus**: Uncomment a milvus-standalone block in
   `docker-compose.yml` (etcd + minio + milvus, ~2GB RAM). Set
   `MILVUS_HOST=milvus`. Falls back to in-memory store if it fails to
   come up — already handled by `core/milvus_client.py`.
3. **LLM-based intent classifier**: Replace the keyword router in
   `agents/sauron/planner.py` with an Anthropic tool_use / Haiku call
   if ambiguous phrasing needs better coverage than the regex `/plan`
   helper. (Sauron's live routing already goes through LangGraph +
   Claude Opus tool_use — this item is only about the `/plan` helper.)

These should each be their own ADR + branch, not part of the
migration itself. Migrate first, upgrade second.
