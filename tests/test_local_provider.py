import json
import urllib.request

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


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_local_provider_chat_uses_chat_endpoint(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse(
            {"message": {"role": "assistant", "content": "Rip looks healthy."}}
        )

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    provider = LocalProvider()
    provider.configure(model="qwen2.5:14b-instruct")
    monkeypatch.setattr(provider, "_require_model_name", lambda: "qwen2.5:14b-instruct")

    result = provider.chat(
        [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "What is happening right now?"},
        ],
        max_tokens=222,
        timeout=9.5,
    )

    assert result == "Rip looks healthy."
    assert captured["url"] == "http://localhost:11434/api/chat"
    assert captured["timeout"] == 9.5
    assert captured["body"] == {
        "model": "qwen2.5:14b-instruct",
        "messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "What is happening right now?"},
        ],
        "stream": False,
        "options": {"num_predict": 222},
    }
