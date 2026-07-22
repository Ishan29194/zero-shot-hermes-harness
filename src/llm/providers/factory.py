"""Provider factory — select provider from settings."""
from __future__ import annotations

from src.config.settings import get_settings
from src.llm.providers.base import LLMError, LLMProvider
from src.llm.providers.nvidia import NVIDIAProvider


def create_llm_provider() -> LLMProvider:
    s = get_settings()
    provider = s.resolve_provider()

    if provider == "nvidia":
        return NVIDIAProvider(
            base_url=s.nvidia_base_url,
            api_key=s.nvidia_api_key,
            model=s.resolve_model(),
        )

    if provider == "ollama":
        from src.llm.providers.ollama import OllamaProvider
        return OllamaProvider(base_url=s.ollama_base_url, model=s.resolve_model())

    if provider == "openrouter":
        from src.llm.providers.openrouter import OpenRouterProvider
        return OpenRouterProvider(
            api_key=s.openrouter_api_key,
            base_url=s.openrouter_base_url,
            model=s.resolve_model(),
        )

    if provider == "gemini":
        from src.llm.providers.gemini import GeminiProvider
        return GeminiProvider(api_key=s.gemini_api_key, model=s.resolve_model())

    if provider == "anthropic":
        from src.llm.providers.anthropic import AnthropicProvider
        return AnthropicProvider(api_key=s.anthropic_api_key, model=s.resolve_model())

    raise LLMError(
        "No usable LLM provider configured. "
        "Set an API key or base URL for NVIDIA, Ollama, OpenRouter, Gemini, or Anthropic."
    )
