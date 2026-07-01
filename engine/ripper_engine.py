"""Engine layer implementation."""

import glob
import json
import os
import queue as queue_module
import re
import shutil
import subprocess
import sys as _sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generator, List, Mapping, Sequence

_POPEN_FLAGS = {"creationflags": 0x08000000} if _sys.platform == "win32" else {}

# MakeMKV robot-mode message line: MSG:code,flags,count,"message","format",...
# The 4th field ("message") is the fully-resolved human text; the 5th
# ("format") is the %1/%2 template.  Capture the resolved message with a
# quote-aware match so commas inside it (e.g. "added (6 cell(s), 0:22:01)")
# don't truncate it the way a positional comma-split would.
_MSG_MESSAGE_RE = re.compile(r'^MSG:[^,]*,[^,]*,[^,]*,"([^"]*)"')

# ---------------------------------------------------------------------------
# Module-level constants (audit #30: named where magic numbers used to live).
# Centralized here so the value, semantics, and rationale are documented
# in one place rather than scattered as bare literals throughout the file.
# ---------------------------------------------------------------------------

# Diagnostic raw-line capture cap.  Bounds memory if makemkvcon spews
# lots of output during a rip or size scan.  5000 lines is roughly the
# upper bound observed for very chatty discs in smoke testing.
_DIAG_RAW_LINES_MAX = 5000

# Title-scoring ambiguity threshold.  When the top two scored titles
# are within this score delta, the auto-pick is flagged as a close
# call in the log.  Empirical value from smoke testing — tighter
# than this produces too many false-positive warnings on extras-heavy
# discs.
_AMBIGUOUS_TOP_SCORE_DELTA = 0.05

# Disc-size-scan cache TTL, in seconds.  A repeat scan of the same
# drive target within this window can reuse the previously computed
# total without re-launching makemkvcon.
_SCAN_CACHE_TTL_SECONDS = 300

# Session-log rollover threshold, in bytes.  When the live log file
# crosses this size, the engine rolls it over (start/end dates
# preserved) rather than appending forever.
_SESSION_LOG_ROLLOVER_BYTES = 5 * 1024**3


def _norm_tool_path(raw: object) -> str:
    """Normalize a configured tool path while preserving the empty string.

    ``os.path.normpath("")`` returns ``"."``, which the tool resolvers
    mis-read as a configured *directory* (the current dir).  When that
    "directory" has no tool, ``_resolve_required_tool`` returns the
    configured-path failure and only *attaches* the bundled binary as a
    suggestion — so the bundled FFmpeg/ffprobe is found but discarded.
    A blank config means "auto-detect the bundled copy", so it must stay
    blank (``""``) and never collapse to ``"."``.
    """
    text = str(raw or "").strip()
    return os.path.normpath(text) if text else ""

from shared.runtime import RIP_ATTEMPT_FLAGS
from shared.ai_diagnostics import (
    ProcessCapture, diag_exception, diag_process, diag_record, get_diagnostics,
)
from shared.disc_memory import (
    build_disc_memory_record,
    compare_disc_memory_records,
)

from config import resolve_ffprobe, resolve_makemkvcon
from utils.helpers import (
    MakeMKVDrive,
    clean_name,
    format_makemkv_drive_label,
    get_available_drives,
    parse_makemkv_drive_row,
)
from utils.parsing import (
    parse_cli_args,
    parse_duration_to_seconds,
    parse_size_to_bytes,
    safe_int,
)
from utils.makemkv_log import (
    MakeMKVIssueSummary,
    MakeMKVMessageCoalescer,
    analyze_makemkv_messages,
)
from utils.scoring import score_title
from utils.classifier import classify_titles, format_classification_log

SESSION_METADATA_FILENAMES = frozenset({
    "_rip_meta.json",
    "_rip_meta.json.tmp",
    "session.state.json",
    "session.state.json.tmp",
})
RIP_METADATA_FILENAMES = frozenset({
    "_rip_meta.json",
    "_rip_meta.json.tmp",
})
STATE_METADATA_FILENAMES = frozenset({
    "session.state.json",
    "session.state.json.tmp",
})


@dataclass(frozen=True)
class Job:
    source: str
    output: str
    profile: str = "default"


@dataclass
class Result:
    success: bool
    outputs: List[str]
    errors: List[Any]


@dataclass
class EngineEvent:
    type: str
    data: Any


class Progress:
    def __init__(self, percent: float = 0.0, eta: str = "", speed: str = ""):
        self.percent = percent
        self.eta = eta
        self.speed = speed


class RipperEngine:
    def find_old_temp_folders(
        self,
        temp_root: str,
        timeout: float = 8.0,
    ) -> list[tuple[str, str, int, int]]:
        """Return UI-ready metadata for leftover temp folders.

        Runs in a daemon thread so a slow network share cannot block the
        caller indefinitely. Each row is `(full_path, name, file_count, size)`.
        """
        import logging

        if not temp_root:
            return []

        old_folders: list[tuple[str, str, int, int]] = []
        abort_event = self.abort_event

        def _scan() -> None:
            if abort_event.is_set():
                return
            if not os.path.isdir(temp_root):
                return
            try:
                names = os.listdir(temp_root)
            except Exception as e:
                logging.warning("Temp folder scan failed to listdir %s: %s", temp_root, e)
                return

            for name in names:
                if abort_event.is_set():
                    return
                full_path = os.path.join(temp_root, name)
                if not os.path.isdir(full_path):
                    continue

                file_count = 0
                total_size = 0
                has_payload_files = False

                try:
                    for dirpath, dirnames, filenames in os.walk(full_path):
                        if abort_event.is_set():
                            return
                        if any(
                            filename not in SESSION_METADATA_FILENAMES
                            for filename in filenames
                        ):
                            has_payload_files = True
                        file_count += len(filenames)
                        for filename in filenames:
                            try:
                                total_size += os.path.getsize(
                                    os.path.join(dirpath, filename)
                                )
                            except OSError:
                                continue
                except Exception as e:
                    logging.warning("Temp folder scan failed in %s: %s", full_path, e)
                    continue

                if has_payload_files:
                    old_folders.append((full_path, name, file_count, total_size))

        thread = threading.Thread(target=_scan, daemon=True)
        thread.start()
        thread.join(timeout=timeout)
        if thread.is_alive():
            logging.warning(
                "Temp folder scan exceeded %.1fs for %s; skipping temp manager rows.",
                timeout,
                temp_root,
            )
            return []
        return old_folders
    def run_job_streaming(self, job: Job) -> Generator[EngineEvent, None, None]:
        result = self.run_job(job)
        for line in result.outputs:
            yield EngineEvent("log", line)
        yield EngineEvent("done", result)

    def run_job(self, job: Job, *, on_log=None, on_progress=None) -> Result:
        outputs: List[str] = []

        # Live log: always buffer into the Result, and also forward to the UI
        # when a logger is supplied (param) or wired by the controller
        # (attribute).  Previously the rip output was buffer-only, so the log
        # pane stayed silent until the whole job finished.
        _live_log = on_log or self._rip_log_cb

        def _logger(msg: str) -> None:
            text = str(msg)
            outputs.append(text)
            if _live_log is not None:
                _live_log(text)

        # Progress: param, else the controller-wired callback, else no-op.
        # Single-arg (percent) — legacy callbacks stay compatible.  Was a
        # hardcoded ``lambda _p: None`` that discarded every update.
        progress_cb = on_progress or self._rip_progress_cb or (lambda _p: None)

        source = str(job.source).strip()
        if source.lower() == "all":
            success = self.rip_all_titles(
                job.output,
                on_progress=progress_cb,
                on_log=_logger,
            )
            errors: List[Any] = []
        else:
            title_ids = [
                safe_int(part)
                for part in source.split(",")
                if str(part).strip()
            ]
            if not title_ids:
                outputs.append("No title IDs supplied for selected-title job.")
                return Result(success=False, outputs=outputs, errors=["no-title-ids"] )
            success, failed_titles = self.rip_selected_titles(
                job.output,
                title_ids,
                on_progress=progress_cb,
                on_log=_logger,
            )
            errors = list(failed_titles)
        return Result(success=bool(success), outputs=outputs, errors=errors)

    def get_disc_target(self) -> str:
        return f"disc:{safe_int(self.cfg.get('opt_drive_index', 0))}"

    def reset_abort(self) -> None:
        with self._abort_lock:
            self.abort_event.clear()

    def _allow_path_tool_resolution(self) -> bool:
        return (
            _sys.platform == "win32"
            and bool(self.cfg.get("opt_allow_path_tool_resolution", False))
        )

    def validate_tools(self) -> tuple[bool, str]:
        """Validate configured MakeMKV and ffprobe paths before starting work."""
        makemkvcon = resolve_makemkvcon(
            _norm_tool_path(self.cfg.get("makemkvcon_path", "")),
            allow_path_lookup=self._allow_path_tool_resolution(),
        )
        ffprobe = resolve_ffprobe(
            _norm_tool_path(self.cfg.get("ffprobe_path", "")),
            allow_path_lookup=self._allow_path_tool_resolution(),
        )
        if not makemkvcon.path:
            msg = makemkvcon.error or "MakeMKV executable not found."
            if makemkvcon.suggestion_path:
                msg += (
                    "\n\nDetected MakeMKV via "
                    f"{makemkvcon.suggestion_source}: {makemkvcon.suggestion_path}"
                    "\nUpdate Settings to use it."
                )
            else:
                msg += "\n\nPlease check Settings."
            return False, msg
        if not ffprobe.path:
            msg = ffprobe.error or "ffprobe executable not found."
            if ffprobe.suggestion_path:
                msg += (
                    "\n\nDetected ffprobe via "
                    f"{ffprobe.suggestion_source}: {ffprobe.suggestion_path}"
                    "\nUpdate Settings to use it."
                )
            else:
                msg += (
                    "\n\nDownload ffmpeg from https://ffmpeg.org and point"
                    "\nSettings -> Paths -> ffprobe folder to its bin directory."
                )
            return False, msg
        self._resolved_makemkvcon = makemkvcon.path
        self._resolved_makemkvcon_src = _norm_tool_path(
            self.cfg.get("makemkvcon_path", "")
        )
        self._makemkvcon_source = makemkvcon.source
        self._resolved_ffprobe = ffprobe.path
        self._resolved_ffprobe_src = _norm_tool_path(
            self.cfg.get("ffprobe_path", "")
        )
        self._ffprobe_source = ffprobe.source
        return True, ""

    def _get_makemkvcon(self) -> str:
        cached = getattr(self, "_resolved_makemkvcon", None)
        current = _norm_tool_path(self.cfg.get("makemkvcon_path", ""))
        if cached and getattr(self, "_resolved_makemkvcon_src", None) == current:
            return cached
        resolved = resolve_makemkvcon(
            current,
            allow_path_lookup=self._allow_path_tool_resolution(),
        )
        self._resolved_makemkvcon = resolved.path
        self._resolved_makemkvcon_src = current
        self._makemkvcon_source = resolved.source
        return resolved.path

    def _get_ffprobe(self) -> str:
        cached = getattr(self, "_resolved_ffprobe", None)
        current = _norm_tool_path(self.cfg.get("ffprobe_path", ""))
        if cached and getattr(self, "_resolved_ffprobe_src", None) == current:
            return cached
        resolved = resolve_ffprobe(
            current,
            allow_path_lookup=self._allow_path_tool_resolution(),
        )
        self._resolved_ffprobe = resolved.path
        self._resolved_ffprobe_src = current
        self._ffprobe_source = resolved.source
        return resolved.path

    def _selected_drive_index(self) -> int:
        return safe_int(self.cfg.get("opt_drive_index", 0))

    def _refresh_drive_probe_state(
        self,
        selected_drive: MakeMKVDrive | None,
        *,
        missing_reason: str = "",
    ) -> None:
        info = dict(getattr(self, "last_drive_info", {}) or {})
        info["drive_index"] = self._selected_drive_index()
        if selected_drive is not None:
            info.update(
                {
                    "drive_name": selected_drive.drive_name,
                    "disc_name": selected_drive.disc_name,
                    "device_path": selected_drive.device_path,
                    "visible": selected_drive.visible,
                    "enabled": selected_drive.enabled,
                    "flags": selected_drive.flags,
                    "usability_state": selected_drive.usability_state,
                }
            )
        elif missing_reason:
            info["usability_state"] = missing_reason
            info.setdefault("visible", 0)
            info.setdefault("enabled", 0)
            info.setdefault("flags", 0)
        self.last_drive_info = info

    def _sleep_with_abort(self, seconds: float) -> bool:
        """Sleep up to ``seconds``, but bail early on abort.

        Ported from MAIN's ripper_engine to fix the previous drive-probe
        backoff using bare ``time.sleep(delay)``, which could block the
        Stop Session button for up to 40 seconds (5 retries × 8s cap)
        during a drive-probe retry storm.  Returns True if the full
        sleep completed without abort, False if abort fired.
        """
        deadline = time.time() + max(0.0, float(seconds))
        while time.time() < deadline:
            if self.abort_event.is_set():
                return False
            time.sleep(min(0.25, max(0.0, deadline - time.time())))
        return not self.abort_event.is_set()

    def _probe_selected_drive(self) -> tuple[MakeMKVDrive | None, list[MakeMKVDrive]]:
        drives = get_available_drives(self._get_makemkvcon(), allow_fallback=False)
        selected_index = self._selected_drive_index()
        selected_drive = next(
            (drive for drive in drives if drive.index == selected_index),
            None,
        )
        self._refresh_drive_probe_state(
            selected_drive,
            missing_reason=(
                "selected drive not detected"
                if selected_drive is None
                else ""
            ),
        )
        return selected_drive, drives

    def _wait_for_drive_ready(self, on_log, *, context: str) -> bool:
        # In-code fallbacks here match the DEFAULTS dict
        # (``opt_drive_probe_retries=5``, ``opt_drive_probe_backoff_seconds=2.0``).
        # The cfg.get fallback is a defensive backstop for the rare
        # case where config.json predates these keys; the actual
        # values live in ``shared/runtime.py``.
        probe_attempts = max(1, int(self.cfg.get("opt_drive_probe_retries", 5) or 5))
        backoff_base = max(
            0.25,
            float(self.cfg.get("opt_drive_probe_backoff_seconds", 2.0) or 2.0),
        )
        selected_index = self._selected_drive_index()

        for probe_num in range(1, probe_attempts + 1):
            if self.abort_event.is_set():
                return False

            selected_drive, drives = self._probe_selected_drive()
            if selected_drive is not None and selected_drive.is_accessible:
                if probe_num > 1:
                    on_log(
                        f"Drive probe recovered for {context} on check "
                        f"{probe_num}/{probe_attempts}: "
                        f"{format_makemkv_drive_label(selected_drive)} "
                        f"({selected_drive.usability_state})"
                    )
                return True

            if selected_drive is None:
                if drives:
                    available = ", ".join(
                        format_makemkv_drive_label(drive) for drive in drives[:3]
                    )
                    detail = (
                        f"selected drive disc:{selected_index} not listed; "
                        f"available: {available}"
                    )
                else:
                    detail = "MakeMKV reported no drives"
            else:
                detail = (
                    f"{format_makemkv_drive_label(selected_drive)} "
                    f"({selected_drive.usability_state})"
                )

            on_log(
                f"Drive probe {probe_num}/{probe_attempts} failed for {context}: "
                f"{detail}"
            )
            if probe_num >= probe_attempts:
                break

            delay_seconds = min(8.0, backoff_base * (2 ** (probe_num - 1)))
            on_log(
                f"Waiting {delay_seconds:.1f}s and refreshing drives before retrying..."
            )
            # Abort-aware sleep: if the user clicks Stop during the
            # backoff, bail out of the retry loop immediately instead
            # of forcing them to wait out the full backoff window.
            if not self._sleep_with_abort(delay_seconds):
                on_log(
                    f"Drive probe wait cancelled by abort ({context})."
                )
                return False

        diag_record(
            "warning",
            "drive_probe_failed",
            "Drive stayed unavailable before rip launch",
            details={
                "context": context,
                "drive_index": selected_index,
                "probe_attempts": probe_attempts,
                "drive_info": dict(getattr(self, "last_drive_info", {}) or {}),
            },
        )
        return False

    def cleanup_partial_files(self, root_path, on_log):
        """Remove stale `.partial` files without touching completed MKVs."""
        if not self.cfg.get("opt_clean_partials_startup", True):
            return
        if not os.path.isdir(root_path):
            return
        for path in glob.glob(os.path.join(root_path, "**", "*.partial"), recursive=True):
            try:
                os.remove(path)
                on_log(f"Cleaned up leftover partial file: {os.path.basename(path)}")
            except FileNotFoundError:
                continue
            except Exception as e:
                on_log(f"Warning: could not remove {os.path.basename(path)}: {e}")

    def unique_path(self, path):
        """Return a collision-free destination path by suffixing ' - N'."""
        if not os.path.exists(path):
            return path
        base, ext = os.path.splitext(path)
        counter = 2
        while os.path.exists(new_path := f"{base} - {counter}{ext}"):
            counter += 1
        return new_path

    def rip_preview_title(self, rip_path, title_id, preview_seconds, on_log):
        """Rip a short disposable preview clip for a single title (delegated)."""
        from engine import rip_ops
        return rip_ops.rip_preview_title(self, rip_path, title_id, preview_seconds, on_log)

    def rip_all_titles(self, rip_path, on_progress, on_log):
        """Rip all disc titles with retry flags and stall-aware process handling (delegated)."""
        from engine import rip_ops
        return rip_ops.rip_all_titles(self, rip_path, on_progress, on_log)

    def rip_selected_titles(self, rip_path, title_ids, on_progress, on_log):
        """Rip selected title IDs with per-title retries and aggregated progress (delegated)."""
        from engine import rip_ops
        return rip_ops.rip_selected_titles(self, rip_path, title_ids, on_progress, on_log)
    def __init__(self, cfg):
        """
        LAYER 1 — Engine

        Pure logic layer. No UI calls, no tkinter, no user interaction.

        Owns everything that touches the disc, the filesystem, and MakeMKV:
          - Disc scanning and title scoring
          - Ripping via makemkvcon subprocess
          - File analysis via ffprobe
          - Atomic file moves and metadata writes
          - Disk space checks and partial file cleanup

        All public methods accept on_log and on_progress callbacks so the
        controller can route output to the UI without this layer knowing
        anything about tkinter.

        Threading model: abort_event is a threading.Event checked throughout
        all long-running operations. Set it to stop everything cleanly.
        """
        self.cfg             = cfg
        self.abort_event     = threading.Event()
        self.current_process = None
        self._abort_lock     = threading.Lock()
        self.last_move_error = ""
        self.last_title_file_map = {}
        self.last_degraded_titles: list = []
        self.last_drive_info: dict = {}
        self.last_process_issue_summary: MakeMKVIssueSummary | None = None
        self.last_scan_issue_summary: MakeMKVIssueSummary | None = None
        self._resolved_makemkvcon = ""
        self._resolved_makemkvcon_src = ""
        self._makemkvcon_source = ""
        self._resolved_ffprobe = ""
        self._resolved_ffprobe_src = ""
        self._ffprobe_source = ""
        # Insertion-order FIFO cache, capped at 1000 entries.  On
        # eviction we pop ``next(iter(self._ffprobe_cache))``, which
        # is the oldest insertion (Python's dict preserves insertion
        # order since 3.7).  Not LRU — a hit does NOT bump the entry
        # to the end.  Adequate because every cached value is keyed
        # by (abspath, mtime_ns, size), so we'd only get cache churn
        # on a working set larger than 1000 unique files per session.
        self._ffprobe_cache = {}
        self._ffprobe_cache_lock = threading.Lock()
        self._ffprobe_cache_max_size = 1000
        self._last_scan_total_bytes = None
        # Per-title expected bytes from the last scan — consulted by
        # rip_ops' degraded-success gate so a truncated rip can't be
        # accepted as a salvage.
        self._last_scan_title_bytes: dict[int, int] = {}
        self._last_scan_timestamp = 0.0
        self._last_scan_target = None
        # Total expected bytes for the rip currently running (sum of the
        # selected titles' sizes).  Set by rip_ops when a rip starts and read
        # by the controller to render the "X.X / Y.Y GB" label; 0 when idle.
        self._rip_total_bytes = 0
        # Set by the controller to route a rip's live progress + log to the
        # UI (size-aware bar + per-title log line).  No-op until wired.
        self._rip_progress_cb = None
        self._rip_log_cb = None
        self.last_disc_info = {}
        self.last_classification: list = []
        # The controller sets this before a scan so the title classifier
        # treats a TV disc as N episodes (no single "main feature").
        self.content_is_tv: bool = False
        self.last_disc_memory: dict[str, Any] | None = None
        self.current_disc_memory: dict[str, Any] | None = None

    @staticmethod
    def _io_path(path):
        """Return a Windows long-path form for file I/O when needed."""
        if _sys.platform != "win32" or not path:
            return path
        p = os.path.abspath(str(path))
        if p.startswith("\\\\?\\"):
            return p
        if p.startswith("\\\\"):
            return "\\\\?\\UNC\\" + p.lstrip("\\")
        return "\\\\?\\" + p

    @property
    def abort_flag(self):
        return self.abort_event.is_set()

    def abort(self):
        """Set abort flag and terminate active MakeMKV process if running."""
        with self._abort_lock:
            if self.abort_event.is_set():
                return
            self.abort_event.set()
            proc = self.current_process  # read inside lock — avoids race with rip thread

        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        try:
                            proc.kill()
                        except Exception:
                            pass
            except Exception:
                pass

    def find_resumable_sessions(self, temp_root, timeout=8.0):
        """Find temp sessions with saved workflow metadata that can be resumed.

        Runs in a daemon thread so a slow or unreachable network share cannot
        block the caller indefinitely. Returns [] if the scan takes longer
        than *timeout* seconds. Now supports cancellation and logs exceptions.
        """
        import logging
        resumable = []
        abort_event = self.abort_event

        def _scan():
            if abort_event.is_set():
                logging.warning("Session scan aborted before start.")
                return
            if not os.path.isdir(temp_root):
                return
            try:
                names = os.listdir(temp_root)
            except Exception as e:
                logging.warning(f"Session scan failed to listdir: {e}")
                return
            for name in names:
                if abort_event.is_set():
                    logging.warning("Session scan cancelled during folder scan.")
                    return
                full = os.path.join(temp_root, name)
                if not os.path.isdir(full):
                    continue
                try:
                    meta = self.read_temp_metadata(full)
                except Exception as e:
                    logging.warning(f"Failed to read metadata for {full}: {e}")
                    continue
                # Skip terminal phases: complete/organized are done,
                # aborted sessions are intentionally not resumable
                # (user explicitly chose Stop Session — see
                # docs/workflow-stabilization-criteria.md "Abort
                # propagation").
                if meta and meta.get("phase") not in {
                    "complete", "organized", "aborted",
                }:
                    mkv_count = 0
                    try:
                        for dp, dn, fns in os.walk(full):
                            if abort_event.is_set():
                                logging.warning(f"Session scan cancelled during os.walk in {full}.")
                                return
                            mkv_count += sum(
                                1 for f in fns if f.endswith(".mkv")
                            )
                    except Exception as e:
                        logging.warning(f"Failed os.walk in {full}: {e}")
                    resumable.append((full, name, meta, mkv_count))

        t = threading.Thread(target=_scan, daemon=True)
        t.start()
        t.join(timeout=timeout)
        if t.is_alive():
            logging.warning("Session scan thread exceeded timeout and may still be running.")
        return resumable

    def _atomic_write_json(self, path, data):
        """Write JSON atomically using temp file + os.replace."""
        tmp = path + ".tmp"
        io_tmp = self._io_path(tmp)
        io_path = self._io_path(path)
        try:
            with open(io_tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(io_tmp, io_path)
        except Exception as e:
            import logging
            logging.warning("Failed atomic JSON write for %s: %s", path, e)
            if os.path.exists(io_tmp):
                try:
                    os.remove(io_tmp)
                except Exception as cleanup_err:
                    logging.warning(
                        "Failed to remove temporary JSON file %s: %s",
                        io_tmp, cleanup_err,
                    )

    def write_temp_metadata(self, rip_path, title, disc_number,
                            season=None, year=None, edition="",
                            media_type=None,
                            selected_titles=None, episode_names=None,
                            episode_numbers=None, dest_folder=None,
                            completed_titles=None, phase="ripping"):
        """Create initial metadata file for a rip temp folder."""
        meta = {
            "title":            title,
            "year":             year,
            "edition":          edition,
            "media_type":       media_type,
            "season":           season,
            "selected_titles":  list(selected_titles or []),
            "episode_names":    list(episode_names or []),
            "episode_numbers":  list(episode_numbers or []),
            "completed_titles": list(completed_titles or []),
            "phase":            phase,
            "dest_folder":      dest_folder,
            "disc_number":      disc_number,
            "timestamp":        datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "file_count":       0,
            "status":           "ripping",
        }
        self._atomic_write_json(
            os.path.join(rip_path, "_rip_meta.json"), meta
        )

    def update_temp_metadata(self, rip_path, status=None, **updates):
        """Refresh metadata counters/status for a temp session folder."""
        meta_path = os.path.join(rip_path, "_rip_meta.json")
        io_meta_path = self._io_path(meta_path)
        try:
            with open(io_meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            # Count MKV files: single pass instead of glob
            file_count = 0
            try:
                for dp, dn, fns in os.walk(rip_path):
                    file_count += sum(
                        1 for f in fns if f.endswith(".mkv")
                    )
            except Exception:
                pass
            meta["file_count"] = file_count
            if status:
                meta["status"] = status
            for key, value in updates.items():
                meta[key] = value
            self._atomic_write_json(meta_path, meta)
        except Exception as e:
            # Route through stdlib logging like the rest of this module
            # (line 120 et al.) rather than ``print``.  Plain ``print``
            # bypasses the controller's log capture and interleaves
            # with the progress display on stdout.
            import logging
            logging.warning(
                "failed to update session metadata %s: %s", meta_path, e,
            )

    def read_temp_metadata(self, rip_path):
        """Read metadata for a temp session folder, returning None on failure."""
        meta_path = os.path.join(rip_path, "_rip_meta.json")
        try:
            with open(self._io_path(meta_path), encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _disc_memory_path(self) -> str:
        from shared.runtime import get_config_dir

        return os.path.join(get_config_dir(), "last_disc_memory.json")

    def build_disc_memory_record(
        self,
        titles: Sequence[Mapping[str, Any]],
        *,
        disc_info: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        return build_disc_memory_record(
            titles,
            disc_info=disc_info or self.last_disc_info,
        )

    def read_last_disc_memory(self) -> dict[str, Any] | None:
        path = self._disc_memory_path()
        try:
            with open(self._io_path(path), encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return None

        if not isinstance(data, dict):
            return None
        self.last_disc_memory = dict(data)
        return dict(data)

    def write_last_disc_memory(self, record: Mapping[str, Any]) -> None:
        payload = dict(record)
        if not payload.get("identity_hash") or not payload.get("structure_hash"):
            return
        self._atomic_write_json(self._disc_memory_path(), payload)
        self.last_disc_memory = payload

    def remember_last_disc_scan(
        self,
        titles: Sequence[Mapping[str, Any]],
        *,
        disc_info: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        record = self.build_disc_memory_record(
            titles,
            disc_info=disc_info,
        )
        if not record:
            return None
        self.current_disc_memory = dict(record)
        return record

    def commit_current_disc_memory(
        self,
        *,
        preserve_session_info: bool = False,
    ) -> dict[str, Any] | None:
        current = dict(self.current_disc_memory or {})
        if not current:
            return None

        if preserve_session_info:
            previous = self.read_last_disc_memory()
            match = compare_disc_memory_records(previous, current)
            if match and isinstance(previous, Mapping):
                previous_session = previous.get("session_info")
                if isinstance(previous_session, Mapping):
                    current["session_info"] = dict(previous_session)

        self.write_last_disc_memory(current)
        return current

    def update_last_disc_session_state(
        self,
        session_info: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        base = dict(
            self.last_disc_memory
            or self.read_last_disc_memory()
            or self.current_disc_memory
            or {}
        )
        if not base:
            return None

        existing = base.get("session_info")
        merged_session: dict[str, Any] = (
            dict(existing) if isinstance(existing, Mapping) else {}
        )
        for key, value in session_info.items():
            merged_session[str(key)] = value

        base["session_info"] = merged_session
        self.write_last_disc_memory(base)
        return base

    def match_last_disc_memory(
        self,
        titles: Sequence[Mapping[str, Any]],
        *,
        disc_info: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        current = self.current_disc_memory or self.build_disc_memory_record(
            titles,
            disc_info=disc_info,
        )
        previous = self.read_last_disc_memory()
        match = compare_disc_memory_records(previous, current)
        if not match or not previous or not current:
            return None
        return {
            "match_type": match["match_type"],
            "saved": previous,
            "current": current,
        }

    def delete_temp_metadata(self, rip_path, on_log=None):
        """Delete session JSON sidecars from a temp folder after success."""
        removed = 0
        has_rip_metadata = any(
            os.path.exists(self._io_path(os.path.join(rip_path, filename)))
            for filename in RIP_METADATA_FILENAMES
        )
        filenames = set(RIP_METADATA_FILENAMES)
        if has_rip_metadata:
            filenames.update(STATE_METADATA_FILENAMES)
        for filename in filenames:
            path = os.path.join(rip_path, filename)
            io_path = self._io_path(path)
            if not os.path.exists(io_path):
                continue
            try:
                os.remove(io_path)
                removed += 1
            except Exception as e:
                if on_log:
                    on_log(
                        "Warning: could not delete session metadata "
                        f"{os.path.basename(path)}: {e}"
                    )
        if removed and on_log:
            on_log(f"Removed {removed} session metadata file(s).")
        return removed

    def scan_disc(self, on_log, on_progress):
        """
        Scan disc and return list of title dicts sorted by score.

        Parses both TINFO (title info) and SINFO (stream info) from
        makemkvcon output. Stores duration_seconds at parse time so
        score_title() has numeric data to work with.

        Returns list sorted best-first, or None on abort/error.
        """
        makemkvcon  = self._get_makemkvcon()
        disc_target = self.get_disc_target()
        global_args = parse_cli_args(
            self.cfg.get("opt_makemkv_global_args", ""),
            on_log,
            "MakeMKV global args"
        )
        info_args = parse_cli_args(
            self.cfg.get("opt_makemkv_info_args", ""),
            on_log,
            "MakeMKV info args"
        )
        minlength = int(self.cfg.get("opt_minlength_seconds", 0) or 0)
        minlength_args = [f"--minlength={minlength}"] if minlength > 0 else []

        on_log(f"Scanning disc ({disc_target})...")
        disc_info   = {}
        titles      = {}
        title_count = 0
        self.last_drive_info = {}
        self.last_scan_issue_summary = None
        _msg_lines: list[str] = []
        _drive_rows = []
        _scan_cmd = (
            [makemkvcon] + global_args +
            ["-r", "info", disc_target] + minlength_args + info_args
        )
        _diag_capture = ProcessCapture(
            command=_scan_cmd,
            start_time=datetime.now().isoformat(),
            working_directory=os.getcwd(),
        )
        _diag_raw_lines: list[str] = []
        _scan_start = time.time()
        proc = None
        try:
            proc = subprocess.Popen(
                _scan_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                # makemkvcon emits UTF-8; without this, text=True decodes
                # with the Windows ANSI code page — non-ASCII disc titles
                # mojibake and some byte sequences raise mid-stream,
                # killing the reader thread.
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **_POPEN_FLAGS
            )
            self.current_process = proc
            # Abort-responsive reader.  A dedicated daemon thread feeds
            # stdout lines into a queue; the main loop polls that queue
            # with a short timeout and checks abort_event every cycle.
            # This mirrors _run_rip_process and fixes the bug where the
            # old blocking ``iter(proc.stdout.readline, "")`` trapped
            # the abort check whenever makemkvcon went quiet during a
            # scan (slow disc, drive still spinning up) — Stop Session
            # appeared dead because the loop was frozen inside
            # readline() and never reached the abort check.
            line_queue = queue_module.Queue()
            reader = threading.Thread(
                target=self._stdout_reader,
                args=(proc.stdout, line_queue),
                daemon=True,
            )
            reader.start()
            stall_detection = self.cfg.get("opt_stall_detection", True)
            stall_timeout = int(self.cfg.get("opt_stall_timeout_seconds", 120))
            last_output = time.time()
            stall_warned = False
            while True:
                if self.abort_event.is_set():
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    on_log("Scan aborted.")
                    try:
                        proc.wait(timeout=5)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    return None
                try:
                    line = line_queue.get(timeout=0.5)
                except queue_module.Empty:
                    # Break only when the process has exited AND the
                    # queue is drained, so we don't drop trailing
                    # TINFO/SINFO lines that arrived after the process
                    # closed its pipe.
                    if proc.poll() is not None and line_queue.empty():
                        break
                    if (stall_detection and not stall_warned and
                            time.time() - last_output > stall_timeout):
                        on_log(
                            f"No output from disc scan for {stall_timeout}s; "
                            "still waiting..."
                        )
                        stall_warned = True
                    continue
                last_output = time.time()
                stall_warned = False
                line = line.strip()
                if not line:
                    continue
                if len(_diag_raw_lines) < _DIAG_RAW_LINES_MAX:
                    _diag_raw_lines.append(line)
                if line.startswith("CINFO:"):
                    parts = line[6:].split(",", 2)
                    if len(parts) < 3:
                        continue
                    try:
                        attr = int(parts[0])
                        val  = parts[2].strip().strip('"')
                    except (ValueError, IndexError):
                        continue
                    if attr == 2:
                        disc_info["title"] = val
                    elif attr == 28:
                        disc_info["lang_code"] = val
                    elif attr == 29:
                        disc_info["lang_name"] = val
                    elif attr == 32:
                        disc_info["volume_id"] = val
                elif line.startswith("DRV:"):
                    drive_row = parse_makemkv_drive_row(line)
                    if drive_row is not None:
                        _drive_rows.append(drive_row)
                elif line.startswith("TINFO:"):
                    parts = line[6:].split(",", 3)
                    if len(parts) < 4:
                        continue
                    try:
                        tid  = int(parts[0])
                        attr = int(parts[1])
                        val  = parts[3].strip().strip('"')
                    except (ValueError, IndexError):
                        continue
                    if tid not in titles:
                        titles[tid] = {
                            "id":               tid,
                            "name":             f"Title {tid+1}",
                            "duration":         "",
                            "duration_seconds": 0,
                            "size":             "",
                            "size_bytes":       0,
                            "chapters":         0,
                            "_invalid":         False,
                            "streams":          {},
                        }
                        title_count += 1
                        on_progress(
                            min(5 + title_count, 90)
                        )
                    if attr == 2:
                        titles[tid]["name"] = val
                    elif attr == 9:
                        titles[tid]["duration"] = val
                        dur_seconds = parse_duration_to_seconds(val)
                        titles[tid]["duration_seconds"] = dur_seconds
                        if val and dur_seconds <= 0:
                            titles[tid]["_invalid"] = True
                    elif attr == 8:
                        titles[tid]["chapters"] = safe_int(val)
                    elif attr == 11:
                        size_bytes = parse_size_to_bytes(val)
                        titles[tid]["size_bytes"] = size_bytes
                        if val and size_bytes <= 0:
                            titles[tid]["_invalid"] = True
                        if size_bytes > 0:
                            gb = size_bytes / (1024**3)
                            titles[tid]["size"] = f"{gb:.2f} GB"
                        else:
                            titles[tid]["size"] = val
                elif line.startswith("MSG:"):
                    parts = line[4:].split(",", 4)
                    if len(parts) >= 5:
                        msg = parts[4].strip().strip('"')
                        if msg:
                            _msg_lines.append(msg)
                elif line.startswith("SINFO:"):
                    parts = line[6:].split(",", 4)
                    if len(parts) < 5:
                        continue
                    try:
                        tid  = int(parts[0])
                        sid  = int(parts[1])
                        attr = int(parts[2])
                        val  = parts[4].strip().strip('"')
                    except (ValueError, IndexError):
                        continue
                    if tid not in titles:
                        continue
                    streams = titles[tid]["streams"]
                    if sid not in streams:
                        streams[sid] = {}
                    stream = streams[sid]
                    if attr == 1:
                        stream["type"] = val
                    elif attr == 2:
                        stream["codec"] = val
                    elif attr == 3:
                        stream["lang"] = val
                    elif attr == 4:
                        stream["channels"] = val
                    elif attr == 21:
                        stream["lang_name"] = val
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        except Exception as e:
            _diag_capture.end_time = datetime.now().isoformat()
            _diag_capture.duration_seconds = time.time() - _scan_start
            _diag_capture.stdout = "\n".join(_diag_raw_lines[-200:])
            on_log(f"Error scanning disc: {e}")
            diag_exception(e, context="scan_disc", category="scan_anomaly")
            return None
        finally:
            if proc is not None and self.current_process is proc:
                self.current_process = None

        scan_rc = 1
        if proc is not None and proc.returncode is not None:
            scan_rc = int(proc.returncode)
        _diag_capture.exit_code = scan_rc
        _diag_capture.end_time = datetime.now().isoformat()
        _diag_capture.duration_seconds = time.time() - _scan_start
        _diag_capture.stdout = "\n".join(_diag_raw_lines[-200:])

        scan_summary = analyze_makemkv_messages(_msg_lines)
        self.last_scan_issue_summary = scan_summary
        for summary_line in scan_summary.build_summary_lines(
            phase="scan",
            exit_code=scan_rc,
        ):
            on_log(summary_line)
        if scan_summary.has_disc_read_errors:
            diag_record(
                "warning" if scan_rc == 0 else "error",
                "disc_read_error",
                "MakeMKV logged disc-read errors during scan",
                details={
                    "phase": "scan",
                    "exit_code": scan_rc,
                    **scan_summary.to_dict(),
                },
                process_capture=_diag_capture,
            )
        if scan_rc != 0:
            on_log(f"MakeMKV scan failed (exit code {scan_rc}).")
            diag_process(_diag_capture, success=False, category="scan_anomaly")
            return None

        result = []
        for tid in sorted(titles.keys()):
            t = titles[tid]
            if not isinstance(t.get("size_bytes"), (int, float)):
                t["size_bytes"] = 0
                t["_invalid"] = True
            if not isinstance(t.get("duration_seconds"), (int, float)):
                t["duration_seconds"] = 0
                t["_invalid"] = True
            t["chapters"] = safe_int(t.get("chapters", 0))
            audio_tracks    = []
            subtitle_tracks = []
            for sid in sorted(t["streams"].keys()):
                s     = t["streams"][sid]
                stype = s.get("type", "")
                if stype == "Audio":
                    audio_tracks.append({
                        "id":        sid,
                        "codec":     s.get("codec", ""),
                        "lang":      s.get("lang", ""),
                        "lang_name": s.get("lang_name", ""),
                        "channels":  s.get("channels", ""),
                    })
                elif stype == "Subtitles":
                    subtitle_tracks.append({
                        "id":        sid,
                        "lang":      s.get("lang", ""),
                        "lang_name": s.get("lang_name", ""),
                    })
            t["audio_tracks"]    = audio_tracks
            t["subtitle_tracks"] = subtitle_tracks
            del t["streams"]
            result.append(t)

        # Sort by score descending — best candidate first.
        # Keep scores out of title dicts to avoid mutating shared objects.
        scored = [(t, score_title(t, result)) for t in result]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        result = [t for t, _score in scored]

        # Log scores for debugging edge cases and bad discs
        # On large discs: show top 20 + always include selected title.
        log_all = len(scored) <= 50
        on_log(
            "Title scores:"
            if log_all else
            f"Title scores (top 20 of {len(scored)} shown, + selected):"
        )
        # Collect titles to log: all or top 20 + best if not in top 20
        titles_to_log = scored if log_all else scored[:20]
        if not log_all and scored and scored[0] not in titles_to_log:
            titles_to_log = titles_to_log + [scored[0]]

        for t, score in titles_to_log:
            raw_name = str(t.get("name", "") or "").strip()
            projected_name = clean_name(raw_name) if raw_name else ""
            if not projected_name:
                projected_name = f"Title_{safe_int(t.get('id', 0)) + 1:02d}"
            # Show MakeMKV's real output filename (e.g. "B1_t10.mkv")
            # alongside our "Title N" so the scan list lines up with
            # the ripped files.
            disc_name = str(t.get("output_name", "") or "").strip()
            disc_part = f" disc='{disc_name}'" if disc_name else ""
            on_log(
                f"  Title {t['id']+1}: score={score:.3f} | "
                f"{t['duration']} {t['size']} | "
                f"chap={safe_int(t.get('chapters', 0))} "
                f"aud={len(t.get('audio_tracks', []))} "
                f"sub={len(t.get('subtitle_tracks', []))} | "
                f"projected='{projected_name}'{disc_part}"
            )
        if scored:
            best_title = scored[0][0]
            best_raw_name = str(best_title.get("name", "") or "").strip()
            best_projected_name = clean_name(best_raw_name) if best_raw_name else ""
            if not best_projected_name:
                best_projected_name = f"Title_{safe_int(best_title.get('id', 0)) + 1:02d}"
            on_log(
                f"BEST: Title {scored[0][0]['id']+1} "
                f"(score={scored[0][1]:.3f}) "
                f"projected='{best_projected_name}'"
            )

            if len(scored) > 1:
                diff = scored[0][1] - scored[1][1]
                if diff < _AMBIGUOUS_TOP_SCORE_DELTA:
                    on_log(
                        "WARNING: Top titles are very close — "
                        "possible ambiguity."
                    )

        # Classify titles into MAIN / DUPLICATE / EXTRA / UNKNOWN
        # (EPISODE / EXTRA on a TV disc — the controller sets the flag).
        self.last_classification = classify_titles(
            result, is_tv=getattr(self, "content_is_tv", False)
        )
        if self.last_classification:
            classification_log_cap = 20
            if len(self.last_classification) > classification_log_cap:
                on_log(
                    "Classification results "
                    f"(top {classification_log_cap} of "
                    f"{len(self.last_classification)} shown):"
                )
                classification_rows = self.last_classification[:classification_log_cap]
            else:
                classification_rows = self.last_classification
            for log_line in format_classification_log(classification_rows):
                on_log(log_line)

        try:
            from engine.scan_ops import _parse_drive_info

            self.last_drive_info = _parse_drive_info(
                _msg_lines,
                drive_rows=_drive_rows,
                selected_drive_index=safe_int(self.cfg.get("opt_drive_index", 0)),
            )
        except Exception:
            self.last_drive_info = {}

        self._last_scan_total_bytes = sum(
            max(0, int(t.get("size_bytes", 0) or 0)) for t in result
        )
        self._last_scan_title_bytes = {
            int(t.get("id", -1)): max(0, int(t.get("size_bytes", 0) or 0))
            for t in result
        }
        self._last_scan_timestamp = time.time()
        self._last_scan_target = disc_target
        disc_info["title_count"] = len(result)
        disc_info["size_signature"] = ",".join(
            str(max(0, int(t.get("size_bytes", 0) or 0)))
            for t in sorted(result, key=lambda t: int(t.get("id", -1)))
        )
        self.last_disc_info = disc_info
        self.remember_last_disc_scan(
            result,
            disc_info=disc_info,
        )

        if disc_info.get("title"):
            on_log(f"Disc title (CINFO): {disc_info['title']}")
        if disc_info.get("lang_name"):
            on_log(f"Disc language: {disc_info['lang_name']}")
        if minlength > 0:
            on_log(f"--minlength={minlength}s applied (titles shorter than {minlength}s excluded)")

        on_progress(100)
        on_log(f"Disc scan complete. Found {len(result)} title(s).")
        return result

    def get_disc_size(self, on_log, prefer_cached=False, timeout_seconds=None):
        """
        Lightweight disc size query used only by dump/multi-disc modes.
        TV/Movie disc flows use size_bytes from scan_disc() instead,
        avoiding a second full pass over the disc.
        """
        makemkvcon  = self._get_makemkvcon()
        disc_target = self.get_disc_target()
        if prefer_cached:
            scan_age = time.time() - float(self._last_scan_timestamp or 0.0)
            if (
                self._last_scan_target == disc_target
                and self._last_scan_total_bytes
                and scan_age < _SCAN_CACHE_TTL_SECONDS
            ):
                on_log("Using cached disc size from recent scan.")
                return int(self._last_scan_total_bytes)
        global_args = parse_cli_args(
            self.cfg.get("opt_makemkv_global_args", ""),
            on_log,
            "MakeMKV global args"
        )
        info_args = parse_cli_args(
            self.cfg.get("opt_makemkv_info_args", ""),
            on_log,
            "MakeMKV info args"
        )
        disc_info = {}
        total_bytes = 0
        seen_any_tinfo = False   # True once any TINFO: line is received
        title_ids = set()
        title_sizes = {}
        proc = None
        reader = None
        line_queue = None
        try:
            proc = subprocess.Popen(
                [makemkvcon] + global_args +
                ["-r", "info", disc_target] + info_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                # makemkvcon emits UTF-8; without this, text=True decodes
                # with the Windows ANSI code page — non-ASCII disc titles
                # mojibake and some byte sequences raise mid-stream,
                # killing the reader thread.
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **_POPEN_FLAGS
            )
            self.current_process = proc

            line_queue = queue_module.Queue()
            reader = threading.Thread(
                target=self._stdout_reader,
                args=(proc.stdout, line_queue),
                daemon=True,
            )
            reader.start()

            scan_start = time.time()
            last_output = scan_start
            stall_warned = False
            stall_timeout = max(
                10, int(self.cfg.get("opt_stall_timeout_seconds", 120))
            )
            if timeout_seconds is None:
                info_timeout = max(
                    30, int(self.cfg.get("opt_disc_info_timeout_seconds", 180))
                )
            else:
                info_timeout = max(5, int(timeout_seconds))

            while True:
                if self.abort_event.is_set():
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    on_log("Disc-size scan aborted.")
                    try:
                        proc.wait(timeout=5)
                    except Exception:
                        pass
                    return None

                try:
                    line = line_queue.get(timeout=0.5)
                except queue_module.Empty:
                    now = time.time()
                    if proc.poll() is not None:
                        break

                    if now - scan_start > info_timeout:
                        on_log(
                            "Warning: disc-size scan timed out; "
                            "continuing without pre-rip size check."
                        )
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                        try:
                            proc.wait(timeout=5)
                        except Exception:
                            try:
                                proc.kill()
                            except Exception:
                                pass
                        return None

                    if (not stall_warned and
                            now - last_output > stall_timeout):
                        on_log(
                            f"No output from disc-size scan for {stall_timeout}s; "
                            "still waiting..."
                        )
                        stall_warned = True
                    continue

                last_output = time.time()
                stall_warned = False
                if line.startswith("CINFO:"):
                    parts = line[6:].split(",", 2)
                    if len(parts) < 3:
                        continue
                    try:
                        attr = int(parts[0])
                        val = parts[2].strip().strip('"')
                    except (ValueError, IndexError):
                        continue
                    if attr == 2:
                        disc_info["title"] = val
                    elif attr == 28:
                        disc_info["lang_code"] = val
                    elif attr == 29:
                        disc_info["lang_name"] = val
                    elif attr == 32:
                        disc_info["volume_id"] = val
                elif line.startswith("TINFO:"):
                    seen_any_tinfo = True
                    parts = line[6:].split(",", 3)
                    # Reset tid every iteration so a TINFO line with
                    # too few parts can never accidentally inherit the
                    # previous iteration's tid.  Today the size block
                    # is gated by the same ``len(parts) >= 4`` check
                    # that updates tid, so no incorrect ``title_sizes``
                    # write can occur — but a future edit to either
                    # gate could break that invariant.  Defensive
                    # reset costs nothing.
                    tid = None
                    if len(parts) >= 4:
                        try:
                            tid = int(parts[0])
                            title_ids.add(tid)
                        except (TypeError, ValueError):
                            tid = None
                    if len(parts) >= 4 and parts[1] == "11":
                        try:
                            size_str = parts[3].strip().strip('"')
                            size_bytes = parse_size_to_bytes(size_str)
                            total_bytes += size_bytes
                            if tid is not None:
                                title_sizes[tid] = size_bytes
                        except IndexError:
                            pass

            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        except Exception as e:
            on_log(f"Warning: could not read disc size: {e}")
            return None
        finally:
            self.current_process = None
            if reader is not None:
                try:
                    reader.join(timeout=1)
                except Exception:
                    pass
        proc_returncode = getattr(proc, "returncode", None) if proc is not None else None
        if proc_returncode not in (0, None):
            on_log(
                f"Warning: MakeMKV disc info exited with code {proc_returncode}; "
                "ignoring partial size data."
            )
            return None
        if title_ids:
            disc_info["title_count"] = len(title_ids)
        if title_sizes:
            disc_info["size_signature"] = ",".join(
                str(max(0, int(size or 0)))
                for _tid, size in sorted(title_sizes.items())
            )
        if disc_info:
            self.last_disc_info = disc_info
        if total_bytes > 0:
            self._last_scan_total_bytes = int(total_bytes)
            self._last_scan_timestamp = time.time()
            self._last_scan_target = disc_target
            return total_bytes
        # Disc is present (TINFO lines seen) but sizes are all zero — return 0
        # so callers that distinguish None ("no disc") from 0 ("disc, no size")
        # still get a truthy-enough signal.  Returning None here would cause
        # _disc_present() to permanently report False for such discs.
        if seen_any_tinfo:
            return 0
        return None

    def check_disk_space(self, path, required_bytes, on_log, timeout=8.0):
        hard_floor = int(
            self.cfg.get("opt_hard_block_gb", 20)
        ) * (1024**3)
        try:
            if not os.path.exists(path):
                on_log(f"Warning: disk-space path does not exist: {path}")
                return "ok", 0, required_bytes
            # Run in a daemon thread — shutil.disk_usage() can hang indefinitely
            # on an offline or slow network share.
            _free = [None]
            _err  = [None]

            def _query():
                try:
                    _free[0] = shutil.disk_usage(path).free
                except Exception as e:
                    _err[0] = e

            t = threading.Thread(target=_query, daemon=True)
            t.start()
            t.join(timeout=timeout)

            if _free[0] is None:
                msg = str(_err[0]) if _err[0] else "timeout"
                on_log(f"Warning: could not check disk space: {msg}")
                return "ok", 0, required_bytes

            free = _free[0]
            on_log(
                f"Disk space — "
                f"Required: {required_bytes / (1024**3):.1f} GB  "
                f"Free: {free / (1024**3):.1f} GB"
            )
            if free < hard_floor:
                return "block", free, required_bytes
            if free < required_bytes:
                return "warn", free, required_bytes
            return "ok", free, required_bytes
        except Exception as e:
                import logging
                logging.warning("check_disk_space failed: %s", e)
                return "ok", 0, required_bytes

    def _snapshot_mkv_files(self, rip_path):
        return set(
            glob.glob(
                os.path.join(rip_path, "**", "*.mkv"),
                recursive=True
            )
        )

    def _log_rip_dir_contents(self, rip_path, before_set, on_log):
        """Log a compact recursive directory listing after a suspicious rip result."""
        try:
            if not os.path.isdir(rip_path):
                on_log(f"Rip target directory is missing: {rip_path}")
                return

            entries: list[tuple[str, str]] = []
            before = {os.path.normcase(os.path.abspath(path)) for path in before_set}

            for dirpath, dirnames, filenames in os.walk(rip_path):
                dirnames.sort()
                filenames.sort()
                rel_dir = os.path.relpath(dirpath, rip_path)
                for dirname in dirnames:
                    rel_path = dirname if rel_dir == "." else os.path.join(rel_dir, dirname)
                    entries.append(("dir", rel_path))
                for filename in filenames:
                    full_path = os.path.join(dirpath, filename)
                    rel_path = filename if rel_dir == "." else os.path.join(rel_dir, filename)
                    marker = ""
                    if os.path.normcase(os.path.abspath(full_path)) in before:
                        marker = " (pre-existing)"
                    try:
                        size_bytes = os.path.getsize(full_path)
                        size_note = f" ({size_bytes / (1024**2):.1f} MB)"
                    except OSError:
                        size_note = ""
                    entries.append(("file", rel_path + size_note + marker))

            if not entries:
                on_log(f"Rip target directory is empty after MakeMKV run: {rip_path}")
                return

            max_entries = 20
            on_log(
                "Rip target directory contents after MakeMKV run "
                f"({len(entries)} item(s)):"
            )
            for kind, rel_path in entries[:max_entries]:
                prefix = "[DIR]" if kind == "dir" else "[FILE]"
                on_log(f"  {prefix} {rel_path}")
            if len(entries) > max_entries:
                on_log(f"  ... {len(entries) - max_entries} more item(s) not shown")
        except Exception as exc:
            on_log(f"Warning: could not inspect rip target directory: {exc}")

    def _purge_rip_target_files(self, rip_path, on_log):
        """Ensure each rip starts fresh; never resume prior file fragments."""
        removed = 0
        for pattern in ("**/*.mkv", "**/*.partial"):
            for f in glob.glob(
                os.path.join(rip_path, pattern), recursive=True
            ):
                try:
                    os.remove(f)
                    removed += 1
                except Exception:
                    pass
        if removed:
            on_log(
                f"Removed {removed} pre-existing file(s) from rip target "
                "to avoid file-level resume."
            )

    def wipe_session_outputs(self, rip_path, on_log):
        """Delete ripped outputs for a session while preserving metadata."""
        removed = 0
        for pattern in ("**/*.mkv", "**/*.partial"):
            for f in glob.glob(
                os.path.join(rip_path, pattern), recursive=True
            ):
                try:
                    os.remove(f)
                    removed += 1
                except Exception:
                    pass
        on_log(
            f"Cleared {removed} session output file(s); preserved metadata "
            "for workflow resume."
        )

    def _clean_new_mkv_files(self, rip_path, before_set, on_log):
        """
        Delete only MKV files created since before_set snapshot.
        Preserves successfully ripped titles from prior attempts in
        a multi-title session.
        """
        if not self.cfg.get("opt_clean_mkv_before_retry", True):
            return
        after_set = self._snapshot_mkv_files(rip_path)
        new_files = after_set - before_set
        for f in new_files:
            try:
                os.remove(f)
                on_log(
                    f"Removed partial MKV before retry: "
                    f"{os.path.basename(f)}"
                )
            except Exception:
                pass
        # Also remove transient partial files from failed attempts.
        for f in glob.glob(
            os.path.join(rip_path, "**", "*.partial"),
            recursive=True
        ):
            try:
                os.remove(f)
                on_log(
                    f"Removed transient partial before retry: "
                    f"{os.path.basename(f)}"
                )
            except Exception:
                pass

    def _log_forced_failure_with_outputs(self, rip_path, before_set, on_log):
        """Log when a failed MakeMKV run still created output files."""
        after_set = self._snapshot_mkv_files(rip_path)
        new_files = after_set - before_set
        if not new_files:
            return
        on_log(
            "MakeMKV failed with non-zero exit code; forcing failure "
            f"regardless of output ({len(new_files)} new file(s))."
        )

    def _stdout_reader(self, pipe, q):
        """Feed subprocess stdout into a queue. Runs in a daemon thread."""
        try:
            for line in iter(pipe.readline, ""):
                q.put(line)
        except Exception as e:
            import logging
            logging.warning("_read_pipe_lines failed: %s", e)
        finally:
            try:
                pipe.close()
            except Exception as e:
                import logging
                logging.warning("pipe.close failed: %s", e)

    def _run_rip_process(self, cmd, on_progress, on_log):
        """
        Run a single MakeMKV rip subprocess with reliable cross-platform
        stdout handling.

        Uses a dedicated reader thread that feeds lines into a queue, then
        reads from that queue with a 1-second timeout. This approach:
          - Avoids blocking readline() on Windows where select() doesn't
            work on pipes
          - Keeps abort checks responsive regardless of MakeMKV output rate

        Returns True on rc==0, False otherwise.
        """
        _diag_capture = ProcessCapture(
            command=list(cmd),
            start_time=datetime.now().isoformat(),
            working_directory=os.getcwd(),
        )
        _diag_raw_lines: list[str] = []
        self.last_process_issue_summary = None
        issue_summary = MakeMKVIssueSummary()
        message_coalescer = MakeMKVMessageCoalescer()

        def _emit_makemkv_message(message: str) -> None:
            issue_summary.record(message)
            for emitted in message_coalescer.feed(message):
                on_log(emitted)

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            # makemkvcon emits UTF-8 (see note at the scan Popen).
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **_POPEN_FLAGS
        )
        self.current_process = proc

        line_queue    = queue_module.Queue()
        reader        = threading.Thread(
            target=self._stdout_reader,
            args=(proc.stdout, line_queue),
            daemon=True
        )
        reader.start()

        last_pct        = -1
        last_output     = time.time()
        rip_start       = time.time()
        stall_detection = self.cfg.get("opt_stall_detection", True)
        stall_timeout   = int(
            self.cfg.get("opt_stall_timeout_seconds", 120)
        )
        stall_warned = False

        while True:
            if self.abort_event.is_set():
                try:
                    proc.terminate()
                except Exception:
                    pass
                on_log("Rip aborted.")
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
                self.current_process = None
                return False

            try:
                line = line_queue.get(timeout=1.0)
            except queue_module.Empty:
                if proc.poll() is not None:
                    break
                if stall_detection:
                    if (not stall_warned and
                            time.time() - last_output > stall_timeout):
                        on_log(
                            f"No output for {stall_timeout}s — "
                            "process may be reading difficult sectors; waiting."
                        )
                        stall_warned = True
                continue

            last_output = time.time()
            stall_warned = False
            line = line.strip()
            if not line:
                continue

            if len(_diag_raw_lines) < _DIAG_RAW_LINES_MAX:
                _diag_raw_lines.append(line)

            if line.startswith("PRGV:"):
                # PRGV:current,total,max — ``current`` is the current
                # *task*'s progress, ``total`` the whole *operation*'s,
                # and ``max`` a fixed scale (65536).  Percent must be
                # total/max: current/total runs far ahead on single-title
                # rips and pegs at 100% / sawtooths on multi-title dumps.
                parts = line[5:].split(",")
                if len(parts) >= 3:
                    try:
                        total = int(parts[1])
                        scale = int(parts[2])
                        if scale > 0 and total > 0:
                            pct = min(
                                int(total / scale * 100), 100
                            )
                            on_progress(pct)
                            if pct != last_pct:
                                last_pct = pct
                                elapsed  = time.time() - rip_start
                                rate     = (
                                    total / elapsed
                                    if elapsed > 0 else 0
                                )
                                remain   = (
                                    int((scale - total) / rate)
                                    if rate > 0 else 0
                                )
                                mins, secs = divmod(remain, 60)
                                eta = (
                                    f"~{mins}m {secs:02d}s remaining"
                                    if remain > 0 else ""
                                )
                                on_log(f"Ripping: {pct}%  {eta}")
                    except ValueError:
                        pass
            elif line.startswith("PRGT:"):
                parts = line[5:].split(",")
                if len(parts) >= 3:
                    on_log(f"Task: {parts[2].strip()}")
            elif line.startswith("PRGC:"):
                parts = line[5:].split(",")
                if len(parts) >= 3:
                    on_log(parts[2].strip())
            elif line.startswith("MSG:"):
                m = _MSG_MESSAGE_RE.match(line)
                msg = m.group(1).strip() if m else ""
                if msg:
                    _emit_makemkv_message(msg)

        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
        except Exception:
            pass
        try:
            rc = proc.returncode
            if rc is None:
                rc = 1
        except Exception:
            rc = 1
        on_log(f"MakeMKV exit code: {rc}")
        self.current_process = None

        try:
            reader.join(timeout=1)
        except Exception:
            pass

        try:
            while True:
                line = line_queue.get_nowait()
                line = line.strip()
                if line.startswith("MSG:"):
                    m = _MSG_MESSAGE_RE.match(line)
                    msg = m.group(1).strip() if m else ""
                    if msg:
                        _emit_makemkv_message(msg)
                        # Log all messages. Don't use them for failure
                        # detection — trust the return code only.
        except queue_module.Empty:
            pass

        for emitted in message_coalescer.flush():
            on_log(emitted)

        # Warn if MakeMKV exited successfully but we got stall warnings
        # during the rip — this can indicate disc read/write problems that
        # might prevent file creation even though the process succeeded.
        if rc == 0 and stall_warned:
            on_log(
                "Warning: MakeMKV exited successfully but had long I/O pauses. "
                "Files may be incomplete or missing; validation will follow."
            )

        self.last_process_issue_summary = issue_summary
        for summary_line in issue_summary.build_summary_lines(
            phase="rip",
            exit_code=rc,
        ):
            on_log(summary_line)

        # AI diagnostics: finalize capture
        _diag_capture.exit_code = rc
        _diag_capture.end_time = datetime.now().isoformat()
        _diag_capture.duration_seconds = time.time() - rip_start
        _diag_capture.stdout = "\n".join(_diag_raw_lines[-200:])
        _diag_capture.stall_detected = stall_warned
        if issue_summary.has_disc_read_errors:
            diag_record(
                "warning" if rc == 0 else "error",
                "disc_read_error",
                "MakeMKV logged disc-read errors during rip",
                details={
                    "phase": "rip",
                    "exit_code": rc,
                    **issue_summary.to_dict(),
                },
                process_capture=_diag_capture,
            )
        if rc != 0:
            diag_process(_diag_capture, success=False,
                         category="subprocess_nonzero_exit")
        elif stall_warned:
            diag_record(
                "warning", "stall_timeout",
                "MakeMKV completed but had I/O stalls (exit %d)" % rc,
                details=_diag_capture.to_dict())
        return rc == 0

    def _run_preview_process(self, cmd, preview_seconds, on_log):
        """Run a disposable preview rip with no retries.

        ``preview_seconds`` > 0 stops the rip after that many seconds
        (a quick sample); 0 / falsy means NO time cap — the full
        title rips to completion (the watch-before-rip flow).
        """
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            # makemkvcon emits UTF-8 (see note at the scan Popen).
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **_POPEN_FLAGS
        )
        self.current_process = proc

        line_queue = queue_module.Queue()
        reader = threading.Thread(
            target=self._stdout_reader,
            args=(proc.stdout, line_queue),
            daemon=True
        )
        reader.start()

        start = time.time()
        timed_out = False

        while True:
            if self.abort_event.is_set():
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
                self.current_process = None
                return False

            if (
                preview_seconds
                and time.time() - start >= preview_seconds
                and proc.poll() is None
            ):
                timed_out = True
                on_log(
                    f"Preview sample reached {preview_seconds}s; stopping rip."
                )
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                except Exception:
                    pass
                break

            try:
                line = line_queue.get(timeout=0.5)
            except queue_module.Empty:
                if proc.poll() is not None:
                    break
                continue

            line = line.strip()
            if not line:
                continue

            if line.startswith("PRGT:"):
                parts = line[5:].split(",")
                if len(parts) >= 3:
                    on_log(f"Preview task: {parts[2].strip()}")
            elif line.startswith("MSG:"):
                parts = line[4:].split(",", 4)
                if len(parts) >= 5:
                    msg = parts[4].strip().strip('"')
                    if msg:
                        on_log(msg)

        try:
            proc.wait(timeout=10)
        except Exception:
            pass
        self.current_process = None

        try:
            reader.join(timeout=1)
        except Exception:
            pass

        return timed_out or proc.returncode == 0



    def _get_rip_attempts(self):
        """Resolve retry strategy flags based on config toggles."""
        count = int(self.cfg.get("opt_retry_attempts", 3))
        if not self.cfg.get("opt_auto_retry", True):
            count = 1
        return RIP_ATTEMPT_FLAGS[
            :max(1, min(count, len(RIP_ATTEMPT_FLAGS)))
        ]





    def analyze_files(self, mkv_files, on_log):
        """
        Analyze MKV files using ffprobe to get duration.
        Runs in parallel using ThreadPoolExecutor with automatic
        worker scaling (min(32, cpu_count+4)).

        Each worker kills its ffprobe process immediately on abort,
        preventing zombie processes. Results are collected in completion
        order, not submission order.

        Returns list of (filepath, duration_seconds, size_mb) tuples,
        sorted longest-first. Ties fall back to larger files first, then
        pathname order for determinism. Unknown-duration files are appended
        at the end and sorted largest-first.
        """
        ffprobe = self._get_ffprobe()
        abort   = self.abort_event
        results = []
        total   = len(mkv_files)

        def analyze_one(f):
            if abort.is_set():
                return None
            try:
                dur, mb = self._probe_file_duration_and_size(
                    f, ffprobe=ffprobe, honor_abort=True, on_log=on_log
                )
                return (f, dur, mb)
            except Exception:
                try:
                    mb = os.path.getsize(f) // (1024**2)
                except Exception:
                    mb = 0
                return (f, -1, mb)

        with ThreadPoolExecutor() as executor:
            futures = [
                executor.submit(analyze_one, f) for f in mkv_files
            ]
            for i, future in enumerate(as_completed(futures), 1):
                if abort.is_set():
                    on_log("Analysis aborted.")
                    return []
                try:
                    res = future.result()
                except Exception:
                    res = None
                if res is not None:
                    results.append(res)
                    on_log(
                        f"Analyzed {i}/{total}: "
                        f"{os.path.basename(res[0])}"
                    )

        known = [x for x in results if x[1] > 0]
        unknown = [x for x in results if x[1] <= 0]
        known.sort(
            key=lambda x: (
                -x[1],
                -x[2],
                os.path.basename(x[0]).lower(),
                os.path.normpath(x[0]).lower(),
            )
        )
        unknown.sort(
            key=lambda x: (
                -x[2],
                os.path.basename(x[0]).lower(),
                os.path.normpath(x[0]).lower(),
            )
        )
        return known + unknown

    def _probe_file_duration_and_size(self, path, ffprobe=None, honor_abort=False,
                                      on_log=None):
        """Return (duration_seconds, size_mb) with metadata cache by mtime/size."""
        stat_result = [None]
        stat_error = [None]

        def _do_stat():
            try:
                stat_result[0] = os.stat(path)
            except Exception as e:
                stat_error[0] = e

        stat_thread = threading.Thread(target=_do_stat, daemon=True)
        stat_thread.start()
        stat_thread.join(timeout=8.0)
        if stat_thread.is_alive():
            if on_log:
                on_log(
                    f"WARNING: ffprobe stat timed out for {os.path.basename(path)}"
                )
            return -1.0, 0
        if stat_error[0] is not None or stat_result[0] is None:
            if on_log and stat_error[0] is not None:
                on_log(
                    "WARNING: ffprobe stat failed for "
                    f"{os.path.basename(path)}: {stat_error[0]}"
                )
            return -1.0, 0

        stat = stat_result[0]
        # ``os.path.normcase`` collapses ``C:\Foo\Bar`` and
        # ``c:\foo\bar`` to a single canonical form on Windows so the
        # cache doesn't get two separate entries for the same file
        # reached via differently-cased paths.  No-op on POSIX.  Other
        # path-comparison sites in this file already normcase
        # (e.g., the rip-output reconciliation in run_rip_session);
        # the ffprobe-cache key was the one outlier.
        cache_key = (
            os.path.normcase(os.path.abspath(path)),
            stat.st_mtime_ns,
            stat.st_size,
        )
        with self._ffprobe_cache_lock:
            cached = self._ffprobe_cache.get(cache_key)
            if cached is not None:
                return cached

        ffprobe_exe = ffprobe or self._get_ffprobe()
        mb = stat.st_size // (1024**2)
        dur = -1.0
        out = ""
        timed_out = False
        proc = subprocess.Popen(
            [ffprobe_exe, "-v", "error", "-show_entries",
             "format=duration", "-of", "json", path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # ffprobe's JSON output is UTF-8 (filenames echo into it).
            encoding="utf-8",
            errors="replace",
            **_POPEN_FLAGS
        )
        try:
            if honor_abort:
                timeout_per_check = 0.1
                max_total_time = 30
                elapsed = 0.0
                while proc.poll() is None and elapsed < max_total_time:
                    if self.abort_event.is_set():
                        proc.kill()
                        try:
                            proc.communicate(timeout=2)
                        except Exception:
                            pass
                        # Use -1.0 (float) not -1 (int) so the
                        # return type is consistent with the other
                        # two early-exit sites above and the
                        # "duration unknown" sentinel callers
                        # check against.
                        return -1.0, mb
                    time.sleep(timeout_per_check)
                    elapsed += timeout_per_check
                if proc.poll() is None:
                    timed_out = True
                    proc.kill()
            out, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            try:
                out, _ = proc.communicate(timeout=2)
            except Exception:
                out = ""

        if timed_out and on_log:
            on_log(
                f"WARNING: ffprobe timed out for {os.path.basename(path)}"
            )

        try:
            data = json.loads(out)
            dur = float(data.get("format", {}).get("duration", -1) or -1)
        except Exception as e:
            if on_log:
                on_log(
                    f"WARNING: ffprobe parse failed for {os.path.basename(path)}: {e}"
                )
            dur = -1.0

        result = (dur, mb)
        with self._ffprobe_cache_lock:
            if len(self._ffprobe_cache) >= self._ffprobe_cache_max_size:
                oldest_key = next(iter(self._ffprobe_cache), None)
                if oldest_key is not None:
                    self._ffprobe_cache.pop(oldest_key, None)
            self._ffprobe_cache[cache_key] = result
        return result

    def copy_with_abort(self, src, dst, buf_size=8 * 1024 * 1024):
        """Stream-copy a file while honoring abort requests between chunks."""
        use_fsync = self.cfg.get("opt_fsync", True)
        try:
            with open(self._io_path(src), "rb") as fsrc, open(self._io_path(dst), "wb") as fdst:
                while True:
                    if self.abort_event.is_set():
                        return False
                    chunk = fsrc.read(buf_size)
                    if not chunk:
                        break
                    fdst.write(chunk)
                fdst.flush()
                if use_fsync:
                    os.fsync(fdst.fileno())
            return True
        except Exception as e:
            self.last_move_error = f"Copy failed: {e}"
            return False

    def _quick_ffprobe_ok(self, path, on_log):
        """Fast container integrity check: ffprobe must return duration > 0."""
        try:
            ffprobe = self._get_ffprobe()
            duration, _mb = self._probe_file_duration_and_size(
                path, ffprobe=ffprobe, honor_abort=False, on_log=on_log
            )
            if duration <= 0:
                on_log(
                    "ERROR: ffprobe duration invalid for "
                    f"{os.path.basename(path)}"
                )
                return False
            return True
        except Exception as e:
            on_log(
                "ERROR: ffprobe integrity check failed for "
                f"{os.path.basename(path)}: {e}"
            )
            return False

    def _quarantine_bad_destination(self, io_final_path, on_log):
        """Rename a destination that failed post-move validation to
        ``<name>.failed-validation`` so a corrupt file never sits in
        the library under its real name (media servers index it
        immediately)."""
        try:
            os.replace(io_final_path, io_final_path + ".failed-validation")
            on_log(
                "Quarantined failed destination as "
                f"{os.path.basename(io_final_path)}.failed-validation"
            )
        except Exception as q_err:
            on_log(f"WARNING: could not quarantine bad destination: {q_err}")

    def move_file_atomic(
        self,
        source,
        final_path,
        on_log,
        replace_existing=False,
    ):
        """
        Move a file safely using copy+rename (atomic move).
        Preserves timestamps via shutil.copystat.
        Falls back to direct shutil.move if opt_atomic_move is off.
        Cleans up .partial file on abort or failure.
        REQUIRES: Source must be fully written (verified by move start check).
        """
        self.last_move_error = ""
        io_source = self._io_path(source)

        if not os.path.exists(io_source):
            on_log(f"Missing file: {source}")
            self.last_move_error = "Move failed — source file is missing."
            return False

        # Verify source has stopped growing before accepting move.
        # Prior implementation took a single before/after sample over
        # 1 second and failed immediately if the size changed.  That
        # made ``opt_stabilize_timeout_seconds`` misleading — its
        # name (and default of 60) suggested "wait up to 60s for the
        # file to settle", but the code only ever observed for ~1s.
        # Now ``stabilize_timeout`` is an actual deadline: poll the
        # file size every second (or every 1/10 of the timeout, up
        # to 1 second) until either two consecutive samples agree
        # (stable!) or the deadline elapses (failure).
        if self.cfg.get("opt_file_stabilization", True):
            stabilize_timeout = float(self.cfg.get("opt_stabilize_timeout_seconds", 60))
            check_interval = max(0.1, min(1.0, stabilize_timeout / 10))
            try:
                deadline = time.time() + max(check_interval, stabilize_timeout)
                size_before = os.path.getsize(io_source)
                stable = False
                while time.time() < deadline:
                    if self.abort_event.is_set():
                        on_log("Stabilization check aborted.")
                        self.last_move_error = (
                            "Move cancelled during stabilization check."
                        )
                        return False
                    time.sleep(check_interval)
                    size_after = os.path.getsize(io_source)
                    if size_before == size_after:
                        stable = True
                        break
                    size_before = size_after
                if not stable:
                    on_log(
                        f"ERROR: Source file did not stabilize within "
                        f"{stabilize_timeout:.0f}s "
                        f"(last size {size_before} bytes, still growing)."
                    )
                    self.last_move_error = (
                        "Move failed — source file incomplete. "
                        "Stabilization did not complete within the configured "
                        "timeout."
                    )
                    return False
            except Exception as e:
                on_log(f"ERROR checking source stability: {e}")
                return False

        if os.path.exists(final_path) and not replace_existing:
            final_path = self.unique_path(final_path)
            on_log(
                f"Destination exists. Using unique path: {final_path}"
            )
        elif os.path.exists(final_path):
            on_log(
                f"Destination exists. Replacing: {final_path}"
            )

        verify_retries = max(
            1, int(self.cfg.get("opt_move_verify_retries", 5))
        )

        def wait_for_size_match(src_size, dst_path):
            last_size = -1
            for _ in range(verify_retries):
                if os.path.exists(dst_path):
                    last_size = os.path.getsize(dst_path)
                    if last_size == src_size:
                        return True, last_size
                if self.abort_event.is_set():
                    break
                time.sleep(1.0)
            return False, last_size

        if not self.cfg.get("opt_atomic_move", True):
            try:
                io_final_path = self._io_path(final_path)
                src_size = os.path.getsize(io_source)
                shutil.move(io_source, io_final_path)
                if not os.path.exists(io_final_path):
                    on_log("ERROR: destination missing after move.")
                    self.last_move_error = (
                        "Move failed — destination file missing after move."
                    )
                    return False
                size_ok, dst_size = wait_for_size_match(
                    src_size, io_final_path
                )
                if not size_ok:
                    on_log(
                        "ERROR: destination size mismatch after move "
                        f"(src={src_size} bytes, dst={dst_size} bytes) "
                        f"after {verify_retries} verification check(s)."
                    )
                    self.last_move_error = (
                        "Move failed — network write mismatch. "
                        "Destination size did not match source."
                    )
                    # shutil.move already consumed the source; don't
                    # leave the bad copy at the library path.
                    self._quarantine_bad_destination(io_final_path, on_log)
                    return False
                if not self._quick_ffprobe_ok(io_final_path, on_log):
                    self.last_move_error = (
                        "Move failed — destination file failed integrity "
                        "probe (ffprobe)."
                    )
                    self._quarantine_bad_destination(io_final_path, on_log)
                    return False
                return True
            except Exception as e:
                on_log(f"ERROR moving file: {e}")
                return False

        temp_dest = final_path + ".partial"
        io_temp_dest = self._io_path(temp_dest)
        io_final_path = self._io_path(final_path)
        try:
            ok = self.copy_with_abort(source, temp_dest)
            if not ok or self.abort_event.is_set():
                if os.path.exists(io_temp_dest):
                    try:
                        os.remove(io_temp_dest)
                    except Exception:
                        pass
                on_log("Move aborted — partial file removed.")
                self.last_move_error = "Move failed — operation aborted."
                return False
            try:
                shutil.copystat(io_source, io_temp_dest)
            except Exception:
                pass
            if os.path.exists(io_final_path) and not replace_existing:
                new_final = self.unique_path(final_path)
                on_log(
                    "Destination appeared during move; "
                    f"using unique path: {new_final}"
                )
                final_path = new_final
                io_final_path = self._io_path(final_path)
            src_size = os.path.getsize(io_source)  # read before replace — source may vanish after
            # Validate the STAGED copy before it takes the final name —
            # a short/corrupt copy must never appear in the library
            # under its real filename (media servers index it
            # immediately, and a later retry would just uniquify
            # around it).  The old order validated after os.replace,
            # leaving the bad file at the destination on failure.
            size_ok, dst_size = wait_for_size_match(src_size, io_temp_dest)
            if not size_ok:
                on_log(
                    "ERROR: staged copy size mismatch before finalize "
                    f"(src={src_size} bytes, staged={dst_size} bytes) "
                    f"after {verify_retries} verification check(s).  "
                    "Source file retained; destination not created."
                )
                self.last_move_error = (
                    "Move failed — staged copy size mismatch (network "
                    "write?).  Destination was not finalized."
                )
                try:
                    os.remove(io_temp_dest)
                except Exception:
                    pass
                return False
            if not self._quick_ffprobe_ok(io_temp_dest, on_log):
                self.last_move_error = (
                    "Move failed — staged copy failed integrity probe "
                    "(ffprobe).  Destination was not finalized."
                )
                try:
                    os.remove(io_temp_dest)
                except Exception:
                    pass
                return False
            try:
                os.replace(io_temp_dest, io_final_path)
            except OSError as e:
                # Cross-volume fallback when atomic rename is unavailable.
                on_log(
                    f"Atomic rename failed ({e}); falling back to shutil.move for "
                    f"{os.path.basename(source)}"
                )
                try:
                    shutil.move(io_temp_dest, io_final_path)
                except Exception as move_err:
                    on_log(
                        f"ERROR: fallback move failed for {os.path.basename(source)}: {move_err}"
                    )
                    self.last_move_error = f"Move failed — fallback move error: {move_err}"
                    return False
            if os.path.exists(io_final_path):
                try:
                    os.remove(io_source)
                except Exception as remove_err:
                    on_log(
                        f"WARNING: moved file but could not remove source "
                        f"{os.path.basename(source)}: {remove_err}"
                    )
            else:
                on_log("ERROR: destination missing after move.")
                self.last_move_error = (
                    "Move failed — destination file missing after move."
                )
                return False
            return True
        except Exception as e:
            if os.path.exists(io_temp_dest):
                try:
                    os.remove(io_temp_dest)
                except Exception:
                    pass
            on_log(f"ERROR moving file: {e}")
            self.last_move_error = f"Move failed \u2014 {e}"
            diag_exception(e, context="move_file_atomic",
                           category="move_verify_failure")
            return False

    def move_files(self, titles_list, main_indices, episode_numbers,
                   real_names, extra_indices, is_tv, title, dest_folder,
                   extras_folder, season, year, extra_counter,
                   on_progress, on_log,
                   bonus_indices=None, bonus_folder=None,
                   replace_main_existing=False, edition=""):
        """Move selected main/extras/bonus files into final library structure.

        extra_indices: None = keep all non-main as extras,
                       []   = keep no extras,
                       [i]  = keep only those absolute indices as extras.
        bonus_indices: None = no bonus category, [] = none,
                       [i]  = absolute indices to move into bonus_folder.
        """
        _main_set    = set(main_indices)
        _extras_list = (
            [i for i in range(len(titles_list)) if i not in _main_set]
            if extra_indices is None
            else list(extra_indices)
        )
        _bonus_list = list(bonus_indices) if bonus_indices else []
        total_to_move = len(main_indices) + len(_extras_list) + len(_bonus_list)
        moved = 0
        moved_paths = []

        selected_size = sum(
            os.path.getsize(titles_list[i][0]) for i in main_indices
        )
        if _extras_list:
            selected_size += sum(
                os.path.getsize(titles_list[i][0]) for i in _extras_list
            )
        if _bonus_list:
            selected_size += sum(
                os.path.getsize(titles_list[i][0]) for i in _bonus_list
            )

        if self.cfg.get("opt_check_dest_space", True):
            on_log("Checking destination drive space...")
            try:
                status, free, required = self.check_disk_space(
                    dest_folder, selected_size, on_log
                )
                if status == "block":
                    on_log(
                        f"ERROR: Critically low space on destination "
                        f"({free / (1024**3):.1f} GB free). "
                        f"Cannot proceed."
                    )
                    return False, extra_counter, moved_paths
            except Exception as e:
                on_log(
                    f"Warning: could not check destination space: {e}"
                )

        try:
            for idx, i in enumerate(main_indices):
                if self.abort_event.is_set():
                    on_log("Move aborted by user.")
                    return False, extra_counter, moved_paths

                source = titles_list[i][0]
                if is_tv:
                    ep_num  = episode_numbers[idx]
                    ep_name = clean_name(
                        real_names[idx] if idx < len(real_names)
                        else f"Episode {ep_num:02d}"
                    )
                    name = (
                        f"{clean_name(title)} - "
                        f"S{season:02d}E{ep_num:02d} - {ep_name}.mkv"
                    )
                else:
                    # Inlined movie-filename construction (was
                    # `build_movie_main_filename` from
                    # controller.naming, which made engine depend on
                    # the controller layer — see audit item #16).
                    # Mirrors controller/naming.py:build_movie_base_name
                    # exactly: "Title (Year)" with " - Edition" suffix
                    # when an edition is supplied.
                    edition_raw = str(edition or "").strip()
                    cleaned_edition = (
                        clean_name(edition_raw).strip()
                        if edition_raw
                        else ""
                    )
                    suffix = (
                        f" - {cleaned_edition}" if cleaned_edition else ""
                    )
                    name = f"{clean_name(title)} ({year}){suffix}.mkv"

                final_path = os.path.join(dest_folder, name)
                if not replace_main_existing:
                    final_path = self.unique_path(final_path)
                on_log(f"Moving: {os.path.basename(source)}")
                on_log(f"    To: {final_path}")
                ok = self.move_file_atomic(
                    source,
                    final_path,
                    on_log,
                    replace_existing=bool(replace_main_existing),
                )
                if not ok:
                    return False, extra_counter, moved_paths

                moved += 1
                moved_paths.append(final_path)
                on_progress(int(moved / total_to_move * 100))
                on_log(f"Done: {os.path.basename(final_path)}")

            for i in _extras_list:
                if self.abort_event.is_set():
                    on_log("Move aborted by user.")
                    return False, extra_counter, moved_paths
                old_file, dur, mb = titles_list[i]
                if is_tv:
                    name = (
                        f"{clean_name(title)} - "
                        f"Extra {extra_counter}.mkv"
                    )
                else:
                    name = (
                        f"{clean_name(title)} ({year}) "
                        f"- Extra {extra_counter}.mkv"
                    )
                final_path = self.unique_path(
                    os.path.join(extras_folder, name)
                )
                extra_counter += 1
                on_log(
                    f"Moving extra: "
                    f"{os.path.basename(old_file)}"
                )
                ok = self.move_file_atomic(
                    old_file, final_path, on_log
                )
                if not ok:
                    return False, extra_counter, moved_paths
                moved += 1
                moved_paths.append(final_path)
                on_progress(
                    int(moved / total_to_move * 100)
                )
                on_log(
                    f"Done: {os.path.basename(final_path)}"
                )

            bonus_counter = 1
            for i in _bonus_list:
                if self.abort_event.is_set():
                    on_log("Move aborted by user.")
                    return False, extra_counter, moved_paths
                old_file, dur, mb = titles_list[i]
                if is_tv:
                    name = (
                        f"{clean_name(title)} - "
                        f"Bonus {bonus_counter}.mkv"
                    )
                else:
                    name = (
                        f"{clean_name(title)} ({year}) "
                        f"- Bonus {bonus_counter}.mkv"
                    )
                target_dir = bonus_folder or extras_folder
                final_path = self.unique_path(
                    os.path.join(target_dir, name)
                )
                bonus_counter += 1
                on_log(
                    f"Moving bonus: "
                    f"{os.path.basename(old_file)}"
                )
                ok = self.move_file_atomic(
                    old_file, final_path, on_log
                )
                if not ok:
                    return False, extra_counter, moved_paths
                moved += 1
                moved_paths.append(final_path)
                on_progress(
                    int(moved / total_to_move * 100)
                )
                on_log(
                    f"Done: {os.path.basename(final_path)}"
                )

            on_log(f"All files moved. {moved} file(s) total.")
            return True, extra_counter, moved_paths

        except Exception as e:
            on_log(f"ERROR during move: {e}")
            on_log(
                "Check temp folder — "
                "some files may not have moved."
            )
            return False, extra_counter, moved_paths

    def write_session_log(self, log_file, start_time,
                          session_log, on_log):
        """Append session logs to disk with rollover for oversized log files."""
        if not log_file:
            on_log("No log file configured — session log not saved.")
            return
        try:
            # Ensure log file has .txt extension
            if not log_file.lower().endswith(('.txt', '.log')):
                log_file = log_file + '.txt'
            log_dir = os.path.dirname(log_file)
            if log_dir:
                os.makedirs(self._io_path(log_dir), exist_ok=True)
            max_size = _SESSION_LOG_ROLLOVER_BYTES
            io_log_file = self._io_path(log_file)
            if (os.path.exists(io_log_file) and
                    os.path.getsize(io_log_file) >= max_size):
                # Read only first/last line metadata to avoid loading large logs in memory.
                start_date = "unknown"
                end_date = "unknown"
                with open(io_log_file, "rb") as f:
                    first = f.readline().decode("utf-8", errors="replace").strip()
                    if first:
                        start_date = first[:10]

                    f.seek(0, os.SEEK_END)
                    file_size = f.tell()
                    if file_size > 0:
                        offset = min(file_size, 8192)
                        f.seek(-offset, os.SEEK_END)
                        tail = f.read().decode("utf-8", errors="replace")
                        tail_lines = [ln for ln in tail.splitlines() if ln.strip()]
                        if tail_lines:
                            end_date = tail_lines[-1][:10]
                old_name = (
                    f"rip_log_{start_date}_to_{end_date}.txt"
                )
                shutil.move(
                    io_log_file, self._io_path(os.path.join(log_dir, old_name))
                )
            with open(io_log_file, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*60}\n")
                f.write(
                    f"Session: "
                    f"{start_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                )
                f.write(f"{'='*60}\n")
                for line in session_log:
                    f.write(line + "\n")
                f.write(f"{'='*60}\n")
            on_log(f"Session log written to: {log_file}")
        except Exception as e:
            on_log(f"Warning: could not write log: {e}")



__all__ = ["RipperEngine"]
