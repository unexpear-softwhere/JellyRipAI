from shared.ai import credential_store
from shared.ai import provider_registry
from shared.ai.providers.local_provider import LocalProvider


def test_get_provider_returns_fresh_instance():
    first = provider_registry.get_provider("openai")
    second = provider_registry.get_provider("openai")

    assert first is not None
    assert second is not None
    assert first is not second


def test_get_configured_provider_does_not_reuse_stale_credentials(monkeypatch):
    creds = {"openai": {"api_key": "test-key", "model": "gpt-4o"}}

    monkeypatch.setattr(
        provider_registry,
        "get_provider_credentials",
        lambda provider_id: creds.get(provider_id, {}),
    )

    configured = provider_registry.get_configured_provider("openai")
    assert configured is not None
    assert configured.is_available() is True

    creds.clear()

    fresh = provider_registry.get_configured_provider("openai")
    assert fresh is not None
    assert fresh.is_available() is False


def test_connection_summary_marks_local_not_connected_without_installed_models(monkeypatch):
    monkeypatch.setattr(provider_registry, "load_credentials", lambda: {})
    monkeypatch.setattr(provider_registry, "get_active_provider_id", lambda: "")
    monkeypatch.setattr(LocalProvider, "_get_available_models", lambda self: [])

    summary = provider_registry.get_connection_summary()

    assert summary["local"]["has_credentials"] is False
    assert summary["local"]["model"] == ""


def test_connect_single_provider_preserves_other_saved_backends(monkeypatch):
    store = {
        "_active_provider": {"id": "openai"},
        "openai": {"api_key": "old-key", "model": "gpt-4o"},
        "local": {"model": "qwen2.5-coder:14b", "base_url": "http://localhost:11434"},
    }
    saved: dict[str, dict] = {}

    monkeypatch.setattr(credential_store, "load_credentials", lambda: dict(store))
    monkeypatch.setattr(
        credential_store,
        "save_credentials",
        lambda creds: saved.setdefault("data", creds),
    )

    credential_store.connect_single_provider(
        "local",
        model="llama3.1:8b",
        base_url="http://localhost:11434",
    )

    assert saved["data"]["openai"]["api_key"] == "old-key"
    assert saved["data"]["local"]["model"] == "llama3.1:8b"
    assert saved["data"]["_active_provider"] == {"id": "openai"}


def test_connect_single_provider_promotes_cloud_without_dropping_local(monkeypatch):
    store = {
        "_active_provider": {"id": "openai"},
        "openai": {"api_key": "old-key", "model": "gpt-4o"},
        "local": {"model": "qwen2.5-coder:14b", "base_url": "http://localhost:11434"},
    }
    saved: dict[str, dict] = {}

    monkeypatch.setattr(credential_store, "load_credentials", lambda: dict(store))
    monkeypatch.setattr(
        credential_store,
        "save_credentials",
        lambda creds: saved.setdefault("data", creds),
    )

    credential_store.connect_single_provider(
        "claude",
        api_key="new-key",
        model="claude-sonnet-4-6",
    )

    assert saved["data"]["local"]["model"] == "qwen2.5-coder:14b"
    assert saved["data"]["claude"]["api_key"] == "new-key"
    assert saved["data"]["_active_provider"] == {"id": "claude"}
