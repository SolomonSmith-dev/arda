# ARDA Production Launch Design

**Date:** 2026-05-20
**Author:** Solomon Smith
**Status:** Approved — ready for implementation planning

---

## Goal

Get ARDA running as a daily personal tool and a portfolio-quality system.
Daily use means: waking up to a GitHub activity brief on Telegram, asking
the bot questions and getting real answers, and eventually having it
discover OSS opportunities and tailor resumes autonomously.

Portfolio quality means: clean PRs, tested code, design docs, and a README
that shows the full system working end-to-end.

---

## Current State Audit

### Running containers (as of 2026-05-20)

| Service | Image | Status | Problem |
|---|---|---|---|
| `arda-api-1` | `arda:0.3.0` (fresh) | Healthy | None |
| `arda-tommodbadil-1` | `arda:0.3.0` (fresh) | Running | None |
| `arda-redis-1` | `redis:7-alpine` | Healthy | None |
| `arda-gwaihir-1` | Stale SHA (11 days) | Running | Old image, missing recent code |
| `arda-worker-1` | Stale SHA (6 days) | Running | Old image, missing recent code |
| `arda-galadriel-1` | — | Not started | Never brought up |

### Agent code status

| Agent | Code | Gaps |
|---|---|---|
| Sauron | LLM classifier exists (`classify_llm` via Claude Haiku), falls back to regex | Needs `CLAUDE_API_KEY` set; no GitHub audit intent |
| Earendil | Keyword planner routes messages to shell commands | No GitHub API tool |
| Finrod | VectorStore protocol + InMemoryStore working | No GitHub snapshot schema |
| Galadriel | Full cron/at scheduler, Redis-backed, Telegram delivery wired | Never started; no seed jobs |
| Gwaihir | Long-poll Telegram bot, routes to `/execute/wait` | Stale image |
| Tom Bombadil | Discord film club bot, Letterboxd loaded (903 films) | Working |

---

## Approach

**Two phases. No features until infra is honest.**

**Phase 1 — Infrastructure hardening:** Rebuild all stale containers, start
Galadriel, verify every agent is reachable, confirm Sauron LLM routing is
active, and run one end-to-end smoke test via Telegram.

**Phase 2 — GitHub Activity Audit:** One complete workflow exercising all
five non-Tom agents. Earendil fetches, Sauron summarizes, Finrod stores,
Galadriel schedules, Gwaihir delivers. After this ships, every future
workflow (OSS discovery, resume applications, workflow audits) is the same
four-file pattern with different tools.

---

## Phase 1: Infrastructure Hardening

### 1.1 Rebuild stale images

`gwaihir` and `worker` are running on images from before the recent
refactors. They must be rebuilt from the current `arda:0.3.0` image.

```bash
cd /home/solomon/Code/arda-stack/arda
docker compose up -d --force-recreate worker gwaihir
```

Both services share the single `arda:0.3.0` image built in Phase 1. The
image was already rebuilt `--no-cache` earlier in this session. The
recreate is all that's needed.

### 1.2 Start Galadriel

```bash
docker compose --profile cron up -d galadriel
docker compose logs -f galadriel
# Expected: {"event": "galadriel_worker_starting", ...}
```

Galadriel polls `cron:queue` every 5 seconds. No jobs are seeded yet; it
will idle cleanly until Phase 2 seeds the GitHub audit job.

### 1.3 Verify Sauron LLM routing

`agents/sauron/planner.py` already has `classify_llm` using Claude Haiku
(`claude-haiku-4-5-20251001`). It activates when `CLAUDE_API_KEY` is set.

Check the home server `.env`:
```bash
grep CLAUDE_API_KEY /home/solomon/Code/arda-stack/arda/.env
```

If missing or empty, set it. No code change needed -- the fallback to
regex routing is already wired.

### 1.4 Add GitHub config

Add to `core/config.py` settings:
- `github_token: str = ""` — personal access token with `repo` + `read:user` scopes
- `github_username: str = "SolomonSmith-dev"` — default target account

Add to `.env.example`:
```
GITHUB_TOKEN=
GITHUB_USERNAME=SolomonSmith-dev
```

Set the actual token in `/home/solomon/Code/arda-stack/arda/.env` on the
home server.

### 1.5 End-to-end smoke test

Send a Telegram message through Gwaihir. Confirm it reaches `/execute/wait`,
routes through Sauron, dispatches to an agent, and returns a reply. If this
works, every layer is connected.

---

## Phase 2: GitHub Activity Audit Workflow

### Data flow

```
[Galadriel] 8am PT cron fires
      ↓  agentTurn: "Run daily GitHub audit for SolomonSmith-dev"
[Sauron] classifies as GITHUB_AUDIT
      ↓  dispatch to Earendil
[Earendil] calls GitHub API → returns GitHubSnapshot
      ↓  snapshot back to Sauron
[Sauron] calls Finrod to store snapshot
         calls Gemini to generate narrative
      ↓  result with narrative
[Galadriel] delivers via Telegram → [Gwaihir] → phone
```

Pull path:
```
Telegram: "what did I do this week?"
      ↓
[Gwaihir] → /execute/wait → [Sauron]
      ↓  classifies as GITHUB_AUDIT (week scope)
[Finrod] searches for snapshots from last 7 days
      ↓  summaries
[Sauron] Gemini weekly narrative → reply via Gwaihir
```

### 2.1 GitHub data fetcher: `agents/earendil/tools/github.py`

New module. Uses `httpx` (already a dependency) against GitHub API v3.
No new packages needed.

**Data model:**

```python
@dataclass
class CommitSummary:
    repo: str
    message: str
    sha: str
    created_at: str

@dataclass
class PrSummary:
    repo: str
    title: str
    number: int
    state: str  # "open" | "merged" | "closed"

@dataclass
class GitHubSnapshot:
    date: str           # ISO date YYYY-MM-DD
    username: str
    commits: list[CommitSummary]
    prs_opened: list[PrSummary]
    prs_merged: list[PrSummary]
    issues_opened: list[str]   # repo#number titles
    streak_days: int
    total_contributions: int
```

**Function:**
```python
def fetch_activity(username: str, date: str, token: str) -> GitHubSnapshot
```

Uses the GitHub Search API (`/search/commits`, `/search/issues`) with
`author:username created:YYYY-MM-DD` filters. Streak calculation uses
the user events feed (`/users/{username}/events`).

Token goes in `Authorization: Bearer {token}` header. Unauthenticated
calls are rate-limited to 60/hour; authenticated to 5000/hour.

### 2.2 Earendil GitHub audit handler

Update `agents/earendil/agent.py`:

When the message matches the pattern `github.audit` (new helper in
`context_trimmer.py` or inline in `plan_task`), call `tools/github.py`
instead of shelling a command.

Return value is the `GitHubSnapshot` serialized to JSON, surfaced as
the task result's `output` field. Sauron reads this to generate the
summary.

New message detection patterns (added to `plan_task`):
- `"github audit"` / `"github activity"` / `"daily audit"`
- Galadriel's scheduled message: `"Run daily GitHub audit for {username}"`

### 2.3 Sauron compound intent: GITHUB_AUDIT

**Classifier update (`agents/sauron/planner.py`):**

GitHub audit routes to Earendil -- no new `Specialist` value needed.
The existing regex pattern `git|commit|push|pull` already matches, but
the LLM prompt needs one explicit line so it never sends audit messages
to Finrod:

```
- earendil: shell ops, server diagnostics, AND GitHub activity audits.
  When the message asks to run a daily GitHub audit, fetch GitHub activity,
  or check commits/PRs, always route to earendil.
```

**Sauron summary step (`agents/sauron/agent.py`):**

After Earendil returns a result, detect if the result payload contains
a `github_snapshot` field. If it does, Sauron makes a second Gemini call:

```
Prompt: "Here is a developer's GitHub activity for {date}.
Write 3-5 sentences: what they shipped, what's in progress, and a
streak/momentum note. Be specific. Use the actual repo names and
commit messages. Do not pad with generic filler.

Activity data: {snapshot_json}"
```

The narrative replaces `result.output` before returning to Galadriel.

This is the only place where Sauron makes two sequential calls. It is
scoped to `github_snapshot` results specifically.

### 2.4 Finrod snapshot storage

Update `agents/finrod/ingest.py`:

```python
def ingest_github_snapshot(snapshot: GitHubSnapshot, summary: str) -> None
```

Stores the summary text as a vector chunk with metadata:
```python
{
    "type": "github_snapshot",
    "date": snapshot.date,
    "username": snapshot.username,
}
```

The chunk text is the narrative summary so semantic search ("what did I
work on last Tuesday?") retrieves the right snapshot.

Called from `agents/sauron/agent.py` after the Gemini summary is generated,
before the result is returned to Galadriel. Fire-and-forget with a logged
warning on failure.

### 2.5 Galadriel seed job

New module `agents/galadriel/seed.py`:

```python
def seed_default_jobs(redis, telegram_chat_id: str) -> None:
    """Create default jobs if they don't exist in Redis."""
```

Seeded at Galadriel worker startup (called once from `run_forever`).

Jobs seeded:

```python
{
    "name": "github-daily-audit",
    "schedule": {
        "kind": "cron",
        "expr": "0 8 * * *",
        "tz": "America/Los_Angeles"
    },
    "payload": {
        "kind": "agentTurn",
        "message": "Run daily GitHub audit for SolomonSmith-dev",
        "timeout_seconds": 60
    },
    "delivery": {
        "mode": "telegram",
        "to": "{TELEGRAM_CHAT_ID}"
    }
}
```

`TELEGRAM_CHAT_ID` is read from `settings.telegram_allowed_chat_ids`
(first value). If empty, job is seeded without delivery (logs only).

Idempotent: checks for job by name before creating.

### 2.6 Telegram output format

Galadriel's existing `_format_announcement` extracts the result body and
formats it as `[{job_name}] {body}`. For the GitHub audit, `body` is the
Gemini narrative, which is already human-readable prose.

Example output delivered to Telegram:
```
[github-daily-audit] You pushed 4 commits to arda today — anti-hallucination
patch for Tom Bombadil and a Docker image rebuild fix. 1 PR opened (#31).
You're on a 12-day streak. soc-triage-ai and llm-from-scratch had no activity.
```

No Gwaihir changes needed.

---

## The Replication Pattern

After Phase 2, each new workflow is:

| File | What changes |
|---|---|
| `agents/earendil/tools/<workflow>.py` | New data fetcher |
| `agents/earendil/agent.py` | New message pattern detected → calls tool |
| `agents/sauron/planner.py` | Classifier prompt updated |
| `agents/finrod/ingest.py` | New `ingest_<workflow>_snapshot` function |
| `agents/galadriel/seed.py` | New seed job added |

**Phase 3 — OSS Discovery:**
Earendil tool searches GitHub for repos tagged `good-first-issue` in
Python/TypeScript, active in last 30 days, matching Solomon's skill
profile. Weekly cron. Delivers a ranked list of 5 opportunities.

**Phase 4 — Resume Applications:**
Earendil tool reads a job description URL or text, reads the master
resume from a mounted volume, calls Sauron (Gemini) to generate tailored
bullet points. On-demand via Telegram. Finrod stores each application
for tracking. Delivers "ready to apply" copy.

**Phase 5 — Workflow Audit:**
Earendil tool reads `git log --since=7days` across `~/Projects/` (via
SSH to home server or local execution), ARDA logs, and task history.
Sauron generates a weekly "what you worked on, what stalled, what's
next" narrative. Delivered Sunday mornings.

---

## Git and Portfolio Strategy

Each phase ships as one PR with:
- Spec document in `docs/superpowers/specs/`
- Tests in `tests/` (mock mode, no real API calls)
- `README.md` updated with one working example showing real output
- Clean commit history (no WIP or fix-up commits in the PR)

Branch naming: `feature/phase-1-infra`, `feature/phase-2-github-audit`,
`feature/phase-3-oss-discovery`, etc.

The README "Example output" section is load-bearing for portfolio use. An
engineer reading the README should be able to see the Telegram message,
understand the data flow, and trace it to the code in under 5 minutes.

---

## Open Questions (resolved before implementation)

1. **CLAUDE_API_KEY on home server** — is it set? If not, Sauron falls
   back to regex routing, which still works for GitHub audit (git patterns
   match). Confirm before Phase 2.

2. **GitHub token scopes** — needs `repo` (for private repos) and
   `read:user`. If only tracking public repos, `public_repo` suffices.

3. **Telegram chat ID** — `TELEGRAM_ALLOWED_CHAT_IDS` in `.env` should
   already contain Solomon's personal chat ID. Confirm it's set before
   seeding the Galadriel job.

4. **Finrod vector store persistence** — InMemoryStore is lost on restart.
   For Phase 2, this is acceptable (snapshot is regenerated nightly). Phase
   3+ should evaluate whether to enable Milvus persistence or switch to a
   Redis-backed store for snapshots.
