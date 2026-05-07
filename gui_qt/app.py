"""PySide6 application entry point.

Sub-phase 3c-ii second pass added the workflow launcher wiring —
``app.py`` now constructs ``RipperEngine`` + ``RipperController`` +
``WorkflowLauncher`` alongside the ``MainWindow``, mirroring the
tkinter init order at ``gui/main_window.py:368``.

Workflow buttons now reach the controller; status / progress / log
callbacks marshal back via ``gui_qt.thread_safety``.

Still pending in 3c-ii:

* ``ask_tv_setup`` / ``ask_movie_setup`` — multi-field forms
* ``show_disc_tree`` — tree-view selector dialog
* ``show_temp_manager`` — separate window (3c-iii territory)

Out of scope:

* Settings dialog + theme picker — Phase 3d
* MKV preview — Phase 3e
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, Optional

from PySide6.QtWidgets import QApplication

from gui_qt.main_window import MainWindow
from gui_qt.theme import load_theme
from gui_qt.themes import THEMES_BY_ID
from gui_qt.drive_handler import DriveHandler
from gui_qt.tray_icon import JellyRipTray
from gui_qt.utility_handlers import UtilityHandler
from gui_qt.workflow_launchers import WorkflowLauncher
from shared.runtime import APP_DISPLAY_NAME

if TYPE_CHECKING:
    pass


def _theme_tag_colors(theme_name: str) -> dict[str, str]:
    """Pull the active theme's prompt/answer colors so the log pane
    can apply them inline via ``QTextCharFormat``.

    Falls back to the ``dark_github`` defaults if the configured
    theme name doesn't resolve.
    """
    theme = THEMES_BY_ID.get(theme_name) or THEMES_BY_ID["dark_github"]
    return {
        "prompt": theme.tokens["promptFg"],
        "answer": theme.tokens["answerFg"],
    }


def _build_engine_and_controller(
    cfg: dict,
    window: MainWindow,
) -> tuple[Any, Any]:
    """Construct ``RipperEngine`` + ``RipperController`` paired with
    the window as the UI adapter.

    Imports are deferred until call time so that:

    1. Tests importing ``gui_qt.app`` for inspection don't pay the
       import cost.
    2. The fact that ``gui_qt`` doesn't depend on the engine /
       controller at import time stays true (decouples the
       packages).
    """
    from controller.controller import RipperController
    from engine.ripper_engine import RipperEngine

    engine = RipperEngine(cfg)
    controller = RipperController(engine, window)
    return engine, controller


def run_qt_app(cfg: dict, *, splash: Any = None) -> int:
    """Launch the PySide6 GUI.

    Returns the QApplication exit code.  The shape (returning an int
    that the caller can ``sys.exit`` on) matches PySide6 conventions
    and lets ``main.py`` treat tkinter and Qt paths uniformly.

    ``splash`` is the optional startup splash (``JellyRipSplash`` or
    the no-op fallback from ``main.py``).  When provided, it is
    handed off via ``splash.finish_for(window)`` right after
    ``window.show()`` so the splash fades cleanly into the real UI.
    Duck-typed — anything with a ``finish_for(window)`` method
    works.
    """
    app = QApplication.instance() or QApplication(sys.argv)

    theme_name = cfg.get("opt_pyside6_theme", "dark_github")
    try:
        load_theme(app, theme_name)
    except FileNotFoundError as e:
        # Don't crash — fall back to no stylesheet so the window
        # at least appears.  The user-facing theme picker (sub-phase
        # 3d) will offer a recovery path.
        print(f"WARNING: {e}", file=sys.stderr)

    window = MainWindow(
        cfg=cfg,
        theme_tag_colors=_theme_tag_colors(theme_name),
    )
    # Profile-aware title so users running two instances side-by-side
    # can tell them apart at a glance.  Default profile → bare
    # APP_DISPLAY_NAME (unchanged for single-instance users).
    from shared.runtime import get_profile_window_title
    window.setWindowTitle(get_profile_window_title())

    # Wire the controller, workflow launcher, utility chip handler,
    # and drive scan handler.
    engine, controller = _build_engine_and_controller(cfg, window)
    launcher = WorkflowLauncher(window, controller, engine)
    launcher.connect_signals()
    util_handler = UtilityHandler(window)
    util_handler.connect_signals()

    # Drive handler does an initial scan via refresh_async() so the
    # combo populates without the user having to click ↻ first.
    from config import save_config
    drive_handler = DriveHandler(window, cfg=cfg, save_cfg=save_config)
    drive_handler.connect_signals()
    drive_handler.refresh_async()

    # System-tray companion — gives users a tooltip-progress view
    # plus a click-to-restore handle while a long rip runs.  No-ops
    # silently on systems without a tray (rare on Windows; common
    # on stripped VMs).  Gated by ``opt_tray_icon_enabled`` so the
    # Appearance tab can disable it; defaults True.
    tray: "JellyRipTray | None" = None
    if cfg.get("opt_tray_icon_enabled", True):
        tray = JellyRipTray(window, app_name=APP_DISPLAY_NAME)
    window.set_tray(tray)

    # AI BRANCH only — wire the chat sidebar's backend.  The sidebar
    # itself is constructed lazily via the toolbar chip; the
    # ChatController hooks signals to provider calls.  Lazy
    # construction keeps cold-start time fast and the QTextBrowser
    # cost is only paid when the user opens the chat.  Welcome
    # message renders the first time the user opens the sidebar
    # (idempotent — re-opens skip it).
    sidebar = window.ensure_chat_sidebar()
    from gui_qt.chat_controller import ChatController
    chat_controller = ChatController(
        sidebar=sidebar, cfg=cfg, parent=window,
    )
    chat_controller.show_welcome()
    # Hidden by default; user opens it via the "☰  Chat" toolbar chip.
    sidebar.hide()

    # Hold references on the window so they don't get garbage-collected
    # while the window is alive.
    window._engine = engine
    window._controller = controller
    window._launcher = launcher
    window._util_handler = util_handler
    window._drive_handler = drive_handler
    window._tray_icon = tray
    window._chat_controller = chat_controller

    window.show()

    # Hand off the splash to the live window — fades it out as the
    # main UI takes over.  ``splash`` is optional and duck-typed;
    # ``_NullStartupWindow``'s no-op ``finish_for`` is a safe path.
    if splash is not None:
        try:
            splash.finish_for(window)
        except Exception:
            # A misbehaving splash must not block the app from running.
            pass

    # Seed the log so a freshly-launched window isn't empty.  Pre-
    # 2026-05-04 this also dumped a "Workflow buttons wired ..."
    # status that mentioned internal phase codes (3c-iii) — engineering
    # speak in user-visible copy.  One welcome line is enough; the
    # next line in the log will be a real action.
    window.append_log(f"{APP_DISPLAY_NAME} ready.  Insert a disc to begin.")

    return app.exec()
