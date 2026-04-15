"""Main entrypoint for the split package layout."""

import os
import sys
from pathlib import Path

from config import load_startup_config
from shared.runtime import get_config_dir

JellyRipperGUI = None


class _NullStartupWindow:
    def set_status(self, _message: str) -> None:
        return None

    def close(self) -> None:
        return None


class _StartupWindow:
    def __init__(self) -> None:
        self._root = None
        self._status_var = None

        try:
            import tkinter as tk
        except Exception:
            return

        root = None
        try:
            root = tk.Tk()
            root.title("JellyRip")
            root.configure(bg="#091321")
            root.resizable(False, False)
            root.protocol("WM_DELETE_WINDOW", lambda: None)

            width = 420
            height = 140
            frame = tk.Frame(root, bg="#091321", padx=22, pady=18)
            frame.pack(fill="both", expand=True)

            title = tk.Label(
                frame,
                text="JellyRip",
                bg="#091321",
                fg="#27b8ff",
                font=("Segoe UI", 16, "bold"),
                anchor="w",
            )
            title.pack(fill="x")

            subtitle = tk.Label(
                frame,
                text="Starting up",
                bg="#091321",
                fg="#f5f9ff",
                font=("Segoe UI", 10),
                anchor="w",
                pady=6,
            )
            subtitle.pack(fill="x")

            self._status_var = tk.StringVar(value="Preparing startup...")
            status = tk.Label(
                frame,
                textvariable=self._status_var,
                bg="#091321",
                fg="#a8b7ca",
                font=("Segoe UI", 9),
                anchor="w",
            )
            status.pack(fill="x")

            root.update_idletasks()
            screen_width = max(1, int(root.winfo_screenwidth()))
            screen_height = max(1, int(root.winfo_screenheight()))
            pos_x = max(0, (screen_width - width) // 2)
            pos_y = max(0, (screen_height - height) // 2)
            root.geometry(f"{width}x{height}+{pos_x}+{pos_y}")
            root.update()
            self._root = root
        except Exception:
            if root is not None:
                try:
                    root.destroy()
                except Exception:
                    pass
            self._root = None
            self._status_var = None

    def set_status(self, message: str) -> None:
        root = self._root
        status_var = self._status_var
        if root is None or status_var is None:
            return
        try:
            status_var.set(str(message).strip() or "Loading...")
            root.update_idletasks()
            root.update()
        except Exception:
            pass

    def close(self) -> None:
        root = self._root
        self._root = None
        self._status_var = None
        if root is None:
            return
        try:
            root.destroy()
        except Exception:
            pass


def _load_env_file() -> None:
    """Load .env file from app directory if present (for API keys etc.)."""
    env_path = Path(__file__).parent / ".env"
    if not env_path.is_file():
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value

def _bootstrap_tk_paths() -> None:
    """Set Tcl/Tk library paths when Python's auto-discovery is broken."""
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
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "JellyRip.App.1"
        )
    except Exception as e:
        logging.warning("SetCurrentProcessExplicitAppUserModelID failed: %s", e)


def _prepare_startup_environment() -> None:
    _load_env_file()
    _bootstrap_tk_paths()
    get_config_dir()


def _resolve_gui_class():
    global JellyRipperGUI
    if JellyRipperGUI is None:
        from gui.main_window import JellyRipperGUI as _JellyRipperGUI

        JellyRipperGUI = _JellyRipperGUI
    return JellyRipperGUI


def _create_startup_window():
    try:
        return _StartupWindow()
    except Exception:
        return _NullStartupWindow()




def main() -> None:
    _prepare_startup_environment()
    _set_windows_app_user_model_id()

    startup_window = _create_startup_window()
    try:
        startup_window.set_status("Loading settings...")
        startup = load_startup_config()
        startup_window.set_status("Loading interface...")
        gui_class = _resolve_gui_class()
        startup_window.set_status("Opening app...")
        startup_window.close()
        startup_window = _NullStartupWindow()
        app = gui_class(
            startup.config,
            startup_context={
                "issues": [issue.message for issue in startup.issues],
                "open_settings": startup.open_settings,
            },
        )
    finally:
        startup_window.close()

    try:
        app.mainloop()
    except KeyboardInterrupt:
        engine = getattr(app, "engine", None)
        abort = getattr(engine, "abort", None)
        if callable(abort):
            abort()
        try:
            app.destroy()
        except Exception:
            pass
        print("console interrupt", file=sys.stderr)
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()


# -----------------------------------------------------------------------------
# Project file map (quick navigation when chat history is unavailable)
# -----------------------------------------------------------------------------
# Main GUI entrypoint: gui/main_window.py
# Workflow/controller logic: controller/controller.py
# Disc + file operations engine: engine/ripper_engine.py
# Config load/save and defaults bridge: config.py
# Shared defaults/runtime primitives: shared/runtime.py
# Utility exports: utils/__init__.py
# Session state machine: utils/state_machine.py
# Fallback policy gateway: utils/fallback.py
# File selection helpers: utils/media.py
# Behavioral tests: tests/test_behavior_guards.py
