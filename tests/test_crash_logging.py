"""Crash-logging pins.

The windowed exe has no console (``sys.stderr`` is None), so before
``_install_crash_logging`` every post-startup unhandled exception —
including Qt slot exceptions, which PySide6 routes through
``sys.excepthook`` — vanished silently, and native faults showed
nothing at all.  These tests pin that the hook writes a readable
traceback to ``crash.log`` in the profile config dir and that
``faulthandler`` is armed for native faults.
"""

from __future__ import annotations

import faulthandler
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as main_module


def test_crash_hook_writes_traceback_to_config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        main_module, "get_config_dir", lambda create=True: str(tmp_path),
    )
    # pytest-qt hooks sys.excepthook itself and FAILS any test whose
    # exception flows through it — park a no-op sentinel as the
    # "previous" hook so the chain ends with us, then invoke the
    # installed hook directly.  monkeypatch restores the real hook.
    sentinel = lambda *_a: None  # noqa: E731
    monkeypatch.setattr(sys, "excepthook", sentinel)
    try:
        main_module._install_crash_logging()
        installed = sys.excepthook
        assert installed is not sentinel, "hook must be installed"
        assert faulthandler.is_enabled(), \
            "faulthandler must be armed for native faults"

        try:
            raise RuntimeError("boom for crash log")
        except RuntimeError:
            installed(*sys.exc_info())

        text = (tmp_path / "crash.log").read_text(encoding="utf-8")
        assert "RuntimeError" in text
        assert "boom for crash log" in text
        assert "Unhandled exception at" in text
    finally:
        faulthandler.disable()


def test_crash_logging_survives_unwritable_config_dir(monkeypatch):
    """A broken config dir must degrade to a no-op, never break
    startup."""
    def boom(create=True):
        raise OSError("read-only profile dir")

    monkeypatch.setattr(main_module, "get_config_dir", boom)
    previous_hook = sys.excepthook
    try:
        main_module._install_crash_logging()  # must not raise
        assert sys.excepthook is previous_hook, \
            "no hook installed when there is nowhere to write"
    finally:
        sys.excepthook = previous_hook
