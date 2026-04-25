"""GUI layer implementation."""


import glob
import json
import math
import os
import platform
import queue as queue_module
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import quote_plus

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from gui.secure_tk import SecureTk
from shared.event import Event
from ui.adapters import UIAdapter
from ui.dialogs import ask_yes_no
from ui.settings import summarize_profile


if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes

    class _TaskbarProgress:
        """ITaskbarList3 taskbar progress overlay — Windows only."""
        TBPF_NOPROGRESS = 0
        TBPF_NORMAL     = 2
        TBPF_ERROR      = 4

        def __init__(self, hwnd):
            self._hwnd = hwnd
            self._com  = None
            try:
                clsid = ctypes.c_buffer(
                    b'\x55\xb9\xfb\x56\x37\x13\x43\x42'
                    b'\x9a\xdc\x9c\xc6\x04\x2e\x63\x33', 16)
                iid = ctypes.c_buffer(
                    b'\x91\xfb\x1a\xea\x28\x9e\x86\x4b'
                    b'\x90\xe9\x9e\x9f\x8a\x5e\xef\xaf', 16)
                obj = ctypes.c_void_p()
                hr = ctypes.windll.ole32.CoCreateInstance(
                    clsid, None, 1, iid, ctypes.byref(obj))
                if hr == 0:
                    self._com = obj
                    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))
                    ctypes.CFUNCTYPE(
                        ctypes.HRESULT, ctypes.c_void_p
                    )(vtbl[0][3])(obj)
            except Exception:
                pass

        def set_value(self, current, total):
            if not self._com or total <= 0:
                return
            try:
                vtbl = ctypes.cast(self._com, ctypes.POINTER(ctypes.c_void_p))
                fn = ctypes.CFUNCTYPE(
                    ctypes.HRESULT, ctypes.c_void_p,
                    ctypes.wintypes.HWND,
                    ctypes.c_ulonglong, ctypes.c_ulonglong
                )(vtbl[0][9])
                fn(self._com, self._hwnd, current, total)
            except Exception:
                pass

        def set_state(self, state):
            if not self._com:
                return
            try:
                vtbl = ctypes.cast(self._com, ctypes.POINTER(ctypes.c_void_p))
                fn = ctypes.CFUNCTYPE(
                    ctypes.HRESULT, ctypes.c_void_p,
                    ctypes.wintypes.HWND, ctypes.c_int
                )(vtbl[0][10])
                fn(self._com, self._hwnd, state)
            except Exception:
                pass

        def clear(self):
            self.set_state(self.TBPF_NOPROGRESS)

else:
    class _TaskbarProgress:
        TBPF_NOPROGRESS = 0
        TBPF_NORMAL     = 2
        TBPF_ERROR      = 4
        def __init__(self, hwnd): pass
        def set_value(self, c, t): pass
        def set_state(self, s):   pass
        def clear(self):          pass

from shared.runtime import (
    APP_AUMID,
    APP_DISPLAY_NAME,
    APP_EXE_BASENAME,
    CONFIG_FILE,
    DEFAULTS,
    RIP_ATTEMPT_FLAGS,
    __version__,
    _duration_debug_warn,
    _safe_int_debug_warn,
    configure_duration_debug,
    configure_safe_int_debug,
    get_config_dir,
)
from shared.ai_profile import (
    AIProfile,
    AI_PROFILE_CHOICE_LABELS,
    AI_PROFILE_FIELDS,
    AI_PROFILE_VALUE_LABELS,
    DEFAULT_AI_PROFILE,
    load_ai_profile,
)
from shared.ai_chat_memory import AIChatMemory
from shared.ai_chat_replay import (
    ai_chat_replay_path,
    append_ai_chat_replay,
    list_ai_chat_replay_bundles,
)

from config import (
    handbrake_gui_installed,
    load_startup_config,
    resolve_ffmpeg,
    resolve_ffprobe,
    resolve_handbrake,
    resolve_makemkvcon,
    save_config,
    should_keep_current_tool_path,
    validate_ffmpeg,
    validate_ffprobe,
    validate_handbrake,
    validate_makemkvcon,
)
from core.media_scan import (
    build_folder_scan_request,
    build_folder_scan_results_model,
    select_folder_scan_entries,
    select_folder_scan_paths,
)
from controller.controller import RipperController
from controller.naming import (
    build_fallback_title,
    build_naming_preview_text,
    normalize_naming_mode,
    resolve_naming_mode,
)
from engine.ripper_engine import RipperEngine
from transcode.engine import (
    FFMPEG_SOURCE_MODE_SAFE_COPY,
    describe_ffmpeg_source_mode,
    normalize_ffmpeg_source_mode,
)
from transcode.planner import (
    FFMPEG_SOURCE_MODE_LABEL_TO_VALUE,
    build_transcode_plan,
    ffmpeg_source_mode_label,
    suggest_transcode_output_root,
    transcode_backend_label,
)
from transcode.encoder_probe import get_ffmpeg_version_info
from transcode.profiles import PROFILE_SCHEMA
from transcode.profiles import ProfileLoader
from transcode.profiles import ProfileValidationError
from transcode.profiles import TranscodeProfile
from transcode.profiles import normalize_profile_data
from transcode.queue_builder import (
    build_queue_jobs,
    build_recommendation_job,
    build_transcode_queue,
    required_output_directories,
)
from transcode.recommendations import (
    build_ffmpeg_recommendations,
    format_analysis_summary,
    probe_media_for_recommendation,
)
from utils.helpers import (
    MakeMKVDrive,
    format_makemkv_drive_label,
    get_available_drives,
    is_network_path,
    make_default_drive,
    make_rip_folder_name,
)
from utils.scoring import format_audio_summary
from utils.classifier import (
    ClassifiedTitle,
    classification_matches_titles,
    classify_titles,
    get_recommended_title,
)
from gui.theme import (
    APP_THEME,
    build_app_theme,
)

from gui.update_ui import check_for_updates, launch_downloaded_update
from shared.windows_exec import (
    get_explorer_executable,
    get_powershell_executable,
)


def compute_initial_window_bounds(
    screen_width: int,
    screen_height: int,
) -> tuple[int, int, int, int]:
    screen_width = max(1024, int(screen_width))
    screen_height = max(768, int(screen_height))
    window_width = max(1024, min(1260, screen_width - 160))
    window_height = max(760, min(900, screen_height - 120))
    min_width = min(window_width, 1040)
    min_height = min(window_height, 760)
    return window_width, window_height, min_width, min_height


def get_bottom_safe_margin_px(cfg) -> int:
    return int((cfg or {}).get("opt_bottom_safe_margin_px", 72))


HANDBRAKE_PRESETS = [
    "Fast 1080p30",
    "HQ 1080p30 Surround",
    "Fast 2160p60 4K HEVC",
    "HQ 2160p60 4K HEVC Surround",
    "Super HQ 1080p30 Surround",
]
TRANSCODE_PROFILE_FILENAME = "transcode_profiles.json"
CONCURRENT_MODE_KEYS = frozenset({"scan"})
_AI_ASSISTANT_SYSTEM_PROMPT = (
    "You are the conversational assistant inside the JellyRip desktop app. "
    "Keep the tone natural and useful, like a strong general chat assistant rather than a rigid support script. "
    "You can help with the current rip session, answer general questions, explain what is happening, and suggest next steps. "
    "Every request includes a stable AI profile, controller-owned workflow facts, and a UI snapshot with the current status, progress, selected drive, "
    "and recent live log lines so you can reason from what the user sees. "
    "Use that context when it is relevant, but do not let it narrow answers that are clearly general. "
    "Treat the workflow facts, UI snapshot, and live log as the trusted source for app-state facts only. "
    "For ordinary movie or TV knowledge such as release year, edition naming, and common metadata identifiers like TMDB or TVDB, use normal model knowledge when you are confident. "
    "Do not invent app-state details such as progress values, drive names, workflow steps, or control labels that are not present in the provided context. "
    "If an app-state value is missing, say it is missing or uncertain. "
    "If the user is just greeting you or making general small talk, respond naturally instead of turning it into a UI status report. "
    "If the user asks what metadata to enter for a title, answer with the likely fields directly first, then mention uncertainty or alternate matches if needed. "
    "You do not have direct screen vision beyond that snapshot. "
    "Be concise, practical, flexible, and honest about uncertainty."
)

_AI_CHAT_QUOTA_ERROR_PATTERNS = (
    "quota",
    "rate_limit",
    "rate limit",
    "too many requests",
    "429",
    "insufficient_quota",
    "billing",
    "resource_exhausted",
)


def _friendly_ai_chat_error(message: str) -> str:
    lowered = str(message or "").strip().lower()
    if not lowered:
        return "Could not get an answer."
    if any(pattern in lowered for pattern in _AI_CHAT_QUOTA_ERROR_PATTERNS):
        return (
            "Cloud AI is rate limited or out of quota right now. "
            "Check billing or usage limits, or try again later."
        )
    if "timed out" in lowered or "timeout" in lowered:
        if "local" in lowered:
            return (
                "The local model timed out before it could finish. "
                "Try a smaller pulled model or raise the Local AI timeout in Settings."
            )
        return "The AI request timed out before it could finish."
    return f"Could not get an answer: {str(message or '').strip()}"


def _prompt_looks_like_ui_help(prompt: str) -> bool:
    lowered = str(prompt or "").lower()
    cues = (
        "current ui",
        "live log",
        "current rip",
        "what to do next",
        "next step",
        "status",
        "progress",
        "check progress",
        "how far",
        "what's happening",
        "what is happening",
        "drive",
        "session",
        "rip",
    )
    return any(cue in lowered for cue in cues)


def _looks_like_ai_payload_echo(message: str) -> bool:
    text = str(message or "").strip()
    if not text:
        return False
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        keys = {str(key) for key in parsed.keys()}
        if {"request", "conversation_history", "ui_snapshot"}.issubset(keys):
            return True
    lowered = text.lower()
    return (
        '"request"' in lowered
        and '"conversation_history"' in lowered
        and '"ui_snapshot"' in lowered
    )


def _build_ui_help_fallback(
    snapshot: dict[str, object],
    log_tail: str,
    error_message: str = "",
) -> str:
    status = str(snapshot.get("status", "") or "").strip()
    drive = str(snapshot.get("selected_drive", "") or "").strip()
    ai_mode = str(snapshot.get("ai_mode", "") or "").strip().lower()
    abort_state = str(snapshot.get("abort_button_state", "") or "").strip().lower()
    progress = 0.0
    try:
        progress = float(snapshot.get("progress_percent", 0.0) or 0.0)
    except Exception:
        progress = 0.0

    log_lower = str(log_tail or "").lower()
    error_lower = str(error_message or "").lower()
    suggestions: list[str] = []
    status_lower = status.lower()
    active_session = (
        progress > 0
        or any(token in status_lower for token in ("rip", "scan", "move", "prep", "abort"))
    )

    if status:
        if progress > 0:
            suggestions.append(
                f"Current status is {status} ({progress:.1f}% reported)."
            )
        elif active_session:
            suggestions.append(
                f"Current status is {status}. Reported progress is still {progress:.1f}%, which usually means the current step has started but has not emitted progress yet."
            )

    if "loading drives" in drive.lower():
        if active_session:
            suggestions.append(
                "The drive picker still says Loading drives..., but during an active session that field can lag behind the real rip state."
            )
        else:
            suggestions.append(
                "The drive list still looks busy. Refresh it or wait for the drive picker to finish populating before starting."
            )

    if active_session:
        suggestions.append(
            f"Let the current step continue unless the live log stops changing. Abort stays available while the session is active."
        )
        if abort_state == "normal":
            suggestions.append("Abort is available if the job is genuinely stuck.")
    elif "choose a mode to begin" in log_lower or status.lower() == "ready":
        suggestions.append(
            "Nothing is actively running right now. Once the drive is ready, choose the rip mode that matches the disc."
        )
        suggestions.append(
            "Use Rip Movie Disc for a film, Rip TV Show Disc for episodes, Dump All Titles for manual review, or Organize Existing MKVs for files already on disk."
        )

    if "no active session to abort" in log_lower:
        suggestions.append("There is no active rip session yet, so abort will not do anything.")

    if ai_mode == "local" and ("timed out" in error_lower or "timeout" in error_lower):
        suggestions.append(
            "The local assistant model is taking too long to answer. A smaller pulled Ollama model or a longer Local AI timeout in Settings will make the chat panel more reliable."
        )

    if not suggestions:
        return (
            "I could not get a model response, but the app still looks healthy enough to keep using. "
            "Check the latest live log lines, confirm the selected drive, and retry the request once the current state is stable."
        )

    return "\n".join(f"- {item}" for item in suggestions[:4])


def _ffmpeg_source_mode_label(value: str) -> str:
    return ffmpeg_source_mode_label(value)


def _transcode_backend_label(backend: str) -> str:
    return transcode_backend_label(backend)


def _suggest_transcode_output_root(scan_root: str, backend: str) -> str:
    return suggest_transcode_output_root(scan_root, backend)


def _build_transcode_plan(
    scan_root: str,
    selected_paths: list[str],
    output_root: str,
) -> list[dict[str, str]]:
    return build_transcode_plan(scan_root, selected_paths, output_root)


class JellyRipperGUI(SecureTk, UIAdapter):
    def auto_detect_existing_folder_mode(self, folder_path):
        """
        Auto-detect mode for an existing folder:
        - If folder is under tv_folder, default to 'no' for order prompt.
        - If folder is under movies_folder, default to 'main'.
        """
        tv_folder = self.cfg.get('tv_folder', '').lower()
        movies_folder = self.cfg.get('movies_folder', '').lower()
        folder_path_l = folder_path.lower()
        if tv_folder and folder_path_l.startswith(tv_folder):
            return 'tv_no_order'  # TV: quick no for order
        if movies_folder and folder_path_l.startswith(movies_folder):
            return 'movie_main'   # Movie: main
        return None


    # --- UIAdapter interface ---
    def handle_event(self, event: Event) -> None:
        if event.type == "progress":
            percent = event.data.get("percent")
            if isinstance(percent, (int, float)):
                self.on_progress(event.job_id, float(percent))
            return

        if event.type == "log":
            self.on_log(event.job_id, str(event.data.get("message", "")))
            return

        if event.type == "done":
            self.on_complete(event.job_id)
            return

        if event.type == "error":
            raw_error = event.data.get("error", "Unknown error")
            error = raw_error if isinstance(raw_error, Exception) else Exception(str(raw_error))
            self.on_error(event.job_id, error)

    def on_progress(self, _job_id: str, value: float) -> None:
        self.set_progress(value)

    def on_log(self, _job_id: str, message: str) -> None:
        self.append_log(message)

    def on_error(self, job_id: str, error: Exception) -> None:
        title = f"Job {job_id}" if job_id else "Rip Error"
        self.show_error(title, str(error))

    def on_complete(self, _job_id: str) -> None:
        self.set_progress(100)

    def __init__(self, cfg, startup_context=None):
        """
        LAYER 3 — GUI

        Display and input layer. All tkinter lives here and only here.

        Owns all widgets, user prompts, progress indicators, and the inline
        yes/no and text input UI. Makes no content decisions.

        Threading model: all GUI updates must happen on the main thread via
        self.after(). Worker threads communicate through:
          - message_queue → process_queue() polls every 100ms for log lines
          - threading.Event for ask_input() and ask_yesno() blocking calls
          - _run_on_main() for one-off calls that need a return value

        Never call engine or controller methods directly from widget
        callbacks. Always go through start_task() which runs the target
        in a daemon thread.
        """
        super().__init__()
        self.cfg   = cfg
        self._startup_context = dict(startup_context or {})
        self.title(f"{APP_DISPLAY_NAME} v{__version__}")
        self._theme = build_app_theme(self.cfg.get("opt_theme_overrides"))
        window_width, window_height, min_width, min_height = (
            compute_initial_window_bounds(
                self.winfo_screenwidth(),
                self.winfo_screenheight(),
            )
        )
        self.geometry(f"{window_width}x{window_height}")
        self.minsize(min_width, min_height)
        self.configure(bg=self._theme["window_bg"])
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.message_queue = queue_module.Queue()
        self.engine        = RipperEngine(cfg)
        self.controller    = RipperController(self.engine, self)
        self.rip_thread    = None
        self._settings_window = None
        self._input_result = None
        self._input_event  = threading.Event()
        self._input_active = False
        self._input_lock   = threading.Lock()
        self._log_widget_lock = threading.Lock()
        self._text_context_menu: tk.Menu | None = None
        self._text_context_widget: tk.Misc | None = None
        self._ai_sidebar_visible = bool(self.cfg.get("opt_ai_sidebar_open", False))
        self._ai_chat_busy = False
        self._ai_chat_history: list[dict[str, str]] = []
        self._ai_chat_memory = AIChatMemory()
        self._ai_chat_body_labels: list[tk.Misc] = []
        self._ai_chat_typing_row = None
        self._ai_chat_typing_body = None
        self._ai_chat_typing_job = None
        self._ai_chat_typing_base_text = "Thinking"
        self._ai_chat_typing_step = 0
        self._ai_chat_mousewheel_binding_installed = False
        self._ai_chat_transcript_frame = None
        self._task_active = False
        self._ai_sidebar_min_width = 340
        self._log_panel_min_width = 520
        self._progress_visible = False
        self._ai_sidebar_edge_margin = 18
        self._ai_sidebar_main_min_visible_width = 240
        self._ai_sidebar_width = max(
            self._ai_sidebar_min_width,
            int(self.cfg.get("opt_ai_sidebar_width", 360)),
        )
        self._ai_sidebar_resize_start_x = None
        self._ai_sidebar_resize_start_width = None
        self._ai_sidebar_overlay_anchor = None

        configure_safe_int_debug(
            cfg.get("opt_debug_safe_int", False),
            self.controller.log
        )
        configure_duration_debug(
            cfg.get("opt_debug_duration", False),
            self.controller.log
        )

        self.build_interface()
        self._install_text_context_menu_bindings()
        self.controller.log(f"{APP_DISPLAY_NAME} v{__version__} started")
        self.controller.log("Choose a mode to begin")
        self._taskbar_progress = None
        self.after(500, self._init_taskbar)
        # Schedule process_queue last to guarantee all widgets are initialized
        self.after(100, self.process_queue)
        if self._startup_context.get("issues"):
            self.after(150, self._apply_startup_context)

    def _apply_startup_context(self) -> None:
        raw_issues = self._startup_context.get("issues", [])
        issues = [
            str(issue).strip()
            for issue in raw_issues
            if str(issue).strip()
        ]
        if not issues:
            return

        self.controller.log("Startup recovery enabled.")
        for issue in issues:
            self.controller.log(f"Startup: {issue}")

        self.set_status("Settings need attention")
        summary = "\n\n".join(issues)
        try:
            self._run_on_main(
                lambda: messagebox.showwarning(
                    "Startup Recovery",
                    summary
                    + f"\n\n{APP_DISPLAY_NAME} opened with safe defaults so you can fix this in Settings.",
                    parent=self,
                )
            )
        except Exception:
            self.controller.log("Startup: could not show recovery dialog.")

        if self._startup_context.get("open_settings"):
            self.after(0, self._open_settings_safe)

    def _ui_state_debug_enabled(self) -> bool:
        try:
            return bool(self.cfg.get("opt_debug_state", False))
        except Exception:
            return False

    def _ui_state_debug_json_enabled(self) -> bool:
        try:
            return bool(self.cfg.get("opt_debug_state_json", False))
        except Exception:
            return False

    def _debug_widget_identity(self, widget) -> str:
        if widget is None:
            return ""

        parts: list[str] = []
        try:
            widget_class = str(widget.winfo_class() or "").strip()
            if widget_class:
                parts.append(widget_class)
        except Exception:
            pass
        try:
            widget_name = str(widget.winfo_name() or "").strip()
            if widget_name:
                parts.append(widget_name)
        except Exception:
            pass
        try:
            widget_path = str(widget)
            if widget_path:
                parts.append(widget_path)
        except Exception:
            pass
        if parts:
            return " | ".join(parts)
        try:
            return repr(widget)
        except Exception:
            return ""

    def _current_focus_identity(self) -> str:
        try:
            return self._debug_widget_identity(self.focus_get())
        except Exception:
            return ""

    def _debug_ui_event(self, event_name: str, **details) -> None:
        if not self._ui_state_debug_enabled():
            return

        payload: dict[str, object] = {
            "event": str(event_name or "").strip() or "unknown",
            "focus": self._current_focus_identity(),
            "thread": threading.current_thread().name or "unknown",
            "ai_sidebar_open": bool(getattr(self, "_ai_sidebar_visible", False)),
            "ai_chat_busy": bool(getattr(self, "_ai_chat_busy", False)),
            "task_active": bool(getattr(self, "_task_active", False)),
        }
        try:
            payload["abort_requested"] = bool(self.engine.abort_event.is_set())
        except Exception:
            payload["abort_requested"] = False

        for key, value in details.items():
            if key.endswith("widget"):
                payload[key] = self._debug_widget_identity(value)
            else:
                payload[key] = value

        try:
            if self._ui_state_debug_json_enabled():
                message = f"DEBUG UI {json.dumps(payload, sort_keys=True, default=str)}"
            else:
                detail_text = ", ".join(
                    f"{key}={payload[key]!r}"
                    for key in sorted(payload)
                    if payload[key] not in ("", None)
                )
                message = f"DEBUG UI {payload['event']}: {detail_text}"
            self.controller.log(message)
        except Exception:
            pass

    def _bind_debug_focus_trace(self, widget, source: str) -> None:
        if widget is None:
            return

        def _on_focus_in(event, _source=source):
            self._debug_ui_event(
                "focus_in",
                source=_source,
                widget=event.widget,
            )

        def _on_focus_out(event, _source=source):
            self._debug_ui_event(
                "focus_out",
                source=_source,
                widget=event.widget,
            )

        try:
            widget.bind("<FocusIn>", _on_focus_in, add="+")
        except Exception:
            pass
        try:
            widget.bind("<FocusOut>", _on_focus_out, add="+")
        except Exception:
            pass

    def _start_task_from_ui(self, mode: str, *, source: str) -> None:
        self._debug_ui_event(
            "mode_button_invoke",
            mode=mode,
            source=source,
        )
        self.start_task(mode)

    def _current_ai_chat_prompt_len(self) -> int:
        widget = self.__dict__.get("ai_chat_input")
        if widget is None:
            return 0
        try:
            return len(widget.get("1.0", "end-1c").strip())
        except Exception:
            return 0

    def _submit_ai_chat_from_ui(self, source: str = "button") -> None:
        self._debug_ui_event(
            "ai_chat_send_invoke",
            source=source,
            prompt_len=self._current_ai_chat_prompt_len(),
        )
        self._submit_ai_chat()

    def _append_log_text_main(self, msg, tag=None):
        """Append one line to the log widget from the Tk main thread only."""
        with self._log_widget_lock:
            self.log_text.config(state="normal")
            at_bottom = self.log_text.yview()[1] > 0.95
            text = msg if msg.endswith("\n") else f"{msg}\n"
            if tag:
                self.log_text.insert("end", text, tag)
            else:
                self.log_text.insert("end", text)
            # Trim widget to prevent unbounded memory growth in long sessions.
            line_count = int(self.log_text.index("end").split(".")[0]) - 1
            cap = int(self.cfg.get("opt_log_cap_lines", 300000))
            if line_count > cap:
                trim = int(self.cfg.get("opt_log_trim_lines", 200000))
                self.log_text.delete("1.0", f"{line_count - trim}.0")
            # Only auto-scroll if the user was already near the bottom.
            if at_bottom:
                self.log_text.see("end")
            self.log_text.config(state="disabled")

    def _install_text_context_menu_bindings(self) -> None:
        for class_name in ("Entry", "TEntry", "TCombobox", "Text"):
            self.bind_class(class_name, "<Button-3>", self._show_text_context_menu, add="+")

    def _capture_widget_selection_context(self, widget: tk.Misc) -> dict[str, object] | None:
        try:
            if isinstance(widget, (tk.Entry, ttk.Entry, ttk.Combobox)):
                if not widget.selection_present():
                    return None
                return {
                    "kind": "entry",
                    "start": int(widget.index("sel.first")),
                    "end": int(widget.index("sel.last")),
                }
            if isinstance(widget, tk.Text):
                return {
                    "kind": "text",
                    "start": str(widget.index("sel.first")),
                    "end": str(widget.index("sel.last")),
                }
        except Exception:
            return None
        return None

    def _is_text_widget_editable(self, widget: tk.Misc) -> bool:
        try:
            if isinstance(widget, ttk.Combobox):
                return not (widget.instate(("readonly",)) or widget.instate(("disabled",)))
            state = str(widget.cget("state"))
        except Exception:
            return True
        return state not in {"disabled", "readonly"}

    def _get_text_widget_selection(self, widget: tk.Misc) -> str:
        try:
            if isinstance(widget, (tk.Entry, ttk.Entry, ttk.Combobox)):
                if widget.selection_present():
                    return str(widget.selection_get())
                return ""
            if isinstance(widget, tk.Text):
                return str(widget.get("sel.first", "sel.last"))
        except Exception:
            return ""
        return ""

    def _text_widget_has_content(self, widget: tk.Misc) -> bool:
        try:
            if isinstance(widget, (tk.Entry, ttk.Entry, ttk.Combobox)):
                return bool(str(widget.get()))
            if isinstance(widget, tk.Text):
                return bool(str(widget.get("1.0", "end-1c")).strip())
        except Exception:
            return False
        return False

    def _copy_from_widget(self, widget: tk.Misc) -> None:
        selected = self._get_text_widget_selection(widget)
        if not selected:
            return
        self.clipboard_clear()
        self.clipboard_append(selected)

    def _cut_from_widget(self, widget: tk.Misc) -> None:
        if not self._is_text_widget_editable(widget):
            return
        self._copy_from_widget(widget)
        self._delete_widget_selection(widget)

    def _paste_into_widget(self, widget: tk.Misc) -> None:
        if not self._is_text_widget_editable(widget):
            return
        try:
            text = str(self.clipboard_get())
        except Exception:
            return
        self._delete_widget_selection(widget)
        try:
            if isinstance(widget, (tk.Entry, ttk.Entry, ttk.Combobox)):
                widget.insert("insert", text)
            elif isinstance(widget, tk.Text):
                widget.insert("insert", text)
        except Exception:
            pass

    def _delete_widget_selection(self, widget: tk.Misc) -> None:
        if not self._is_text_widget_editable(widget):
            return
        try:
            if isinstance(widget, (tk.Entry, ttk.Entry, ttk.Combobox)):
                if widget.selection_present():
                    first = int(widget.index("sel.first"))
                    last = int(widget.index("sel.last"))
                    widget.delete(first, last)
            elif isinstance(widget, tk.Text):
                widget.delete("sel.first", "sel.last")
        except Exception:
            pass

    def _select_all_in_widget(self, widget: tk.Misc) -> None:
        try:
            widget.focus_set()
            if isinstance(widget, (tk.Entry, ttk.Entry, ttk.Combobox)):
                widget.selection_range(0, "end")
                widget.icursor("end")
            elif isinstance(widget, tk.Text):
                widget.tag_add("sel", "1.0", "end-1c")
                widget.mark_set("insert", "end-1c")
                widget.see("insert")
        except Exception:
            pass

    @staticmethod
    def _trim_context_label(text: str, limit: int = 40) -> str:
        compact = " ".join(text.split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 3].rstrip() + "..."

    def _persist_config(self) -> None:
        try:
            save_config(self.cfg)
        except Exception:
            pass

    def _set_ai_profile(
        self,
        raw_profile: dict[str, object] | None = None,
        *,
        onboarded: bool | None = None,
    ) -> dict[str, str]:
        profile = AIProfile.from_mapping(raw_profile).to_dict()
        self.cfg["opt_ai_profile"] = dict(profile)
        if onboarded is not None:
            self.cfg["opt_ai_profile_onboarded"] = bool(onboarded)

        engine = getattr(self, "engine", None)
        engine_cfg = getattr(engine, "cfg", None)
        if isinstance(engine_cfg, dict):
            engine_cfg["opt_ai_profile"] = dict(profile)
            if onboarded is not None:
                engine_cfg["opt_ai_profile_onboarded"] = bool(onboarded)
        return profile

    def _ensure_ai_profile_onboarded(self) -> bool:
        if bool(self.cfg.get("opt_ai_profile_onboarded", False)):
            return True

        choice = self._run_on_main(
            lambda: messagebox.askyesnocancel(
                "AI Assistant Setup",
                f"Set up the {APP_DISPLAY_NAME} assistant profile before your first run?\n\n"
                "Yes = open AI Assistant settings now\n"
                "No = keep the current defaults and continue\n"
                "Cancel = stop here",
                parent=self,
            )
        )
        self._debug_ui_event(
            "ai_onboarding_prompt",
            choice=(
                "cancel" if choice is None else
                "yes" if choice else
                "no"
            ),
        )
        if choice is None:
            self.controller.log("AI assistant onboarding cancelled.")
            return False
        if choice:
            self.controller.log(
                "Opening AI assistant settings for first-use profile setup."
            )
            self.open_settings(selected_tab="ai")
            return False

        self._set_ai_profile(
            load_ai_profile(self.cfg).to_dict(),
            onboarded=True,
        )
        self._persist_config()
        self.controller.log(
            "AI assistant profile kept at defaults for now."
        )
        return True

    def _configure_main_styles(self) -> None:
        colors = self._theme
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "JellyRipMain.TCombobox",
            fieldbackground=colors["input_bg"],
            background=colors["input_bg"],
            foreground=colors["input_fg"],
            arrowcolor=colors["muted_soft"],
            bordercolor=colors["panel_border"],
            lightcolor=colors["input_bg"],
            darkcolor=colors["input_bg"],
            relief="flat",
            padding=(12, 6, 12, 6),
        )
        style.map(
            "JellyRipMain.TCombobox",
            fieldbackground=[("readonly", colors["input_bg"])],
            foreground=[("readonly", colors["input_fg"])],
            selectbackground=[("readonly", colors["input_bg"])],
            selectforeground=[("readonly", colors["input_fg"])],
            background=[("readonly", colors["input_bg"])],
            arrowcolor=[
                ("active", colors["title"]),
                ("readonly", colors["muted_soft"]),
            ],
        )
        style.configure(
            "JellyRipMain.Horizontal.TProgressbar",
            background=colors["progress_fill"],
            troughcolor=colors["progress_trough"],
            bordercolor=colors["panel_border"],
            lightcolor=colors["progress_fill"],
            darkcolor=colors["progress_fill"],
            thickness=10,
        )

    def _set_progress_visibility(self, visible: bool) -> None:
        if not hasattr(self, "progress_shell"):
            return
        if visible and not self._progress_visible:
            self.progress_shell.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(8, 0))
            self._progress_visible = True
        elif not visible and self._progress_visible:
            self.progress_shell.grid_remove()
            self._progress_visible = False

    def _focus_live_log(self) -> None:
        if not hasattr(self, "log_text"):
            return
        try:
            self.log_text.focus_set()
            self.log_text.see("end")
        except Exception:
            pass

    def _update_ai_sidebar_toggle_ui(self) -> None:
        if not hasattr(self, "ai_chat_toggle_btn"):
            return
        colors = self._theme
        if self._ai_sidebar_visible:
            self.ai_chat_toggle_btn.configure(
                bg=colors["toolbar_button_active"],
                fg=colors["toolbar_button_text"],
                activebackground=colors["toolbar_button_active"],
                activeforeground=colors["toolbar_button_text"],
                relief="flat",
            )
        else:
            self.ai_chat_toggle_btn.configure(
                bg=colors["toolbar_button"],
                fg=colors["toolbar_button_muted"],
                activebackground=colors["toolbar_button"],
                activeforeground=colors["toolbar_button_text"],
                relief="flat",
            )

    def _layout_ai_chat_action_row(self, width: int | None = None) -> None:
        row = self.__dict__.get("ai_chat_action_row")
        suggest_btn = self.__dict__.get("ai_chat_suggest_btn")
        new_btn = self.__dict__.get("ai_chat_new_btn")
        copy_btn = self.__dict__.get("ai_chat_copy_btn")
        send_btn = self.__dict__.get("ai_chat_send_btn")
        buttons = [suggest_btn, new_btn, copy_btn, send_btn]
        if row is None or any(button is None for button in buttons):
            return

        raw_width = width
        if raw_width is None or int(raw_width) <= 1:
            try:
                raw_width = int(row.winfo_width())
            except Exception:
                raw_width = 0
        if int(raw_width) <= 1:
            raw_width = max(0, int(self._ai_sidebar_width) - 36)

        wide_layout = int(raw_width) >= 440
        visible_columns = 4 if wide_layout else 2
        uniform_name = "ai_actions_wide" if wide_layout else "ai_actions_narrow"
        wraplength = max(72, int(raw_width / visible_columns) - 28)

        for column in range(4):
            row.grid_columnconfigure(column, weight=0, uniform="")
        for column in range(visible_columns):
            row.grid_columnconfigure(column, weight=1, uniform=uniform_name)
        for grid_row in range(2):
            row.grid_rowconfigure(grid_row, weight=0, minsize=0)

        placements = (
            [
                (suggest_btn, 0, 0),
                (new_btn, 0, 1),
                (copy_btn, 0, 2),
                (send_btn, 0, 3),
            ]
            if wide_layout else
            [
                (suggest_btn, 0, 0),
                (new_btn, 0, 1),
                (copy_btn, 1, 0),
                (send_btn, 1, 1),
            ]
        )

        for button in buttons:
            button.grid_forget()
            try:
                button.configure(wraplength=wraplength, justify="center")
            except Exception:
                pass

        for button, grid_row, column in placements:
            xpad = (0, 6) if column < visible_columns - 1 else (0, 0)
            ypad = (0, 6) if not wide_layout and grid_row == 0 else (0, 0)
            button.grid(
                row=grid_row,
                column=column,
                sticky="ew",
                padx=xpad,
                pady=ypad,
            )

    def _remember_ai_sidebar_width(self, width: int | None = None) -> None:
        raw_width = width
        sidebar_frame = self.__dict__.get("ai_sidebar_frame")
        if raw_width is None and sidebar_frame is not None:
            try:
                raw_width = int(sidebar_frame.winfo_width())
            except Exception:
                raw_width = None
        if raw_width is None:
            return
        new_width = self._clamp_ai_sidebar_width(int(raw_width))
        self._ai_sidebar_width = new_width
        self.cfg["opt_ai_sidebar_width"] = new_width

    def _get_ai_sidebar_max_width(self) -> int:
        try:
            window_width = int(self.winfo_width())
        except Exception:
            window_width = 0
        if window_width <= 1:
            return self._ai_sidebar_width
        max_width = (
            window_width
            - (self._ai_sidebar_edge_margin * 2)
            - self._ai_sidebar_main_min_visible_width
        )
        return max(self._ai_sidebar_min_width, int(max_width))

    def _clamp_ai_sidebar_width(self, width: int) -> int:
        return max(
            self._ai_sidebar_min_width,
            min(int(width), self._get_ai_sidebar_max_width()),
        )

    def _get_ai_sidebar_overlay_bounds(self) -> dict[str, int] | None:
        try:
            window_width = int(self.winfo_width())
            window_height = int(self.winfo_height())
        except Exception:
            return None
        if window_width <= 1 or window_height <= 1:
            return None

        anchor = self.__dict__.get("_ai_sidebar_overlay_anchor")
        margin = int(self._ai_sidebar_edge_margin)
        top = margin
        if anchor is not None:
            try:
                if not anchor.winfo_ismapped():
                    return None
                top = max(margin, int(anchor.winfo_y()))
            except Exception:
                top = margin

        height = window_height - top - margin
        if height <= 120:
            return None

        width = self._clamp_ai_sidebar_width(self._ai_sidebar_width)
        x = max(margin, window_width - margin - width)
        return {"x": x, "y": top, "width": width, "height": height}

    def _content_pane_has_widget(self, widget: tk.Misc) -> bool:
        if not hasattr(self, "content_pane"):
            return False
        try:
            panes = tuple(str(pane) for pane in self.content_pane.panes())
        except Exception:
            return False
        return str(widget) in panes

    def _on_ai_sidebar_configure(self, event) -> None:
        if not self._ai_sidebar_visible:
            return
        if int(getattr(event, "width", 0)) <= 1:
            return
        self._remember_ai_sidebar_width(int(event.width))
        self._refresh_ai_chat_bubble_layout(int(event.width))
        self._layout_ai_chat_action_row(int(event.width))

    def _on_ai_chat_canvas_configure(self, event) -> None:
        canvas = self.__dict__.get("ai_chat_canvas")
        window_id = self.__dict__.get("_ai_chat_canvas_window")
        if canvas is None or window_id is None:
            return
        width = max(1, int(getattr(event, "width", 1)))
        try:
            canvas.itemconfigure(window_id, width=width)
        except Exception:
            pass
        self._refresh_ai_chat_bubble_layout(width)

    def _install_ai_chat_mousewheel_binding(self) -> None:
        if getattr(self, "_ai_chat_mousewheel_binding_installed", False):
            return
        try:
            self.bind_all("<MouseWheel>", self._handle_ai_chat_mousewheel, add="+")
        except Exception:
            return
        self._ai_chat_mousewheel_binding_installed = True

    def _widget_is_inside_ai_chat_transcript(self, widget: tk.Misc | None) -> bool:
        if widget is None:
            return False
        ancestors = (
            self.__dict__.get("ai_chat_canvas"),
            self.__dict__.get("_ai_chat_transcript_frame"),
        )
        current = widget
        while current is not None:
            if any(current is ancestor for ancestor in ancestors if ancestor is not None):
                return True
            current = getattr(current, "master", None)
        return False

    def _handle_ai_chat_mousewheel(self, event) -> str | None:
        if not getattr(self, "_ai_sidebar_visible", False):
            return None
        if not self._widget_is_inside_ai_chat_transcript(getattr(event, "widget", None)):
            return None
        canvas = self.__dict__.get("ai_chat_canvas")
        if canvas is None:
            return None
        delta = int(getattr(event, "delta", 0))
        if delta == 0:
            return None
        units = int(-1 * (delta / 120))
        if units == 0:
            units = -1 if delta > 0 else 1
        try:
            canvas.yview_scroll(units, "units")
        except Exception:
            return None
        return "break"

    def _update_ai_chat_scrollregion(self, _event=None) -> None:
        canvas = self.__dict__.get("ai_chat_canvas")
        if canvas is None:
            return
        try:
            canvas.configure(scrollregion=canvas.bbox("all"))
        except Exception:
            pass

    def _get_ai_chat_bubble_wraplength(self, width: int | None = None) -> int:
        raw_width = width
        if raw_width is None:
            raw_width = self._ai_sidebar_width
            canvas = self.__dict__.get("ai_chat_canvas")
            if canvas is not None:
                try:
                    raw_width = int(canvas.winfo_width())
                except Exception:
                    raw_width = self._ai_sidebar_width
        return max(220, min(520, int(raw_width) - 140))

    def _get_ai_chat_text_width_chars(self, width: int | None = None) -> int:
        wraplength = self._get_ai_chat_bubble_wraplength(width)
        return max(24, min(74, int(round(wraplength / 7.2))))

    def _estimate_ai_chat_text_lines(
        self,
        content: str,
        width_chars: int,
    ) -> int:
        text = str(content or "")
        width = max(12, int(width_chars))
        total = 0
        for paragraph in text.splitlines() or [""]:
            line = paragraph or " "
            total += max(1, int(math.ceil(len(line) / width)))
        return max(1, total)

    def _fit_ai_chat_text_widget(self, widget: tk.Text, width: int | None = None) -> None:
        try:
            state = str(widget.cget("state"))
        except Exception:
            state = "normal"

        width_chars = self._get_ai_chat_text_width_chars(width)
        content = ""

        try:
            if state == "disabled":
                widget.configure(state="normal")
            widget.configure(width=width_chars)
            try:
                content = widget.get("1.0", "end-1c")
            except Exception:
                content = ""
            try:
                mapped = bool(widget.winfo_ismapped())
            except Exception:
                mapped = False
            if mapped:
                try:
                    line_count = int(
                        widget.count("1.0", "end-1c", "displaylines")[0]
                    )
                except Exception:
                    line_count = self._estimate_ai_chat_text_lines(
                        content,
                        width_chars,
                    )
            else:
                line_count = self._estimate_ai_chat_text_lines(
                    content,
                    width_chars,
                )
            widget.configure(height=max(1, line_count))
        finally:
            if state == "disabled":
                try:
                    widget.configure(state="disabled")
                except Exception:
                    pass

    def _refresh_ai_chat_bubble_layout(self, width: int | None = None) -> None:
        wraplength = self._get_ai_chat_bubble_wraplength(width)
        for label in list(getattr(self, "_ai_chat_body_labels", [])):
            try:
                if isinstance(label, tk.Text):
                    self._fit_ai_chat_text_widget(label, width)
                else:
                    label.configure(wraplength=wraplength)
            except Exception:
                pass

    def _scroll_ai_chat_to_end(self) -> None:
        canvas = self.__dict__.get("ai_chat_canvas")
        if canvas is None:
            return
        self._update_ai_chat_scrollregion()
        try:
            canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _ai_chat_typing_text_for_status(self, status_text: str | None = None) -> str:
        text = str(status_text or "").strip()
        if not text or text.lower().startswith("ready"):
            text = "Thinking"
        return text.rstrip(". ").strip() or "Thinking"

    def _tick_ai_chat_typing_indicator(self) -> None:
        body = self.__dict__.get("_ai_chat_typing_body")
        if body is None:
            self._ai_chat_typing_job = None
            return
        try:
            if not body.winfo_exists():
                self._ai_chat_typing_job = None
                return
        except Exception:
            self._ai_chat_typing_job = None
            return

        frames = ("", ".", "..", "...")
        suffix = frames[self._ai_chat_typing_step % len(frames)]
        self._ai_chat_typing_step += 1
        body.configure(text=f"{self._ai_chat_typing_base_text}{suffix}")
        self._ai_chat_typing_job = self.after(420, self._tick_ai_chat_typing_indicator)

    def _show_ai_chat_typing_indicator(self, status_text: str | None = None) -> None:
        container = self.__dict__.get("ai_chat_messages")
        if container is None:
            return

        self._ai_chat_typing_base_text = self._ai_chat_typing_text_for_status(status_text)
        self._ai_chat_typing_step = 0

        row = self.__dict__.get("_ai_chat_typing_row")
        body = self.__dict__.get("_ai_chat_typing_body")
        if row is None or body is None:
            colors = self._theme
            row = tk.Frame(container, bg=colors["surface_alt"])
            row.pack(fill="x", pady=(0, 10))

            bubble = tk.Frame(
                row,
                bg=colors["surface_deep"],
                highlightbackground=colors["panel_border"],
                highlightthickness=1,
                bd=0,
            )
            bubble.pack(side="left", padx=(0, 70))

            tk.Label(
                bubble,
                text="Assistant",
                bg=colors["surface_deep"],
                fg=colors["muted"],
                font=("Segoe UI", 9, "bold"),
                anchor="w",
            ).pack(fill="x", padx=12, pady=(10, 4))

            body = tk.Label(
                bubble,
                text="",
                bg=colors["surface_deep"],
                fg=colors["muted"],
                font=("Segoe UI", 11, "italic"),
                justify="left",
                anchor="w",
                wraplength=self._get_ai_chat_bubble_wraplength(),
            )
            body.pack(fill="x", padx=12, pady=(0, 10))
            self._ai_chat_body_labels.append(body)
            self._ai_chat_typing_row = row
            self._ai_chat_typing_body = body
        else:
            try:
                row.pack_forget()
            except Exception:
                pass
            row.pack(fill="x", pady=(0, 10))

        job = self.__dict__.get("_ai_chat_typing_job")
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass
            self._ai_chat_typing_job = None

        self._tick_ai_chat_typing_indicator()
        self.after_idle(self._scroll_ai_chat_to_end)

    def _hide_ai_chat_typing_indicator(self) -> None:
        job = self.__dict__.get("_ai_chat_typing_job")
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._ai_chat_typing_job = None

        row = self.__dict__.get("_ai_chat_typing_row")
        body = self.__dict__.get("_ai_chat_typing_body")
        if body in self._ai_chat_body_labels:
            self._ai_chat_body_labels.remove(body)
        if row is not None:
            try:
                row.destroy()
            except Exception:
                pass
        self._ai_chat_typing_row = None
        self._ai_chat_typing_body = None

    def _apply_ai_sidebar_width(self) -> None:
        if not hasattr(self, "ai_sidebar_frame"):
            return
        if not self._ai_sidebar_visible:
            try:
                self.ai_sidebar_frame.place_forget()
            except Exception:
                pass
            return
        bounds = self._get_ai_sidebar_overlay_bounds()
        if bounds is None:
            self.after(50, self._apply_ai_sidebar_width)
            return
        try:
            self.ai_sidebar_frame.place(
                x=bounds["x"],
                y=bounds["y"],
                width=bounds["width"],
                height=bounds["height"],
            )
            self.ai_sidebar_frame.lift()
        except Exception:
            pass

    def _on_content_pane_configure(self, _event) -> None:
        if not self._ai_sidebar_visible:
            return
        self.after_idle(self._apply_ai_sidebar_width)

    def _on_window_configure(self, event) -> None:
        if event.widget is not self or not self._ai_sidebar_visible:
            return
        self.after_idle(self._apply_ai_sidebar_width)

    def _start_ai_sidebar_resize(self, event) -> str:
        if not self._ai_sidebar_visible:
            return "break"
        self._ai_sidebar_resize_start_x = int(getattr(event, "x_root", 0))
        try:
            current_width = int(self.ai_sidebar_frame.winfo_width())
        except Exception:
            current_width = self._ai_sidebar_width
        self._ai_sidebar_resize_start_width = current_width
        return "break"

    def _drag_ai_sidebar_resize(self, event) -> str:
        start_x = self.__dict__.get("_ai_sidebar_resize_start_x")
        start_width = self.__dict__.get("_ai_sidebar_resize_start_width")
        if start_x is None or start_width is None:
            return "break"
        current_x = int(getattr(event, "x_root", start_x))
        delta = int(start_x) - current_x
        self._remember_ai_sidebar_width(int(start_width) + delta)
        self._apply_ai_sidebar_width()
        return "break"

    def _stop_ai_sidebar_resize(self, _event=None) -> str:
        self._ai_sidebar_resize_start_x = None
        self._ai_sidebar_resize_start_width = None
        self._persist_config()
        return "break"

    def _show_ai_sidebar(self) -> None:
        if not hasattr(self, "ai_sidebar_frame"):
            return
        self._ai_sidebar_visible = True
        self.cfg["opt_ai_sidebar_open"] = True
        self._debug_ui_event("ai_sidebar_show")
        self.after_idle(self._apply_ai_sidebar_width)
        self._persist_config()
        self._update_ai_sidebar_toggle_ui()
        if hasattr(self, "ai_chat_input"):
            self.after(0, self.ai_chat_input.focus_set)

    def _hide_ai_sidebar(self) -> None:
        self._remember_ai_sidebar_width()
        if hasattr(self, "ai_sidebar_frame"):
            try:
                self.ai_sidebar_frame.place_forget()
            except Exception:
                pass
        self._ai_sidebar_visible = False
        self.cfg["opt_ai_sidebar_open"] = False
        self._debug_ui_event("ai_sidebar_hide")
        self._persist_config()
        self._update_ai_sidebar_toggle_ui()

    def _toggle_ai_sidebar(self) -> None:
        if self._ai_sidebar_visible:
            self._hide_ai_sidebar()
        else:
            self._show_ai_sidebar()

    def _collect_live_log_tail(
        self,
        max_lines: int = 60,
        max_chars: int = 6000,
    ) -> str:
        if not hasattr(self, "log_text"):
            return ""
        try:
            content = self.log_text.get("1.0", "end-1c")
        except Exception:
            return ""
        if not content:
            return ""
        lines = content.splitlines()
        tail = "\n".join(lines[-max_lines:])
        if len(tail) > max_chars:
            tail = tail[-max_chars:]
        return tail

    def _get_ai_sidebar_snapshot(
        self,
        *,
        max_log_lines: int = 60,
        max_log_chars: int = 6000,
    ) -> dict[str, object]:
        status = self.status_var.get() if hasattr(self, "status_var") else ""
        progress = float(self.progress_var.get()) if hasattr(self, "progress_var") else 0.0
        drive = self.drive_var.get() if hasattr(self, "drive_var") else ""
        ai_mode = self._ai_mode_var.get() if hasattr(self, "_ai_mode_var") else "off"
        abort_state = (
            str(self.abort_btn.cget("state")) if hasattr(self, "abort_btn") else "disabled"
        )
        return {
            "window_title": self.title(),
            "status": status,
            "progress_percent": round(progress, 1),
            "selected_drive": drive,
            "ai_mode": ai_mode,
            "abort_button_state": abort_state,
            "assistant_panel_open": bool(self._ai_sidebar_visible),
            "live_log_tail": self._collect_live_log_tail(
                max_lines=max_log_lines,
                max_chars=max_log_chars,
            ),
        }

    def _build_ai_sidebar_payload(
        self,
        prompt: str,
        *,
        max_history: int = 8,
        max_log_lines: int = 60,
        max_log_chars: int = 6000,
    ) -> str:
        payload = self._build_ai_sidebar_context_payload(
            prompt,
            max_history=max_history,
            max_log_lines=max_log_lines,
            max_log_chars=max_log_chars,
        )
        return json.dumps(payload, indent=2)

    def _build_ai_sidebar_context_payload(
        self,
        prompt: str,
        *,
        max_history: int = 8,
        max_log_lines: int = 60,
        max_log_chars: int = 6000,
    ) -> dict[str, object]:
        snapshot = self._get_ai_sidebar_snapshot(
            max_log_lines=max_log_lines,
            max_log_chars=max_log_chars,
        )
        profile = self._get_ai_profile_context()
        session_facts = self._get_ai_session_facts()
        memory = self._sync_ai_chat_memory_from_history()
        memory.pin_session_facts(session_facts)
        memory_payload = memory.build_context_payload(
            max_recent_turns=max_history,
        )
        return {
            "request": str(prompt or "").strip(),
            "conversation_history": list(memory_payload.get("recent_turns", [])),
            "conversation_summary": str(memory_payload.get("rolling_summary", "") or ""),
            "pinned_session_facts": dict(
                memory_payload.get("pinned_session_facts", {})
                if isinstance(memory_payload.get("pinned_session_facts"), dict)
                else {}
            ),
            "compaction_trace": list(memory_payload.get("compaction_trace", [])),
            "ai_profile": profile,
            "session_facts": session_facts,
            "ui_snapshot": snapshot,
        }

    def _get_ai_profile_context(self) -> dict[str, str]:
        return load_ai_profile(self.__dict__.get("cfg", {})).to_dict()

    def _get_ai_session_facts(self) -> dict[str, object]:
        controller = self.__dict__.get("controller", None)
        build_facts = getattr(controller, "build_ai_session_facts", None)
        if not callable(build_facts):
            return {}
        try:
            facts = build_facts()
        except Exception as exc:
            return {"context_error": str(exc)}
        if isinstance(facts, dict):
            return facts
        return {}

    def _ensure_ai_chat_memory(self) -> AIChatMemory:
        memory = self.__dict__.get("_ai_chat_memory")
        if isinstance(memory, AIChatMemory):
            return memory
        memory = AIChatMemory()
        self._ai_chat_memory = memory
        return memory

    def _sync_ai_chat_memory_from_history(self) -> AIChatMemory:
        memory = self._ensure_ai_chat_memory()
        snapshot = memory.build_context_payload()
        if snapshot["recent_turns"] or snapshot["rolling_summary"] or snapshot["compaction_trace"]:
            return memory

        for item in list(self.__dict__.get("_ai_chat_history", [])):
            if not isinstance(item, dict):
                continue
            memory.remember_turn(
                item.get("role", ""),
                item.get("content", ""),
            )
        return memory

    def _remember_ai_chat_turn(self, role: str, text: str) -> dict[str, object] | None:
        return self._ensure_ai_chat_memory().remember_turn(role, text)

    def _build_ai_sidebar_chat_messages(
        self,
        prompt: str,
        *,
        max_history: int = 8,
        max_log_lines: int = 60,
        max_log_chars: int = 6000,
    ) -> list[dict[str, str]]:
        payload = self._build_ai_sidebar_context_payload(
            prompt,
            max_history=max_history,
            max_log_lines=max_log_lines,
            max_log_chars=max_log_chars,
        )
        return self._build_ai_sidebar_chat_messages_from_payload(payload)

    def _build_ai_sidebar_chat_messages_from_payload(
        self,
        payload: dict[str, object] | None,
    ) -> list[dict[str, str]]:
        payload = payload if isinstance(payload, dict) else {}
        request_text = str(payload.get("request", "") or "").strip()
        snapshot = payload.get("ui_snapshot")
        if not isinstance(snapshot, dict):
            snapshot = {}
        profile = payload.get("ai_profile")
        if not isinstance(profile, dict):
            profile = {}
        summary_text = str(payload.get("conversation_summary", "") or "").strip()
        pinned_session_facts = payload.get("pinned_session_facts")
        if not isinstance(pinned_session_facts, dict):
            pinned_session_facts = {}
        session_facts = payload.get("session_facts")
        if not isinstance(session_facts, dict):
            session_facts = {}
        if not pinned_session_facts:
            pinned_session_facts = dict(session_facts)
        raw_history = payload.get("conversation_history")
        if not isinstance(raw_history, list):
            raw_history = []
        history: list[dict[str, str]] = []
        for item in raw_history:
            role = str(item.get("role", "") or "").strip().lower()
            content = str(item.get("content", "") or "")
            if role not in {"user", "assistant"} or not content.strip():
                continue
            if history and history[-1]["role"] == role:
                history[-1]["content"] = (
                    f"{history[-1]['content']}\n\n{content}"
                ).strip()
                continue
            history.append({"role": role, "content": content})
        if history and history[-1]["role"] == "user":
            history[-1] = {"role": "user", "content": request_text}
        elif request_text:
            history.append({"role": "user", "content": request_text})
        return [
            {"role": "system", "content": _AI_ASSISTANT_SYSTEM_PROMPT},
            {
                "role": "system",
                "content": (
                    "Stable JellyRip AI profile. Use this as preference context, "
                    "not as evidence about the current session:\n"
                    f"{json.dumps(profile, indent=2)}"
                ),
            },
            {
                "role": "system",
                "content": (
                    "Pinned JellyRip workflow facts from the controller. Use this as the trusted "
                    "source for workflow state:\n"
                    f"{json.dumps(pinned_session_facts, indent=2)}"
                ),
            },
            *(
                [
                    {
                        "role": "system",
                        "content": (
                            "Rolling conversation memory for older sidebar turns. "
                            "Prefer the recent turns when they are more specific:\n"
                            f"{summary_text}"
                        ),
                    }
                ]
                if summary_text else []
            ),
            {
                "role": "system",
                "content": (
                    "Current JellyRip app snapshot. Use this as the trusted "
                    "source for current app-state facts, not as a limit on general movie or TV knowledge:\n"
                    f"{json.dumps(snapshot, indent=2)}"
                ),
            },
            *history,
        ]

    def _record_ai_chat_replay(
        self,
        phase: str,
        *,
        replay_id: str,
        title: str = "AI Assistant",
        backend: str = "",
        request_text: str = "",
        display_text: str = "",
        response_text: str = "",
        error_text: str = "",
        details: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        record = append_ai_chat_replay(
            phase,
            replay_id=replay_id,
            title=title,
            backend=backend,
            request_text=request_text,
            display_text=display_text,
            response_text=response_text,
            error_text=error_text,
            details=details,
        )
        if record is None:
            return None
        path = ai_chat_replay_path()
        self._last_ai_chat_replay_id = replay_id
        self._last_ai_chat_replay_path = path
        if not self.__dict__.get("_ai_chat_replay_path_announced", False):
            controller = self.__dict__.get("controller", None)
            log = getattr(controller, "log", None)
            if callable(log):
                log(f"[AI] Sidebar chat replay: {path}")
            self._ai_chat_replay_path_announced = True
        return record

    def _copy_text_to_clipboard(self, text: str) -> bool:
        value = str(text or "")
        if not value.strip():
            return False
        try:
            self.clipboard_clear()
            self.clipboard_append(value)
            self.update_idletasks()
            return True
        except Exception:
            return False

    def _format_ai_chat_transcript(self) -> str:
        role_labels = {
            "user": "You",
            "assistant": "Assistant",
            "system": "Note",
        }
        parts = []
        for item in list(self.__dict__.get("_ai_chat_history", [])):
            role = role_labels.get(str(item.get("role", "") or ""), "Assistant")
            content = str(item.get("content", "") or "").strip()
            if not content:
                continue
            parts.append(f"{role}\n{content}")
        return "\n\n".join(parts).strip()

    def _copy_ai_chat_text(self, text: str) -> None:
        copied = self._copy_text_to_clipboard(text)
        if hasattr(self, "ai_chat_status_var"):
            if copied:
                self.ai_chat_status_var.set("Copied")
            else:
                self.ai_chat_status_var.set("Nothing to copy")

    def _copy_ai_chat_transcript(self) -> None:
        self._copy_ai_chat_text(self._format_ai_chat_transcript())

    @staticmethod
    def _format_ai_chat_replay_value(value: object) -> str:
        if isinstance(value, (dict, list)):
            if not value:
                return "(empty)"
            return json.dumps(value, indent=2, ensure_ascii=False)
        text = str(value or "")
        return text if text.strip() else "(empty)"

    def _serialize_ai_chat_replay_bundle(self, bundle: dict[str, object] | None) -> str:
        return json.dumps(bundle or {}, indent=2, ensure_ascii=False, sort_keys=True)

    def _format_ai_chat_replay_bundle_text(
        self,
        bundle: dict[str, object] | None,
    ) -> str:
        bundle = bundle if isinstance(bundle, dict) else {}
        final_record = bundle.get("final_record")
        if not isinstance(final_record, dict):
            final_record = {}

        summary = [
            "Summary",
            f"Replay ID: {str(bundle.get('replay_id', '') or '(missing)')}",
            f"Title: {str(bundle.get('title', '') or 'AI Assistant')}",
            f"Status: {str(bundle.get('status', '') or 'request')}",
            f"Backend: {str(bundle.get('backend', '') or '(pending)')}",
            f"First Timestamp: {str(bundle.get('first_timestamp', '') or '(unknown)')}",
            f"Last Timestamp: {str(bundle.get('last_timestamp', '') or '(unknown)')}",
            f"Phases: {', '.join(bundle.get('phase_sequence', []) or []) or '(none)'}",
            f"Records: {int(bundle.get('line_count', 0) or 0)}",
            f"Final Phase: {str(final_record.get('phase', '') or '(none)')}",
        ]

        sections = [
            "\n".join(summary),
            f"Request Text\n{self._format_ai_chat_replay_value(bundle.get('request_text'))}",
            f"Display Text\n{self._format_ai_chat_replay_value(bundle.get('display_text'))}",
            f"Final Answer\n{self._format_ai_chat_replay_value(bundle.get('final_answer_text'))}",
            f"Final Error\n{self._format_ai_chat_replay_value(bundle.get('final_error_text'))}",
            f"AI Profile\n{self._format_ai_chat_replay_value(bundle.get('ai_profile'))}",
            f"Session Facts\n{self._format_ai_chat_replay_value(bundle.get('session_facts'))}",
            f"Payload\n{self._format_ai_chat_replay_value(bundle.get('payload'))}",
            f"Payload By Provider\n{self._format_ai_chat_replay_value(bundle.get('payload_by_provider'))}",
            f"Messages\n{self._format_ai_chat_replay_value(bundle.get('messages'))}",
            f"Messages By Provider\n{self._format_ai_chat_replay_value(bundle.get('messages_by_provider'))}",
        ]
        return "\n\n".join(sections).strip()

    def _get_selected_ai_chat_replay_bundle(self) -> dict[str, object] | None:
        tree = self.__dict__.get("_ai_chat_replay_tree")
        bundle_index = self.__dict__.get("_ai_chat_replay_index", {})
        if tree is None or not isinstance(bundle_index, dict):
            return None
        try:
            selection = list(tree.selection())
        except Exception:
            return None
        if not selection:
            return None
        return bundle_index.get(selection[0])

    def _update_ai_chat_replay_detail(self, _event=None) -> None:
        detail = self.__dict__.get("_ai_chat_replay_detail")
        status_var = self.__dict__.get("_ai_chat_replay_status_var")
        if detail is None:
            return

        bundle = self._get_selected_ai_chat_replay_bundle()
        if bundle is None:
            body_text = (
                "No replay bundle selected.\n\n"
                "Choose a replay on the left to inspect the bundled request and response."
            )
            if status_var is not None:
                status_var.set(f"No replay selected. Source: {ai_chat_replay_path()}")
        else:
            body_text = self._format_ai_chat_replay_bundle_text(bundle)
            if status_var is not None:
                status_var.set(
                    "Showing replay "
                    f"{bundle.get('replay_id', '(missing)')} from {ai_chat_replay_path()}"
                )

        try:
            detail.configure(state="normal")
            detail.delete("1.0", "end")
            detail.insert("1.0", body_text)
            detail.see("1.0")
            detail.configure(state="disabled")
        except Exception:
            pass

    def _refresh_ai_chat_replay_inspector(self) -> None:
        tree = self.__dict__.get("_ai_chat_replay_tree")
        detail = self.__dict__.get("_ai_chat_replay_detail")
        status_var = self.__dict__.get("_ai_chat_replay_status_var")
        if tree is None or detail is None:
            return

        try:
            previous_selection = list(tree.selection())
        except Exception:
            previous_selection = []

        try:
            tree.delete(*tree.get_children(""))
        except Exception:
            pass

        bundles = list_ai_chat_replay_bundles(limit=60)
        bundle_index: dict[str, dict[str, object]] = {}

        for bundle in bundles:
            replay_id = str(bundle.get("replay_id", "") or "")
            if not replay_id:
                continue
            bundle_index[replay_id] = bundle
            updated = str(bundle.get("last_timestamp", "") or "")
            status = str(bundle.get("status", "") or "request").title()
            backend = str(bundle.get("backend", "") or "").upper() or "-"
            request_text = str(
                bundle.get("display_text")
                or bundle.get("request_text")
                or ""
            )
            request_summary = self._trim_context_label(request_text, limit=72) or "(empty)"
            tree.insert(
                "",
                "end",
                iid=replay_id,
                values=(updated, status, backend, request_summary),
            )

        self._ai_chat_replay_index = bundle_index

        if not bundles:
            if status_var is not None:
                status_var.set(f"No replay entries found at {ai_chat_replay_path()}")
            self._update_ai_chat_replay_detail()
            return

        target = (
            previous_selection[0]
            if previous_selection and previous_selection[0] in bundle_index
            else str(bundles[0].get("replay_id", "") or "")
        )
        if target:
            try:
                tree.selection_set(target)
                tree.focus(target)
                tree.see(target)
            except Exception:
                pass

        self._update_ai_chat_replay_detail()

    def _copy_selected_ai_chat_replay(self) -> None:
        bundle = self._get_selected_ai_chat_replay_bundle()
        status_var = self.__dict__.get("_ai_chat_replay_status_var")
        if bundle is None:
            if status_var is not None:
                status_var.set("Choose a replay before copying.")
            return
        if self._copy_text_to_clipboard(self._serialize_ai_chat_replay_bundle(bundle)):
            if status_var is not None:
                status_var.set(
                    f"Copied replay {bundle.get('replay_id', '(missing)')}."
                )
        elif status_var is not None:
            status_var.set("Copy failed.")

    def _export_selected_ai_chat_replay(self) -> None:
        bundle = self._get_selected_ai_chat_replay_bundle()
        status_var = self.__dict__.get("_ai_chat_replay_status_var")
        if bundle is None:
            if status_var is not None:
                status_var.set("Choose a replay before exporting.")
            return

        replay_id = str(bundle.get("replay_id", "replay") or "replay")
        export_path = self.ask_save_file(
            "Export Replay",
            "Save the selected replay bundle as JSON",
            initialdir=get_config_dir(),
            initialfile=f"ai_chat_replay_{replay_id}.json",
            defaultextension=".json",
            filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
        )
        if not export_path:
            if status_var is not None:
                status_var.set("Export cancelled.")
            return

        try:
            with open(export_path, "w", encoding="utf-8") as handle:
                handle.write(self._serialize_ai_chat_replay_bundle(bundle))
                handle.write("\n")
        except Exception as exc:
            if status_var is not None:
                status_var.set(f"Export failed: {exc}")
            return

        controller = self.__dict__.get("controller", None)
        log = getattr(controller, "log", None)
        if callable(log):
            log(f"[AI] Exported sidebar chat replay bundle: {export_path}")
        if status_var is not None:
            status_var.set(f"Exported replay to {export_path}")

    def _open_ai_chat_replay_inspector(self) -> None:
        existing = self.__dict__.get("_ai_chat_replay_window")
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.deiconify()
                    existing.lift()
                    existing.focus_force()
                    self._refresh_ai_chat_replay_inspector()
                    return
            except Exception:
                pass

        colors = self._theme
        win = tk.Toplevel(self)
        win.title("AI Chat Replay Browser")
        win.configure(bg=colors["window_bg"])
        win.geometry("1180x760")
        win.transient(self)
        win.lift()
        win.focus_force()
        self._ai_chat_replay_window = win

        def _close() -> None:
            self._ai_chat_replay_window = None
            self._ai_chat_replay_tree = None
            self._ai_chat_replay_detail = None
            self._ai_chat_replay_status_var = None
            self._ai_chat_replay_index = {}
            try:
                win.destroy()
            except Exception:
                pass

        win.protocol("WM_DELETE_WINDOW", _close)

        tk.Label(
            win,
            text="AI Chat Replay Browser",
            bg=colors["window_bg"],
            fg=colors["title"],
            font=("Segoe UI", 14, "bold"),
            anchor="w",
        ).pack(fill="x", padx=16, pady=(14, 4))

        tk.Label(
            win,
            text=(
                "Read-only debug view of append-only sidebar chat replay bundles. "
                f"Source: {ai_chat_replay_path()}"
            ),
            bg=colors["window_bg"],
            fg=colors["muted"],
            font=("Segoe UI", 9),
            wraplength=1120,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=16, pady=(0, 10))

        action_row = tk.Frame(win, bg=colors["window_bg"])
        action_row.pack(fill="x", padx=16, pady=(0, 10))

        self._ai_chat_replay_status_var = tk.StringVar(value="")
        tk.Label(
            action_row,
            textvariable=self._ai_chat_replay_status_var,
            bg=colors["window_bg"],
            fg=colors["muted"],
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)

        action_button = {
            "bg": colors["toolbar_button"],
            "fg": colors["toolbar_button_text"],
            "activebackground": colors["toolbar_button_active"],
            "activeforeground": colors["toolbar_button_text"],
            "font": ("Segoe UI", 10, "bold"),
            "relief": "flat",
            "bd": 0,
            "padx": 12,
            "pady": 6,
            "cursor": "hand2",
        }

        tk.Button(
            action_row,
            text="Refresh",
            command=self._refresh_ai_chat_replay_inspector,
            **action_button,
        ).pack(side="right", padx=(6, 0))
        tk.Button(
            action_row,
            text="Export JSON",
            command=self._export_selected_ai_chat_replay,
            **action_button,
        ).pack(side="right", padx=(6, 0))
        tk.Button(
            action_row,
            text="Copy Replay",
            command=self._copy_selected_ai_chat_replay,
            **action_button,
        ).pack(side="right", padx=(6, 0))
        tk.Button(
            action_row,
            text="Close",
            command=_close,
            **action_button,
        ).pack(side="right", padx=(6, 0))

        panes = tk.PanedWindow(
            win,
            orient="horizontal",
            sashwidth=8,
            bd=0,
            relief="flat",
            bg=colors["window_bg"],
        )
        panes.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        list_frame = tk.Frame(
            panes,
            bg=colors["surface"],
            highlightbackground=colors["panel_border"],
            highlightthickness=1,
            bd=0,
        )
        detail_frame = tk.Frame(
            panes,
            bg=colors["surface"],
            highlightbackground=colors["panel_border"],
            highlightthickness=1,
            bd=0,
        )
        panes.add(list_frame, minsize=320)
        panes.add(detail_frame, minsize=520)

        tk.Label(
            list_frame,
            text="Replay Bundles",
            bg=colors["surface"],
            fg=colors["title"],
            font=("Segoe UI", 11, "bold"),
            anchor="w",
        ).pack(fill="x", padx=12, pady=(12, 8))

        style = ttk.Style(win)
        style.configure(
            "AIReplay.Treeview",
            background=colors["surface_alt"],
            foreground=colors["text"],
            fieldbackground=colors["surface_alt"],
            rowheight=24,
            font=("Consolas", 10),
        )
        style.configure(
            "AIReplay.Treeview.Heading",
            background=colors["surface"],
            foreground=colors["title"],
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "AIReplay.Treeview",
            background=[("selected", colors["blue"])],
            foreground=[("selected", colors["text"])],
        )

        tree_shell = tk.Frame(list_frame, bg=colors["surface"])
        tree_shell.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        tree = ttk.Treeview(
            tree_shell,
            columns=("updated", "status", "backend", "request"),
            show="headings",
            style="AIReplay.Treeview",
            selectmode="browse",
        )
        tree.heading("updated", text="Updated")
        tree.heading("status", text="Status")
        tree.heading("backend", text="Backend")
        tree.heading("request", text="Request")
        tree.column("updated", width=180, anchor="w", stretch=False)
        tree.column("status", width=80, anchor="center", stretch=False)
        tree.column("backend", width=90, anchor="center", stretch=False)
        tree.column("request", width=300, anchor="w", stretch=True)
        tree_scroll = ttk.Scrollbar(tree_shell, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=tree_scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
        tree.bind("<<TreeviewSelect>>", self._update_ai_chat_replay_detail)
        self._ai_chat_replay_tree = tree

        tk.Label(
            detail_frame,
            text="Selected Replay Bundle",
            bg=colors["surface"],
            fg=colors["title"],
            font=("Segoe UI", 11, "bold"),
            anchor="w",
        ).pack(fill="x", padx=12, pady=(12, 8))

        detail = scrolledtext.ScrolledText(
            detail_frame,
            bg=colors["surface_alt"],
            fg=colors["text"],
            font=("Consolas", 10),
            insertbackground=colors["text"],
            wrap="word",
            relief="flat",
            bd=0,
            padx=12,
            pady=12,
            state="disabled",
        )
        detail.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self._ai_chat_replay_detail = detail
        self._ai_chat_replay_index = {}

        self._refresh_ai_chat_replay_inspector()

    def _set_ai_chat_busy(self, busy: bool, status_text: str | None = None) -> None:
        self._ai_chat_busy = busy
        if hasattr(self, "ai_chat_send_btn"):
            self.ai_chat_send_btn.configure(state=("disabled" if busy else "normal"))
        if hasattr(self, "ai_chat_suggest_btn"):
            self.ai_chat_suggest_btn.configure(state=("disabled" if busy else "normal"))
        if hasattr(self, "ai_chat_status_var"):
            if status_text:
                self.ai_chat_status_var.set(status_text)
            else:
                self.ai_chat_status_var.set("Thinking..." if busy else "Ready")
        if busy:
            self._show_ai_chat_typing_indicator(status_text)
        else:
            self._hide_ai_chat_typing_indicator()

    def _append_ai_chat_message(
        self,
        role: str,
        text: str,
        backend_tag: str = "",
    ) -> None:
        container = self.__dict__.get("ai_chat_messages")
        if container is None:
            return
        if role in {"assistant", "system"}:
            self._hide_ai_chat_typing_indicator()
        message = str(text or "").strip() or "(empty)"
        colors = self._theme
        title_map = {
            "user": "You",
            "assistant": "Assistant",
            "system": "Note",
        }
        label = title_map.get(role, "Assistant")
        if backend_tag:
            label = f"{label} · {str(backend_tag).lower()}"

        bubble_styles = {
            "user": {
                "row_side": "right",
                "bg": colors["toolbar_button_active"],
                "border": colors["title"],
                "title_fg": colors["toolbar_button_text"],
                "text_fg": colors["text"],
                "padx": (70, 0),
            },
            "assistant": {
                "row_side": "left",
                "bg": colors["surface"],
                "border": colors["panel_border"],
                "title_fg": colors["title"],
                "text_fg": colors["text"],
                "padx": (0, 70),
            },
            "system": {
                "row_side": "left",
                "bg": colors["surface_deep"],
                "border": colors["pill_warn_border"],
                "title_fg": "#ffd166",
                "text_fg": colors["text"],
                "padx": (24, 24),
            },
        }
        style = bubble_styles.get(role, bubble_styles["assistant"])

        row = tk.Frame(container, bg=colors["surface_alt"])
        row.pack(fill="x", pady=(0, 10))

        bubble = tk.Frame(
            row,
            bg=style["bg"],
            highlightbackground=style["border"],
            highlightthickness=1,
            bd=0,
        )
        bubble.pack(side=style["row_side"], padx=style["padx"])

        header = tk.Frame(bubble, bg=style["bg"])
        header.pack(fill="x", padx=12, pady=(10, 4))

        tk.Label(
            header,
            text=label,
            bg=style["bg"],
            fg=style["title_fg"],
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)

        tk.Button(
            header,
            text="Copy",
            command=lambda value=message: self._copy_ai_chat_text(value),
            bg=style["bg"],
            fg=style["title_fg"],
            activebackground=style["bg"],
            activeforeground=style["title_fg"],
            disabledforeground=style["title_fg"],
            font=("Segoe UI", 8, "bold"),
            relief="flat",
            bd=0,
            padx=4,
            pady=0,
            cursor="hand2",
        ).pack(side="right")

        body = tk.Text(
            bubble,
            bg=style["bg"],
            fg=style["text_fg"],
            font=("Segoe UI", 11),
            relief="flat",
            bd=0,
            highlightthickness=0,
            wrap="word",
            padx=0,
            pady=0,
            insertwidth=0,
            takefocus=1,
            cursor="xterm",
            undo=False,
            exportselection=True,
            selectbackground=colors["title"],
            selectforeground=colors["text"],
            width=1,
            height=1,
        )
        body.insert("1.0", message)
        body.configure(state="disabled")
        body.pack(fill="x", padx=12, pady=(0, 10))
        self._ai_chat_body_labels.append(body)
        self.after_idle(
            lambda widget=body: self._fit_ai_chat_text_widget(widget)
        )
        self.after_idle(self._scroll_ai_chat_to_end)

    def push_ai_chat_message(
        self,
        role: str,
        text: str,
        *,
        backend_tag: str = "",
        open_sidebar: bool = False,
    ) -> None:
        safe_role = role if role in {"user", "assistant", "system"} else "assistant"
        message = str(text or "").strip()
        if not message:
            return

        def _push() -> None:
            history = getattr(self, "_ai_chat_history", None)
            if history is None:
                self._ai_chat_history = []
                history = self._ai_chat_history
            history.append({"role": safe_role, "content": message})
            self._remember_ai_chat_turn(safe_role, message)
            if open_sidebar:
                self._show_ai_sidebar()
            self._append_ai_chat_message(
                safe_role,
                message,
                backend_tag=backend_tag,
            )
            status_var = self.__dict__.get("ai_chat_status_var")
            if not getattr(self, "_ai_chat_busy", False) and status_var is not None:
                label = (
                    f"New message via {str(backend_tag).lower()}"
                    if backend_tag else "New message"
                )
                status_var.set(label)

        self._run_on_main(_push)

    def ask_ai_identity_choice(
        self,
        suggestion_text: str,
        *,
        backend_tag: str = "",
    ) -> str:
        if threading.current_thread() is threading.main_thread():
            return "edit"

        result = ["edit"]
        done = threading.Event()
        finish_holder = {"fn": None}
        timeout_seconds = self._get_user_prompt_timeout_seconds()
        start = time.time()

        def _show() -> None:
            self._show_ai_sidebar()
            self.push_ai_chat_message(
                "assistant",
                suggestion_text,
                backend_tag=backend_tag,
                open_sidebar=False,
            )

            container = self.__dict__.get("ai_chat_messages")
            if container is None:
                done.set()
                return

            colors = self._theme
            row = tk.Frame(container, bg=colors["surface_alt"])
            row.pack(fill="x", pady=(0, 10))

            bubble = tk.Frame(
                row,
                bg=colors["surface_deep"],
                highlightbackground=colors["pill_warn_border"],
                highlightthickness=1,
                bd=0,
            )
            bubble.pack(side="left", padx=(24, 24))

            tk.Label(
                bubble,
                text="Choose",
                bg=colors["surface_deep"],
                fg="#ffd166",
                font=("Segoe UI", 9, "bold"),
                anchor="w",
            ).pack(fill="x", padx=12, pady=(10, 4))

            tk.Label(
                bubble,
                text="Apply this suggestion to the identity fields, review manually, or cancel this rip.",
                bg=colors["surface_deep"],
                fg=colors["text"],
                font=("Segoe UI", 10),
                justify="left",
                anchor="w",
                wraplength=self._get_ai_chat_bubble_wraplength(),
            ).pack(fill="x", padx=12, pady=(0, 10))

            action_row = tk.Frame(bubble, bg=colors["surface_deep"])
            action_row.pack(fill="x", padx=12, pady=(0, 10))

            def finish(choice: str, note: str = "") -> None:
                if done.is_set():
                    return
                result[0] = choice
                try:
                    action_row.destroy()
                except Exception:
                    pass
                if note:
                    self._append_ai_chat_message("system", note)
                done.set()

            finish_holder["fn"] = finish

            tk.Button(
                action_row,
                text="Use Suggestion",
                command=lambda: finish(
                    "accept",
                    "Applying the assistant suggestion to the identity fields.",
                ),
                bg=colors["green"],
                fg=colors["text"],
                font=("Segoe UI", 9, "bold"),
                relief="flat",
                padx=8,
                pady=4,
                cursor="hand2",
            ).pack(side="left", padx=(0, 6))
            tk.Button(
                action_row,
                text="Edit",
                command=lambda: finish("edit", "Opening the identity editor."),
                bg=colors["blue"],
                fg=colors["text"],
                font=("Segoe UI", 9, "bold"),
                relief="flat",
                padx=8,
                pady=4,
                cursor="hand2",
            ).pack(side="left", padx=(0, 6))
            tk.Button(
                action_row,
                text="Cancel",
                command=lambda: finish("cancel", "Cancelled at assistant identity prompt."),
                bg=colors["abort"],
                fg=colors["text"],
                font=("Segoe UI", 9, "bold"),
                relief="flat",
                padx=8,
                pady=4,
                cursor="hand2",
            ).pack(side="left")

            self.after_idle(self._scroll_ai_chat_to_end)

            def _abort_watch() -> None:
                while not done.is_set():
                    if self.engine.abort_event.is_set():
                        self.after(0, lambda: finish("cancel"))
                        return
                    time.sleep(0.1)

            threading.Thread(target=_abort_watch, daemon=True).start()

        self.after(0, _show)
        while not done.wait(timeout=0.1):
            if self.engine.abort_event.is_set():
                return "cancel"
            if (
                timeout_seconds is not None
                and time.time() - start >= timeout_seconds
            ):
                if finish_holder["fn"] is not None:
                    self.after(
                        0,
                        lambda: finish_holder["fn"](
                            "edit",
                            "Identity prompt timed out. Opening the editor.",
                        ),
                    )
                    done.wait(timeout=1.0)
                return "edit"
        return result[0]

    def _reset_ai_chat(self) -> None:
        self._ai_chat_history.clear()
        self._ensure_ai_chat_memory().reset()
        self._hide_ai_chat_typing_indicator()
        self._ai_chat_body_labels = []
        container = self.__dict__.get("ai_chat_messages")
        if container is None:
            return
        for child in list(container.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass
        self._append_ai_chat_message(
            "assistant",
            "Ask about the current rip, ask a movie question, or use Suggest Next Step "
            "when you want me to reason from the current UI.",
        )
        if hasattr(self, "ai_chat_input"):
            try:
                self.ai_chat_input.delete("1.0", "end")
            except Exception:
                pass
        self._set_ai_chat_busy(False, "Ready")

    def _handle_ai_chat_return(self, event) -> str | None:
        state = int(getattr(event, "state", 0) or 0)
        shift_pressed = bool(state & 0x1)
        self._debug_ui_event(
            "ai_chat_return",
            event_state=state,
            shift_pressed=shift_pressed,
            prompt_len=self._current_ai_chat_prompt_len(),
        )
        if event.state & 0x1:
            return None
        self._submit_ai_chat()
        return "break"

    def _request_ai_response_async(
        self,
        title: str,
        user_text: str,
        system_prompt: str,
        max_tokens: int,
        on_success,
        on_error,
        on_status=None,
        log_request: bool = True,
        log_failures: bool = True,
        payload_by_provider: dict[str, str] | None = None,
        max_tokens_by_provider: dict[str, int] | None = None,
    ) -> None:
        text = str(user_text or "").strip()
        if not text:
            self.after(0, lambda: on_error("No request text provided."))
            return

        providers = self._resolve_ai_text_providers()
        if not providers:
            self.after(
                0,
                lambda: on_error(
                    "AI is off or no configured provider is available. "
                    "Use the AI mode toggle or Provider Setup first."
                ),
            )
            return

        if log_request:
            self.controller.log(f"[AI] {title} requested.")

        def worker() -> None:
            last_error = "No provider available."
            for tag, provider, timeout in providers:
                if provider is None:
                    continue
                try:
                    if on_status is not None:
                        self.after(0, lambda current=tag: on_status(current))
                    request_text = (
                        str(payload_by_provider.get(tag, text))
                        if payload_by_provider else text
                    )
                    request_max_tokens = (
                        int(max_tokens_by_provider.get(tag, max_tokens))
                        if max_tokens_by_provider else int(max_tokens)
                    )
                    result = provider.diagnose(
                        request_text,
                        system_prompt,
                        max_tokens=request_max_tokens,
                        timeout=float(timeout),
                    ).strip()
                    if log_request:
                        self.controller.log(f"[AI:{tag}] {title} complete.")
                    self.after(
                        0,
                        lambda response=result, backend=tag: on_success(response, backend),
                    )
                    return
                except Exception as e:
                    last_error = f"{tag}: {e}"
                    if log_failures:
                        self.controller.log(f"[AI:{tag}] {title} failed: {e}")
            self.after(0, lambda err=last_error: on_error(err))

        threading.Thread(target=worker, daemon=True).start()

    def _request_ai_chat_async(
        self,
        title: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        on_success,
        on_error,
        on_status=None,
        log_request: bool = True,
        log_failures: bool = True,
        messages_by_provider: dict[str, list[dict[str, str]]] | None = None,
        max_tokens_by_provider: dict[str, int] | None = None,
    ) -> None:
        if not list(messages or []):
            self.after(0, lambda: on_error("No chat messages provided."))
            return

        providers = self._resolve_ai_text_providers()
        if not providers:
            self.after(
                0,
                lambda: on_error(
                    "AI is off or no configured provider is available. "
                    "Use the AI mode toggle or Provider Setup first."
                ),
            )
            return

        if log_request:
            self.controller.log(f"[AI] {title} requested.")

        def worker() -> None:
            last_error = "No provider available."
            for tag, provider, timeout in providers:
                if provider is None:
                    continue
                try:
                    if on_status is not None:
                        self.after(0, lambda current=tag: on_status(current))
                    request_messages = list(
                        messages_by_provider.get(tag, messages)
                        if messages_by_provider else messages
                    )
                    request_max_tokens = (
                        int(max_tokens_by_provider.get(tag, max_tokens))
                        if max_tokens_by_provider else int(max_tokens)
                    )
                    result = provider.chat(
                        request_messages,
                        max_tokens=request_max_tokens,
                        timeout=float(timeout),
                    ).strip()
                    if log_request:
                        self.controller.log(f"[AI:{tag}] {title} complete.")
                    self.after(
                        0,
                        lambda response=result, backend=tag: on_success(response, backend),
                    )
                    return
                except Exception as e:
                    last_error = f"{tag}: {e}"
                    if log_failures:
                        self.controller.log(f"[AI:{tag}] {title} failed: {e}")
            self.after(0, lambda err=last_error: on_error(err))

        threading.Thread(target=worker, daemon=True).start()

    def _start_ai_chat_request(self, prompt: str, display_text: str | None = None) -> None:
        request_text = str(prompt or "").strip()
        if not request_text or self._ai_chat_busy:
            return
        if not self._ensure_ai_profile_onboarded():
            return

        shown_text = str(display_text or request_text).strip()
        replay_id = uuid.uuid4().hex
        self._ai_chat_history.append({"role": "user", "content": shown_text})
        self._remember_ai_chat_turn("user", shown_text)
        self._append_ai_chat_message("user", shown_text)
        self._set_ai_chat_busy(True, "Thinking...")

        if _prompt_looks_like_ui_help(request_text):
            fallback_payload = self._build_ai_sidebar_context_payload(
                request_text,
                max_log_lines=20,
                max_log_chars=2200,
            )
            self._record_ai_chat_replay(
                "request",
                replay_id=replay_id,
                request_text=request_text,
                display_text=shown_text,
                details={
                    "mode": "app_ui_help",
                    "payload": fallback_payload,
                    "compaction_trace": list(fallback_payload.get("compaction_trace", [])),
                },
            )
            snapshot = (
                fallback_payload.get("ui_snapshot", {})
                if isinstance(fallback_payload, dict)
                else {}
            )
            answer = _build_ui_help_fallback(
                snapshot,
                str(snapshot.get("live_log_tail", "")),
            )
            self._record_ai_chat_replay(
                "response",
                replay_id=replay_id,
                backend="app",
                request_text=request_text,
                display_text=shown_text,
                response_text=answer,
                details={
                    "mode": "app_ui_help",
                    "compaction_trace": list(fallback_payload.get("compaction_trace", [])),
                },
            )
            self._ai_chat_history.append({"role": "assistant", "content": answer})
            self._remember_ai_chat_turn("assistant", answer)
            self._append_ai_chat_message("assistant", answer, backend_tag="app")
            self._set_ai_chat_busy(False, "Ready via app")
            if self._ai_sidebar_visible and hasattr(self, "ai_chat_input"):
                self.ai_chat_input.focus_set()
            return

        full_payload = self._build_ai_sidebar_context_payload(request_text)
        full_messages = self._build_ai_sidebar_chat_messages_from_payload(full_payload)
        local_payload = self._build_ai_sidebar_context_payload(
            request_text,
            max_history=4,
            max_log_lines=20,
            max_log_chars=2200,
        )
        local_messages = self._build_ai_sidebar_chat_messages_from_payload(local_payload)
        self._record_ai_chat_replay(
            "request",
            replay_id=replay_id,
            request_text=request_text,
            display_text=shown_text,
            details={
                "mode": "provider_chat",
                "ai_profile": full_payload.get("ai_profile", {}),
                "session_facts": full_payload.get("session_facts", {}),
                "conversation_summary": full_payload.get("conversation_summary", ""),
                "pinned_session_facts": full_payload.get("pinned_session_facts", {}),
                "compaction_trace": list(full_payload.get("compaction_trace", [])),
                "payload": full_payload,
                "payload_by_provider": {"LOCAL": local_payload},
                "messages": full_messages,
                "messages_by_provider": {"LOCAL": local_messages},
                "max_tokens": 900,
                "max_tokens_by_provider": {"LOCAL": 420},
            },
        )

        def _on_success(result_text: str, backend_tag: str) -> None:
            raw_answer = result_text.strip() or "(no response)"
            answer = raw_answer
            replay_backend = backend_tag
            replay_details: dict[str, object] = {
                "mode": "provider_chat",
                "raw_response_text": raw_answer,
            }
            if _looks_like_ai_payload_echo(answer):
                snapshot = (
                    local_payload.get("ui_snapshot", {})
                    if isinstance(local_payload, dict)
                    else {}
                )
                answer = _build_ui_help_fallback(
                    snapshot,
                    str(snapshot.get("live_log_tail", "")),
                    "AI returned raw request payload.",
                )
                replay_backend = "app"
                replay_details["fallback_reason"] = "payload_echo"
                replay_details["source_backend"] = backend_tag
                replay_details["fallback_payload"] = local_payload
            self._ai_chat_history.append({"role": "assistant", "content": answer})
            self._remember_ai_chat_turn("assistant", answer)
            memory_payload = self._build_ai_sidebar_context_payload(
                request_text,
                max_history=8,
                max_log_lines=20,
                max_log_chars=2200,
            )
            replay_details["conversation_summary"] = memory_payload.get(
                "conversation_summary",
                "",
            )
            replay_details["pinned_session_facts"] = memory_payload.get(
                "pinned_session_facts",
                {},
            )
            replay_details["compaction_trace"] = list(
                memory_payload.get("compaction_trace", [])
            )
            self._record_ai_chat_replay(
                "response",
                replay_id=replay_id,
                backend=replay_backend,
                request_text=request_text,
                display_text=shown_text,
                response_text=answer,
                details=replay_details,
            )
            self._append_ai_chat_message("assistant", answer, backend_tag=replay_backend)
            self._set_ai_chat_busy(False, f"Ready via {replay_backend.lower()}")
            if self._ai_sidebar_visible and hasattr(self, "ai_chat_input"):
                self.ai_chat_input.focus_set()

        def _on_error(message: str) -> None:
            friendly = _friendly_ai_chat_error(message)
            self._record_ai_chat_replay(
                "error",
                replay_id=replay_id,
                request_text=request_text,
                display_text=shown_text,
                error_text=message,
                details={
                    "mode": "provider_chat",
                    "friendly_error": friendly,
                    "conversation_summary": full_payload.get("conversation_summary", ""),
                    "pinned_session_facts": full_payload.get("pinned_session_facts", {}),
                    "compaction_trace": list(full_payload.get("compaction_trace", [])),
                },
            )
            self._append_ai_chat_message("system", friendly)
            if _prompt_looks_like_ui_help(request_text):
                snapshot = self._get_ai_sidebar_snapshot(
                    max_log_lines=20,
                    max_log_chars=2200,
                )
                fallback = _build_ui_help_fallback(
                    snapshot,
                    str(snapshot.get("live_log_tail", "")),
                    message,
                )
                self._ai_chat_history.append({"role": "assistant", "content": fallback})
                self._remember_ai_chat_turn("assistant", fallback)
                self._append_ai_chat_message("assistant", fallback, backend_tag="fallback")
                self._set_ai_chat_busy(False, "Ready with fallback")
            else:
                self._set_ai_chat_busy(False, "Unavailable")
            if self._ai_sidebar_visible and hasattr(self, "ai_chat_input"):
                self.ai_chat_input.focus_set()

        self._request_ai_chat_async(
            title="AI Assistant",
            messages=full_messages,
            max_tokens=900,
            on_success=_on_success,
            on_error=_on_error,
            on_status=lambda current: self._set_ai_chat_busy(
                True, f"Thinking with {current.lower()}..."
            ),
            log_request=False,
            log_failures=False,
            messages_by_provider={"LOCAL": local_messages},
            max_tokens_by_provider={"LOCAL": 420},
        )

    def _submit_ai_chat(self) -> None:
        if not hasattr(self, "ai_chat_input"):
            return
        prompt = self.ai_chat_input.get("1.0", "end-1c").strip()
        self._debug_ui_event(
            "ai_chat_submit",
            prompt_len=len(prompt),
        )
        if not prompt:
            self._debug_ui_event("ai_chat_submit_empty")
            self.ai_chat_input.focus_set()
            return
        if not self._ensure_ai_profile_onboarded():
            self._debug_ui_event(
                "ai_chat_submit_blocked_onboarding",
                prompt_len=len(prompt),
            )
            return
        self.ai_chat_input.delete("1.0", "end")
        self._debug_ui_event(
            "ai_chat_submit_dispatch",
            prompt_len=len(prompt),
        )
        self._start_ai_chat_request(prompt)

    def _request_ai_sidebar_suggestion(self) -> None:
        self._start_ai_chat_request(
            (
                "Look at the current UI snapshot and recent live rip log. "
                "Suggest the most useful next steps or checks right now. "
                "Call out anything that looks healthy, anything that looks risky, "
                "and what the user should do next."
            ),
            "Suggest what to do next from the current UI and live log.",
        )

    def _resolve_ai_text_providers(self) -> list[tuple[str, object, float]]:
        mode = str(self.cfg.get("opt_ai_mode", "cloud"))
        if mode == "off":
            return []
        try:
            from shared.ai.provider_registry import (
                resolve_active_cloud_provider,
                resolve_local_provider,
            )
        except Exception:
            return []

        cloud = None
        local = None

        try:
            if bool(self.cfg.get("opt_ai_cloud_enabled", True)):
                cloud = resolve_active_cloud_provider()
                if cloud and not cloud.is_available():
                    cloud = None
        except Exception:
            cloud = None

        try:
            if bool(self.cfg.get("opt_ai_local_enabled", True)):
                local = resolve_local_provider()
                if local and not local.is_available():
                    local = None
        except Exception:
            local = None

        cloud_timeout = float(self.cfg.get("opt_ai_cloud_timeout_seconds", 30))
        local_timeout = max(float(self.cfg.get("opt_ai_local_timeout_seconds", 90)), 5.0)

        if mode == "local":
            return [("LOCAL", local, local_timeout)] if local else []

        providers: list[tuple[str, object, float]] = []
        if cloud:
            providers.append(("CLOUD", cloud, cloud_timeout))
        if local:
            providers.append(("LOCAL", local, local_timeout))
        return providers

    def _run_ai_text_request(
        self,
        title: str,
        user_text: str,
        system_prompt: str,
        max_tokens: int = 700,
        replace_target: tuple[tk.Misc, dict[str, object]] | None = None,
    ) -> None:
        text = str(user_text or "").strip()
        if not text:
            self.show_info("AI", "Select some text first.")
            return

        providers = self._resolve_ai_text_providers()
        if not providers:
            self.show_info(
                "AI Unavailable",
                "AI is off or no configured provider is available.\n\n"
                "Use the AI mode toggle or Provider Setup first.",
            )
            return

        progress = tk.Toplevel(self)
        progress.title(title)
        progress.configure(bg=self._theme["surface"])
        progress.resizable(False, False)
        progress.transient(self)
        progress.grab_set()
        progress.lift()
        progress.focus_force()

        tk.Label(
            progress,
            text=title,
            bg=self._theme["surface"],
            fg=self._theme["title"],
            font=("Segoe UI", 12, "bold"),
        ).pack(padx=20, pady=(16, 6))
        status_var = tk.StringVar(value="Contacting AI...")
        tk.Label(
            progress,
            textvariable=status_var,
            bg=self._theme["surface"],
            fg=self._theme["text"],
            font=("Segoe UI", 10),
            wraplength=420,
            justify="left",
        ).pack(padx=20, pady=(0, 14))

        self.controller.log(f"[AI] {title} requested.")

        def _close_progress() -> None:
            try:
                progress.grab_release()
            except Exception:
                pass
            try:
                progress.destroy()
            except Exception:
                pass

        def _show_result(result_text: str, backend_tag: str) -> None:
            _close_progress()
            self._show_ai_result_dialog(
                title=title,
                result_text=result_text,
                backend_tag=backend_tag,
                replace_target=replace_target,
            )

        def _show_error(message: str) -> None:
            _close_progress()
            self.show_error("AI Request Failed", message)
        self._request_ai_response_async(
            title=title,
            user_text=text,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            on_success=_show_result,
            on_error=_show_error,
            on_status=lambda current: status_var.set(f"Contacting {current} backend..."),
            log_request=True,
            log_failures=True,
        )

    def _replace_widget_text(
        self,
        widget: tk.Misc,
        selection_ctx: dict[str, object],
        new_text: str,
    ) -> None:
        if not self._is_text_widget_editable(widget):
            return
        try:
            widget.focus_set()
            kind = selection_ctx.get("kind")
            if kind == "entry":
                start = int(selection_ctx["start"])
                end = int(selection_ctx["end"])
                widget.delete(start, end)
                widget.insert(start, new_text)
            elif kind == "text":
                start = str(selection_ctx["start"])
                end = str(selection_ctx["end"])
                widget.delete(start, end)
                widget.insert(start, new_text)
        except Exception as e:
            self.show_error("Replace Failed", str(e))

    def _show_ai_result_dialog(
        self,
        title: str,
        result_text: str,
        backend_tag: str,
        replace_target: tuple[tk.Misc, dict[str, object]] | None = None,
    ) -> None:
        colors = self._theme
        win = tk.Toplevel(self)
        win.title(title)
        win.configure(bg=colors["window_bg"])
        win.geometry("760x520")
        win.transient(self)
        win.grab_set()
        win.lift()
        win.focus_force()

        tk.Label(
            win,
            text=f"{title} ({backend_tag})",
            bg=colors["window_bg"],
            fg=colors["title"],
            font=("Segoe UI", 12, "bold"),
        ).pack(padx=16, pady=(14, 6), anchor="w")

        body = scrolledtext.ScrolledText(
            win,
            bg=colors["surface_alt"],
            fg=colors["text"],
            font=("Consolas", 10),
            insertbackground=colors["text"],
            wrap="word",
        )
        body.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        body.insert("1.0", result_text)
        body.config(state="disabled")

        btn_row = tk.Frame(win, bg=colors["window_bg"])
        btn_row.pack(fill="x", padx=16, pady=(0, 14))

        tk.Button(
            btn_row,
            text="Copy",
            command=lambda: (self.clipboard_clear(), self.clipboard_append(result_text)),
            bg=colors["toolbar_button"],
            fg=colors["toolbar_button_text"],
            font=("Segoe UI", 10),
            relief="flat",
        ).pack(side="left", padx=(0, 6))

        if replace_target is not None:
            widget, selection_ctx = replace_target
            tk.Button(
                btn_row,
                text="Replace Selection",
                command=lambda w=widget, ctx=selection_ctx: (
                    self._replace_widget_text(w, ctx, result_text),
                    win.destroy(),
                ),
                bg=colors["green"],
                fg=colors["text"],
                font=("Segoe UI", 10, "bold"),
                relief="flat",
            ).pack(side="left", padx=(0, 6))

        tk.Button(
            btn_row,
            text="Close",
            command=win.destroy,
            bg=colors["toolbar_button"],
            fg=colors["toolbar_button_text"],
            font=("Segoe UI", 10),
            relief="flat",
        ).pack(side="right")

    def _run_ai_text_action(
        self,
        widget: tk.Misc,
        action: str,
    ) -> None:
        selection = self._get_text_widget_selection(widget)
        if not selection:
            self.show_info("AI", "Select some text first.")
            return

        replace_target = None
        selection_ctx = self._capture_widget_selection_context(widget)
        if selection_ctx and self._is_text_widget_editable(widget) and action in {"fix", "rewrite"}:
            replace_target = (widget, selection_ctx)

        if action == "fix":
            self._run_ai_text_request(
                title="AI Spell Check",
                user_text=selection,
                system_prompt=(
                    "You are an editing assistant inside JellyRip. "
                    "Correct spelling, grammar, punctuation, and obvious typos "
                    "while preserving meaning, tone, names, and formatting. "
                    "Return only the corrected text."
                ),
                max_tokens=700,
                replace_target=replace_target,
            )
            return

        if action == "rewrite":
            self._run_ai_text_request(
                title="AI Rewrite",
                user_text=selection,
                system_prompt=(
                    "You are a concise writing assistant inside JellyRip. "
                    "Rewrite the text to be clearer and smoother while preserving "
                    "the meaning and tone. Return only the rewritten text."
                ),
                max_tokens=700,
                replace_target=replace_target,
            )
            return

        if action == "explain":
            self._run_ai_text_request(
                title="AI Explain",
                user_text=selection,
                system_prompt=(
                    "You are a helpful assistant inside JellyRip. "
                    "Explain the selected text in plain English. "
                    "Be concise and useful."
                ),
                max_tokens=600,
            )
            return

        if action == "search":
            self._run_ai_text_request(
                title="AI Search",
                user_text=selection,
                system_prompt=(
                    "You are a search-style assistant inside JellyRip. "
                    "Given the selected text, answer the likely intent directly. "
                    "If it is a phrase, explain it. If it is a question, answer it. "
                    "If it names a concept, summarize the key facts. "
                    "Be concise and practical."
                ),
                max_tokens=700,
            )
            return

    def _show_text_context_menu(self, event) -> str:
        widget = event.widget
        if not isinstance(widget, (tk.Entry, ttk.Entry, ttk.Combobox, tk.Text)):
            return ""

        self._text_context_widget = widget
        try:
            widget.focus_set()
        except Exception:
            pass

        if isinstance(widget, (tk.Entry, ttk.Entry, ttk.Combobox)):
            try:
                widget.icursor(f"@{event.x}")
            except Exception:
                pass
        elif isinstance(widget, tk.Text):
            try:
                widget.mark_set("insert", f"@{event.x},{event.y}")
            except Exception:
                pass

        selection = self._get_text_widget_selection(widget)
        can_copy = bool(selection)
        can_edit = self._is_text_widget_editable(widget)
        has_content = self._text_widget_has_content(widget)

        menu = tk.Menu(self, tearoff=0)
        menu.add_command(
            label="Cut",
            command=lambda w=widget: self._cut_from_widget(w),
            state=("normal" if can_copy and can_edit else "disabled"),
        )
        menu.add_command(
            label="Copy",
            command=lambda w=widget: self._copy_from_widget(w),
            state=("normal" if can_copy else "disabled"),
        )
        menu.add_command(
            label="Paste",
            command=lambda w=widget: self._paste_into_widget(w),
            state=("normal" if can_edit else "disabled"),
        )
        menu.add_command(
            label="Delete",
            command=lambda w=widget: self._delete_widget_selection(w),
            state=("normal" if can_copy and can_edit else "disabled"),
        )
        menu.add_separator()
        menu.add_command(
            label="Select All",
            command=lambda w=widget: self._select_all_in_widget(w),
            state=("normal" if has_content else "disabled"),
        )
        if can_copy:
            label_text = self._trim_context_label(selection)
            menu.add_separator()
            menu.add_command(
                label=f'Search Google for "{label_text}"',
                command=lambda text=selection: webbrowser.open_new_tab(
                    f"https://www.google.com/search?q={quote_plus(text)}"
                ),
            )
            menu.add_command(
                label=f'Search with AI for "{label_text}"',
                command=lambda w=widget: self._run_ai_text_action(w, "search"),
            )
            menu.add_separator()
            menu.add_command(
                label="Fix Spelling & Grammar with AI",
                command=lambda w=widget: self._run_ai_text_action(w, "fix"),
            )
            menu.add_command(
                label="Rewrite More Clearly with AI",
                command=lambda w=widget: self._run_ai_text_action(w, "rewrite"),
            )
            menu.add_command(
                label="Explain Selection with AI",
                command=lambda w=widget: self._run_ai_text_action(w, "explain"),
            )

        self._text_context_menu = menu
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _build_interface_v2(self):
        colors = self._theme
        bg = colors["window_bg"]
        self.configure(bg=bg)
        self._configure_main_styles()

        toolbar_button = {
            "bg": colors["toolbar_button"],
            "fg": colors["toolbar_button_muted"],
            "activebackground": colors["toolbar_button_active"],
            "activeforeground": colors["toolbar_button_text"],
            "disabledforeground": colors["muted"],
            "font": ("Segoe UI", 14, "bold"),
            "relief": "flat",
            "bd": 0,
            "highlightthickness": 1,
            "highlightbackground": colors["panel_border"],
            "highlightcolor": colors["panel_border"],
            "padx": 14,
            "pady": 10,
            "cursor": "hand2",
        }
        tile_button = {
            "fg": colors["text"],
            "activeforeground": colors["text"],
            "font": ("Segoe UI", 21, "bold"),
            "relief": "flat",
            "bd": 0,
            "highlightthickness": 0,
            "padx": 18,
            "pady": 18,
            "justify": "center",
            "anchor": "center",
            "cursor": "hand2",
        }

        def make_tile(parent, row, column, text, mode, color, command, *, columnspan=1, wraplength=360):
            tile = tk.Frame(
                parent,
                bg=color,
                highlightbackground=color,
                highlightthickness=1,
                bd=0,
            )
            tile.grid(
                row=row,
                column=column,
                columnspan=columnspan,
                sticky="nsew",
                padx=8,
                pady=8,
            )
            btn = tk.Button(
                tile,
                text=text,
                command=command,
                bg=color,
                activebackground=color,
                wraplength=wraplength,
                disabledforeground=colors["text"],
                **tile_button,
            )
            btn.pack(fill="both", expand=True)
            self._bind_debug_focus_trace(btn, f"mode_button:{mode}")
            self.mode_buttons[mode] = btn
            return btn

        drive_frame = tk.Frame(self, bg=bg)
        drive_frame.pack(fill="x", padx=36, pady=(14, 0))
        drive_frame.grid_columnconfigure(1, weight=1)

        tk.Label(
            drive_frame,
            text="Drive:",
            bg=bg,
            fg=colors["muted"],
            font=("Segoe UI", 17),
        ).grid(row=0, column=0, sticky="w", padx=(0, 18))

        self.drive_var = tk.StringVar(value="Loading drives...")
        self.drive_options = [make_default_drive()]
        self.drive_menu = ttk.Combobox(
            drive_frame,
            textvariable=self.drive_var,
            values=["Loading drives..."],
            state="readonly",
            style="JellyRipMain.TCombobox",
            font=("Segoe UI", 15, "bold"),
        )
        self.drive_menu.bind("<<ComboboxSelected>>", self._on_drive_select)
        self.drive_menu.grid(row=0, column=1, sticky="ew", ipady=4)

        tk.Button(
            drive_frame,
            text="Refresh",
            command=self._refresh_drives,
            **toolbar_button,
        ).grid(row=0, column=2, sticky="ns", padx=(18, 0))

        util_frame = tk.Frame(
            self,
            bg=colors["surface"],
            highlightbackground=colors["panel_border"],
            highlightthickness=1,
            bd=0,
        )
        util_frame.pack(fill="x", padx=36, pady=(10, 0))

        ai_frame = tk.Frame(util_frame, bg=colors["surface"])
        ai_frame.pack(side="left", fill="x", padx=14, pady=10)
        tk.Label(
            ai_frame,
            text="AI:",
            bg=colors["surface"],
            fg=colors["muted"],
            font=("Segoe UI", 14),
        ).pack(side="left", padx=(0, 14))

        self._ai_mode_var = tk.StringVar(value=str(self.cfg.get("opt_ai_mode", "cloud")))
        self._ai_mode_buttons = {}
        for mode_value, mode_label in [("off", "OFF"), ("cloud", "CLOUD"), ("local", "LOCAL")]:
            btn = tk.Button(
                ai_frame,
                text=mode_label,
                command=lambda m=mode_value: self._set_ai_mode(m),
                bg=colors["surface"],
                fg=colors["muted"],
                activebackground=colors["toolbar_button_active"],
                activeforeground=colors["toolbar_button_text"],
                disabledforeground=colors["muted"],
                font=("Segoe UI", 15, "bold"),
                relief="flat",
                bd=0,
                highlightthickness=0,
                padx=12,
                pady=6,
                cursor="hand2",
            )
            btn.pack(side="left", padx=(0, 6))
            self._ai_mode_buttons[mode_value] = btn

        self._ai_status_pill = tk.Frame(
            ai_frame,
            bg=colors["pill_active_bg"],
            highlightbackground=colors["pill_active_border"],
            highlightthickness=1,
            bd=0,
        )
        self._ai_status_pill.pack(side="left", padx=(18, 8))
        self._ai_status_label = tk.Label(
            self._ai_status_pill,
            text="",
            bg=colors["pill_active_bg"],
            fg=colors["pill_active_border"],
            font=("Segoe UI", 14, "bold"),
            padx=14,
            pady=4,
        )
        self._ai_status_label.pack()

        tk.Button(
            ai_frame,
            text="Setup",
            command=self._open_ai_providers,
            bg=colors["surface"],
            fg=colors["muted"],
            activebackground=colors["toolbar_button_active"],
            activeforeground=colors["toolbar_button_text"],
            disabledforeground=colors["muted"],
            font=("Segoe UI", 14, "bold"),
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=8,
            pady=8,
            cursor="hand2",
        ).pack(side="left", padx=(2, 0))

        toolbar_actions = tk.Frame(util_frame, bg=colors["surface"])
        toolbar_actions.pack(
            side="right",
            fill="x",
            expand=True,
            padx=14,
            pady=10,
        )
        for col in range(6):
            toolbar_actions.grid_columnconfigure(col, weight=1, uniform="toolbar")

        self.ai_chat_toggle_btn = tk.Button(
            toolbar_actions,
            text="Assistant",
            command=self._toggle_ai_sidebar,
            **toolbar_button,
        )
        self.ai_chat_toggle_btn.grid(row=0, column=0, sticky="ew", padx=6)

        self.settings_btn = tk.Button(
            toolbar_actions,
            text="Settings",
            command=self._open_settings_safe,
            **toolbar_button,
        )
        self.settings_btn.grid(row=0, column=1, sticky="ew", padx=6)

        self.update_btn = tk.Button(
            toolbar_actions,
            text="Updates",
            command=self.check_for_updates,
            **toolbar_button,
        )
        self.update_btn.grid(row=0, column=2, sticky="ew", padx=6)

        self.log_btn = tk.Button(
            toolbar_actions,
            text="Log",
            command=self._focus_live_log,
            **toolbar_button,
        )
        self.log_btn.grid(row=0, column=3, sticky="ew", padx=6)

        self.copy_log_btn = tk.Button(
            toolbar_actions,
            text="Copy Log",
            command=self.copy_log_to_clipboard,
            **toolbar_button,
        )
        self.copy_log_btn.grid(row=0, column=4, sticky="ew", padx=6)

        self.browse_btn = tk.Button(
            toolbar_actions,
            text="Browse",
            command=self._browse_folder_in_explorer,
            **toolbar_button,
        )
        self.browse_btn.grid(row=0, column=5, sticky="ew", padx=6)

        self._update_ai_mode_ui()

        action_frame = tk.Frame(self, bg=bg)
        action_frame.pack(fill="x", padx=36, pady=(12, 0))
        self._ai_sidebar_overlay_anchor = action_frame
        for col in range(3):
            action_frame.grid_columnconfigure(col, weight=1, uniform="action")
        action_frame.grid_rowconfigure(0, minsize=88)
        action_frame.grid_rowconfigure(1, minsize=88)

        self.mode_buttons = {}
        make_tile(action_frame, 0, 0, "Rip TV Show Disc", "t", colors["green"], lambda: self._start_task_from_ui("t", source="tile"))
        make_tile(action_frame, 0, 1, "Rip Movie Disc", "m", colors["teal"], lambda: self._start_task_from_ui("m", source="tile"))
        make_tile(action_frame, 0, 2, "Dump All Titles", "d", colors["blue"], lambda: self._start_task_from_ui("d", source="tile"))
        make_tile(
            action_frame,
            1,
            0,
            "Organize Existing MKVs",
            "i",
            colors["purple"],
            lambda: self._start_task_from_ui("i", source="tile"),
            columnspan=2,
            wraplength=720,
        )
        make_tile(
            action_frame,
            1,
            2,
            "Prep for FFmpeg or HandBrake",
            "scan",
            colors["orange"],
            self._open_folder_scanner,
            wraplength=320,
        )

        self._bottom_dock = tk.Frame(self, bg=bg)
        self._bottom_dock.pack(side="bottom", fill="x")

        self.content_frame = tk.Frame(self, bg=bg)
        self.content_frame.pack(fill="both", expand=True, padx=36, pady=(10, 0))

        self.content_pane = tk.PanedWindow(
            self.content_frame,
            orient="horizontal",
            bg=colors["sash"],
            sashwidth=10,
            sashrelief="flat",
            relief="flat",
            bd=0,
            opaqueresize=True,
            showhandle=False,
        )
        self.content_pane.pack(fill="both", expand=True)
        self.content_pane.bind("<Configure>", self._on_content_pane_configure)
        self.bind("<Configure>", self._on_window_configure, add="+")

        self.log_panel = tk.Frame(self.content_pane, bg=bg)
        self.content_pane.add(self.log_panel, minsize=self._log_panel_min_width)

        status_strip = tk.Frame(self.log_panel, bg=bg)
        status_strip.pack(fill="x", pady=(0, 6))
        for col in range(4):
            status_strip.grid_columnconfigure(col, weight=1, uniform="status")
        self.status_strip = status_strip

        self.status_brand = tk.Frame(
            status_strip,
            bg=colors["header_bg"],
            highlightbackground=colors["header_border"],
            highlightthickness=1,
            bd=0,
        )
        self.status_brand.grid(row=0, column=0, columnspan=3, sticky="ew", padx=(0, 12))
        self.status_brand_inner = tk.Frame(self.status_brand, bg=colors["header_bg"], bd=0)
        self.status_brand_inner.pack(expand=True, pady=0)
        for text in ("Raw", "Jelly", "Ripper"):
            tk.Label(
                self.status_brand_inner,
                text=text,
                bg=colors["header_bg"],
                fg=colors["title"],
                font=("Segoe UI", 24, "bold"),
                anchor="center",
                justify="center",
                padx=22,
                pady=8,
                bd=0,
            ).pack(side="left")

        self.status_indicator = tk.Frame(
            status_strip,
            bg=colors["pill_idle_bg"],
            highlightbackground=colors["pill_idle_border"],
            highlightthickness=1,
            bd=0,
        )
        self.status_indicator.grid(row=0, column=3, sticky="ew")

        self.status_var = tk.StringVar(value="Ready")
        self.status_value_label = tk.Label(
            self.status_indicator,
            textvariable=self.status_var,
            bg=colors["pill_idle_bg"],
            fg=colors["ready_text"],
            font=("Segoe UI", 22, "italic"),
            anchor="center",
            justify="center",
            padx=14,
            pady=8,
            bd=0,
        )
        self.status_value_label.pack(fill="both", expand=True)
        self._apply_main_status_style(self.status_var.get())

        self.progress_shell = tk.Frame(status_strip, bg=bg)
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(
            self.progress_shell,
            variable=self.progress_var,
            maximum=100,
            mode="determinate",
            style="JellyRipMain.Horizontal.TProgressbar",
        )
        self.progress_bar.pack(fill="x", expand=True)

        log_card = tk.Frame(
            self.log_panel,
            bg=colors["surface_alt"],
            highlightbackground=colors["panel_border"],
            highlightthickness=1,
            bd=0,
        )
        log_card.pack(fill="both", expand=True)

        log_header = tk.Frame(log_card, bg=colors["surface_alt"], bd=0)
        log_header.pack(fill="x")
        tk.Label(
            log_header,
            text="Live Log",
            bg=colors["surface_alt"],
            fg=colors["text"],
            font=("Segoe UI", 15, "bold"),
        ).pack(anchor="w", padx=18, pady=(10, 8))

        tk.Frame(log_card, bg=colors["panel_border"], height=1).pack(fill="x")

        self.log_text = scrolledtext.ScrolledText(
            log_card,
            height=18,
            bg=colors["log_bg"],
            fg=colors["log_text"],
            font=("Consolas", 13),
            insertbackground=colors["text"],
            wrap="word",
            relief="flat",
            bd=0,
            padx=14,
            pady=12,
            state="disabled",
        )
        self.log_text.pack(fill="both", expand=True)
        self.log_text.tag_configure("prompt", foreground="#ffd166")
        self.log_text.tag_configure("answer", foreground=colors["title"])

        self.ai_sidebar_frame = tk.Frame(
            self,
            bg=colors["panel_border"],
            width=self._ai_sidebar_width,
            highlightbackground=colors["panel_border"],
            highlightthickness=1,
            bd=0,
        )
        self.ai_sidebar_frame.pack_propagate(False)
        self.ai_sidebar_frame.bind("<Configure>", self._on_ai_sidebar_configure)
        self.ai_sidebar_frame.place_forget()

        self.ai_sidebar_resize_handle = tk.Frame(
            self.ai_sidebar_frame,
            bg=colors["sash"],
            width=8,
            cursor="sb_h_double_arrow",
            bd=0,
            highlightthickness=0,
        )
        self.ai_sidebar_resize_handle.pack(side="left", fill="y")
        self.ai_sidebar_resize_handle.bind("<ButtonPress-1>", self._start_ai_sidebar_resize)
        self.ai_sidebar_resize_handle.bind("<B1-Motion>", self._drag_ai_sidebar_resize)
        self.ai_sidebar_resize_handle.bind("<ButtonRelease-1>", self._stop_ai_sidebar_resize)

        ai_shell = tk.Frame(
            self.ai_sidebar_frame,
            bg=colors["surface_alt"],
            highlightbackground=colors["panel_border"],
            highlightthickness=1,
            bd=0,
        )
        ai_shell.pack(side="left", fill="both", expand=True, padx=(0, 10), pady=10)

        sidebar_action_button = {
            "bg": colors["toolbar_button"],
            "fg": colors["toolbar_button_muted"],
            "activebackground": colors["toolbar_button_active"],
            "activeforeground": colors["toolbar_button_text"],
            "disabledforeground": colors["muted"],
            "font": ("Segoe UI", 10, "bold"),
            "relief": "flat",
            "bd": 0,
            "highlightthickness": 1,
            "highlightbackground": colors["panel_border"],
            "highlightcolor": colors["panel_border"],
            "padx": 10,
            "pady": 5,
            "cursor": "hand2",
        }

        ai_header = tk.Frame(ai_shell, bg=colors["surface"])
        ai_header.pack(fill="x")
        ai_header.grid_columnconfigure(0, weight=0)
        ai_header.grid_columnconfigure(1, weight=1)
        ai_header.grid_columnconfigure(2, weight=0)

        tk.Label(
            ai_header,
            text="Assistant",
            bg=colors["surface"],
            fg=colors["text"],
            font=("Segoe UI", 18, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=(18, 10), pady=10)

        self.ai_chat_status_var = tk.StringVar(value="Ready")
        tk.Label(
            ai_header,
            textvariable=self.ai_chat_status_var,
            bg=colors["surface"],
            fg=colors["muted"],
            font=("Segoe UI", 12),
            anchor="e",
        ).grid(row=0, column=1, sticky="e", padx=(0, 8), pady=10)

        tk.Button(
            ai_header,
            text="Close",
            command=self._hide_ai_sidebar,
            **toolbar_button,
        ).grid(row=0, column=2, sticky="e", padx=(0, 10), pady=8)

        transcript_frame = tk.Frame(
            ai_shell,
            bg=colors["surface_alt"],
            highlightbackground=colors["panel_border"],
            highlightthickness=1,
            bd=0,
        )
        self._ai_chat_transcript_frame = transcript_frame
        transcript_frame.pack(fill="both", expand=True, padx=10, pady=(8, 0))

        self.ai_chat_canvas = tk.Canvas(
            transcript_frame,
            bg=colors["surface_alt"],
            highlightthickness=0,
            bd=0,
        )
        self.ai_chat_canvas.pack(side="left", fill="both", expand=True)

        ai_chat_scrollbar = ttk.Scrollbar(
            transcript_frame,
            orient="vertical",
            command=self.ai_chat_canvas.yview,
        )
        ai_chat_scrollbar.pack(side="right", fill="y")
        self.ai_chat_canvas.configure(yscrollcommand=ai_chat_scrollbar.set)

        self.ai_chat_messages = tk.Frame(
            self.ai_chat_canvas,
            bg=colors["surface_alt"],
        )
        self._ai_chat_canvas_window = self.ai_chat_canvas.create_window(
            (0, 0),
            window=self.ai_chat_messages,
            anchor="nw",
        )
        self.ai_chat_canvas.bind("<Configure>", self._on_ai_chat_canvas_configure)
        self.ai_chat_messages.bind("<Configure>", self._update_ai_chat_scrollregion)
        self._install_ai_chat_mousewheel_binding()

        ai_input_section = tk.Frame(ai_shell, bg=colors["surface_alt"])
        ai_input_section.pack(side="bottom", fill="x", padx=10, pady=(6, 6))

        input_frame = tk.Frame(
            ai_input_section,
            bg=colors["surface_deep"],
            highlightbackground=colors["panel_border"],
            highlightthickness=1,
            bd=0,
        )
        input_frame.pack(fill="x")

        self.ai_chat_input = tk.Text(
            input_frame,
            height=2,
            width=1,
            bg=colors["surface_deep"],
            fg=colors["text"],
            font=("Segoe UI", 12),
            insertbackground=colors["text"],
            relief="flat",
            bd=0,
            wrap="word",
            padx=12,
            pady=12,
        )
        self.ai_chat_input.pack(fill="x")
        self.ai_chat_input.bind("<Return>", self._handle_ai_chat_return)
        self._bind_debug_focus_trace(self.ai_chat_input, "ai_chat_input")

        ai_send_row = tk.Frame(ai_input_section, bg=colors["surface_alt"])
        ai_send_row.pack(fill="x", pady=(6, 0))
        self.ai_chat_action_row = ai_send_row

        self.ai_chat_suggest_btn = tk.Button(
            ai_send_row,
            text="Suggest Next Step",
            command=self._request_ai_sidebar_suggestion,
            **sidebar_action_button,
        )

        self.ai_chat_new_btn = tk.Button(
            ai_send_row,
            text="New Chat",
            command=self._reset_ai_chat,
            **sidebar_action_button,
        )

        self.ai_chat_copy_btn = tk.Button(
            ai_send_row,
            text="Copy Chat",
            command=self._copy_ai_chat_transcript,
            **sidebar_action_button,
        )

        self.ai_chat_send_btn = tk.Button(
            ai_send_row,
            text="Send",
            command=lambda: self._submit_ai_chat_from_ui("button"),
            bg=colors["green"],
            fg=colors["text"],
            activebackground=colors["green"],
            activeforeground=colors["text"],
            disabledforeground="#d7f6e2",
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            bd=0,
            padx=16,
            pady=6,
            cursor="hand2",
        )
        self._bind_debug_focus_trace(self.ai_chat_send_btn, "ai_chat_send_button")
        self._layout_ai_chat_action_row()

        transcript_frame.pack_forget()
        transcript_frame.pack(fill="both", expand=True, padx=10, pady=(8, 0))

        if self._ai_sidebar_visible:
            self.after_idle(self._show_ai_sidebar)
        self._reset_ai_chat()
        self._update_ai_sidebar_toggle_ui()

        self.input_bar = tk.Frame(
            self._bottom_dock,
            bg=colors["surface"],
            highlightbackground=colors["panel_border"],
            highlightthickness=1,
            bd=0,
        )
        self._input_bar_pack_kwargs = {"fill": "x", "padx": 36, "pady": (10, 0)}
        self.input_bar.pack(**self._input_bar_pack_kwargs)
        self.input_bar.pack_forget()

        self.input_label_var = tk.StringVar(value="")
        tk.Label(
            self.input_bar,
            textvariable=self.input_label_var,
            bg=colors["surface"],
            fg=colors["text"],
            font=("Segoe UI", 11),
            anchor="w",
        ).pack(side="left", padx=(12, 6), pady=10)

        self.input_var = tk.StringVar()
        self.input_field = tk.Entry(
            self.input_bar,
            textvariable=self.input_var,
            bg=colors["surface_deep"],
            fg=colors["text"],
            font=("Segoe UI", 12),
            insertbackground=colors["text"],
            relief="flat",
            bd=0,
            width=42,
        )
        self.input_field.pack(side="left", fill="x", expand=True, padx=6, pady=10)
        self.input_field.bind("<Return>", lambda e: self._confirm_input())

        tk.Button(
            self.input_bar,
            text="Confirm",
            command=self._confirm_input,
            bg=colors["green"],
            fg=colors["text"],
            activebackground=colors["green"],
            activeforeground=colors["text"],
            font=("Segoe UI", 11, "bold"),
            relief="flat",
            bd=0,
            padx=14,
            pady=8,
            cursor="hand2",
        ).pack(side="left", padx=6, pady=8)

        tk.Button(
            self.input_bar,
            text="Skip",
            command=self._skip_input,
            **toolbar_button,
        ).pack(side="left", padx=(0, 10), pady=8)

        self._session_controls_frame = tk.Frame(self._bottom_dock, bg=bg)
        self._session_controls_frame.pack(fill="x", padx=36, pady=(10, 0))
        self.abort_btn = tk.Button(
            self._session_controls_frame,
            text="ABORT SESSION",
            command=self.request_abort,
            bg=colors["abort"],
            fg=colors["text"],
            activebackground=colors["abort"],
            activeforeground=colors["text"],
            disabledforeground=colors["text"],
            font=("Segoe UI", 18, "bold"),
            relief="flat",
            bd=0,
            padx=18,
            pady=12,
            cursor="hand2",
            state="disabled",
        )
        self.abort_btn.pack(fill="x")

        safe_margin_px = get_bottom_safe_margin_px(self.cfg)
        self._bottom_safe_spacer = tk.Frame(self._bottom_dock, bg=bg, height=safe_margin_px)
        self._bottom_safe_spacer.pack(fill="x")
        self._bottom_safe_spacer.pack_propagate(False)

    def build_interface(self):
        self._build_interface_v2()

    def _browse_folder_in_explorer(self):
        folder = self.ask_directory("Browse Folder", "Choose a folder to open")
        if not folder:
            return
        self._open_path_in_explorer(folder)

    # --- AI Mode Control ---

    def _set_ai_mode(self, mode: str) -> None:
        """Handle AI mode toggle click."""
        self._ai_mode_var.set(mode)
        self.cfg["opt_ai_mode"] = mode
        try:
            from shared.ai_diagnostics import get_diagnostics
            mgr = get_diagnostics()
            if mgr:
                mgr.set_mode(mode)
        except Exception:
            pass
        self._update_ai_mode_ui()
        try:
            from config import save_config
            save_config(self.cfg)
        except Exception:
            pass

    def _update_ai_mode_ui(self) -> None:
        """Update AI toggle button highlights and status indicator."""
        colors = self._theme
        current = self._ai_mode_var.get()
        for mode_value, btn in self._ai_mode_buttons.items():
            if mode_value == current:
                btn.configure(
                    bg=colors["toolbar_button_active"],
                    fg=colors["toolbar_button_text"],
                    activebackground=colors["toolbar_button_active"],
                    activeforeground=colors["toolbar_button_text"],
                    relief="flat",
                )
            else:
                btn.configure(
                    bg=colors["surface"],
                    fg=colors["muted"],
                    activebackground=colors["toolbar_button_active"],
                    activeforeground=colors["toolbar_button_text"],
                    relief="flat",
                )

        # Status indicator
        try:
            from shared.ai_diagnostics import get_diagnostics
            mgr = get_diagnostics()
            if mgr:
                status = mgr.get_status()
                state = status["state"]
                pill_styles = {
                    "active": (colors["pill_active_bg"], colors["pill_active_border"]),
                    "degraded": (colors["pill_warn_bg"], colors["pill_warn_border"]),
                    "disabled": (colors["pill_error_bg"], colors["pill_error_border"]),
                    "off": (colors["pill_idle_bg"], colors["pill_idle_border"]),
                }
                pill_bg, color = pill_styles.get(
                    state,
                    (colors["pill_idle_bg"], colors["pill_idle_border"]),
                )
                calls = f"{status['calls_made']}/{status['calls_max']}"
                if hasattr(self, "_ai_status_pill"):
                    self._ai_status_pill.configure(
                        bg=pill_bg,
                        highlightbackground=color,
                    )
                self._ai_status_label.configure(
                    text=f"\u25cf {state.title()} ({calls})",
                    bg=pill_bg,
                    fg=color,
                )
            else:
                if hasattr(self, "_ai_status_pill"):
                    self._ai_status_pill.configure(
                        bg=colors["pill_idle_bg"],
                        highlightbackground=colors["pill_idle_border"],
                    )
                self._ai_status_label.configure(
                    text="\u25cf Init",
                    bg=colors["pill_idle_bg"],
                    fg=colors["pill_idle_border"],
                )
        except Exception:
            if hasattr(self, "_ai_status_pill"):
                self._ai_status_pill.configure(
                    bg=colors["pill_idle_bg"],
                    highlightbackground=colors["pill_idle_border"],
                )
            self._ai_status_label.configure(
                text="",
                bg=colors["pill_idle_bg"],
                fg=colors["pill_idle_border"],
            )

    def _test_ai_backends(self) -> None:
        """Run quick health check of AI backends (for future Test AI button)."""
        try:
            from shared.ai_diagnostics import get_diagnostics
            mgr = get_diagnostics()
            if not mgr:
                self.show_info("AI Test", "Diagnostics not initialized.")
                return
            results = mgr.test_backends()
            msg = f"Cloud: {results.get('cloud', 'N/A')}\nLocal: {results.get('local', 'N/A')}"
            self.show_info("AI Backend Test", msg)
            self._update_ai_mode_ui()
        except Exception as e:
            self.show_info("AI Test", f"Test failed: {e}")

    def _open_ai_providers(self) -> None:
        """Open the AI provider connection dialog."""
        from gui.ai_provider_dialog import open_ai_provider_dialog
        open_ai_provider_dialog(self, on_change=self._update_ai_mode_ui)

    def _open_folder_scanner(self):
        from tools.folder_scanner import scan_folder

        folder = self.ask_directory("Folder Scanner", "Choose a folder to scan")
        if not folder:
            self.show_info("Folder Scanner", "No folder selected.")
            return

        scan_options = self._ask_folder_scan_options()
        if scan_options is None:
            self.show_info("Folder Scanner", "Scan cancelled.")
            return

        scan_request = build_folder_scan_request(
            folder=folder,
            scan_options=scan_options,
            main_log=str(self.cfg.get("log_file", "") or ""),
            ffprobe_path=str(self.cfg.get("ffprobe_path", "") or ""),
            include_dirs=False,
            allow_path_lookup=self._allow_path_tool_resolution(),
        )

        progress_win = tk.Toplevel(self)
        progress_win.title("Scanning MKVs...")
        progress_win.geometry("400x120")
        progress_win.configure(bg=self._theme["surface"])
        progress_win.grab_set()
        tk.Label(
            progress_win,
            text=f"Scanning: {folder}",
            bg=self._theme["surface"],
            fg=self._theme["title"],
            font=("Segoe UI", 11, "bold"),
        ).pack(pady=(18, 8))
        progress_var = tk.DoubleVar(value=0)
        progress_bar = ttk.Progressbar(
            progress_win,
            variable=progress_var,
            maximum=100,
            mode="determinate",
        )
        progress_bar.pack(fill="x", padx=30, pady=(0, 12))
        status_var = tk.StringVar(value="Starting scan...")
        tk.Label(
            progress_win,
            textvariable=status_var,
            bg=self._theme["surface"],
            fg=self._theme["muted"],
            font=("Segoe UI", 10, "italic"),
        ).pack()

        results = []

        def do_scan():
            nonlocal results
            import traceback

            try:
                def progress_cb(current, total):
                    def _update_progress() -> None:
                        pct = (current / total) * 100 if total else 0
                        progress_var.set(pct)
                        if total:
                            status_var.set(f"Scanning {current} of {total} items...")
                        else:
                            status_var.set(f"Scanning {current} item(s)...")

                    self.after(0, _update_progress)

                results = scan_folder(
                    scan_request.folder,
                    mode=scan_request.mode,
                    progress_cb=progress_cb,
                    log_path=scan_request.log_path,
                    recursive=scan_request.recursive,
                    include_dirs=scan_request.include_dirs,
                    ffprobe_exe=scan_request.ffprobe_exe,
                )
            except Exception as e:
                print("[ERROR] Exception in folder scan thread:", e)
                traceback.print_exc()
                results.append(e)
            self.after(0, on_done)

        def on_done():
            try:
                progress_win.destroy()
            except Exception as destroy_exc:
                print("[ERROR] Exception destroying progress_win:", destroy_exc)
            if results and isinstance(results[0], Exception):
                import traceback

                tb = traceback.format_exc()
                print(f"[ERROR] Folder Scanner error: {results[0]}\nTraceback:\n{tb}")
                self.show_error(
                    "Folder Scanner",
                    f"Error scanning folder:\n{results[0]}\n\nSee terminal for traceback.",
                )
                return
            try:
                self._show_folder_scan_results(folder, results, scan_options)
            except Exception as show_exc:
                print("[ERROR] Exception showing scan results:", show_exc)
                import traceback

                traceback.print_exc()
                self.show_error(
                    "Folder Scanner",
                    f"Error displaying scan results:\n{show_exc}\n\nSee terminal for traceback.",
                )

        threading.Thread(target=do_scan, daemon=True).start()

    def _ask_folder_scan_options(self):
        from tools.folder_scanner import SORT_MODE_LABELS

        win = tk.Toplevel(self)
        win.title("MKV Scanner — Sort Options")
        win.configure(bg=self._theme["surface"])
        win.grab_set()
        win.resizable(False, False)
        sort_var = tk.StringVar(value="size_desc")
        recursive_var = tk.BooleanVar(value=True)
        tk.Label(
            win,
            text="Scan MKV files for ffmpeg / HandBrake prep",
            bg=self._theme["surface"],
            fg=self._theme["title"],
            font=("Segoe UI", 12, "bold"),
        ).pack(padx=18, pady=(18, 6))
        tk.Label(
            win,
            text="Only .mkv files are shown. Subfolders are scanned by default.",
            bg=self._theme["surface"],
            fg=self._theme["muted"],
            font=("Segoe UI", 10),
            wraplength=420,
            justify="left",
        ).pack(padx=18, pady=(0, 10), anchor="w")
        for mode, label in SORT_MODE_LABELS.items():
            tk.Radiobutton(
                win,
                text=label,
                variable=sort_var,
                value=mode,
                bg=self._theme["surface"],
                fg=self._theme["text"],
                selectcolor=self._theme["surface_deep"],
                font=("Segoe UI", 11),
                anchor="w",
            ).pack(anchor="w", padx=24)
        tk.Checkbutton(
            win,
            text="Scan subfolders recursively",
            variable=recursive_var,
            bg=self._theme["surface"],
            fg=self._theme["text"],
            selectcolor=self._theme["surface_deep"],
            activebackground=self._theme["surface"],
            activeforeground=self._theme["text"],
            font=("Segoe UI", 10),
        ).pack(anchor="w", padx=24, pady=(8, 0))
        btn_row = tk.Frame(win, bg=self._theme["surface"])
        btn_row.pack(pady=16)
        result = [None]

        def ok():
            result[0] = {
                "mode": sort_var.get(),
                "recursive": bool(recursive_var.get()),
            }
            win.destroy()

        def cancel():
            result[0] = None
            win.destroy()

        tk.Button(
            btn_row,
            text="Scan",
            command=ok,
            bg=self._theme["green"],
            fg=self._theme["text"],
            font=("Segoe UI", 10, "bold"),
            width=10,
            relief="flat",
        ).pack(side="left", padx=8)
        tk.Button(
            btn_row,
            text="Cancel",
            command=cancel,
            bg=self._theme["toolbar_button"],
            fg=self._theme["toolbar_button_muted"],
            font=("Segoe UI", 10),
            width=10,
            relief="flat",
        ).pack(side="left", padx=8)
        win.wait_window()
        return result[0]

    def _show_folder_scan_results(self, folder, results, scan_options):
        colors = self._theme
        bg = colors["window_bg"]
        results_model = build_folder_scan_results_model(results, scan_options)
        win = tk.Toplevel(self)
        win.title(f"MKV Scanner Results — {os.path.basename(folder)}")
        win.configure(bg=bg)
        win.geometry("1100x650")
        win.lift()
        win.focus_force()
        tk.Label(
            win,
            text=f"MKV Scan Results for:\n{folder}",
            bg=bg,
            fg=colors["title"],
            font=("Segoe UI", 12, "bold"),
        ).pack(pady=(16, 4))
        tk.Label(
            win,
            text=results_model.subtitle,
            bg=bg,
            fg=colors["muted"],
            font=("Segoe UI", 10, "italic"),
        ).pack(pady=(0, 10))
        frame = tk.Frame(win, bg=bg)
        frame.pack(fill="both", expand=True, padx=16, pady=8)
        style = ttk.Style(win)
        style.theme_use("default")
        style.configure(
            "FolderScan.Treeview",
            background=colors["surface_alt"],
            foreground=colors["text"],
            fieldbackground=colors["surface_alt"],
            rowheight=24,
            font=("Consolas", 10),
        )
        style.configure(
            "FolderScan.Treeview.Heading",
            background=colors["surface"],
            foreground=colors["title"],
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "FolderScan.Treeview",
            background=[("selected", colors["blue"])],
            foreground=[("selected", colors["text"])],
        )
        tree = ttk.Treeview(
            frame,
            columns=("name", "folder", "size", "duration", "modified", "status"),
            show="headings",
            style="FolderScan.Treeview",
            selectmode="extended",
        )
        tree.heading("name", text="Name")
        tree.heading("folder", text="Folder")
        tree.heading("size", text="Size")
        tree.heading("duration", text="Runtime")
        tree.heading("modified", text="Modified")
        tree.heading("status", text="Status")
        tree.column("name", width=280, anchor="w")
        tree.column("folder", width=320, anchor="w")
        tree.column("size", width=110, anchor="e")
        tree.column("duration", width=90, anchor="e")
        tree.column("modified", width=140, anchor="center")
        tree.column("status", width=110, anchor="center")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        for row in results_model.rows:
            tree.insert(
                "",
                "end",
                iid=row["iid"],
                values=row["values"],
            )

        footer = tk.Frame(win, bg=bg)
        footer.pack(fill="x", padx=16, pady=(0, 12))
        status_var = tk.StringVar(value=results_model.status_text)
        tk.Label(
            footer,
            textvariable=status_var,
            bg=bg,
            fg=colors["title"],
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left")

        def _selected_entries():
            return select_folder_scan_entries(results_model.rows, tree.selection())

        def _selected_paths():
            return select_folder_scan_paths(results_model.rows, tree.selection())

        def _reveal_selected(_event=None):
            selected_paths = _selected_paths()
            if not selected_paths:
                status_var.set("Select at least one MKV file first.")
                return
            self._reveal_path_in_explorer(selected_paths[0])
            status_var.set(f"Revealed: {os.path.basename(selected_paths[0])}")

        def _copy_selected():
            selected_paths = _selected_paths()
            if not selected_paths:
                status_var.set("Select at least one MKV file first.")
                return
            self.clipboard_clear()
            self.clipboard_append("\n".join(selected_paths))
            status_var.set(f"Copied {len(selected_paths)} path(s) to the clipboard.")

        def _select_all(_event=None):
            children = tree.get_children("")
            if not children:
                status_var.set("No MKV files are available to select.")
                return "break"
            tree.selection_set(children)
            status_var.set(f"Selected {len(children)} MKV file(s).")
            return "break"

        def _queue_selected():
            selected_entries = _selected_entries()
            if not selected_entries:
                status_var.set("Select at least one MKV file first.")
                return
            self._open_transcode_queue_builder(
                folder,
                [entry["path"] for entry in selected_entries],
                selected_entries=selected_entries,
            )

        def _recommend_selected():
            selected_paths = _selected_paths()
            if not selected_paths:
                status_var.set("Select one MKV file first.")
                return
            if len(selected_paths) != 1:
                status_var.set("Select exactly one MKV file for recommendations.")
                return
            self._open_ffmpeg_recommendation_scan(folder, selected_paths[0])

        tree.bind("<Double-1>", _reveal_selected)
        tree.bind("<Control-a>", _select_all)
        tree.bind("<Control-A>", _select_all)
        tree.focus_set()

        button_row = tk.Frame(win, bg=bg)
        button_row.pack(fill="x", padx=16, pady=(0, 12))
        tk.Button(
            button_row,
            text="Copy Selected Paths",
            command=_copy_selected,
            bg=colors["toolbar_button"],
            fg=colors["toolbar_button_text"],
            font=("Segoe UI", 10),
            relief="flat",
        ).pack(side="left", padx=4)
        tk.Button(
            button_row,
            text="Build Queue",
            command=_queue_selected,
            bg=colors["blue"],
            fg=colors["text"],
            font=("Segoe UI", 10, "bold"),
            relief="flat",
        ).pack(side="left", padx=4)
        tk.Button(
            button_row,
            text="Recommend For Selected",
            command=_recommend_selected,
            bg=colors["orange"],
            fg=colors["text"],
            font=("Segoe UI", 10, "bold"),
            relief="flat",
        ).pack(side="left", padx=4)
        tk.Button(
            button_row,
            text="Reveal Selected",
            command=_reveal_selected,
            bg=colors["toolbar_button"],
            fg=colors["toolbar_button_text"],
            font=("Segoe UI", 10),
            relief="flat",
        ).pack(side="left", padx=4)
        tk.Button(
            button_row,
            text="Close",
            command=win.destroy,
            bg=colors["toolbar_button"],
            fg=colors["toolbar_button_muted"],
            font=("Segoe UI", 10),
            relief="flat",
        ).pack(side="right", padx=4)

    def _open_ffmpeg_recommendation_scan(self, scan_root, input_path):
        ffprobe_exe = self._resolve_ffprobe_tool().path or ""
        if not ffprobe_exe or not os.path.isfile(ffprobe_exe):
            self.show_error(
                "FFmpeg Recommendation",
                "ffprobe is required for recommendations and was not found.\n\n"
                "Open Settings > Paths and confirm the ffmpeg / ffprobe folder.",
            )
            return

        progress_win = tk.Toplevel(self)
        progress_win.title("Analyzing MKV...")
        progress_win.geometry("420x130")
        progress_win.configure(bg=self._theme["surface"])
        tk.Label(
            progress_win,
            text=f"Analyzing:\n{os.path.basename(input_path)}",
            bg=self._theme["surface"],
            fg=self._theme["title"],
            font=("Segoe UI", 11, "bold"),
        ).pack(pady=(18, 8))
        tk.Label(
            progress_win,
            text="Running a second pass with ffprobe to recommend safer FFmpeg settings.",
            bg=self._theme["surface"],
            fg=self._theme["muted"],
            font=("Segoe UI", 10),
            wraplength=360,
            justify="center",
        ).pack(pady=(0, 12))
        status_var = tk.StringVar(value="Starting analysis...")
        tk.Label(
            progress_win,
            textvariable=status_var,
            bg=self._theme["surface"],
            fg=self._theme["text"],
            font=("Segoe UI", 10, "italic"),
        ).pack()

        result_holder = {
            "analysis": None,
            "recommendation_result": None,
            "error": None,
        }

        def _worker():
            try:
                result_holder["analysis"] = probe_media_for_recommendation(
                    input_path,
                    ffprobe_exe,
                )
                result_holder["recommendation_result"] = build_ffmpeg_recommendations(
                    result_holder["analysis"]
                )
            except Exception as exc:
                result_holder["error"] = exc
            self.after(0, _on_done)

        def _on_done():
            try:
                progress_win.destroy()
            except Exception:
                pass

            error = result_holder["error"]
            if error is not None:
                self.show_error(
                    "FFmpeg Recommendation",
                    f"Could not analyze the selected MKV:\n{error}",
                )
                return

            self._show_ffmpeg_recommendations(
                scan_root,
                result_holder["analysis"],
                result_holder["recommendation_result"],
            )

        threading.Thread(target=_worker, daemon=True).start()

    def _start_ffmpeg_recommendation_queue(
        self,
        scan_root,
        analysis,
        recommendation,
        output_root,
    ):
        ffmpeg_exe, ffmpeg_status = self._resolve_transcode_backend_path("ffmpeg")
        if not ffmpeg_exe:
            self.show_error(
                "FFmpeg Recommendation",
                f"{ffmpeg_status}\n\nSet the FFmpeg executable in Settings > Paths.",
            )
            return False

        if not self._ffmpeg_version_ok(ffmpeg_exe):
            return False

        if not output_root:
            self.show_error(
                "FFmpeg Recommendation",
                "Choose an output folder before queuing the recommendation.",
            )
            return False

        try:
            os.makedirs(output_root, exist_ok=True)
        except Exception as exc:
            self.show_error(
                "FFmpeg Recommendation",
                f"Could not create the output folder:\n{exc}",
            )
            return False

        plans = _build_transcode_plan(scan_root, [analysis["path"]], output_root)
        if not plans:
            self.show_error(
                "FFmpeg Recommendation",
                "The selected file could not be added to the queue.",
            )
            return False

        ffmpeg_source_mode = normalize_ffmpeg_source_mode(
            self.cfg.get("opt_ffmpeg_source_mode", FFMPEG_SOURCE_MODE_SAFE_COPY)
        )
        build_result = build_recommendation_job(
            plan=plans[0],
            analysis=analysis,
            recommendation=recommendation,
            ffmpeg_source_mode=ffmpeg_source_mode,
        )

        log_dir = os.path.join(get_config_dir(), "transcode_logs")
        transcode_queue = build_transcode_queue(
            jobs=build_result.jobs,
            log_dir=log_dir,
            ffmpeg_exe=ffmpeg_exe,
            ffprobe_exe=self._resolve_ffprobe_tool().path,
            handbrake_exe=self._resolve_transcode_backend_path("handbrake")[0],
            ffmpeg_source_mode=ffmpeg_source_mode,
            temp_root=os.path.normpath(
                self.cfg.get("temp_folder", DEFAULTS["temp_folder"])
            ),
        )
        self._run_transcode_queue(
            transcode_queue,
            "FFmpeg",
            os.path.normpath(output_root),
            queue_detail=build_result.queue_detail,
        )
        return True

    def _show_ffmpeg_recommendations(self, scan_root, analysis, recommendation_result):
        colors = self._theme
        bg = colors["window_bg"]
        win = tk.Toplevel(self)
        win.title(f"FFmpeg Recommendation - {analysis['name']}")
        win.configure(bg=bg)
        win.geometry("1040x900")
        win.lift()
        win.focus_force()

        tk.Label(
            win,
            text=f"FFmpeg recommendations for {analysis['name']}",
            bg=bg,
            fg=colors["title"],
            font=("Segoe UI", 13, "bold"),
        ).pack(padx=18, pady=(18, 6), anchor="w")
        tk.Label(
            win,
            text=(
                "This second pass looks at the actual file and gives you three safer starting points "
                "for making it smaller with FFmpeg."
            ),
            bg=bg,
            fg=colors["muted"],
            font=("Segoe UI", 10),
            wraplength=900,
            justify="left",
        ).pack(padx=18, pady=(0, 12), anchor="w")

        summary_frame = tk.Frame(win, bg=colors["surface"])
        summary_frame.pack(fill="x", padx=18, pady=(0, 10))
        tk.Label(
            summary_frame,
            text="File summary",
            bg=colors["surface"],
            fg=colors["title"],
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=12, pady=(10, 4))
        for line in format_analysis_summary(analysis):
            tk.Label(
                summary_frame,
                text=line,
                bg=colors["surface"],
                fg=colors["text"],
                font=("Segoe UI", 10),
                anchor="w",
                justify="left",
            ).pack(fill="x", padx=12)

        recommended_id = recommendation_result["recommended_id"]
        selected_var = tk.StringVar(value=recommended_id)
        recommendation_map = {
            rec["id"]: rec
            for rec in recommendation_result["recommendations"]
        }
        status_var = tk.StringVar(
            value=f"We recommend {recommendation_map[recommended_id]['label']}. "
            f"{recommendation_result['recommendation_reason']}"
        )

        if recommendation_result["advisory"]:
            advisory_frame = tk.Frame(win, bg=colors["pill_warn_bg"])
            advisory_frame.pack(fill="x", padx=18, pady=(0, 10))
            tk.Label(
                advisory_frame,
                text=recommendation_result["advisory"],
                bg=colors["pill_warn_bg"],
                fg=colors["pill_warn_border"],
                font=("Segoe UI", 10, "bold"),
                wraplength=900,
                justify="left",
            ).pack(fill="x", padx=12, pady=10)

        decision_lines = []
        decision_lines.extend(recommendation_result.get("decision_factors", []))
        decision_lines.extend(recommendation_result.get("source_notes", []))
        if decision_lines:
            decision_frame = tk.Frame(win, bg=colors["surface"])
            decision_frame.pack(fill="x", padx=18, pady=(0, 10))
            tk.Label(
                decision_frame,
                text="Why this recommendation",
                bg=colors["surface"],
                fg=colors["title"],
                font=("Segoe UI", 10, "bold"),
            ).pack(anchor="w", padx=12, pady=(10, 4))
            tk.Label(
                decision_frame,
                text="\n".join(f"- {line}" for line in decision_lines),
                bg=colors["surface"],
                fg=colors["text"],
                font=("Segoe UI", 10),
                wraplength=960,
                justify="left",
            ).pack(fill="x", padx=12, pady=(0, 10))

        tk.Label(
            win,
            textvariable=status_var,
            bg=bg,
            fg=colors["title"],
            font=("Segoe UI", 10, "bold"),
            wraplength=900,
            justify="left",
        ).pack(padx=18, pady=(0, 10), anchor="w")

        options_frame = tk.Frame(win, bg=bg)
        options_frame.pack(fill="both", expand=True, padx=18, pady=(0, 10))
        for recommendation in recommendation_result["recommendations"]:
            is_recommended = recommendation["id"] == recommended_id
            card = tk.Frame(
                options_frame,
                bg=colors["surface"] if is_recommended else colors["surface_alt"],
                highlightthickness=1,
                highlightbackground=colors["title"] if is_recommended else colors["panel_border"],
            )
            card.pack(fill="x", pady=6)
            tk.Radiobutton(
                card,
                text=(
                    f"{recommendation['label']}"
                    f"{' (Recommended)' if is_recommended else ''}"
                ),
                variable=selected_var,
                value=recommendation["id"],
                bg=card.cget("bg"),
                fg=colors["text"],
                selectcolor=colors["surface_deep"],
                activebackground=card.cget("bg"),
                activeforeground=colors["text"],
                font=("Segoe UI", 11, "bold"),
                anchor="w",
                command=lambda rec=recommendation: status_var.set(
                    f"{rec['label']}: {rec['why']}"
                ),
            ).pack(anchor="w", padx=12, pady=(10, 2))
            tk.Label(
                card,
                text=recommendation["summary"],
                bg=card.cget("bg"),
                fg=colors["title"],
                font=("Segoe UI", 10, "bold"),
                anchor="w",
                justify="left",
            ).pack(fill="x", padx=34)
            tk.Label(
                card,
                text=recommendation["details"],
                bg=card.cget("bg"),
                fg=colors["text"],
                font=("Segoe UI", 10),
                anchor="w",
                justify="left",
                wraplength=860,
            ).pack(fill="x", padx=34, pady=(2, 2))
            tk.Label(
                card,
                text=f"Best for: {recommendation.get('best_for', 'General use')}",
                bg=card.cget("bg"),
                fg=colors["muted"],
                font=("Segoe UI", 10),
                anchor="w",
                justify="left",
                wraplength=920,
            ).pack(fill="x", padx=34, pady=(0, 2))
            tk.Label(
                card,
                text=f"Expected: {recommendation.get('expected_result', recommendation['summary'])}",
                bg=card.cget("bg"),
                fg=colors["muted"],
                font=("Segoe UI", 10),
                anchor="w",
                justify="left",
                wraplength=920,
            ).pack(fill="x", padx=34, pady=(0, 2))
            tk.Label(
                card,
                text=recommendation["why"],
                bg=card.cget("bg"),
                fg=colors["muted"],
                font=("Segoe UI", 10, "italic"),
                anchor="w",
                justify="left",
                wraplength=860,
            ).pack(fill="x", padx=34, pady=(0, 10))
            caution = str(recommendation.get("caution", "") or "").strip()
            if caution:
                tk.Label(
                    card,
                    text=f"Watch out: {caution}",
                    bg=card.cget("bg"),
                    fg=colors["pill_warn_border"],
                    font=("Segoe UI", 10),
                    anchor="w",
                    justify="left",
                    wraplength=920,
                ).pack(fill="x", padx=34, pady=(0, 10))

        output_root_var = tk.StringVar(
            value=_suggest_transcode_output_root(scan_root, "ffmpeg")
        )
        output_row = tk.Frame(win, bg=bg)
        output_row.pack(fill="x", padx=18, pady=(0, 10))
        tk.Label(
            output_row,
            text="Output root:",
            bg=bg,
            fg=colors["text"],
            font=("Segoe UI", 10, "bold"),
            width=12,
            anchor="w",
        ).pack(side="left")
        tk.Entry(
            output_row,
            textvariable=output_root_var,
            bg=colors["surface_deep"],
            fg=colors["text"],
            font=("Segoe UI", 10),
            relief="flat",
            bd=3,
        ).pack(side="left", fill="x", expand=True, padx=(0, 8))

        def _browse_output_root():
            current_output = output_root_var.get().strip()
            initial_dir = current_output or os.path.dirname(scan_root) or scan_root
            chosen = self.ask_directory(
                "FFmpeg Recommendation",
                "Choose an output folder",
                initialdir=initial_dir,
            )
            if chosen:
                output_root_var.set(os.path.normpath(chosen))

        tk.Button(
            output_row,
            text="Browse",
            command=_browse_output_root,
            bg=colors["toolbar_button"],
            fg=colors["toolbar_button_text"],
            font=("Segoe UI", 10),
            relief="flat",
        ).pack(side="left")

        button_row = tk.Frame(win, bg=bg)
        button_row.pack(fill="x", padx=18, pady=(0, 18))

        def _queue_recommendation():
            selected_recommendation = recommendation_map.get(selected_var.get())
            if not selected_recommendation:
                self.show_error(
                    "FFmpeg Recommendation",
                    "Choose a recommendation first.",
                )
                return
            if self._start_ffmpeg_recommendation_queue(
                scan_root,
                analysis,
                selected_recommendation,
                output_root_var.get().strip(),
            ):
                win.destroy()

        def _make_custom_profile():
            # Seed the editor from the file-specific recommended preset so all
            # starting values (CRF, preset) already reflect this MKV's resolution,
            # bitrate, and HDR status — not generic defaults.
            seed_rec = (
                recommendation_map.get(recommended_id)
                or recommendation_result["recommendations"][0]
            )
            initial_data = dict(seed_rec["profile_data"])

            def _on_custom_apply(profile_data, crf, preset):
                synthetic_rec = {
                    "id": "custom",
                    "label": "Custom",
                    "profile_name": f"Custom - {analysis['name']}",
                    "profile_data": profile_data,
                    "crf": crf,
                    "preset": preset,
                    "details": f"Custom encode: CRF {crf}, preset {preset}.",
                    "why": "User-configured custom settings.",
                    "caution": "",
                    "expected_result": "Results depend on your chosen settings.",
                }
                if self._start_ffmpeg_recommendation_queue(
                    scan_root,
                    analysis,
                    synthetic_rec,
                    output_root_var.get().strip(),
                ):
                    win.destroy()

            self._open_custom_transcode_editor(win, initial_data, _on_custom_apply, analysis=analysis)

        tk.Button(
            button_row,
            text="Queue Chosen Recommendation",
            command=_queue_recommendation,
            bg=colors["green"],
            fg=colors["text"],
            font=("Segoe UI", 10, "bold"),
            relief="flat",
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            button_row,
            text="Make Custom Profile",
            command=_make_custom_profile,
            bg=colors["blue"],
            fg=colors["text"],
            font=("Segoe UI", 10, "bold"),
            relief="flat",
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            button_row,
            text="Open Regular Queue",
            command=lambda: self._open_transcode_queue_builder(
                scan_root,
                [analysis["path"]],
                backend="ffmpeg",
            ),
            bg=colors["toolbar_button"],
            fg=colors["toolbar_button_text"],
            font=("Segoe UI", 10),
            relief="flat",
        ).pack(side="left")
        tk.Button(
            button_row,
            text="Close",
            command=win.destroy,
            bg=colors["toolbar_button"],
            fg=colors["toolbar_button_muted"],
            font=("Segoe UI", 10),
            relief="flat",
        ).pack(side="right")

    def _open_custom_transcode_editor(self, parent, initial_data, on_apply, analysis=None):
        theme = self._theme
        BG       = theme["window_bg"]
        CARD     = theme["surface"]
        FG       = theme["text"]
        ACCENT   = theme["title"]
        MUTED    = theme["muted"]
        INPUT_BG = theme["surface_deep"]
        WARN     = theme["pill_warn_border"]

        data        = initial_data or {}
        video       = data.get("video", {})
        audio       = data.get("audio", {})
        subs        = data.get("subtitles", {})
        output_sec  = data.get("output", {})
        constraints = data.get("constraints", {})
        meta        = data.get("metadata", {})
        advanced    = data.get("advanced", {})

        dlg = tk.Toplevel(parent)
        dlg.title("Make Custom Profile")
        dlg.configure(bg=BG)
        dlg.geometry("860x660")
        dlg.transient(parent)
        dlg.grab_set()
        dlg.lift()
        dlg.focus_force()

        # ── Header ─────────────────────────────────────────────────────────────
        tk.Label(
            dlg, text="Make Custom Profile",
            bg=BG, fg=ACCENT, font=("Segoe UI", 12, "bold"),
        ).pack(padx=18, pady=(14, 2), anchor="w")

        if analysis:
            info_parts = []
            if analysis.get("video_codec"):
                info_parts.append(analysis["video_codec"].upper())
            w, h = analysis.get("width", 0), analysis.get("height", 0)
            if w and h:
                info_parts.append(f"{w}x{h}")
            if analysis.get("bitrate_bps", 0) > 0:
                info_parts.append(f"{analysis['bitrate_bps'] / 1_000_000:.1f} Mbps")
            if analysis.get("size_bytes", 0) > 0:
                info_parts.append(f"{analysis['size_bytes'] / (1024 ** 3):.2f} GB")
            if info_parts:
                tk.Label(
                    dlg,
                    text=f"Source: {analysis.get('name', '')}   ·   {' | '.join(info_parts)}",
                    bg=BG, fg=MUTED, font=("Segoe UI", 9),
                ).pack(padx=18, pady=(0, 2), anchor="w")

        tk.Label(
            dlg,
            text=(
                "All values are pre-filled from the file-specific recommendation. "
                "Adjust any setting, then Apply Once for this file or Save as a reusable profile."
            ),
            bg=BG, fg=MUTED, font=("Segoe UI", 10),
            wraplength=800, justify="left",
        ).pack(padx=18, pady=(0, 8), anchor="w")

        # ── Notebook ──────────────────────────────────────────────────────────
        nb_style = ttk.Style()
        nb_style.configure(
            "CTP.TNotebook",
            background=BG, borderwidth=0, tabmargins=[0, 0, 0, 0],
        )
        nb_style.configure(
            "CTP.TNotebook.Tab",
            background=INPUT_BG, foreground=MUTED,
            padding=[12, 5], font=("Segoe UI", 10),
        )
        nb_style.map(
            "CTP.TNotebook.Tab",
            background=[("selected", CARD)],
            foreground=[("selected", ACCENT)],
        )
        nb = ttk.Notebook(dlg, style="CTP.TNotebook")
        nb.pack(fill="both", expand=True, padx=18, pady=(0, 8))

        # Helper: scrollable tab body
        def _make_tab(label):
            outer = tk.Frame(nb, bg=BG)
            nb.add(outer, text=f"  {label}  ")
            canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
            vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
            body = tk.Frame(canvas, bg=BG)
            body.bind(
                "<Configure>",
                lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
            )
            canvas.create_window((0, 0), window=body, anchor="nw")
            canvas.configure(yscrollcommand=vsb.set)
            canvas.pack(side="left", fill="both", expand=True)
            vsb.pack(side="right", fill="y")

            def _on_enter(_e):
                canvas.bind_all(
                    "<MouseWheel>",
                    lambda ev: canvas.yview_scroll(int(-1 * (ev.delta / 120)), "units"),
                )

            def _on_leave(_e):
                canvas.unbind_all("<MouseWheel>")

            canvas.bind("<Enter>", _on_enter)
            canvas.bind("<Leave>", _on_leave)
            return body

        # Layout helpers
        def _sec(parent, text):
            tk.Label(
                parent, text=text, bg=BG, fg=ACCENT,
                font=("Segoe UI", 10, "bold"),
            ).pack(anchor="w", padx=10, pady=(14, 2))
            tk.Frame(parent, bg=theme["panel_border"], height=1).pack(fill="x", padx=10, pady=(0, 6))

        def _row(parent, label, widget_fn, hint=None):
            f = tk.Frame(parent, bg=BG)
            f.pack(fill="x", padx=10, pady=3)
            tk.Label(
                f, text=label, bg=BG, fg=FG,
                font=("Segoe UI", 10), width=28, anchor="w",
            ).pack(side="left")
            w = widget_fn(f)
            w.pack(side="left")
            if hint:
                tk.Label(f, text=hint, bg=BG, fg=MUTED,
                         font=("Segoe UI", 9)).pack(side="left", padx=(6, 0))
            return w

        def _check_row(parent, label, var, hint=None):
            f = tk.Frame(parent, bg=BG)
            f.pack(fill="x", padx=10, pady=2)
            tk.Checkbutton(
                f, text=label, variable=var,
                bg=BG, fg=FG, selectcolor=INPUT_BG,
                activebackground=BG, activeforeground=FG,
                font=("Segoe UI", 10), anchor="w",
            ).pack(side="left", anchor="w")
            if hint:
                tk.Label(f, text=hint, bg=BG, fg=MUTED,
                         font=("Segoe UI", 9)).pack(side="left", padx=(6, 0))

        def _combo(p, values, var, width=16):
            return ttk.Combobox(
                p, textvariable=var, values=values, state="readonly", width=width,
            )

        def _entry(p, var, width=14):
            return tk.Entry(
                p, textvariable=var, bg=INPUT_BG, fg=FG,
                font=("Segoe UI", 10), relief="flat", bd=3, width=width,
            )

        # ── VIDEO TAB ─────────────────────────────────────────────────────────
        vt = _make_tab("Video")

        _sec(vt, "Codec & Quality")
        v_codec   = tk.StringVar(value=str(video.get("codec") or "h265"))
        v_mode    = tk.StringVar(value=str(video.get("mode")  or "crf"))
        v_crf     = tk.IntVar(value=int(video.get("crf") or 18))
        v_bitrate = tk.StringVar(
            value="" if not video.get("bitrate") else str(video["bitrate"]),
        )
        _row(vt, "Codec:", lambda p: _combo(p, ["h265", "h264", "copy"], v_codec))
        _row(vt, "Mode:", lambda p: _combo(p, ["crf", "bitrate", "copy"], v_mode))

        crf_row = tk.Frame(vt, bg=BG)
        crf_row.pack(fill="x", padx=10, pady=3)
        tk.Label(
            crf_row, text="CRF (0–51, lower = better):", bg=BG, fg=FG,
            font=("Segoe UI", 10), width=28, anchor="w",
        ).pack(side="left")
        crf_spin = tk.Spinbox(
            crf_row, textvariable=v_crf, from_=0, to=51,
            bg=INPUT_BG, fg=FG, font=("Segoe UI", 10), relief="flat", bd=1, width=6,
        )
        crf_spin.pack(side="left")
        tk.Label(
            crf_row,
            text="h265: 18 = high quality   22 = default   28 = smaller",
            bg=BG, fg=MUTED, font=("Segoe UI", 9),
        ).pack(side="left", padx=(8, 0))

        br_row = tk.Frame(vt, bg=BG)
        br_row.pack(fill="x", padx=10, pady=3)
        tk.Label(
            br_row, text="Bitrate (kbps):", bg=BG, fg=FG,
            font=("Segoe UI", 10), width=28, anchor="w",
        ).pack(side="left")
        br_entry = tk.Entry(
            br_row, textvariable=v_bitrate,
            bg=INPUT_BG, fg=FG, font=("Segoe UI", 10), relief="flat", bd=3, width=10,
        )
        br_entry.pack(side="left")
        tk.Label(
            br_row, text="e.g. 4000 for 4 Mbps   (only used when mode = bitrate)",
            bg=BG, fg=MUTED, font=("Segoe UI", 9),
        ).pack(side="left", padx=(8, 0))

        def _sync_mode(*_):
            m = v_mode.get()
            crf_spin.configure(state="normal" if m == "crf"     else "disabled")
            br_entry.configure(state="normal" if m == "bitrate" else "disabled")

        v_mode.trace_add("write", _sync_mode)
        _sync_mode()

        _sec(vt, "Speed & Quality Trade-offs")
        v_preset = tk.StringVar(value=str(video.get("preset") or "slow"))
        v_tune   = tk.StringVar(value=str(video.get("tune") or ""))
        _row(vt, "Preset:", lambda p: _combo(p, [
            "ultrafast", "superfast", "veryfast", "faster", "fast",
            "medium", "slow", "slower", "veryslow",
        ], v_preset, width=12),
            hint="slow = best quality; faster encodes trade quality for speed")
        _row(vt, "Tune:", lambda p: _combo(p, [
            "", "film", "animation", "grain", "stillimage", "fastdecode", "zerolatency",
        ], v_tune, width=14),
            hint="film = live action  animation = anime  grain = preserve noise")

        _sec(vt, "Encoder Details")
        v_vid_profile = tk.StringVar(value=str(video.get("video_profile") or ""))
        v_pix_fmt     = tk.StringVar(value=str(video.get("pix_fmt") or ""))
        v_hwaccel     = tk.StringVar(value=str(video.get("hw_accel") or "cpu"))
        _row(vt, "Encoder profile:", lambda p: _combo(p, [
            "", "main", "main10", "high", "high10", "baseline",
        ], v_vid_profile, width=12),
            hint="main10 for 10-bit; blank = let the encoder decide")
        _row(vt, "Pixel format:", lambda p: _combo(p, [
            "", "yuv420p", "yuv420p10le", "yuv422p10le", "yuv444p", "yuv444p10le",
        ], v_pix_fmt, width=14),
            hint="yuv420p10le = 10-bit for HDR; blank = keep source format")
        _row(vt, "Hardware acceleration:", lambda p: _combo(p, [
            "cpu", "auto_prefer", "nvenc", "qsv", "amf",
        ], v_hwaccel, width=12),
            hint="cpu = safest; nvenc / qsv / amf = GPU encoder")

        _sec(vt, "Advanced Encoding Controls")
        v_keyint  = tk.StringVar(
            value="" if video.get("keyint")  is None else str(video["keyint"]),
        )
        v_bframes = tk.StringVar(
            value="" if video.get("bframes") is None else str(video["bframes"]),
        )
        v_refs    = tk.StringVar(
            value="" if video.get("refs")    is None else str(video["refs"]),
        )
        v_extra   = tk.StringVar(value=str(video.get("extra_video_params") or ""))
        _row(vt, "Keyframe interval (frames):", lambda p: _entry(p, v_keyint, width=8),
             hint="blank = auto (typically 250); controls GOP size")
        _row(vt, "B-frames (0–16):", lambda p: _entry(p, v_bframes, width=8),
             hint="blank = encoder default; more = better compression, slower")
        _row(vt, "Reference frames (1–16):", lambda p: _entry(p, v_refs, width=8),
             hint="blank = encoder default")
        _row(vt, "Extra encoder params:", lambda p: _entry(p, v_extra, width=38),
             hint="x265: key=val:key=val   e.g. ctu=32:qcomp=0.7:me=3")

        tk.Frame(vt, bg=BG, height=12).pack()

        # ── AUDIO TAB ─────────────────────────────────────────────────────────
        at = _make_tab("Audio")

        _sec(at, "Codec")
        a_mode = tk.StringVar(value=str(audio.get("mode") or "copy"))
        _row(at, "Mode:", lambda p: _combo(p, [
            "copy", "aac", "ac3", "eac3", "mp3", "opus", "flac",
        ], a_mode, width=12),
            hint="copy = bit-perfect; others re-encode audio")

        a_bitrate_var = tk.StringVar(
            value="" if audio.get("bitrate") is None else str(audio["bitrate"]),
        )
        a_bitrate_widget = [None]

        def _make_a_bitrate(p):
            w = _entry(p, a_bitrate_var, width=8)
            a_bitrate_widget[0] = w
            return w

        _row(at, "Bitrate (kbps):", _make_a_bitrate,
             hint="e.g. 192 or 320  (ignored when mode = copy)")

        def _sync_audio_mode(*_):
            state = "disabled" if a_mode.get() == "copy" else "normal"
            if a_bitrate_widget[0]:
                a_bitrate_widget[0].configure(state=state)

        a_mode.trace_add("write", _sync_audio_mode)
        _sync_audio_mode()

        _sec(at, "Channels & Sample Rate")
        a_channels    = tk.StringVar(
            value="" if audio.get("channels")    is None else str(audio["channels"]),
        )
        a_sample_rate = tk.StringVar(
            value="" if audio.get("sample_rate") is None else str(audio["sample_rate"]),
        )
        _row(at, "Channels:", lambda p: _combo(p, [
            "", "1 (mono)", "2 (stereo)", "6 (5.1)", "8 (7.1)",
        ], a_channels, width=14),
            hint="blank = keep source channel count")
        _row(at, "Sample rate (Hz):", lambda p: _combo(p, [
            "", "44100", "48000", "96000",
        ], a_sample_rate, width=10),
            hint="blank = keep source sample rate; 48000 is standard for video")

        _sec(at, "Track Selection")
        a_tracks  = tk.StringVar(value=str(audio.get("tracks") or "all"))
        a_lang    = tk.StringVar(value=str(audio.get("language") or ""))
        a_downmix = tk.BooleanVar(value=bool(audio.get("downmix", False)))
        _row(at, "Tracks:", lambda p: _combo(p, ["all", "main", "language"], a_tracks, width=12))
        _row(at, "Language filter (e.g. eng):", lambda p: _entry(p, a_lang, width=8),
             hint="blank = keep all language tracks")
        _check_row(at, "Downmix to stereo  (-ac 2)", a_downmix,
                   hint="forces stereo regardless of source")

        tk.Frame(at, bg=BG, height=12).pack()

        # ── SUBTITLES TAB ─────────────────────────────────────────────────────
        st = _make_tab("Subtitles")

        _sec(st, "Subtitle Handling")
        s_mode = tk.StringVar(value=str(subs.get("mode") or "all"))
        s_lang = tk.StringVar(value=str(subs.get("language") or ""))
        s_burn = tk.BooleanVar(value=bool(subs.get("burn", False)))
        _row(st, "Mode:", lambda p: _combo(p, [
            "all", "forced", "language", "none",
        ], s_mode, width=12))
        _row(st, "Language filter (e.g. eng):", lambda p: _entry(p, s_lang, width=8))
        _check_row(st, "Burn subtitles in (hard sub — baked permanently into the picture)", s_burn)

        tk.Frame(st, bg=BG, height=12).pack()

        # ── OUTPUT TAB ────────────────────────────────────────────────────────
        ot = _make_tab("Output")

        _sec(ot, "File & Naming")
        o_container = tk.StringVar(value=str(output_sec.get("container") or "mkv"))
        o_naming    = tk.StringVar(
            value=str(output_sec.get("naming") or "{title}_{profile}"),
        )
        o_overwrite = tk.BooleanVar(value=bool(output_sec.get("overwrite", False)))
        o_auto_inc  = tk.BooleanVar(value=bool(output_sec.get("auto_increment", True)))
        _row(ot, "Container:", lambda p: _combo(p, ["mkv", "mp4", "mov"], o_container, width=8))
        _row(ot, "Naming pattern:", lambda p: _entry(p, o_naming, width=32))
        _check_row(ot, "Auto-increment filename to avoid overwriting an existing file", o_auto_inc)
        _check_row(ot, "Overwrite existing output file", o_overwrite)

        _sec(ot, "Constraints & Metadata")
        _skip_default = constraints.get("skip_if_below_gb")
        c_skip_gb    = tk.StringVar(
            value="" if _skip_default is None else str(_skip_default),
        )
        c_skip_codec = tk.BooleanVar(
            value=bool(constraints.get("skip_if_codec_matches", False)),
        )
        m_preserve = tk.BooleanVar(value=bool(meta.get("preserve", True)))
        _row(ot, "Skip if source below (GB):", lambda p: _entry(p, c_skip_gb, width=8),
             hint="blank = encode regardless of file size")
        _check_row(
            ot,
            "Skip if source is already the target codec (avoids HEVC → HEVC re-encode)",
            c_skip_codec,
        )
        _check_row(ot, "Preserve all metadata (title, chapters, language tags)", m_preserve)

        tk.Frame(ot, bg=BG, height=12).pack()

        # ── ADVANCED TAB ──────────────────────────────────────────────────────
        advt = _make_tab("Advanced")

        _sec(advt, "Raw FFmpeg Arguments")
        adv_extra = tk.StringVar(value=str(advanced.get("extra_output_args") or ""))

        f_extra = tk.Frame(advt, bg=BG)
        f_extra.pack(fill="x", padx=10, pady=3)
        tk.Label(
            f_extra, text="Extra output args:", bg=BG, fg=FG,
            font=("Segoe UI", 10), anchor="w",
        ).pack(anchor="w")
        tk.Entry(
            f_extra, textvariable=adv_extra,
            bg=INPUT_BG, fg=FG, font=("Segoe UI", 10), relief="flat", bd=3,
        ).pack(fill="x", pady=(4, 0))
        tk.Label(
            f_extra,
            text=(
                "Appended to the FFmpeg command before the output file path.\n"
                "e.g.  -vf yadif   or   -vf scale=1920:-2   or   -movflags +faststart"
            ),
            bg=BG, fg=MUTED, font=("Segoe UI", 9), justify="left",
        ).pack(anchor="w", pady=(6, 0))

        warn_frame = tk.Frame(
            advt, bg=theme["pill_warn_bg"],
            highlightthickness=1, highlightbackground=theme["pill_warn_border"],
        )
        warn_frame.pack(fill="x", padx=10, pady=(14, 0))
        tk.Label(
            warn_frame,
            text=(
                "Invalid or incompatible args will cause the encode to fail. "
                "Test on a short clip before running the full file."
            ),
            bg=theme["pill_warn_bg"], fg=WARN, font=("Segoe UI", 9),
            wraplength=780, justify="left",
        ).pack(padx=10, pady=8)

        tk.Frame(advt, bg=BG, height=12).pack()

        # ── Collect all fields into a profile dict ─────────────────────────────
        def _collect():
            try:
                crf_val = max(0, min(51, int(v_crf.get())))
            except (ValueError, tk.TclError):
                crf_val = 18

            def _int_or_none(s):
                t = str(s or "").strip()
                try:
                    return int(float(t)) if t else None
                except ValueError:
                    return None

            def _float_or_none(s):
                t = str(s or "").strip()
                try:
                    return float(t) if t else None
                except ValueError:
                    return None

            def _str_or_none(s):
                return str(s or "").strip() or None

            mode = v_mode.get()

            # Parse "2 (stereo)" → 2
            ch_raw = a_channels.get().strip()
            ch_val = None
            if ch_raw:
                try:
                    ch_val = int(ch_raw.split()[0])
                except ValueError:
                    pass

            profile_data = {
                "video": {
                    "codec":              v_codec.get(),
                    "mode":               mode,
                    "crf":                crf_val if mode == "crf"     else None,
                    "bitrate":            _int_or_none(v_bitrate.get()) if mode == "bitrate" else None,
                    "preset":             v_preset.get(),
                    "hw_accel":           v_hwaccel.get(),
                    "tune":               _str_or_none(v_tune.get()),
                    "video_profile":      _str_or_none(v_vid_profile.get()),
                    "pix_fmt":            _str_or_none(v_pix_fmt.get()),
                    "keyint":             _int_or_none(v_keyint.get()),
                    "bframes":            _int_or_none(v_bframes.get()),
                    "refs":               _int_or_none(v_refs.get()),
                    "extra_video_params": _str_or_none(v_extra.get()),
                },
                "audio": {
                    "mode":        a_mode.get(),
                    "language":    _str_or_none(a_lang.get()),
                    "tracks":      a_tracks.get(),
                    "bitrate":     _int_or_none(a_bitrate_var.get()),
                    "channels":    ch_val,
                    "sample_rate": _int_or_none(a_sample_rate.get()),
                    "downmix":     a_downmix.get(),
                },
                "subtitles": {
                    "mode":     s_mode.get(),
                    "burn":     s_burn.get(),
                    "language": _str_or_none(s_lang.get()),
                },
                "output": {
                    "container":      o_container.get(),
                    "naming":         o_naming.get().strip() or "{title}_{profile}",
                    "overwrite":      o_overwrite.get(),
                    "auto_increment": o_auto_inc.get(),
                },
                "constraints": {
                    "skip_if_below_gb":      _float_or_none(c_skip_gb.get()),
                    "skip_if_codec_matches": c_skip_codec.get(),
                },
                "metadata": {
                    "preserve": m_preserve.get(),
                },
                "advanced": {
                    "extra_output_args": _str_or_none(adv_extra.get()),
                },
            }
            return profile_data, crf_val, v_preset.get()

        # ── Bottom buttons ─────────────────────────────────────────────────────
        btn_bar = tk.Frame(
            dlg, bg=CARD,
            highlightthickness=1, highlightbackground=theme["panel_border"],
        )
        btn_bar.pack(fill="x", padx=18, pady=(0, 14))
        btn_inner = tk.Frame(btn_bar, bg=CARD)
        btn_inner.pack(fill="x", padx=10, pady=8)

        def _apply_once():
            profile_data, crf_val, preset_val = _collect()
            dlg.destroy()
            on_apply(profile_data, crf_val, preset_val)

        def _save_and_apply():
            name_dlg = tk.Toplevel(dlg)
            name_dlg.title("Save as Profile")
            name_dlg.configure(bg=BG)
            name_dlg.geometry("440x160")
            name_dlg.transient(dlg)
            name_dlg.grab_set()
            name_dlg.lift()
            name_dlg.focus_force()

            tk.Label(
                name_dlg, text="Profile name:",
                bg=BG, fg=FG, font=("Segoe UI", 10),
            ).pack(padx=18, pady=(18, 4), anchor="w")
            name_var = tk.StringVar()
            name_entry = tk.Entry(
                name_dlg, textvariable=name_var,
                bg=CARD, fg=FG, font=("Segoe UI", 10), relief="flat", bd=3, width=46,
            )
            name_entry.pack(padx=18, fill="x")
            name_entry.focus()
            err_var = tk.StringVar()
            tk.Label(
                name_dlg, textvariable=err_var,
                bg=BG, fg=theme["pill_error_border"], font=("Segoe UI", 9),
            ).pack(padx=18, anchor="w")

            def _do_save():
                name = name_var.get().strip()
                if not name:
                    err_var.set("Enter a name for the profile.")
                    return
                profile_data, crf_val, preset_val = _collect()
                if not self._confirm_profile_hdr_metadata_save(
                    profile_data,
                    name_dlg,
                ):
                    return
                try:
                    loader = self._get_transcode_profile_loader()
                    loader.add_profile(name, profile_data)
                except Exception as exc:
                    err_var.set(f"Could not save: {exc}")
                    return
                name_dlg.destroy()
                dlg.destroy()
                on_apply(profile_data, crf_val, preset_val)

            save_row = tk.Frame(name_dlg, bg=BG)
            save_row.pack(fill="x", padx=18, pady=(8, 0))
            tk.Button(
                save_row, text="Save & Apply", command=_do_save,
                bg=theme["green"], fg=theme["text"], font=("Segoe UI", 10, "bold"), relief="flat",
            ).pack(side="left", padx=(0, 8))
            tk.Button(
                save_row, text="Cancel", command=name_dlg.destroy,
                bg=INPUT_BG, fg=FG, font=("Segoe UI", 10), relief="flat",
            ).pack(side="left")
            name_entry.bind("<Return>", lambda e: _do_save())

        tk.Button(
            btn_inner, text="Apply Once", command=_apply_once,
            bg=theme["green"], fg=theme["text"], font=("Segoe UI", 10, "bold"), relief="flat",
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            btn_inner, text="Save as Profile & Apply", command=_save_and_apply,
            bg=theme["blue"], fg=theme["text"], font=("Segoe UI", 10, "bold"), relief="flat",
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            btn_inner, text="Cancel", command=dlg.destroy,
            bg=INPUT_BG, fg=MUTED, font=("Segoe UI", 10), relief="flat",
        ).pack(side="right")

    def _resolve_transcode_backend_path(self, backend):
        backend_key = str(backend or "").strip().lower()
        if backend_key == "handbrake":
            resolved = resolve_handbrake(
                os.path.normpath(str(self.cfg.get("handbrake_path", "") or "")),
                allow_path_lookup=self._allow_path_tool_resolution(),
            )
            backend_label = _transcode_backend_label("handbrake")
        else:
            resolved = resolve_ffmpeg(
                os.path.normpath(str(self.cfg.get("ffmpeg_path", "") or "")),
                allow_path_lookup=self._allow_path_tool_resolution(),
            )
            backend_label = _transcode_backend_label("ffmpeg")

        if resolved.path:
            return (
                resolved.path,
                f"Using {backend_label} ({resolved.source}): {resolved.path}",
            )

        if resolved.suggestion_path:
            detail = resolved.error or (
                f"Configured {backend_label} executable is unavailable."
            )
            detail += (
                f"\n\nUsing {backend_label} ({resolved.suggestion_source}): "
                f"{resolved.suggestion_path}"
            )
            return resolved.suggestion_path, detail

        detail = resolved.error or f"{backend_label} executable not found."
        detail += "\n\nSet it in Settings > Paths."
        return "", detail

    def _allow_path_tool_resolution(self) -> bool:
        return (
            sys.platform == "win32"
            and bool(self.cfg.get("opt_allow_path_tool_resolution", False))
        )

    def _resolve_ffprobe_tool(self):
        return resolve_ffprobe(
            os.path.normpath(str(self.cfg.get("ffprobe_path", "") or "")),
            allow_path_lookup=self._allow_path_tool_resolution(),
        )

    def _get_transcode_profile_loader(self):
        profile_path = os.path.join(get_config_dir(), TRANSCODE_PROFILE_FILENAME)
        return ProfileLoader(profile_path)

    @staticmethod
    def _format_expert_profile_value(value):
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    @staticmethod
    def _make_expert_var_handle(var, kind="value"):
        return {"var": var, "kind": kind}

    @staticmethod
    def _expert_var_get(handle):
        if isinstance(handle, dict):
            return handle["var"].get()
        return handle.get()

    def _expert_var_set(self, handle, value):
        if isinstance(handle, dict):
            var = handle["var"]
            if handle.get("kind") == "bool":
                var.set(bool(value))
            else:
                var.set(self._format_expert_profile_value(value))
            return
        handle.set(self._format_expert_profile_value(value))

    def _expert_var_matches(self, handle, expected_value):
        if isinstance(handle, dict) and handle.get("kind") == "bool":
            return bool(handle["var"].get()) == bool(expected_value)
        current_value = self._expert_var_get(handle)
        return (
            str(current_value if current_value is not None else "").strip()
            == self._format_expert_profile_value(expected_value)
        )

    @staticmethod
    def _parse_expert_profile_value(field_name, raw_value, expected_type):
        raw_text = str(raw_value if raw_value is not None else "").strip()
        allowed_types = (
            expected_type
            if isinstance(expected_type, tuple) else
            (expected_type,)
        )
        allows_none = type(None) in allowed_types
        non_none_types = tuple(
            field_type
            for field_type in allowed_types
            if field_type is not type(None)
        )

        if not raw_text:
            if allows_none:
                return None
            if str in non_none_types:
                raise ValueError(f"{field_name} cannot be blank.")
            raise ValueError(f"{field_name} requires a value.")

        if bool in non_none_types:
            normalized = raw_text.lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
            raise ValueError(
                f"{field_name} must be true/false, yes/no, on/off, or 1/0."
            )

        if int in non_none_types and float in non_none_types:
            try:
                numeric = float(raw_text)
            except ValueError as exc:
                raise ValueError(f"{field_name} must be a number.") from exc
            return int(numeric) if numeric.is_integer() else numeric

        if int in non_none_types:
            try:
                return int(raw_text)
            except ValueError as exc:
                raise ValueError(f"{field_name} must be an integer.") from exc

        if float in non_none_types:
            try:
                return float(raw_text)
            except ValueError as exc:
                raise ValueError(f"{field_name} must be a number.") from exc

        if str in non_none_types:
            return raw_text

        return raw_text

    def _collect_expert_profile_data(
        self,
        base_profile_data,
        expert_vars,
        profile_name,
    ):
        profile_data = normalize_profile_data(base_profile_data or {})
        for section_name, schema in PROFILE_SCHEMA.items():
            section_vars = expert_vars.get(section_name, {})
            for key, expected_type in schema.items():
                var = section_vars.get(key)
                if var is None:
                    continue
                field_name = f"{section_name}.{key}"
                profile_data[section_name][key] = self._parse_expert_profile_value(
                    field_name,
                    self._expert_var_get(var),
                    expected_type,
                )
        TranscodeProfile(profile_name, profile_data)
        return profile_data

    def _save_expert_profile_data(self, profile_name, profile_data):
        loader = self._get_transcode_profile_loader()
        target_name = str(profile_name or loader.default or "").strip()
        if not target_name:
            target_name = "Balanced (Recommended)"
        normalized = normalize_profile_data(profile_data)
        loader.profiles[target_name] = TranscodeProfile(target_name, normalized)
        if not loader.default:
            loader.default = target_name
        loader.save()
        return target_name

    def _persist_settings_and_profile(
        self,
        staged_cfg,
        *,
        expert_profile_name=None,
        expert_profile_data=None,
    ):
        saved_name = None
        profile_loader = None
        profile_snapshot = None

        if expert_profile_data is not None:
            profile_loader = self._get_transcode_profile_loader()
            profile_snapshot = {
                "profiles": {
                    name: normalize_profile_data(profile.to_dict())
                    for name, profile in profile_loader.profiles.items()
                },
                "default": profile_loader.default,
            }
            saved_name = self._save_expert_profile_data(
                expert_profile_name,
                expert_profile_data,
            )

        try:
            save_config(staged_cfg)
        except Exception:
            if profile_loader is not None and profile_snapshot is not None:
                try:
                    profile_loader.profiles = {
                        name: TranscodeProfile(name, profile_data)
                        for name, profile_data in profile_snapshot["profiles"].items()
                    }
                    profile_loader.default = profile_snapshot["default"]
                    profile_loader.save()
                except Exception as rollback_exc:
                    logger = getattr(getattr(self, "controller", None), "log", None)
                    if callable(logger):
                        logger(
                            "Settings rollback failed after config save error: "
                            f"{rollback_exc}"
                        )
            raise

        return saved_name

    def _create_expert_profile(self, profile_name, profile_data=None):
        loader = self._get_transcode_profile_loader()
        target_name = str(profile_name or "").strip()
        if not target_name:
            raise ProfileValidationError("Enter a profile name.")
        if target_name in loader.profiles:
            raise ProfileValidationError(
                f"Profile already exists: {target_name}"
            )
        normalized = normalize_profile_data(profile_data or {})
        loader.add_profile(target_name, normalized)
        return target_name

    def _duplicate_expert_profile(
        self,
        source_name,
        new_name,
        profile_data=None,
    ):
        loader = self._get_transcode_profile_loader()
        source_profile_name = str(source_name or "").strip()
        target_name = str(new_name or "").strip()
        if not source_profile_name or source_profile_name not in loader.profiles:
            raise ProfileValidationError(
                f"Unknown transcode profile: {source_profile_name}"
            )
        if not target_name:
            raise ProfileValidationError("Enter a profile name.")
        if target_name in loader.profiles:
            raise ProfileValidationError(
                f"Profile already exists: {target_name}"
            )
        duplicated_data = normalize_profile_data(
            profile_data or loader.get_profile(source_profile_name).to_dict()
        )
        loader.add_profile(target_name, duplicated_data)
        return target_name

    def _delete_expert_profile(self, profile_name):
        loader = self._get_transcode_profile_loader()
        target_name = str(profile_name or "").strip()
        if not target_name or target_name not in loader.profiles:
            raise ProfileValidationError(
                f"Unknown transcode profile: {target_name}"
            )
        if len(loader.profiles) <= 1:
            raise ProfileValidationError(
                "At least one transcode profile must remain."
            )
        loader.delete_profile(target_name)
        next_name = loader.default or next(iter(loader.profiles), None)
        if not next_name:
            raise ProfileValidationError("No transcode profiles are available.")
        return next_name

    def _set_default_expert_profile(self, profile_name):
        loader = self._get_transcode_profile_loader()
        target_name = str(profile_name or loader.default or "").strip()
        if not target_name:
            raise ProfileValidationError("No transcode profiles are available.")
        if target_name not in loader.profiles:
            raise ProfileValidationError(
                f"Unknown transcode profile: {target_name}"
            )
        loader.set_default(target_name)
        return target_name

    def _load_expert_profile_snapshot(self, profile_name=None):
        loader = self._get_transcode_profile_loader()
        profile_names = list(loader.profiles)
        default_name = loader.default or (
            profile_names[0] if profile_names else None
        )
        selected_name = str(profile_name or "").strip() or default_name
        if not selected_name:
            raise ProfileValidationError("No transcode profiles are available.")
        if selected_name not in loader.profiles:
            raise ProfileValidationError(
                f"Unknown transcode profile: {selected_name}"
            )
        profile = loader.get_profile(selected_name)
        return {
            "name": selected_name,
            "default_name": default_name,
            "names": profile_names,
            "data": normalize_profile_data(profile.to_dict()),
        }

    def _confirm_profile_hdr_metadata_save(self, profile_data, parent):
        hdr_markers = {"colorprim=", "transfer=", "colormatrix=", "hdr-opt="}
        extra = (profile_data.get("video") or {}).get("extra_video_params") or ""
        if not any(marker in extra for marker in hdr_markers):
            return True
        return ask_yes_no(
            "Source-specific HDR settings",
            "The 'Extra encoder params' field contains HDR color metadata "
            "(e.g. colorprim=bt2020, transfer=smpte2084) that was seeded "
            "from this specific file.\n\n"
            "Saving it as a reusable profile will embed those HDR tags "
            "into every file encoded with it, including SDR content.\n\n"
            "Use 'Apply Once' to keep it file-specific, or clear "
            "'Extra encoder params' before saving a general profile.\n\n"
            "Save with HDR metadata anyway?",
            icon="warning",
            parent=parent,
        )

    def _confirm_discard_dirty_expert_changes(
        self,
        profile_data,
        expert_vars,
        prompt,
        parent,
    ):
        if not expert_vars:
            return True
        if not self._expert_profile_form_is_dirty(profile_data, expert_vars):
            return True
        return ask_yes_no(
            "Discard Expert Changes",
            prompt,
            parent=parent,
        )

    def _populate_expert_profile_vars(self, expert_vars, profile_data):
        for section_name, schema in PROFILE_SCHEMA.items():
            section_vars = expert_vars.get(section_name, {})
            values = (profile_data or {}).get(section_name, {})
            for key in schema:
                var = section_vars.get(key)
                if var is None:
                    continue
                self._expert_var_set(var, values.get(key))

    def _expert_profile_form_is_dirty(self, profile_data, expert_vars):
        expected = normalize_profile_data(profile_data or {})
        for section_name, schema in PROFILE_SCHEMA.items():
            section_vars = expert_vars.get(section_name, {})
            values = expected.get(section_name, {})
            for key in schema:
                var = section_vars.get(key)
                if var is None:
                    continue
                if not self._expert_var_matches(var, values.get(key)):
                    return True
        return False

    def _summarize_expert_profile(self, profile_data):
        return summarize_profile(normalize_profile_data(profile_data or {}))

    def _open_transcode_queue_builder(
        self,
        scan_root,
        selected_paths,
        backend="ffmpeg",
        selected_entries=None,
    ):
        backend_key = str(backend or "").strip().lower()
        if backend_key not in {"ffmpeg", "handbrake"}:
            backend_key = "ffmpeg"

        normalized_paths = [
            os.path.normpath(path)
            for path in selected_paths
            if str(path or "").strip()
        ]
        if not normalized_paths:
            self.show_info(
                "Build Queue",
                "Select at least one MKV file before building a queue.",
            )
            return

        try:
            profile_loader = self._get_transcode_profile_loader()
        except Exception as exc:
            self.show_error(
                "Build Queue",
                f"Could not load transcode profiles:\n{exc}",
            )
            return

        backend_choices = {
            "FFmpeg": "ffmpeg",
            "HandBrake": "handbrake",
        }
        backend_key_to_label = {value: key for key, value in backend_choices.items()}
        backend_var = tk.StringVar(
            value=backend_key_to_label.get(backend_key, "FFmpeg")
        )
        output_root_var = tk.StringVar(
            value=_suggest_transcode_output_root(scan_root, backend_key)
        )
        executable_var = tk.StringVar()
        option_label_var = tk.StringVar()
        option_var = tk.StringVar()
        source_mode_help_var = tk.StringVar()
        start_button_var = tk.StringVar()
        status_var = tk.StringVar(
            value=f"Ready to queue {len(normalized_paths)} MKV file(s)."
        )
        suggested_output_state = {"value": output_root_var.get()}
        colors = self._theme

        profile_names = list(profile_loader.profiles)
        default_profile_name = profile_loader.default or (
            profile_names[0] if profile_names else ""
        )

        win = tk.Toplevel(self)
        win.title("Transcode Queue Builder")
        win.configure(bg=colors["window_bg"])
        win.geometry("980x620")
        win.lift()
        win.focus_force()

        tk.Label(
            win,
            text=f"Queue {len(normalized_paths)} MKV file(s) for FFmpeg or HandBrake",
            bg=colors["window_bg"],
            fg=colors["title"],
            font=("Segoe UI", 13, "bold"),
        ).pack(padx=18, pady=(18, 6), anchor="w")
        tk.Label(
            win,
            text="Choose a backend, review the output layout, and keep the selected MKVs organized before sending them to the encoder.",
            bg=colors["window_bg"],
            fg=colors["muted"],
            font=("Segoe UI", 10),
            wraplength=920,
            justify="left",
        ).pack(padx=18, pady=(0, 10), anchor="w")

        backend_row = tk.Frame(win, bg=colors["window_bg"])
        backend_row.pack(fill="x", padx=18, pady=(0, 8))
        tk.Label(
            backend_row,
            text="Backend:",
            bg=colors["window_bg"],
            fg=colors["text"],
            font=("Segoe UI", 10, "bold"),
            width=14,
            anchor="w",
        ).pack(side="left")
        ttk.Combobox(
            backend_row,
            textvariable=backend_var,
            values=list(backend_choices),
            state="readonly",
            width=20,
        ).pack(side="left", padx=(0, 8))

        executable_row = tk.Frame(win, bg=colors["window_bg"])
        executable_row.pack(fill="x", padx=18, pady=(0, 8))
        tk.Label(
            executable_row,
            text="Executable:",
            bg=colors["window_bg"],
            fg=colors["text"],
            font=("Segoe UI", 10, "bold"),
            width=14,
            anchor="w",
        ).pack(side="left")
        tk.Label(
            executable_row,
            textvariable=executable_var,
            bg=colors["window_bg"],
            fg=colors["muted"],
            font=("Segoe UI", 10),
            wraplength=760,
            justify="left",
            anchor="w",
        ).pack(side="left", fill="x", expand=True)

        output_row = tk.Frame(win, bg=colors["window_bg"])
        output_row.pack(fill="x", padx=18, pady=(0, 10))
        tk.Label(
            output_row,
            text="Output root:",
            bg=colors["window_bg"],
            fg=colors["text"],
            font=("Segoe UI", 10, "bold"),
            width=14,
            anchor="w",
        ).pack(side="left")
        tk.Entry(
            output_row,
            textvariable=output_root_var,
            bg=colors["surface_deep"],
            fg=colors["text"],
            font=("Segoe UI", 10),
            relief="flat",
            bd=3,
        ).pack(side="left", fill="x", expand=True, padx=(0, 8))

        def _selected_backend_key():
            return backend_choices.get(backend_var.get(), "ffmpeg")

        def _browse_output_root():
            current_output = output_root_var.get().strip()
            initial_dir = current_output or os.path.dirname(scan_root) or scan_root
            chosen = self.ask_directory(
                "Build Queue",
                "Choose an output folder",
                initialdir=initial_dir,
            )
            if chosen:
                output_root_var.set(os.path.normpath(chosen))

        def _reveal_output_root():
            current_output = output_root_var.get().strip()
            if not current_output:
                status_var.set("Choose an output folder first.")
                return
            normalized_output = os.path.normpath(current_output)
            if not os.path.isdir(normalized_output):
                status_var.set("Output folder will be created when the queue starts.")
                return
            self._open_path_in_explorer(normalized_output)

        tk.Button(
            output_row,
            text="Browse",
            command=_browse_output_root,
            bg=colors["toolbar_button"],
            fg=colors["toolbar_button_text"],
            font=("Segoe UI", 10),
            relief="flat",
        ).pack(side="left", padx=(0, 6))
        tk.Button(
            output_row,
            text="Reveal",
            command=_reveal_output_root,
            bg=colors["toolbar_button"],
            fg=colors["toolbar_button_text"],
            font=("Segoe UI", 10),
            relief="flat",
        ).pack(side="left")

        option_row = tk.Frame(win, bg=colors["window_bg"])
        option_row.pack(fill="x", padx=18, pady=(0, 10))
        tk.Label(
            option_row,
            textvariable=option_label_var,
            bg=colors["window_bg"],
            fg=colors["text"],
            font=("Segoe UI", 10, "bold"),
            width=14,
            anchor="w",
        ).pack(side="left")
        option_menu = ttk.Combobox(
            option_row,
            textvariable=option_var,
            state="readonly",
            width=36,
        )
        option_menu.pack(side="left", padx=(0, 8))
        tk.Label(
            win,
            textvariable=source_mode_help_var,
            bg=colors["window_bg"],
            fg=colors["muted"],
            font=("Segoe UI", 9),
            wraplength=920,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=18, pady=(0, 10))

        tk.Label(
            win,
            text="Queue preview",
            bg=colors["window_bg"],
            fg=colors["title"],
            font=("Segoe UI", 11, "bold"),
        ).pack(padx=18, pady=(4, 4), anchor="w")

        preview_frame = tk.Frame(win, bg=colors["window_bg"])
        preview_frame.pack(fill="both", expand=True, padx=18, pady=(0, 8))
        preview_tree = ttk.Treeview(
            preview_frame,
            columns=("source", "output"),
            show="headings",
            style="Disc.Treeview",
        )
        preview_tree.heading("source", text="Source (relative)")
        preview_tree.heading("output", text="Output (relative)")
        preview_tree.column("source", width=360, anchor="w")
        preview_tree.column("output", width=520, anchor="w")
        preview_scroll = ttk.Scrollbar(
            preview_frame, orient="vertical", command=preview_tree.yview
        )
        preview_tree.configure(yscrollcommand=preview_scroll.set)
        preview_tree.pack(side="left", fill="both", expand=True)
        preview_scroll.pack(side="right", fill="y")

        def _refresh_preview(*_args):
            current_output = output_root_var.get().strip()
            plans = _build_transcode_plan(scan_root, normalized_paths, current_output)
            preview_tree.delete(*preview_tree.get_children(""))
            for idx, plan in enumerate(plans):
                preview_tree.insert(
                    "",
                    "end",
                    iid=f"plan_{idx}",
                    values=(
                        plan["relative_path"],
                        plan["output_relative_path"],
                    ),
                )
            if plans:
                status_var.set(
                    f"Queue preview ready: {len(plans)} MKV file(s) preserving subfolders."
                )
            else:
                status_var.set("Choose an output folder to build the queue preview.")

        def _refresh_option_help(*_args):
            current_backend = _selected_backend_key()
            if current_backend == "ffmpeg":
                selected_profile = option_var.get().strip() or default_profile_name
                if selected_profile not in profile_names:
                    selected_profile = default_profile_name
                try:
                    profile_summary = summarize_profile(
                        profile_loader.get_profile(selected_profile)
                    )
                except Exception:
                    profile_summary = "Profile details unavailable."
                current_source_mode = normalize_ffmpeg_source_mode(
                    self.cfg.get(
                        "opt_ffmpeg_source_mode",
                        FFMPEG_SOURCE_MODE_SAFE_COPY,
                    )
                )
                source_mode_help_var.set(
                    f"{profile_summary}\n"
                    f"Source handling: {_ffmpeg_source_mode_label(current_source_mode)}. "
                    f"{describe_ffmpeg_source_mode(current_source_mode)} "
                    "Change this in Settings > Advanced."
                )
            else:
                selected_preset = option_var.get().strip() or HANDBRAKE_PRESETS[0]
                source_mode_help_var.set(
                    f"Preset: {selected_preset}. HandBrake preset controls the encode rules for video, audio, subtitles, and output.\n"
                    "Source handling: HandBrake reads the selected source file directly and writes a separate output file."
                )

        def _refresh_backend_state(*_args):
            current_backend = _selected_backend_key()
            backend_label = _transcode_backend_label(current_backend)
            suggested_output = _suggest_transcode_output_root(scan_root, current_backend)
            current_output = output_root_var.get().strip()
            previous_suggested = suggested_output_state["value"]
            if (
                not current_output or
                os.path.normcase(os.path.normpath(current_output)) ==
                os.path.normcase(os.path.normpath(previous_suggested))
            ):
                output_root_var.set(suggested_output)
            suggested_output_state["value"] = suggested_output

            _chosen_executable, executable_status = self._resolve_transcode_backend_path(
                current_backend
            )
            executable_var.set(executable_status)

            if current_backend == "ffmpeg":
                option_label_var.set("FFmpeg profile:")
                option_menu.configure(values=profile_names)
                selected_value = option_var.get().strip()
                if selected_value not in profile_names:
                    option_var.set(default_profile_name)
            else:
                option_label_var.set("HandBrake preset:")
                option_menu.configure(values=HANDBRAKE_PRESETS)
                selected_value = option_var.get().strip()
                if selected_value not in HANDBRAKE_PRESETS:
                    option_var.set(HANDBRAKE_PRESETS[0])

            start_button_var.set(f"Start {backend_label} Queue")
            _refresh_option_help()

        output_root_var.trace_add("write", _refresh_preview)
        backend_var.trace_add("write", _refresh_backend_state)
        option_var.trace_add("write", _refresh_option_help)
        _refresh_backend_state()
        _refresh_preview()

        footer = tk.Frame(win, bg=colors["window_bg"])
        footer.pack(fill="x", padx=18, pady=(0, 10))
        tk.Label(
            footer,
            textvariable=status_var,
            bg=colors["window_bg"],
            fg=colors["title"],
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left")

        button_row = tk.Frame(win, bg=colors["window_bg"])
        button_row.pack(fill="x", padx=18, pady=(0, 18))

        def _start_queue():
            current_backend = _selected_backend_key()
            backend_label = _transcode_backend_label(current_backend)
            output_root = output_root_var.get().strip()
            if not output_root:
                status_var.set("Choose an output folder first.")
                return
            if os.path.isfile(output_root):
                status_var.set("The output root points to a file. Choose a folder instead.")
                return

            chosen_executable, chosen_status = self._resolve_transcode_backend_path(
                current_backend
            )
            executable_var.set(chosen_status)
            if not chosen_executable:
                self.show_error(
                    "Build Queue",
                    f"{chosen_status}\n\nSet the executable in Settings > Paths.",
                )
                return

            plans = _build_transcode_plan(scan_root, normalized_paths, output_root)
            if not plans:
                status_var.set("Nothing to queue. Select at least one MKV file.")
                return

            try:
                os.makedirs(output_root, exist_ok=True)
            except Exception as exc:
                self.show_error(
                    "Build Queue",
                    f"Could not create the output folder:\n{exc}",
                )
                return

            ffmpeg_source_mode = normalize_ffmpeg_source_mode(
                self.cfg.get("opt_ffmpeg_source_mode", FFMPEG_SOURCE_MODE_SAFE_COPY)
            )

            try:
                build_result = build_queue_jobs(
                    plans=plans,
                    profile_loader=profile_loader,
                    backend=current_backend,
                    option_value=option_var.get().strip(),
                    ffmpeg_source_mode=ffmpeg_source_mode,
                    selected_entries=selected_entries,
                    default_handbrake_preset=HANDBRAKE_PRESETS[0],
                )
            except Exception as exc:
                self.show_error(
                    "Build Queue",
                    f"Could not build the queue:\n{exc}",
                )
                return

            jobs = build_result.jobs
            if not jobs:
                status_var.set("No transcode jobs were added to the queue.")
                return

            try:
                for directory in required_output_directories(jobs, output_root):
                    os.makedirs(directory, exist_ok=True)
            except Exception as exc:
                self.show_error(
                    "Build Queue",
                    f"Could not prepare the output folders:\n{exc}",
                )
                return

            log_dir = os.path.join(get_config_dir(), "transcode_logs")
            ffmpeg_path = (
                chosen_executable if current_backend == "ffmpeg"
                else self._resolve_transcode_backend_path("ffmpeg")[0]
            )
            if current_backend == "ffmpeg" and not self._ffmpeg_version_ok(ffmpeg_path):
                return
            handbrake_path = (
                chosen_executable if current_backend == "handbrake"
                else self._resolve_transcode_backend_path("handbrake")[0]
            )
            transcode_queue = build_transcode_queue(
                jobs=jobs,
                log_dir=log_dir,
                ffmpeg_exe=ffmpeg_path,
                ffprobe_exe=self._resolve_ffprobe_tool().path,
                handbrake_exe=handbrake_path,
                ffmpeg_source_mode=ffmpeg_source_mode,
                temp_root=os.path.normpath(
                    self.cfg.get("temp_folder", DEFAULTS["temp_folder"])
                ),
            )

            win.destroy()
            self._run_transcode_queue(
                transcode_queue,
                backend_label,
                os.path.normpath(output_root),
                queue_detail=build_result.queue_detail,
            )

        tk.Button(
            button_row,
            textvariable=start_button_var,
            command=_start_queue,
            bg=colors["green"],
            fg=colors["text"],
            font=("Segoe UI", 10, "bold"),
            relief="flat",
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            button_row,
            text="Reveal Output Root",
            command=_reveal_output_root,
            bg=colors["toolbar_button"],
            fg=colors["toolbar_button_text"],
            font=("Segoe UI", 10),
            relief="flat",
        ).pack(side="left")
        tk.Button(
            button_row,
            text="Cancel",
            command=win.destroy,
            bg=colors["toolbar_button"],
            fg=colors["toolbar_button_muted"],
            font=("Segoe UI", 10),
            relief="flat",
        ).pack(side="right")

    def _run_transcode_queue(
        self,
        transcode_queue,
        backend_label,
        output_root,
        queue_detail="",
    ):
        total_jobs = len(transcode_queue.jobs)
        if total_jobs <= 0:
            self.show_info(
                f"{backend_label} Queue",
                "No jobs were available to run.",
            )
            return

        colors = self._theme
        bg = colors["window_bg"]
        win = tk.Toplevel(self)
        win.title(f"{backend_label} Queue Progress")
        win.configure(bg=bg)
        win.geometry("760x460")
        win.lift()
        win.focus_force()

        tk.Label(
            win,
            text=f"{backend_label} queue is running",
            bg=bg,
            fg=colors["title"],
            font=("Segoe UI", 12, "bold"),
        ).pack(padx=18, pady=(18, 6), anchor="w")
        tk.Label(
            win,
            text=f"Output root: {output_root}",
            bg=bg,
            fg=colors["muted"],
            font=("Segoe UI", 10),
            wraplength=700,
            justify="left",
        ).pack(padx=18, pady=(0, 2), anchor="w")
        if queue_detail:
            tk.Label(
                win,
                text=queue_detail,
                bg=bg,
                fg=colors["muted"],
                font=("Segoe UI", 10),
            ).pack(padx=18, pady=(0, 8), anchor="w")

        progress_var = tk.DoubleVar(value=0)
        progress_bar = ttk.Progressbar(
            win,
            variable=progress_var,
            maximum=100,
            mode="determinate",
        )
        progress_bar.pack(fill="x", padx=18, pady=(0, 8))

        status_var = tk.StringVar(
            value=f"Queued {total_jobs} job(s). Processing will continue in the background thread."
        )
        tk.Label(
            win,
            textvariable=status_var,
            bg=bg,
            fg=colors["title"],
            font=("Segoe UI", 10, "bold"),
        ).pack(padx=18, pady=(0, 8), anchor="w")

        log_text = scrolledtext.ScrolledText(
            win,
            bg=colors["surface_alt"],
            fg=colors["text"],
            insertbackground=colors["text"],
            font=("Consolas", 10),
            relief="flat",
            height=16,
            state="disabled",
        )
        log_text.pack(fill="both", expand=True, padx=18, pady=(0, 10))

        def _append_log_line(message):
            try:
                if not win.winfo_exists():
                    return
                log_text.config(state="normal")
                log_text.insert("end", f"{message}\n")
                log_text.see("end")
            except tk.TclError:
                return
            finally:
                try:
                    log_text.config(state="disabled")
                except tk.TclError:
                    return

        _append_log_line(f"Output root: {output_root}")
        _append_log_line(f"Log folder: {transcode_queue.engine.log_dir}")
        _append_log_line(f"{backend_label} queue created with {total_jobs} job(s).")
        if queue_detail:
            _append_log_line(queue_detail)

        button_row = tk.Frame(win, bg=bg)
        button_row.pack(fill="x", padx=18, pady=(0, 18))
        queue_abort_event = transcode_queue.abort_event

        def _abort_queue():
            if queue_abort_event.is_set():
                return
            transcode_queue.abort()
            abort_queue_btn.config(state="disabled")
            message = f"{backend_label} queue abort requested."
            status_var.set(message)
            _append_log_line(message)

        tk.Button(
            button_row,
            text="Open Output Folder",
            command=lambda: self._open_path_in_explorer(output_root),
            bg=colors["toolbar_button"],
            fg=colors["toolbar_button_text"],
            font=("Segoe UI", 10),
            relief="flat",
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            button_row,
            text="Open Log Folder",
            command=lambda: self._open_path_in_explorer(transcode_queue.engine.log_dir),
            bg=colors["toolbar_button"],
            fg=colors["toolbar_button_text"],
            font=("Segoe UI", 10),
            relief="flat",
        ).pack(side="left")
        abort_queue_btn = tk.Button(
            button_row,
            text="Abort Queue",
            command=_abort_queue,
            bg=colors["abort"],
            fg=colors["text"],
            font=("Segoe UI", 10, "bold"),
            relief="flat",
        )
        abort_queue_btn.pack(side="left", padx=(8, 0))
        tk.Button(
            button_row,
            text="Close",
            command=win.destroy,
            bg=colors["toolbar_button"],
            fg=colors["toolbar_button_muted"],
            font=("Segoe UI", 10),
            relief="flat",
        ).pack(side="right")

        def _feedback(message):
            def _update_ui():
                try:
                    if not win.winfo_exists():
                        return
                    status_var.set(message)
                    _append_log_line(message)
                except tk.TclError:
                    return

            self.after(0, _update_ui)

        def _progress(event):
            if not isinstance(event, dict):
                return

            overall_percent = event.get("overall_percent")
            message = str(event.get("message", "") or "").strip()

            def _update_ui():
                try:
                    if not win.winfo_exists():
                        return
                    if isinstance(overall_percent, (int, float)):
                        progress_var.set(
                            max(0.0, min(100.0, float(overall_percent)))
                        )
                    if message:
                        status_var.set(message)
                except tk.TclError:
                    return

            self.after(0, _update_ui)

        def _show_queue_result(title, message, kind):
            try:
                parent = win if win.winfo_exists() else self
            except tk.TclError:
                parent = self

            if kind == "error":
                messagebox.showerror(title, message, parent=parent)
            elif kind == "aborted":
                messagebox.showwarning(title, message, parent=parent)
            else:
                messagebox.showinfo(title, message, parent=parent)

        def _mark_progress():
            aborted = len(getattr(transcode_queue, "aborted", []))
            finished = len(transcode_queue.completed) + len(transcode_queue.failed) + aborted
            pct = (finished / total_jobs) * 100 if total_jobs else 0
            summary = (
                f"{backend_label} progress: {finished}/{total_jobs} complete "
                f"(success: {len(transcode_queue.completed)}, failed: {len(transcode_queue.failed)}, "
                f"aborted: {aborted})"
            )
            try:
                if not win.winfo_exists():
                    return
                progress_var.set(pct)
                status_var.set(summary)
                _append_log_line(summary)
            except tk.TclError:
                return

        def _finish(message, complete=True, result_kind="complete"):
            try:
                if win.winfo_exists():
                    if complete:
                        progress_var.set(100)
                    status_var.set(message)
                    _append_log_line(message)
            except tk.TclError:
                pass
            _show_queue_result(f"{backend_label} Queue Result", message, result_kind)

        def _worker():
            try:
                while transcode_queue.jobs:
                    if queue_abort_event.is_set():
                        break
                    transcode_queue.run_next(
                        feedback_cb=_feedback,
                        progress_cb=_progress,
                    )
                    self.after(0, _mark_progress)
                    if queue_abort_event.is_set():
                        break
            except Exception as exc:
                error_message = f"{backend_label} queue stopped with an unexpected error: {exc}"
                self.after(
                    0,
                    lambda: _finish(
                        error_message,
                        complete=False,
                        result_kind="error",
                    ),
                )
                return

            aborted = len(getattr(transcode_queue, "aborted", []))
            canceled_pending = 0
            if queue_abort_event.is_set():
                canceled_pending = transcode_queue.cancel_pending()
                aborted = len(getattr(transcode_queue, "aborted", []))

            if queue_abort_event.is_set() or aborted:
                summary = (
                    f"{backend_label} queue aborted. Success: {len(transcode_queue.completed)}, "
                    f"Failed: {len(transcode_queue.failed)}, Aborted: {aborted}, "
                    f"Canceled pending: {canceled_pending}"
                )
                complete = False
                result_kind = "aborted"
            else:
                summary = (
                    f"{backend_label} queue complete. Success: {len(transcode_queue.completed)}, "
                    f"Failed: {len(transcode_queue.failed)}"
                )
                complete = True
                result_kind = "complete"
            self.after(
                0,
                lambda: _finish(
                    summary,
                    complete=complete,
                    result_kind=result_kind,
                ),
            )

        threading.Thread(target=_worker, daemon=True).start()

    def _refresh_drives(self):
        def _load():
            configured = os.path.normpath(
                self.cfg.get("makemkvcon_path", "")
            )
            resolved = resolve_makemkvcon(
                configured,
                allow_path_lookup=self._allow_path_tool_resolution(),
            )
            drives = get_available_drives(resolved.path or "")
            self.after(0, lambda: self._update_drive_menu(drives))
        threading.Thread(target=_load, daemon=True).start()

    def _update_drive_menu(self, drives: list[MakeMKVDrive]):
        normalized_drives = list(drives or [])
        if not normalized_drives:
            normalized_drives = [make_default_drive()]
        self.drive_options = normalized_drives
        labels = [
            format_makemkv_drive_label(drive)
            for drive in normalized_drives
        ]
        self.drive_menu["values"] = labels
        current_idx = self.cfg.get("opt_drive_index", 0)
        for i, drive in enumerate(normalized_drives):
            if drive.index == current_idx:
                self.drive_var.set(labels[i])
                break
        else:
            if labels:
                self.drive_var.set(labels[0])

    def _on_drive_select(self, *args):
        selected = self.drive_var.get()
        for drive in self.drive_options:
            if format_makemkv_drive_label(drive) == selected:
                self.cfg["opt_drive_index"] = drive.index
                self.engine.cfg["opt_drive_index"] = drive.index
                save_config(self.cfg)
                drive_text = drive.drive_name or f"Drive {drive.index}"
                if drive.device_path:
                    drive_text += f" [{drive.device_path}]"
                if drive.disc_name:
                    drive_text += f" | disc: {drive.disc_name}"
                self.controller.log(
                    f"Drive selected: {drive_text} ({drive.usability_state})"
                )
                break

    def copy_log_to_clipboard(self):
        try:
            content = self.log_text.get("1.0", "end-1c")
            if not content.strip():
                self.controller.log("Log is empty — nothing to copy.")
                return
            self.clipboard_clear()
            self.clipboard_append(content)
            # Ensure clipboard ownership is committed on Windows.
            self.update_idletasks()
            self.controller.log("Log copied to clipboard.")
        except Exception as e:
            self.controller.log(f"Could not copy log: {e}")

    def _launch_downloaded_update(self, downloaded_path):
        """Delegate to update_ui.launch_downloaded_update."""
        launch_downloaded_update(self, downloaded_path)

    def check_for_updates(self):
        """Delegate to update_ui.check_for_updates."""
        check_for_updates(self)

    def _show_input_bar(self, label, initial_value=""):
        self.input_label_var.set(label)
        self.input_var.set(initial_value or "")
        self._input_active = True
        pack_kwargs = getattr(
            self,
            "_input_bar_pack_kwargs",
            {"fill": "x", "padx": 36, "pady": (10, 0)},
        )
        before = getattr(self, "_session_controls_frame", None)
        if before is not None:
            self.input_bar.pack(before=before, **pack_kwargs)
        else:
            self.input_bar.pack(**pack_kwargs)
        if initial_value:
            self.input_field.selection_range(0, "end")
        self.input_field.focus_set()

    def _hide_input_bar(self):
        self._input_active = False
        self.input_bar.pack_forget()
        self.input_var.set("")

    def _confirm_input(self):
        if not self._input_active:
            return
        val = self.input_var.get().strip()
        self._input_result = val
        self._input_event.set()

    def _skip_input(self):
        if not self._input_active:
            return
        self._input_result = ""
        self._input_event.set()

    def _ask_input_modal(self, label, prompt, default_value=""):
        """Show a modal text prompt and return text, empty string, or None."""
        result = [None]
        colors = self._theme
        win = tk.Toplevel(self)
        win.title(label or "Input")
        win.configure(bg=colors["surface"])
        win.grab_set()
        win.lift()
        win.focus_force()
        win.resizable(False, False)

        tk.Label(
            win,
            text=prompt,
            bg=colors["surface"],
            fg=colors["text"],
            justify="left",
            wraplength=520,
            font=("Segoe UI", 10),
        ).pack(padx=18, pady=(18, 10), anchor="w")

        value_var = tk.StringVar(value=default_value or "")
        entry = tk.Entry(
            win,
            textvariable=value_var,
            bg=colors["surface_deep"],
            fg=colors["text"],
            insertbackground=colors["text"],
            relief="flat",
            font=("Segoe UI", 11),
            width=44,
        )
        entry.pack(fill="x", padx=18, pady=(0, 14))

        button_row = tk.Frame(win, bg=colors["surface"])
        button_row.pack(padx=18, pady=(0, 18))
        finished = {"done": False}

        def finish(value):
            if finished["done"]:
                return
            finished["done"] = True
            result[0] = value
            win.destroy()

        def submit(*_args):
            finish(value_var.get().strip())

        def skip(*_args):
            finish("")

        win.protocol("WM_DELETE_WINDOW", skip)
        win.bind("<Return>", submit)
        win.bind("<Escape>", skip)

        tk.Button(
            button_row,
            text="OK",
            bg=colors["green"],
            fg=colors["text"],
            font=("Segoe UI", 10, "bold"),
            command=submit,
            relief="flat",
            width=12,
        ).pack(side="left", padx=4)
        tk.Button(
            button_row,
            text="Skip",
            bg=colors["toolbar_button"],
            fg=colors["toolbar_button_text"],
            font=("Segoe UI", 10),
            command=skip,
            relief="flat",
            width=12,
        ).pack(side="left", padx=4)

        timeout_seconds = self._get_user_prompt_timeout_seconds()
        if timeout_seconds is not None:
            win.after(int(timeout_seconds * 1000), lambda: finish(None))

        def _poll_abort():
            if finished["done"]:
                return
            if self.engine.abort_event.is_set():
                finish(None)
                return
            try:
                win.after(100, _poll_abort)
            except tk.TclError:
                return

        win.after(100, _poll_abort)
        if default_value:
            entry.selection_range(0, "end")
        entry.focus_set()
        win.wait_window()
        return result[0]

    def ask_input(self, label, prompt,
                  default_value=""):
        """Show a modal input popup and wait for the entered value."""
        lock = getattr(self, "_input_lock", None)
        if lock is None:
            lock = threading.Lock()

        with lock:
            value = self._run_modal_dialog(
                lambda: self._ask_input_modal(
                    label,
                    prompt,
                    default_value=default_value,
                )
            )
            if value:
                self.append_log(
                    f"[{datetime.now().strftime('%H:%M:%S')}] "
                    f"{label}: {value}"
                )
            elif value == "":
                self.append_log(
                    f"[{datetime.now().strftime('%H:%M:%S')}] "
                    f"{label}: (skipped)"
                )
            return value

    def _ask_yesno_modal(self, prompt):
        """Show a modal Yes/No popup and return the chosen boolean."""
        result = [False]
        colors = self._theme
        win = tk.Toplevel(self)
        win.title("Confirm")
        win.configure(bg=colors["surface"])
        win.grab_set()
        win.lift()
        win.focus_force()
        win.resizable(False, False)

        tk.Label(
            win,
            text=prompt,
            bg=colors["surface"],
            fg=colors["text"],
            justify="left",
            wraplength=520,
            font=("Segoe UI", 10),
        ).pack(padx=18, pady=(18, 10), anchor="w")

        button_row = tk.Frame(win, bg=colors["surface"])
        button_row.pack(padx=18, pady=(0, 18))
        finished = {"done": False}

        def finish(answer):
            if finished["done"]:
                return
            finished["done"] = True
            result[0] = bool(answer)
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", lambda: finish(False))
        win.bind("<Escape>", lambda _e: finish(False))
        win.bind("<Return>", lambda _e: finish(True))

        tk.Button(
            button_row,
            text="Yes",
            bg=colors["green"],
            fg=colors["text"],
            font=("Segoe UI", 10, "bold"),
            command=lambda: finish(True),
            relief="flat",
            width=12,
        ).pack(side="left", padx=4)
        tk.Button(
            button_row,
            text="No",
            bg=colors["abort"],
            fg=colors["text"],
            font=("Segoe UI", 10, "bold"),
            command=lambda: finish(False),
            relief="flat",
            width=12,
        ).pack(side="left", padx=4)

        timeout_seconds = self._get_user_prompt_timeout_seconds()
        if timeout_seconds is not None:
            win.after(int(timeout_seconds * 1000), lambda: finish(False))

        def _poll_abort():
            if finished["done"]:
                return
            if self.engine.abort_event.is_set():
                finish(False)
                return
            try:
                win.after(100, _poll_abort)
            except tk.TclError:
                return

        win.after(100, _poll_abort)
        win.wait_window()
        return result[0]

    def ask_yesno(self, prompt):
        """Show a modal Yes/No popup and wait for the answer."""
        return bool(
            self._run_modal_dialog(
                lambda: self._ask_yesno_modal(prompt)
            )
        )

    # ------------------------------------------------------------------
    # Session setup dialogs (worker-thread-safe)
    # ------------------------------------------------------------------

    def ask_movie_setup(
        self,
        default_title: str = "",
        default_year: str = "",
        default_edition: str = "",
        default_metadata_provider: str = "TMDB",
        default_metadata_id: str = "",
        default_replace_existing: bool = False,
    ):
        """Show the movie rip setup dialog and return a MovieSessionSetup or None.

        Safe to call from a worker thread — dispatches the Toplevel to the
        main thread and blocks the caller until the user confirms or cancels.
        """
        from gui.session_setup_dialog import build_movie_setup_dialog

        result = [None]
        done   = threading.Event()

        def _show():
            result[0] = build_movie_setup_dialog(
                self,
                default_title=default_title,
                default_year=default_year,
                default_edition=default_edition,
                default_metadata_provider=default_metadata_provider,
                default_metadata_id=default_metadata_id,
                default_replace_existing=default_replace_existing,
            )
            done.set()

        self.after(0, _show)
        while not done.wait(timeout=0.1):
            if self.engine.abort_event.is_set():
                return None
        return result[0]

    def ask_tv_setup(
        self,
        default_title: str = "",
        default_year: str = "",
        default_season: str = "1",
        default_starting_disc: str = "1",
        default_metadata_provider: str = "TMDB",
        default_metadata_id: str = "",
        default_episode_mapping: str = "auto",
        default_multi_episode: str = "auto",
        default_specials: str = "ask",
        default_replace_existing: bool = False,
    ):
        """Show the TV rip setup dialog and return a TVSessionSetup or None.

        Safe to call from a worker thread — dispatches the Toplevel to the
        main thread and blocks the caller until the user confirms or cancels.
        """
        from gui.session_setup_dialog import build_tv_setup_dialog

        result = [None]
        done   = threading.Event()

        def _show():
            result[0] = build_tv_setup_dialog(
                self,
                default_title=default_title,
                default_year=default_year,
                default_season=default_season,
                default_starting_disc=default_starting_disc,
                default_metadata_provider=default_metadata_provider,
                default_metadata_id=default_metadata_id,
                default_episode_mapping=default_episode_mapping,
                default_multi_episode=default_multi_episode,
                default_specials=default_specials,
                default_replace_existing=default_replace_existing,
            )
            done.set()

        self.after(0, _show)
        while not done.wait(timeout=0.1):
            if self.engine.abort_event.is_set():
                return None
        return result[0]

    def ask_dump_setup(
        self,
        default_multi_disc: bool = False,
        default_disc_name: str = "",
        default_disc_count: str = "1",
        default_custom_disc_names: str = "",
        default_batch_title: str = "",
    ):
        """Show the dump session setup dialog and return a DumpSessionSetup or None."""
        from gui.session_setup_dialog import build_dump_setup_dialog

        result = [None]
        done = threading.Event()

        def _show():
            result[0] = build_dump_setup_dialog(
                self,
                default_multi_disc=default_multi_disc,
                default_disc_name=default_disc_name,
                default_disc_count=default_disc_count,
                default_custom_disc_names=default_custom_disc_names,
                default_batch_title=default_batch_title,
            )
            done.set()

        self.after(0, _show)
        while not done.wait(timeout=0.1):
            if self.engine.abort_event.is_set():
                return None
        return result[0]

    # ------------------------------------------------------------------
    # Setup wizard step dialogs (worker-thread-safe)
    # ------------------------------------------------------------------

    def show_scan_results_step(self, classified, drive_info=None):
        """Step 1: Show scan results + classification."""
        from gui.setup_wizard import show_scan_results

        result = [None]
        done = threading.Event()

        def _show():
            result[0] = show_scan_results(self, classified, drive_info)
            done.set()

        self.after(0, _show)
        while not done.wait(timeout=0.1):
            if self.engine.abort_event.is_set():
                return None
        return result[0]

    def show_same_disc_prompt_step(self, context_summary=""):
        """Ask how to handle a disc that matches the previous session."""
        from gui.setup_wizard import show_same_disc_prompt

        result = ["cancel"]
        done = threading.Event()

        def _show():
            result[0] = show_same_disc_prompt(self, context_summary)
            done.set()

        self.after(0, _show)
        while not done.wait(timeout=0.1):
            if self.engine.abort_event.is_set():
                return "cancel"
        return result[0]

    def show_content_mapping_step(self, classified):
        """Step 3: Content mapping — select titles to rip. Returns ContentSelection or None."""
        from gui.setup_wizard import show_content_mapping

        result = [None]
        done = threading.Event()

        def _show():
            result[0] = show_content_mapping(self, classified)
            done.set()

        self.after(0, _show)
        while not done.wait(timeout=0.1):
            if self.engine.abort_event.is_set():
                return None
        return result[0]

    def show_extras_classification_step(self, extra_titles):
        """Step 4: Extras classification — assign Jellyfin categories. Returns ExtrasAssignment or None."""
        from gui.setup_wizard import show_extras_classification

        result = [None]
        done = threading.Event()

        def _show():
            result[0] = show_extras_classification(self, extra_titles)
            done.set()

        self.after(0, _show)
        while not done.wait(timeout=0.1):
            if self.engine.abort_event.is_set():
                return None
        return result[0]

    def show_output_plan_step(
        self,
        base_folder,
        main_label,
        extras_map,
        detail_lines=None,
        header_text="Step 5: Output Plan",
        subtitle_text=f"This is exactly what {APP_DISPLAY_NAME} will create. No guessing, no surprises.",
        confirm_text="Start Rip",
        *,
        suggested_folder=None,
    ):
        """Step 5: Output plan preview. Returns True to confirm, False to cancel."""
        from gui.setup_wizard import show_output_plan

        result = [False]
        done = threading.Event()

        def _show():
            result[0] = show_output_plan(
                self,
                base_folder,
                main_label,
                extras_map,
                detail_lines=detail_lines,
                header_text=header_text,
                subtitle_text=subtitle_text,
                confirm_text=confirm_text,
                suggested_base_folder=suggested_folder,
            )
            done.set()

        self.after(0, _show)
        while not done.wait(timeout=0.1):
            if self.engine.abort_event.is_set():
                return False
        return result[0]

    def ask_directory(self, title, prompt, initialdir=""):
        """Open a native folder picker and return selected path or None."""
        def _pick():
            # Bring the app window to the foreground first so the native dialog
            # is less likely to appear behind other windows.
            try:
                self.deiconify()
                self.lift()
                self.focus_force()
                self.update_idletasks()
            except Exception:
                pass

            chosen = filedialog.askdirectory(
                title=f"{title}: {prompt}",
                initialdir=initialdir or os.path.expanduser("~"),
                mustexist=False,
                parent=self,
            )

            try:
                self.lift()
                self.focus_force()
            except Exception:
                pass

            return chosen if chosen else None

        return self._run_on_main(_pick)

    def ask_open_file(
        self,
        title,
        prompt,
        initialdir="",
        initialfile="",
        filetypes=(("All files", "*.*"),),
    ):
        """Open a native file picker and return selected path or None."""
        def _pick():
            try:
                self.deiconify()
                self.lift()
                self.focus_force()
                self.update_idletasks()
            except Exception:
                pass

            chosen = filedialog.askopenfilename(
                title=f"{title}: {prompt}",
                initialdir=initialdir or os.path.expanduser("~"),
                initialfile=initialfile or "",
                filetypes=filetypes,
                parent=self,
            )

            try:
                self.lift()
                self.focus_force()
            except Exception:
                pass

            return chosen if chosen else None

        return self._run_on_main(_pick)

    def ask_save_file(
        self,
        title,
        prompt,
        initialdir="",
        initialfile="",
        defaultextension="",
        filetypes=(("All files", "*.*"),),
    ):
        """Open a native save dialog and return selected path or None."""
        def _pick():
            try:
                self.deiconify()
                self.lift()
                self.focus_force()
                self.update_idletasks()
            except Exception:
                pass

            chosen = filedialog.asksaveasfilename(
                title=f"{title}: {prompt}",
                initialdir=initialdir or os.path.expanduser("~"),
                initialfile=initialfile or "",
                defaultextension=defaultextension,
                filetypes=filetypes,
                parent=self,
            )

            try:
                self.lift()
                self.focus_force()
            except Exception:
                pass

            return chosen if chosen else None

        return self._run_on_main(_pick)

    def _run_modal_dialog(self, show_dialog):
        """Run a blocking Tk dialog on the main loop and return its result."""
        if threading.current_thread() is threading.main_thread():
            return show_dialog()

        result = [None]
        error = [None]
        done = threading.Event()

        def _show():
            try:
                result[0] = show_dialog()
            except Exception as exc:  # pragma: no cover - defensive handoff
                error[0] = exc
            finally:
                done.set()

        self.after(0, _show)
        while not done.wait(timeout=0.1):
            pass

        if error[0] is not None:
            raise error[0]
        return result[0]

    def ask_duplicate_resolution(self, prompt,
                                 retry_text="Swap and Retry",
                                 bypass_text="Not a Dup",
                                 stop_text="Stop"):
        """
        Three-way decision prompt for duplicate-disc handling.
        Returns one of: 'retry', 'bypass', 'stop'.
        """
        return self._run_modal_dialog(
            lambda: self._ask_duplicate_resolution_modal(
                prompt,
                retry_text=retry_text,
                bypass_text=bypass_text,
                stop_text=stop_text,
            )
        )

    def _get_user_prompt_timeout_seconds(self):
        if not self.cfg.get("opt_user_prompt_timeout_enabled", False):
            return None
        try:
            return max(
                1,
                int(self.cfg.get("opt_user_prompt_timeout_seconds", 300))
            )
        except Exception:
            return 300

    def _ask_duplicate_resolution_modal(self, prompt,
                                        retry_text="Swap and Retry",
                                        bypass_text="Not a Dup",
                                        stop_text="Stop"):
        """Main-thread-safe modal duplicate prompt using a nested Tk event loop."""
        colors = self._theme
        result = ["stop"]
        win = tk.Toplevel(self)
        win.title("Duplicate Disc Check")
        win.configure(bg=colors["surface"])
        win.grab_set()
        win.lift()
        win.focus_force()
        win.resizable(False, False)

        tk.Label(
            win,
            text=prompt,
            bg=colors["surface"],
            fg=colors["text"],
            justify="left",
            wraplength=520,
            font=("Segoe UI", 10),
        ).pack(padx=18, pady=(18, 10))

        btn_row = tk.Frame(win, bg=colors["surface"])
        btn_row.pack(padx=18, pady=(0, 18))

        finished = {"done": False}

        def finish(value):
            if finished["done"]:
                return
            finished["done"] = True
            result[0] = value
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", lambda: finish("stop"))
        win.bind("<Escape>", lambda _e: finish("stop"))
        win.bind("<Return>", lambda _e: finish("retry"))

        tk.Button(
            btn_row,
            text=retry_text,
            bg=colors["green"],
            fg=colors["text"],
            font=("Segoe UI", 10, "bold"),
            command=lambda: finish("retry"),
            relief="flat",
            width=16,
        ).pack(side="left", padx=4)
        tk.Button(
            btn_row,
            text=bypass_text,
            bg=colors["blue"],
            fg=colors["text"],
            font=("Segoe UI", 10, "bold"),
            command=lambda: finish("bypass"),
            relief="flat",
            width=14,
        ).pack(side="left", padx=4)
        tk.Button(
            btn_row,
            text=stop_text,
            bg=colors["abort"],
            fg=colors["text"],
            font=("Segoe UI", 10, "bold"),
            command=lambda: finish("stop"),
            relief="flat",
            width=12,
        ).pack(side="left", padx=4)

        timeout_seconds = self._get_user_prompt_timeout_seconds()
        if timeout_seconds is not None:
            win.after(int(timeout_seconds * 1000), lambda: finish("stop"))

        def _poll_abort():
            if finished["done"]:
                return
            if self.engine.abort_event.is_set():
                finish("stop")
                return
            try:
                win.after(100, _poll_abort)
            except tk.TclError:
                return

        win.after(100, _poll_abort)
        win.wait_window()
        return result[0]

    def _run_on_main(self, fn):
        """Execute callable on tkinter main loop and return its result."""
        if threading.current_thread() is threading.main_thread():
            return fn()

        result = [None]
        done   = threading.Event()

        def wrapper():
            try:
                result[0] = fn()
            finally:
                done.set()

        self.after(0, wrapper)
        while not done.wait(timeout=0.1):
            if self.engine.abort_event.is_set():
                return None
        return result[0]

    def show_info(self, title, msg):
        self._run_on_main(
            lambda: messagebox.showinfo(title, msg, parent=self)
        )

    def show_error(self, title, msg):
        self._run_on_main(
            lambda: messagebox.showerror(title, msg, parent=self)
        )

    def show_error_async(self, title, msg):
        def _show():
            messagebox.showerror(title, msg, parent=self)

        self.after(0, _show)

    def _ffmpeg_version_ok(self, ffmpeg_exe: str) -> bool:
        """Return True if *ffmpeg_exe* meets the minimum version requirement.

        When the binary is detected as too old the user is shown a blocking
        warning with guidance on how to update.  They can still choose to
        proceed (returns True) or abort (returns False).

        When the version cannot be determined (probe error, missing binary)
        this method returns True so a missing-binary error surfaces normally
        later in the pipeline rather than being silenced here.
        """
        if not ffmpeg_exe or not os.path.isfile(ffmpeg_exe):
            return True  # let the normal "not found" error handle this
        info = get_ffmpeg_version_info(ffmpeg_exe)
        if not info["too_old"]:
            return True
        label  = info["label"]
        year   = info.get("build_year")
        year_s = f" (built {year})" if year else ""
        msg = (
            f"The configured FFmpeg is too old to run {APP_DISPLAY_NAME} transcodes.\n\n"
            f"Detected version: {label}{year_s}\n"
            f"Required: FFmpeg 4.0+ (released April 2018)\n\n"
            f"The '-disposition:s:0' flag and modern GPU-encoder options used\n"
            f"by {APP_DISPLAY_NAME} are not available in this build.\n\n"
            f"Download a current FFmpeg from:\n"
            f"  https://ffmpeg.org/download.html\n\n"
            f"Then update Settings \u2192 Paths \u2192 FFmpeg path.\n\n"
            f"Continue anyway (encode will likely fail)?"
        )
        return messagebox.askyesno("FFmpeg Too Old", msg, parent=self)

    def _open_path_in_explorer(self, path):
        normalized = os.path.normpath(str(path))
        if not os.path.exists(normalized):
            self.show_error("Open in Explorer", f"Path not found:\n{normalized}")
            return

        try:
            if sys.platform == "win32":
                target = normalized
                if not os.path.isdir(normalized):
                    target = os.path.dirname(normalized) or normalized
                subprocess.Popen(
                    [get_explorer_executable(), target],
                    shell=False,
                )
            elif sys.platform == "darwin":
                subprocess.Popen(["open", normalized])
            else:
                subprocess.Popen(["xdg-open", normalized])
        except Exception as e:
            self.show_error("Open in Explorer", f"Could not open path:\n{normalized}\n\n{e}")

    def _reveal_path_in_explorer(self, path):
        normalized = os.path.normpath(str(path))
        if not os.path.exists(normalized):
            self.show_error("Reveal in Explorer", f"Path not found:\n{normalized}")
            return

        try:
            if sys.platform == "win32":
                target = normalized
                if os.path.isdir(normalized):
                    self._open_path_in_explorer(normalized)
                    return
                subprocess.Popen(
                    [get_explorer_executable(), f"/select,{target}"],
                    shell=False,
                )
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", normalized])
            else:
                self._open_path_in_explorer(os.path.dirname(normalized) or normalized)
        except Exception as e:
            self.show_error("Reveal in Explorer", f"Could not reveal path:\n{normalized}\n\n{e}")

    def _browse_settings_path(self, key, label, current_path=""):
        normalized = os.path.normpath(current_path) if current_path else ""
        current_dir = ""
        if normalized:
            current_dir = normalized if os.path.isdir(normalized) else os.path.dirname(normalized)

        folder_keys = {"ffprobe_path", "temp_folder", "tv_folder", "movies_folder"}
        if key in folder_keys:
            return self.ask_directory("Browse", f"Choose {label.lower()}", initialdir=current_dir)

        if key == "log_file":
            default_name = (
                os.path.basename(normalized)
                if normalized
                else f"{APP_EXE_BASENAME.lower()}.log"
            )
            return self.ask_save_file(
                "Log File",
                f"Choose {label.lower()}",
                initialdir=current_dir,
                initialfile=default_name,
                defaultextension=".log",
                filetypes=(("Log files", "*.log *.txt"), ("All files", "*.*")),
            )

        default_name = os.path.basename(normalized) if normalized else ""
        return self.ask_open_file(
            "Tool Path",
            f"Choose {label.lower()}",
            initialdir=current_dir,
            initialfile=default_name,
            filetypes=(("Executable files", "*.exe"), ("All files", "*.*")),
        )

    def _open_settings_path(self, key, label, raw_path):
        normalized = os.path.normpath(raw_path.strip()) if raw_path else ""
        if not normalized:
            self.show_info("Open in Explorer", f"No path set for {label.lower()} yet.")
            return

        folder_keys = {"ffprobe_path", "temp_folder", "tv_folder", "movies_folder"}
        if key in folder_keys:
            self._open_path_in_explorer(normalized)
            return

        if os.path.exists(normalized):
            self._reveal_path_in_explorer(normalized)
            return

        parent = os.path.dirname(normalized)
        if parent and os.path.isdir(parent):
            self._open_path_in_explorer(parent)
            return

        self.show_error("Open in Explorer", f"Path not found:\n{normalized}")

    def ask_space_override(self, required_gb, free_gb):
        if threading.current_thread() is threading.main_thread():
            return self._ask_space_override_modal(required_gb, free_gb)

        result = [False]
        done   = threading.Event()
        colors = self._theme

        def _show():
            win = tk.Toplevel(self)
            win.title("Not Enough Space")
            win.configure(bg=colors["surface_deep"])
            win.grab_set()
            win.lift()
            win.focus_force()
            win.resizable(False, False)

            tk.Label(
                win, text="⚠  NOT ENOUGH DISK SPACE",
                font=("Segoe UI", 16, "bold"),
                bg=colors["surface_deep"], fg=colors["pill_error_border"]
            ).pack(pady=(20, 10), padx=20)
            tk.Label(
                win,
                text=f"Required:  {required_gb:.1f} GB\n"
                     f"Free:         {free_gb:.1f} GB\n\n"
                     f"This may cause the rip to fail\n"
                     f"or produce incomplete files.",
                font=("Segoe UI", 12),
                bg=colors["surface_deep"], fg=colors["text"],
                justify="center"
            ).pack(pady=10, padx=30)

            bf = tk.Frame(win, bg=colors["surface_deep"])
            bf.pack(pady=20)

            def proceed():
                result[0] = True
                win.destroy()
                done.set()

            def cancel():
                result[0] = False
                win.destroy()
                done.set()

            win.protocol("WM_DELETE_WINDOW", cancel)

            tk.Button(
                bf, text="I understand, continue anyway",
                bg=colors["abort"], fg=colors["text"],
                font=("Segoe UI", 11, "bold"),
                width=28, command=proceed, relief="flat"
            ).pack(side="left", padx=8)
            tk.Button(
                bf, text="Cancel",
                bg=colors["toolbar_button"], fg=colors["toolbar_button_text"],
                font=("Segoe UI", 11),
                width=12, command=cancel, relief="flat"
            ).pack(side="left", padx=8)

        self.after(0, _show)
        while not done.wait(timeout=0.1):
            if self.engine.abort_event.is_set():
                return False
        return result[0]

    def _ask_space_override_modal(self, required_gb, free_gb):
        """Main-thread-safe modal version of the low-space override prompt."""
        colors = self._theme
        result = [False]
        win = tk.Toplevel(self)
        win.title("Not Enough Space")
        win.configure(bg=colors["surface_deep"])
        win.grab_set()
        win.lift()
        win.focus_force()
        win.resizable(False, False)

        tk.Label(
            win, text="⚠  NOT ENOUGH DISK SPACE",
            font=("Segoe UI", 16, "bold"),
            bg=colors["surface_deep"], fg=colors["pill_error_border"]
        ).pack(pady=(20, 10), padx=20)
        tk.Label(
            win,
            text=f"Required:  {required_gb:.1f} GB\n"
                 f"Free:         {free_gb:.1f} GB\n\n"
                 f"This may cause the rip to fail\n"
                 f"or produce incomplete files.",
            font=("Segoe UI", 12),
            bg=colors["surface_deep"], fg=colors["text"],
            justify="center"
        ).pack(pady=10, padx=30)

        bf = tk.Frame(win, bg=colors["surface_deep"])
        bf.pack(pady=20)

        def finish(value):
            result[0] = bool(value)
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", lambda: finish(False))

        tk.Button(
            bf, text="I understand, continue anyway",
            bg=colors["abort"], fg=colors["text"],
            font=("Segoe UI", 11, "bold"),
            width=28, command=lambda: finish(True), relief="flat"
        ).pack(side="left", padx=8)
        tk.Button(
            bf, text="Cancel",
            bg=colors["toolbar_button"], fg=colors["toolbar_button_text"],
            font=("Segoe UI", 11),
            width=12, command=lambda: finish(False), relief="flat"
        ).pack(side="left", padx=8)

        win.wait_window()
        return result[0]

    def show_disc_tree(self, disc_titles, is_tv, preview_callback=None):
        result = [None]
        done   = threading.Event()
        colors = self._theme

        def _show():
            win = tk.Toplevel(self)
            win.title("Disc Contents - Select Titles to Rip")
            win.configure(bg=colors["window_bg"])
            win.grab_set()
            win.lift()
            win.focus_force()
            win.geometry("1180x700")

            tk.Label(
                win,
                text="Select titles to rip. "
                     "Click anywhere on a title row or press Space to toggle. "
                     "Right-click previews when available. "
                     "Recommended title highlighted in blue.",
                bg=colors["window_bg"], fg=colors["muted"],
                font=("Segoe UI", 10)
            ).pack(pady=(10, 4), padx=15)

            tree_frame = tk.Frame(win, bg=colors["window_bg"])
            tree_frame.pack(
                fill="both", expand=True, padx=15, pady=5
            )

            style = ttk.Style(win)
            style.theme_use("default")
            style.configure(
                "Disc.Treeview",
                background=colors["surface_alt"], foreground=colors["text"],
                fieldbackground=colors["surface_alt"], rowheight=24,
                font=("Consolas", 10)
            )
            style.configure(
                "Disc.Treeview.Heading",
                background=colors["surface"], foreground=colors["title"],
                font=("Segoe UI", 10, "bold")
            )
            style.map(
                "Disc.Treeview",
                background=[("selected", colors["blue"])],
                foreground=[("selected", colors["text"])],
            )

            tree = ttk.Treeview(
                tree_frame, style="Disc.Treeview",
                columns=("duration", "size", "chapters", "status", "audio", "preview"),
                show="tree headings",
                selectmode="none",
            )
            tree.heading("#0",       text="Title / Track")
            tree.heading("duration", text="Duration")
            tree.heading("size",     text="Size")
            tree.heading("chapters", text="Chapters")
            tree.heading("status",   text="Status")
            tree.heading("audio",    text="Audio")
            tree.heading("preview",  text="Preview")
            tree.column("#0",       width=320)
            tree.column("duration", width=80,  anchor="center")
            tree.column("size",     width=80,  anchor="center")
            tree.column("chapters", width=70,  anchor="center")
            tree.column("status",   width=210, anchor="w")
            tree.column("audio",    width=260, anchor="w")
            tree.column("preview",  width=90,  anchor="center")

            vsb = ttk.Scrollbar(
                tree_frame, orient="vertical", command=tree.yview
            )
            tree.configure(yscrollcommand=vsb.set)
            tree.pack(side="left", fill="both", expand=True)
            vsb.pack(side="right", fill="y")

            check_vars  = {}
            base_labels = {}

            def _build_checkbox_image(checked: bool) -> tk.PhotoImage:
                img = tk.PhotoImage(width=14, height=14)
                checkbox_bg = colors["surface_alt"]
                border = colors["muted"]
                mark = colors["title"]
                img.put(checkbox_bg, to=(0, 0, 14, 14))
                img.put(border, to=(1, 1, 13, 2))
                img.put(border, to=(1, 12, 13, 13))
                img.put(border, to=(1, 1, 2, 13))
                img.put(border, to=(12, 1, 13, 13))
                if checked:
                    for x, y in (
                        (3, 7), (4, 8), (5, 9),
                        (6, 8), (7, 7), (8, 6), (9, 5), (10, 4),
                    ):
                        img.put(mark, (x, y))
                return img

            checkbox_images = {
                False: _build_checkbox_image(False),
                True: _build_checkbox_image(True),
            }

            def _set_checked(iid: str, checked: bool) -> None:
                check_vars[iid] = checked
                tree.item(
                    iid,
                    text=base_labels[iid],
                    image=checkbox_images[checked],
                )

            cached_classified = getattr(self.engine, "last_classification", []) or []
            if classification_matches_titles(cached_classified, disc_titles):
                classified = list(cached_classified)
            else:
                classified = classify_titles(disc_titles)
                self.engine.last_classification = classified

            best_ct = get_recommended_title(classified)
            best_id = best_ct.title_id if best_ct else None
            cls_by_id = {ct.title_id: ct for ct in classified}
            id_map  = {int(t.get("id", -1)): t for t in disc_titles}

            for t in (ct.title for ct in classified):
                audio_summary = format_audio_summary(
                    t.get("audio_tracks", [])
                )
                iid          = f"title_{t['id']}"
                pre_selected = (t["id"] == best_id)
                check_vars[iid]  = pre_selected

                # Build label with classification tag
                ct = cls_by_id.get(t["id"])
                if ct:
                    pct = int(ct.confidence * 100)
                    cls_tag = f" [#{ct.rank} {ct.label} {pct}%]"
                else:
                    cls_tag = ""
                base_labels[iid] = f"Title {t['id']+1}: {t['name']}{cls_tag}"

                tags = ["title"]
                if ct and ct.recommended:
                    tags.append("main")
                if ct and not ct.valid:
                    tags.append("invalid")
                if ct and ct.label == "DUPLICATE":
                    tags.append("duplicate")
                if ct and ct.label == "EXTRA":
                    tags.append("extra")

                tree.insert(
                    "", "end", iid=iid,
                    text=base_labels[iid],
                    image=checkbox_images[pre_selected],
                    values=(
                        t.get("duration", ""),
                        t.get("size", ""),
                        t.get("chapters", ""),
                        ct.status_text if ct else "",
                        audio_summary,
                        "Preview",
                    ),
                    tags=tuple(tags)
                )

                if ct:
                    tree.insert(
                        iid, "end",
                        text=f"    Why: {ct.why_text}",
                        values=("", "", "", ct.status_text, "", ""),
                        tags=("meta",)
                    )

                for s in t.get("subtitle_tracks", []):
                    lang = (s.get("lang_name") or
                            s.get("lang") or "Unknown")
                    tree.insert(
                        iid, "end",
                        text=f"    💬 Subtitle: {lang}",
                        values=("", "", "", "", "", ""),
                        tags=("track",)
                    )

            tree.tag_configure("title",     foreground=colors["text"])
            tree.tag_configure("main",      foreground=colors["title"])
            tree.tag_configure("invalid",   foreground=colors["pill_error_border"])
            tree.tag_configure("duplicate", foreground=colors["pill_warn_border"])
            tree.tag_configure("extra",     foreground=colors["muted"])
            tree.tag_configure("meta",      foreground=colors["muted"])
            tree.tag_configure("track",     foreground=colors["muted_soft"])

            def _title_item_for_row(item: str) -> str | None:
                current = item
                while current:
                    if current.startswith("title_"):
                        return current
                    current = tree.parent(current)
                return None

            def _preview_title_item(item: str) -> str:
                if not preview_callback:
                    return "break"
                title_item = _title_item_for_row(item)
                if not title_item:
                    return "break"
                tree.focus(title_item)
                try:
                    tid = int(title_item.split("_")[1])
                    preview_callback(tid)
                except Exception:
                    pass
                return "break"

            def toggle(event):
                item = tree.identify_row(event.y)
                title_item = _title_item_for_row(item)
                if not title_item or item != title_item:
                    return
                tree.focus(title_item)
                col = tree.identify_column(event.x)
                try:
                    element = tree.identify_element(event.x, event.y)
                except Exception:
                    element = ""
                if element.endswith("indicator"):
                    return
                if col == "#6" and preview_callback:
                    _preview_title_item(title_item)
                    return
                _set_checked(title_item, not check_vars[title_item])
                _update_size_label()

            tree.bind("<Button-1>", toggle)
            tree.bind("<Button-3>", lambda event: _preview_title_item(tree.identify_row(event.y)))

            def _toggle_focused(_event=None):
                item = tree.focus()
                if not item or not item.startswith("title_"):
                    return "break"
                _set_checked(item, not check_vars[item])
                _update_size_label()
                return "break"

            tree.bind("<space>", _toggle_focused)
            tree.bind("<Return>", _toggle_focused)
            tree.focus_set()

            def _update_size_label():
                total = sum(
                    id_map[int(iid.split("_")[1])].get("size_bytes", 0)
                    for iid, checked in check_vars.items()
                    if checked
                )
                size_label_var.set(
                    f"Selected: ~{total / (1024**3):.1f} GB"
                )

            def select_all():
                for iid in check_vars:
                    _set_checked(iid, True)
                _update_size_label()

            def deselect_all():
                for iid in check_vars:
                    _set_checked(iid, False)
                _update_size_label()

            def select_best():
                for iid in check_vars:
                    _set_checked(iid, False)
                if best_id is not None:
                    iid = f"title_{best_id}"
                    _set_checked(iid, True)
                _update_size_label()

            def select_top3():
                for iid in check_vars:
                    _set_checked(iid, False)
                for ct in classified[:3]:
                    iid = f"title_{ct.title_id}"
                    _set_checked(iid, True)
                _update_size_label()

            btn_row = tk.Frame(win, bg=colors["window_bg"])
            btn_row.pack(fill="x", padx=15, pady=8)

            size_label_var = tk.StringVar(value="")
            _update_size_label()

            tk.Label(
                btn_row, textvariable=size_label_var,
                bg=colors["window_bg"], fg=colors["title"],
                font=("Segoe UI", 10, "bold")
            ).pack(side="left", padx=8)

            for text, cmd in [
                ("Select All",   select_all),
                ("Deselect All", deselect_all),
                ("Best Only",    select_best),
                ("Top 3",        select_top3),
            ]:
                tk.Button(
                    btn_row, text=text, command=cmd,
                    bg=colors["toolbar_button"], fg=colors["toolbar_button_text"],
                    font=("Segoe UI", 10), relief="flat"
                ).pack(side="left", padx=4)

            def confirm():
                selected = [
                    int(iid.split("_")[1])
                    for iid, checked in check_vars.items()
                    if checked
                ]
                result[0] = selected
                win.destroy()
                done.set()

            def cancel():
                result[0] = None
                win.destroy()
                done.set()

            win.protocol("WM_DELETE_WINDOW", cancel)

            tk.Button(
                btn_row, text="Rip Selected",
                bg=colors["green"], fg=colors["text"],
                font=("Segoe UI", 11, "bold"),
                command=confirm, relief="flat"
            ).pack(side="right", padx=4)
            tk.Button(
                btn_row, text="Cancel",
                bg=colors["toolbar_button"], fg=colors["toolbar_button_text"],
                command=cancel, relief="flat"
            ).pack(side="right", padx=4)

        self.after(0, _show)
        while not done.wait(timeout=0.1):
            if self.engine.abort_event.is_set():
                return None
        return result[0]

    def show_file_list(self, title, prompt, options):
        result = [None]
        done   = threading.Event()
        colors = self._theme

        def _show():
            win = tk.Toplevel(self)
            win.title(title)
            win.configure(bg=colors["window_bg"])
            win.grab_set()
            win.lift()
            win.focus_force()

            tk.Label(
                win, text=prompt,
                bg=colors["window_bg"], fg=colors["text"],
                font=("Segoe UI", 11), wraplength=500
            ).pack(pady=10, padx=15)

            listbox = tk.Listbox(
                win, bg=colors["surface_alt"], fg=colors["text"],
                font=("Consolas", 10), width=70,
                height=min(len(options), 15),
                selectmode="extended", relief="flat",
                selectbackground=colors["blue"],
                selectforeground=colors["text"],
            )
            listbox.pack(padx=15, pady=5)
            for opt in options:
                listbox.insert("end", opt)
            listbox.select_set(0)

            btn_row = tk.Frame(win, bg=colors["window_bg"])
            btn_row.pack(pady=4)
            tk.Button(
                btn_row, text="Select All",
                bg=colors["toolbar_button"], fg=colors["toolbar_button_text"],
                command=lambda: listbox.select_set(0, "end"),
                relief="flat"
            ).pack(side="left", padx=6)
            tk.Button(
                btn_row, text="Deselect All",
                bg=colors["toolbar_button"], fg=colors["toolbar_button_text"],
                command=lambda: listbox.selection_clear(0, "end"),
                relief="flat"
            ).pack(side="left", padx=6)

            def confirm():
                result[0] = [
                    listbox.get(i) for i in listbox.curselection()
                ]
                win.destroy()
                done.set()

            def on_close():
                result[0] = []
                win.destroy()
                done.set()

            win.protocol("WM_DELETE_WINDOW", on_close)
            tk.Button(
                win, text="Confirm",
                bg=colors["green"], fg=colors["text"],
                font=("Segoe UI", 11),
                command=confirm, relief="flat"
            ).pack(pady=10)

        self.after(0, _show)
        while not done.wait(timeout=0.1):
            if self.engine.abort_event.is_set():
                return []
        return result[0]

    def show_extras_picker(self, title, prompt, options):
        """Multi-select dialog with all items pre-selected.
        Returns list of selected 0-based indices, or None if cancelled.
        """
        result = [None]
        done   = threading.Event()
        colors = self._theme

        def _show():
            win = tk.Toplevel(self)
            win.title(title)
            win.configure(bg=colors["window_bg"])
            win.grab_set()
            win.lift()
            win.focus_force()

            tk.Label(
                win, text=prompt,
                bg=colors["window_bg"], fg=colors["text"],
                font=("Segoe UI", 11), wraplength=500
            ).pack(pady=10, padx=15)

            listbox = tk.Listbox(
                win, bg=colors["surface_alt"], fg=colors["text"],
                font=("Consolas", 10), width=70,
                height=min(len(options), 15),
                selectmode="extended", relief="flat",
                selectbackground=colors["blue"],
                selectforeground=colors["text"],
            )
            listbox.pack(padx=15, pady=5)
            for opt in options:
                listbox.insert("end", opt)
            listbox.select_set(0, "end")  # pre-select all

            btn_row = tk.Frame(win, bg=colors["window_bg"])
            btn_row.pack(pady=4)
            tk.Button(
                btn_row, text="Select All",
                bg=colors["toolbar_button"], fg=colors["toolbar_button_text"],
                command=lambda: listbox.select_set(0, "end"),
                relief="flat"
            ).pack(side="left", padx=6)
            tk.Button(
                btn_row, text="Deselect All",
                bg=colors["toolbar_button"], fg=colors["toolbar_button_text"],
                command=lambda: listbox.selection_clear(0, "end"),
                relief="flat"
            ).pack(side="left", padx=6)

            def confirm():
                result[0] = list(listbox.curselection())
                win.destroy()
                done.set()

            def on_close():
                result[0] = None
                win.destroy()
                done.set()

            win.protocol("WM_DELETE_WINDOW", on_close)
            tk.Button(
                win, text="Confirm",
                bg=colors["green"], fg=colors["text"],
                font=("Segoe UI", 11),
                command=confirm, relief="flat"
            ).pack(pady=10)

        self.after(0, _show)
        while not done.wait(timeout=0.1):
            if self.engine.abort_event.is_set():
                return None
        return result[0]

    def show_temp_manager(self, old_folders, engine, log_fn):
        if not old_folders:
            return
        done = threading.Event()
        colors = self._theme

        STATUS_COLORS = {
            "ripped":     colors["green"],
            "organizing": colors["pill_warn_border"],
            "ripping":    colors["pill_warn_border"],
            "organized":  colors["blue"],
        }
        DEFAULT_COLOR = colors["pill_error_border"]

        def _show():
            win = None
            try:
                normalized_folders = []
                for entry in old_folders:
                    if isinstance(entry, (tuple, list)) and len(entry) == 4:
                        full_path, name, file_count, size = entry
                    else:
                        full_path = os.path.normpath(str(entry))
                        name = os.path.basename(full_path.rstrip("\\/")) or full_path
                        file_count = 0
                        size = 0
                    normalized_folders.append(
                        (full_path, name, int(file_count), int(size))
                    )

                win = tk.Toplevel(self)
                win.title("Temp Session Manager")
                win.configure(bg=colors["window_bg"])
                win.grab_set()
                win.lift()
                win.focus_force()
                win.geometry("740x540")

                tk.Label(
                    win, text="Temp Sessions",
                    font=("Segoe UI", 14, "bold"),
                    bg=colors["window_bg"], fg=colors["title"]
                ).pack(pady=(15, 5))
                tk.Label(
                    win,
                    text="Leftover disc folders in your temp directory.\n"
                         "Check the ones you want to delete.",
                    font=("Segoe UI", 10),
                    bg=colors["window_bg"], fg=colors["muted"]
                ).pack(pady=(0, 10))

                frame = tk.Frame(win, bg=colors["window_bg"])
                frame.pack(fill="both", expand=True, padx=15, pady=5)

                canvas = tk.Canvas(
                    frame, bg=colors["window_bg"], highlightthickness=0
                )
                scrollbar = ttk.Scrollbar(
                    frame, orient="vertical", command=canvas.yview
                )
                scroll_frame = tk.Frame(canvas, bg=colors["window_bg"])

                scroll_frame.bind(
                    "<Configure>",
                    lambda e: canvas.configure(
                        scrollregion=canvas.bbox("all")
                    )
                )
                canvas.create_window(
                    (0, 0), window=scroll_frame, anchor="nw"
                )
                canvas.configure(yscrollcommand=scrollbar.set)
                canvas.pack(side="left", fill="both", expand=True)
                scrollbar.pack(side="right", fill="y")

                check_vars = []

                for full_path, name, file_count, size in normalized_folders:
                    meta = engine.read_temp_metadata(full_path)
                    var = tk.BooleanVar(value=False)
                    check_vars.append((var, full_path, name))

                    status_text = (
                        meta.get("status", "unknown")
                        if meta else "unknown"
                    )
                    color = STATUS_COLORS.get(status_text, DEFAULT_COLOR)

                    row = tk.Frame(scroll_frame, bg=colors["surface"])
                    row.pack(fill="x", pady=3, padx=5)

                    tk.Checkbutton(
                        row, variable=var,
                        bg=colors["surface"], activebackground=colors["surface"],
                        selectcolor=colors["green"]
                    ).pack(side="left", padx=8, pady=8)

                    tk.Label(
                        row, text="*", fg=color, bg=colors["surface"],
                        font=("Segoe UI", 14)
                    ).pack(side="left", padx=(0, 6))

                    info = tk.Frame(row, bg=colors["surface"])
                    info.pack(
                        side="left", fill="x", expand=True, pady=6
                    )

                    title_text = (
                        meta.get("title", "Unknown")
                        if meta else "Unknown"
                    )
                    ts_text = (
                        meta.get("timestamp", name) if meta else name
                    )

                    tk.Label(
                        info, text=title_text,
                        font=("Segoe UI", 11, "bold"),
                        bg=colors["surface"], fg=colors["text"], anchor="w"
                    ).pack(fill="x")
                    tk.Label(
                        info,
                        text=f"Ripped: {ts_text}   "
                             f"Files: {file_count}   "
                             f"Size: {size / (1024**3):.1f} GB   "
                             f"Status: {status_text}",
                        font=("Segoe UI", 9),
                        bg=colors["surface"], fg=colors["muted"], anchor="w"
                    ).pack(fill="x")

                btn_row = tk.Frame(win, bg=colors["window_bg"])
                btn_row.pack(fill="x", padx=15, pady=12)

                def select_all():
                    for var, _, _ in check_vars:
                        var.set(True)

                def deselect_all():
                    for var, _, _ in check_vars:
                        var.set(False)

                def delete_selected():
                    selected = [
                        (full_path, name)
                        for var, full_path, name in check_vars
                        if var.get()
                    ]

                    # Close first to keep UI responsive; delete on background
                    # thread so large folder trees do not block tkinter.
                    win.destroy()

                    def _delete_worker():
                        for full_path, name in selected:
                            try:
                                shutil.rmtree(full_path)
                                log_fn(f"Deleted temp folder: {name}")
                            except Exception as e:
                                log_fn(f"Could not delete {name}: {e}")
                        done.set()

                    threading.Thread(
                        target=_delete_worker,
                        daemon=True
                    ).start()

                def close():
                    win.destroy()
                    done.set()

                win.protocol("WM_DELETE_WINDOW", close)

                tk.Button(
                    btn_row, text="Select All",
                    bg=colors["toolbar_button"], fg=colors["toolbar_button_text"],
                    command=select_all, relief="flat"
                ).pack(side="left", padx=4)
                tk.Button(
                    btn_row, text="Deselect All",
                    bg=colors["toolbar_button"], fg=colors["toolbar_button_text"],
                    command=deselect_all, relief="flat"
                ).pack(side="left", padx=4)
                tk.Button(
                    btn_row, text="Delete Selected",
                    bg=colors["abort"], fg=colors["text"],
                    font=("Segoe UI", 11, "bold"),
                    command=delete_selected, relief="flat"
                ).pack(side="right", padx=4)
                tk.Button(
                    btn_row, text="Close",
                    bg=colors["toolbar_button"], fg=colors["toolbar_button_text"],
                    command=close, relief="flat"
                ).pack(side="right", padx=4)
            except Exception as e:
                if win is not None:
                    try:
                        win.destroy()
                    except Exception:
                        pass
                log_fn(f"Temp session manager unavailable: {e}")
                done.set()

        self.after(0, _show)
        while not done.wait(timeout=0.1):
            if self.engine.abort_event.is_set():
                return

    def _open_settings_safe(self, selected_tab: str | None = None):
        """Prevent callback exceptions from tearing down the main window."""
        try:
            self.open_settings(selected_tab=selected_tab)
        except Exception as e:
            self._settings_window = None
            self._settings_notebook = None
            self._settings_tabs = {}
            import traceback
            tb = traceback.format_exc()
            try:
                self.controller.log(f"Fatal settings callback error: {e}\n{tb}")
            except Exception:
                pass
            try:
                self.show_error("Settings Error", f"Could not open Settings:\n{e}")
            except Exception:
                messagebox.showerror(
                    "Settings Error",
                    f"Could not open Settings:\n{e}",
                    parent=self,
                )


    def open_settings(self, *, selected_tab: str | None = None):
        cfg = self.cfg
        colors = self._theme
        # Expert Mode toggle (persistent in config)
        expert_mode_var = tk.BooleanVar(value=cfg.get('opt_expert_mode', False))
        if self.rip_thread and self.rip_thread.is_alive():
            messagebox.showwarning(
                "Rip in Progress",
                "Settings cannot be opened during an active rip.\n"
                "Abort the current session first, or wait for it to finish.",
                parent=self,
            )
            return

        if (
            self._settings_window is not None
            and self._settings_window.winfo_exists()
        ):
            try:
                notebook = getattr(self, "_settings_notebook", None)
                tab_lookup = getattr(self, "_settings_tabs", {})
                if (
                    selected_tab
                    and notebook is not None
                    and selected_tab in tab_lookup
                ):
                    notebook.select(tab_lookup[selected_tab])
                self._settings_window.lift()
                self._settings_window.focus_force()
            except Exception:
                pass
            return

        done = threading.Event()

        def _show():
            win = tk.Toplevel(self)
            self._settings_window = win
            self._settings_notebook = None
            self._settings_tabs = {}
            win.title(f"{APP_DISPLAY_NAME} Settings")
            expert_toggle_row = tk.Frame(win, bg=colors["window_bg"])
            expert_toggle_row.pack(fill="x", padx=16, pady=(8, 0))
            tk.Checkbutton(
                expert_toggle_row, variable=expert_mode_var,
                bg=colors["window_bg"], activebackground=colors["window_bg"],
                selectcolor=colors["green"],
                fg=colors["text"], font=("Segoe UI", 11, "bold"),
                text="Enable Expert Mode (show all advanced profile options)", anchor="w"
            ).pack(side="left")
            win.configure(bg=colors["window_bg"])
            try:
                win.grab_set()
            except tk.TclError:
                # Avoid crashing if another dialog currently owns grab.
                pass
            win.lift()
            win.focus_force()
            win.geometry("700x800")
            win.resizable(False, True)

            style = ttk.Style(win)
            style.configure("JellyRip.TNotebook", background=colors["window_bg"])
            style.configure(
                "JellyRip.TNotebook.Tab",
                padding=(12, 8),
                background=colors["toolbar_button"],
                foreground=colors["toolbar_button_text"]
            )
            style.map(
                "JellyRip.TNotebook.Tab",
                background=[("selected", colors["surface"])],
                foreground=[("selected", colors["title"])]
            )

            notebook = ttk.Notebook(win, style="JellyRip.TNotebook")
            notebook.pack(fill="both", expand=True, padx=8, pady=(8, 0))

            def make_scroll_tab(title, *, tab_key: str | None = None):
                tab = tk.Frame(notebook, bg=colors["window_bg"])
                canvas = tk.Canvas(
                    tab, bg=colors["window_bg"], highlightthickness=0
                )
                scrollbar = ttk.Scrollbar(
                    tab, orient="vertical", command=canvas.yview
                )
                scroll_frame = tk.Frame(canvas, bg=colors["window_bg"])

                scroll_frame.bind(
                    "<Configure>",
                    lambda e, c=canvas: c.configure(
                        scrollregion=c.bbox("all")
                    )
                )
                canvas.create_window(
                    (0, 0), window=scroll_frame, anchor="nw"
                )
                canvas.configure(yscrollcommand=scrollbar.set)
                canvas.pack(side="left", fill="both", expand=True)
                scrollbar.pack(side="right", fill="y")
                notebook.add(tab, text=title)
                if tab_key:
                    self._settings_tabs[tab_key] = tab
                return scroll_frame

            self._settings_notebook = notebook
            paths_tab = make_scroll_tab("Paths", tab_key="paths")
            everyday_tab = make_scroll_tab("Everyday", tab_key="everyday")
            validation_tab = make_scroll_tab("Validation", tab_key="validation")
            advanced_tab = make_scroll_tab("Advanced", tab_key="advanced")
            ai_tab = make_scroll_tab("AI Assistant", tab_key="ai")
            logs_tab = make_scroll_tab("Logs & Debug", tab_key="logs")
            expert_tab = None
            if expert_mode_var.get():
                expert_tab = make_scroll_tab("Expert", tab_key="expert")
            cfg      = self.cfg
            vars_map = {}
            expert_vars: dict[str, dict[str, object]] = {}
            expert_profile_state = {
                "name": None,
                "default_name": None,
                "names": [],
                "data": None,
            }
            expert_status_var = tk.StringVar()
            naming_mode_label_to_value = {
                "Timestamp (default)": "timestamp",
                "Auto title": "auto-title",
                "Auto title + timestamp (safe)": "auto-title+timestamp",
            }
            naming_mode_value_to_label = {
                value: label
                for label, value in naming_mode_label_to_value.items()
            }

            def section(parent, text):
                tk.Label(
                    parent, text=text,
                    bg=colors["window_bg"], fg=colors["title"],
                    font=("Segoe UI", 11, "bold"), anchor="w"
                ).pack(fill="x", padx=16, pady=(14, 2))
                tk.Frame(
                    parent, bg=colors["panel_border"], height=1
                ).pack(fill="x", padx=16, pady=(0, 6))

            def path_row(parent, key, label):
                row = tk.Frame(parent, bg=colors["window_bg"])
                row.pack(fill="x", padx=16, pady=3)
                tk.Label(
                    row, text=label, bg=colors["window_bg"], fg=colors["text"],
                    font=("Segoe UI", 10), width=28, anchor="w"
                ).pack(side="left")
                var = tk.StringVar(
                    value=cfg.get(key, DEFAULTS.get(key, ""))
                )
                tk.Entry(
                    row, textvariable=var,
                    bg=colors["surface_deep"], fg=colors["text"],
                    font=("Segoe UI", 10),
                    insertbackground=colors["text"],
                    relief="flat", bd=3, width=28
                ).pack(side="left", padx=4)

                def browse_path():
                    chosen = self._browse_settings_path(
                        key,
                        label,
                        var.get().strip(),
                    )
                    if chosen:
                        var.set(os.path.normpath(chosen))

                tk.Button(
                    row, text="Browse",
                    bg=colors["toolbar_button"], fg=colors["toolbar_button_text"],
                    font=("Segoe UI", 9),
                    relief="flat", bd=0, padx=8, pady=2,
                    cursor="hand2",
                    command=browse_path,
                ).pack(side="left", padx=(4, 2))
                tk.Button(
                    row, text="Open",
                    bg=colors["toolbar_button"], fg=colors["toolbar_button_muted"],
                    font=("Segoe UI", 9),
                    relief="flat", bd=0, padx=8, pady=2,
                    cursor="hand2",
                    command=lambda: self._open_settings_path(
                        key,
                        label,
                        var.get(),
                    ),
                ).pack(side="left", padx=(2, 0))

                vars_map[key] = ("str", var)

            def toggle_row(parent, key, label):
                """Create a toggle row without dependent number field.
                Number fields are now created separately for full independence."""
                row = tk.Frame(parent, bg=colors["window_bg"])
                row.pack(fill="x", padx=16, pady=2)
                bool_var = tk.BooleanVar(value=cfg.get(key, True))
                tk.Checkbutton(
                    row, variable=bool_var,
                    bg=colors["window_bg"], activebackground=colors["window_bg"],
                    selectcolor=colors["green"],
                    fg=colors["text"], font=("Segoe UI", 10),
                    text=label, anchor="w"
                ).pack(side="left")
                vars_map[key] = ("bool", bool_var)

            def number_row(parent, key, label, default=0):
                row = tk.Frame(parent, bg=colors["window_bg"])
                row.pack(fill="x", padx=16, pady=2)
                tk.Label(
                    row, text=label,
                    bg=colors["window_bg"], fg=colors["text"],
                    font=("Segoe UI", 10), anchor="w", width=36
                ).pack(side="left")
                num_var = tk.StringVar(
                    value=str(cfg.get(key, default))
                )
                tk.Entry(
                    row, textvariable=num_var,
                    bg=colors["surface_deep"], fg=colors["text"],
                    font=("Segoe UI", 10),
                    relief="flat", bd=3, width=10
                ).pack(side="left")
                vars_map[key] = ("int", num_var)

            def float_row(parent, key, label, default=0.0):
                row = tk.Frame(parent, bg=colors["window_bg"])
                row.pack(fill="x", padx=16, pady=2)
                tk.Label(
                    row, text=label,
                    bg=colors["window_bg"], fg=colors["text"],
                    font=("Segoe UI", 10), anchor="w", width=36
                ).pack(side="left")
                num_var = tk.StringVar(
                    value=str(cfg.get(key, default))
                )
                tk.Entry(
                    row, textvariable=num_var,
                    bg=colors["surface_deep"], fg=colors["text"],
                    font=("Segoe UI", 10),
                    relief="flat", bd=3, width=10
                ).pack(side="left")
                vars_map[key] = ("float", num_var)

            def text_row(parent, key, label, width=38):
                row = tk.Frame(parent, bg=colors["window_bg"])
                row.pack(fill="x", padx=16, pady=2)
                tk.Label(
                    row, text=label,
                    bg=colors["window_bg"], fg=colors["text"],
                    font=("Segoe UI", 10), anchor="w", width=36
                ).pack(side="left")
                txt_var = tk.StringVar(
                    value=cfg.get(key, DEFAULTS.get(key, ""))
                )
                tk.Entry(
                    row, textvariable=txt_var,
                    bg=colors["surface_deep"], fg=colors["text"],
                    font=("Segoe UI", 10),
                    relief="flat", bd=3, width=width
                ).pack(side="left")
                vars_map[key] = ("text", txt_var)

            def choice_row(parent, key, label, choices):
                row = tk.Frame(parent, bg=colors["window_bg"])
                row.pack(fill="x", padx=16, pady=2)
                tk.Label(
                    row, text=label,
                    bg=colors["window_bg"], fg=colors["text"],
                    font=("Segoe UI", 10), anchor="w", width=36
                ).pack(side="left")
                selected = tk.StringVar(
                    value=cfg.get(key, DEFAULTS.get(key, choices[0]))
                )
                combo = ttk.Combobox(
                    row, textvariable=selected,
                    values=choices, state="readonly", width=24
                )
                combo.pack(side="left")
                vars_map[key] = ("choice", selected)
                return selected

            def choice_map_row(parent, key, label, label_to_value):
                row = tk.Frame(parent, bg=colors["window_bg"])
                row.pack(fill="x", padx=16, pady=2)
                tk.Label(
                    row, text=label,
                    bg=colors["window_bg"], fg=colors["text"],
                    font=("Segoe UI", 10), anchor="w", width=36
                ).pack(side="left")
                current_value = str(
                    cfg.get(key, DEFAULTS.get(key, ""))
                ).strip()
                normalized_value = normalize_ffmpeg_source_mode(current_value)
                selected = tk.StringVar(
                    value=_ffmpeg_source_mode_label(normalized_value)
                )
                combo = ttk.Combobox(
                    row,
                    textvariable=selected,
                    values=list(label_to_value.keys()),
                    state="readonly",
                    width=24,
                )
                combo.pack(side="left")
                vars_map[key] = ("choice_map", selected, label_to_value)
                return selected

            def multiline_text_row(
                parent,
                label,
                initial_text="",
                *,
                height=5,
            ):
                row = tk.Frame(parent, bg=colors["window_bg"])
                row.pack(fill="both", expand=False, padx=16, pady=2)
                tk.Label(
                    row, text=label,
                    bg=colors["window_bg"], fg=colors["text"],
                    font=("Segoe UI", 10), anchor="nw", width=36,
                ).pack(side="left", anchor="nw", pady=(4, 0))
                widget = tk.Text(
                    row,
                    height=height,
                    width=38,
                    wrap="word",
                    bg=colors["surface_deep"],
                    fg=colors["text"],
                    insertbackground=colors["text"],
                    relief="flat",
                    bd=3,
                    font=("Segoe UI", 10),
                )
                widget.pack(side="left", fill="both", expand=True)
                widget.insert("1.0", str(initial_text or ""))
                return widget

            if expert_tab is not None:
                section(expert_tab, "Transcode Profile (Expert)")
                tk.Label(
                    expert_tab,
                    text=(
                        "Pick a transcode profile to inspect or edit. Saving "
                        "writes back to the selected profile."
                    ),
                    bg=colors["window_bg"],
                    fg=colors["muted"],
                    font=("Segoe UI", 9),
                    wraplength=620,
                    justify="left",
                    anchor="w",
                ).pack(fill="x", padx=16, pady=(0, 4))
                expert_profile_name_var = tk.StringVar()
                expert_summary_var = tk.StringVar()
                expert_profile_combo = None

                def _update_expert_status():
                    profile_name = expert_profile_state.get("name")
                    default_name = expert_profile_state.get("default_name")
                    if not profile_name:
                        return
                    if default_name and profile_name == default_name:
                        expert_status_var.set(
                            f"Editing profile: {profile_name} (current default)"
                        )
                    elif default_name:
                        expert_status_var.set(
                            f"Editing profile: {profile_name} | "
                            f"Current default: {default_name}"
                        )
                    else:
                        expert_status_var.set(f"Editing profile: {profile_name}")

                def _update_expert_summary():
                    profile_data = expert_profile_state.get("data")
                    if profile_data is None:
                        expert_summary_var.set("Profile summary unavailable.")
                        return
                    try:
                        expert_summary_var.set(
                            self._summarize_expert_profile(profile_data)
                        )
                    except Exception:
                        expert_summary_var.set("Profile summary unavailable.")

                def _load_expert_profile(profile_name=None):
                    snapshot = self._load_expert_profile_snapshot(profile_name)
                    expert_profile_state.update(snapshot)
                    expert_profile_name_var.set(snapshot["name"])
                    if expert_profile_combo is not None:
                        expert_profile_combo.configure(
                            values=snapshot.get("names", [])
                        )
                    _update_expert_status()
                    _update_expert_summary()
                    return snapshot

                try:
                    _load_expert_profile()
                except Exception as exc:
                    expert_status_var.set(
                        f"Could not load transcode profile: {exc}"
                    )
                    expert_summary_var.set("Profile summary unavailable.")

                if expert_profile_state["data"] is not None:
                    picker_row = tk.Frame(expert_tab, bg=colors["window_bg"])
                    picker_row.pack(fill="x", padx=16, pady=(0, 6))
                    tk.Label(
                        picker_row,
                        text="Profile:",
                        bg=colors["window_bg"],
                        fg=colors["text"],
                        font=("Segoe UI", 10),
                        width=12,
                        anchor="w",
                    ).pack(side="left")
                    expert_profile_combo = ttk.Combobox(
                        picker_row,
                        textvariable=expert_profile_name_var,
                        values=expert_profile_state.get("names", []),
                        state="readonly",
                        width=34,
                    )
                    expert_profile_combo.pack(side="left", fill="x", expand=True)

                tk.Label(
                    expert_tab,
                    textvariable=expert_status_var,
                    bg=colors["window_bg"],
                    fg=colors["title"],
                    font=("Segoe UI", 10, "bold"),
                    wraplength=620,
                    justify="left",
                    anchor="w",
                ).pack(fill="x", padx=16, pady=(0, 8))
                tk.Label(
                    expert_tab,
                    textvariable=expert_summary_var,
                    bg=colors["window_bg"],
                    fg=colors["muted"],
                    font=("Segoe UI", 9),
                    wraplength=620,
                    justify="left",
                    anchor="w",
                ).pack(fill="x", padx=16, pady=(0, 10))

                if expert_profile_state["data"] is not None:
                    expert_choice_values = {
                        ("video", "codec"): ["h265", "h264", "copy"],
                        ("video", "mode"): ["crf", "bitrate", "copy"],
                        (
                            "video",
                            "preset",
                        ): [
                            "",
                            "ultrafast",
                            "superfast",
                            "veryfast",
                            "faster",
                            "fast",
                            "medium",
                            "slow",
                            "slower",
                            "veryslow",
                        ],
                        (
                            "video",
                            "tune",
                        ): [
                            "",
                            "film",
                            "animation",
                            "grain",
                            "stillimage",
                            "fastdecode",
                            "zerolatency",
                        ],
                        (
                            "video",
                            "video_profile",
                        ): ["", "main", "main10", "high", "high10", "baseline"],
                        (
                            "video",
                            "pix_fmt",
                        ): [
                            "",
                            "yuv420p",
                            "yuv420p10le",
                            "yuv422p10le",
                            "yuv444p",
                            "yuv444p10le",
                        ],
                        ("video", "hw_accel"): [
                            "cpu",
                            "auto_prefer",
                            "nvenc",
                            "qsv",
                            "amf",
                        ],
                        ("audio", "mode"): [
                            "copy",
                            "aac",
                            "ac3",
                            "eac3",
                            "mp3",
                            "opus",
                            "flac",
                        ],
                        ("audio", "tracks"): ["all", "main", "language"],
                        ("audio", "channels"): ["", "1", "2", "6", "8"],
                        ("audio", "sample_rate"): ["", "44100", "48000", "96000"],
                        ("subtitles", "mode"): ["all", "forced", "language", "none"],
                        ("output", "container"): ["mkv", "mp4", "mov"],
                        ("metadata", "preserve"): ["", "true", "false"],
                    }
                    expert_bool_fields = {
                        ("audio", "downmix"),
                        ("subtitles", "burn"),
                        ("output", "overwrite"),
                        ("output", "auto_increment"),
                        ("constraints", "skip_if_codec_matches"),
                    }
                    for section_name, schema in PROFILE_SCHEMA.items():
                        section(expert_tab, section_name.capitalize())
                        section_vars = {}
                        expert_vars[section_name] = section_vars
                        for key in schema:
                            row = tk.Frame(expert_tab, bg=colors["window_bg"])
                            row.pack(fill="x", padx=24, pady=2)
                            tk.Label(
                                row,
                                text=key,
                                bg=colors["window_bg"],
                                fg=colors["text"],
                                font=("Segoe UI", 10),
                                width=18,
                                anchor="w",
                            ).pack(side="left")
                            value = expert_profile_state["data"].get(
                                section_name, {}
                            ).get(key)
                            field_name = (section_name, key)
                            if field_name in expert_bool_fields:
                                var = tk.BooleanVar(value=bool(value))
                                tk.Checkbutton(
                                    row,
                                    variable=var,
                                    bg=colors["window_bg"],
                                    activebackground=colors["window_bg"],
                                    selectcolor=colors["green"],
                                ).pack(side="left")
                                section_vars[key] = self._make_expert_var_handle(
                                    var,
                                    "bool",
                                )
                            elif field_name in expert_choice_values:
                                var = tk.StringVar(
                                    value=self._format_expert_profile_value(value)
                                )
                                ttk.Combobox(
                                    row,
                                    textvariable=var,
                                    values=expert_choice_values[field_name],
                                    state="readonly",
                                    width=24,
                                ).pack(side="left")
                                section_vars[key] = self._make_expert_var_handle(var)
                            else:
                                var = tk.StringVar(
                                    value=self._format_expert_profile_value(value)
                                )
                                tk.Entry(
                                    row,
                                    textvariable=var,
                                    bg=colors["surface_deep"],
                                    fg=colors["text"],
                                    font=("Segoe UI", 10),
                                    relief="flat",
                                    bd=3,
                                    width=24,
                                ).pack(side="left")
                                section_vars[key] = self._make_expert_var_handle(var)

                    self._populate_expert_profile_vars(
                        expert_vars,
                        expert_profile_state["data"],
                    )

                    def _on_expert_profile_selected(_event=None):
                        selected_name = expert_profile_name_var.get().strip()
                        current_name = expert_profile_state.get("name")
                        if not selected_name or selected_name == current_name:
                            return
                        if not self._confirm_discard_dirty_expert_changes(
                            expert_profile_state.get("data"),
                            expert_vars,
                            (
                                "Discard unsaved Expert profile edits and load "
                                f"'{selected_name}'?"
                            ),
                            win,
                        ):
                            expert_profile_name_var.set(current_name or "")
                            return
                        try:
                            snapshot = _load_expert_profile(selected_name)
                        except Exception as exc:
                            self.controller.log(
                                f"Expert profile load failed: {exc}"
                            )
                            messagebox.showerror(
                                "Expert Profile",
                                f"Could not load expert profile:\n{exc}",
                                parent=win,
                            )
                            expert_profile_name_var.set(current_name or "")
                            return
                        self._populate_expert_profile_vars(
                            expert_vars,
                            snapshot["data"],
                        )

                    expert_profile_combo.bind(
                        "<<ComboboxSelected>>",
                        _on_expert_profile_selected,
                    )

                    def apply_expert_profile():
                        try:
                            profile_name = expert_profile_state["name"]
                            profile_data = self._collect_expert_profile_data(
                                expert_profile_state["data"],
                                expert_vars,
                                profile_name,
                            )
                            if not self._confirm_profile_hdr_metadata_save(
                                profile_data,
                                win,
                            ):
                                return
                            saved_name = self._save_expert_profile_data(
                                profile_name,
                                profile_data,
                            )
                        except Exception as exc:
                            self.controller.log(
                                f"Expert profile save failed: {exc}"
                            )
                            messagebox.showerror(
                                "Expert Profile",
                                f"Could not save expert profile:\n{exc}",
                                parent=win,
                            )
                            return

                        expert_profile_state["name"] = saved_name
                        expert_profile_state["data"] = profile_data
                        if not expert_profile_state.get("default_name"):
                            expert_profile_state["default_name"] = saved_name
                        expert_profile_name_var.set(saved_name)
                        _update_expert_status()
                        _update_expert_summary()
                        self.controller.log(
                            f"Expert profile saved: {saved_name}"
                        )

                    def set_default_expert_profile():
                        try:
                            default_name = self._set_default_expert_profile(
                                expert_profile_state["name"]
                            )
                        except Exception as exc:
                            self.controller.log(
                                f"Expert profile default update failed: {exc}"
                            )
                            messagebox.showerror(
                                "Expert Profile",
                                f"Could not set default profile:\n{exc}",
                                parent=win,
                            )
                            return

                        expert_profile_state["default_name"] = default_name
                        _update_expert_status()
                        self.controller.log(
                            f"Expert profile set as default: {default_name}"
                        )

                    def _prompt_expert_profile_name(
                        title,
                        prompt,
                        default_value="",
                    ):
                        value = self.ask_input(
                            title,
                            prompt,
                            default_value=default_value,
                        )
                        name = str(value or "").strip()
                        return name or None

                    def create_expert_profile():
                        if not self._confirm_discard_dirty_expert_changes(
                            expert_profile_state.get("data"),
                            expert_vars,
                            "Discard unsaved Expert profile edits and create a new profile?",
                            win,
                        ):
                            return
                        profile_name = _prompt_expert_profile_name(
                            "New Profile",
                            "Enter a name for the new transcode profile:",
                        )
                        if not profile_name:
                            return
                        try:
                            created_name = self._create_expert_profile(profile_name)
                            snapshot = _load_expert_profile(created_name)
                        except Exception as exc:
                            self.controller.log(
                                f"Expert profile create failed: {exc}"
                            )
                            messagebox.showerror(
                                "Expert Profile",
                                f"Could not create profile:\n{exc}",
                                parent=win,
                            )
                            return
                        self._populate_expert_profile_vars(
                            expert_vars,
                            snapshot["data"],
                        )
                        self.controller.log(
                            f"Expert profile created: {created_name}"
                        )

                    def duplicate_expert_profile():
                        source_name = expert_profile_state.get("name")
                        if not source_name:
                            return
                        new_name = _prompt_expert_profile_name(
                            "Duplicate Profile",
                            f"Enter a name for the duplicate of '{source_name}':",
                            default_value=f"{source_name} Copy",
                        )
                        if not new_name:
                            return
                        try:
                            profile_data = self._collect_expert_profile_data(
                                expert_profile_state["data"],
                                expert_vars,
                                source_name,
                            )
                            if not self._confirm_profile_hdr_metadata_save(
                                profile_data,
                                win,
                            ):
                                return
                            duplicated_name = self._duplicate_expert_profile(
                                source_name,
                                new_name,
                                profile_data,
                            )
                            snapshot = _load_expert_profile(duplicated_name)
                        except Exception as exc:
                            self.controller.log(
                                f"Expert profile duplicate failed: {exc}"
                            )
                            messagebox.showerror(
                                "Expert Profile",
                                f"Could not duplicate profile:\n{exc}",
                                parent=win,
                            )
                            return
                        self._populate_expert_profile_vars(
                            expert_vars,
                            snapshot["data"],
                        )
                        self.controller.log(
                            f"Expert profile duplicated: {duplicated_name}"
                        )

                    def delete_expert_profile():
                        profile_name = expert_profile_state.get("name")
                        if not profile_name:
                            return
                        if not self._confirm_discard_dirty_expert_changes(
                            expert_profile_state.get("data"),
                            expert_vars,
                            (
                                "Discard unsaved Expert profile edits and delete "
                                f"'{profile_name}'?"
                            ),
                            win,
                        ):
                            return
                        if not messagebox.askyesno(
                            "Delete Expert Profile",
                            f"Delete transcode profile '{profile_name}'?",
                            parent=win,
                        ):
                            return
                        try:
                            next_name = self._delete_expert_profile(profile_name)
                            snapshot = _load_expert_profile(next_name)
                        except Exception as exc:
                            self.controller.log(
                                f"Expert profile delete failed: {exc}"
                            )
                            messagebox.showerror(
                                "Expert Profile",
                                f"Could not delete profile:\n{exc}",
                                parent=win,
                            )
                            return
                        self._populate_expert_profile_vars(
                            expert_vars,
                            snapshot["data"],
                        )
                        self.controller.log(
                            f"Expert profile deleted: {profile_name}"
                        )

                    expert_btn_row = tk.Frame(expert_tab, bg=colors["window_bg"])
                    expert_btn_row.pack(fill="x", padx=16, pady=12)
                    tk.Button(
                        expert_btn_row,
                        text="New",
                        bg=colors["toolbar_button"],
                        fg=colors["text"],
                        font=("Segoe UI", 10, "bold"),
                        command=create_expert_profile,
                    ).pack(side="left")
                    tk.Button(
                        expert_btn_row,
                        text="Duplicate",
                        bg=colors["toolbar_button"],
                        fg=colors["text"],
                        font=("Segoe UI", 10, "bold"),
                        command=duplicate_expert_profile,
                    ).pack(side="left", padx=(8, 0))
                    tk.Button(
                        expert_btn_row,
                        text="Delete",
                        bg=colors["abort"],
                        fg=colors["text"],
                        font=("Segoe UI", 10, "bold"),
                        command=delete_expert_profile,
                    ).pack(side="left", padx=(8, 0))
                    tk.Button(
                        expert_btn_row,
                        text="Apply Expert Profile Changes",
                        bg=colors["green"],
                        fg=colors["text"],
                        font=("Segoe UI", 10, "bold"),
                        command=apply_expert_profile,
                    ).pack(side="left", padx=(16, 0))
                    tk.Button(
                        expert_btn_row,
                        text="Set As Default",
                        bg=colors["blue"],
                        fg=colors["text"],
                        font=("Segoe UI", 10, "bold"),
                        command=set_default_expert_profile,
                    ).pack(side="left", padx=(8, 0))

            ai_profile = load_ai_profile(cfg)
            ai_profile_vars: dict[str, tk.StringVar] = {}
            ai_profile_instructions_widget = None

            section(paths_tab, "Apps")
            path_row(paths_tab, "makemkvcon_path", "MakeMKV app")
            path_row(paths_tab, "ffprobe_path",    "ffmpeg / ffprobe folder")
            path_row(paths_tab, "ffmpeg_path",     "FFmpeg executable")
            path_row(paths_tab, "handbrake_path",  "HandBrakeCLI executable")

            # Auto Locate button
            _auto_status_var = tk.StringVar()

            def _do_auto_locate():
                if "opt_allow_path_tool_resolution" in vars_map:
                    allow_path_lookup = bool(
                        vars_map["opt_allow_path_tool_resolution"][1].get()
                    )
                else:
                    allow_path_lookup = self._allow_path_tool_resolution()
                mkv_result = resolve_makemkvcon(
                    "",
                    allow_path_lookup=allow_path_lookup,
                )
                ffprobe_result = resolve_ffprobe(
                    "",
                    allow_path_lookup=allow_path_lookup,
                )
                ffmpeg_result = resolve_ffmpeg(
                    "",
                    allow_path_lookup=allow_path_lookup,
                )
                handbrake_result = resolve_handbrake(
                    "",
                    allow_path_lookup=allow_path_lookup,
                )
                results = []
                notes = []
                if mkv_result.path:
                    vars_map["makemkvcon_path"][1].set(mkv_result.path)
                    results.append(f"MakeMKV ({mkv_result.source})")
                if ffprobe_result.path:
                    vars_map["ffprobe_path"][1].set(ffprobe_result.path)
                    results.append(f"FFprobe ({ffprobe_result.source})")
                if ffmpeg_result.path:
                    vars_map["ffmpeg_path"][1].set(ffmpeg_result.path)
                    results.append(f"FFmpeg ({ffmpeg_result.source})")
                if handbrake_result.path:
                    vars_map["handbrake_path"][1].set(handbrake_result.path)
                    results.append(
                        f"HandBrakeCLI ({handbrake_result.source})"
                    )
                elif handbrake_gui_installed():
                    notes.append(
                        "HandBrake GUI found but HandBrakeCLI is not installed "
                        "(optional — download the CLI from handbrake.fr if needed)."
                    )
                status_parts = []
                if results:
                    status_parts.append(f"Found: {', '.join(results)}")
                if notes:
                    status_parts.extend(notes)
                if not status_parts:
                    status_parts.append("No tools found automatically.")
                _auto_status_var.set("  " + "  ".join(status_parts))
                win.after(8000, lambda: _auto_status_var.set(""))

            auto_btn_row = tk.Frame(paths_tab, bg=colors["window_bg"])
            auto_btn_row.pack(fill="x", padx=16, pady=(0, 6))
            tk.Button(
                auto_btn_row, text="Auto Locate",
                bg=colors["toolbar_button"], fg=colors["toolbar_button_text"],
                font=("Segoe UI", 10),
                relief="flat", bd=0, padx=8, pady=2,
                cursor="hand2",
                command=_do_auto_locate,
            ).pack(side="left")
            tk.Label(
                auto_btn_row, textvariable=_auto_status_var,
                bg=colors["window_bg"], fg=colors["green"],
                font=("Segoe UI", 9),
            ).pack(side="left", padx=8)

            section(paths_tab, "Folders")
            path_row(paths_tab, "temp_folder",     "Temp folder")
            path_row(paths_tab, "tv_folder",       "TV shows library folder")
            path_row(paths_tab, "movies_folder",   "Movies folder")

            row = tk.Frame(paths_tab, bg=colors["window_bg"])
            row.pack(fill="x", padx=16, pady=2)
            tk.Label(
                row, text="Naming mode:",
                bg=colors["window_bg"], fg=colors["text"],
                font=("Segoe UI", 10), anchor="w", width=36
            ).pack(side="left")

            mode_value = resolve_naming_mode(cfg)
            if mode_value == "disc-title":
                mode_value = "auto-title"
            elif mode_value == "disc-title+timestamp":
                mode_value = "auto-title+timestamp"

            naming_mode_var = tk.StringVar(
                value=naming_mode_value_to_label.get(
                    mode_value, "Timestamp (default)"
                )
            )
            naming_dropdown = ttk.Combobox(
                row,
                textvariable=naming_mode_var,
                state="readonly",
                values=list(naming_mode_label_to_value.keys()),
                width=30,
            )
            naming_dropdown.pack(side="left")
            vars_map["opt_naming_mode"] = ("naming_mode", naming_mode_var)

            naming_preview_var = tk.StringVar()
            tk.Label(
                paths_tab,
                textvariable=naming_preview_var,
                bg=colors["window_bg"],
                fg=colors["muted"],
                font=("Segoe UI", 9),
                anchor="w",
            ).pack(fill="x", padx=16, pady=(0, 4))

            def update_naming_preview(*_args):
                selected = naming_mode_var.get().strip()
                mode = normalize_naming_mode(
                    naming_mode_label_to_value.get(selected, "timestamp")
                )
                sample_title = "Inception"
                sample_rip = make_rip_folder_name()
                naming_preview_var.set(
                    build_naming_preview_text(
                        mode, sample_title, sample_rip
                    )
                )

            naming_mode_var.trace_add("write", update_naming_preview)
            update_naming_preview()

            path_row(paths_tab, "log_file",        "Log file")

            section(everyday_tab, "Common Options")
            toggle_row(everyday_tab, "opt_safe_mode",
                       "Safe Mode (recommended)")
            toggle_row(everyday_tab, "opt_confirm_before_rip",
                       "Ask before ripping")
            toggle_row(everyday_tab, "opt_confirm_before_move",
                       "Ask before moving files")
            toggle_row(everyday_tab, "opt_smart_rip_mode",
                       "Smart Rip (auto-pick best title)")
            number_row(everyday_tab, "opt_smart_min_minutes",
                       "Shortest movie length for Smart Rip (minutes):", 20)
            float_row(everyday_tab, "opt_smart_low_confidence_threshold",
                      "Smart Rip low-confidence warning threshold:", 0.45)
            toggle_row(everyday_tab, "opt_show_temp_manager",
                       "Show temp folders before TV or dump runs")
            toggle_row(everyday_tab, "opt_auto_delete_temp",
                       "Delete temp files after successful organize")
            toggle_row(everyday_tab, "opt_auto_delete_session_metadata",
                       "Delete session JSON after successful organize")
            toggle_row(everyday_tab, "opt_clean_partials_startup",
                       "Remove unfinished files at startup")
            toggle_row(everyday_tab, "opt_warn_out_of_order_episodes",
                       "Warn if episode numbers look out of order")
            toggle_row(everyday_tab, "opt_session_failure_report",
                       "Show a failure report at the end")

            section(everyday_tab, "Extras")
            choice_row(everyday_tab, "opt_extras_folder_mode",
                       "Extras folder layout:",
                       ["single", "split"])
            choice_row(everyday_tab, "opt_bonus_folder_name",
                       "Bonus folder name (Jellyfin):",
                       ["behind the scenes", "deleted scenes",
                        "featurettes", "interviews", "scenes",
                        "shorts", "clips", "other", "trailers"])

            section(validation_tab, "Rip Validation")
            toggle_row(validation_tab, "opt_scan_disc_size",
                       "Check disc size before ripping")
            toggle_row(validation_tab, "opt_file_stabilization",
                       "Wait for files to finish writing")
            toggle_row(validation_tab, "opt_check_dest_space",
                       "Check free space before moving files")
            toggle_row(validation_tab, "opt_warn_low_space",
                       "Warn when free space is low")
            number_row(validation_tab, "opt_min_rip_size_gb",
                       "Minimum accepted file size (GB):", 1)
            number_row(validation_tab, "opt_expected_size_ratio_pct",
                       "Preferred size match vs expected (%):", 70)
            number_row(validation_tab, "opt_hard_fail_ratio_pct",
                       "Hard fail below expected size (%):", 40)
            number_row(validation_tab, "opt_stabilize_timeout_seconds",
                       "File-write wait timeout in seconds:", 60)
            number_row(validation_tab, "opt_stabilize_required_polls",
                       "How many stable checks are required:", 4)
            number_row(validation_tab, "opt_move_verify_retries",
                       "Move size check retries:", 5)

            section(advanced_tab, "MakeMKV")
            number_row(advanced_tab, "opt_drive_index",
                       "Drive number for MakeMKV:", 0)
            number_row(advanced_tab, "opt_minlength_seconds",
                       "Min title length in seconds (0=off):", 0)
            toggle_row(advanced_tab, "opt_stall_detection",
                       "Warn when MakeMKV goes quiet")
            number_row(advanced_tab, "opt_stall_timeout_seconds",
                       "Quiet-time warning in seconds:", 120)
            section(advanced_tab, "Interactive Timeouts")
            toggle_row(advanced_tab, "opt_user_prompt_timeout_enabled",
                       "Let prompts auto-timeout")
            number_row(advanced_tab, "opt_user_prompt_timeout_seconds",
                       "Prompt timeout in seconds:", 300)
            toggle_row(advanced_tab, "opt_disc_swap_timeout_enabled",
                       "Let multi-disc swap wait timeout")
            number_row(advanced_tab, "opt_disc_swap_timeout_seconds",
                       "Disc swap timeout in seconds:", 300)
            toggle_row(advanced_tab, "opt_auto_retry",
                       "Retry failed titles automatically")
            number_row(advanced_tab, "opt_retry_attempts",
                       "Retry attempts per title:", 3)
            toggle_row(advanced_tab, "opt_clean_mkv_before_retry",
                       "Delete new MKV files before retry")
            section(advanced_tab, "Moving")
            toggle_row(advanced_tab, "opt_atomic_move",
                       "Use safer move method (slower)")
            toggle_row(advanced_tab, "opt_fsync",
                       "Force file sync to disk during copy")
            number_row(advanced_tab, "opt_hard_block_gb",
                       "Stop when free space is below (GB):", 20)
            section(advanced_tab, "FFmpeg")
            toggle_row(
                advanced_tab,
                "opt_allow_path_tool_resolution",
                "Allow PATH-based tool lookup (advanced, less predictable)",
            )
            ffmpeg_source_mode_var = choice_map_row(
                advanced_tab,
                "opt_ffmpeg_source_mode",
                "FFmpeg source handling:",
                FFMPEG_SOURCE_MODE_LABEL_TO_VALUE,
            )
            ffmpeg_source_help_var = tk.StringVar()
            tk.Label(
                advanced_tab,
                textvariable=ffmpeg_source_help_var,
                bg=colors["window_bg"],
                fg=colors["muted"],
                font=("Segoe UI", 9),
                wraplength=760,
                justify="left",
                anchor="w",
            ).pack(fill="x", padx=16, pady=(0, 4))

            def update_ffmpeg_source_help(*_args):
                selected_label = ffmpeg_source_mode_var.get().strip()
                selected_mode = FFMPEG_SOURCE_MODE_LABEL_TO_VALUE.get(
                    selected_label,
                    FFMPEG_SOURCE_MODE_SAFE_COPY,
                )
                ffmpeg_source_help_var.set(
                    describe_ffmpeg_source_mode(selected_mode)
                )

            ffmpeg_source_mode_var.trace_add("write", update_ffmpeg_source_help)
            update_ffmpeg_source_help()
            section(advanced_tab, "Extra MakeMKV Arguments")
            text_row(
                advanced_tab,
                "opt_makemkv_global_args",
                "Extra MakeMKV args (all commands):"
            )
            text_row(
                advanced_tab,
                "opt_makemkv_info_args",
                "Extra MakeMKV args (scan commands):"
            )
            text_row(
                advanced_tab,
                "opt_makemkv_rip_args",
                "Extra MakeMKV args (rip commands):"
            )
            section(ai_tab, "Assistant Profile")
            tk.Label(
                ai_tab,
                text=(
                    "These settings shape how the assistant explains things in chat "
                    "and diagnostics. They do not let AI make hidden changes."
                ),
                bg=colors["window_bg"],
                fg=colors["muted"],
                font=("Segoe UI", 9),
                wraplength=760,
                justify="left",
                anchor="w",
            ).pack(fill="x", padx=16, pady=(0, 8))

            for profile_key, profile_label in AI_PROFILE_FIELDS:
                label_to_value = AI_PROFILE_CHOICE_LABELS[profile_key]
                current_value = getattr(
                    ai_profile,
                    profile_key,
                    DEFAULT_AI_PROFILE.get(profile_key, ""),
                )
                selected = tk.StringVar(
                    value=AI_PROFILE_VALUE_LABELS[profile_key].get(
                        current_value,
                        next(iter(label_to_value.keys())),
                    )
                )
                row = tk.Frame(ai_tab, bg=colors["window_bg"])
                row.pack(fill="x", padx=16, pady=2)
                tk.Label(
                    row,
                    text=profile_label,
                    bg=colors["window_bg"],
                    fg=colors["text"],
                    font=("Segoe UI", 10),
                    anchor="w",
                    width=36,
                ).pack(side="left")
                combo = ttk.Combobox(
                    row,
                    textvariable=selected,
                    values=list(label_to_value.keys()),
                    state="readonly",
                    width=24,
                )
                combo.pack(side="left")
                ai_profile_vars[profile_key] = selected

            ai_profile_instructions_widget = multiline_text_row(
                ai_tab,
                "Custom instructions (optional):",
                ai_profile.custom_instructions,
                height=6,
            )
            tk.Label(
                ai_tab,
                text=(
                    "Use short factual guidance here, such as preferred tone, how much "
                    "detail to include, or whether to stay conservative when uncertain."
                ),
                bg=colors["window_bg"],
                fg=colors["muted"],
                font=("Segoe UI", 9),
                wraplength=760,
                justify="left",
                anchor="w",
            ).pack(fill="x", padx=16, pady=(4, 2))
            section(logs_tab, "Log Storage")
            number_row(
                logs_tab,
                "opt_log_cap_lines", "Max log lines kept in memory:", 300000
            )
            number_row(
                logs_tab,
                "opt_log_trim_lines", "Trim log down to this many lines:", 200000
            )
            section(logs_tab, "AI Providers")
            ai_provider_row = tk.Frame(logs_tab, bg=colors["window_bg"])
            ai_provider_row.pack(fill="x", padx=16, pady=4)
            tk.Label(
                ai_provider_row,
                text="Configure AI backend connections (API keys, models, local setup):",
                bg=colors["window_bg"], fg=colors["text"],
                font=("Segoe UI", 10), anchor="w",
            ).pack(side="left")
            tk.Button(
                ai_provider_row, text="Open AI Providers...",
                bg=colors["toolbar_button"], fg=colors["title"],
                font=("Segoe UI", 10), relief="flat",
                cursor="hand2",
                command=self._open_ai_providers,
            ).pack(side="left", padx=(8, 0))
            toggle_row(logs_tab, "opt_ai_diagnostics_enabled",
                       "Enable AI diagnostics")
            toggle_row(logs_tab, "opt_ai_log_to_gui",
                       "Show AI suggestions in live log")
            toggle_row(logs_tab, "opt_ai_log_to_file",
                       "Write AI diagnostics to session log files")
            number_row(logs_tab, "opt_ai_cloud_timeout_seconds",
                       "Cloud AI request timeout (seconds):", 30)
            number_row(logs_tab, "opt_ai_local_timeout_seconds",
                       "Local AI request timeout (seconds):", 90)
            number_row(logs_tab, "opt_ai_max_calls_per_session",
                       "Max AI calls per session:", 20)
            number_row(logs_tab, "opt_ai_disable_after_failures",
                       "Disable AI after consecutive failures:", 3)

            section(logs_tab, "Debugging")
            toggle_row(logs_tab, "opt_debug_safe_int",
                       "Debug: log bad integer values")
            toggle_row(logs_tab, "opt_debug_duration",
                       "Debug: log bad duration values")
            toggle_row(logs_tab, "opt_debug_state",
                       "Debug: log UI state transitions")
            toggle_row(logs_tab, "opt_debug_state_json",
                       "Debug: format UI state logs as JSON")
            section(logs_tab, "AI Replay")
            tk.Label(
                logs_tab,
                text=(
                    "Inspect append-only sidebar chat replay bundles without changing app behavior. "
                    "Use this for request and response debugging only."
                ),
                bg=colors["window_bg"],
                fg=colors["muted"],
                font=("Segoe UI", 9),
                wraplength=760,
                justify="left",
                anchor="w",
            ).pack(fill="x", padx=16, pady=(0, 8))
            replay_row = tk.Frame(logs_tab, bg=colors["window_bg"])
            replay_row.pack(fill="x", padx=16, pady=(0, 4))
            tk.Button(
                replay_row,
                text="Open Replay Browser...",
                command=self._open_ai_chat_replay_inspector,
                bg=colors["toolbar_button"],
                fg=colors["title"],
                font=("Segoe UI", 10),
                relief="flat",
                cursor="hand2",
            ).pack(side="left")
            tk.Label(
                replay_row,
                text=f"Stored at {ai_chat_replay_path()}",
                bg=colors["window_bg"],
                fg=colors["muted"],
                font=("Consolas", 9),
                wraplength=540,
                justify="left",
                anchor="w",
            ).pack(side="left", padx=(10, 0))

            if selected_tab and selected_tab in self._settings_tabs:
                try:
                    notebook.select(self._settings_tabs[selected_tab])
                except Exception:
                    pass

            btn_row = tk.Frame(win, bg=colors["window_bg"])
            btn_row.pack(fill="x", padx=16, pady=12)

            def save():
                try:
                    staged_cfg = dict(cfg)
                    staged_cfg["opt_expert_mode"] = expert_mode_var.get()
                    tool_validators = {
                        "makemkvcon_path": validate_makemkvcon,
                        "ffprobe_path": validate_ffprobe,
                        "ffmpeg_path": validate_ffmpeg,
                        "handbrake_path": validate_handbrake,
                    }

                    staged = {}
                    rejected_fields = []
                    expert_profile_to_save = None
                    for key, entry in vars_map.items():
                        vtype = entry[0]
                        var = entry[1]
                        if vtype == "str":
                            v = var.get().strip()
                            candidate = os.path.normpath(v) if v else ""
                            if key in tool_validators:
                                current = os.path.normpath(
                                    str(cfg.get(key, "")).strip()
                                )
                                if should_keep_current_tool_path(
                                    current,
                                    candidate,
                                    tool_validators[key],
                                ):
                                    _new_ok, new_err = tool_validators[key](candidate)
                                    self.controller.log(
                                        f"Settings: kept working {key}; "
                                        f"new path failed validation ({new_err})."
                                    )
                                    continue
                            staged[key] = candidate
                        elif vtype == "text":
                            staged[key] = var.get().strip()
                        elif vtype == "bool":
                            staged[key] = var.get()
                        elif vtype == "int":
                            try:
                                staged[key] = int(var.get())
                            except ValueError:
                                rejected_fields.append(key)
                        elif vtype == "float":
                            try:
                                staged[key] = float(var.get())
                            except ValueError:
                                rejected_fields.append(key)
                        elif vtype == "choice":
                            staged[key] = var.get().strip()
                        elif vtype == "choice_map":
                            selected = var.get().strip()
                            label_to_value = entry[2]
                            staged[key] = label_to_value.get(
                                selected,
                                DEFAULTS.get(key, ""),
                            )
                        elif vtype == "naming_mode":
                            selected = var.get().strip()
                            staged[key] = naming_mode_label_to_value.get(
                                selected, "timestamp"
                            )

                    if expert_vars and expert_profile_state["name"] is not None:
                        expert_profile_to_save = self._collect_expert_profile_data(
                            expert_profile_state["data"],
                            expert_vars,
                            expert_profile_state["name"],
                        )
                        if not self._confirm_profile_hdr_metadata_save(
                            expert_profile_to_save,
                            win,
                        ):
                            return

                    ai_profile_raw: dict[str, object] = {}
                    for profile_key, selected in ai_profile_vars.items():
                        label_to_value = AI_PROFILE_CHOICE_LABELS[profile_key]
                        ai_profile_raw[profile_key] = label_to_value.get(
                            selected.get().strip(),
                            DEFAULT_AI_PROFILE.get(profile_key, ""),
                        )
                    if ai_profile_instructions_widget is not None:
                        ai_profile_raw["custom_instructions"] = (
                            ai_profile_instructions_widget.get("1.0", "end-1c").strip()
                        )
                    staged["opt_ai_profile"] = AIProfile.from_mapping(
                        ai_profile_raw
                    ).to_dict()
                    staged["opt_ai_profile_onboarded"] = True

                    staged_cfg.update(staged)
                    staged_cfg["opt_ai_cloud_timeout_seconds"] = max(
                        10, int(staged_cfg.get("opt_ai_cloud_timeout_seconds", 30))
                    )
                    staged_cfg["opt_ai_local_timeout_seconds"] = max(
                        10, int(staged_cfg.get("opt_ai_local_timeout_seconds", 90))
                    )
                    if rejected_fields:
                        names = ", ".join(rejected_fields)
                        self.controller.log(
                            f"Settings: invalid numeric input ignored for: {names}"
                        )
                    saved_name = self._persist_settings_and_profile(
                        staged_cfg,
                        expert_profile_name=expert_profile_state["name"],
                        expert_profile_data=expert_profile_to_save,
                    )
                    if saved_name is not None:
                        expert_profile_state["name"] = saved_name
                        expert_profile_state["data"] = expert_profile_to_save
                    cfg.clear()
                    cfg.update(staged_cfg)
                    self.engine.cfg = cfg
                    configure_safe_int_debug(
                        cfg.get("opt_debug_safe_int", False),
                        self.controller.log
                    )
                    configure_duration_debug(
                        cfg.get("opt_debug_duration", False),
                        self.controller.log
                    )
                    self.controller.log("Settings saved.")
                except Exception as e:
                    self.controller.log(f"Error saving settings: {e}")
                    messagebox.showerror(
                        "Save Failed",
                        f"Settings could not be saved:\n{e}",
                        parent=win,
                    )
                    return
                try:
                    win.destroy()
                except Exception:
                    pass
                self._settings_window = None
                self._settings_notebook = None
                self._settings_tabs = {}
                done.set()

            def cancel():
                if not self._confirm_discard_dirty_expert_changes(
                    expert_profile_state.get("data"),
                    expert_vars,
                    "Discard unsaved Expert profile edits and close Settings?",
                    win,
                ):
                    return
                try:
                    win.destroy()
                except Exception:
                    pass
                finally:
                    self._settings_window = None
                    self._settings_notebook = None
                    self._settings_tabs = {}
                    done.set()

            win.protocol("WM_DELETE_WINDOW", cancel)

            tk.Button(
                btn_row, text="Save",
                bg=colors["green"], fg=colors["text"],
                font=("Segoe UI", 11, "bold"),
                width=12, command=save, relief="flat"
            ).pack(side="left", padx=4)
            tk.Button(
                btn_row, text="Cancel",
                bg=colors["toolbar_button"], fg=colors["toolbar_button_text"],
                font=("Segoe UI", 11),
                width=12, command=cancel, relief="flat"
            ).pack(side="left", padx=4)

        def _safe_show():
            try:
                _show()
            except Exception as e:
                self._settings_window = None
                self._settings_notebook = None
                self._settings_tabs = {}
                self.controller.log(f"Error opening settings: {e}")
                self.show_error("Settings Error", f"Could not open Settings:\n{e}")
                done.set()

        if threading.current_thread() is threading.main_thread():
            _safe_show()
        else:
            self.after(0, _safe_show)
            while not done.wait(timeout=0.1):
                if self.engine.abort_event.is_set():
                    return

    def start_indeterminate(self):
        def _start():
            self._set_progress_visibility(True)
            if self.progress_bar["mode"] != "indeterminate":
                self.progress_bar.config(mode="indeterminate")
            self.progress_bar.start(12)
        self.after(0, _start)

    def stop_indeterminate(self):
        def _stop():
            self.progress_bar.stop()
            if self.progress_bar["mode"] != "determinate":
                self.progress_bar.config(mode="determinate")
            self.progress_var.set(0)
            self._set_progress_visibility(False)
        self.after(0, _stop)

    def _init_taskbar(self):
        try:
            self._taskbar_progress = _TaskbarProgress(self.winfo_id())
        except Exception:
            self._taskbar_progress = None

    def _notify_complete(self, title=APP_DISPLAY_NAME, message="Rip complete."):
        """Send a Windows toast notification and play a completion beep."""
        if sys.platform != "win32":
            return
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass
        try:
            # Pass title and message as process arguments ($args[0], $args[1])
            # so no string escaping is needed and disc metadata cannot inject PS code.
            ps = (
                "[Windows.UI.Notifications.ToastNotificationManager,"
                " Windows.UI.Notifications, ContentType=WindowsRuntime]"
                " | Out-Null;"
                "$tpl = [Windows.UI.Notifications.ToastTemplateType]::ToastText02;"
                "$x = [Windows.UI.Notifications.ToastNotificationManager]"
                "::GetTemplateContent($tpl);"
                "$x.GetElementsByTagName('text')[0].AppendChild("
                "$x.CreateTextNode($args[0])) | Out-Null;"
                "$x.GetElementsByTagName('text')[1].AppendChild("
                "$x.CreateTextNode($args[1])) | Out-Null;"
                "$n = [Windows.UI.Notifications.ToastNotification]::new($x);"
                "[Windows.UI.Notifications.ToastNotificationManager]"
                "::CreateToastNotifier($args[2]).Show($n);"
            )
            _ps = get_powershell_executable()
            subprocess.Popen(
                [_ps, "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps,
                 title, message, APP_AUMID],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                **({"creationflags": 0x08000000} if sys.platform == "win32" else {}),
            )
        except Exception:
            pass

    def set_progress(self, value):
        def _update():
            self.progress_var.set(value if value is not None and value >= 0 else 0)
            show_progress = value is not None and value > 0
            self._set_progress_visibility(show_progress)
            if self._taskbar_progress:
                if value is None or value < 0:
                    self._taskbar_progress.clear()
                else:
                    self._taskbar_progress.set_value(int(value), 100)
        self.after(0, _update)

    def _main_status_style_for_message(self, msg):
        colors = getattr(self, "_theme", None) or build_app_theme()
        normalized = str(msg or "").strip().lower()
        if not normalized or normalized in {"ready", "idle"} or "choose a mode" in normalized:
            return (
                colors["pill_idle_bg"],
                colors["pill_idle_border"],
                colors["ready_text"],
            )
        if any(token in normalized for token in ("failed", "error", "missing", "invalid", "blocked", "unavailable")):
            return (
                colors["pill_error_bg"],
                colors["pill_error_border"],
                colors["pill_error_border"],
            )
        if any(token in normalized for token in ("attention", "warning", "aborting", "cancelled", "canceled", "retry", "waiting")):
            return (
                colors["pill_warn_bg"],
                colors["pill_warn_border"],
                colors["pill_warn_border"],
            )
        return (
            colors["pill_active_bg"],
            colors["pill_active_border"],
            colors["pill_active_border"],
        )

    def _apply_main_status_style(self, msg=None):
        bg, border, fg = self._main_status_style_for_message(
            self.status_var.get() if msg is None and hasattr(self, "status_var") else msg
        )
        if hasattr(self, "status_indicator"):
            self.status_indicator.configure(bg=bg, highlightbackground=border)
        if hasattr(self, "status_value_label"):
            self.status_value_label.configure(bg=bg, fg=fg)

    def set_status(self, msg):
        status_text = str(msg).strip() or "Ready"

        def _update():
            if hasattr(self, "status_var"):
                self.status_var.set(status_text)
            self._apply_main_status_style(status_text)

        self.after(0, _update)

    def append_log(self, msg):
        self.message_queue.put(msg)

    def process_queue(self):
        # Defensive: skip if log_text is not yet initialized
        if not hasattr(self, "log_text"):
            self.after(100, self.process_queue)
            return
        # Batch process log messages for better performance.
        # Collect up to 100 messages, then insert all at once
        # instead of state on/off 100 times.
        messages = []
        for _ in range(100):
            if self.message_queue.empty():
                break
            try:
                messages.append(self.message_queue.get_nowait())
            except queue_module.Empty:
                break
        if messages:
            with self._log_widget_lock:
                self.log_text.config(state="normal")
                at_bottom = self.log_text.yview()[1] > 0.95
                batch_text = "\n".join(messages) + "\n"
                self.log_text.insert("end", batch_text)
                # Trim widget (same cap/trim as _append_log_text_main).
                line_count = int(self.log_text.index("end").split(".")[0]) - 1
                cap = int(self.cfg.get("opt_log_cap_lines", 300000))
                if line_count > cap:
                    trim = int(self.cfg.get("opt_log_trim_lines", 200000))
                    self.log_text.delete("1.0", f"{line_count - trim}.0")
                # Only auto-scroll if the user was already near the bottom.
                if at_bottom:
                    self.log_text.see("end")
                self.log_text.config(state="disabled")
        self.after(100, self.process_queue)

    def disable_buttons(self):
        self._task_active = True
        for mode, btn in self.mode_buttons.items():
            state = "normal" if mode in CONCURRENT_MODE_KEYS else "disabled"
            btn.config(state=state)
        if hasattr(self, "settings_btn"):
            self.settings_btn.config(state="disabled")
        if hasattr(self, "update_btn"):
            self.update_btn.config(state="disabled")
        if hasattr(self, "abort_btn"):
            self.abort_btn.config(text="ABORT SESSION", state="normal")

    def enable_buttons(self):
        self._task_active = False
        for btn in self.mode_buttons.values():
            btn.config(state="normal")
        if hasattr(self, "settings_btn"):
            self.settings_btn.config(state="normal")
        if hasattr(self, "update_btn"):
            self.update_btn.config(state="normal")
        if hasattr(self, "abort_btn"):
            self.abort_btn.config(text="ABORT SESSION", state="disabled")

    def _has_abortable_session(self):
        if getattr(self, "_task_active", False):
            return True

        return self._has_live_abort_work()

    def _has_live_abort_work(self):
        rip_thread = getattr(self, "rip_thread", None)
        if rip_thread is not None:
            try:
                if rip_thread.is_alive():
                    return True
            except Exception:
                pass

        engine = getattr(self, "engine", None)
        proc = getattr(engine, "current_process", None) if engine is not None else None
        if proc is not None:
            try:
                if proc.poll() is None:
                    return True
            except Exception:
                return True

        return bool(getattr(self, "_input_active", False))

    def _schedule_abort_ui_recovery(self):
        if self.__dict__.get("_abort_ui_recovery_job") is not None:
            return
        after = self.__dict__.get("after")
        if not callable(after):
            if self.__dict__.get("tk") is None:
                return
            after = getattr(self, "after", None)
        if not callable(after):
            return
        self._abort_ui_recovery_job = after(
            120,
            self._poll_abort_ui_recovery,
        )

    def _poll_abort_ui_recovery(self):
        self._abort_ui_recovery_job = None
        engine = getattr(self, "engine", None)
        abort_event = getattr(engine, "abort_event", None)
        if abort_event is None or not abort_event.is_set():
            return
        if self._has_live_abort_work():
            self._schedule_abort_ui_recovery()
            return
        try:
            self.enable_buttons()
        except Exception:
            pass
        try:
            self.set_status("Ready")
        except Exception:
            pass

    def _release_abort_ui(self):
        if getattr(self, "_input_active", False):
            self._input_result = None
            try:
                self._hide_input_bar()
            except Exception:
                pass
            try:
                self._input_event.set()
            except Exception:
                pass

        grab_window = None
        try:
            grab_window = self.grab_current()
        except Exception:
            grab_window = None

        if grab_window in (None, self):
            return

        try:
            grab_window.grab_release()
        except Exception:
            pass
        try:
            grab_window.destroy()
        except Exception:
            pass

    def request_abort(self):
        """Abort immediately when a task is active."""
        if not self._has_abortable_session():
            self.controller.log("No active session to abort.")
            return
        if self.engine.abort_event.is_set():
            self._release_abort_ui()
            self._schedule_abort_ui_recovery()
            return
        self.controller.log("ABORT REQUESTED BY USER")
        self.set_status("Aborting...")
        self.abort_btn.config(text="ABORTING...", state="disabled")
        self.engine.abort()
        self._release_abort_ui()
        self._schedule_abort_ui_recovery()

    def on_close(self):
        if messagebox.askokcancel(
            "Exit", f"Close {APP_DISPLAY_NAME}?", parent=self
        ):
            self._remember_ai_sidebar_width()
            self._persist_config()
            self.engine.abort()
            # Attempt to join any running rip thread
            if self.rip_thread and self.rip_thread.is_alive():
                try:
                    self.rip_thread.join(timeout=3)
                except Exception:
                    pass
            self.destroy()

    def _pick_movie_mode(self):
        choice = self._run_on_main(
            lambda: messagebox.askyesnocancel(
                "Movie Mode",
                "Use Smart Rip for this movie disc?\n\n"
                "Yes = auto-pick main feature\n"
                "No = manual title selection\n"
                "Cancel = stop",
                parent=self,
            )
        )
        self._debug_ui_event(
            "movie_mode_prompt",
            choice=(
                "cancel" if choice is None else
                "yes" if choice else
                "no"
            ),
        )
        if choice is None:
            self.controller.log("Movie mode prompt cancelled before scan.")
            return None
        if choice:
            return self.controller.run_smart_rip
        return self.controller.run_movie_disc

    def start_task(self, mode):
        self._debug_ui_event("start_task_enter", mode=mode)
        if self.rip_thread and self.rip_thread.is_alive():
            messagebox.showwarning(
                "Busy",
                "Wait for current operation to finish.",
                parent=self
            )
            return

        ok, msg = self.engine.validate_tools()
        if not ok:
            messagebox.showerror(
                "Configuration Error", msg, parent=self
            )
            return

        makemkv_src = getattr(self.engine, "_makemkvcon_source", "")
        makemkv_path = getattr(self.engine, "_resolved_makemkvcon", "")
        if makemkv_src and makemkv_path:
            self.controller.log(
                f"MakeMKV resolved via {makemkv_src}: {makemkv_path}"
            )

        ffprobe_src = getattr(self.engine, "_ffprobe_source", "")
        ffprobe_path = getattr(self.engine, "_resolved_ffprobe", "")
        if ffprobe_src and ffprobe_path:
            self.controller.log(
                f"ffprobe resolved via {ffprobe_src}: {ffprobe_path}"
            )

        temp_folder = os.path.normpath(
            self.cfg.get("temp_folder", DEFAULTS["temp_folder"])
        )
        _safe_mode_keys = (
            "opt_file_stabilization",
            "opt_stabilize_required_polls",
            "opt_stabilize_timeout_seconds",
            "opt_move_verify_retries",
            "opt_expected_size_ratio_pct",
        )
        _safe_mode_snapshot = {k: self.cfg.get(k) for k in _safe_mode_keys}
        if self.cfg.get("opt_safe_mode", True):
            self.cfg["opt_file_stabilization"] = True
            self.cfg["opt_stabilize_required_polls"] = max(
                4, int(self.cfg.get("opt_stabilize_required_polls", 4))
            )
            self.cfg["opt_stabilize_timeout_seconds"] = max(
                90, int(self.cfg.get("opt_stabilize_timeout_seconds", 60))
            )
            self.cfg["opt_move_verify_retries"] = max(
                5, int(self.cfg.get("opt_move_verify_retries", 5))
            )
            self.cfg["opt_expected_size_ratio_pct"] = max(
                50, int(self.cfg.get("opt_expected_size_ratio_pct", 70))
            )

        if (not self.cfg.get("opt_first_run_done", False) and
                is_network_path(temp_folder)):
            messagebox.showwarning(
                "Network Temp Folder",
                "Your temp folder appears to be on a network/mapped "
                "drive. Network storage is slower and may cause "
                "incomplete rips. Local temp storage is recommended.\n\n"
                f"Current temp folder:\n{temp_folder}",
                parent=self
            )

        if not self.cfg.get("opt_first_run_done", False):
            self.cfg["opt_first_run_done"] = True
            self.engine.cfg["opt_first_run_done"] = True
            save_config(self.cfg)

        self.engine.reset_abort()
        self.controller.session_log          = []
        self.controller.session_report       = []
        self.controller.start_time           = datetime.now()
        self.controller.global_extra_counter = 1
        self.disable_buttons()
        self.set_progress(0)

        targets = {
            "t":  self.controller.run_tv_disc,
            "m":  self._pick_movie_mode,
            "sr": self.controller.run_smart_rip,
            "d":  self.controller.run_dump_all,
            "i":  self.controller.run_organize,
        }
        target = targets.get(mode, self.controller.run_organize)
        needs_pick = mode == "m"

        def task_wrapper():
            _success = False
            try:
                # Important: mode pickers use ask_yesno(), which schedules UI
                # work on the main thread and waits from the caller thread.
                # Resolve picker targets here (background thread), not in
                # start_task() on the main thread, to avoid UI deadlocks.
                fn = target() if needs_pick else target
                # If abort was requested during the mode picker prompt,
                # don't start the rip — just bail out cleanly.
                if self.engine.abort_event.is_set():
                    return
                if fn is None:
                    self.set_status("Ready")
                    return
                fn()
                _success = True
            except Exception as e:
                self.controller.log(f"Unhandled error: {e}")
                # AI diagnostics: record crash and dump ring buffer
                try:
                    from shared.ai_diagnostics import diag_exception, get_diagnostics
                    diag_exception(e, context="GUI task_wrapper top-level crash")
                    mgr = get_diagnostics()
                    if mgr:
                        path = mgr.dump_ring_buffer()
                        self.controller.log(f"[AI] Crash buffer dumped to: {path}")
                except Exception:
                    pass
                self.after(0, lambda msg=str(e): self._notify_complete(
                    f"{APP_DISPLAY_NAME} - Error", f"Rip failed: {msg}"
                ))
            finally:
                # Restore safe-mode-overridden config keys so Settings shows
                # the user's actual values, not the enforced minimums.
                for k, v in _safe_mode_snapshot.items():
                    if v is None:
                        self.cfg.pop(k, None)
                    else:
                        self.cfg[k] = v
                self.stop_indeterminate()
                self.after(0, self.enable_buttons)
                self.set_status("Ready")
                if not self.engine.abort_event.is_set():
                    # Determine session result
                    from utils.session_result import normalize_session_result
                    abort = self.engine.abort_event.is_set()
                    failed_titles = getattr(self.controller, "failed_titles", [])
                    files = getattr(self.controller, "session_files", [])
                    valid_files = getattr(self.controller, "valid_files", files)
                    is_full_success = normalize_session_result(abort, failed_titles, files, valid_files)
                    is_partial = (not is_full_success) and bool(files)
                    if is_full_success:
                        self.after(0, lambda: self._notify_complete(
                            APP_DISPLAY_NAME, "Rip complete!"
                        ))
                    elif is_partial:
                        def handle_partial():
                            accept = self.ask_accept_partial()
                            if accept:
                                self._notify_complete(
                                    APP_DISPLAY_NAME,
                                    "Partial rip accepted. Files and metadata kept.",
                                )
                            else:
                                # Delete session folder and metadata
                                session_dir = getattr(self.controller, "session_dir", None)
                                if session_dir and os.path.exists(session_dir):
                                    import shutil
                                    try:
                                        shutil.rmtree(session_dir)
                                        self._notify_complete(
                                            APP_DISPLAY_NAME,
                                            "Partial rip deleted. Session and files removed.",
                                        )
                                    except Exception as e:
                                        self._notify_complete(
                                            APP_DISPLAY_NAME,
                                            f"Error deleting session: {e}",
                                        )
                                else:
                                    self._notify_complete(
                                        APP_DISPLAY_NAME,
                                        "Session directory not found. Nothing deleted.",
                                    )
                        self.after(0, handle_partial)
                    else:
                        self.after(0, lambda: self._notify_complete(
                            APP_DISPLAY_NAME, "Rip failed. No files kept."
                        ))

        self.rip_thread = threading.Thread(
            target=task_wrapper, daemon=True
        )
        self.rip_thread.start()


if __name__ == "__main__":
    # Example: direct AppConfig construction for testing or alternate entry
    # config = AppConfig(source="/path/to/makemkvcon", output="/path/to/ffprobe", quality="high")
    startup = load_startup_config()
    app = JellyRipperGUI(
        startup.config,
        startup_context={
            "issues": [issue.message for issue in startup.issues],
            "open_settings": startup.open_settings,
        },
    )
    app.mainloop()

__all__ = ["JellyRipperGUI"]
