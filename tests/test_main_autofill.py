"""Tests for ``main.main()`` — the PySide6 entrypoint flow.

**Phase 3h, 2026-05-04** — rewritten for the post-tkinter ``main.py``.
The pre-Phase-3h version monkeypatched ``main.JellyRipperGUI``
(retired tkinter class) and pinned tkinter-shaped behaviors
(``mainloop()`` / ``destroy()`` / per-platform branches).  After
Phase 3h, ``main.main()`` is a thin wrapper around three things:

1. ``_prepare_startup_environment()`` — TCL paths + config dir
2. ``_create_startup_window()`` — splash (or no-op fallback)
3. ``run_qt_app(cfg, splash=startup_window)`` — the QApplication
   exec loop, raised as ``SystemExit(exit_code)``

These tests pin that contract: ``main()`` calls each in the right
order, propagates the splash, and tears it down via ``close()`` in
the ``finally`` block.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # type: ignore[import-not-found]


class _FakeStartupWindow:
    def __init__(self):
        self.statuses: list[str] = []
        self.closed = False
        self.finish_for_calls: list[object] = []

    def set_status(self, message):
        self.statuses.append(message)

    def close(self):
        self.closed = True

    def finish_for(self, window):
        self.finish_for_calls.append(window)


def test_main_has_no_startup_autofill_helper():
    """The pre-Phase-3h ``_autofill_tool_paths`` helper was deleted —
    its job (auto-resolving makemkvcon / ffprobe paths at startup)
    moved into ``config.load_startup_config``.  Pinned so a future
    refactor doesn't accidentally re-introduce a duplicate."""
    assert not hasattr(main, "_autofill_tool_paths")


def test_main_runs_full_startup_sequence(monkeypatch):
    """Happy path: prepare → splash → load_startup_config →
    run_qt_app.  Splash receives both status updates and is closed
    in the finally block.

    ``run_qt_app`` raises ``SystemExit(0)`` to short-circuit the
    QApplication exec loop in tests.  ``main()`` re-raises so a
    real exec-and-exit cycle is preserved.
    """
    cfg = {
        "makemkvcon_path": "",
        "ffprobe_path": "",
        "ffmpeg_path": "",
        "handbrake_path": "",
    }
    captured = {
        "prepared": False,
        "set_user_model_id": False,
        "run_qt_called_with_cfg": None,
        "run_qt_called_with_splash": None,
    }

    class _Startup:
        config = cfg
        issues = ()
        open_settings = False

    startup_window = _FakeStartupWindow()

    def _fake_run_qt_app(cfg_arg, *, splash=None):
        captured["run_qt_called_with_cfg"] = cfg_arg
        captured["run_qt_called_with_splash"] = splash
        # Mimic real ``run_qt_app``: returns the exit code, which
        # ``main()`` wraps in ``SystemExit``.  We return 0 here.
        return 0

    monkeypatch.setattr(main, "load_startup_config", lambda: _Startup())
    monkeypatch.setattr(
        main, "_prepare_startup_environment",
        lambda: captured.__setitem__("prepared", True),
    )
    monkeypatch.setattr(
        main, "_set_windows_app_user_model_id",
        lambda: captured.__setitem__("set_user_model_id", True),
    )
    monkeypatch.setattr(main, "_create_startup_window", lambda: startup_window)
    # Inject the fake run_qt_app via the lazy import inside main().
    # We can't monkeypatch it before main() runs the import, so we
    # add it to gui_qt.app's namespace.
    import gui_qt.app
    monkeypatch.setattr(gui_qt.app, "run_qt_app", _fake_run_qt_app)

    with pytest.raises(SystemExit) as exc_info:
        main.main()

    assert exc_info.value.code == 0
    assert captured["prepared"] is True
    # ``_set_windows_app_user_model_id`` runs unconditionally; on
    # non-Windows it's a no-op inside but main still calls it.
    assert captured["set_user_model_id"] is True
    assert captured["run_qt_called_with_cfg"] is cfg
    # The splash is forwarded so run_qt_app can call finish_for
    # after the main window shows.
    assert captured["run_qt_called_with_splash"] is startup_window
    # main() emits both status updates while the splash is alive.
    assert "Loading settings..." in startup_window.statuses
    assert any(
        "Loading interface" in msg for msg in startup_window.statuses
    )
    # Finally block closes the splash.
    assert startup_window.closed is True


def test_main_closes_splash_when_load_startup_config_raises(monkeypatch):
    """If ``load_startup_config`` blows up, the ``finally`` in
    ``main()`` must still close the splash so it doesn't linger as
    a ghost window."""
    startup_window = _FakeStartupWindow()
    monkeypatch.setattr(main, "_prepare_startup_environment", lambda: None)
    monkeypatch.setattr(main, "_set_windows_app_user_model_id", lambda: None)
    monkeypatch.setattr(main, "_create_startup_window", lambda: startup_window)
    monkeypatch.setattr(
        main, "load_startup_config",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        main.main()

    # Splash was started but never reached "Loading PySide6..."
    assert "Loading settings..." in startup_window.statuses
    assert not any("PySide6" in msg for msg in startup_window.statuses)
    # Finally closed it.
    assert startup_window.closed is True


def test_main_propagates_run_qt_app_exit_code(monkeypatch):
    """``run_qt_app`` returns an int exit code; ``main()`` wraps it
    in ``SystemExit`` so the OS sees the same code.  Pinned because
    a non-zero exit is how the app signals errors to launchers /
    parent shells."""
    cfg = {}

    class _Startup:
        config = cfg
        issues = ()
        open_settings = False

    startup_window = _FakeStartupWindow()
    monkeypatch.setattr(main, "load_startup_config", lambda: _Startup())
    monkeypatch.setattr(main, "_prepare_startup_environment", lambda: None)
    monkeypatch.setattr(main, "_set_windows_app_user_model_id", lambda: None)
    monkeypatch.setattr(main, "_create_startup_window", lambda: startup_window)

    import gui_qt.app
    monkeypatch.setattr(gui_qt.app, "run_qt_app", lambda *a, **k: 42)

    with pytest.raises(SystemExit) as exc_info:
        main.main()

    assert exc_info.value.code == 42


def test_main_passes_cfg_dict_through_unmodified(monkeypatch):
    """The cfg dict ``main()`` reads from ``load_startup_config`` is
    handed to ``run_qt_app`` unchanged.  Pinned because a future
    refactor that copies / wraps the dict would break the shared-
    reference semantics the rest of the app relies on (e.g., the
    Appearance tab mutates the same object)."""
    sentinel_cfg = {"sentinel": object()}

    class _Startup:
        config = sentinel_cfg
        issues = ()
        open_settings = False

    captured: dict[str, object] = {}
    startup_window = _FakeStartupWindow()
    monkeypatch.setattr(main, "load_startup_config", lambda: _Startup())
    monkeypatch.setattr(main, "_prepare_startup_environment", lambda: None)
    monkeypatch.setattr(main, "_set_windows_app_user_model_id", lambda: None)
    monkeypatch.setattr(main, "_create_startup_window", lambda: startup_window)

    import gui_qt.app
    monkeypatch.setattr(
        gui_qt.app,
        "run_qt_app",
        lambda cfg, *, splash=None: captured.update({"cfg": cfg}) or 0,
    )

    with pytest.raises(SystemExit):
        main.main()

    # Same object — not a copy.
    assert captured["cfg"] is sentinel_cfg
