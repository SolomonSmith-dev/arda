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

1. YAML owner entry by `discord_id` → `Tier.SOLOMON`.
2. YAML regulars entry by `discord_id` → `Tier.REGULAR`.
3. Case-insensitive name match against `FILM_DATABASE['people']` →
   `Tier.REGULAR` (or `Tier.SOLOMON` for "Solomon Smith").
4. Fallback → `Tier.STRANGER` with `canonical_name=None`.

To enable identity-aware behavior in production, populate
`data/tombombadil/identity.yaml` with real Discord snowflake IDs
(Developer Mode → right-click user → "Copy User ID").

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
