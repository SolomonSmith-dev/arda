# arda

Multi-agent FastAPI + MCP system (Tolkien-named agents) for orchestration, RAG, shell, cron, Telegram.

## Task Routing

| Task Type | Go To | Description |
|-----------|-------|-------------|
| Add/change agent | `agents/<name>/` | BaseAgent run contract |
| API surface | `api/` | FastAPI lifespan + routes |
| Core models / policy | `core/` | AgentTask, shell policy, config |
| MCP tools | `mcp_server/` | arda_execute/query/plan/status |
| Evals | evals/ · scripts/run_evals.py | Routing/retrieval baselines |
| Deploy / demo | docs/ · deploy/ | Demo mode and hosting |

## Shared Resources

| Resource | Location | Contains |
|----------|----------|----------|
| CLAUDE | `CLAUDE.md` | Architecture map |
| AGENTS | `AGENTS.md` | Agent-facing rules |
| Graph | `graphify-out/` | God node: AgentTask |
