from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Mapping, Sequence


def _normalize_text(value: object) -> str:
    return str(value or "").strip()


def _normalize_text_key(value: object) -> str:
    return _normalize_text(value).lower()


def _normalize_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _normalize_audio_track(track: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "codec": _normalize_text_key(track.get("codec")),
        "lang": _normalize_text_key(track.get("lang")),
        "lang_name": _normalize_text_key(track.get("lang_name")),
        "channels": _normalize_text_key(track.get("channels")),
    }


def _normalize_subtitle_track(track: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "lang": _normalize_text_key(track.get("lang")),
        "lang_name": _normalize_text_key(track.get("lang_name")),
    }


def _json_key(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _normalize_title_entry(title: Mapping[str, Any]) -> dict[str, Any]:
    raw_audio = title.get("audio_tracks")
    raw_subtitles = title.get("subtitle_tracks")

    audio_tracks: list[dict[str, Any]] = []
    subtitle_tracks: list[dict[str, Any]] = []

    if isinstance(raw_audio, Sequence) and not isinstance(raw_audio, (str, bytes)):
        audio_tracks = [
            _normalize_audio_track(track)
            for track in raw_audio
            if isinstance(track, Mapping)
        ]
    if isinstance(raw_subtitles, Sequence) and not isinstance(raw_subtitles, (str, bytes)):
        subtitle_tracks = [
            _normalize_subtitle_track(track)
            for track in raw_subtitles
            if isinstance(track, Mapping)
        ]

    audio_tracks.sort(key=_json_key)
    subtitle_tracks.sort(key=_json_key)

    return {
        "duration_seconds": _normalize_int(title.get("duration_seconds")),
        "size_bytes": _normalize_int(title.get("size_bytes")),
        "chapters": _normalize_int(title.get("chapters")),
        "audio_tracks": audio_tracks,
        "subtitle_tracks": subtitle_tracks,
    }


def _title_sort_key(title: Mapping[str, Any]) -> tuple[Any, ...]:
    audio_tracks = title.get("audio_tracks", [])
    subtitle_tracks = title.get("subtitle_tracks", [])
    return (
        _normalize_int(title.get("duration_seconds")),
        _normalize_int(title.get("size_bytes")),
        _normalize_int(title.get("chapters")),
        len(audio_tracks) if isinstance(audio_tracks, Sequence) else 0,
        len(subtitle_tracks) if isinstance(subtitle_tracks, Sequence) else 0,
        _json_key(audio_tracks),
        _json_key(subtitle_tracks),
    )


def _hash_payload(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_disc_memory_record(
    titles: Sequence[Mapping[str, Any]],
    disc_info: Mapping[str, Any] | None = None,
    *,
    recorded_at: str | None = None,
) -> dict[str, Any] | None:
    if not titles:
        return None

    disc_info = disc_info or {}
    title_entries = [_normalize_title_entry(title) for title in titles]
    title_entries.sort(key=_title_sort_key)

    total_duration_seconds = sum(
        entry["duration_seconds"] for entry in title_entries
    )
    total_size_bytes = sum(entry["size_bytes"] for entry in title_entries)
    size_signature = _normalize_text(disc_info.get("size_signature"))
    if not size_signature:
        size_signature = ",".join(
            str(entry["size_bytes"]) for entry in title_entries
        )

    structure_payload: dict[str, Any] = {
        "title_count": len(title_entries),
        "total_duration_seconds": total_duration_seconds,
        "total_size_bytes": total_size_bytes,
        "titles": title_entries,
    }
    identity_payload = dict(structure_payload)

    disc_title = _normalize_text(disc_info.get("title"))
    volume_id = _normalize_text(disc_info.get("volume_id"))
    lang_code = _normalize_text(disc_info.get("lang_code"))
    lang_name = _normalize_text(disc_info.get("lang_name"))

    if size_signature:
        identity_payload["size_signature"] = size_signature
    if disc_title:
        identity_payload["disc_title"] = disc_title.lower()
    if volume_id:
        identity_payload["volume_id"] = volume_id.lower()
    if lang_code:
        identity_payload["lang_code"] = lang_code.lower()
    if lang_name:
        identity_payload["lang_name"] = lang_name.lower()

    return {
        "version": 1,
        "recorded_at": recorded_at or datetime.now().isoformat(timespec="seconds"),
        "disc_title": disc_title,
        "volume_id": volume_id,
        "lang_code": lang_code,
        "lang_name": lang_name,
        "title_count": len(title_entries),
        "total_duration_seconds": total_duration_seconds,
        "total_size_bytes": total_size_bytes,
        "size_signature": size_signature,
        "structure_hash": _hash_payload(structure_payload),
        "identity_hash": _hash_payload(identity_payload),
        "titles": title_entries,
    }


def compare_disc_memory_records(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not previous or not current:
        return None

    previous_identity = _normalize_text(previous.get("identity_hash"))
    current_identity = _normalize_text(current.get("identity_hash"))
    if previous_identity and previous_identity == current_identity:
        return {"match_type": "identity"}

    previous_structure = _normalize_text(previous.get("structure_hash"))
    current_structure = _normalize_text(current.get("structure_hash"))
    if previous_structure and previous_structure == current_structure:
        return {"match_type": "structure"}

    return None
