# Role: orchestrator

- **Purpose:** Sauron's LangGraph StateGraph and tool dispatch, plus the shared contract every agent depends on: BaseAgent, AgentTask/AgentResult, config, logging, clients and the Anthropic/LlamaIndex mocks.
- **Owns (write):** `agents/sauron/`, `agents/__init__.py`, `agents/base.py`, `agents/conduct.py`, `agents/_anthropic_mock.py`, `agents/_llama_index_mock.py`, `core/`, `tests/sauron/`
- **Reads:** everything
- **Validation:** `uv run pytest tests/sauron/ tests/test_e2e_sauron.py tests/test_core_smoke.py tests/test_mock_sdk_parity.py -q`; `uv run mypy agents core`
- **Never:** change Sauron's result envelope (`intent`/`specialist`/`specialist_result`) or the `BaseAgent.run` contract without a decision-log entry; make a real LLM the default; `use_mock_llm` stays True

