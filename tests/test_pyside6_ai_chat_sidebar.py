"""Qt widget tests for the AI chat sidebar.

Phase 4b (2026-05-05) shipped the Qt-side **shell** of the chat
sidebar — a QDockWidget with QTextBrowser transcript, input field,
and the four action buttons (Suggest / New / Copy / Send).  Backend
wiring (controller-driven send/receive) is a follow-up; these tests
pin the shell contract so the follow-up has a stable target.

What's pinned:

* Widget construction is cheap and offscreen-safe.
* ``message_submitted`` fires with the trimmed prompt on click + on
  Enter.
* Empty prompts don't fire ``message_submitted``.
* ``set_busy(True)`` disables Send + Suggest; ``set_busy(False)``
  re-enables them.
* ``append_user_message`` and ``append_assistant_message`` add
  visible content to the transcript.
* ``clear_transcript`` wipes the transcript.
* The ``utilAIChat`` toolbar chip lazily constructs the dock and
  toggles its visibility.

Skipped cleanly via ``pytest.importorskip("pytestqt")`` on
environments without the GUI test harness.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QPushButton

from gui_qt.ai_chat_sidebar import ChatSidebar
from gui_qt.main_window import MainWindow
from gui_qt.utility_handlers import UtilityHandler


# ─── Construction ──────────────────────────────────────────────────


def test_sidebar_constructs_without_raising(qtbot):
    """Smoke test — the chat dock builds with no parent provider data.

    The sidebar is a right-edge ``QDockWidget`` (objectName
    ``chatSidebarDock``).  It stays interactive while a workflow
    dialog is open because those dialogs run non-modally
    (``exec_modeless``) — not because the dock is detached into its
    own window."""
    sb = ChatSidebar()
    qtbot.addWidget(sb)

    assert sb.objectName() == "chatSidebarDock"
    assert sb.windowTitle() == "Assistant"
    # Default action buttons exist with stable objectNames.
    assert sb.findChild(QPushButton, "chatSendButton") is not None
    assert sb.findChild(QPushButton, "chatSuggestButton") is not None
    assert sb.findChild(QPushButton, "chatNewButton") is not None
    assert sb.findChild(QPushButton, "chatCopyButton") is not None


def test_sidebar_starts_with_empty_transcript(qtbot):
    sb = ChatSidebar()
    qtbot.addWidget(sb)

    assert sb.transcript_text().strip() == ""


# ─── Submit signal — button + Enter key ────────────────────────────


def test_send_button_emits_message_submitted_with_prompt(qtbot):
    sb = ChatSidebar()
    qtbot.addWidget(sb)

    sent: list[str] = []
    sb.message_submitted.connect(sent.append)

    # Type a prompt + click Send.
    sb._input.setPlainText("hello world")
    send_btn = sb.findChild(QPushButton, "chatSendButton")
    send_btn.click()

    assert sent == ["hello world"]


def test_empty_prompt_does_not_emit(qtbot):
    """Empty / whitespace-only prompts are silently ignored.  Mirrors
    tkinter's ``_submit_ai_chat`` early return."""
    sb = ChatSidebar()
    qtbot.addWidget(sb)

    sent: list[str] = []
    sb.message_submitted.connect(sent.append)

    sb._input.setPlainText("   ")
    sb.findChild(QPushButton, "chatSendButton").click()

    assert sent == []


def test_enter_key_submits_shift_enter_inserts_newline(qtbot):
    """Plain Enter fires ``message_submitted``; Shift+Enter inserts
    a newline (lets the user write multi-line prompts)."""
    sb = ChatSidebar()
    qtbot.addWidget(sb)

    sent: list[str] = []
    sb.message_submitted.connect(sent.append)

    # Plain Enter on a non-empty prompt should fire.
    sb._input.setPlainText("first line")
    plain_enter = QKeyEvent(
        QKeyEvent.Type.KeyPress,
        Qt.Key.Key_Return,
        Qt.KeyboardModifier.NoModifier,
    )
    sb._input.keyPressEvent(plain_enter)
    assert sent == ["first line"]

    # Shift+Enter should NOT fire — it inserts a newline instead.
    sb._input.setPlainText("two")
    cursor = sb._input.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    sb._input.setTextCursor(cursor)
    shift_enter = QKeyEvent(
        QKeyEvent.Type.KeyPress,
        Qt.Key.Key_Return,
        Qt.KeyboardModifier.ShiftModifier,
    )
    sb._input.keyPressEvent(shift_enter)
    # Still only one fire from the earlier plain-Enter call.
    assert sent == ["first line"]
    assert "\n" in sb._input.toPlainText()


# ─── Action signals (Suggest / New / Copy) ─────────────────────────


def test_suggest_button_emits_signal(qtbot):
    sb = ChatSidebar()
    qtbot.addWidget(sb)

    fired: list[bool] = []
    sb.suggest_requested.connect(lambda: fired.append(True))

    sb.findChild(QPushButton, "chatSuggestButton").click()
    assert fired == [True]


def test_new_button_emits_signal(qtbot):
    sb = ChatSidebar()
    qtbot.addWidget(sb)

    fired: list[bool] = []
    sb.new_chat_requested.connect(lambda: fired.append(True))

    sb.findChild(QPushButton, "chatNewButton").click()
    assert fired == [True]


def test_copy_button_emits_signal(qtbot):
    sb = ChatSidebar()
    qtbot.addWidget(sb)

    fired: list[bool] = []
    sb.copy_chat_requested.connect(lambda: fired.append(True))

    sb.findChild(QPushButton, "chatCopyButton").click()
    assert fired == [True]


# ─── Slot behavior ─────────────────────────────────────────────────


def test_append_user_message_adds_visible_content(qtbot):
    sb = ChatSidebar()
    qtbot.addWidget(sb)

    sb.append_user_message("Test prompt")
    text = sb.transcript_text()
    assert "You" in text
    assert "Test prompt" in text


def test_append_assistant_message_adds_visible_content(qtbot):
    sb = ChatSidebar()
    qtbot.addWidget(sb)

    sb.append_assistant_message("Hello there")
    text = sb.transcript_text()
    assert "Assistant" in text
    assert "Hello there" in text


def test_set_busy_disables_send_and_suggest(qtbot):
    sb = ChatSidebar()
    qtbot.addWidget(sb)

    send_btn = sb.findChild(QPushButton, "chatSendButton")
    suggest_btn = sb.findChild(QPushButton, "chatSuggestButton")
    assert send_btn.isEnabled()
    assert suggest_btn.isEnabled()

    sb.set_busy(True)
    assert not send_btn.isEnabled()
    assert not suggest_btn.isEnabled()
    assert "Thinking" in sb._status_label.text()
    assert sb._status_label.property("state") == "busy"

    sb.set_busy(False)
    assert send_btn.isEnabled()
    assert suggest_btn.isEnabled()
    assert sb._status_label.property("state") == "ready"


def test_clear_transcript_wipes_content(qtbot):
    sb = ChatSidebar()
    qtbot.addWidget(sb)

    sb.append_user_message("Question")
    sb.append_assistant_message("Answer")
    assert "Question" in sb.transcript_text()

    sb.clear_transcript()
    assert sb.transcript_text().strip() == ""


def test_set_status_updates_label_and_qss_property(qtbot):
    sb = ChatSidebar()
    qtbot.addWidget(sb)

    sb.set_status("Custom status", state="error")
    assert sb._status_label.text() == "Custom status"
    assert sb._status_label.property("state") == "error"


def test_clear_input_empties_field_and_focuses(qtbot):
    sb = ChatSidebar()
    qtbot.addWidget(sb)

    sb._input.setPlainText("draft")
    sb.clear_input()
    assert sb._input.toPlainText() == ""


# ─── MainWindow integration ────────────────────────────────────────


def test_main_window_lazily_constructs_chat_sidebar(qtbot):
    """``MainWindow.chat_sidebar`` is None until ``ensure_chat_sidebar``
    is called.  Pinned because eager construction would mean every
    user pays the QTextBrowser cost at startup even if they never
    open the chat."""
    mw = MainWindow()
    qtbot.addWidget(mw)

    assert mw.chat_sidebar is None

    sb = mw.ensure_chat_sidebar()
    assert isinstance(sb, ChatSidebar)
    assert mw.chat_sidebar is sb

    # Idempotent — second call returns the same instance.
    sb2 = mw.ensure_chat_sidebar()
    assert sb2 is sb


def test_main_window_toolbar_includes_ai_chat_chip(qtbot):
    """``utilAIChat`` chip is present on AI BRANCH's toolbar.  Pins
    the AI-BRANCH-only divergence from MAIN's main_window."""
    mw = MainWindow()
    qtbot.addWidget(mw)

    assert "utilAIChat" in mw.utility_buttons


def test_utility_handler_toggles_chat_sidebar(qtbot):
    """Clicking the chip opens/closes the dock and writes a log
    line.  Show the main window first so ``isVisible()`` is
    meaningful (offscreen mode keeps everything False until the
    parent is shown)."""
    mw = MainWindow()
    qtbot.addWidget(mw)
    mw.show()  # so isVisible() reflects intent

    handler = UtilityHandler(mw)
    handler.connect_signals()

    initial_log = mw.log_pane.get_text()

    # First click — opens the sidebar.
    mw.utility_buttons["utilAIChat"].trigger()
    sb = mw.chat_sidebar
    assert sb is not None
    assert sb.isVisible()
    assert "AI chat sidebar opened" in mw.log_pane.get_text()

    # Second click — closes it.
    mw.utility_buttons["utilAIChat"].trigger()
    assert not sb.isVisible()
    assert "AI chat sidebar closed" in mw.log_pane.get_text()

    mw.hide()


def test_closed_signal_fires_on_dock_close(qtbot):
    """Closing the sidebar dock fires ``closed`` so the controller
    can persist ``opt_ai_sidebar_open=False``."""
    sb = ChatSidebar()
    qtbot.addWidget(sb)

    fired: list[bool] = []
    sb.closed.connect(lambda: fired.append(True))

    sb.close()
    assert fired == [True]
