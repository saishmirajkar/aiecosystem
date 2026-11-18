"""Application configuration loaded from environment variables (12-factor).

Every customer deployment has its own environment file; nothing here is tenant-aware.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PLACEHOLDER_SECRET = "change-me-in-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["development", "test", "demo", "production"] = "development"
    app_name: str = "Above Industrial AI"
    secret_key: str = PLACEHOLDER_SECRET

    database_url: str = "postgresql+psycopg://above:above@localhost:5432/above"
    redis_url: str = "redis://localhost:6379/0"

    # NoDecode: accept the documented comma-separated form instead of requiring JSON.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:5173"])
    session_cookie_name: str = "above_session"
    session_ttl_hours: int = 12
    session_idle_minutes: int = 120
    cookie_secure: bool = False

    login_rate_limit_per_minute: int = 10
    ai_rate_limit_per_minute: int = 20
    max_failed_logins: int = 5
    lockout_minutes: int = 15

    upload_dir: str = "./var/uploads"
    max_upload_mb: int = 10

    # AI
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    anthropic_fallbacks_enabled: bool = True
    ai_engine: Literal["auto", "claude", "offline"] = "auto"
    ai_max_iterations: int = 8
    ai_max_tool_calls: int = 12
    ai_history_turns: int = 10
    ai_max_tokens: int = 16000

    mail_backend: Literal["console", "disabled"] = "console"
    frontend_url: str = "http://localhost:5173"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            if v.strip().startswith("["):
                import json
                return json.loads(v)
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @model_validator(mode="after")
    def _production_guards(self) -> "Settings":
        if self.environment == "production":
            if self.secret_key == PLACEHOLDER_SECRET or len(self.secret_key) < 32:
                raise ValueError("SECRET_KEY must be set to a strong value in production")
            self.cookie_secure = True
        return self

    @property
    def ai_mode(self) -> Literal["claude", "offline"]:
        if self.ai_engine == "offline":
            return "offline"
        if self.ai_engine == "claude":
            return "claude"
        return "claude" if self.anthropic_api_key else "offline"


@lru_cache
def get_settings() -> Settings:
    return Settings()
