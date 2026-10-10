# Role: ops-agents

- **Purpose:** Earendil (Redis shell queue + worker), Galadriel (cron) and Gwaihir (Telegram).
- **Owns (write):** `agents/earendil/`, `agents/galadriel/`, `agents/gwaihir/`, `tests/earendil/`, `tests/galadriel/`, `tests/test_gwaihir_bot.py`, `tests/test_gwaihir_notifier.py`
- **Reads:** everything
- **Validation:** `uv run pytest tests/earendil/ tests/galadriel/ tests/test_gwaihir_bot.py tests/test_gwaihir_notifier.py -q`
- **Never:** add an LLM planner to Earendil; it is an intentional regex executor; widen Gwaihir's chat allowlist

