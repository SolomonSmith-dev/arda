# Tom Bombadil Audit Report

**Date:** 2026-05-10
**Spec under audit:** `docs/superpowers/specs/2026-05-10-tom-bombadil-behavior-spec.md`
**Integration suite:** `tests/integration/test_tom_*.py` (PR #17)
**Total deltas tracked:** 11 (D1-D10 + I1)

## Summary

The Tom Bombadil bot ships in production with [70 passing integration tests](../../tests/integration/) asserting the spec's contracts. This audit catalogues the 11 documented gaps between the spec and the shipped bot, converted into trackable GitHub issues for future implementation cycles.

## Severity distribution

- **High:** 0 open (D2 draft binding — fixed; stamped `requester_discord_id` at push)
- **Medium:** 4 (D3 Letterboxd themes, D4 Galadriel down, D5 Milvus down, D7 stranger onboarding)
- **Low:** 6 (D1 viewer-prefix decay, D6 /setpref, D8 LLM retry, D9 /unrate, D10 admin slashes, I1 test duplication)

## Issue index

| # | Title | Severity | Spec ref | GitHub issue |
|---|-------|----------|----------|-------------|
| D1 | `[viewer]` prefix may persist in stored assistant-turn history | Low | Section 4.1.1 | [#18](https://github.com/SolomonSmith-dev/arda/issues/18) |
| D2 | Draft binding may attribute rating to wrong viewer under concurrent drafts | ~~High~~ **Fixed** | Section 4.1.2, 5.2 | [#19](https://github.com/SolomonSmith-dev/arda/issues/19) |
| D3 | Letterboxd-imported films missing themes, breaking /recommend ranking | Medium | Section 4.2.2, 4.2.4 | [#20](https://github.com/SolomonSmith-dev/arda/issues/20) |
| D4 | Galadriel cron container not running in production, blocking scheduled jobs | Medium | Section 4.2.5, 4.3.1, 4.3.2 | [#21](https://github.com/SolomonSmith-dev/arda/issues/21) |
| D5 | Milvus standalone not running, Finrod falls back to volatile InMemoryStore | Medium | Section 4.4.1 | [#22](https://github.com/SolomonSmith-dev/arda/issues/22) |
| D6 | No /setpref slash command for explicit user preference control | Low | Section 4.4.2 | [#23](https://github.com/SolomonSmith-dev/arda/issues/23) |
| D7 | Stranger first-contact reply is generic, missing spec onboarding paragraph | Medium | Section 5.1 | [#24](https://github.com/SolomonSmith-dev/arda/issues/24) |
| D8 | No LLM retry/backoff -- transient Groq errors surface directly to users | Low | Section 5.3 | [#25](https://github.com/SolomonSmith-dev/arda/issues/25) |
| D9 | No self-service note deletion (/unrate) for individual film corrections | Low | Section 5.4 | [#26](https://github.com/SolomonSmith-dev/arda/issues/26) |
| D10 | Admin operations have no slash commands, require direct redis-cli access | Low | Section 5.5 | [#27](https://github.com/SolomonSmith-dev/arda/issues/27) |
| I1 | tests/integration/test_tom_slash.py duplicates unit-test coverage | Low | N/A (test quality) | [#28](https://github.com/SolomonSmith-dev/arda/issues/28) |

The body of each issue contains the current behavior, desired behavior, fix sketch, and acceptance criteria. They are all tagged `tom-bombadil` + `audit-delta` + `severity-*` for filtering.

## Recommended fix order

1. ~~**D2** (High)~~ — fixed: `requester_discord_id` stamped at push; `asyncio.gather` test covers crossed FIFO pops.
2. **D4 + D5** (operator) -- bring up Galadriel + Milvus containers; activates inert PR 4 / PR 5 work.
3. **D7** (Medium) -- implement templated onboarding so the integration suite has another non-xfail assertion.
4. **D3** (Medium) -- enrich Letterboxd merge with themes so `/recommend` becomes useful for the owner.
5. **I1** (Low) -- wire `FakeInteraction` through the slash tests; reclaims ~250 LOC of accidental duplication.
6. **D1, D6, D8, D9, D10** (Low) -- bundle into a single "Tom Bombadil polish round 2" PR when there's a quiet window.

## What this audit did NOT cover

- Bot personality / voice quality (out of spec scope; lives in `agents/conduct.py`).
- Discord-side OAuth scopes or invite-URL hygiene (covered in deploy runbooks).
- Production performance characteristics (no perf SLOs defined for Tom yet).
- Letterboxd RSS feed shape regressions outside the SAMPLE_FEED fixture format.

## Next cycles

Each issue is independently mergeable. Most are small enough to be a single PR. Pick by severity + interest; the spec + integration suite stay durable while the fixes land.
