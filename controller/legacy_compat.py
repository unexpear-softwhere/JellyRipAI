"""Compatibility helpers restored from the pre-refactor controller."""

import glob
import json
import os
import re
import shutil
import subprocess
import sys as _sys
import tempfile
import threading
import time
from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence, cast

from controller.library_scan import (
    episodes_from_filename,
    get_next_episode as get_next_library_episode,
    scan_episode_files,
    scan_highest_episode,
    scan_library_folder,
)
from controller.rip_validation import (
    SizeValidationStatus,
    compute_size_validation_status,
    log_expected_vs_actual_summary,
    normalize_rip_result,
    retry_rip_once_after_size_failure,
    verify_container_integrity,
    verify_expected_sizes,
)
from controller.session_recovery import (
    map_title_ids_to_analyzed_indices,
    mark_session_aborted,
    mark_session_failed,
    restore_selected_titles,
)
from controller.session_paths import (
    ensure_session_paths,
    get_session_path,
    init_session_paths,
    log_session_paths,
    validate_session_paths,
)
from config import resolve_vlc
from shared.runtime import __version__
from utils.helpers import clean_name, make_temp_title
from utils.media import select_largest_file
from utils.state_machine import SessionState, SessionStateMachine
from utils.scoring import choose_best_title


# Local-temp home for disposable watch/preview clips.  One source of
# truth so ``preview_title`` and the startup purge agree on the path.
_PREVIEW_TEMP_SUBDIR = "JellyRipAI"


def _preview_root() -> str:
    """Local-temp root for disposable watch/preview clips."""
    return os.path.join(tempfile.gettempdir(), _PREVIEW_TEMP_SUBDIR, "preview")


def purge_preview_temp() -> None:
    """Best-effort sweep of leftover preview clips from a prior run.

    Called once at startup.  Sample clips delete when the player
    closes and kept full-title watches are moved/discarded on
    continue — but a full-title watch the user never continued past
    (the app was closed first) or a failed/0-byte preview would
    otherwise linger in local temp.  Clearing the whole preview root
    at launch guarantees nothing accumulates across runs.
    """
    try:
        root = _preview_root()
        if os.path.isdir(root):
            shutil.rmtree(root, ignore_errors=True)
    except Exception:
        pass


DiscTitle = dict[str, Any]
DiscTitles = list[DiscTitle]
AnalyzedFile = tuple[str, float, float]
AnalyzedFiles = list[AnalyzedFile]
PathOverrides = dict[str, str]
PathField = tuple[str, str]


class _SessionHelpersLike(Protocol):
    def log(self, msg: str) -> None: ...
    def report(self, msg: str) -> None: ...
    def flush_log(self) -> None: ...
    def write_session_summary(self) -> None: ...
    def scan_with_retry(self) -> DiscTitles | None: ...
    def check_resume(self, temp_root: str, media_type: str | None = None) -> Any: ...


class LegacyControllerMixin:
    engine: Any
    gui: Any
    session_helpers: _SessionHelpersLike
    session_paths: PathOverrides | None
    _preview_lock: threading.Lock
    _wiped_session_paths: set[str]
    _current_rip_path: str | None
    session_report: list[str]
    sm: SessionStateMachine

    def log(self, message: str) -> None: ...
    def _stabilize_ripped_files(
        self,
        mkv_files: Sequence[str],
        expected_size_by_title: Mapping[int, int] | None = None,
    ) -> tuple[bool, bool]: ...

    def report(self, msg: str) -> None:
        return self.session_helpers.report(msg)

    def _warn_degraded_rips(self) -> None:
        """Add session warnings for any degraded titles from the last rip."""
        for tid in self.engine.last_degraded_titles:
            self.report(
                f"Title {tid}: MakeMKV read errors but output produced "
                f"(degraded rip — validated downstream by ffprobe)"
            )

    def _successful_title_ids_from_last_rip(
        self,
        selected_title_ids: Sequence[int],
    ) -> list[int]:
        raw_map = getattr(self.engine, "last_title_file_map", {}) or {}
        tracked: set[int] = set()
        if isinstance(raw_map, Mapping):
            for raw_tid, raw_files in raw_map.items():
                if not isinstance(raw_tid, (int, str)):
                    continue
                if not isinstance(raw_files, Sequence) or isinstance(
                    raw_files, (str, bytes)
                ):
                    continue
                if any(
                    str(path).strip()
                    for path in cast(Sequence[object], raw_files)
                ):
                    tracked.add(int(raw_tid))
        return [
            int(title_id)
            for title_id in selected_title_ids
            if int(title_id) in tracked
        ]

    def _begin_partial_rip_session(
        self,
        rip_path: str,
        selected_title_ids: Sequence[int],
        failed_titles: Sequence[Any] | None,
        mkv_files: Sequence[str],
        expected_size_by_title: Mapping[int, int] | None,
        *,
        label: str,
        title: str,
        year: str | None,
        media_type: str | None,
        season: int | None = None,
        dest_folder: str | None = None,
        required_title_ids: Sequence[int] | None = None,
    ) -> tuple[bool, list[int], list[str], dict[int, int] | None]:
        fallback_expected = (
            dict(expected_size_by_title) if expected_size_by_title else None
        )
        if self.engine.abort_flag or not failed_titles or not mkv_files:
            return False, [], list(mkv_files), fallback_expected

        valid_files = [
            path
            for path in mkv_files
            if self.engine._quick_ffprobe_ok(path, self.log)
        ]
        if len(valid_files) != len(mkv_files):
            return False, [], list(mkv_files), fallback_expected

        successful_title_ids = self._successful_title_ids_from_last_rip(
            selected_title_ids
        )
        if not successful_title_ids:
            return False, [], list(mkv_files), fallback_expected

        if required_title_ids:
            required = {int(title_id) for title_id in required_title_ids}
            if not required.intersection(successful_title_ids):
                self.log(
                    "Partial rip produced files, but none match the required "
                    "main title selection. Treating session as failed."
                )
                return False, [], list(mkv_files), fallback_expected

        filtered_expected = None
        if expected_size_by_title:
            filtered_expected = {
                int(title_id): int(
                    expected_size_by_title.get(int(title_id), 0) or 0
                )
                for title_id in successful_title_ids
                if int(title_id) in expected_size_by_title
            }

        failed_list = [
            int(title_id)
            for title_id in failed_titles
            if isinstance(title_id, (int, str))
        ]
        self.report(
            f"{label}: keeping successful output(s) and preserving session as "
            f"partial; failed titles = {failed_list}"
        )
        self.log(
            f"Continuing with {len(valid_files)} validated file(s) from "
            f"{len(successful_title_ids)} successful title(s)."
        )
        self.engine.update_temp_metadata(
            rip_path,
            status="partial",
            phase="analyzing",
            title=title,
            year=year,
            media_type=media_type,
            season=season,
            selected_titles=list(selected_title_ids),
            completed_titles=list(successful_title_ids),
            failed_titles=failed_list,
            dest_folder=dest_folder,
        )
        return True, successful_title_ids, valid_files, filtered_expected

    def _preserve_partial_session(
        self,
        rip_path: str,
        *,
        title: str,
        year: str | None,
        media_type: str | None,
        season: int | None = None,
        selected_titles: Sequence[int] | None = None,
        completed_titles: Sequence[int] | None = None,
        failed_titles: Sequence[Any] | None = None,
        dest_folder: str | None = None,
    ) -> None:
        failed_list = [
            int(title_id)
            for title_id in (failed_titles or [])
            if isinstance(title_id, (int, str))
        ]
        self.engine.update_temp_metadata(
            rip_path,
            status="partial",
            phase="partial",
            title=title,
            year=year,
            media_type=media_type,
            season=season,
            selected_titles=list(selected_titles or []),
            completed_titles=list(completed_titles or []),
            failed_titles=failed_list,
            dest_folder=dest_folder,
        )
        self.log(f"Partial session preserved at: {rip_path}")

    def _reset_state_machine(self) -> None:
        self.sm = SessionStateMachine(
            debug=bool(self.engine.cfg.get("opt_debug_state", False)),
            logger=self.log,
        )

    def _state_transition(self, new_state: SessionState) -> None:
        self.sm.transition(new_state)
        if self.engine.cfg.get("opt_debug_state_json", False):
            self.log(
                "STATE_JSON: " + json.dumps(
                    {
                        "event": "transition",
                        "state": self.sm.state.name,
                        "time": datetime.now().isoformat(timespec="seconds"),
                    }
                )
            )

    def _state_fail(self, reason: str) -> None:
        self.sm.fail(reason)
        if self.engine.cfg.get("opt_debug_state_json", False):
            self.log(
                "STATE_JSON: " + json.dumps(
                    {
                        "event": "fail",
                        "reason": reason,
                        "state": self.sm.state.name,
                        "time": datetime.now().isoformat(timespec="seconds"),
                    }
                )
            )

    def _record_fallback_event(
        self,
        reason: str,
        accepted: bool,
        strict: bool,
    ) -> None:
        if not self.engine.cfg.get("opt_debug_state_json", False):
            return
        self.log(
            "STATE_JSON: " + json.dumps(
                {
                    "event": "fallback",
                    "reason": reason,
                    "accepted": bool(accepted),
                    "strict": bool(strict),
                    "time": datetime.now().isoformat(timespec="seconds"),
                }
            )
        )

    def flush_log(self) -> None:
        return self.session_helpers.flush_log()

    def write_session_summary(self) -> None:
        return self.session_helpers.write_session_summary()

    def scan_with_retry(self) -> DiscTitles | None:
        return self.session_helpers.scan_with_retry()

    def _log_drive_compatibility(self) -> bool:
        """Log and display LibreDrive / UHD / disc-type compatibility.

        Returns False if the user chose to abort after a UHD warning,
        True in all other cases (including when there is nothing to report).
        """
        from engine.scan_ops import format_drive_compatibility
        info = getattr(self.engine, "last_drive_info", None)
        if not info:
            return True
        lines = format_drive_compatibility(info)
        if not lines:
            return True

        # --- Log block (always) ---
        self.log("--- Drive / Disc Compatibility ---")
        for line in lines:
            self.log(f"  {line}")
        self.log("----------------------------------")

        # Debug: raw MakeMKV LibreDrive line for troubleshooting
        raw = info.get("libre_drive_raw", "")
        if raw:
            self.log(f"[AI][DEBUG] LibreDrive raw: \"{raw}\"")

        # --- UI dialog: show drive status in scan results panel ---
        disc_type = info.get("disc_type")
        ld = info.get("libre_drive")
        dialog_lines = ["Drive Status:"] + [f"  {ln}" for ln in lines]

        # UHD + LibreDrive not enabled -> warn before rip
        if disc_type == "UHD" and ld != "enabled":
            dialog_lines.append("")
            if ld == "possible":
                dialog_lines.append(
                    "Your drive may support LibreDrive but it is not active.\n"
                    "A firmware patch may enable full UHD support."
                )
            else:
                dialog_lines.append(
                    "Without LibreDrive, UHD rips often fail or produce\n"
                    "degraded output. Consider using a LibreDrive-capable drive."
                )
            dialog_lines.append("")
            dialog_lines.append("Continue anyway?")
            return self.gui.ask_yesno("\n".join(dialog_lines))

        # Non-critical: show as info (not blocking)
        self.gui.show_info("Drive Status", "\n".join(dialog_lines))
        return True

    def check_resume(self, temp_root: str, media_type: str | None = None) -> Any:
        return self.session_helpers.check_resume(temp_root, media_type)

    def _init_session_paths(self, overrides: Mapping[str, str] | None = None) -> None:
        """Initialize per-run path state from defaults plus optional overrides."""
        self.session_paths = init_session_paths(self.engine.cfg, overrides)

    def get_path(self, key: str) -> str:
        return get_session_path(self.session_paths, key)

    def _log_session_paths(self) -> None:
        log_session_paths(self.session_paths, version=__version__, log_fn=self.log)

    def _validate_paths(
        self,
        temp: str | None,
        movies: str | None = None,
        tv: str | None = None,
    ) -> str | None:
        return validate_session_paths(temp, movies=movies, tv=tv)

    def _prompt_run_path_overrides(
        self,
        path_fields: Sequence[PathField],
    ) -> PathOverrides | None:
        """Optionally override folder paths for this run only.

        path_fields: list of tuples (config_key, human_label).
        Returns dict of resolved paths, or None if aborted.
        """
        resolved = {
            key: os.path.normpath(self.engine.cfg.get(key, ""))
            for key, _label in path_fields
        }

        if not path_fields:
            return resolved

        if not self.gui.ask_yesno(
            "Use custom folders for this run?\n\n"
            "Yes = browse/select per-mode paths\n"
            "No = use saved defaults"
        ):
            if self.engine.abort_event.is_set():
                return None
            return resolved
        if self.engine.abort_event.is_set():
            return None

        self.log("Custom run-folder override selected — collecting paths.")

        for key, label in path_fields:
            if self.engine.abort_event.is_set():
                return None
            default_path = resolved[key]
            used_picker = callable(getattr(self.gui, "ask_directory", None))

            if used_picker:
                self.log(
                    f"Opening folder picker — {label} (default: {default_path})"
                )
                chosen = self.gui.ask_directory(
                    f"{label} (Run Override)",
                    "Choose folder (Cancel = keep default)",
                    initialdir=default_path,
                )
                if self.engine.abort_event.is_set():
                    return None
                if chosen is None:
                    resolved[key] = default_path
                    self.log(
                        f"Folder picker closed/cancelled — {label}: using default {default_path}"
                    )
                    continue
                self.log(f"Folder picker result — {label}: {chosen}")
                chosen = os.path.normpath(str(chosen).strip())
                if os.path.isdir(chosen):
                    resolved[key] = chosen
                    self.log(f"Run override — {label}: {chosen}")
                    continue
                try:
                    os.makedirs(chosen, exist_ok=True)
                    resolved[key] = chosen
                    self.log(f"Run override — {label}: {chosen}")
                    continue
                except Exception as e:
                    self.log(
                        f"Could not create folder '{chosen}': {e}"
                    )
                    resolved[key] = default_path
                    self.log(
                        f"Falling back to default {label}: {default_path}"
                    )
                    continue

            while True:
                entered = self.gui.ask_input(
                    f"{label} (Run Override)",
                    "Enter folder path (Skip = keep default):",
                    default_value=default_path,
                )
                if self.engine.abort_event.is_set():
                    return None
                if entered is None:
                    resolved[key] = default_path
                    self.log(
                        f"Run override cancelled — {label}: using default {default_path}"
                    )
                    break

                chosen = default_path if entered == "" else str(entered).strip()
                if not chosen:
                    chosen = default_path
                chosen = os.path.normpath(chosen)

                if os.path.isdir(chosen):
                    resolved[key] = chosen
                    self.log(f"Run override — {label}: {chosen}")
                    break

                if self.gui.ask_yesno(
                    f"Folder does not exist:\n{chosen}\n\nCreate it?"
                ):
                    if self.engine.abort_event.is_set():
                        return None
                    try:
                        os.makedirs(chosen, exist_ok=True)
                        resolved[key] = chosen
                        self.log(f"Run override — {label}: {chosen}")
                        break
                    except Exception as e:
                        self.log(
                            f"Could not create folder '{chosen}': {e}"
                        )
                else:
                    self.log(
                        f"Re-enter {label} or click Skip to keep default."
                    )

        error = self._validate_paths(
            resolved.get("temp_folder"),
            movies=resolved.get("movies_folder"),
            tv=resolved.get("tv_folder"),
        )
        if error:
            self.log(f"ERROR: {error}")
            self.gui.show_error("Invalid Run Paths", error)
            return None

        if self.engine.abort_event.is_set():
            return None
        self.log("Custom folders set, continuing...")

        return resolved

    def _restore_selected_titles(
        self,
        disc_titles: DiscTitles,
        resume_meta: Mapping[str, Any],
    ) -> list[int] | None:
        return restore_selected_titles(disc_titles, resume_meta)

    def _map_title_ids_to_analyzed_indices(
        self,
        titles_list: AnalyzedFiles,
        title_ids: Sequence[int] | None,
    ) -> list[int]:
        """Map MakeMKV title ids to analyze_files indices.

        Primary: explicit engine tracking from rip_selected_titles.
        Fallback: filename parsing tags for legacy compatibility.
        """
        wanted = {int(tid) for tid in (title_ids or [])}
        if not wanted:
            return []
        tracked_obj = getattr(self.engine, "last_title_file_map", {}) or {}
        tracked_map: Mapping[object, object]
        if isinstance(tracked_obj, Mapping):
            tracked_map = cast(Mapping[object, object], tracked_obj)
        else:
            tracked_map = {}
        return map_title_ids_to_analyzed_indices(
            titles_list,
            list(wanted),
            title_file_map=tracked_map,
            title_id_from_filename=self._title_id_from_filename,
        )

    def _fallback_title_from_mode(self, disc_titles: DiscTitles | None = None) -> str:
        """Build fallback title string based on configured naming mode."""
        from controller import controller as controller_module

        disc_name = self.engine.last_disc_info.get("title")
        title: str = controller_module.build_fallback_title(
            self.engine.cfg,
            make_temp_title,
            clean_name,
            choose_best_title,
            disc_titles=disc_titles,
            disc_name=disc_name,
        )
        self.log(f"Auto-title fallback used: '{title}'")
        return title

    def _log_ripped_file_sizes(self, mkv_files: Sequence[str]) -> None:
        """Log final sizes for newly ripped files so anomalies stand out."""
        for f in sorted(mkv_files):
            try:
                size_gb = os.path.getsize(f) / (1024**3)
                self.log(
                    f"Ripped file: {os.path.basename(f)} — {size_gb:.2f} GB"
                )
            except Exception as e:
                self.log(
                    f"Ripped file: {os.path.basename(f)} — size unavailable ({e})"
                )

    @staticmethod
    def _format_size_label(size_bytes: int) -> str:
        if size_bytes >= 1024**3:
            return f"{size_bytes / (1024**3):.2f} GB"
        return f"{size_bytes / (1024**2):.0f} MB"

    def _build_output_title_groups(
        self, mkv_files: Sequence[str]
    ) -> list[tuple[str, list[str]]]:
        lookup = {
            os.path.normcase(os.path.abspath(path)): str(path)
            for path in mkv_files
        }
        assigned: set[str] = set()
        grouped: dict[int, list[str]] = {}

        tracked_obj = getattr(self.engine, "last_title_file_map", {}) or {}
        tracked_map: dict[int, list[str]] = {}
        for raw_tid, raw_files in tracked_obj.items():
            try:
                tid = int(raw_tid)
            except Exception:
                continue
            if not isinstance(raw_files, Sequence) or isinstance(
                raw_files, (str, bytes)
            ):
                continue
            matched: list[str] = []
            for path in raw_files:
                resolved = lookup.get(
                    os.path.normcase(os.path.abspath(str(path)))
                )
                if resolved:
                    matched.append(resolved)
            if matched:
                tracked_map[tid] = sorted(
                    matched,
                    key=lambda p: (
                        os.path.basename(p).lower(),
                        os.path.normpath(p).lower(),
                    ),
                )
                assigned.update(tracked_map[tid])

        grouped.update(tracked_map)

        for path in mkv_files:
            if path in assigned:
                continue
            tid = self._title_id_from_filename(path)
            if tid is None:
                continue
            grouped.setdefault(tid, []).append(path)
            assigned.add(path)

        result: list[tuple[str, list[str]]] = []
        for tid in sorted(grouped):
            files = sorted(
                grouped[tid],
                key=lambda p: (
                    os.path.basename(p).lower(),
                    os.path.normpath(p).lower(),
                ),
            )
            result.append((f"Title {tid + 1}", files))

        leftovers = [
            path for path in sorted(
                mkv_files,
                key=lambda p: (
                    os.path.basename(p).lower(),
                    os.path.normpath(p).lower(),
                ),
            )
            if path not in assigned
        ]
        for path in leftovers:
            result.append((os.path.basename(path), [path]))
        return result

    def _log_dump_output_summary(self, mkv_files: Sequence[str]) -> int:
        groups = self._build_output_title_groups(mkv_files)
        if not groups:
            return 0
        self.log(
            f"Dump output summary: {len(mkv_files)} file(s) across "
            f"{len(groups)} title group(s)."
        )
        for label, files in groups:
            total_bytes = 0
            file_sizes: list[tuple[str, int]] = []
            for path in files:
                try:
                    size_bytes = os.path.getsize(path)
                except Exception:
                    size_bytes = 0
                total_bytes += size_bytes
                file_sizes.append((path, size_bytes))
            file_word = "file" if len(files) == 1 else "files"
            self.log(
                f"  {label}: {len(files)} {file_word}, "
                f"{self._format_size_label(total_bytes)} total"
            )
            if len(files) > 1:
                for path, size_bytes in file_sizes:
                    self.log(
                        f"    - {os.path.basename(path)} "
                        f"({self._format_size_label(size_bytes)})"
                    )
        return len(groups)

    def _title_id_from_filename(self, path: str) -> int | None:
        name = os.path.basename(path)
        # Suffix-anchored: MakeMKV emits "<DiscLabel>_tNN.mkv"; the
        # literal "title_tNN" only appears for label-less discs.
        m = re.search(r'_t(\d+)(?:_part\d+)?\.mkv$', name, re.IGNORECASE)
        if not m:
            return None
        try:
            return int(m.group(1))
        except Exception:
            return None

    def _size_validation_status(
        self,
        actual_bytes: int,
        expected_bytes: int,
    ) -> tuple[SizeValidationStatus, str, float]:
        return compute_size_validation_status(
            actual_bytes,
            expected_bytes,
            hard_fail_ratio_pct=float(
                self.engine.cfg.get("opt_hard_fail_ratio_pct", 40)
            ),
            expected_size_ratio_pct=float(
                self.engine.cfg.get("opt_expected_size_ratio_pct", 70)
            ),
        )

    def _verify_expected_sizes(
        self,
        mkv_files: Sequence[str],
        expected_size_by_title: Mapping[int, int] | None,
    ) -> tuple[SizeValidationStatus, str]:
        return verify_expected_sizes(
            mkv_files,
            expected_size_by_title,
            safe_mode=bool(self.engine.cfg.get("opt_safe_mode", True)),
            hard_fail_ratio_pct=float(
                self.engine.cfg.get("opt_hard_fail_ratio_pct", 40)
            ),
            expected_size_ratio_pct=float(
                self.engine.cfg.get("opt_expected_size_ratio_pct", 70)
            ),
            log_fn=self.log,
        )

    def _log_expected_vs_actual_summary(
        self,
        mkv_files: Sequence[str],
        expected_size_by_title: Mapping[int, int] | None,
    ) -> None:
        log_expected_vs_actual_summary(
            mkv_files,
            expected_size_by_title,
            log_fn=self.log,
        )

    def _ensure_session_paths(self) -> None:
        """Hard guard: raises if session_paths has not been initialized."""
        ensure_session_paths(self.session_paths)

    def _verify_container_integrity(
        self,
        mkv_files: Sequence[str],
        analyzed: AnalyzedFiles | None = None,
        expected_durations: Mapping[str, float] | None = None,
        expected_sizes: Mapping[str, int] | None = None,
        title_file_map: Mapping[int, Sequence[str]] | None = None,
    ) -> bool:
        return verify_container_integrity(
            mkv_files,
            analyzed=analyzed,
            analyze_files=self.engine.analyze_files,
            expected_durations=expected_durations,
            expected_sizes=expected_sizes,
            title_file_map=title_file_map,
            strict=bool(self.engine.cfg.get("opt_strict_mode", False)),
            log_fn=self.log,
            report_fn=self.report,
        )

    def _normalize_rip_result(
        self,
        rip_path: str,
        success: bool,
        failed_titles: Sequence[Any] | None,
        pre_existing_files: frozenset[str] | None = None,
    ) -> tuple[bool, list[str]]:
        return normalize_rip_result(
            rip_path,
            success,
            failed_titles,
            pre_existing_files=pre_existing_files,
            safe_glob=self._safe_glob,
            quick_ffprobe_ok=self.engine._quick_ffprobe_ok,
            abort_flag=bool(self.engine.abort_flag),
            log_fn=self.log,
        )

    @staticmethod
    def get_next_episode(existing: set[int]) -> int:
        return get_next_library_episode(existing)

    def _episodes_from_filename(self, fname: str, season: int) -> set[int]:
        return episodes_from_filename(fname, season)

    def _scan_episode_files(self, folder: str | None, season: int) -> set[int]:
        return scan_episode_files(folder, season)

    def _scan_library_folder(self, show_root: str | None) -> dict[int, list[int]]:
        return scan_library_folder(show_root, log_fn=self.log)

    def _scan_highest_episode(self, dest_folder: str | None, season: int) -> int:
        return scan_highest_episode(dest_folder, season)

    def _mark_session_failed(self, rip_path: str, **metadata: Any) -> None:
        mark_session_failed(
            self.engine,
            rip_path,
            wiped_session_paths=self._wiped_session_paths,
            log_fn=self.log,
            metadata=metadata,
        )

    def _mark_session_aborted(self, rip_path: str, **metadata: Any) -> None:
        """Wire ``mark_session_aborted`` from session_recovery into
        the controller's wiped-session set + log.  Called on each
        workflow's abort-return path so aborted sessions don't leak
        into the resume picker."""
        mark_session_aborted(
            self.engine,
            rip_path,
            wiped_session_paths=self._wiped_session_paths,
            log_fn=self.log,
            metadata=metadata,
        )

    def _finalize_abort_cleanup_if_needed(self) -> None:
        """Workflow abort-cleanup hook.

        Called from each rip-producing workflow's outer ``finally``
        block.  If the abort flag is set AND a rip session was
        opened (``_current_rip_path`` populated by
        ``write_temp_metadata``) AND the session hasn't already
        reached a terminal state (complete / organized / failed /
        aborted) or a deliberately-preserved one (partial / moving),
        marks it aborted and wipes partial outputs.

        Idempotent — the wiped-paths set guards against double-wipe
        if multiple finallies fire (e.g., nested workflow calls).
        Resets ``_current_rip_path`` after handling so the next run
        starts clean.
        """
        rip_path = self._current_rip_path
        if not rip_path:
            return
        try:
            if not self.engine.abort_event.is_set():
                return
            if rip_path in self._wiped_session_paths:
                return
            meta = self.engine.read_temp_metadata(rip_path)
            if meta is None:
                return
            phase = meta.get("phase")
            if phase in {
                "complete", "organized", "failed", "aborted",
                # Deliberately-preserved outcomes, not just terminal
                # ones: "partial" sessions promise the user "Temp
                # preserved at ..." for retry, and "moving" sessions
                # hold fully-validated MKVs mid-move.  Wiping either
                # destroys data the UI said it kept.
                "partial", "moving",
            }:
                return
            self._mark_session_aborted(rip_path)
        finally:
            self._current_rip_path = None

    def _safe_glob(
        self,
        pattern: str,
        recursive: bool = False,
        timeout: float = 8.0,
        context: str = "glob",
    ) -> list[str]:
        """Run glob with timeout so slow/offline shares do not block flow."""
        matches: list[str] = []
        err: list[Exception | None] = [None]

        def _scan() -> None:
            try:
                matches.extend(glob.glob(pattern, recursive=recursive))
            except Exception as e:
                err[0] = e

        t = threading.Thread(target=_scan, daemon=True)
        t.start()
        t.join(timeout=timeout)
        if t.is_alive():
            self.log(f"WARNING: {context} timed out; continuing.")
            return []
        if err[0] is not None:
            self.log(f"WARNING: {context} failed: {err[0]}")
            return []
        return matches

    # ------------------------------------------------------------------
    # Watched-rip registry — full-title watch rips kept for reuse.
    # Watching a title rips ALL of it, which is exactly what the real
    # rip would produce — so a watched title that stays checked is
    # moved into the session instead of ripping twice; uncheck it and
    # it's deleted when the run continues (user rule, 2026-06-12).
    # ------------------------------------------------------------------

    def _watched_rip_lock(self) -> threading.Lock:
        lock = getattr(self, "_watched_rips_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._watched_rips_lock = lock
        return lock

    def _register_watched_rip(self, title_id: int, path: str) -> None:
        """Remember a completed full-title watch rip for reuse."""
        with self._watched_rip_lock():
            registry = getattr(self, "_watched_rips", None)
            if registry is None:
                registry = {}
                self._watched_rips = registry
            registry[int(title_id)] = str(path)

    def discard_watched_rips(self, keep_ids: Sequence[int] = ()) -> None:
        """Delete watched-rip files (except ``keep_ids``) and forget
        them.  Called when the user continues with a title unchecked
        and when a new disc starts (title numbers restart per disc,
        so stale entries must never be reused)."""
        keep = {int(k) for k in keep_ids}
        with self._watched_rip_lock():
            registry = getattr(self, "_watched_rips", None) or {}
            for tid in list(registry):
                if tid in keep:
                    continue
                path = registry.pop(tid)
                try:
                    os.remove(path)
                except OSError:
                    pass
                try:
                    os.rmdir(os.path.dirname(path))
                except OSError:
                    pass

    def _reuse_watched_rips(
        self, selected_ids: Sequence[int], rip_path: str,
    ) -> set[int]:
        """Move watched full-title rips for still-selected titles into
        the session rip folder so they are not ripped a second time;
        discard the rest.  Returns the reused title ids."""
        # Never run two MakeMKV processes against the drive at once —
        # wait out an in-flight watch rip before the real rip starts.
        lock = getattr(self, "_preview_lock", None)
        if lock is not None:
            if not lock.acquire(blocking=False):
                self.log(
                    "Waiting for the in-progress watch rip to finish "
                    "before starting the disc rip..."
                )
                lock.acquire()
            lock.release()

        reused: set[int] = set()
        for tid in selected_ids:
            with self._watched_rip_lock():
                registry = getattr(self, "_watched_rips", None) or {}
                src = registry.pop(int(tid), None)
            if not src or not os.path.isfile(src):
                continue
            dst = os.path.join(rip_path, os.path.basename(src))
            try:
                shutil.move(src, dst)
            except Exception as e:
                self.log(
                    f"Title {int(tid) + 1}: could not reuse the "
                    f"watched rip ({e}); it will rip normally."
                )
                continue
            try:
                os.rmdir(os.path.dirname(src))
            except OSError:
                pass
            reused.add(int(tid))
            self.log(
                f"Title {int(tid) + 1}: reusing the watched rip — "
                "no second rip needed."
            )
        # Anything still registered was watched but left unchecked.
        self.discard_watched_rips()
        return reused

    def preview_title(
        self, title_id: int, preview_seconds: int | None = None
    ) -> None:
        """Rip a short preview clip for one title and open it in VLC.

        ``preview_seconds`` controls the sample length: 0 (the
        default) rips the FULL title — the watch-before-rip flow —
        and a positive value rips a quick sample, clamped to a sane
        5–600 s range.  ``None`` falls back to the configured
        ``opt_preview_seconds``.
        """
        try:
            if preview_seconds is None:
                seconds = int(
                    self.engine.cfg.get("opt_preview_seconds", 0) or 0
                )
            else:
                seconds = int(preview_seconds)
        except (TypeError, ValueError):
            seconds = 0
        seconds = 0 if seconds <= 0 else max(5, min(seconds, 600))
        # Preview clips are disposable, so rip them to LOCAL temp —
        # never the configured temp root.  The temp root may be a
        # network share (UNC path), and VLC refuses ``file://`` MRLs
        # that carry a remote host (field failure 2026-06-12: "VLC is
        # unable to open the MRL 'file://DESKTOP-…/MediaHub/…'").
        # Local previews also skip the network round-trip while
        # MakeMKV writes, so samples start faster.
        # Each title gets its own subfolder so a KEPT watch rip of one
        # title survives watching another — the per-run cleanup below
        # and MakeMKV's pre-rip purge stay scoped to this title only.
        preview_dir = os.path.join(_preview_root(), f"t{title_id:02d}")

        try:
            shutil.rmtree(preview_dir, ignore_errors=True)
            os.makedirs(preview_dir, exist_ok=True)
        except Exception as e:
            self.log(f"Preview setup failed: {e}")
            return

        def run_preview() -> None:
            lock_acquired = False
            try:
                if not self._preview_lock.acquire(blocking=False):
                    self.log("Preview already running. Wait for it to finish.")
                    return
                lock_acquired = True

                if self.engine.abort_event.is_set():
                    self.log("Preview skipped: abort requested.")
                    return
                length_text = (
                    "full title" if seconds == 0 else f"{seconds}s sample"
                )
                self.log(
                    f"Preview: starting Title {title_id + 1} "
                    f"({length_text})..."
                )
                if seconds == 0:
                    self.log(
                        "Watching the full title — it rips to local "
                        "temp first, so this can take a few minutes."
                    )
                self.gui.set_status(
                    f"Ripping Title {title_id + 1} to watch... "
                    f"({length_text})"
                )
                preview_ok = self.engine.rip_preview_title(
                    preview_dir, title_id, seconds, self.log
                )

                # MakeMKV may create output in nested paths and can finish
                # metadata flush shortly after process stop. Poll briefly.
                files = []
                for _ in range(8):
                    files = self._safe_glob(
                        os.path.join(preview_dir, "**", "*.mkv"),
                        recursive=True,
                        context="Scanning preview outputs",
                    )
                    if files:
                        break
                    time.sleep(0.25)
                if not files:
                    if not preview_ok:
                        self.log("Preview failed: rip process did not complete.")
                    else:
                        self.log("Preview failed: no preview file found.")
                    return

                latest = select_largest_file(files)
                if not latest:
                    self.log("Preview failed: preview file selection returned nothing.")
                    return
                try:
                    size_mb = os.path.getsize(latest) / (1024**2)
                except Exception:
                    size_mb = 0.0
                analyzed = self.engine.analyze_files([latest], self.log)
                duration = int(analyzed[0][1]) if analyzed else 0
                mm = duration // 60
                ss = duration % 60
                self.log(
                    f"Preview candidate: {os.path.basename(latest)} | "
                    f"{size_mb:.0f} MB | {mm:02d}:{ss:02d}"
                )
                if seconds == 0:
                    # A full-title watch IS a real rip — keep it.
                    # Still checked when the run continues → moved
                    # into the session, no second rip; unchecked →
                    # deleted then.
                    self._register_watched_rip(title_id, latest)
                    self.log(
                        "Watched rip kept for reuse — leave the title "
                        "checked and it won't rip twice; uncheck it "
                        "and it's deleted when you continue."
                    )
                vlc_result = resolve_vlc(
                    allow_path_lookup=bool(
                        self.engine.cfg.get(
                            "opt_allow_path_tool_resolution",
                            False,
                        )
                    ),
                )
                if vlc_result.path:
                    if _sys.platform == "win32":
                        player_proc = subprocess.Popen(
                            [vlc_result.path, latest],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=0x08000000,
                            shell=False,
                        )
                    else:
                        player_proc = subprocess.Popen(
                            [vlc_result.path, latest],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            shell=False,
                        )
                    self.log(
                        f"Preview player resolved via {vlc_result.source}: "
                        f"{vlc_result.path}"
                    )
                    self.log(
                        f"Preview opened in VLC: {os.path.basename(latest)}"
                    )

                    if seconds > 0:
                        # Sample clips are useless afterward — delete
                        # once the player exits.  Full-title watches
                        # are KEPT (registered above) for reuse.
                        def _delete_clip_after_player(
                            proc=player_proc, clip=latest,
                        ):
                            # Windows locks the clip while the player
                            # has it open, so wait for the player to
                            # exit and only then remove it.
                            try:
                                proc.wait()
                            except Exception:
                                pass
                            try:
                                os.remove(clip)
                            except OSError:
                                pass
                            try:
                                os.rmdir(os.path.dirname(clip))
                            except OSError:
                                pass

                        threading.Thread(
                            target=_delete_clip_after_player,
                            daemon=True,
                        ).start()
                        self.log(
                            "Preview clip is temporary — it is "
                            "deleted when the player closes."
                        )
                else:
                    self.log(
                        f"VLC not found; opening in default player: {os.path.basename(latest)}"
                    )
                    os.startfile(latest)
                    if seconds > 0:
                        self.log(
                            "Preview clip stays in local temp until "
                            "the next preview replaces it (the "
                            "default player does not report when it "
                            "closes)."
                        )
            except Exception as e:
                self.log(f"Preview open failed: {e}")
            finally:
                # Only clear abort if no rip is running — otherwise
                # preview cleanup would cancel a real abort request.
                rip = getattr(self.gui, "rip_thread", None)
                if not (rip and rip.is_alive()):
                    self.engine.reset_abort()
                self.gui.set_status("Ready")
                if lock_acquired:
                    self._preview_lock.release()

        threading.Thread(target=run_preview, daemon=True).start()

    def _retry_rip_once_after_size_failure(
        self,
        rip_path: str,
        selected_ids: Sequence[int],
        expected_size_by_title: Mapping[int, int] | None,
    ) -> bool:
        return retry_rip_once_after_size_failure(
            self,
            rip_path,
            selected_ids,
            expected_size_by_title,
        )
