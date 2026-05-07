from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping, MutableSet, Sequence
from typing import Any, Protocol, cast


DiscTitle = Mapping[str, Any]
AnalyzedFile = tuple[str, float, float]
ResumableSession = tuple[str, str, Mapping[str, Any], int]
ResumeSelection = dict[str, Any]
LogFn = Callable[[str], None]
AskYesNoFn = Callable[[str], bool]
TitleIdFromFilenameFn = Callable[[str], int | None]


class FailureMetadataEngineLike(Protocol):
    def update_temp_metadata(self, rip_path: str, status: str | None = None, **updates: Any) -> None: ...

    def wipe_session_outputs(self, rip_path: str, on_log: LogFn) -> None: ...


def restore_selected_titles(
    disc_titles: Sequence[DiscTitle],
    resume_meta: Mapping[str, Any],
) -> list[int] | None:
    def _coerce_title_ids(raw_value: object) -> list[int]:
        title_ids: list[int] = []
        if isinstance(raw_value, Sequence) and not isinstance(raw_value, (str, bytes)):
            for raw_tid in cast(Sequence[object], raw_value):
                if isinstance(raw_tid, (int, str)):
                    title_ids.append(int(raw_tid))
        return title_ids

    saved = _coerce_title_ids(resume_meta.get("selected_titles"))
    if not saved:
        return None

    completed = set(_coerce_title_ids(resume_meta.get("completed_titles")))
    valid_ids = {int(title.get("id", -1)) for title in disc_titles}
    restored = [
        title_id for title_id in saved
        if title_id in valid_ids and title_id not in completed
    ]
    return restored or None


def map_title_ids_to_analyzed_indices(
    titles_list: Sequence[AnalyzedFile],
    title_ids: Sequence[int] | None,
    *,
    title_file_map: Mapping[object, object] | None,
    title_id_from_filename: TitleIdFromFilenameFn,
) -> list[int]:
    wanted = {int(title_id) for title_id in (title_ids or [])}
    if not wanted:
        return []

    tracked_lookup: dict[str, int] = {}
    for raw_tid, raw_files in (title_file_map or {}).items():
        if not isinstance(raw_tid, (int, str)):
            continue
        title_id = int(raw_tid)
        if title_id not in wanted:
            continue
        if not isinstance(raw_files, Sequence) or isinstance(raw_files, (str, bytes)):
            continue
        for path in cast(Sequence[object], raw_files):
            tracked_lookup[os.path.normcase(os.path.abspath(str(path)))] = title_id

    mapped: list[int] = []
    for index, (path, _duration, _mb) in enumerate(titles_list):
        norm = os.path.normcase(os.path.abspath(path))
        title_id = tracked_lookup.get(norm)
        if title_id is None:
            title_id = title_id_from_filename(path)
        if title_id in wanted:
            mapped.append(index)
    return mapped


def resume_session_matches(
    meta: Mapping[str, Any],
    media_type: str | None,
) -> bool:
    return not media_type or meta.get("media_type") in {None, media_type}


def build_resume_prompt(
    name: str,
    meta: Mapping[str, Any],
    file_count: int,
) -> str:
    title = meta.get("title", "Unknown")
    timestamp = meta.get("timestamp", name)
    phase = meta.get("phase", meta.get("status", "unknown"))
    return (
        f"Resume previous session?\n\n"
        f"Title: {title}\n"
        f"Started: {timestamp}\n"
        f"Phase: {phase}\n"
        f"Files so far: {file_count}\n\n"
        "This reloads saved workflow metadata only. Any partial "
        "rip files will be replaced by a fresh rip."
    )


def select_resumable_session(
    resumable: Iterable[ResumableSession],
    *,
    media_type: str | None,
    ask_yesno: AskYesNoFn | None,
    log_fn: LogFn,
) -> ResumeSelection | None:
    if not ask_yesno:
        return None

    for full_path, name, meta, file_count in resumable:
        if not resume_session_matches(meta, media_type):
            continue
        prompt = build_resume_prompt(name, meta, file_count)
        if ask_yesno(prompt):
            log_fn(f"Resuming session: {name}")
            return {
                "path": full_path,
                "name": name,
                "meta": meta,
            }
    return None


def mark_session_failed(
    engine: FailureMetadataEngineLike,
    rip_path: str,
    *,
    wiped_session_paths: MutableSet[str],
    log_fn: LogFn,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    log_fn("Session failed - wiping outputs.")
    engine.update_temp_metadata(
        rip_path,
        status="failed",
        phase="failed",
        **dict(metadata or {}),
    )
    if rip_path not in wiped_session_paths:
        engine.wipe_session_outputs(rip_path, log_fn)
        wiped_session_paths.add(rip_path)


def mark_session_aborted(
    engine: FailureMetadataEngineLike,
    rip_path: str,
    *,
    wiped_session_paths: MutableSet[str],
    log_fn: LogFn,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    """Mirror of ``mark_session_failed`` but for user-initiated abort.

    Contract (per ``docs/workflow-stabilization-criteria.md`` *"Abort
    propagation"*):

    - Writes ``status="aborted"`` / ``phase="aborted"`` so metadata
      accurately reflects the user's choice.
    - Wipes partial output files (``.mkv`` / ``.partial``) so no
      half-finished disc data sits in the temp folder.
    - Idempotent via ``wiped_session_paths`` — calling twice is a
      no-op on the wipe side.
    - Keeps the metadata file itself so a tombstone exists for
      diagnostics.  ``find_resumable_sessions`` filters
      ``phase="aborted"`` so the session does NOT show up in the
      resume picker.

    The result: aborted sessions are NOT recoverable.  The user
    explicitly chose to stop, so resume-from-where-they-left-off is
    intentionally unavailable — they start fresh on the next run.
    """
    log_fn("Session aborted by user - wiping partial outputs.")
    engine.update_temp_metadata(
        rip_path,
        status="aborted",
        phase="aborted",
        **dict(metadata or {}),
    )
    if rip_path not in wiped_session_paths:
        engine.wipe_session_outputs(rip_path, log_fn)
        wiped_session_paths.add(rip_path)
