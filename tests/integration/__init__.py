"""Tom Bombadil integration test suite (sub-project B).

Asserts the contracts in docs/superpowers/specs/2026-05-10-tom-bombadil-
behavior-spec.md across test_tom_*.py modules.

## Known limitations (carried to sub-project C audit)

- test_tom_slash.py largely duplicates the unit-test coverage in
  tests/tombombadil/test_commands.py because it calls the pure cmd_*
  handler functions directly rather than driving the registered
  discord.app_commands callbacks through FakeInteraction. The
  FakeInteraction harness exists in tests/integration/_doubles.py
  but is currently unused. Future work: rewrite the slash tests to
  exercise interaction handlers (assertions on ephemeral flag, on
  slash_commands_total counter increments, on parameter validation
  raised by the @app_commands.describe layer) or trim the
  duplicative cases.

- test_concurrent_drafts_bind_to_original_drafters and
  test_concurrent_mentions_history_interleaves exercise SEQUENTIAL
  on_message calls because asyncio awaits each call to completion
  before the next runs. True concurrency demonstration (interleaved
  coroutines via asyncio.gather, or threadpool reentrancy) would
  require a different orchestration. D2 in the spec depends on this.

- D7 onboarding xfail (test_stranger_first_contact_includes_greeting_
  and_suggestion) cannot pass under MockLLM regardless of production
  code, because MockLLM does not follow system prompt instructions.
  Real-LLM or templated-reply driving is required.
"""
