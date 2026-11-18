"""Configuration parsing and production guards."""
from __future__ import annotations

import pytest

from app.core.config import PLACEHOLDER_SECRET, Settings


def test_cors_origins_accept_comma_separated_and_json(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://erp.example.com, http://localhost:8080")
    assert Settings().cors_origins == ["https://erp.example.com", "http://localhost:8080"]
    monkeypatch.setenv("CORS_ORIGINS", '["https://a.example.com"]')
    assert Settings().cors_origins == ["https://a.example.com"]


def test_production_requires_strong_secret_and_secure_cookies(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SECRET_KEY", PLACEHOLDER_SECRET)
    with pytest.raises(ValueError, match="SECRET_KEY"):
        Settings()
    monkeypatch.setenv("SECRET_KEY", "x" * 48)
    assert Settings().cookie_secure is True


def test_ai_mode_selection(monkeypatch) -> None:
    monkeypatch.setenv("AI_ENGINE", "auto")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert Settings().ai_mode == "offline"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-placeholder")
    assert Settings().ai_mode == "claude"
