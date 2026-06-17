"""Main entrypoint for the split package layout (AI BRANCH).

Qt-only path — mirrors MAIN's main.py after Phase 3h.  AI BRANCH
keeps its own ``APP_DISPLAY_NAME = "JellyRip AI"`` and
``APP_AUMID = "JellyRip.AI.1"`` constants from ``shared/runtime.py``;
the Qt UI itself is the same code as MAIN.

**Multi-instance / multi-drive support (added 2026-05-05):**

Run a separate instance per optical drive by passing
``--profile NAME``::

    JellyRipAI.exe --profile drive-a
    JellyRipAI.exe --profile drive-b

Each profile gets its own config dir, log file, and Windows
taskbar identity — the two windows don't fight over state, and
each can target a different ``opt_drive_index`` so both rip in
parallel.  Without ``--profile`` the install behaves exactly like
before (default config dir, single instance).

The flag is parsed BEFORE any other imports so
``shared.runtime``'s module-level ``CONFIG_FILE`` and the
``DEFAULTS["log_file"]`` value see the active profile from
``$JELLYRIP_PROFILE`` and route to ``profiles/<NAME>/``.
"""

import os
import sys
from pathlib import Path


def _bootstrap_profile_from_argv() -> None:
    """Pull ``--profile NAME`` (or ``--profile=NAME``) out of
    ``sys.argv`` and stash it in the env var that
    ``shared.runtime`` reads.  Runs BEFORE anything else imports
    ``shared.runtime`` so the per-profile config dir takes effect
    on first read.

    Strips the flag from ``sys.argv`` so downstream parsers don't
    trip on it.  No-op if the flag isn't present.
    """
    argv = sys.argv
    consumed: list[int] = []
    profile = ""
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--profile":
            if i + 1 < len(argv):
                profile = argv[i + 1].strip()
                consumed.extend((i, i + 1))
                i += 2
                continue
            consumed.append(i)
            i += 1
            continue
        if arg.startswith("--profile="):
            profile = arg.split("=", 1)[1].strip()
            consumed.append(i)
            i += 1
            continue
        i += 1
    if consumed:
        sys.argv = [a for idx, a in enumerate(argv) if idx not in set(consumed)]
    if profile:
        os.environ["JELLYRIP_PROFILE"] = profile


_bootstrap_profile_from_argv()


from config import load_startup_config
from shared.runtime import (
    APP_AUMID,
    get_active_profile,
    get_config_dir,
    get_profile_aumid,
    get_profile_window_title,
)


class _NullStartupWindow:
    """No-op startup-status holder — fallback when the Qt splash
    can't be built (no display, PySide6 failing to load, etc.)."""

    def set_status(self, _message: str) -> None:
        return None

    def close(self) -> None:
        return None

    def finish_for(self, _window: "object") -> None:
        return None


def _bootstrap_tk_paths() -> None:
    """Set Tcl/Tk library paths defensively.

    Carried over from the tkinter era as a safety net.  Phase 4
    close-out (2026-05-05) retired tkinter as a UI dependency, but
    PyInstaller's bundled Python still ships Tcl/Tk by default and
    the bundled python's auto-discovery occasionally misfires —
    this stub points it at the right place so a stray ``import
    tkinter`` from a third-party module doesn't kill startup.

    Same shape as MAIN's equivalent in ``main.py`` so the shared
    test contract (``tests/test_security_hardening.py``) is
    satisfied without divergence.
    """
    if sys.platform != "win32":
        return
    if os.environ.get("TCL_LIBRARY") and os.environ.get("TK_LIBRARY"):
        return

    candidate_roots: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = Path(str(getattr(sys, "_MEIPASS", "")))
        candidate_roots.append(meipass)
        candidate_roots.append(meipass / "tcl")

    base_prefix = Path(str(getattr(sys, "base_prefix", sys.prefix)))
    candidate_roots.append(base_prefix / "tcl")

    for root in candidate_roots:
        if root.name == "tcl":
            tcl_dir = root / "tcl8.6"
            tk_dir = root / "tk8.6"
        else:
            tcl_dir = root / "_tcl_data"
            tk_dir = root / "_tk_data"
        if (tcl_dir / "init.tcl").is_file() and tk_dir.is_dir():
            os.environ.setdefault("TCL_LIBRARY", str(tcl_dir))
            os.environ.setdefault("TK_LIBRARY", str(tk_dir))
            return

        tcl_dir = root / "tcl8.6"
        tk_dir = root / "tk8.6"
        if (tcl_dir / "init.tcl").is_file() and tk_dir.is_dir():
            os.environ.setdefault("TCL_LIBRARY", str(tcl_dir))
            os.environ.setdefault("TK_LIBRARY", str(tk_dir))
            return


def _set_windows_app_user_model_id() -> None:
    if sys.platform != "win32":
        return

    import ctypes
    import logging

    try:
        # Use the profile-aware AUMID so two instances pinned to
        # different drives appear as separate apps on the taskbar.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            get_profile_aumid()
        )
    except Exception as e:
        logging.warning("SetCurrentProcessExplicitAppUserModelID failed: %s", e)


def _prepare_startup_environment() -> None:
    _bootstrap_tk_paths()
    get_config_dir()
    # Sweep any leftover watch/preview clips from a prior run (a
    # full-title watch the user never continued past, or a failed
    # preview) so disposable clips never pile up in local temp.
    try:
        from controller.legacy_compat import purge_preview_temp
        purge_preview_temp()
    except Exception:
        pass


def _read_show_splash_pref() -> bool:
    """Cheap config peek — reads just ``opt_show_splash`` from
    config.json without going through the full validating loader.

    Used by ``_create_startup_window`` because the splash needs to
    decide whether to render BEFORE ``load_startup_config`` runs.
    Defaults to True on any failure.
    """
    try:
        from shared.runtime import get_config_dir
        config_path = get_config_dir() / "config.json"
        if not config_path.is_file():
            return True
        import json
        with config_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return bool(data.get("opt_show_splash", True))
    except Exception:
        return True


def _create_startup_window():
    """Build the splash screen.

    Tries the Qt-native ``JellyRipSplash`` first; falls back to
    ``_NullStartupWindow`` if disabled or if anything goes wrong.
    """
    import logging

    if not _read_show_splash_pref():
        return _NullStartupWindow()

    try:
        from gui_qt.splash import JellyRipSplash
        from shared.runtime import __version__ as _version
    except Exception as e:
        logging.warning("Splash unavailable, falling back to no-op: %s", e)
        return _NullStartupWindow()

    try:
        return JellyRipSplash(version=_version)
    except Exception as e:
        logging.warning("Splash construction failed: %s", e)
        return _NullStartupWindow()


def _install_crash_logging() -> None:
    """Persist unhandled exceptions + native faults to ``crash.log``.

    The windowed exe has no console: ``sys.stderr`` is None, so
    without this every post-startup exception — Qt slot exceptions
    included; PySide6 routes them through ``sys.excepthook`` — used to
    vanish silently, and a native fault (access violation, Qt plugin
    abort) showed nothing at all.  The log lives in the profile's
    config dir next to config.json.
    """
    import faulthandler
    import traceback
    from datetime import datetime

    try:
        crash_path = os.path.join(str(get_config_dir()), "crash.log")
        # Size cap: roll the previous log aside at ~1 MB so a crash
        # loop can't grow it without bound.
        try:
            if (
                os.path.exists(crash_path)
                and os.path.getsize(crash_path) > 1_000_000
            ):
                os.replace(crash_path, crash_path + ".1")
        except OSError:
            pass
        crash_file = open(  # noqa: SIM115 — must outlive this function
            crash_path, "a", encoding="utf-8", buffering=1,
        )
    except Exception:
        return  # nowhere safe to write — never break startup over this

    try:
        # faulthandler needs a live fd at fault time; the handle stays
        # open for the process lifetime on purpose.
        faulthandler.enable(file=crash_file)
    except Exception:
        pass

    previous_hook = sys.excepthook

    def _hook(exc_type, exc, tb):
        try:
            crash_file.write(
                f"\n=== Unhandled exception at "
                f"{datetime.now():%Y-%m-%d %H:%M:%S} ===\n"
            )
            traceback.print_exception(exc_type, exc, tb, file=crash_file)
            crash_file.flush()
        except Exception:
            pass
        try:
            previous_hook(exc_type, exc, tb)
        except Exception:
            pass

    sys.excepthook = _hook


def main() -> None:
    _prepare_startup_environment()
    _install_crash_logging()
    _set_windows_app_user_model_id()

    startup_window = _create_startup_window()
    try:
        startup_window.set_status("Loading settings...")
        startup = load_startup_config()

        startup_window.set_status("Loading interface...")
        from gui_qt.app import run_qt_app
        # Keep the splash visible through ``run_qt_app``'s window
        # construction.  ``run_qt_app`` calls
        # ``startup_window.finish_for(window)`` right after
        # ``window.show()``, so the splash fades cleanly into the
        # real UI without a visible gap.
        raise SystemExit(run_qt_app(startup.config, splash=startup_window))
    finally:
        startup_window.close()


if __name__ == "__main__":
    main()


# -----------------------------------------------------------------------------
# Project file map (quick navigation when chat history is unavailable)
# -----------------------------------------------------------------------------
# Main GUI entrypoint: gui_qt/app.py (run_qt_app)
# Startup splash (Qt-native): gui_qt/splash.py (JellyRipSplash)
# GUI window + screens: gui_qt/ (main_window, setup_wizard, settings, etc.)
# Workflow/controller logic: controller/controller.py
# Disc + file operations engine: engine/ripper_engine.py
# Config load/save and defaults bridge: config.py
# Shared defaults/runtime primitives: shared/runtime.py
# Shared dataclasses (wizard + session setup): shared/wizard_types.py + shared/session_setup_types.py
# Utility exports: utils/__init__.py
# Session state machine: utils/state_machine.py
# Fallback policy gateway: utils/fallback.py
# File selection helpers: utils/media.py
# Behavioral tests: tests/test_behavior_guards.py
# AI provider dialog (Qt port pending): gui/ai_provider_dialog.py → gui_qt/dialogs/ai_provider.py
# AI identity assist (backend, no UI): controller/assist.py
# AI workflow history (backend, no UI): shared/workflow_history.py
# AI provider abstraction: shared/ai/providers/
