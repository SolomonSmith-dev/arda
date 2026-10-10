# Role: qa

- **Purpose:** Cross-cutting tests (integration, e2e, smoke, conftest), bug reproduction, acceptance validation, regression runs; reviews test quality on domain tasks.
- **Owns (write):** `tests/__init__.py`, `tests/conftest.py`, `tests/integration/`, `tests/test_conduct.py`, `tests/test_core_smoke.py`, `tests/test_e2e_sauron.py`, `tests/test_live_smoke.py`, `tests/test_mock_sdk_parity.py`, `tests/test_startup_without_redis.py`
- **Reads:** everything
- **Validation:** `uv run pytest tests/ -q`
- **Never:** write a test that cannot fail; write a test that needs an API key or a live service without the `phase4`/`integration` marker
- For every new test, state what failing output looks like.
- Domain roles own their unit-test dirs (e.g. `tests/finrod/`); qa owns the shared ones. Does not own `tests/scripts/` (devops).
