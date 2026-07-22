"""NVIDIA NIM provider — OpenAI-compatible chat completions."""
from __future__ import annotations

import time
from typing import NamedTuple

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from src.llm.providers.base import LLMProvider, LLMError


class _CompletionResult(NamedTuple):
    text: str
    input_tokens: int
    output_tokens: int


class NVIDIAProvider(LLMProvider):
    name = "nvidia"

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=2, min=1, max=8))
    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> _CompletionResult:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=120) as client:
                resp = client.post(
                    f"{self._base_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": self._model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        "max_tokens": max_tokens,
                        "stream": False,
                    },
                )
                resp.raise_for_status()
                body = resp.json()
        except httpx.HTTPError as exc:
            raise LLMError(f"NVIDIA API unreachable: {exc}") from exc

        if "error" in body:
            raise LLMError(f"NVIDIA API error: {body['error']}")

        choices = body.get("choices") or []
        if not choices:
            raise LLMError("NVIDIA returned empty choices")

        text = (choices[0].get("message") or {}).get("content") or ""
        if not text:
            raise LLMError("NVIDIA returned empty response")

        usage = body.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        completion_tokens = usage.get("completion_tokens") or usage.get("output_tokens") or 0

        return _CompletionResult(
            text=text,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
        )
