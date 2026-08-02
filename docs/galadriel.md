# Galadriel — cron scheduler

Galadriel runs scheduled jobs on ARDA. A job is `(schedule, payload, delivery)`:

- **schedule**: when it fires — `cron` (5-field expression in a named tz) or `at` (one-shot ISO 8601 datetime)
- **payload**: what runs — `agentTurn` (full Sauron call with a message) or `systemEvent` (logged text only)
- **delivery**: where the result goes — `announce` to a chat ID, or `none`

The scheduler runs as a separate compose service gated behind the `cron` profile so plain `docker compose up -d` doesn't start it.

## Bring it up

```bash
cd ~/Code/arda-stack/arda
docker compose --profile cron up -d galadriel
docker compose logs -f galadriel
```

You should see:

```
{"event": "galadriel_worker_starting", "base_url": "http://api:5000", ...}
```

## Create a job

All routes require `X-API-Key` (the same key as the rest of ARDA).

```bash
ARDA_API_KEY=$(grep ^ARDA_API_KEY .env | cut -d= -f2)

# Daily security audit at 8am Pacific
curl -s -X POST http://localhost:5000/cron \
  -H "x-api-key: $ARDA_API_KEY" \
  -H "content-type: application/json" \
  -d '{
    "name": "daily-security-audit",
    "schedule": {"kind": "cron", "expr": "0 8 * * *", "tz": "America/Los_Angeles"},
    "payload": {"kind": "agentTurn", "message": "audit ufw status, listening ports, recent ssh logins", "timeout_seconds": 120},
    "delivery": {"mode": "announce", "to": "<chat-id>"}
  }' | python3 -m json.tool
```

Returns the full job including the server-assigned `id` and `next_run_at_ms`.

## List, inspect, delete

```bash
# All jobs
curl -s -H "x-api-key: $ARDA_API_KEY" http://localhost:5000/cron | python3 -m json.tool

# One job
curl -s -H "x-api-key: $ARDA_API_KEY" http://localhost:5000/cron/<job-id> | python3 -m json.tool

# Cancel
curl -s -X DELETE -H "x-api-key: $ARDA_API_KEY" http://localhost:5000/cron/<job-id>
```

## One-shot reminders

```bash
# Remind me in 20 minutes — set at_iso to NOW + 20m in your local tz
curl -s -X POST http://localhost:5000/cron \
  -H "x-api-key: $ARDA_API_KEY" \
  -H "content-type: application/json" \
  -d '{
    "name": "remind-stretch",
    "schedule": {"kind": "at", "at_iso": "'"$(date -u -d '+20 min' +%Y-%m-%dT%H:%M:%SZ)"'"},
    "payload": {"kind": "systemEvent", "text": "Stretch your legs."},
    "delete_after_run": true
  }'
```

`delete_after_run: true` purges the job after it fires; otherwise the job stays around with `enabled: false` so you can inspect its history.

## How it runs internally

1. `POST /cron` computes `next_run_at_ms` via the schedule and saves the job to Redis:
   - `cron:job:<id>` (string) — JSON blob of the full job
   - `cron:queue` (sorted set) — score = `next_run_at_ms`, member = job id
2. The Galadriel worker polls `cron:queue` every 5 seconds.
3. Due jobs are claimed atomically (`ZRANGEBYSCORE` + `ZREM`) so two workers can't double-fire.
4. For `agentTurn` payloads, the worker POSTs to `/execute/wait` with the message; for `systemEvent` payloads, it just logs.
5. `announce` delivery is currently a stub (logs only). Real outbound
   delivery uses `delivery.mode="telegram"` (Gwaihir) or
   `delivery.mode="discord"` (Tom Bombadil's delivery subscriber).
6. The worker writes back `last_run_at_ms`, `last_status`, `last_duration_ms`, `consecutive_errors`, then either re-queues (cron) or retires (at).

## Troubleshooting

- **Jobs never fire**: confirm the worker is running (`docker compose ps galadriel`) and that `next_run_at_ms` is in the past (`redis-cli ZRANGEBYSCORE cron:queue -inf +inf WITHSCORES`).
- **Worker can't reach the API**: check `INTERNAL_API_URL` env var. Inside compose it should be `http://api:5000`. Outside compose, set it to `http://localhost:5000`.
- **`consecutive_errors` keeps climbing**: the worker keeps retrying on schedule; check `last_error` on the job for the cause.
