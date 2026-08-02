# Tom Bombadil memory: operator runbook

PR 1 gives Tom two memory tiers. This doc is the spec for both — Redis
key schema, Finrod metadata shape, and recipes for wiping or
inspecting state during development.

## Identity resolution

Discord author → :class:`agents.tombombadil.identity.Viewer` lookup,
keyed by `data/tombombadil/identity.yaml` (mounted read-only at
`/app/data/tombombadil/identity.yaml`). See
`data/tombombadil/identity.yaml.example` for the schema. The file is
gitignored — only the example ships.

Resolution order:

0. Redis `/setrole` override at `tom:identity:{discord_id}` (if present).
1. YAML owner entry by `discord_id` → `Tier.SOLOMON`.
2. YAML regulars entry by `discord_id` → `Tier.REGULAR`.
3. Case-insensitive name match against `FILM_DATABASE['people']` →
   `Tier.REGULAR` (or `Tier.SOLOMON` for "Solomon Smith").
4. Fallback → `Tier.STRANGER` with `canonical_name=None`.

To enable identity-aware behavior in production, populate
`data/tombombadil/identity.yaml` with real Discord snowflake IDs
(Developer Mode → right-click user → "Copy User ID"). Owner can also
apply temporary overrides via `/setrole` without editing YAML.

## Short-term: conversation history

Redis lists, one per chat surface.

| Key                              | Type | Contains                                  |
|----------------------------------|------|-------------------------------------------|
| `tom:hist:ch:{channel_id}`       | LIST | Last 40 turns in a guild channel/thread   |
| `tom:hist:dm:{user_id}`          | LIST | Last 40 turns in a 1:1 DM with that user  |

Each list entry is JSON:

```json
{"role": "user|assistant", "viewer": "Solomon Smith",
 "discord_id": "123...", "content": "...", "ts": 1715300000}
```

- Capped at `2 * HISTORY_MAX_TURNS` entries (20 user/assistant pairs).
- 7-day idle TTL; refreshed on every push.
- Each turn's content is prefixed with `[viewer]` when injected into
  the LLM prompt, so multi-user channel history disambiguates speakers.
- Stored history should never include `[viewer]` prefixes on assistant
  turns (V6). Tom strips leaked speaker prefixes when reinjecting
  history and before persisting new replies (D1 active heal). Any
  remaining pre-V6 Redis rows also expire within the 7-day idle TTL.
  Optional audit after the TTL window:

  ```bash
  redis-cli --scan --pattern 'tom:hist:*' \
    | xargs -I{} redis-cli LRANGE "{}" 0 -1 \
    | grep '"role":"assistant".*"\[viewer\]'
  ```

DM and guild-channel scopes are deliberately separate. Personal
context shared in DMs does not leak into the club channel.

## Short-term: per-user preferences

`tom:pref:{discord_id}` HASH. Known keys:

| Key              | Values    | Effect                                                |
|------------------|-----------|-------------------------------------------------------|
| `suppress_films` | `"0"|"1"` | When `1`, Tom won't bring up films unprompted         |
| `preferred_tone` | string    | Free-text tone hint surfaced in the system prompt     |
| `do_not_log`     | `"0"|"1"` | When `1`, fact extractor skips persistence for them   |

Set automatically by `agents.tombombadil.fact_extractor` from natural
phrasings ("stop mentioning films", "for the record I prefer terse").

## Long-term: Finrod-backed fact recall

Significant facts (preferences, declared ratings, "remember that..."
asks, strong opinions) are embedded into Finrod via
`Finrod.run(action="ingest")` with metadata:

```python
{
    "viewer": "Solomon Smith",       # canonical name, used to filter
    "discord_id": "123...",
    "kind": "tom_fact",              # distinguishes from RAG docs
    "ts": 1715300000,
    "source_channel": "tom:hist:..."
}
```

Recall happens on every inbound message: Tom queries Finrod with the
user's message, then client-side filters to entries where
`metadata.kind == "tom_fact" AND metadata.viewer == viewer.canonical_name`
with `score > 0.35`. Top-K results are injected into the system prompt
as "things this user told you in earlier sessions".

Long-term memory uses Finrod's `InMemoryStore` until Milvus standalone
lands in PR 6. **Container restarts wipe long-term facts** — Solomon's
preferences need to be re-asserted after a redeploy. Short-term Redis
history is unaffected.

## Reaction-confirmed note capture (PR 2)

When a user message contains a loose-form rating ("I just watched
Inception 8/10"), :mod:`agents.tombombadil.fact_extractor` produces a
:class:`NoteDraft`. PR 2 changed the persistence flow from
auto-save-on-extraction to react-to-confirm. Two Redis keys back this:

| Key                              | Type | Lifetime           | Purpose                                                                             |
|----------------------------------|------|--------------------|-------------------------------------------------------------------------------------|
| `tom:drafts:scope:{scope_key}`   | LIST | 24h idle TTL       | Hand-off slot between `agent.get_response` and `bot.on_message`. JSON-encoded.      |
| `tom:draft:{message_id}`         | HASH | 24h after binding  | One pending draft awaiting `✅`/`❌` from the requester on `message_id`.    |

Flow:

1. User: "I rated Inception 9/10 last night"
2. `agent.get_response` extracts a `NoteDraft` and pushes it to
   `tom:drafts:scope:{scope_key}` via `draft_store.push_pending`.
3. `bot.on_message`: after sending Tom's reply, `pop_pending` returns
   the draft; the bot posts a follow-up "React `✅` to log..." and
   calls `bind_to_message(message_id=...)`.
4. `bot.on_reaction_add`: only the original requester can act.
   `✅` calls `persistent_memory.save_note` and clears the draft.
   `❌` deletes the draft. Other reactions are ignored.

Unconfirmed drafts expire silently after 24h. The rigid
`Film:/Rating:` parser path was removed in PR 2; users can no longer
log notes by typing the template, only by speaking naturally.

## Wiping state during development

```bash
# All of a user's preferences:
docker compose exec redis redis-cli DEL "tom:pref:{discord_id}"

# A specific channel's conversation history:
docker compose exec redis redis-cli DEL "tom:hist:ch:{channel_id}"

# A user's DM history:
docker compose exec redis redis-cli DEL "tom:hist:dm:{user_id}"

# Inspect what's there:
docker compose exec redis redis-cli KEYS 'tom:hist:*'
docker compose exec redis redis-cli LRANGE "tom:hist:ch:{channel_id}" 0 -1
docker compose exec redis redis-cli HGETALL "tom:pref:{discord_id}"
```

Long-term facts have no manual wipe path yet. Restart the
`tombombadil` container to clear them; per-fact `/forget` lands in
PR 3.

## Production profiles (D4 / D5)

Default `docker compose up -d` runs Redis + API + Earendil worker only.
Scheduled jobs and durable long-term memory need extra profiles **on the
deploy host**:

```bash
# Galadriel cron (Letterboxd sync + watch-party reminders)
docker compose --profile cron up -d
# API lifespan seeds tom_letterboxd_sync into Redis; Galadriel drains it.

# Discord bot
docker compose --profile discord up -d   # needs DISCORD_TOKEN

# Durable Finrod memory (Milvus) — also needs [full] install + USE_MOCK_EMBEDDER=false
docker compose --profile milvus up -d
```

`.env` for the milvus profile (compose DNS):

```bash
MILVUS_HOST=milvus
MILVUS_PORT=19530
USE_MOCK_EMBEDDER=false
```

Verify with `./scripts/verify-d4-d5.sh`, or manually:
`docker compose ps galadriel` / `docker compose ps milvus`.
Owner can force a Letterboxd sync from Discord with `/sync`.

Close GitHub #21 (D4) / #22 (D5) only after the deploy-host checks pass.

