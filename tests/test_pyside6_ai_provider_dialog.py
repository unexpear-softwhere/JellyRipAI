"""Qt widget tests for the AI Provider Setup dialog.

Phase 4 (2026-05-04) ported the dialog from tkinter to PySide6.  These
tests construct the actual ``QDialog`` and exercise it so a future
refactor can't silently break the toolbar wiring or the provider-card
build pass.

Skipped cleanly via ``pytest.importorskip("pytestqt")`` on
environments without the GUI test harness — same pattern as the rest
of the Phase 3 PySide6 tests.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton

from gui_qt.dialogs.ai_provider import (
    AIProviderDialog,
    open_ai_provider_dialog,
)
from gui_qt.main_window import MainWindow
from gui_qt.utility_handlers import UtilityHandler


# ─── Stub provider registry ──────────────────────────────────────────


class _StubProviderInfo(SimpleNamespace):
    """Mimics ``shared.ai.provider_registry.ProviderInfo`` so the
    dialog can build cards without hitting the real registry."""


def _stub_providers() -> list[_StubProviderInfo]:
    return [
        _StubProviderInfo(
            id="claude",
            display_name="Claude",
            category="cloud",
            requires_api_key=True,
            available_models=["claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
            default_model="claude-sonnet-4-6",
            help_url="https://console.anthropic.com/settings/keys",
        ),
        _StubProviderInfo(
            id="local",
            display_name="Local (Ollama)",
            category="local",
            requires_api_key=False,
            available_models=["llama3.1:8b"],
            default_model="llama3.1:8b",
            help_url="https://ollama.com/library",
        ),
    ]


def _stub_summary() -> dict[str, dict]:
    return {
        "claude": {"is_active": False, "has_credentials": False},
        "local": {"is_active": False, "has_credentials": False},
    }


def _stub_credentials(_pid: str) -> dict[str, str]:
    return {}


@pytest.fixture
def patched_registry(monkeypatch):
    """Patch the provider registry + credential store so dialog
    construction doesn't depend on real config.json state."""
    import shared.ai.provider_registry as registry_module
    import shared.ai.credential_store as credential_module

    monkeypatch.setattr(registry_module, "list_providers", _stub_providers)
    monkeypatch.setattr(registry_module, "get_connection_summary", _stub_summary)
    monkeypatch.setattr(
        credential_module, "get_provider_credentials", _stub_credentials
    )
    monkeypatch.setattr(credential_module, "is_encrypted_storage", lambda: True)


# ─── Construction + cards ────────────────────────────────────────────


def test_dialog_constructs_without_raising(qtbot, patched_registry):
    """Smoke test — the dialog builds with stub data and shows two
    provider cards (Claude + Local).  Doesn't call ``exec()`` because
    that would block the test on the modal loop."""
    dlg = AIProviderDialog()
    qtbot.addWidget(dlg)

    assert dlg.windowTitle() == "AI Provider Setup"
    assert dlg.objectName() == "aiProviderDialog"

    # Both stub providers show up.
    assert "claude" in dlg._provider_widgets
    assert "local" in dlg._provider_widgets


def test_dialog_header_uses_app_display_name(qtbot, patched_registry):
    """Subtitle pulls APP_DISPLAY_NAME so AI BRANCH renders
    "JellyRip AI" instead of MAIN's "JellyRip"."""
    from shared.runtime import APP_DISPLAY_NAME

    dlg = AIProviderDialog()
    qtbot.addWidget(dlg)

    # Find the subtitle label by objectName.
    subtitle = dlg.findChild(QLabel, "aiProviderSubtitle")
    assert subtitle is not None
    assert APP_DISPLAY_NAME in subtitle.text()


def test_cloud_card_has_api_key_field_and_show_button(qtbot, patched_registry):
    """Cloud providers (Claude here) get a password-mode QLineEdit
    plus a Show toggle that flips the echo mode."""
    dlg = AIProviderDialog()
    qtbot.addWidget(dlg)

    widgets = dlg._provider_widgets["claude"]
    key_edit = widgets["key_edit"]
    show_btn = widgets["show_btn"]

    assert isinstance(key_edit, QLineEdit)
    assert key_edit.echoMode() == QLineEdit.EchoMode.Password
    assert isinstance(show_btn, QPushButton)
    assert show_btn.isCheckable()

    # Toggle Show — echo mode flips to Normal.
    show_btn.setChecked(True)
    assert key_edit.echoMode() == QLineEdit.EchoMode.Normal
    show_btn.setChecked(False)
    assert key_edit.echoMode() == QLineEdit.EchoMode.Password


def test_local_card_has_url_field_not_api_key(qtbot, patched_registry):
    """Local providers (Ollama) get a base-URL QLineEdit instead of
    an API-key field."""
    dlg = AIProviderDialog()
    qtbot.addWidget(dlg)

    widgets = dlg._provider_widgets["local"]
    assert "url_edit" in widgets
    assert "key_edit" not in widgets
    assert widgets["url_edit"].text() == "http://localhost:11434"


def test_status_state_machine_updates_label_and_qss_property(
    qtbot, patched_registry,
):
    """``_set_provider_status`` updates both the visible text and the
    ``state`` QSS property — pinned because the QSS files style the
    status dot via the property selector."""
    dlg = AIProviderDialog()
    qtbot.addWidget(dlg)

    dlg._set_provider_status("claude", "validating", detail="…")
    label = dlg._provider_widgets["claude"]["status_label"]
    detail_label = dlg._provider_widgets["claude"]["detail_label"]

    assert "Validating" in label.text()
    assert label.property("state") == "validating"
    assert detail_label.text() == "…"

    dlg._set_provider_status("claude", "connected", detail="200ms • claude-sonnet")
    assert label.text() == "● Connected"
    assert label.property("state") == "connected"
    assert "200ms" in detail_label.text()


# ─── Worker-thread result marshaling ─────────────────────────────────
#
# Pinned because the original code marshaled with
# ``QTimer.singleShot(0, ...)`` from a plain ``threading.Thread`` — a
# zero-timeout functor singleShot binds to the *calling* thread, which
# has no Qt event loop, so the callback NEVER fired: Test / Save /
# "Set as Active" stuck at "Validating…" forever and credentials were
# never persisted from this dialog.


def test_provider_check_result_reaches_gui_callback(qtbot, patched_registry):
    """The worker's test_connection result must arrive at ``on_result``
    on the GUI thread.  Fails on the old QTimer marshal (callback never
    runs); passes with the Invoker."""
    from PySide6.QtCore import QThread

    dlg = AIProviderDialog()
    qtbot.addWidget(dlg)

    sentinel = SimpleNamespace(success=True, latency_ms=12.0,
                               model_confirmed="stub", error="")
    fake_provider = SimpleNamespace(
        test_connection=lambda timeout=15.0: sentinel,
    )

    received: list = []
    threads: list = []

    def on_result(result):
        received.append(result)
        threads.append(QThread.currentThread())

    dlg._run_provider_check("claude", fake_provider, on_result)

    qtbot.waitUntil(lambda: bool(received), timeout=3000)
    assert received == [sentinel]
    # Delivered on the GUI thread, where widget-touching callbacks
    # are safe.
    assert threads[0] is dlg.thread()


def test_provider_check_exception_becomes_failed_result(
    qtbot, patched_registry,
):
    """test_connection's contract is "must not raise" — but if a
    provider bug raises anyway, the worker must deliver a failed
    ConnectionResult instead of dying silently and stranding the card
    at "Validating…"."""
    dlg = AIProviderDialog()
    qtbot.addWidget(dlg)

    def boom(timeout=15.0):
        raise RuntimeError("provider exploded")

    fake_provider = SimpleNamespace(test_connection=boom)

    received: list = []
    dlg._run_provider_check("claude", fake_provider, received.append)

    qtbot.waitUntil(lambda: bool(received), timeout=3000)
    result = received[0]
    assert result.success is False
    assert "provider exploded" in result.error


# ─── Toolbar wiring ──────────────────────────────────────────────────


def test_main_window_toolbar_includes_ai_provider_chip(qtbot):
    """``utilAIProvider`` chip is present on AI BRANCH's toolbar.
    This test pins the AI-BRANCH-only divergence from MAIN's
    main_window — if a future merge from MAIN drops it, this test
    fails fast."""
    mw = MainWindow()
    qtbot.addWidget(mw)

    assert "utilAIProvider" in mw.utility_buttons


def test_utility_handler_has_ai_provider_handler(qtbot):
    """The handler method must be reachable on the UtilityHandler so
    ``_dispatch`` can resolve it via ``getattr``."""
    mw = MainWindow()
    qtbot.addWidget(mw)
    handler = UtilityHandler(mw)

    assert hasattr(handler, "handle_utilAIProvider")
    assert callable(handler.handle_utilAIProvider)


def test_utility_handler_opens_dialog_via_lazy_import(
    qtbot, patched_registry, monkeypatch,
):
    """Clicking the chip must open the AI Provider dialog via the
    lazy-imported ``open_ai_provider_dialog``.  Patches the entry
    point so the test doesn't actually pop a modal."""
    mw = MainWindow()
    qtbot.addWidget(mw)
    handler = UtilityHandler(mw)
    handler.connect_signals()

    captured: dict = {}

    def fake_open(parent, on_change=None):
        captured["parent"] = parent
        captured["on_change"] = on_change
        return 1  # Accepted

    monkeypatch.setattr(
        "gui_qt.dialogs.ai_provider.open_ai_provider_dialog", fake_open
    )

    # Trigger the chip — should invoke fake_open with the window
    # as parent and an on_change callable.
    mw.utility_buttons["utilAIProvider"].trigger()

    assert captured.get("parent") is mw
    assert callable(captured.get("on_change"))


def test_open_entry_point_returns_dialog_exec_code(qtbot, patched_registry):
    """``open_ai_provider_dialog`` returns the dialog's exec result.
    We patch ``exec`` on AIProviderDialog so the test doesn't block."""
    captured = {}

    real_init = AIProviderDialog.__init__

    def patched_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        captured["dialog"] = self
        # Stub out exec so the call returns immediately.
        self.exec = lambda: 1

    AIProviderDialog.__init__ = patched_init
    try:
        result = open_ai_provider_dialog(parent=None)
    finally:
        AIProviderDialog.__init__ = real_init

    assert result == 1
    assert "dialog" in captured
