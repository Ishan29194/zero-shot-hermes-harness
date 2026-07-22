"""Shared fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_settings():
    import src.config.settings as settings_mod
    settings_mod._settings = None
    yield
    settings_mod._settings = None


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    yield


@pytest.fixture()
def no_keys(monkeypatch):
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "auto")
    monkeypatch.setenv("AGENT_LLM_MODEL", "")
    monkeypatch.setenv("AGENT_ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("AGENT_GEMINI_API_KEY", "")
    monkeypatch.setenv("AGENT_OPENROUTER_API_KEY", "")
    monkeypatch.setenv("AGENT_OLLAMA_BASE_URL", "")
    monkeypatch.setenv("AGENT_OLLAMA_MODEL", "")
    monkeypatch.setenv("AGENT_NVIDIA_API_KEY", "")
    monkeypatch.setenv("AGENT_NVIDIA_BASE_URL", "")
    monkeypatch.setenv("AGENT_NVIDIA_MODEL", "")
    yield
