# Role: retrieval

- **Purpose:** Finrod: LlamaIndex RAG with injected LLM, embed model and vector store.
- **Owns (write):** `agents/finrod/`, `tests/finrod/`
- **Reads:** everything
- **Validation:** `uv run pytest tests/finrod/ -q`
- **Never:** unpin `numpy<2` (deploy host CPU lacks x86-64-v2); pull torch or pymilvus into the slim install

