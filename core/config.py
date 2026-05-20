from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Tier = Literal["orchestrator", "executor", "retriever", "specialist"]
Provider = Literal["anthropic", "google", "groq", "mock"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Auth
    arda_api_key: str = "arda-dev-key-2026"

    # LLM providers
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    groq_api_key: str = ""
    claude_api_key: str = ""

    # Dev mode -- mock all LLM calls
    use_mock_llm: bool = True

    # Mock embedder (skips torch + sentence-transformers download).
    # Defaults to mirroring use_mock_llm so dev stays zero-cost; set
    # USE_MOCK_EMBEDDER=true explicitly on weak hosts to keep real
    # Groq/Gemini LLM calls while avoiding the ~1GB torch footprint.
    use_mock_embedder: bool | None = None

    # Model overrides per tier
    orchestrator_model: str = "claude-opus-4-7"
    executor_model: str = "meta-llama/llama-4-scout-17b-16e-instruct"
    retriever_model: str = "meta-llama/llama-4-scout-17b-16e-instruct"
    specialist_model: str = "meta-llama/llama-4-scout-17b-16e-instruct"

    # Sauron LangGraph checkpointer (SQLite path; relative to cwd)
    checkpointer_db_path: str = ".arda/checkpoints.sqlite"

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    # Milvus
    milvus_host: str = "localhost"
    milvus_port: int = 19530

    # Discord / TMDB
    discord_token: str = ""
    tmdb_api_key: str = ""

    # Telegram (Gwaihir bot). Comma-separated allowlist of chat IDs;
    # any inbound update from a chat not in this list is silently dropped.
    telegram_bot_token: str = ""
    telegram_allowed_chat_ids: str = ""

    # Base URL of the unified ARDA API. The MCP server (mcp_server/server.py)
    # talks to it over HTTP; agents and routes share the same key in
    # `arda_api_key` for the `x-api-key` header.
    arda_api_url: str = "http://localhost:5000"

    # Galadriel worker calls the API back via this URL. Defaults to the
    # docker-compose service name; set INTERNAL_API_URL=http://localhost:5000
    # for local dev outside compose.
    internal_api_url: str = "http://api:5000"

    # Logging
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    @property
    def telegram_chat_allowlist(self) -> frozenset[int]:
        raw = self.telegram_allowed_chat_ids.strip()
        if not raw:
            return frozenset()
        ids: set[int] = set()
        for part in raw.split(","):
            part = part.strip()
            if part:
                ids.add(int(part))
        return frozenset(ids)

    @property
    def mock_embedder_enabled(self) -> bool:
        if self.use_mock_embedder is None:
            return self.use_mock_llm
        return self.use_mock_embedder

    def model_for_tier(self, tier: Tier) -> str:
        return {
            "orchestrator": self.orchestrator_model,
            "executor": self.executor_model,
            "retriever": self.retriever_model,
            "specialist": self.specialist_model,
        }[tier]

    def provider_for_tier(self, tier: Tier) -> Provider:
        if self.use_mock_llm:
            return "mock"
        mapping: dict[Tier, Provider] = {
            "orchestrator": "anthropic",
            "executor": "groq",
            "retriever": "groq",
            "specialist": "groq",
        }
        return mapping[tier]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
