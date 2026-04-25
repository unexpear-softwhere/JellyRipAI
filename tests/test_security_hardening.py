import os
import sys
import threading
import types
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import controller.legacy_compat as legacy_compat
import gui.main_window as main_window
import gui.update_ui as update_ui
import main
from config import ResolvedTool
from gui.secure_tk import SecureTk
from utils import updater
from utils.helpers import MakeMKVDrive


def test_main_gui_uses_secure_tk_root():
    assert issubclass(main_window.JellyRipperGUI, SecureTk)


def test_startup_window_uses_secure_tk(monkeypatch):
    class _FakeSecureTk:
        def __init__(self):
            self.destroyed = False

        def title(self, _value):
            pass

        def configure(self, **_kwargs):
            pass

        def resizable(self, *_args):
            pass

        def protocol(self, *_args):
            pass

        def update_idletasks(self):
            pass

        def winfo_screenwidth(self):
            return 1920

        def winfo_screenheight(self):
            return 1080

        def geometry(self, _value):
            pass

        def update(self):
            pass

        def destroy(self):
            self.destroyed = True

    class _FakeWidget:
        def __init__(self, *_args, **_kwargs):
            pass

        def pack(self, *_args, **_kwargs):
            pass

    class _FakeStringVar:
        def __init__(self, value=""):
            self.value = value

        def set(self, value):
            self.value = value

    fake_tk = types.SimpleNamespace(
        Frame=_FakeWidget,
        Label=_FakeWidget,
        StringVar=_FakeStringVar,
    )

    monkeypatch.setitem(sys.modules, "tkinter", fake_tk)
    monkeypatch.setattr(main, "SecureTk", _FakeSecureTk)

    window = main._StartupWindow()

    assert isinstance(window._root, _FakeSecureTk)


def test_prepare_startup_environment_ignores_adjacent_env(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("JELLYRIP_TEST_ENV=1\n", encoding="utf-8")
    monkeypatch.setattr(main, "__file__", str(tmp_path / "main.py"))
    monkeypatch.setattr(main, "_bootstrap_tk_paths", lambda: None)
    monkeypatch.setattr(main, "get_config_dir", lambda: None)
    monkeypatch.delenv("JELLYRIP_TEST_ENV", raising=False)

    main._prepare_startup_environment()

    assert "JELLYRIP_TEST_ENV" not in os.environ
    assert not hasattr(main, "_load_env_file")


def test_updater_signature_check_uses_trusted_powershell(monkeypatch):
    seen = {}

    class _Result:
        returncode = 0
        stdout = (
            '{"Status":"Valid","StatusMessage":"","Thumbprint":"ABCDEF",'
            '"Subject":"CN=Signer"}'
        )
        stderr = ""

    def _fake_run(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return _Result()

    monkeypatch.setattr(updater, "_ps_exe", r"C:\Trusted\powershell.exe")
    monkeypatch.setattr(updater.subprocess, "run", _fake_run)

    updater.get_authenticode_signature(r"C:\Temp\JellyRipAI.exe")

    assert seen["command"][0] == r"C:\Trusted\powershell.exe"
    assert seen["kwargs"]["shell"] is False


def test_launch_downloaded_update_cleanup_uses_trusted_powershell(monkeypatch, tmp_path):
    calls = []
    startfile_calls = []
    update_dir = tmp_path / "JellyRipAIUpdate_123"
    update_dir.mkdir()
    downloaded_path = update_dir / "JellyRipAIInstaller.exe"
    downloaded_path.write_text("stub", encoding="utf-8")

    class _Controller:
        def log(self, _message):
            pass

    class _Engine:
        def abort(self):
            pass

    class _GUI:
        controller = _Controller()
        engine = _Engine()

        def after(self, _delay, _callback):
            pass

        def destroy(self):
            pass

        def show_error(self, *_args):
            raise AssertionError("show_error should not be called")

    def _fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return types.SimpleNamespace()

    monkeypatch.setattr(update_ui.sys, "platform", "win32")
    monkeypatch.setattr(
        update_ui,
        "get_powershell_executable",
        lambda: r"C:\Trusted\powershell.exe",
    )
    monkeypatch.setattr(update_ui.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(
        update_ui.os,
        "startfile",
        lambda path: startfile_calls.append(path),
    )

    update_ui.launch_downloaded_update(_GUI(), str(downloaded_path))

    assert startfile_calls == []
    assert calls[0][0][:3] == [str(downloaded_path), "/SP-", "/SUPPRESSMSGBOXES"]
    assert calls[0][1]["shell"] is False
    assert calls[-1][0][0] == r"C:\Trusted\powershell.exe"
    assert calls[-1][1]["shell"] is False


def test_check_for_updates_blocks_cleanly_without_signer_thumbprint(
    monkeypatch, tmp_path
):
    removed_dirs = []
    real_rmtree = update_ui.shutil.rmtree
    update_dir = tmp_path / "JellyRipAIUpdate_123"
    update_dir.mkdir()

    class _ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            if self._target is not None:
                self._target()

    class _Controller:
        def __init__(self):
            self.logs = []

        def log(self, message):
            self.logs.append(message)

    class _UpdateButton(dict):
        def config(self, **kwargs):
            self.update(kwargs)

    class _GUI:
        def __init__(self):
            self.controller = _Controller()
            self.cfg = {
                "opt_update_require_signature": True,
                "opt_update_signer_thumbprint": "",
            }
            self.engine = types.SimpleNamespace(abort_event=threading.Event())
            self.update_btn = _UpdateButton()
            self.statuses = []
            self.errors = []
            self.prompts = []

        def set_status(self, message):
            self.statuses.append(message)

        def ask_yesno(self, prompt):
            self.prompts.append(prompt)
            return True

        def after(self, _delay, callback):
            callback()

        def show_error(self, title, msg):
            self.errors.append((title, msg))

        def show_info(self, *_args):
            raise AssertionError("show_info should not be called")

    def _fake_download_asset(_url, destination, _on_progress, abort_event=None):
        _ = abort_event
        Path(destination).write_text("stub", encoding="utf-8")

    def _fake_rmtree(path, ignore_errors=False):
        removed_dirs.append(path)
        real_rmtree(path, ignore_errors=ignore_errors)

    monkeypatch.setattr(update_ui.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(
        update_ui,
        "fetch_latest_release",
        lambda *_args, **_kwargs: {
            "version": "9.9.9",
            "asset_url": "https://example.invalid/JellyRipAIInstaller.exe",
            "asset_name": "JellyRipAIInstaller.exe",
            "html_url": "",
        },
    )
    monkeypatch.setattr(update_ui, "download_asset", _fake_download_asset)
    monkeypatch.setattr(update_ui, "sha256_file", lambda _path: "abc123")
    monkeypatch.setattr(
        update_ui.tempfile, "mkdtemp", lambda prefix="": str(update_dir)
    )
    monkeypatch.setattr(update_ui.shutil, "rmtree", _fake_rmtree)
    monkeypatch.setattr(
        update_ui,
        "verify_downloaded_update",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError(
                "verify_downloaded_update should not run without a configured thumbprint"
            )
        ),
    )

    gui = _GUI()

    update_ui.check_for_updates(gui)

    assert gui.errors
    title, message = gui.errors[0]
    assert title == "Update Blocked"
    assert "no signer thumbprint is configured" in message
    assert "opt_update_signer_thumbprint" in message
    assert str(update_dir) in removed_dirs
    assert gui.update_btn["state"] == "normal"
    assert any(
        "opt_update_signer_thumbprint is empty" in entry
        for entry in gui.controller.logs
    )


def test_open_path_in_explorer_uses_trusted_explorer(monkeypatch, tmp_path):
    calls = []
    folder = tmp_path / "output"
    folder.mkdir()

    class _Dummy:
        def show_error(self, *_args):
            raise AssertionError("show_error should not be called")

    def _fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return types.SimpleNamespace()

    monkeypatch.setattr(main_window.sys, "platform", "win32")
    monkeypatch.setattr(
        main_window,
        "get_explorer_executable",
        lambda: r"C:\Trusted\explorer.exe",
    )
    monkeypatch.setattr(main_window.subprocess, "Popen", _fake_popen)

    main_window.JellyRipperGUI._open_path_in_explorer(_Dummy(), str(folder))

    assert calls == [
        ([r"C:\Trusted\explorer.exe", os.path.normpath(str(folder))], {"shell": False})
    ]


def test_reveal_path_in_explorer_uses_trusted_explorer(monkeypatch, tmp_path):
    calls = []
    target = tmp_path / "movie.mkv"
    target.write_text("stub", encoding="utf-8")

    class _Dummy:
        def show_error(self, *_args):
            raise AssertionError("show_error should not be called")

        def _open_path_in_explorer(self, _path):
            raise AssertionError("file reveal should not fall back to open")

    def _fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return types.SimpleNamespace()

    monkeypatch.setattr(main_window.sys, "platform", "win32")
    monkeypatch.setattr(
        main_window,
        "get_explorer_executable",
        lambda: r"C:\Trusted\explorer.exe",
    )
    monkeypatch.setattr(main_window.subprocess, "Popen", _fake_popen)

    main_window.JellyRipperGUI._reveal_path_in_explorer(_Dummy(), str(target))

    assert calls == [
        ([r"C:\Trusted\explorer.exe", f"/select,{os.path.normpath(str(target))}"], {"shell": False})
    ]


def test_notify_complete_uses_trusted_powershell(monkeypatch):
    calls = []
    fake_winsound = types.SimpleNamespace(
        MB_ICONASTERISK=1,
        MessageBeep=lambda _value: None,
    )

    def _fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return types.SimpleNamespace()

    monkeypatch.setattr(main_window.sys, "platform", "win32")
    monkeypatch.setattr(
        main_window,
        "get_powershell_executable",
        lambda: r"C:\Trusted\powershell.exe",
    )
    monkeypatch.setattr(main_window.subprocess, "Popen", _fake_popen)
    monkeypatch.setitem(sys.modules, "winsound", fake_winsound)

    main_window.JellyRipperGUI._notify_complete(object(), "Done", "Rip complete.")

    assert calls[0][0][0] == r"C:\Trusted\powershell.exe"
    assert calls[0][1]["shell"] is False


def test_refresh_drives_uses_resolved_makemkv_path(monkeypatch):
    seen = {}

    class _ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            if self._target is not None:
                self._target()

    def _fake_resolve(path, *, allow_path_lookup=False):
        seen["resolved_from"] = path
        seen["allow_path_lookup"] = allow_path_lookup
        return ResolvedTool(
            path=r"C:\Trusted\makemkvcon64.exe",
            source="configured executable",
        )

    def _fake_get_available_drives(path):
        seen["drive_path"] = path
        return [
            MakeMKVDrive(
                index=0,
                visible=1,
                enabled=1,
                flags=0,
                drive_name="Blu-ray Drive",
                disc_name="TEST_DISC",
                device_path="D:",
            )
        ]

    gui = object.__new__(main_window.JellyRipperGUI)
    gui.cfg = {
        "makemkvcon_path": r"C:\Configured\makemkvcon.exe",
    }
    gui.after = lambda _delay, callback: callback()
    gui._update_drive_menu = lambda drives: seen.setdefault("drives", drives)

    monkeypatch.setattr(main_window.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(main_window, "resolve_makemkvcon", _fake_resolve)
    monkeypatch.setattr(main_window, "get_available_drives", _fake_get_available_drives)

    gui._refresh_drives()

    assert seen["resolved_from"] == os.path.normpath(r"C:\Configured\makemkvcon.exe")
    assert seen["allow_path_lookup"] is False
    assert seen["drive_path"] == r"C:\Trusted\makemkvcon64.exe"
    assert seen["drives"][0].drive_name == "Blu-ray Drive"


def test_refresh_drives_honors_path_toggle_when_resolving_makemkv(monkeypatch):
    seen = {}

    class _ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            if self._target is not None:
                self._target()

    def _fake_resolve(path, *, allow_path_lookup=False):
        seen["resolved_from"] = path
        seen["allow_path_lookup"] = allow_path_lookup
        return ResolvedTool(
            path=r"C:\Trusted\makemkvcon64.exe",
            source="PATH via advanced toggle",
        )

    def _fake_get_available_drives(path):
        seen["drive_path"] = path
        return [
            MakeMKVDrive(
                index=0,
                visible=1,
                enabled=1,
                flags=0,
                drive_name="Blu-ray Drive",
                disc_name="TEST_DISC",
                device_path="D:",
            )
        ]

    gui = object.__new__(main_window.JellyRipperGUI)
    gui.cfg = {
        "makemkvcon_path": r"C:\Configured\makemkvcon.exe",
        "opt_allow_path_tool_resolution": True,
    }
    gui.after = lambda _delay, callback: callback()
    gui._update_drive_menu = lambda drives: seen.setdefault("drives", drives)

    monkeypatch.setattr(main_window.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(main_window, "resolve_makemkvcon", _fake_resolve)
    monkeypatch.setattr(main_window, "get_available_drives", _fake_get_available_drives)
    monkeypatch.setattr(main_window.sys, "platform", "win32")

    gui._refresh_drives()

    assert seen["resolved_from"] == os.path.normpath(r"C:\Configured\makemkvcon.exe")
    assert seen["allow_path_lookup"] is True
    assert seen["drive_path"] == r"C:\Trusted\makemkvcon64.exe"
    assert seen["drives"][0].drive_name == "Blu-ray Drive"


def test_refresh_drives_does_not_fall_back_to_raw_configured_path_on_resolution_failure(
    monkeypatch,
):
    seen = {}

    class _ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            if self._target is not None:
                self._target()

    def _fake_resolve(path, *, allow_path_lookup=False):
        seen["resolved_from"] = path
        seen["allow_path_lookup"] = allow_path_lookup
        return ResolvedTool(
            path="",
            source="",
            error="resolution failed",
        )

    def _fake_get_available_drives(path):
        seen["drive_path"] = path
        return [
            MakeMKVDrive(
                index=0,
                visible=1,
                enabled=1,
                flags=0,
                drive_name="Blu-ray Drive",
                disc_name="TEST_DISC",
                device_path="D:",
            )
        ]

    gui = object.__new__(main_window.JellyRipperGUI)
    gui.cfg = {
        "makemkvcon_path": r"C:\Configured\makemkvcon.exe",
    }
    gui.after = lambda _delay, callback: callback()
    gui._update_drive_menu = lambda drives: seen.setdefault("drives", drives)

    monkeypatch.setattr(main_window.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(main_window, "resolve_makemkvcon", _fake_resolve)
    monkeypatch.setattr(main_window, "get_available_drives", _fake_get_available_drives)

    gui._refresh_drives()

    assert seen["resolved_from"] == os.path.normpath(r"C:\Configured\makemkvcon.exe")
    assert seen["allow_path_lookup"] is False
    assert seen["drive_path"] == ""
    assert seen["drives"][0].drive_name == "Blu-ray Drive"


def test_open_folder_scanner_honors_path_toggle_when_building_request(monkeypatch):
    seen = {}

    class _ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            if self._target is not None:
                self._target()

    class _DummyWindow:
        def title(self, *_args, **_kwargs):
            pass

        def geometry(self, *_args, **_kwargs):
            pass

        def configure(self, *_args, **_kwargs):
            pass

        def grab_set(self, *_args, **_kwargs):
            pass

        def destroy(self):
            pass

    class _DummyWidget:
        def __init__(self, *_args, **_kwargs):
            pass

        def pack(self, *_args, **_kwargs):
            pass

    class _DummyVar:
        def __init__(self, value=None):
            self.value = value

        def set(self, value):
            self.value = value

        def get(self):
            return self.value

    def _fake_build_folder_scan_request(**kwargs):
        seen["build_kwargs"] = kwargs
        return types.SimpleNamespace(
            folder=kwargs["folder"],
            mode=kwargs["scan_options"]["mode"],
            recursive=bool(kwargs["scan_options"]["recursive"]),
            include_dirs=kwargs["include_dirs"],
            log_path=r"C:\Logs\folder_scan_log.txt",
            ffprobe_exe=r"C:\Trusted\ffprobe.exe",
        )

    gui = object.__new__(main_window.JellyRipperGUI)
    gui.cfg = {
        "log_file": r"C:\Logs\jellyrip.log",
        "ffprobe_path": r"C:\Tools\ffmpeg",
        "opt_allow_path_tool_resolution": True,
    }
    gui._theme = {
        "surface": "#111111",
        "title": "#ffffff",
        "muted": "#aaaaaa",
    }
    gui.ask_directory = lambda *_args, **_kwargs: r"C:\Media"
    gui._ask_folder_scan_options = lambda: {
        "mode": "duration_desc",
        "recursive": True,
    }
    gui.after = lambda _delay, callback: callback()
    gui._show_folder_scan_results = (
        lambda folder, results, scan_options: seen.setdefault(
            "shown",
            (folder, results, scan_options),
        )
    )
    gui.show_info = lambda *_args, **_kwargs: None
    gui.show_error = lambda *_args, **_kwargs: None

    monkeypatch.setattr(main_window.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(main_window, "build_folder_scan_request", _fake_build_folder_scan_request)
    monkeypatch.setattr("tools.folder_scanner.scan_folder", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(main_window.tk, "Toplevel", lambda *_args, **_kwargs: _DummyWindow())
    monkeypatch.setattr(main_window.tk, "Label", lambda *_args, **_kwargs: _DummyWidget())
    monkeypatch.setattr(main_window.tk, "DoubleVar", _DummyVar)
    monkeypatch.setattr(main_window.tk, "StringVar", _DummyVar)
    monkeypatch.setattr(main_window.ttk, "Progressbar", lambda *_args, **_kwargs: _DummyWidget())
    monkeypatch.setattr(main_window.sys, "platform", "win32")

    gui._open_folder_scanner()

    assert seen["build_kwargs"]["folder"] == r"C:\Media"
    assert seen["build_kwargs"]["ffprobe_path"] == r"C:\Tools\ffmpeg"
    assert seen["build_kwargs"]["allow_path_lookup"] is True
    assert seen["shown"][0] == r"C:\Media"
    assert seen["shown"][1] == []


def test_preview_title_uses_resolved_vlc_path(monkeypatch, tmp_path):
    calls = []

    class _GUI:
        def __init__(self):
            self.statuses = []
            self.logs = []
            self.rip_thread = None

        def append_log(self, message):
            self.logs.append(message)

        def set_status(self, message):
            self.statuses.append(message)

    class _Engine:
        def __init__(self, temp_root):
            self.cfg = {
                "temp_folder": str(temp_root),
                "opt_allow_path_tool_resolution": False,
                "opt_debug_state": False,
            }
            self.abort_event = threading.Event()
            self.preview_file = ""

        def rip_preview_title(self, preview_dir, _title_id, _seconds, _log):
            self.preview_file = str(Path(preview_dir) / "preview.mkv")
            Path(self.preview_file).write_bytes(b"x" * 1024)
            return True

        def analyze_files(self, files, _log):
            return [(files[0], 120.0, 0.0)]

        def reset_abort(self):
            pass

    class _ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            if self._target is not None:
                self._target()

    def _fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return types.SimpleNamespace()

    gui = _GUI()
    engine = _Engine(tmp_path)
    from controller.controller import RipperController

    controller = RipperController(engine, gui)

    monkeypatch.setattr(legacy_compat.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(
        legacy_compat,
        "resolve_vlc",
        lambda *, allow_path_lookup=False: ResolvedTool(
            path=r"C:\Trusted\vlc.exe",
            source="known location",
        ),
    )
    monkeypatch.setattr(legacy_compat._sys, "platform", "win32")
    monkeypatch.setattr(legacy_compat.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(
        controller,
        "_safe_glob",
        lambda _pattern, recursive, context: [engine.preview_file],
    )

    controller.preview_title(0)

    assert calls[0][0] == [r"C:\Trusted\vlc.exe", engine.preview_file]
    assert calls[0][1]["shell"] is False
    assert any("Preview player resolved via known location" in line for line in gui.logs)
