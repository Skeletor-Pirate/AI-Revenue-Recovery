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

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5"

    razorpay_key_id: str | None = None
    razorpay_key_secret: str | None = None
    razorpay_webhook_secret: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
