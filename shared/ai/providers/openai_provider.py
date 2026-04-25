"""OpenAI provider adapter."""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Any

from shared.ai.providers.base import BaseProvider, ConnectionResult, ProviderInfo


class OpenAIProvider(BaseProvider):
    """OpenAI API provider (GPT-4o, GPT-4o-mini, etc.)."""

    _DEFAULT_MODEL = "gpt-4o-mini"
    _MODELS = [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
    ]
    _BASE_URL = "https://api.openai.com/v1"

    def __init__(self) -> None:
        self._api_key = ""
        self._model = self._DEFAULT_MODEL

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="openai",
            display_name="OpenAI",
            category="cloud",
            requires_api_key=True,
            default_model=self._DEFAULT_MODEL,
            available_models=list(self._MODELS),
            help_url="https://platform.openai.com/api-keys",
        )

    def configure(self, api_key: str = "", model: str = "", **kwargs: Any) -> None:
        if api_key:
            self._api_key = api_key
        if model:
            self._model = model

    def is_available(self) -> bool:
        return bool(self._api_key)

    def _chat_completion(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        timeout: float,
    ) -> str:
        body = json.dumps({
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": messages,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self._BASE_URL}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read())
        choices = result.get("choices", [])
        if not choices:
            return "(no response)"
        return choices[0].get("message", {}).get("content", "(no response)")

    def test_connection(self, timeout: float = 10.0) -> ConnectionResult:
        try:
            start = time.time()
            self._chat_completion(
                [
                    {"role": "system", "content": "You are a test."},
                    {"role": "user", "content": "ping"},
                ],
                max_tokens=5,
                timeout=timeout,
            )
            ms = (time.time() - start) * 1000
            return ConnectionResult(
                success=True,
                latency_ms=ms,
                model_confirmed=self._model,
            )
        except Exception as e:
            return ConnectionResult(success=False, error=str(e))

    def chat(self, messages: list[dict[str, str]],
             max_tokens: int = 800, timeout: float = 30.0) -> str:
        return self._chat_completion(list(messages or []), max_tokens, timeout)

    def diagnose(self, payload_json: str, system_prompt: str,
                 max_tokens: int = 800, timeout: float = 30.0) -> str:
        return self._chat_completion(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": payload_json},
            ],
            max_tokens,
            timeout,
        )

    def summarize(self, payload_json: str, system_prompt: str,
                  max_tokens: int = 1200, timeout: float = 30.0) -> str:
        return self._chat_completion(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": payload_json},
            ],
            max_tokens,
            timeout,
        )
