from shared.ai.providers.local_provider import LocalProvider


def test_local_provider_resolves_exact_model(monkeypatch):
    provider = LocalProvider()
    provider.configure(model="llama3.1:8b")
    monkeypatch.setattr(
        provider,
        "_get_available_models",
        lambda: ["llama3.1:8b", "qwen2.5-coder:7b"],
    )

    assert provider.is_available() is True
    assert provider._require_model_name() == "llama3.1:8b"


def test_local_provider_resolves_closest_installed_model(monkeypatch):
    provider = LocalProvider()
    provider.configure(model="qwen2.5:7b-instruct")
    monkeypatch.setattr(
        provider,
        "_get_available_models",
        lambda: ["llama3.1:8b", "qwen2.5-coder:7b", "qwen2.5-coder:14b"],
    )

    assert provider.is_available() is True
    assert provider._require_model_name() == "qwen2.5-coder:14b"


def test_local_provider_info_includes_installed_models(monkeypatch):
    provider = LocalProvider()
    monkeypatch.setattr(
        provider,
        "_get_available_models",
        lambda: ["llama3.1:8b", "qwen2.5-coder:7b"],
    )

    info = provider.info()

    assert info.available_models == ["llama3.1:8b", "qwen2.5-coder:7b"]


def test_local_provider_prefers_exact_installed_model_name(monkeypatch):
    provider = LocalProvider()
    provider.configure(model="qwen2.5-coder:7b")
    monkeypatch.setattr(
        provider,
        "_get_available_models",
        lambda: ["llama3.1:8b", "qwen2.5-coder:14b", "qwen2.5-coder:7b"],
    )

    assert provider._require_model_name() == "qwen2.5-coder:7b"
