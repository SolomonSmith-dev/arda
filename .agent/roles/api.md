# Role: api

- **Purpose:** FastAPI app (lifespan, routes, auth middleware, checkpointer choice) and the MCP server that calls it.
- **Owns (write):** `api/`, `mcp_server/`, `tests/mcp_server/`, `tests/test_api_routes.py`, `tests/test_api_cron_routes.py`
- **Reads:** everything
- **Validation:** `uv run pytest tests/test_api_routes.py tests/test_api_cron_routes.py tests/mcp_server/ -q`
- **Never:** remove `X-API-Key` auth from any route other than `/health` and `/metrics`; rename `mcp_server/` (ADR 0006)

