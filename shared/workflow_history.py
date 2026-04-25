from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Mapping, Sequence

from shared.runtime import get_config_dir


WORKFLOW_HISTORY_FILENAME = "workflow_history.jsonl"
_MAX_DEPTH = 4
_MAX_ITEMS = 25
_MAX_STRING_LENGTH = 400


def workflow_history_path(config_dir: str | None = None) -> str:
    return os.path.join(
        config_dir or get_config_dir(),
        WORKFLOW_HISTORY_FILENAME,
    )


def _truncate_text(value: str) -> str:
    text = str(value)
    if len(text) <= _MAX_STRING_LENGTH:
        return text
    return text[: _MAX_STRING_LENGTH - 3] + "..."


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _truncate_text(value)
    if depth >= _MAX_DEPTH:
        return _truncate_text(repr(value))
    if isinstance(value, Mapping):
        items = list(value.items())[:_MAX_ITEMS]
        return {
            str(key): _json_safe(item, depth=depth + 1)
            for key, item in items
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = list(value)[:_MAX_ITEMS]
        return [_json_safe(item, depth=depth + 1) for item in items]
    return _truncate_text(repr(value))


def append_workflow_event(
    event_type: str,
    *,
    session_id: str = "",
    workflow: str = "",
    pipeline_step: str = "",
    disc_identity_hash: str = "",
    disc_structure_hash: str = "",
    details: Mapping[str, Any] | None = None,
    config_dir: str | None = None,
) -> dict[str, Any] | None:
    record: dict[str, Any] = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "event_type": str(event_type or "").strip(),
        "session_id": str(session_id or "").strip(),
        "workflow": str(workflow or "").strip(),
        "pipeline_step": str(pipeline_step or "").strip(),
        "disc_identity_hash": str(disc_identity_hash or "").strip(),
        "disc_structure_hash": str(disc_structure_hash or "").strip(),
        "details": _json_safe(dict(details or {})),
    }

    if not record["event_type"]:
        return None

    path = workflow_history_path(config_dir)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    except Exception:
        return None
    return record


def read_workflow_history(
    *,
    config_dir: str | None = None,
) -> list[dict[str, Any]]:
    path = workflow_history_path(config_dir)
    try:
        with open(path, encoding="utf-8") as handle:
            return [
                json.loads(line)
                for line in handle
                if line.strip()
            ]
    except Exception:
        return []
