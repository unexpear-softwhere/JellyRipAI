import json
import urllib.request

from shared.ai.providers.local_provider import LocalProvider, _is_embedding_model


def test_is_available_stubbed_false_by_default():
    """Pins the autouse conftest fixture: the local provider reports
    unavailable by default so the suite never touches a real Ollama
    server (fast + deterministic regardless of dev state)."""
    assert LocalProvider().is_available() is False


def test_local_provider_resolves_exact_model(monkeypatch):
    provider = LocalProvider()
    provider.configure(model="llama3.1:8b")
    monkeypatch.setattr(
        provider,
        "_get_available_models",
        lambda: ["llama3.1:8b", "qwen2.5-coder:7b"],
    )
    # is_available() is a live TCP probe to Ollama (audit #19); mock it
    # so model resolution is tested deterministically whether or not
    # Ollama happens to be running on the test machine.
    monkeypatch.setattr(provider, "is_available", lambda: True)

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
    # is_available() is a live TCP probe to Ollama (audit #19); mock it
    # so model resolution is tested deterministically whether or not
    # Ollama happens to be running on the test machine.
    monkeypatch.setattr(provider, "is_available", lambda: True)

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


def test_is_embedding_model_detects_embeddings_by_name_and_family():
    # By name — covers nomic-embed-text, mxbai-embed-large, etc.
    assert _is_embedding_model({"name": "nomic-embed-text:latest"}) is True
    assert _is_embedding_model({"name": "mxbai-embed-large:latest"}) is True
    # By family — bge / all-minilm names lack "embed" but report bert.
    assert _is_embedding_model(
        {"name": "all-minilm:latest", "details": {"family": "bert"}}
    ) is True
    assert _is_embedding_model(
        {"name": "bge-m3:latest", "details": {"families": ["nomic-bert"]}}
    ) is True
    # Chat models pass through.
    assert _is_embedding_model(
        {"name": "qwen2.5:14b-instruct", "details": {"family": "qwen2"}}
    ) is False
    assert _is_embedding_model(
        {"name": "llama3.1:8b", "details": {"family": "llama"}}
    ) is False
    # Malformed / missing details must not raise (else the whole model
    # list would be silently emptied by _get_available_models' guard).
    assert _is_embedding_model({"name": "weird", "details": "oops"}) is False
    assert _is_embedding_model({}) is False


def test_get_available_models_excludes_embedding_models(monkeypatch):
    """Embedding / non-chat models must never reach the model picker —
    they can't run the assistant, so listing them only confuses users."""
    payload = {
        "models": [
            {"name": "qwen2.5:14b-instruct",
             "details": {"family": "qwen2", "families": ["qwen2"]}},
            {"name": "llama3.1:8b",
             "details": {"family": "llama", "families": ["llama"]}},
            {"name": "nomic-embed-text:latest",
             "details": {"family": "nomic-bert", "families": ["nomic-bert"]}},
            {"name": "mxbai-embed-large:latest",
             "details": {"family": "bert", "families": ["bert"]}},
            {"name": "all-minilm:latest",
             "details": {"family": "bert", "families": ["bert"]}},
        ]
    }
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda req, timeout=0: _FakeResponse(payload)
    )

    models = LocalProvider()._get_available_models()

    assert models == ["qwen2.5:14b-instruct", "llama3.1:8b"]
    assert not any("embed" in m or "minilm" in m for m in models)


def test_is_cloud_model_detects_cloud_tags():
    from shared.ai.providers.local_provider import _is_cloud_model
    assert _is_cloud_model("qwen3.5:cloud") is True
    assert _is_cloud_model("qwen3-vl:235b-cloud") is True
    assert _is_cloud_model("llama3.1:8b") is False
    assert _is_cloud_model("gemma3:12b") is False
    assert _is_cloud_model("") is False


def test_cloud_models_usable_true_when_no_cloud_models():
    from shared.ai.providers import local_provider as lp
    lp._reset_cloud_auth_cache()
    # No cloud models pulled → nothing to probe, trivially usable.
    assert lp.LocalProvider().cloud_models_usable(
        ["llama3.1:8b", "gemma3:12b"]
    ) is True
    lp._reset_cloud_auth_cache()


def test_cloud_models_usable_false_on_403(monkeypatch):
    """A cloud model returning 403 (not signed in) marks cloud unusable."""
    import urllib.error
    import urllib.request as ur
    from shared.ai.providers import local_provider as lp
    lp._reset_cloud_auth_cache()

    def _forbidden(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(ur, "urlopen", _forbidden)
    assert lp.LocalProvider().cloud_models_usable(
        ["qwen3.5:cloud", "llama3.1:8b"]
    ) is False
    lp._reset_cloud_auth_cache()


def test_cloud_models_usable_true_on_success(monkeypatch):
    """A successful probe (signed in) keeps cloud models usable."""
    import urllib.request as ur
    from shared.ai.providers import local_provider as lp
    lp._reset_cloud_auth_cache()

    class _Resp:
        def read(self):
            return b'{"response":"ok"}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(ur, "urlopen", lambda req, timeout=0: _Resp())
    assert lp.LocalProvider().cloud_models_usable(["qwen3.5:cloud"]) is True
    lp._reset_cloud_auth_cache()
