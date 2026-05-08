"""Claude (Anthropic) provider adapter."""

from __future__ import annotations

import time
from typing import Any

from shared.ai.providers.base import BaseProvider, ConnectionResult, ProviderInfo


class ClaudeProvider(BaseProvider):
    """Anthropic Claude API provider."""

    # Refreshed 2026-05-08 against the live Anthropic lineup.  Default is
    # Sonnet 4.6 — best price/perf balance for diagnostics + chat.  Bump
    # to Opus 4.7 if you need the strongest reasoning; drop to Haiku 4.5
    # for the cheapest replies.  Old IDs (`claude-sonnet-4-20250514`,
    # `claude-opus-4-6`) were stale or never released.
    _DEFAULT_MODEL = "claude-sonnet-4-6"
    _MODELS = [
        "claude-opus-4-7",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    ]

    def __init__(self) -> None:
        self._api_key = ""
        self._model = self._DEFAULT_MODEL
        self._client: Any = None

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="claude",
            display_name="Claude (Anthropic)",
            category="cloud",
            requires_api_key=True,
            default_model=self._DEFAULT_MODEL,
            available_models=list(self._MODELS),
            help_url="https://console.anthropic.com/settings/keys",
        )

    def configure(self, api_key: str = "", model: str = "", **kwargs: Any) -> None:
        if api_key:
            self._api_key = api_key
        if model:
            self._model = model
        self._client = None  # force re-init

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self._api_key:
            raise RuntimeError("No Anthropic API key configured")
        import anthropic
        self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def is_available(self) -> bool:
        return bool(self._api_key)

    def test_connection(self, timeout: float = 10.0) -> ConnectionResult:
        try:
            client = self._get_client()
            start = time.time()
            msg = client.messages.create(
                model=self._model,
                max_tokens=10,
                timeout=timeout,
                messages=[{"role": "user", "content": "ping"}],
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
        client = self._get_client()
        system_parts: list[str] = []
        chat_messages: list[dict[str, str]] = []
        for item in list(messages or []):
            role = str(item.get("role", "") or "").strip().lower()
            content = str(item.get("content", "") or "")
            if not content.strip():
                continue
            if role == "system":
                system_parts.append(content)
                continue
            if role in {"user", "assistant"}:
                chat_messages.append({"role": role, "content": content})
        if not chat_messages:
            raise RuntimeError("No chat messages provided.")
        message = client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            timeout=timeout,
            system="\n\n".join(system_parts),
            messages=chat_messages,
        )
        return message.content[0].text if message.content else "(no response)"

    def diagnose(self, payload_json: str, system_prompt: str,
                 max_tokens: int = 800, timeout: float = 30.0) -> str:
        client = self._get_client()
        message = client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            timeout=timeout,
            system=system_prompt,
            messages=[{"role": "user", "content": payload_json}],
        )
        return message.content[0].text if message.content else "(no response)"

    def summarize(self, payload_json: str, system_prompt: str,
                  max_tokens: int = 1200, timeout: float = 30.0) -> str:
        return self.diagnose(payload_json, system_prompt, max_tokens, timeout)
