import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # type: ignore[import-not-found]


class _FakeStartupWindow:
    def __init__(self):
        self.statuses = []
        self.closed = False

    def set_status(self, message):
        self.statuses.append(message)

    def close(self):
        self.closed = True


def test_main_has_no_startup_autofill_helper():
    assert not hasattr(main, "_autofill_tool_paths")


def test_main_launches_gui_with_loaded_config(monkeypatch):
    cfg = {
        "makemkvcon_path": "",
        "ffprobe_path": "",
        "ffmpeg_path": "",
        "handbrake_path": "",
    }
    launched = {
        "config": None,
        "startup_context": None,
        "mainloop_called": False,
        "prepared": False,
    }

    class _FakeGUI:
        def __init__(self, config, startup_context=None):
            launched["config"] = config
            launched["startup_context"] = startup_context

        def mainloop(self):
            launched["mainloop_called"] = True

    class _Startup:
        config = cfg
        issues = ()
        open_settings = False

    startup_window = _FakeStartupWindow()
    monkeypatch.setattr(main, "load_startup_config", lambda: _Startup())
    monkeypatch.setattr(main, "JellyRipperGUI", _FakeGUI)
    monkeypatch.setattr(main, "_prepare_startup_environment", lambda: launched.__setitem__("prepared", True))
    monkeypatch.setattr(main, "_create_startup_window", lambda: startup_window)
    monkeypatch.setattr(main.sys, "platform", "linux")

    main.main()

    assert launched["config"] is cfg
    assert launched["prepared"] is True
    assert launched["startup_context"] == {"issues": [], "open_settings": False}
    assert launched["mainloop_called"] is True
    assert startup_window.statuses == [
        "Loading settings...",
        "Loading interface...",
        "Opening app...",
    ]
    assert startup_window.closed is True


def test_main_cleans_up_and_exits_130_on_console_interrupt(monkeypatch, capsys):
    cfg = {
        "makemkvcon_path": "",
        "ffprobe_path": "",
        "ffmpeg_path": "",
        "handbrake_path": "",
    }
    launched = {"aborted": False, "destroyed": False}

    class _FakeEngine:
        def abort(self):
            launched["aborted"] = True

    class _FakeGUI:
        engine = _FakeEngine()

        def __init__(self, config, startup_context=None):
            launched["config"] = config
            launched["startup_context"] = startup_context

        def mainloop(self):
            raise KeyboardInterrupt

        def destroy(self):
            launched["destroyed"] = True

    class _Startup:
        config = cfg
        issues = ()
        open_settings = False

    startup_window = _FakeStartupWindow()
    monkeypatch.setattr(main, "load_startup_config", lambda: _Startup())
    monkeypatch.setattr(main, "JellyRipperGUI", _FakeGUI)
    monkeypatch.setattr(main, "_prepare_startup_environment", lambda: None)
    monkeypatch.setattr(main, "_create_startup_window", lambda: startup_window)
    monkeypatch.setattr(main.sys, "platform", "linux")

    with pytest.raises(SystemExit) as exc_info:
        main.main()

    assert exc_info.value.code == 130
    assert launched["config"] is cfg
    assert launched["startup_context"] == {"issues": [], "open_settings": False}
    assert launched["aborted"] is True
    assert launched["destroyed"] is True
    assert startup_window.closed is True
    assert "console interrupt" in capsys.readouterr().err


def test_main_passes_startup_recovery_context(monkeypatch):
    cfg = {
        "makemkvcon_path": "",
        "ffprobe_path": "",
        "ffmpeg_path": "",
        "handbrake_path": "",
    }
    launched = {"startup_context": None}

    class _Issue:
        def __init__(self, message):
            self.message = message

    class _Startup:
        config = cfg
        issues = (_Issue("Config file was unreadable."),)
        open_settings = True

    class _FakeGUI:
        def __init__(self, config, startup_context=None):
            launched["config"] = config
            launched["startup_context"] = startup_context

        def mainloop(self):
            return None

    startup_window = _FakeStartupWindow()
    monkeypatch.setattr(main, "load_startup_config", lambda: _Startup())
    monkeypatch.setattr(main, "JellyRipperGUI", _FakeGUI)
    monkeypatch.setattr(main, "_prepare_startup_environment", lambda: None)
    monkeypatch.setattr(main, "_create_startup_window", lambda: startup_window)

    main.main()

    assert launched["config"] is cfg
    assert launched["startup_context"] == {
        "issues": ["Config file was unreadable."],
        "open_settings": True,
    }
    assert startup_window.closed is True


def test_main_closes_bootstrap_window_on_gui_load_failure(monkeypatch):
    startup_window = _FakeStartupWindow()

    monkeypatch.setattr(main, "_prepare_startup_environment", lambda: None)
    monkeypatch.setattr(main, "_create_startup_window", lambda: startup_window)
    monkeypatch.setattr(main, "load_startup_config", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        main.main()

    assert startup_window.statuses == ["Loading settings..."]
    assert startup_window.closed is True
