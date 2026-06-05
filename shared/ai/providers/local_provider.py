"""Local model provider adapter (Ollama)."""

from __future__ import annotations

import json
import re
import time
import urllib.error
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


# Ollama embedding / non-chat model families (as reported under the
# /api/tags "details" block).  These models can't run chat/generate,
# so they must never appear as a selectable assistant model.
_EMBEDDING_FAMILIES = frozenset({"bert", "nomic-bert"})


def _is_embedding_model(model: dict[str, Any]) -> bool:
    """True for an Ollama embedding / non-chat model.

    Such models have no chat/generate capability, so they can't run the
    assistant and shouldn't be offered as a model choice.  Detected by
    name (contains ``"embed"`` — nomic-embed-text, mxbai-embed-large,
    snowflake-arctic-embed, ...) or by the embedding families Ollama
    reports under ``details`` (``bert`` / ``nomic-bert``, which also
    catches bge / all-minilm whose names lack "embed").
    """
    name = str(model.get("name", "")).lower()
    if "embed" in name:
        return True
    details = model.get("details")
    if not isinstance(details, dict):
        return False
    if str(details.get("family", "")).lower() in _EMBEDDING_FAMILIES:
        return True
    families = details.get("families") or []
    return any(str(f).lower() in _EMBEDDING_FAMILIES for f in families)


def _is_cloud_model(name: str) -> bool:
    """True for an Ollama *cloud* model (e.g. ``qwen3.5:cloud``,
    ``qwen3-vl:235b-cloud``).  These run on Ollama's servers and return
    HTTP 403 unless the user has run ``ollama signin`` — so they're
    unusable until then and the model picker greys them out."""
    lowered = str(name or "").lower()
    tag = lowered.split(":", 1)[1] if ":" in lowered else ""
    return "cloud" in tag or lowered.endswith("-cloud")


# Process-level cache for the cloud sign-in probe (avoid re-probing on
# every picker refresh).  None = not yet checked; True = cloud usable
# (signed in, or no cloud models); False = a cloud model returned 403.
_cloud_auth_usable: bool | None = None


def _reset_cloud_auth_cache() -> None:
    """Test hook / used after sign-in state may have changed."""
    global _cloud_auth_usable
    _cloud_auth_usable = None


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
        """Fast TCP probe: does the Ollama port answer within 200ms?

        Prior implementation called ``_get_available_models`` via HTTP
        with a 5-second timeout.  ``is_available`` is hit on every
        diagnostic error event (audit #19), so 5 seconds of UI freeze
        per check would stack up during a bad rip session.

        A bare TCP connect is fast (typically <5ms when Ollama is up,
        ~200ms when it isn't), enough to answer the only question
        callers actually ask: "is Ollama listening at all?"  Code
        that needs the model list pays the HTTP cost separately via
        ``_get_available_models``.
        """
        try:
            import socket
            from urllib.parse import urlparse
            parsed = urlparse(self._base_url)
            host = parsed.hostname or "localhost"
            port = parsed.port or 11434
            with socket.create_connection((host, port), timeout=0.2):
                return True
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
                [
                    m["name"]
                    for m in data.get("models", [])
                    if "name" in m and not _is_embedding_model(m)
                ]
            )
        except Exception:
            return []

    def cloud_models_usable(
        self, available: "list[str] | None" = None, *, timeout: float = 3.0,
    ) -> bool:
        """Whether Ollama *cloud* models can actually run right now.

        Cloud models 403 unless the user has signed in to Ollama, and
        the model list alone can't tell us — so we probe once with a
        1-token generate and cache the verdict for the process.  Returns
        ``True`` unless we positively observe a 403; a timeout or network
        hiccup never hides a model.  Cheap in the common cases: no cloud
        models installed → no probe; not signed in → the 403 is instant.
        """
        global _cloud_auth_usable
        if _cloud_auth_usable is not None:
            return _cloud_auth_usable
        try:
            models = (
                available if available is not None
                else self._get_available_models()
            )
        except Exception:
            models = []
        cloud = [m for m in models if _is_cloud_model(m)]
        if not cloud:
            _cloud_auth_usable = True
            return True
        body = json.dumps({
            "model": cloud[0],
            "prompt": "ok",
            "stream": False,
            "options": {"num_predict": 1},
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self._base_url}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp.read()
            _cloud_auth_usable = True
        except urllib.error.HTTPError as exc:
            # 403 = not signed in → cloud models unusable.  Other HTTP
            # errors aren't an auth problem, so don't hide on them.
            _cloud_auth_usable = exc.code != 403
        except Exception:
            # Timeout / connection error: don't hide on a transient.
            _cloud_auth_usable = True
        return _cloud_auth_usable

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

            available_models = [
                m.get("name", "")
                for m in data.get("models", [])
                if m.get("name") and not _is_embedding_model(m)
            ]
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
