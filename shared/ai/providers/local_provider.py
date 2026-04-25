"""Local model provider adapter (Ollama)."""

from __future__ import annotations

import json
import re
import time
import urllib.request
from typing import Any

from shared.ai.providers.base import BaseProvider, ConnectionResult, ProviderInfo

_MODEL_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)b\b")


def _local_model_sort_key(model_name: str) -> tuple[float, float, str]:
    lowered = str(model_name or "").strip().lower()
    size_matches = [float(match) for match in _MODEL_SIZE_RE.findall(lowered)]
    largest_size = max(size_matches) if size_matches else 0.0

    family_bonus = 0.0
    if lowered.startswith("qwen"):
        family_bonus = 40.0
    elif lowered.startswith("llama"):
        family_bonus = 30.0
    elif lowered.startswith("mistral"):
        family_bonus = 20.0
    elif lowered.startswith("gemma"):
        family_bonus = 10.0

    return (-largest_size, -family_bonus, lowered)


def _sort_local_models(models: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for model in models:
        name = str(model or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(name)
    return sorted(unique, key=_local_model_sort_key)


class LocalProvider(BaseProvider):
    """Local model backend via Ollama HTTP API."""

    _DEFAULT_MODEL = "qwen2.5:14b-instruct"

    def __init__(self) -> None:
        self._model = self._DEFAULT_MODEL
        self._base_url = "http://localhost:11434"

    def info(self) -> ProviderInfo:
        available_models = self._get_available_models()
        return ProviderInfo(
            id="local",
            display_name="Local (Ollama)",
            category="local",
            requires_api_key=False,
            default_model=available_models[0] if available_models else "",
            available_models=available_models,
            help_url="https://ollama.com/download",
        )

    def configure(self, api_key: str = "", model: str = "", **kwargs: Any) -> None:
        if model:
            self._model = model
        if "base_url" in kwargs:
            self._base_url = str(kwargs["base_url"]).rstrip("/")

    @staticmethod
    def _family_token(model_name: str) -> str:
        head = model_name.lower().split(":", 1)[0]
        return head.split("-", 1)[0]

    @staticmethod
    def _size_token(model_name: str) -> str:
        tail = model_name.lower().split(":", 1)[1] if ":" in model_name else ""
        for token in re.split(r"[^a-z0-9.]+", tail):
            if token.endswith("b") and any(ch.isdigit() for ch in token):
                return token
        return ""

    def _resolve_model_name(self, available_models: list[str] | None = None) -> str | None:
        models = _sort_local_models(list(available_models or self._get_available_models()))
        if not models:
            return None

        configured = self._model.strip().lower()
        exact = {name.lower(): name for name in models}
        if configured in exact:
            return exact[configured]
        return models[0]

    def get_selected_model_name(self, available_models: list[str] | None = None) -> str:
        return self._resolve_model_name(available_models) or ""

    def _require_model_name(self) -> str:
        available_models = self._get_available_models()
        resolved = self._resolve_model_name(available_models)
        if resolved:
            return resolved
        available_preview = ", ".join(available_models[:5]) or "none"
        raise ValueError(
            f"Model '{self._model}' not pulled. Available: {available_preview}"
        )

    def is_configured_model_exact(self, available_models: list[str] | None = None) -> bool:
        models = [name.lower() for name in (available_models or self._get_available_models())]
        return self._model.strip().lower() in models

    def is_available(self) -> bool:
        try:
            return bool(self._get_available_models())
        except Exception:
            return False

    def _get_available_models(self) -> list[str]:
        """Query Ollama for actually-pulled models."""
        try:
            req = urllib.request.Request(
                f"{self._base_url}/api/tags", method="GET",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
            return _sort_local_models(
                [m["name"] for m in data.get("models", []) if "name" in m]
            )
        except Exception:
            return []

    def _call(self, system: str, user: str, max_tokens: int, timeout: float) -> str:
        actual_model = self._require_model_name()
        body = json.dumps({
            "model": actual_model,
            "system": system,
            "prompt": user,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self._base_url}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read())
        return result.get("response", "(no response)")

    def _chat(self, messages: list[dict[str, str]], max_tokens: int, timeout: float) -> str:
        actual_model = self._require_model_name()
        body = json.dumps({
            "model": actual_model,
            "messages": list(messages or []),
            "stream": False,
            "options": {"num_predict": max_tokens},
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self._base_url}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read())
        return result.get("message", {}).get("content", "(no response)")

    def test_connection(self, timeout: float = 10.0) -> ConnectionResult:
        try:
            # First check if Ollama is reachable
            req = urllib.request.Request(
                f"{self._base_url}/api/tags", method="GET",
            )
            start = time.time()
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
            ms = (time.time() - start) * 1000

            available_models = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
            available_models = _sort_local_models(available_models)
            resolved_model = self._resolve_model_name(available_models)
            if not resolved_model:
                pulled = [m.get("name", "?") for m in data.get("models", [])]
                return ConnectionResult(
                    success=False,
                    latency_ms=ms,
                    error=f"Model '{self._model}' not pulled. Available: {', '.join(pulled[:5]) or 'none'}",
                )

            confirmed = resolved_model
            if resolved_model.lower() != self._model.strip().lower():
                confirmed = f"{resolved_model} (installed on this PC)"
            return ConnectionResult(
                success=True,
                latency_ms=ms,
                model_confirmed=confirmed,
            )
        except Exception as e:
            return ConnectionResult(success=False, error=str(e))

    def chat(self, messages: list[dict[str, str]],
             max_tokens: int = 800, timeout: float = 20.0) -> str:
        return self._chat(messages, max_tokens, timeout)

    def diagnose(self, payload_json: str, system_prompt: str,
                 max_tokens: int = 800, timeout: float = 20.0) -> str:
        return self._call(system_prompt, payload_json, max_tokens, timeout)

    def summarize(self, payload_json: str, system_prompt: str,
                  max_tokens: int = 1200, timeout: float = 20.0) -> str:
        return self._call(system_prompt, payload_json, max_tokens, timeout)
