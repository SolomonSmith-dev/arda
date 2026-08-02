"""Tom Bombadil integration test suite (sub-project B).

Asserts the contracts in docs/superpowers/specs/2026-05-10-tom-bombadil-
behavior-spec.md across test_tom_*.py modules.

## Known limitations / notes

- test_tom_slash.py drives ``register_commands`` callbacks through
  ``FakeInteraction`` (ephemeral flags, ``tom_slash_commands_total``,
  interaction-derived channel/history scope). Pure ``cmd_*`` behaviour
  stays in ``tests/tombombadil/test_commands.py`` /
  ``tests/tombombadil/test_club.py``. Calling ``.callback`` bypasses
  discord.py option parsing; ``@app_commands.describe`` is client
  metadata and is not asserted in-process.

- test_concurrent_drafts_bind_to_original_drafters uses asyncio.gather
  to interleave two on_message coroutines (D2). Attribution is locked
  via NoteDraft.requester_discord_id at push time.
  test_concurrent_mentions_history_interleaves still uses sequential
  awaits (history order, not draft attribution).

- D7 stranger onboarding is covered by
  test_stranger_first_contact_includes_greeting_and_suggestion (templated
  first-contact path; no longer xfail).
"""
