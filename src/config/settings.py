"""Application settings — Pydantic BaseSettings, env prefix ``AGENT_``."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "sessions"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "gemini": "gemini-2.5-flash",
    "openrouter": "tencent/hy3",
    "ollama": "llama3.1:8b-instruct-q4_0",
    "nvidia": "meta/llama-3.3-70b-instruct",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENT_",
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = Field(default="postgresql://analyst:***@localhost:5432/up_police_analyst")

    llm_provider: str = Field(default="auto")
    llm_model: str = Field(default="")

    anthropic_api_key: str = Field(default="")
    gemini_api_key: str = Field(default="")
    openrouter_api_key: str = Field(default="")
    openrouter_base_url: str = Field(default="https://openrouter.ai/api/v1")

    ollama_base_url: str = Field(default="")
    ollama_model: str = Field(default="llama3.1:8b-instruct-q4_0")

    nvidia_api_key: str = Field(default="")
    nvidia_base_url: str = Field(default="https://integrate.api.nvidia.com/v1")
    nvidia_model: str = Field(default="meta/llama-3.3-70b-instruct")

    token_budget: int = Field(default=8192)

    mssql_connection_string: str = Field(default="")
    mssql_ingest_enabled: bool = Field(default=False)

    port: int = Field(default=8002)
    log_level: str = Field(default="INFO")

    @field_validator("llm_provider", "llm_model", mode="before")
    def _strip_comments(cls, v):
        if isinstance(v, str):
            v = v.split("#")[0].strip()
            return v or ""
        return v

    def resolve_provider(self) -> str:
        if self.llm_provider and self.llm_provider != "auto":
            return self.llm_provider
        for name, key in [
            ("anthropic", self.anthropic_api_key),
            ("gemini", self.gemini_api_key),
            ("openrouter", self.openrouter_api_key),
            ("nvidia", self.nvidia_api_key),
            ("ollama", self.ollama_base_url),
        ]:
            if key:
                return name
        return "stub"

    def resolve_model(self) -> str:
        if self.llm_model:
            return self.llm_model
        return DEFAULT_MODELS.get(self.resolve_provider(), "")

    def key_for(self, provider: str) -> str:
        return {
            "anthropic": self.anthropic_api_key,
            "gemini": self.gemini_api_key,
            "openrouter": self.openrouter_api_key,
            "nvidia": self.nvidia_api_key,
            "ollama": self.ollama_base_url,
        }.get(provider, "")


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
