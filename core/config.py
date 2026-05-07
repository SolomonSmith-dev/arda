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

    # Dev mode -- mock all LLM calls
    use_mock_llm: bool = True

    # Model overrides per tier
    orchestrator_model: str = "claude-opus-4-7"
    executor_model: str = "llama-4-scout-17b-16e-instruct"
    retriever_model: str = "llama-4-scout-17b-16e-instruct"
    specialist_model: str = "llama-4-scout-17b-16e-instruct"

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

    # Earendil mac mini
    earendil_host: str = "http://100.112.3.116:5000"
    earendil_api_key: str = "earendil-dev-key-2026"

    # Logging
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

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
