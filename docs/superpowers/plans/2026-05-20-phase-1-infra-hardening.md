# Phase 1: Infrastructure Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Get every ARDA container running on the current image, Galadriel started, and a full Telegram-to-agent round-trip verified.

**Architecture:** Rebuild the two stale containers (gwaihir + worker) against the already-rebuilt `arda:0.3.0` image, start Galadriel under the `cron` profile, add GitHub credentials to config, and run a smoke test that traces a Telegram message through Gwaihir → Sauron → Earendil → reply.

**Tech Stack:** Docker Compose, Python 3.12, Pydantic Settings, pytest, fakeredis

---

## File Map

| Action | File | Change |
|---|---|---|
| Modify | `core/config.py` | Add `github_token` + `github_username` settings |
| Modify | `.env.example` | Add `GITHUB_TOKEN=` + `GITHUB_USERNAME=` keys |
| Modify | `tests/test_core_smoke.py` | Add assertion for new config fields |

Everything else in Phase 1 is operational (SSH commands on home server).

---

### Task 1: Rebuild stale containers

**Files:** None (operational only)

- [ ] **Step 1: SSH to home server**

```bash
ssh solomon@100.112.3.116
```

- [ ] **Step 2: Confirm current image tag for worker and gwaihir**

```bash
cd /home/solomon/Code/arda-stack/arda
docker compose ps --format "table {{.Name}}\t{{.Image}}"
```

Expected: `arda-worker-1` and `arda-gwaihir-1` show stale SHA hashes, not `arda:0.3.0`.

- [ ] **Step 3: Force-recreate worker and gwaihir against current image**

```bash
docker compose up -d --force-recreate worker gwaihir
```

Expected output includes:
```
Container arda-worker-1 Recreated
Container arda-gwaihir-1 Recreated
```

- [ ] **Step 4: Verify both show arda:0.3.0**

```bash
docker compose ps --format "table {{.Name}}\t{{.Image}}"
```

Expected: all five services show either `arda:0.3.0` or `redis:7-alpine`.

- [ ] **Step 5: Check worker and gwaihir logs for clean startup**

```bash
docker compose logs worker --since 1m | tail -10
docker compose logs gwaihir --since 1m | tail -10
```

Expected for worker: no errors, idle polling messages.
Expected for gwaihir: long-poll GET to Telegram API repeating.

---

### Task 2: Start Galadriel

**Files:** None (operational only)

- [ ] **Step 1: Start Galadriel under the cron profile**

```bash
cd /home/solomon/Code/arda-stack/arda
docker compose --profile cron up -d galadriel
```

- [ ] **Step 2: Watch for the startup log line**

```bash
docker compose logs -f galadriel
```

Wait for:
```json
{"event": "galadriel_worker_starting", "base_url": "http://api:5000", ...}
```

Press Ctrl+C once you see it. If you see errors instead, note the error message and stop -- do not proceed to Task 3.

- [ ] **Step 3: Verify Galadriel is polling cleanly**

```bash
docker compose logs galadriel --since 30s | tail -5
```

Expected: no errors, quiet (no jobs seeded yet so nothing to execute).

---

### Task 3: Add GitHub config to core/config.py

**Files:**
- Modify: `core/config.py`
- Modify: `.env.example`
- Modify: `tests/test_core_smoke.py`

- [ ] **Step 1: Write failing test**

Open `tests/test_core_smoke.py`. Add at the end:

```python
def test_github_config_fields_exist():
    from core.config import Settings
    s = Settings()
    assert hasattr(s, "github_token")
    assert hasattr(s, "github_username")
    assert s.github_username == "SolomonSmith-dev"  # default
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run pytest tests/test_core_smoke.py::test_github_config_fields_exist -v
```

Expected: `FAILED — AttributeError: 'Settings' object has no attribute 'github_token'`

- [ ] **Step 3: Add the fields to core/config.py**

In `core/config.py`, find the block with `telegram_bot_token` and add after it:

```python
    # GitHub (Earendil GitHub audit tool)
    github_token: str = ""
    github_username: str = "SolomonSmith-dev"
```

- [ ] **Step 4: Run test to confirm it passes**

```bash
uv run pytest tests/test_core_smoke.py::test_github_config_fields_exist -v
```

Expected: `PASSED`

- [ ] **Step 5: Add keys to .env.example**

In `.env.example`, add after the telegram block:

```
# GitHub (Earendil GitHub audit tool)
GITHUB_TOKEN=
GITHUB_USERNAME=SolomonSmith-dev
```

- [ ] **Step 6: Set token on home server**

On the home server, add the GitHub PAT (needs `repo` + `read:user` scopes):

```bash
# On home server:
cd /home/solomon/Code/arda-stack/arda
echo "GITHUB_TOKEN=<your-pat-here>" >> .env
echo "GITHUB_USERNAME=SolomonSmith-dev" >> .env
```

Verify it was written:
```bash
grep GITHUB .env
```

- [ ] **Step 7: Run full test suite to confirm nothing broke**

```bash
uv run pytest --tb=short -q
```

Expected: all existing tests pass.

- [ ] **Step 8: Commit**

```bash
git add core/config.py .env.example tests/test_core_smoke.py
git commit -m "feat(config): add github_token and github_username settings"
```

---

### Task 4: End-to-end Telegram smoke test

**Files:** None (operational only)

- [ ] **Step 1: Get your ARDA API key**

```bash
# On home server:
ARDA_API_KEY=$(grep ^ARDA_API_KEY /home/solomon/Code/arda-stack/arda/.env | cut -d= -f2)
echo $ARDA_API_KEY
```

- [ ] **Step 2: Hit the health endpoint to confirm API is up**

```bash
curl -s http://localhost:5000/health | python3 -m json.tool
```

Expected: `{"status": "ok"}` or similar.

- [ ] **Step 3: Send a test message through /execute/wait**

```bash
curl -s -X POST http://localhost:5000/execute/wait \
  -H "x-api-key: $ARDA_API_KEY" \
  -H "content-type: application/json" \
  -d '{"message": "uptime"}' | python3 -m json.tool
```

Expected: response with `"status": "completed"` and output containing uptime data.

- [ ] **Step 4: Send a Telegram message to your bot and verify it replies**

Open Telegram, find the Gwaihir bot, send: `uptime`

Expected: bot replies with the server uptime string within 10 seconds.

If the bot doesn't reply within 30 seconds:
```bash
docker compose logs gwaihir --since 2m | tail -20
docker compose logs api --since 2m | tail -20
```
Check for error lines and note them before proceeding.

- [ ] **Step 5: Verify Sauron LLM routing is active**

```bash
grep CLAUDE_API_KEY /home/solomon/Code/arda-stack/arda/.env
```

If the value is empty, Sauron uses regex routing (still works, just less smart). Set it if you have an Anthropic key available:

```bash
echo "CLAUDE_API_KEY=<your-key>" >> .env
docker compose up -d --force-recreate api
```

If no key is available, leave it blank -- regex routing handles all Phase 2 traffic correctly.

---

### Task 5: PR

- [ ] **Step 1: Confirm all tests pass locally**

```bash
uv run pytest --tb=short -q
```

Expected: all pass, no errors.

- [ ] **Step 2: Push branch and open PR**

```bash
git push origin HEAD
gh pr create \
  --title "Phase 1: infrastructure hardening" \
  --body "Rebuilds stale gwaihir/worker containers, starts Galadriel, adds GitHub config fields. All 5 services now run on arda:0.3.0. Telegram smoke test passes end-to-end." \
  --base main
```

- [ ] **Step 3: Verify CI passes on the PR**

```bash
gh pr checks
```

Wait for green. If any check fails, fix it before merging.
