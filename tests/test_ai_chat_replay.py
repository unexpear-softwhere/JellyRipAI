import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.ai_chat_replay import (
    append_ai_chat_replay,
    build_ai_chat_replay_bundles,
    list_ai_chat_replay_bundles,
    read_latest_ai_chat_replay_bundle,
    read_ai_chat_replay,
)


def test_append_ai_chat_replay_writes_append_only_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr("shared.ai_chat_replay.get_config_dir", lambda create=True: str(tmp_path))

    first = append_ai_chat_replay(
        "request",
        replay_id="replay-1",
        title="AI Assistant",
        request_text="What happened?",
        details={"payload": {"request": "What happened?"}},
    )
    second = append_ai_chat_replay(
        "response",
        replay_id="replay-1",
        title="AI Assistant",
        backend="CLOUD",
        response_text="Here is what happened.",
    )

    records = read_ai_chat_replay(config_dir=str(tmp_path))

    assert first is not None
    assert second is not None
    assert [record["phase"] for record in records] == ["request", "response"]
    assert records[0]["replay_id"] == "replay-1"
    assert records[1]["backend"] == "CLOUD"


def test_append_ai_chat_replay_sanitizes_nested_values(tmp_path, monkeypatch):
    monkeypatch.setattr("shared.ai_chat_replay.get_config_dir", lambda create=True: str(tmp_path))

    class _Weird:
        def __repr__(self) -> str:
            return "<weird-replay>"

    append_ai_chat_replay(
        "request",
        replay_id="replay-2",
        details={
            "payload": {
                "profile": _Weird(),
                "messages": [1, _Weird()],
                "long_text": "x" * 20000,
            }
        },
    )

    records = read_ai_chat_replay(config_dir=str(tmp_path))

    assert len(records) == 1
    payload = records[0]["details"]["payload"]
    assert payload["profile"] == "<weird-replay>"
    assert payload["messages"][1] == "<weird-replay>"
    assert len(payload["long_text"]) < 20000


def test_read_ai_chat_replay_skips_malformed_lines(tmp_path, monkeypatch):
    monkeypatch.setattr("shared.ai_chat_replay.get_config_dir", lambda create=True: str(tmp_path))

    path = Path(tmp_path, "ai_chat_replay.jsonl")
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-04-17T10:00:00-04:00",
                        "phase": "request",
                        "replay_id": "replay-1",
                        "request_text": "hello",
                        "details": {},
                    }
                ),
                "{not-json",
                json.dumps(
                    {
                        "timestamp": "2026-04-17T10:00:01-04:00",
                        "phase": "response",
                        "replay_id": "replay-1",
                        "response_text": "hi",
                        "details": {},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    records = read_ai_chat_replay(config_dir=str(tmp_path))

    assert [record["phase"] for record in records] == ["request", "response"]
    assert records[0]["replay_id"] == "replay-1"
    assert records[1]["response_text"] == "hi"


def test_build_ai_chat_replay_bundles_groups_request_and_response():
    bundles = build_ai_chat_replay_bundles(
        [
            {
                "timestamp": "2026-04-17T10:00:00-04:00",
                "phase": "request",
                "replay_id": "replay-1",
                "title": "AI Assistant",
                "request_text": "What happened?",
                "display_text": "What happened?",
                "details": {
                    "ai_profile": {"verbosity": "concise"},
                    "session_facts": {"pipeline_step": "output_plan"},
                    "payload": {
                        "request": "What happened?",
                        "session_facts": {"pipeline_step": "output_plan"},
                    },
                    "messages_by_provider": {
                        "LOCAL": [{"role": "user", "content": "What happened?"}]
                    },
                },
            },
            {
                "timestamp": "2026-04-17T10:00:03-04:00",
                "phase": "response",
                "replay_id": "replay-1",
                "backend": "CLOUD",
                "response_text": "Here is the answer.",
                "details": {"mode": "provider_chat"},
            },
        ]
    )

    assert len(bundles) == 1
    bundle = bundles[0]
    assert bundle["replay_id"] == "replay-1"
    assert bundle["status"] == "response"
    assert bundle["backend"] == "CLOUD"
    assert bundle["request_text"] == "What happened?"
    assert bundle["final_answer_text"] == "Here is the answer."
    assert bundle["ai_profile"]["verbosity"] == "concise"
    assert bundle["session_facts"]["pipeline_step"] == "output_plan"
    assert bundle["payload"]["request"] == "What happened?"
    assert bundle["messages_by_provider"]["LOCAL"][-1]["content"] == "What happened?"
    assert bundle["phase_sequence"] == ["request", "response"]


def test_build_ai_chat_replay_bundles_orders_newest_bundle_first():
    bundles = build_ai_chat_replay_bundles(
        [
            {
                "timestamp": "2026-04-17T09:59:00-04:00",
                "phase": "request",
                "replay_id": "older",
                "request_text": "older",
                "details": {},
            },
            {
                "timestamp": "2026-04-17T10:00:00-04:00",
                "phase": "request",
                "replay_id": "newer",
                "request_text": "newer",
                "details": {},
            },
            {
                "timestamp": "2026-04-17T10:00:01-04:00",
                "phase": "error",
                "replay_id": "newer",
                "error_text": "boom",
                "details": {"friendly_error": "Try again."},
            },
        ]
    )

    assert [bundle["replay_id"] for bundle in bundles] == ["newer", "older"]
    assert bundles[0]["status"] == "error"
    assert bundles[0]["final_error_text"] == "boom"


def test_list_ai_chat_replay_bundles_reads_recent_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr("shared.ai_chat_replay.get_config_dir", lambda create=True: str(tmp_path))

    append_ai_chat_replay(
        "request",
        replay_id="replay-1",
        request_text="hello",
        details={"payload": {"request": "hello"}},
    )
    append_ai_chat_replay(
        "response",
        replay_id="replay-1",
        backend="LOCAL",
        response_text="hi",
    )

    bundles = list_ai_chat_replay_bundles(config_dir=str(tmp_path), limit=5)

    assert len(bundles) == 1
    assert bundles[0]["replay_id"] == "replay-1"
    assert bundles[0]["backend"] == "LOCAL"
    assert bundles[0]["final_answer_text"] == "hi"


def test_list_ai_chat_replay_bundles_limit_uses_tail_records_without_full_read(tmp_path, monkeypatch):
    monkeypatch.setattr("shared.ai_chat_replay.get_config_dir", lambda create=True: str(tmp_path))

    path = Path(tmp_path, "ai_chat_replay.jsonl")
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-04-17T10:00:00-04:00",
                        "phase": "request",
                        "replay_id": "older",
                        "request_text": "older",
                        "details": {},
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-17T10:00:01-04:00",
                        "phase": "request",
                        "replay_id": "newer",
                        "request_text": "newer",
                        "details": {},
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-17T10:00:02-04:00",
                        "phase": "response",
                        "replay_id": "older",
                        "backend": "LOCAL",
                        "response_text": "older-answer",
                        "details": {},
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-17T10:00:03-04:00",
                        "phase": "response",
                        "replay_id": "newer",
                        "backend": "CLOUD",
                        "response_text": "newer-answer",
                        "details": {},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "shared.ai_chat_replay.read_ai_chat_replay",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("full replay reader should not run")),
    )

    bundles = list_ai_chat_replay_bundles(config_dir=str(tmp_path), limit=1)

    assert len(bundles) == 1
    assert bundles[0]["replay_id"] == "newer"
    assert bundles[0]["phase_sequence"] == ["request", "response"]
    assert bundles[0]["backend"] == "CLOUD"
    assert bundles[0]["final_answer_text"] == "newer-answer"


def test_read_latest_ai_chat_replay_bundle_returns_newest_bundle_with_monkeypatch(monkeypatch):
    monkeypatch.setattr(
        "shared.ai_chat_replay.list_ai_chat_replay_bundles",
        lambda **kwargs: [
            {"replay_id": "latest", "status": "response"},
            {"replay_id": "older", "status": "request"},
        ],
    )

    bundle = read_latest_ai_chat_replay_bundle()

    assert bundle == {"replay_id": "latest", "status": "response"}
