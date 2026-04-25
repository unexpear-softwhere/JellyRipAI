import json
import urllib.request

from shared.ai.providers.openai_provider import OpenAIProvider


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_openai_provider_chat_sends_role_based_messages(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse(
            {"choices": [{"message": {"content": "Alien was released in 1979."}}]}
        )

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    provider = OpenAIProvider()
    provider.configure(api_key="test-key", model="gpt-4o-mini")

    result = provider.chat(
        [
            {"role": "system", "content": "You are helpful."},
            {"role": "system", "content": "App snapshot here."},
            {"role": "user", "content": "When did Alien come out?"},
            {"role": "assistant", "content": "Let me think."},
            {"role": "user", "content": "Answer directly."},
        ],
        max_tokens=111,
        timeout=12.5,
    )

    assert result == "Alien was released in 1979."
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["timeout"] == 12.5
    assert captured["body"]["max_tokens"] == 111
    assert captured["body"]["messages"] == [
        {"role": "system", "content": "You are helpful."},
        {"role": "system", "content": "App snapshot here."},
        {"role": "user", "content": "When did Alien come out?"},
        {"role": "assistant", "content": "Let me think."},
        {"role": "user", "content": "Answer directly."},
    ]
