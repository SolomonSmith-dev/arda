# ARDA Handoff — 2026-05-30

## State

Branch: `feature/github-config`
PR: #42 (open, will need updating after Tasks 3-5)

## What's done

### Commits ahead of `origin/main` (4)
- `f531b80` feat(config): add github_token and github_username settings
- `99fb0be` security: remove hardcoded arda_api_key default, fail-closed on missing env var
- `b13c9db` fix(tests): tighten arda_api_key regression test to check Pydantic field invariant
- `b85737e` refactor(sauron): derive SAURON_TOOLS dynamically from SPECIALIST_TOOL_MAP

### Test state
- Full suite: 362 passed, 1 skipped, 1 xfailed
- ruff check: clean on all touched files
- ruff format --check: pre-existing warnings in `graph.py` left alone (existed on main)

## Plan being executed

`docs/superpowers/plans/2026-05-30-phase1-fixes-and-new-specialist.md`

Execution mode: subagent-driven-development (fresh subagent per task, two-stage review)

### Task status

- [x] **Task 1** Fail-close `arda_api_key` -- DONE, reviewed (spec ✓ + quality ✓ + lint fixup)
- [~] **Task 2** Dynamic SAURON_TOOLS via SPECIALIST_TOOL_MAP -- IMPLEMENTED + COMMITTED, NOT REVIEWED YET
- [ ] **Task 3** Fix `wait_for_tasks` blocking event loop
- [ ] **Task 4** Scaffold Cirdan GitHub audit specialist
- [ ] **Task 5** Push branch + update PR #42

## Task 2 implementer notes (needs review when picked up)

DONE_WITH_CONCERNS report:
1. **Scope expansion** -- implementer touched a 4th file (`tests/sauron/test_agent_smoke.py`) to widen `test_sauron_fails_when_specialist_not_registered` to accept both `"not registered"` and `"unknown tool"` error messages. Reason: with the new dynamic tool list, when no specialists are registered, MockLLM's `tool_use` hits the "unknown tool" gate before the "not registered" gate. Test intent preserved.
2. Pre-existing `graph.py` ruff format warnings left as-is.
3. Plan typo `TOMMODBADIL_TOOL` correctly ignored -- kept existing `TOMBOMBADIL_TOOL`.
4. `dispatch_tool` backward compat preserved (optional `tool_name_to_specialist` param defaults to global).

## Next steps (in order)

1. **Spec-review Task 2** -- verify b85737e matches the plan. The scope expansion (4th file, test assertion widening) needs sign-off.
2. **Code-quality-review Task 2** -- second stage review.
3. **Dispatch Task 3 implementer** (asyncio.to_thread for wait_for_tasks in both Earendil.run and api/routes/tasks.py handle_execute_wait).
4. **Dispatch Task 4 implementer** (scaffold Cirdan -- new agents/cirdan/ package, new tests/cirdan/, add CIRDAN_TOOL to SPECIALIST_TOOL_MAP, register in api/main.py lifespan).
5. **Task 5** -- push, update PR #42 title and body.

## Critical reminders

- ARDA_API_KEY now REQUIRED at startup. Home server `.env` already has it (production), but the GITHUB_TOKEN was set literally as `<your-pat>` and still needs replacing with a real PAT before API restart.
- Old PR #41 is closed.
- Cirdan is the chosen Tolkien name for the GitHub audit specialist (Círdan the Shipwright). Rename before Task 4 if desired.
- Project rule: never use Co-Authored-By trailers, no emojis, no em dashes.
