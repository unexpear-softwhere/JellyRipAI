from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.workflow_history import append_workflow_event, read_workflow_history


def test_append_workflow_event_writes_append_only_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr("shared.workflow_history.get_config_dir", lambda create=True: str(tmp_path))

    first = append_workflow_event(
        "workflow_started",
        session_id="session-1",
        workflow="smart_rip",
        pipeline_step="init",
        details={"media_type": "movie"},
    )
    second = append_workflow_event(
        "output_plan_decision",
        session_id="session-1",
        workflow="smart_rip",
        pipeline_step="output_plan",
        details={"confirmed": False},
    )

    records = read_workflow_history(config_dir=str(tmp_path))

    assert first is not None
    assert second is not None
    assert [record["event_type"] for record in records] == [
        "workflow_started",
        "output_plan_decision",
    ]
    assert records[0]["session_id"] == "session-1"
    assert records[1]["details"]["confirmed"] is False


def test_append_workflow_event_sanitizes_unserializable_values(tmp_path, monkeypatch):
    monkeypatch.setattr("shared.workflow_history.get_config_dir", lambda create=True: str(tmp_path))

    class _Weird:
        def __repr__(self) -> str:
            return "<weird-value>"

    append_workflow_event(
        "same_disc_prompt_shown",
        session_id="session-2",
        workflow="smart_rip",
        pipeline_step="same_disc",
        details={
            "object": _Weird(),
            "values": [1, _Weird()],
            "long_text": "x" * 700,
        },
    )

    records = read_workflow_history(config_dir=str(tmp_path))

    assert len(records) == 1
    assert records[0]["details"]["object"] == "<weird-value>"
    assert records[0]["details"]["values"][1] == "<weird-value>"
    assert len(records[0]["details"]["long_text"]) < 700
