from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Tier = Literal["orchestrator", "executor", "retriever", "specialist"]
Provider = Literal["google", "groq", "mock"]


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
    gemini_api_key: str = ""
    groq_api_key: str = ""

    # Dev mode -- mock all LLM calls
    use_mock_llm: bool = True

    # Mock embedder (skips torch + sentence-transformers download).
    # Defaults to mirroring use_mock_llm so dev stays zero-cost; set
    # USE_MOCK_EMBEDDER=true explicitly on weak hosts to keep real
    # Groq/Gemini LLM calls while avoiding the ~1GB torch footprint.
    use_mock_embedder: bool | None = None

    # Model overrides per tier
    orchestrator_model: str = "gemini-2.5-flash"
    executor_model: str = "llama-4-scout-17b-16e-instruct"
    retriever_model: str = "llama-4-scout-17b-16e-instruct"
    specialist_model: str = "llama-4-scout-17b-16e-instruct"

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

    # Earendil mac mini
    earendil_host: str = "http://100.112.3.116:5000"
    earendil_api_key: str = "earendil-dev-key-2026"

    # Logging
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

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
        return {
            "orchestrator": "google",
            "executor": "groq",
            "retriever": "groq",
            "specialist": "groq",
        }[tier]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
