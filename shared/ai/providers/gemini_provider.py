"""Google Gemini provider adapter."""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Any

from shared.ai.providers.base import BaseProvider, ConnectionResult, ProviderInfo


class GeminiProvider(BaseProvider):
    """Google Gemini API provider."""

    _DEFAULT_MODEL = "gemini-2.0-flash"
    _MODELS = [
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
    ]
    _BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self) -> None:
        self._api_key = ""
        self._model = self._DEFAULT_MODEL

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="gemini",
            display_name="Google Gemini",
            category="cloud",
            requires_api_key=True,
            default_model=self._DEFAULT_MODEL,
            available_models=list(self._MODELS),
            help_url="https://aistudio.google.com/apikey",
        )

    def configure(self, api_key: str = "", model: str = "", **kwargs: Any) -> None:
        if api_key:
            self._api_key = api_key
        if model:
            self._model = model

    def is_available(self) -> bool:
        return bool(self._api_key)

    def _generate(self, system: str, user: str, max_tokens: int, timeout: float) -> str:
        body = json.dumps({
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"parts": [{"text": user}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }).encode("utf-8")
        url = (
            f"{self._BASE_URL}/{self._model}:generateContent"
            f"?key={self._api_key}"
        )
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read())
        candidates = result.get("candidates", [])
        if not candidates:
            return "(no response)"
        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            return "(no response)"
        return parts[0].get("text", "(no response)")

    def _chat(self, messages: list[dict[str, str]], max_tokens: int, timeout: float) -> str:
        system_parts: list[str] = []
        contents: list[dict[str, object]] = []
        for item in list(messages or []):
            role = str(item.get("role", "") or "").strip().lower()
            content = str(item.get("content", "") or "")
            if not content.strip():
                continue
            if role == "system":
                system_parts.append(content)
                continue
            if role == "assistant":
                role = "model"
            if role not in {"user", "model"}:
                continue
            contents.append({"role": role, "parts": [{"text": content}]})
        if not contents:
            raise RuntimeError("No chat messages provided.")
        body = json.dumps({
            "system_instruction": {"parts": [{"text": "\n\n".join(system_parts)}]},
            "contents": contents,
            "generationConfig": {"maxOutputTokens": max_tokens},
        }).encode("utf-8")
        url = (
            f"{self._BASE_URL}/{self._model}:generateContent"
            f"?key={self._api_key}"
        )
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read())
        candidates = result.get("candidates", [])
        if not candidates:
            return "(no response)"
        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            return "(no response)"
        return parts[0].get("text", "(no response)")

    def test_connection(self, timeout: float = 10.0) -> ConnectionResult:
        try:
            start = time.time()
            self._generate("You are a test.", "ping", max_tokens=5, timeout=timeout)
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
        return self._chat(messages, max_tokens, timeout)

    def diagnose(self, payload_json: str, system_prompt: str,
                 max_tokens: int = 800, timeout: float = 30.0) -> str:
        return self._generate(system_prompt, payload_json, max_tokens, timeout)

    def summarize(self, payload_json: str, system_prompt: str,
                  max_tokens: int = 1200, timeout: float = 30.0) -> str:
        return self._generate(system_prompt, payload_json, max_tokens, timeout)
