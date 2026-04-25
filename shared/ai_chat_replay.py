import json
import os
from datetime import datetime
from itertools import count
from typing import Any, Mapping, Sequence

from shared.runtime import get_config_dir


AI_CHAT_REPLAY_FILENAME = "ai_chat_replay.jsonl"
_MAX_DEPTH = 6
_MAX_ITEMS = 40
_MAX_STRING_LENGTH = 16000


def ai_chat_replay_path(config_dir: str | None = None) -> str:
    return os.path.join(
        config_dir or get_config_dir(),
        AI_CHAT_REPLAY_FILENAME,
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


def append_ai_chat_replay(
    phase: str,
    *,
    replay_id: str = "",
    title: str = "",
    backend: str = "",
    request_text: str = "",
    display_text: str = "",
    response_text: str = "",
    error_text: str = "",
    details: Mapping[str, Any] | None = None,
    config_dir: str | None = None,
) -> dict[str, Any] | None:
    record: dict[str, Any] = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "phase": str(phase or "").strip(),
        "replay_id": str(replay_id or "").strip(),
        "title": str(title or "").strip(),
        "backend": str(backend or "").strip(),
        "request_text": str(request_text or ""),
        "display_text": str(display_text or ""),
        "response_text": str(response_text or ""),
        "error_text": str(error_text or ""),
        "details": _json_safe(dict(details or {})),
    }

    if not record["phase"]:
        return None

    path = ai_chat_replay_path(config_dir)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    except Exception:
        return None
    return record


def read_ai_chat_replay(
    *,
    config_dir: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    path = ai_chat_replay_path(config_dir)
    records: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except Exception:
                    continue
                if isinstance(record, Mapping):
                    records.append(dict(record))
    except Exception:
        return []
    if isinstance(limit, int) and limit > 0:
        return records[-limit:]
    return records


def _iter_replay_lines_reverse(path: str, *, chunk_size: int = 65536):
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        buffer = b""

        while position > 0:
            read_size = min(chunk_size, position)
            position -= read_size
            handle.seek(position)
            buffer = handle.read(read_size) + buffer
            lines = buffer.split(b"\n")
            buffer = lines[0]

            for raw_line in reversed(lines[1:]):
                yield raw_line.decode("utf-8", errors="replace")

        if buffer:
            yield buffer.decode("utf-8", errors="replace")


def _read_latest_bundle_records(
    path: str,
    *,
    bundle_limit: int,
) -> list[dict[str, Any]]:
    if bundle_limit <= 0:
        return []

    selected_ids: list[str] = []
    selected_id_set: set[str] = set()
    completed_ids: set[str] = set()
    records_reversed: list[dict[str, Any]] = []
    fallback_ids = count(1)

    try:
        for raw_line in _iter_replay_lines_reverse(path):
            if not raw_line.strip():
                continue
            try:
                raw_record = json.loads(raw_line)
            except Exception:
                continue
            if not isinstance(raw_record, Mapping):
                continue

            record = dict(raw_record)
            replay_id = str(record.get("replay_id", "") or "").strip()
            if not replay_id:
                replay_id = f"tail-line-{next(fallback_ids)}"
                record["replay_id"] = replay_id

            if replay_id not in selected_id_set:
                if len(selected_ids) >= bundle_limit:
                    continue
                selected_ids.append(replay_id)
                selected_id_set.add(replay_id)

            records_reversed.append(record)
            if str(record.get("phase", "") or "").strip() == "request":
                completed_ids.add(replay_id)

            if (
                len(selected_ids) >= bundle_limit
                and all(replay_id in completed_ids for replay_id in selected_ids)
            ):
                break
    except Exception:
        return []

    records_reversed.reverse()
    return records_reversed


def _mapping_or_empty(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _non_empty_mapping(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, Mapping) and value:
            return dict(value)
    return {}


def _non_empty_value(*values: Any) -> Any:
    for value in values:
        if isinstance(value, str):
            if value.strip():
                return value
            continue
        if isinstance(value, (list, tuple, dict)):
            if value:
                return value
            continue
        if value is not None:
            return value
    return ""


def build_ai_chat_replay_bundles(
    records: Sequence[Mapping[str, Any]] | None,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    source_records = list(records or [])
    bundles_by_id: dict[str, dict[str, Any]] = {}

    for index, raw_record in enumerate(source_records):
        record = dict(raw_record or {})
        replay_id = str(record.get("replay_id", "") or "").strip() or f"line-{index + 1}"
        details = _mapping_or_empty(record.get("details"))
        record["details"] = details

        bundle = bundles_by_id.get(replay_id)
        if bundle is None:
            bundle = {
                "replay_id": replay_id,
                "title": "",
                "request_text": "",
                "display_text": "",
                "status": "",
                "backend": "",
                "first_timestamp": "",
                "last_timestamp": "",
                "phase_sequence": [],
                "records": [],
                "request_record": None,
                "response_record": None,
                "error_record": None,
                "final_record": None,
                "final_answer_text": "",
                "final_error_text": "",
                "ai_profile": {},
                "session_facts": {},
                "payload": {},
                "payload_by_provider": {},
                "messages": [],
                "messages_by_provider": {},
                "max_tokens": "",
                "max_tokens_by_provider": {},
                "_last_index": index,
            }
            bundles_by_id[replay_id] = bundle

        bundle["records"].append(record)
        bundle["_last_index"] = index

        phase = str(record.get("phase", "") or "").strip()
        if phase:
            bundle["phase_sequence"].append(phase)
        if phase == "request":
            bundle["request_record"] = record
        elif phase == "response":
            bundle["response_record"] = record
        elif phase == "error":
            bundle["error_record"] = record

        timestamp = str(record.get("timestamp", "") or "").strip()
        if timestamp and not bundle["first_timestamp"]:
            bundle["first_timestamp"] = timestamp
        if timestamp:
            bundle["last_timestamp"] = timestamp

        bundle["title"] = str(
            _non_empty_value(bundle["title"], record.get("title", ""))
        ).strip()
        bundle["request_text"] = str(
            _non_empty_value(bundle["request_text"], record.get("request_text", ""))
        )
        bundle["display_text"] = str(
            _non_empty_value(bundle["display_text"], record.get("display_text", ""))
        )

    bundles = sorted(
        bundles_by_id.values(),
        key=lambda item: int(item.get("_last_index", 0)),
        reverse=True,
    )
    if isinstance(limit, int) and limit > 0:
        bundles = bundles[:limit]

    finalized: list[dict[str, Any]] = []
    for bundle in bundles:
        request_record = bundle.get("request_record")
        response_record = bundle.get("response_record")
        error_record = bundle.get("error_record")
        records_list = list(bundle.get("records", []))
        final_record = error_record or response_record or (records_list[-1] if records_list else None)
        request_details = _mapping_or_empty(
            request_record.get("details") if isinstance(request_record, Mapping) else {}
        )

        payload = _non_empty_mapping(
            request_details.get("payload"),
        )

        bundle["final_record"] = final_record
        bundle["status"] = (
            "error"
            if error_record is not None
            else "response"
            if response_record is not None
            else str(
                _non_empty_value(
                    bundle.get("phase_sequence", [])[-1]
                    if bundle.get("phase_sequence")
                    else "",
                    "",
                )
            )
        )
        bundle["backend"] = str(
            _non_empty_value(
                final_record.get("backend", "") if isinstance(final_record, Mapping) else "",
                response_record.get("backend", "") if isinstance(response_record, Mapping) else "",
                error_record.get("backend", "") if isinstance(error_record, Mapping) else "",
                request_record.get("backend", "") if isinstance(request_record, Mapping) else "",
            )
        ).strip()
        bundle["final_answer_text"] = str(
            _non_empty_value(
                response_record.get("response_text", "") if isinstance(response_record, Mapping) else "",
                final_record.get("response_text", "") if isinstance(final_record, Mapping) else "",
            )
        )
        bundle["final_error_text"] = str(
            _non_empty_value(
                error_record.get("error_text", "") if isinstance(error_record, Mapping) else "",
                final_record.get("error_text", "") if isinstance(final_record, Mapping) else "",
            )
        )
        bundle["ai_profile"] = _non_empty_mapping(
            request_details.get("ai_profile"),
            payload.get("ai_profile"),
        )
        bundle["session_facts"] = _non_empty_mapping(
            request_details.get("session_facts"),
            payload.get("session_facts"),
        )
        bundle["payload"] = payload
        bundle["payload_by_provider"] = _non_empty_mapping(
            request_details.get("payload_by_provider"),
        )
        bundle["messages"] = list(
            _non_empty_value(
                request_details.get("messages"),
                [],
            )
            or []
        )
        bundle["messages_by_provider"] = _non_empty_mapping(
            request_details.get("messages_by_provider"),
        )
        bundle["max_tokens"] = _non_empty_value(request_details.get("max_tokens"), "")
        bundle["max_tokens_by_provider"] = _non_empty_mapping(
            request_details.get("max_tokens_by_provider"),
        )
        bundle["line_count"] = len(records_list)
        bundle.pop("_last_index", None)
        finalized.append(bundle)

    return finalized


def list_ai_chat_replay_bundles(
    *,
    config_dir: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    if isinstance(limit, int) and limit > 0:
        path = ai_chat_replay_path(config_dir)
        records = _read_latest_bundle_records(path, bundle_limit=limit)
        if records:
            return build_ai_chat_replay_bundles(records, limit=limit)
    return build_ai_chat_replay_bundles(
        read_ai_chat_replay(config_dir=config_dir),
        limit=limit,
    )


def read_latest_ai_chat_replay_bundle(
    *,
    config_dir: str | None = None,
) -> dict[str, Any] | None:
    bundles = list_ai_chat_replay_bundles(
        config_dir=config_dir,
        limit=1,
    )
    return bundles[0] if bundles else None
