# ADR 0005: Defer Earendil Legacy HTTP Tests to Phase 3 Rewrite

**Date:** 2026-05-01
**Status:** Accepted
**Amends:** ADR 0002 (Phase 2 Sub-Pass 1)

## Context

ADR 0002 row 13 said `earendil/tests/` would be moved to `tests/earendil/` and have imports updated. Reading the three test files during execution shows they are not import-driven unit tests:

- `test_api.py`, `test_queue.py`, `test_e2e.py` all hit `http://100.112.3.116:5000` (Mac Mini Tailscale IP) via `requests.post/get`. They are integration tests against a live, deployed FastAPI server.
- They share a hard-coded `BASE_URL` and `API_KEY`.
- They never `import` Earendil code -- they only exercise it through HTTP.

The HTTP layer they test is exactly `legacy_api/earendil_api.py`, which Phase 3 deletes and replaces with `api/main.py` plus `api/routes/*.py`. Porting these tests now would mean:

1. Marking every test `@pytest.mark.phase4` so they skip-by-default.
2. Carrying ~600 LOC of skipped tests that exercise code slated for deletion.
3. Rewriting them again in Phase 3 against the new `api/` layer.

That's debt with no coverage benefit -- the unit-level coverage already exists in `tests/earendil/test_worker_smoke.py` and `tests/earendil/test_agent_smoke.py` (17 tests against `agents/earendil/`).

## Decision

Skip the legacy HTTP test port. Leave `earendil/tests/*.py` in the legacy directory (deleted with the rest of `earendil/` in sub-pass 2 cleanup, scheduled for 2026-05-11). Phase 3 writes new HTTP integration tests against `api/main.py` from scratch.

The unit coverage gap is zero: `Earendil.run()`, `plan_task`, `normalize_task`, `enqueue_task`, and the worker process loop are all covered by fakeredis-backed unit tests already on disk.

## Alternatives considered

- **Port verbatim with `pytest.mark.phase4`.** Rejected. ~600 LOC of tests skipped-by-default that test code being deleted in Phase 3. Pure overhead.
- **Port and adapt to use FastAPI `TestClient` against `legacy_api/`.** Rejected. `legacy_api/earendil_api.py` is verbatim-preserved per ADR 0002 -- no test surface should be added against it because it's reference-only, not part of the wheel.
- **Port and re-target at `agents/earendil/agent.py`.** Rejected. The tests assume HTTP semantics (status codes, JSON response shapes) that don't apply to a bare Python class. Forcing the mapping creates brittle adaptations of tests that should be rewritten clean in Phase 3.

## Consequences

- ADR 0002 row 13 is amended: `earendil/tests/` is not migrated. Sub-pass 1 verification gate drops the "port Earendil tests" line.
- Phase 3 must include: write `tests/api/test_*.py` against the new `api/` layer using FastAPI's `TestClient` (and `httpx.AsyncClient` for async paths). New tests are written against the new API's actual surface, not adapted from the deleted one.
- Sub-pass 2 cleanup deletes `earendil/tests/` along with the rest of the legacy directory.

## Verification

After sub-pass 1 lands:
- `ls earendil/tests/ 2>/dev/null` shows the legacy files (still on disk, untracked).
- `ls tests/earendil/` shows `__init__.py`, `test_worker_smoke.py`, `test_agent_smoke.py` only.
- `pytest tests/earendil/ -v` reports 17 passing tests, zero skipped.
