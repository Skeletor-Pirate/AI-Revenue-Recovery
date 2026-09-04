"""Typed application settings, loaded from environment / .env.

pydantic-settings reads each field from an env var of the same name
(case-insensitive). Access the singleton via `get_settings()`.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "postgresql+psycopg://revrec:revrec@localhost:5432/revrec"
    test_database_url: str = (
        "postgresql+psycopg://revrec:revrec@localhost:5432/revrec_test"
    )
    frontend_origin: str = "http://localhost:5173"

    # --- LLM provider (all optional; agents degrade to deterministic behaviour
    #     when none is set). Auto-detected in this order unless llm_provider is
    #     set explicitly: anthropic -> openrouter -> openai. See app/llm.py. ---
    llm_provider: str | None = None            # "anthropic" | "openrouter" | "openai"

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5"

    openrouter_api_key: str | None = None
    openrouter_model: str = "anthropic/claude-3.7-sonnet"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    openai_embed_model: str = "text-embedding-3-small"
    openai_base_url: str = "https://api.openai.com/v1"

    # RAG knowledge base (app/rag.py). rag_enabled=False turns retrieval off
    # even when pgvector + embeddings are available.
    rag_enabled: bool = True
    rag_top_k: int = 5
    rag_bucket_cap: int = 200          # max cases per (root_cause, event_type)
    rag_dedup_distance: float = 0.05   # skip inserts within this cosine distance

    razorpay_key_id: str | None = None
    razorpay_key_secret: str | None = None
    razorpay_webhook_secret: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
