"""Tests for non-modal workflow dialogs + the workflow-button soft-lock.

The AI BRANCH headline feature is "the assistant is always available,
even while the identity step is open."  Because the chat is a *docked*
panel inside the main window, a modal dialog would freeze it.  The fix
(2026-05-29):

* ``gui_qt.dialogs._modeless.exec_modeless`` shows a dialog
  **non-modally** and spins a local event loop until it closes — the
  worker thread stays blocked (so the rip still waits for the answer)
  but the GUI keeps pumping events, so the chat dock stays live.
* ``MainWindow.begin_workflow_dialog`` / ``end_workflow_dialog``
  soft-lock the workflow controls (mode buttons, Stop, drive picker)
  for the dialog's lifetime so a non-modal main window can't desync a
  running workflow.  Ref-counted; restores each control's prior state.

These tests pin both halves.  Skipped cleanly without pytest-qt.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QWidget

from gui_qt.dialogs._modeless import exec_modeless
from gui_qt.main_window import MainWindow


# ─── exec_modeless ─────────────────────────────────────────────────


def test_exec_modeless_is_nonmodal_and_returns_accepted(qtbot):
    """The dialog runs non-modally and returns its Accepted code; the
    nested loop must quit (no hang) when the dialog closes."""
    dlg = QDialog()
    qtbot.addWidget(dlg)

    # Accept on the next event-loop tick — fires inside exec_modeless's
    # nested loop, which then quits.
    QTimer.singleShot(0, dlg.accept)
    result = exec_modeless(dlg)

    assert result == QDialog.DialogCode.Accepted
    assert dlg.windowModality() == Qt.WindowModality.NonModal
    assert not dlg.isModal()


def test_exec_modeless_returns_rejected_on_reject(qtbot):
    dlg = QDialog()
    qtbot.addWidget(dlg)

    QTimer.singleShot(0, dlg.reject)
    result = exec_modeless(dlg)

    assert result == QDialog.DialogCode.Rejected


class _FakeHost(QWidget):
    """Top-level widget exposing the workflow-dialog guard API so we
    can assert exec_modeless brackets the loop with begin/end."""

    def __init__(self) -> None:
        super().__init__()
        self.begin_calls = 0
        self.end_calls = 0

    def begin_workflow_dialog(self) -> None:
        self.begin_calls += 1

    def end_workflow_dialog(self) -> None:
        self.end_calls += 1


def test_exec_modeless_brackets_host_guard(qtbot):
    """When the dialog's top-level window exposes the guard API,
    exec_modeless calls begin once before and end once after."""
    host = _FakeHost()
    qtbot.addWidget(host)

    dlg = QDialog(host)  # parentWidget().window() is the host
    QTimer.singleShot(0, dlg.accept)
    exec_modeless(dlg)

    assert host.begin_calls == 1
    assert host.end_calls == 1


def test_exec_modeless_end_runs_even_if_rejected(qtbot):
    """The guard is released on the finally path regardless of how the
    dialog closed — a stuck soft-lock would freeze the workflow."""
    host = _FakeHost()
    qtbot.addWidget(host)

    dlg = QDialog(host)
    QTimer.singleShot(0, dlg.reject)
    exec_modeless(dlg)

    assert host.end_calls == 1


def test_exec_modeless_without_host_does_not_raise(qtbot):
    """A parentless dialog (no guard host) still runs fine."""
    dlg = QDialog()
    qtbot.addWidget(dlg)

    QTimer.singleShot(0, dlg.accept)
    # Should not raise despite there being no guard host.
    assert exec_modeless(dlg) == QDialog.DialogCode.Accepted


# ─── MainWindow workflow-dialog soft-lock ──────────────────────────


def test_guard_disables_then_restores_workflow_controls(qtbot):
    mw = MainWindow()
    qtbot.addWidget(mw)

    buttons = list(mw.workflow_buttons.values())
    assert buttons, "expected at least one workflow button"
    for btn in buttons:
        btn.setEnabled(True)
    mw.stop_button.setEnabled(True)

    mw.begin_workflow_dialog()
    assert all(not b.isEnabled() for b in buttons)
    assert not mw.stop_button.isEnabled()

    mw.end_workflow_dialog()
    assert all(b.isEnabled() for b in buttons)
    assert mw.stop_button.isEnabled()


def test_guard_restores_exact_prior_state(qtbot):
    """Restore must honor each control's *prior* enabled-state, not
    blindly re-enable — Stop is disabled when idle, and we mustn't
    light it up just because a setup dialog opened."""
    mw = MainWindow()
    qtbot.addWidget(mw)

    buttons = list(mw.workflow_buttons.values())
    assert len(buttons) >= 2
    buttons[0].setEnabled(True)
    buttons[1].setEnabled(False)
    mw.stop_button.setEnabled(False)

    mw.begin_workflow_dialog()
    mw.end_workflow_dialog()

    assert buttons[0].isEnabled() is True
    assert buttons[1].isEnabled() is False
    assert mw.stop_button.isEnabled() is False


def test_guard_is_refcounted(qtbot):
    """Nested begins (back-to-back dialogs in one workflow) only
    release the lock when the outermost dialog closes."""
    mw = MainWindow()
    qtbot.addWidget(mw)

    btn = next(iter(mw.workflow_buttons.values()))
    btn.setEnabled(True)

    mw.begin_workflow_dialog()
    mw.begin_workflow_dialog()
    assert not btn.isEnabled()

    mw.end_workflow_dialog()  # depth 2 → 1, still locked
    assert not btn.isEnabled()

    mw.end_workflow_dialog()  # depth 1 → 0, released
    assert btn.isEnabled()
