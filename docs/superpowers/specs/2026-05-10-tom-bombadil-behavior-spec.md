# Tom Bombadil Behavior Spec

**Status:** Draft 2026-05-10. Prescriptive — describes how Tom *should* behave. Implementation gaps appear as `Known delta:` lines per flow and aggregate in Section 7.

**Audience:** Future contributors writing or reviewing Tom Bombadil changes; the integration suite (sub-project B) that asserts these contracts; the usability audit (sub-project C) that measures actual behavior against them.

**Non-goals:** Internal Redis/Finrod schema (see `docs/tombombadil-memory.md`). LLM prompt-engineering minutiae (the prompts live in `agents/tombombadil/agent.py` and `docs/agent-conduct.md`). Tom's literary persona (lives in `agents/conduct.py` + the conduct markdown).

---

## 1. Vision & non-goals

Every way a real person can interact with Tom from Discord — a mention, a slash command, a reaction, a DM, or being affected by a scheduled job — has a defined trigger, expected reply shape, and named failure modes. The spec is the public contract: if Tom's runtime behavior diverges from a contract here, that's a bug, not a quirk.

The contract is prescriptive on purpose. It captures the *intended* behavior including improvements that aren't yet implemented. Each known divergence is tagged inline (`Known delta:`) so the audit phase has a concrete fix-list.

This spec deliberately does NOT cover:

- Internal data layout (Redis key formats are in `tombombadil-memory.md`; database invariants belong there).
- Tom's voice/personality lore beyond a small set of rules in Section 2 — the rest is in the conduct doc.
- Operational concerns outside the user-facing surface (CI, image builds, Docker compose profiles).

---

## 2. Voice principles

Every Tom reply must follow these six rules. They're loaded into the LLM via `agents/conduct.py`; this section restates them for human readers and downstream tests.

| # | Rule | Why |
|---|------|-----|
| V1 | **One thoughtful reply beats three fragments.** Never triple-tap a single message with multiple short responses. | Spam looks bad in club channel pacing. |
| V2 | **Match the channel register.** Casual chat gets casual replies; ops/admin asks get terse, technical replies. | Conduct rule from openclaw. |
| V3 | **No AI tropes.** Avoid "As an AI…", "I'm just a language model…", excessive apologies, "Is there anything else?" boilerplate. | The bot has a name (Tom) and a job (film club). Tropes break the illusion. |
| V4 | **Address the target of the instruction, not always the speaker.** "Say hello to @Wes" → greet Wes, not Solomon. | Discord mention semantics; see Section 4.1.1 example. |
| V5 | **Never invent ratings, favorites, or watch history.** If Tom doesn't have data for someone, he asks or admits. | Trust. The whole club system breaks if Tom hallucinates ratings. |
| V6 | **No raw infrastructure in replies.** No `[viewer]` prefixes, no JSON, no raw `<@id>` tokens, no scope_keys, no Redis output. | The user-facing surface must look human. Tom's plumbing stays internal.

---

## 3. Identity & viewer tiers

Every flow that involves a Discord user resolves a `Viewer` first. The `Viewer` is consulted everywhere downstream and is the single concept all flows can rely on.

### 3.1 Tiers

- **`Tier.SOLOMON`** — the bot owner. Bypass rate limits. Always has full film summary in prompt unless `suppress_films=1`. Can be banned but the ban list is operator-only, so this only happens by accident.
- **`Tier.REGULAR`** — a known club member (Brian, Gavin, Isis, Anthony Taylor, G, or anyone whose Discord ID is mapped in `data/tombombadil/identity.yaml` under `regulars`). Subject to rate limits. Film summary uses their `FILM_DATABASE['people']` entry.
- **`Tier.STRANGER`** — unknown Discord user. No film history; system prompt says "ask, don't fabricate". Subject to rate limits. Cannot have NoteDrafts attributed to them (no canonical name). Long-term facts are not stored. Sees a slightly friendlier onboarding-style first reply (see Section 5.1).

### 3.2 Resolution order

1. YAML `owner.discord_id` matches → `SOLOMON`.
2. YAML `regulars[*].discord_id` matches → `REGULAR` with the listed canonical name.
3. Case-insensitive match of Discord display name against `FILM_DATABASE['people']` keys → `REGULAR` (or `SOLOMON` for "Solomon Smith") with that key as canonical.
4. Otherwise → `STRANGER` with `canonical_name=None`.

### 3.3 Invariants

Every flow can assume:

- `viewer.discord_id` is a non-empty string.
- `viewer.tier is Tier.STRANGER` implies `viewer.canonical_name is None`.
- `viewer.canonical_name is not None` implies a corresponding entry in `FILM_DATABASE['people']`.

---

## 4. Flow contracts

The 14 user-visible flows. Each follows the same template (trigger / tier / inputs / preconditions / side effects / expected reply / error modes / examples / known delta).

### 4.1 Conversational

#### 4.1.1 Mention reply

- **Trigger:** `bot.user.mentioned_in(message)` AND message does NOT start the rigid `Film:/Rating:` template (that path is removed in PR 2).
- **Tier required:** any.
- **Inputs:** `viewer` (resolved Viewer), `content` (str, AFTER `_resolve_mentions` substitutes `<@id>` / `<@&id>` / `<#id>` tokens), `scope_key` (DM vs channel namespace).
- **Preconditions:** viewer is not banned (`tom:bans`); `len(content) <= MAX_PROMPT_CHARS` (4000); rate-limit token available unless `viewer.is_owner`.
- **Side effects:**
  - `RPUSH` user turn to scope history.
  - LLM call.
  - On success: `RPUSH` assistant turn; run fact extractor; persist prefs / queue NoteDrafts / embed free-facts.
  - `tom_replies_total{tier=...}` counter increment.
- **Expected reply:**
  - Addresses the *intent* of the message, not the speaker (V4).
  - 1–3 short paragraphs; no Markdown tables on Discord.
  - No `[viewer]` prefix, no raw mention tokens, no JSON (V6).
  - For `STRANGER`: asks rather than invents (V5).
  - For viewer with `suppress_films=1` in prefs: no film mentions unless the message itself names one.
- **Error modes:**
  - LLM timeout → `"LLM timeout, try again"`. No history append. No fact extraction.
  - LLM empty content → `"No response generated"`. No history append.
  - LLM raises → `"Error processing your request"`. No history append.
  - Banned viewer → `"I've been asked not to engage with you. Sorry."` No LLM call. `tom_guards_tripped{kind="ban"}` increment.
  - Prompt too long → `"That message is N characters; I cap inputs at 4000. Trim it down and try again."` No LLM call. Counter as `prompt_too_long`.
  - Rate-limited → `"Easy there -- you're hitting Tom faster than the cooldown allows. Try again in {N}s."` No LLM call. Counter as `rate_limit`.
- **Examples:**
  - In: Solomon mentions Tom, `"what did I rate Get Out?"`
    Out: `"You rated Get Out 10/10. Want a follow-up rec?"`
  - In: Brian (no Letterboxd history in seed) mentions Tom, `"what's my favorite film?"`
    Out: `"Your tracked profile is light -- Ran is what I have. What else have you logged?"`
  - In: stranger mentions Tom, `"hi"`
    Out: `"Hey -- haven't seen you here before. Ask me about films, club nights, or what to watch."`
- **Known delta:** Currently the LLM still occasionally leaks `[viewer]` prefix when prior assistant turns were stored under the buggy prefix-on-assistant code (pre-fix). Burning down as the affected histories age out (7-day TTL).

#### 4.1.2 Note capture (loose-form + draft + confirm)

- **Trigger:** Inside the mention-reply flow, the fact extractor identifies a `NoteDraft` from natural-language phrasing (`I rated X N/10`, `I'd rate X N`, etc.).
- **Tier required:** SOLOMON or REGULAR. `STRANGER` viewers never produce NoteDrafts (the extractor requires `viewer.canonical_name`).
- **Inputs:** `NoteDraft(film, rating, viewer)` from extractor.
- **Preconditions:** `film` not in pronoun blacklist (`it`, `this`, `that`, `them`, `those`, `these`, `him`, `her`, `us`); `viewer.canonical_name` is set; `do_not_log` pref is not `"1"`.
- **Side effects:**
  - Bot posts a follow-up message after the primary reply: `"React ✅ to log **<film>** (<rating>/10) for <viewer.canonical_name>, or ❌ to skip."`
  - Bot pre-adds ✅ and ❌ reactions.
  - Draft bound at `tom:draft:{follow_up_message_id}` with 24h TTL.
  - `tom_drafts_offered_total` increment.
- **Expected reply (on reaction):**
  - Only the original requester can act. Other users' reactions are ignored.
  - ✅ → `save_note(film, viewer.canonical_name, rating)`. Bot replies `"OK <film> (<rating>/10) logged"`. `tom_drafts_committed_total` increment.
  - ❌ → draft deleted. Bot replies `"Skipped."` (delete-after=10s). `tom_drafts_skipped_total` increment.
  - Reactions other than ✅/❌ are ignored entirely.
- **Error modes:**
  - Draft expires (>24h, no reaction) → silently drop, no user-visible message.
  - `save_note` rejects on duplicate (same film/watcher/week) → `"Duplicate submission"`.
  - `save_note` rejects on invalid rating → returned error string. (Should not happen: extractor clamps to 0–10.)
- **Examples:**
  - In: `"@Tom I just watched Stalker 10/10"`. Out (primary): conversational reply about Stalker. Out (draft): `"React ✅ to log Stalker (10/10) for Solomon Smith, or ❌ to skip."` User reacts ✅. Tom: `"OK Stalker (10/10) logged"`.
  - In: `"@Tom I rated it 5/10"` (no antecedent). Out: primary reply only — no draft (pronoun blacklist).
- **Known delta:** None for attribution — `NoteDraft.requester_discord_id`
  is stamped at extraction / push time and reused at bind (D2 fixed).
  Concurrent mentions may still surface a confirmation prompt under the
  reply that finished first, but only the originator can confirm.

### 4.2 Slash commands

#### 4.2.1 `/rate film:<title> rating:<0-10>`

- **Trigger:** Discord slash command.
- **Tier required:** SOLOMON or REGULAR. STRANGER returns a configuration-help message (no save).
- **Inputs:** `film` (str), `rating` (float).
- **Preconditions:** `film` non-empty after trim; `0 <= rating <= 10`.
- **Side effects:** `save_note(film, viewer.canonical_name, rating)`. `tom_slash_commands{name="rate"}` increment.
- **Expected reply:** `"OK **<film>** (<rating>/10) logged for <viewer.canonical_name>."` Non-ephemeral.
- **Error modes:**
  - Empty film → `"Film is required."`
  - Rating not numeric / out of range → `"Rating must be numeric."` or `"Rating must be between 0 and 10."`
  - Stranger → `"I don't have a canonical name for you yet, so I can't file this rating. Ask Solomon to add you to data/tombombadil/identity.yaml."`
  - Duplicate week's submission → error string from `save_note`.
- **Examples:**
  - `/rate film:Inception rating:9` → `"OK **Inception** (9/10) logged for Solomon Smith."`
- **Known delta:** none.

#### 4.2.2 `/recommend [for_name:<name>]`

- **Trigger:** Discord slash command.
- **Tier required:** any.
- **Inputs:** optional `for_name`. Defaults to invoker's canonical name.
- **Preconditions:** resolvable target (either explicit `for_name` matching a known viewer, or invoker has a canonical name).
- **Side effects:** `tom_slash_commands{name="recommend"}` increment.
- **Expected reply:** Either a recommendation paragraph (theme-overlap pick) OR a favorites-list fallback when the target has already watched everything theme-tagged in the catalog. Never `"I don't have enough data"`.
- **Error modes:**
  - Target not resolvable (stranger invoking with no `for_name`) → `"I don't have a name to recommend for. Pass for:<name> or ask Solomon to add you to the identity map first."`
  - Unknown `for_name` → `"I don't know <name> well enough to recommend something yet."`
- **Examples:**
  - `/recommend` (as Solomon) → favorites-list fallback (seed catalog exhausted for Solomon).
  - `/recommend for_name:Brian` → theme-matched suggestion or fallback paragraph.
- **Known delta:** Letterboxd-imported films don't carry `themes` after merge, so `suggest_for_person` only ever picks from the 3 seed films. For any viewer with full Letterboxd history, the recommender lands on the favorites fallback. Fix: tag Letterboxd films during merge.

#### 4.2.3 `/club stats`

- **Trigger:** Discord slash subcommand.
- **Tier required:** any.
- **Inputs:** none.
- **Preconditions:** `FILM_DATABASE['films']` non-empty.
- **Side effects:** `tom_slash_commands{name="club_stats"}` increment.
- **Expected reply:** Markdown bullets containing top-rated films (avg), most-watched film, most-active reviewer. Non-ephemeral.
- **Error modes:**
  - Empty catalog → `"No films in the club catalog yet."`
- **Examples:**
  - `/club stats` → "**Club stats**\nTop-rated:\n- **La Haine** -- avg 9.8 (3 watchers)\n…"
- **Known delta:** none.

#### 4.2.4 `/club recommend names:<comma-separated>`

- **Trigger:** Discord slash subcommand.
- **Tier required:** any.
- **Inputs:** comma-separated viewer names.
- **Preconditions:** at least one name resolves to a known viewer.
- **Side effects:** `tom_slash_commands{name="club_recommend"}` increment.
- **Expected reply:** A blended recommendation (intersection of preferred themes, fallback to union) skipping any film the group has collectively watched. OR a "everyone has watched the overlap" message.
- **Error modes:**
  - Empty names → `"Pass one or more names so I know who I'm blending for."`
  - All unknown → `"I don't know any of: <list>."`
- **Examples:**
  - `/club recommend names:Brian, Gavin` → blended Markdown rec.
- **Known delta:** Same Letterboxd-themeless issue as `/recommend` — limits the catalog the blender can pick from.

#### 4.2.5 `/club schedule film:<title> when:<ISO 8601>`

- **Trigger:** Discord slash subcommand.
- **Tier required:** any. (Anyone in the channel can schedule.)
- **Inputs:** `film` (str), `when` (ISO 8601 timestamp string).
- **Preconditions:** `film` non-empty; `when` parseable as ISO.
- **Side effects:**
  - Galadriel `at` job saved with `delivery.mode="discord"`, `delivery.to=interaction.channel_id`, `payload.message=...announce...`.
  - `tom_slash_commands{name="club_schedule"}` increment.
- **Expected reply:** `"Scheduled watch party for **<film>** at <when> (job <id>)."` Adds `" (Heads up: <film> isn't in the catalog yet.)"` when the film isn't seeded.
- **Error modes:**
  - Empty film → `"Film is required."`
  - Bad ISO → `"When must be an ISO 8601 timestamp, e.g. 2026-05-15T19:00:00."`
- **Examples:**
  - `/club schedule film:Inception when:2026-05-15T19:00:00` → confirmation reply.
- **Known delta:** Requires Galadriel container running. Without it the job sits in Redis and never fires. Operator note: `docker compose --profile cron --profile discord up -d`.

#### 4.2.6 `/forget scope:<short|long|prefs|all>`

- **Trigger:** Discord slash command.
- **Tier required:** any.
- **Inputs:** `scope` ∈ `{short, long, prefs, all}`.
- **Preconditions:** valid scope string.
- **Side effects:**
  - `short`: `DEL` the invoker's history scope key.
  - `long`: `delete_by_metadata({kind: "tom_fact", viewer: canonical})` against Finrod's store. STRANGER → no-op.
  - `prefs`: `DEL tom:pref:{invoker.discord_id}`.
  - `all`: all three.
  - `tom_slash_commands{name="forget"}` increment.
- **Expected reply (ephemeral):** `"Cleared: <list of cleared parts>."` Listing exactly what was deleted (e.g. `"recent conversation history in this channel; your saved preferences; 3 long-term fact chunk(s) attributed to you"`).
- **Error modes:**
  - Unknown scope → `"Scope must be one of: all, long, prefs, short."`
- **Examples:**
  - `/forget scope:prefs` → ephemeral confirmation.
- **Known delta:** `long` against the in-memory Finrod store works; against future Milvus standalone it routes through `delete_by_expr` which has a real impl but isn't exercised in production (Milvus is profile-gated).

#### 4.2.7 `/whoami`

- **Trigger:** Discord slash command.
- **Tier required:** any.
- **Inputs:** none.
- **Side effects:** `tom_slash_commands{name="whoami"}` increment.
- **Expected reply (ephemeral):** Multi-line viewer record: `Tier`, `Canonical name`, `Discord` (display + id), `Owner` (yes/no).
- **Error modes:** none.
- **Examples:**
  - `/whoami` (as Solomon) → "**Tier**: `solomon`\n**Canonical name**: Solomon Smith\n…**Owner**: yes".
- **Known delta:** none.

### 4.3 Scheduled

#### 4.3.1 Watch-party announcement

- **Trigger:** Galadriel job with `payload.kind="agentTurn"` and `delivery.mode="discord"` fires at its `at_iso`.
- **Tier required:** N/A (system-initiated).
- **Inputs:** job record (film, channel_id, organizer).
- **Side effects:**
  - Galadriel calls `/execute/wait` with the announce message; result is a Tom-authored Discord-ready string.
  - Galadriel calls `agents.tombombadil.delivery.publish(channel_id, text)`.
  - Bot's `delivery.subscriber_loop` `BLPOP`s and posts to the named channel.
- **Expected reply (in channel):** A Tom-voiced announcement, e.g. `"Club night tonight. We're watching **Inception**. Hosted by Solomon Smith. Drop reactions if you're in."`
- **Error modes:**
  - Galadriel unreachable → job never fires; logged. No user-facing recovery.
  - Bot offline at fire time → message sits in `tom:announce:queue` and posts when bot returns.
  - Channel unreachable from bot (deleted / no permission) → log warning, drop.
- **Examples:** see operator instructions in `docs/tombombadil-memory.md`.
- **Known delta:** Galadriel container is currently off in production.

#### 4.3.2 Letterboxd auto-sync

- **Trigger:** Galadriel cron `0 6 * * *` (default America/Los_Angeles) fires `letterboxd_sync` system event.
- **Tier required:** N/A.
- **Inputs:** `LETTERBOXD_USERNAME`, `LETTERBOXD_VIEWER_NAME`, optional `TOM_LETTERBOXD_ANNOUNCE_CHANNEL_ID`.
- **Side effects:**
  - HTTP GET to Letterboxd RSS feed.
  - Diff against watermark at `tom:letterboxd:last_watched_iso`.
  - For each new rated entry: `save_note(film, viewer_name, rating*2)`.
  - Optional: `delivery.publish` an announcement per saved film.
  - Watermark advance.
  - `tom_letterboxd_sync{kind="saved_films"|"errors"}` increments.
- **Expected reply (if announcement channel set):** One Discord message per newly-logged film, formatted: `"<viewer> logged **<film>** (<year>) (<rating>/10) on Letterboxd."`
- **Error modes:**
  - `LETTERBOXD_USERNAME` unset → returns error result; no replies.
  - Fetch failure → logged; no replies; watermark unchanged.
  - Unrated diary entries → skipped at save time.
- **Examples:**
  - Manual run: `docker compose exec tombombadil python -m agents.tombombadil.sync_job` → `fetched=15 new=2 saved=2`.
- **Known delta:** Galadriel container off; cron is inert. Manual invocation works fine.

### 4.4 Internal flows (per-message, transparent to user)

These fire inside other flows and shape replies, but don't have their own trigger.

#### 4.4.1 Long-term fact recall

- **Trigger:** Inside `agent.get_response`, before the LLM call.
- **Inputs:** `viewer` (must have `canonical_name`), current message text.
- **Side effects:** Finrod query with the message; client-side filter on `metadata.kind == "tom_fact"` and `metadata.viewer == viewer.canonical_name`; score floor 0.35.
- **Expected effect:** Top-K (default 5) recalled facts appear in a `Relevant things this user told you in earlier sessions: …` block in the LLM's system prompt.
- **Error modes:**
  - STRANGER → returns `[]` without querying.
  - Empty query → returns `[]`.
  - Finrod query fails → logged warning; returns `[]`. Reply continues without recall.
- **Known delta:** Finrod's `InMemoryStore` is ephemeral. Facts evaporate on container recreate until Milvus standalone profile is brought up.

#### 4.4.2 Preference enforcement

- **Trigger:** Inside `agent.get_response`, when assembling the system prompt.
- **Inputs:** `viewer.discord_id`, prefs from `tom:pref:{id}`.
- **Side effects:**
  - `suppress_films=1` → film-summary block is replaced by an explicit "user asked you NOT to bring up films unprompted" line. `tom_pref_suppressed_total` increment.
  - `preferred_tone=<value>` → surfaces in a "preferences on file" block.
  - `do_not_log=1` → fact extractor skipped entirely after reply.
- **Expected effect:** Tom's next reply respects the pref without the user having to re-state it.
- **Error modes:**
  - Redis pref read fails → fail-open (treat as no prefs). Reply continues.
- **Known delta:** No `/setpref` slash; prefs are set ONLY by the fact extractor's natural-language detector. Adding a slash command for explicit control is a PR-7-candidate.

#### 4.4.3 Identity resolution + roster injection

- **Trigger:** Every inbound message (mention or slash).
- **Inputs:** Discord author id + display name.
- **Side effects:** none (pure compute) other than YAML lru_cache hit.
- **Expected effect:** Returns a `Viewer`. System prompt gets:
  - An identity block: `"You are speaking with <name> (tier=<tier>, discord_id=<id>). Other recognised club members: <all_known()>."`
  - A roster block listing every `FILM_DATABASE['people']` entry with avg/watched/themes so Tom can answer questions about other club members.
- **Error modes:**
  - Malformed YAML → log warning; fall back to name heuristic only.
  - YAML missing → name heuristic only.
- **Known delta:** None.

---

## 5. Cross-cutting concerns

### 5.1 Onboarding (first-contact contract)

When a stranger (no YAML entry, no `FILM_DATABASE` name match) `@`s Tom for the first time, the bot should:

1. Recognise this is a new face (tier=STRANGER, canonical_name=None).
2. Reply with a single short paragraph that:
   - Greets them by their Discord display name.
   - Names what Tom does ("I run the film club's notes here…").
   - Suggests *one* concrete next action (`"Try /whoami to see how I see you, or just tell me what you've been watching."`).
   - Does NOT lecture about identity.yaml setup; that's an operator concern, not a user one.
3. Does NOT invent a film history or pretend to know them.
4. The first turn is added to history like any other; subsequent strangers' messages get normal replies (no infinite onboarding).

**Known delta:** Currently strangers get the generic film-history-aware reply, just with the "ask, don't fabricate" system block. The onboarding paragraph above isn't a special case in code — would need a first-turn detector keyed on empty history.

### 5.2 Multi-user collisions

#### Concurrent mentions (two users `@`Tom at once in the same channel)

- Both messages flow through `on_message` independently. Each gets its own `trace_id`. No shared state contended.
- Both get appended to the same `tom:hist:ch:{channel_id}` list in arrival order.
- Both get rate-limit-checked against their *individual* `tom:rl:{discord_id}` keys (no shared bucket).
- Both LLM calls run concurrently. The bot replies in whatever order they finish.
- The second reply sees the first as history (because `recent_turns` is read fresh).

#### Simultaneous draft offers in the same channel

- The fact extractor produces at most one draft per inbound message.
- Drafts queue at `tom:drafts:scope:{scope_key}` via `RPUSH`. **Each draft must carry the originator's `discord_id`** so subsequent binding doesn't lose attribution.
- After each reply, `bot._offer_pending_draft` does *one* `LPOP` per turn. The popped draft is bound to `tom:draft:{message_id}` using `draft.requester_discord_id` (the originator's id, captured at extraction time) — NOT the id of whoever's reply is currently being delivered.
- This way, if Solomon and Brian both say `"I rated X 9/10"` concurrently, and Brian's LLM call finishes first, Brian still sees a confirmation prompt — but the draft it's bound to is Solomon's, with `requester_discord_id=Solomon`. Brian reacting ✅ on it does nothing (reactor.id != requester_discord_id). When Solomon eventually finds the message and reacts ✅, the rating gets logged correctly.
- Each pending draft gets its own `tom:draft:{message_id}` key. The `requester_discord_id` field guards reactions: only the original drafter can confirm or skip.

#### Reaction races

- If two users react ✅ to the same draft, only the requester's reaction triggers `save_note` (the handler rejects non-requester reactions before calling save). The second reaction is silently ignored.
- If the requester reacts ✅ and ❌ in quick succession, ordering determines the outcome (Discord delivers events in order). The first to land wins; the second finds the draft already deleted and exits silently.

**Known delta:** None — drafts carry `requester_discord_id` at push time
(D2). Two users typing identical phrases back to back is the boundary
case and it's handled by the LIST queue + per-draft requester guard.

### 5.3 Failure modes

When a backend dependency degrades, Tom's user-facing behavior should be:

| Dependency | Outage symptom | Tom's user-facing behavior |
|---|---|---|
| LLM (Groq) | timeout / 5xx / network | `"LLM timeout, try again"` or `"Error processing your request"` per error class. No history append. No fact extraction. |
| Redis | unreachable | Bot fails-open on rate limit (allows the request). History append silently fails — current reply is unaffected, but no history persists. Long-term recall and pref reads fail-open. **Bot stays alive.** |
| Discord API | rate-limited | discord.py's built-in backoff handles 429s. Tom may appear slower to reply, no user-visible message. |
| Finrod (Milvus or InMemoryStore) | unreachable / crashed | `recall_facts` returns `[]`. `remember_fact` logs `remember_fact_failed`. Reply continues; long-term memory just goes cold until backend recovers. |
| Letterboxd RSS | unreachable / 5xx | Sync job logs `letterboxd_fetch_failed`, returns errored `SyncResult`, watermark unchanged. No user-facing message unless an announcement channel was configured (then no announcements that day). |
| YAML config | missing / malformed | Identity falls back to name heuristic. No replies break, but tier classifications become best-effort. |
| Galadriel cron | container off | All scheduled flows (4.3.x) are inert. Users get no announcements at scheduled times. Operator action required. |

**Known delta:** Tom doesn't yet retry the LLM on timeout. A short retry-with-backoff (2 attempts, jitter) would mask transient Groq blips at the cost of slower failure feedback.

### 5.4 Privacy & data handling

#### What Tom stores

- **Conversation history** (`tom:hist:ch:*`, `tom:hist:dm:*`): last 20 turn-pairs per scope, 7-day idle TTL.
- **Per-user prefs** (`tom:pref:{discord_id}`): no TTL — explicit `/forget scope:prefs` to clear.
- **Long-term facts** (Finrod vector store): no TTL when persistent. In-memory store evaporates on container recreate.
- **Pending draft store** (`tom:drafts:scope:*`, `tom:draft:{message_id}`): 24h TTL.
- **Film notes** (`note:*`, `film:*:notes`, `watcher:*:notes`, `films`, `watchers`): persistent. Deletion only via `redis-cli` today.
- **Ban list** (`tom:bans` SET): persistent. Membership controls bot's willingness to reply.

#### What Tom does NOT do

- Does not share another user's preferences, history, or facts when replying in a DM.
- Does not name uninvolved users in a reply unless the asking user explicitly named or mentioned them.
- Does not paste secrets — API keys, tokens, IDs — in replies.
- Does not log message content outside what's already in the structured-log stream and Redis history.

#### What a user can do

- `/forget scope:short|long|prefs|all` — self-service wipe.
- `/whoami` — see how Tom sees them.
- (Future) `/setpref` — explicit pref control without the natural-language detector.

**Known delta:** Notes (films/ratings) have no self-service deletion path. Need a `/unrate film:<title>` slash or a `/forget scope:notes`.

### 5.5 Operator surface

Solomon (the bot owner) has admin actions beyond what slash commands expose. All require shell access to home-server.

| Action | Invocation |
|---|---|
| Ban a Discord user | `docker compose exec redis redis-cli SADD tom:bans <discord_id>` |
| Unban | `docker compose exec redis redis-cli SREM tom:bans <discord_id>` |
| Wipe a user's prefs | `docker compose exec redis redis-cli DEL tom:pref:<discord_id>` |
| Wipe a channel's history | `docker compose exec redis redis-cli DEL tom:hist:ch:<channel_id>` |
| Wipe a user's DM history | `docker compose exec redis redis-cli DEL tom:hist:dm:<discord_id>` |
| Inspect a pending draft | `docker compose exec redis redis-cli HGETALL tom:draft:<message_id>` |
| Reset Letterboxd sync watermark | `docker compose exec redis redis-cli DEL tom:letterboxd:last_watched_iso` |
| Force a Letterboxd sync | `docker compose exec tombombadil python -m agents.tombombadil.sync_job` |
| Add a Discord user to identity map | edit `data/tombombadil/identity.yaml` on host, then recreate `tombombadil` container |
| Recreate the bot | `docker compose --profile discord up -d --force-recreate tombombadil` |
| Inspect metrics | `curl http://localhost:5000/metrics` |

**Known delta:** No admin slash commands (e.g., `/ban`, `/sync`) gated behind owner tier. Today everything's shell. A future PR could add slash equivalents guarded by `viewer.is_owner`.

---

## 6. Test rubric

The integration suite (sub-project B) should produce one test module per major flow group, asserting the contracts in Section 4 against `MockLLM` + `fakeredis` + a `discord.py` doubles harness.

Recommended structure:

```
tests/integration/
  test_tom_conversational.py   # 4.1.x (mention, draft+confirm)
  test_tom_slash.py            # 4.2.x (rate, recommend, club, forget, whoami)
  test_tom_scheduled.py        # 4.3.x (watch-party, letterboxd sync)
  test_tom_internal.py         # 4.4.x (recall, prefs, identity+roster)
  test_tom_cross_cutting.py    # 5.x  (onboarding, collisions, failures, privacy, operator)
```

Each test maps to exactly one spec contract. A test failure means *the contract isn't met today*; fixing the test means changing code, not the spec (unless the contract itself was wrong, in which case the spec updates and an audit-fix ticket lands).

Specific high-value tests to seed sub-project B with:

- Stranger mention does NOT inject Solomon's film summary into the system prompt.
- A user reacting ✅ on someone else's draft does NOT trigger `save_note`.
- `/forget scope:long` for Solomon does NOT delete Brian's facts (already covered, port to integration).
- LLM timeout produces the canned reply and does NOT append history.
- Banned user gets the canned refusal and consumes zero LLM tokens.

---

## 7. Known deltas (aggregated index)

Pulled together for the usability audit (sub-project C). Each entry references the Section 4/5 contract it diverges from.

| # | Delta | Section | Severity |
|---|-------|---------|----------|
| D1 | Old `[viewer]` prefix may still appear from pre-fix history entries | 4.1.1 | Low (decays in 7 days) |
| D2 | ~~Draft `requester_discord_id` is bound at pop time~~ **Fixed** — stamped on `NoteDraft` at push; concurrent `asyncio.gather` test covers crossed FIFO pops | 4.1.2, 5.2 | ~~High~~ Done |
| D3 | Letterboxd-imported films have no `themes`, so `suggest_for_person` only ever ranks against 3 seeded films | 4.2.2, 4.2.4 | Med |
| D4 | Galadriel container off → watch-party + daily sync inert | 4.2.5, 4.3.1, 4.3.2 | Med (operator action) |
| D5 | Milvus standalone profile not running → long-term facts evaporate on container recreate | 4.4.1 | Med (operator action) |
| D6 | No `/setpref` for explicit pref control | 4.4.2 | Low |
| D7 | Stranger onboarding paragraph not specialised — strangers get generic film-aware reply | 5.1 | Med |
| D8 | LLM has no retry-with-backoff; transient Groq blips surface as user-visible failures | 5.3 | Low |
| D9 | No self-service note deletion (no `/unrate`) | 5.4 | Low |
| D10 | No admin slash commands (`/ban`, `/sync`) gated to owner tier | 5.5 | Low |

The audit (sub-project C) will turn each row into a GitHub issue with an owner and a fix sketch.

---

## Appendix A — How this spec was built

Brainstormed via the `superpowers:brainstorming` skill in a single session on 2026-05-10 following PRs 1–6's shipping. The brainstorm explicitly decomposed the user request (`make Tom production-quality and testable`) into three sub-projects:

- **A**: this document.
- **B**: integration suite — `superpowers:writing-plans` next.
- **C**: usability audit — separate cycle, references D1–D10.

The decomposition rationale, structural-approach trade-offs, and per-section rationale live in the Claude Code conversation transcript at session start time 2026-05-10 13:00 PT.
