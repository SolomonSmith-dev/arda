# Role: filmclub

- **Purpose:** Tom Bombadil: Discord film-club chat, fact extraction, Letterboxd sync and Finrod-backed memory.
- **Owns (write):** `agents/tombombadil/`, `data/tombombadil/`, `tests/tombombadil/`
- **Reads:** everything
- **Validation:** `uv run pytest tests/tombombadil/ -q`
- **Never:** add new import-time side effects to `agents/tombombadil/agent.py`

