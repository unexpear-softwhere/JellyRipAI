"""Provider registry: lists available providers and resolves the active one.

This is the single lookup point for the rest of the app. The GUI dialog
writes credentials via credential_store; the registry reads them back
and hands out configured provider instances.
"""

from __future__ import annotations

import logging
from typing import Any

from shared.ai.credential_store import (
    get_active_provider_id,
    get_provider_credentials,
    load_credentials,
    set_active_provider_id,
)
from shared.ai.providers.base import BaseProvider, ProviderInfo

_logger = logging.getLogger("ai_provider_registry")

_PROVIDER_ORDER = ("claude", "openai", "gemini", "local")

# Lazy-loaded provider classes. We create fresh instances per request so
# stale API keys, models, or local URLs never bleed across lookups.
_provider_classes: dict[str, type[BaseProvider]] = {}


def _ensure_provider_classes() -> dict[str, type[BaseProvider]]:
    """Lazy-init all known provider classes."""
    if _provider_classes:
        return _provider_classes

    from shared.ai.providers.claude_provider import ClaudeProvider
    from shared.ai.providers.gemini_provider import GeminiProvider
    from shared.ai.providers.local_provider import LocalProvider
    from shared.ai.providers.openai_provider import OpenAIProvider

    _provider_classes["claude"] = ClaudeProvider
    _provider_classes["openai"] = OpenAIProvider
    _provider_classes["gemini"] = GeminiProvider
    _provider_classes["local"] = LocalProvider
    return _provider_classes


def _iter_provider_ids() -> list[str]:
    classes = _ensure_provider_classes()
    return [pid for pid in _PROVIDER_ORDER if pid in classes]


def list_providers() -> list[ProviderInfo]:
    """Return metadata for all known providers (cloud first, then local)."""
    all_creds = load_credentials()
    cloud: list[ProviderInfo] = []
    local: list[ProviderInfo] = []

    for pid in _iter_provider_ids():
        provider = get_provider(pid)
        if provider is None:
            continue
        creds = all_creds.get(pid, {})
        if creds:
            provider.configure(**creds)
        info = provider.info()
        if info.category == "cloud":
            cloud.append(info)
        else:
            local.append(info)
    return cloud + local


def get_provider(provider_id: str) -> BaseProvider | None:
    """Get a provider instance by id. Returns None if unknown."""
    provider_cls = _ensure_provider_classes().get(provider_id)
    if provider_cls is None:
        return None
    return provider_cls()


def get_configured_provider(provider_id: str) -> BaseProvider | None:
    """Get a provider instance with saved credentials applied."""
    provider = get_provider(provider_id)
    if provider is None:
        return None
    creds = get_provider_credentials(provider_id)
    if creds:
        provider.configure(**creds)
    return provider


def resolve_active_cloud_provider() -> BaseProvider | None:
    """Return the user's chosen cloud provider, fully configured.

    Falls back through providers if the active one is not available:
    1. Explicit active provider (from credential_store)
    2. First cloud provider that has saved credentials
    3. None
    """
    active_id = get_active_provider_id()
    if active_id:
        provider = get_configured_provider(active_id)
        if provider and provider.info().category == "cloud" and provider.is_available():
            return provider

    # Fallback: find any cloud provider with credentials
    all_creds = load_credentials()
    for pid in _iter_provider_ids():
        p = get_provider(pid)
        if p is None:
            continue
        info = p.info()
        if info.category != "cloud":
            continue
        creds = all_creds.get(pid, {})
        if creds.get("api_key"):
            p.configure(**creds)
            if p.is_available():
                set_active_provider_id(pid)
                return p

    return None


def resolve_local_provider() -> BaseProvider | None:
    """Return the local provider, fully configured."""
    return get_configured_provider("local")


def resolve_provider_for_mode(mode: str) -> BaseProvider | None:
    """Given an AI mode ('off'/'cloud'/'local'), return the right provider.

    For 'cloud' mode, returns the active cloud provider.
    For 'local' mode, returns the local provider.
    For 'off', returns None.
    """
    if mode == "off":
        return None
    if mode == "local":
        return resolve_local_provider()
    if mode == "cloud":
        return resolve_active_cloud_provider()
    return None


def get_connection_summary() -> dict[str, dict[str, Any]]:
    """Return a summary of all providers and their connection state.

    Used by the AI provider dialog to show current status.
    """
    all_creds = load_credentials()
    active_id = get_active_provider_id()
    summary: dict[str, dict[str, Any]] = {}

    for pid in _iter_provider_ids():
        provider = get_provider(pid)
        if provider is None:
            continue
        creds = all_creds.get(pid, {})
        if creds:
            provider.configure(**creds)
        info = provider.info()
        if info.category == "cloud":
            has_credentials = bool(creds.get("api_key"))
            model = creds.get("model", info.default_model)
        else:
            # For local providers, ``has_credentials`` here means
            # "this provider is actually usable for an AI call",
            # which requires (a) the service is reachable AND
            # (b) at least one model is installed.  ``is_available()``
            # is the cheap TCP-only reachability probe used in hot
            # paths (audit #19); for the summary display we can
            # afford the HTTP cost to check actual model
            # availability via ``_get_available_models``.
            try:
                local = provider  # type: ignore[assignment]
                has_credentials = bool(local._get_available_models())
            except Exception:
                has_credentials = False
            model = creds.get("model", "") or (info.available_models[0] if info.available_models else "")

        summary[pid] = {
            "display_name": info.display_name,
            "category": info.category,
            "has_credentials": has_credentials,
            "model": model,
            "is_active": pid == active_id,
            "help_url": info.help_url,
        }

    return summary
