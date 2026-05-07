"""Security-hardening tests.

**Phase 3h, 2026-05-04** — partially rewritten for the PySide6 UI.
The pre-Phase-3h version pinned ``subprocess.Popen`` security
properties of tkinter UI methods that have since been retired.
The hardening **primitives** (``shared/windows_exec.py``,
``utils/updater.py``) are still alive and still tested in their
own files (``test_windows_exec.py``, ``test_updater.py``).  This
file pins the security-sensitive consumers that still exist in the
live codebase.

Migration map for the retired tests:

| Pre-Phase-3h test                                        | New home                                                                          |
|----------------------------------------------------------|-----------------------------------------------------------------------------------|
| ``test_launch_downloaded_update_cleanup_*``              | ``tools/update_check.py`` is a deferred-port stub — see ``handle_utilUpdates``.   |
| ``test_check_for_updates_blocks_cleanly_*``              | Same — Qt-native rewrite is polish-tier follow-up.                                |
| ``test_open_path_in_explorer_uses_trusted_explorer``     | No PySide6 equivalent yet; QFileDialog handles browse via the OS file manager.    |
| ``test_reveal_path_in_explorer_uses_trusted_explorer``   | Same — no Qt-side reveal-in-explorer surface today.                               |
| ``test_notify_complete_uses_trusted_powershell``         | Replaced by ``JellyRipTray.notify_complete`` (Qt-native, no subprocess).          |
| ``test_refresh_drives_uses_resolved_makemkv_path``       | Retargeted to ``gui_qt.drive_handler.DriveHandler`` below.                        |
| ``test_update_drive_menu_shows_full_drive_identity``     | Covered by ``test_pyside6_drive_handler`` + ``test_pyside6_formatters``.          |

Tests that were always primitive-level (env scrubbing, updater
signature check, controller-side preview-title) survive verbatim —
they don't touch the retired UI methods.
"""

import os
import sys
import threading
import types
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import controller.legacy_compat as legacy_compat
import main
from config import ResolvedTool
from controller.controller import RipperController
from utils import updater


# ---------------------------------------------------------------------------
# Primitive-level tests — survived Phase 3h verbatim
# ---------------------------------------------------------------------------


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
    """The Authenticode signature check shells out to PowerShell.
    The path passed to ``subprocess.run`` must be the trusted
    binary returned by ``get_powershell_executable`` — never the
    raw ``"powershell.exe"`` (which would resolve via PATH and
    could pick up an attacker-controlled binary)."""
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

    # ``updater._ps_exe`` is captured at module-import time via
    # ``get_powershell_executable()`` (a one-shot call).  Patch the
    # cached value directly so the next ``verify_downloaded_update``
    # picks up our trusted-path stand-in.
    monkeypatch.setattr(updater, "_ps_exe", r"C:\Trusted\powershell.exe")
    monkeypatch.setattr(updater.subprocess, "run", _fake_run)

    ok, _msg = updater.verify_downloaded_update(
        Path("dummy.exe"),
        require_signature=True,
        required_thumbprint="ABCDEF",
    )
    assert ok is True
    assert seen["command"][0] == r"C:\Trusted\powershell.exe"
    assert seen["kwargs"]["shell"] is False


# ---------------------------------------------------------------------------
# Deferred-port surfaces — skip with pointer to the future home
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason=(
        "launch_downloaded_update lived in retired gui/update_ui.py; "
        "the PySide6 replacement (tools/update_check.py) is a deferred-port "
        "stub today.  Restore this test once the Qt-native update flow "
        "lands — the trusted-powershell property must hold."
    )
)
def test_launch_downloaded_update_cleanup_uses_trusted_powershell():
    pass


@pytest.mark.skip(
    reason=(
        "check_for_updates lived in retired gui/update_ui.py; the PySide6 "
        "replacement is currently a stub at tools/update_check.py.  Restore "
        "this test once the Qt-native update flow lands — the empty-thumbprint "
        "block path must keep working so users can't disable signature "
        "verification by clearing the cfg key."
    )
)
def test_check_for_updates_blocks_cleanly_without_signer_thumbprint():
    pass


@pytest.mark.skip(
    reason=(
        "_open_path_in_explorer lived on the retired tkinter "
        "JellyRipperGUI.  The PySide6 UI uses QFileDialog (Browse Folder) "
        "and has no equivalent 'open this folder in Explorer' surface yet.  "
        "If we add one, restore the trusted-explorer property test."
    )
)
def test_open_path_in_explorer_uses_trusted_explorer():
    pass


@pytest.mark.skip(
    reason=(
        "_reveal_path_in_explorer lived on the retired tkinter "
        "JellyRipperGUI.  Same situation as _open_path_in_explorer — "
        "no Qt-side reveal-in-explorer surface today."
    )
)
def test_reveal_path_in_explorer_uses_trusted_explorer():
    pass


@pytest.mark.skip(
    reason=(
        "_notify_complete on the retired tkinter UI used PowerShell to play "
        "a notification sound.  The PySide6 replacement is "
        "JellyRipTray.notify_complete (gui_qt/tray_icon.py), which uses "
        "QSystemTrayIcon's Qt-native showMessage — no subprocess, no "
        "powershell.  The 'trusted exe path' property doesn't apply to "
        "the Qt path, but tray.notify_complete has its own coverage in "
        "test_pyside6_tray_icon.py."
    )
)
def test_notify_complete_uses_trusted_powershell():
    pass


# ---------------------------------------------------------------------------
# Retargeted to PySide6 — drive refresh still goes through resolve_makemkvcon
# ---------------------------------------------------------------------------


def test_drive_handler_uses_resolved_makemkv_path(monkeypatch, qtbot):
    """The drive scanner must resolve ``makemkvcon`` via
    ``config.resolve_makemkvcon`` so the trusted-binary check
    (digital signature, allow-list path) runs before we shell
    out to it.  Pinned because the only source of disc identity
    is whatever ``makemkvcon`` reports — running an attacker-
    controlled binary at that point is a privileged-data leak.

    Pre-Phase-3h this checked ``main_window.JellyRipperGUI._refresh_drives``
    (retired).  The PySide6 replacement lives in
    ``gui_qt.drive_handler.DriveHandler._default_scanner``.
    """
    from gui_qt.drive_handler import DriveHandler
    from gui_qt.main_window import MainWindow

    seen: dict[str, object] = {}

    def _fake_resolve(path, *, allow_path_lookup=False):
        seen["resolved_from"] = path
        seen["allow_path_lookup"] = allow_path_lookup
        return ResolvedTool(
            path=r"C:\Trusted\makemkvcon.exe",
            source="configured executable",
        )

    def _fake_get_drives(path):
        seen["drive_path"] = path
        return [(0, "Blu-ray Drive")]

    # Patch where _default_scanner does the lazy imports.
    import config
    import utils.helpers
    monkeypatch.setattr(config, "resolve_makemkvcon", _fake_resolve)
    monkeypatch.setattr(utils.helpers, "get_available_drives", _fake_get_drives)

    mw = MainWindow()
    qtbot.addWidget(mw)
    handler = DriveHandler(
        mw,
        cfg={
            "makemkvcon_path": r"C:\Configured\makemkvcon.exe",
            "opt_allow_path_tool_resolution": True,
        },
    )

    drives = handler._default_scanner()

    # The configured path was passed to resolve_makemkvcon (not used
    # directly in subprocess) — security primitive engaged.
    assert seen["resolved_from"] == os.path.normpath(
        r"C:\Configured\makemkvcon.exe"
    )
    assert seen["allow_path_lookup"] is True
    # The path get_available_drives shells out to is the RESOLVED
    # one — not the configured one.  This is the hardening: even
    # if the user has a malicious makemkvcon_path, the resolver's
    # known-locations whitelist gates which binary we actually run.
    assert seen["drive_path"] == r"C:\Trusted\makemkvcon.exe"
    assert drives == [(0, "Blu-ray Drive")]


# ---------------------------------------------------------------------------
# Controller-side preview — primitive level, survived verbatim
# ---------------------------------------------------------------------------


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
