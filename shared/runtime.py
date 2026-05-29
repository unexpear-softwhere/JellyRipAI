"""Shared runtime primitives used by split modules and the compatibility shim."""

from collections.abc import Callable
from typing import Protocol, TypeAlias

from shared.ai_profile import DEFAULT_AI_PROFILE


class GuiCallbacks(Protocol):
    def append_log(self, msg: str) -> None: ...
    def set_status(self, msg: str) -> None: ...
    def set_progress(self, value: int) -> None: ...
    def start_indeterminate(self) -> None: ...
    def stop_indeterminate(self) -> None: ...
    def ask_yesno(self, prompt: str) -> bool: ...

import glob
import json
import os
import platform
import queue as queue_module
import re
import shlex
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

__version__ = "1.0.23"
APP_VARIANT = "ai"
APP_DISPLAY_NAME = "JellyRip AI"
APP_EXE_BASENAME = "JellyRipAI"
APP_EXE_NAME = f"{APP_EXE_BASENAME}.exe"
APP_INSTALLER_NAME = f"{APP_EXE_BASENAME}Installer.exe"
APP_CONFIG_DIR_NAME = "JellyRipAI"
APP_LOCAL_INSTALL_DIR_NAME = "JellyRip AI"
APP_RELEASE_REPO = "unexpear-softwhere/JellyRipAI"
APP_RELEASE_TAG_PREFIX = "ai-v"
APP_AUMID = "JellyRip.AI.1"
APP_UPDATER_ASSET_PREFERENCE = (
    APP_INSTALLER_NAME,
    APP_EXE_NAME,
)

ConfigScalar: TypeAlias = str | int | bool | float | dict[str, str]
LogFn: TypeAlias = Callable[[str], None]


_PROFILE_ENV_VAR = "JELLYRIP_PROFILE"
_PROFILE_NAME_RE = __import__("re").compile(r"[^A-Za-z0-9_\-]")


def _sanitize_profile_name(name: str) -> str:
    """Strip filesystem-unsafe characters from a profile name.

    Profiles map to a directory under
    ``%APPDATA%\\<APP_CONFIG_DIR_NAME>\\profiles\\<NAME>``, so any
    character a path-aware OS would hate (slashes, colons, periods,
    spaces) gets replaced with ``_``.  Empty after sanitization →
    treat as no profile (use the default config dir).
    """
    cleaned = _PROFILE_NAME_RE.sub("_", str(name or "").strip())
    cleaned = cleaned.strip("_")
    return cleaned


def get_active_profile() -> str:
    """Return the active profile name, or empty string for the
    default (no-profile) install.

    Profile is selected via the ``JELLYRIP_PROFILE`` environment
    variable.  ``main.py`` parses ``--profile NAME`` from
    ``sys.argv`` very early and sets this env var so that every
    subsequent ``shared.runtime`` import sees the same profile —
    including ``CONFIG_FILE`` which is computed at module-load time.

    Empty string is the default profile (legacy behavior; backwards
    compatible with installs that predate profile support).
    """
    return _sanitize_profile_name(os.environ.get(_PROFILE_ENV_VAR, ""))


def _config_dir_path() -> str:
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    elif system == "Darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get(
            "XDG_CONFIG_HOME", os.path.expanduser("~/.config")
        )
    root = os.path.join(base, APP_CONFIG_DIR_NAME)
    profile = get_active_profile()
    if profile:
        return os.path.join(root, "profiles", profile)
    return root


def get_config_dir(create: bool = True) -> str:
    config_dir = _config_dir_path()
    if create:
        os.makedirs(config_dir, exist_ok=True)
    return config_dir


def get_profile_aumid(base: str = APP_AUMID) -> str:
    """AUMID extended with the active profile so Windows treats
    each profile as a separate app on the taskbar.  No profile →
    returns ``base`` unchanged."""
    profile = get_active_profile()
    return f"{base}.{profile}" if profile else base


def get_profile_window_title(base: str = APP_DISPLAY_NAME) -> str:
    """Window-title string with the active profile suffixed in
    parens.  No profile → returns ``base`` unchanged."""
    profile = get_active_profile()
    return f"{base} — {profile}" if profile else base


def get_profile_log_file_default() -> str:
    """Default log-file path that's distinct per profile so two
    instances don't trample each other.  Used as the default for
    ``DEFAULTS['log_file']`` when no profile config exists yet."""
    profile = get_active_profile()
    base = os.path.expanduser("~/Downloads/rip_log")
    if profile:
        return f"{base}_{profile}.txt"
    return f"{base}.txt"


CONFIG_FILE = os.path.join(_config_dir_path(), "config.json")

_WIN_TEMP = os.environ.get("TEMP") or os.path.expanduser("~\\AppData\\Local\\Temp")
_WIN_HOME = os.environ.get("USERPROFILE") or os.path.expanduser("~")
_WIN_VIDEOS = os.path.join(_WIN_HOME, "Videos")
_DEFAULT_TEMP = _WIN_TEMP if platform.system() == "Windows" else os.path.expanduser("~/tmp")
_DEFAULT_TV = (
    os.path.join(_WIN_VIDEOS, "TV Shows")
    if platform.system() == "Windows"
    else os.path.expanduser("~/Videos/TV Shows")
)
_DEFAULT_MOVIES = (
    os.path.join(_WIN_VIDEOS, "Movies")
    if platform.system() == "Windows"
    else os.path.expanduser("~/Videos/Movies")
)
_DEFAULT_MAKEMKVCON = (
    r"C:\Program Files (x86)\MakeMKV\makemkvcon64.exe"
    if platform.system() == "Windows" and os.environ.get("ProgramW6432")
    else r"C:\Program Files (x86)\MakeMKV\makemkvcon.exe"
)

DEFAULTS: dict[str, ConfigScalar] = {
    "makemkvcon_path": _DEFAULT_MAKEMKVCON,
    "ffprobe_path": "",
    "ffmpeg_path": "",
    "handbrake_path": "",
    "temp_folder": _DEFAULT_TEMP,
    "tv_folder": _DEFAULT_TV,
    "movies_folder": _DEFAULT_MOVIES,
    "log_file": get_profile_log_file_default(),
    "opt_save_logs": True,
    "opt_drive_index": 0,
    "opt_safe_mode": True,
    "opt_first_run_done": False,
    "opt_scan_disc_size": True,
    "opt_confirm_before_rip": True,
    "opt_clean_mkv_before_retry": True,
    "opt_stall_detection": True,
    "opt_stall_timeout_seconds": 120,
    "opt_user_prompt_timeout_enabled": False,
    "opt_user_prompt_timeout_seconds": 300,
    "opt_disc_swap_timeout_enabled": False,
    "opt_disc_swap_timeout_seconds": 300,
    # How long ``_disc_present`` waits for a drive to either report
    # a disc or time out, in seconds.  Read by
    # ``controller/controller.py:_disc_present`` with a hardcoded
    # 45-second fallback; surfacing here so the value is discoverable
    # and tweakable from config.json (and eventually from Settings).
    "opt_disc_presence_probe_seconds": 45,
    "opt_file_stabilization": True,
    "opt_stabilize_timeout_seconds": 60,
    "opt_stabilize_required_polls": 4,
    "opt_min_rip_size_gb": 1,
    "opt_expected_size_ratio_pct": 70,
    "opt_hard_fail_ratio_pct": 40,
    "opt_smart_low_confidence_threshold": 0.45,
    "opt_smart_auto_pick_threshold": 0.70,
    "opt_move_verify_retries": 5,
    "opt_auto_retry": True,
    "opt_retry_attempts": 3,
    "opt_check_dest_space": True,
    "opt_confirm_before_move": True,
    "opt_atomic_move": True,
    "opt_fsync": True,
    "opt_allow_path_tool_resolution": False,
    "opt_show_temp_manager": True,
    "opt_auto_delete_temp": True,
    "opt_auto_delete_session_metadata": True,
    "opt_clean_partials_startup": True,
    "opt_warn_low_space": True,
    "opt_hard_block_gb": 20,
    "opt_warn_out_of_order_episodes": True,
    "opt_debug_safe_int": False,
    "opt_debug_duration": False,
    "opt_debug_state": False,
    "opt_debug_state_json": False,
    "opt_strict_mode": False,
    "opt_session_failure_report": True,
    "opt_plain_english_profile_summary": False,
    "opt_log_cap_lines": 300000,
    "opt_log_trim_lines": 200000,
    "opt_smart_rip_mode": False,
    "opt_smart_min_minutes": 20,
    "opt_naming_mode": "timestamp",
    "opt_extras_folder_mode": "single",
    "opt_bonus_folder_name": "featurettes",
    "opt_ffmpeg_source_mode": "safe_copy",
    "opt_minlength_seconds": 0,
    "opt_makemkv_global_args": "",
    "opt_makemkv_info_args": "",
    "opt_makemkv_rip_args": "",
    "opt_update_require_signature": True,
    "opt_update_signer_thumbprint": "",
    # PySide6 UI (Phase 4 — AI BRANCH inherits MAIN's Qt UI defaults).
    # Selected QSS theme name (without .qss extension); switchable
    # live from Settings -> Themes.  Available themes live under
    # ``gui_qt/qss/``.
    "opt_pyside6_theme": "dark_github",
    # Appearance-tab toggles inherited from MAIN.  All default True
    # so existing AI BRANCH config.json files see no behavior change
    # after the Phase 4 graft; see
    # ``docs/handoffs/appearance-tab-spec.md`` for the rationale.
    "opt_log_color_levels": True,    # auto-color warn/error in the live log
    "opt_log_glyph_prefix": True,    # prepend ⚠/✗ to warn/error log lines
    "opt_drive_state_glyph": True,   # prefix ◉/⊚/◌ before disc name in drive picker
    "opt_tray_icon_enabled": True,   # system-tray companion for long rips
    "opt_show_splash": True,         # startup splash screen (next-launch only)
    # Drive-probe retry tuning.  Harmonized 2026-05-08 with MAIN:
    # 5 retries × 2s base backoff (capped at 8s) → max wait
    # 2+4+8+8+8 = 30s before giving up on an unresponsive drive.
    # Was hidden behind in-code defaults (5 / 1.0) on the AI side
    # only, so the Settings UI couldn't expose them.
    "opt_drive_probe_retries": 5,
    "opt_drive_probe_backoff_seconds": 2.0,
    # AI diagnostics
    "opt_ai_diagnostics_enabled": True,
    "opt_ai_diagnostics_mode": "suggest",
    "opt_ai_log_to_gui": True,
    "opt_ai_log_to_file": True,
    "opt_ai_capture_raw_process_output": True,
    "opt_ai_emit_state_json": True,
    # AI backend routing
    "opt_ai_mode": "cloud",  # "off" | "cloud" | "local"
    "opt_ai_cloud_enabled": True,
    "opt_ai_local_enabled": True,
    "opt_ai_local_provider": "ollama",
    "opt_ai_local_model": "qwen2.5:14b-instruct",
    "opt_ai_cloud_timeout_seconds": 30,
    "opt_ai_local_timeout_seconds": 90,
    "opt_ai_max_calls_per_session": 20,
    "opt_ai_disable_after_failures": 3,
    # AI provider connection (managed via gui/ai_provider_dialog.py)
    "opt_ai_active_cloud_provider": "",  # "claude" | "openai" | "gemini" | ""
    "opt_ai_profile": dict(DEFAULT_AI_PROFILE),
    "opt_ai_profile_onboarded": False,
    "opt_ai_sidebar_open": False,
    "opt_ai_sidebar_width": 360,
    # 🌐 Web lookup (chat): when on, the assistant searches DuckDuckGo
    # (and TMDB, if a key is set) before answering.
    "opt_ai_web_search": False,
    "opt_tmdb_api_key": "",  # free key from themoviedb.org enables TMDB lookup
    "opt_theme_overrides": {},
}

RIP_ATTEMPT_FLAGS: list[list[str]] = [
    ["--cache=1024"],
    ["--noscan", "--cache=1024"],
    ["--noscan", "--directio=true", "--cache=512"],
]


_SAFE_INT_DEBUG_ENABLED = False
_SAFE_INT_DEBUG_LOG_FN: LogFn | None = None
_SAFE_INT_WARNED_VALUES: set[str] = set()
_SAFE_INT_WARNED_LIMIT_REACHED = False
_SAFE_INT_WARN_MAX_UNIQUE = 50

_DURATION_DEBUG_ENABLED = False
_DURATION_DEBUG_LOG_FN: LogFn | None = None
_DURATION_WARNED_VALUES: set[str] = set()
_DURATION_WARNED_LIMIT_REACHED = False
_DURATION_WARN_MAX_UNIQUE = 50


def configure_safe_int_debug(enabled: bool = False, log_fn: LogFn | None = None) -> None:
    global _SAFE_INT_DEBUG_ENABLED
    global _SAFE_INT_DEBUG_LOG_FN
    _SAFE_INT_DEBUG_ENABLED = bool(enabled)
    _SAFE_INT_DEBUG_LOG_FN = log_fn


def configure_duration_debug(enabled: bool = False, log_fn: LogFn | None = None) -> None:
    global _DURATION_DEBUG_ENABLED
    global _DURATION_DEBUG_LOG_FN
    _DURATION_DEBUG_ENABLED = bool(enabled)
    _DURATION_DEBUG_LOG_FN = log_fn


def _safe_int_debug_warn(val: object) -> None:
    global _SAFE_INT_WARNED_LIMIT_REACHED

    if not _SAFE_INT_DEBUG_ENABLED:
        return

    token = str(val).strip()
    if len(token) > 80:
        token = token[:77] + "..."
    key = token or "<empty>"

    if key in _SAFE_INT_WARNED_VALUES:
        return

    if len(_SAFE_INT_WARNED_VALUES) >= _SAFE_INT_WARN_MAX_UNIQUE:
        if not _SAFE_INT_WARNED_LIMIT_REACHED:
            _SAFE_INT_WARNED_LIMIT_REACHED = True
            msg = (
                "DEBUG safe_int: warning limit reached; "
                "suppressing additional unique parse warnings."
            )
            if _SAFE_INT_DEBUG_LOG_FN:
                _SAFE_INT_DEBUG_LOG_FN(msg)
            else:
                print(msg)
        return

    _SAFE_INT_WARNED_VALUES.add(key)
    msg = f"DEBUG safe_int: could not parse {key!r}; defaulting to 0"
    if _SAFE_INT_DEBUG_LOG_FN:
        _SAFE_INT_DEBUG_LOG_FN(msg)
    else:
        print(msg)


def _duration_debug_warn(val: object) -> None:
    global _DURATION_WARNED_LIMIT_REACHED

    if not _DURATION_DEBUG_ENABLED:
        return

    token = str(val).strip()
    if len(token) > 80:
        token = token[:77] + "..."
    key = token or "<empty>"

    if key in _DURATION_WARNED_VALUES:
        return

    if len(_DURATION_WARNED_VALUES) >= _DURATION_WARN_MAX_UNIQUE:
        if not _DURATION_WARNED_LIMIT_REACHED:
            _DURATION_WARNED_LIMIT_REACHED = True
            msg = (
                "DEBUG duration: warning limit reached; "
                "suppressing additional unique parse warnings."
            )
            if _DURATION_DEBUG_LOG_FN:
                _DURATION_DEBUG_LOG_FN(msg)
            else:
                print(msg)
        return

    _DURATION_WARNED_VALUES.add(key)
    msg = f"DEBUG duration: could not parse {key!r}; defaulting to 0"
    if _DURATION_DEBUG_LOG_FN:
        _DURATION_DEBUG_LOG_FN(msg)
    else:
        print(msg)


def safe_int_debug_warn(val: object) -> None:
    _safe_int_debug_warn(val)


def duration_debug_warn(val: object) -> None:
    _duration_debug_warn(val)


__all__ = [
    # Runtime constants
    "CONFIG_FILE",
    "DEFAULTS",
    "RIP_ATTEMPT_FLAGS",
    "__version__",
    # Config helpers
    "get_config_dir",
    # Debug helpers
    "_duration_debug_warn",
    "_safe_int_debug_warn",
    "configure_duration_debug",
    "configure_safe_int_debug",
]
