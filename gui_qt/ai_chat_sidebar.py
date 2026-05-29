"""AI chat sidebar — Qt (PySide6) port.

Phase 4b (2026-05-05) — replaces the tkinter chat sidebar embedded
inside ``gui/main_window.py`` (~320 references in a 10,700-line file)
with a clean Qt widget.

This module ships the **UI shell**:

* ``ChatSidebar`` — a ``QDockWidget`` locked to the main window's
  right edge (closable + horizontally resizable, but not floatable),
  with markdown-rendering transcript (``QTextBrowser``), an input
  field (``QPlainTextEdit``), and an action row (Suggest / New /
  Copy / Send).
* Public signals (Qt-native) for backend wiring:
    - ``message_submitted(str)`` — user pressed Enter or clicked Send.
    - ``suggest_requested()`` — user clicked "Suggest Next Step".
    - ``new_chat_requested()`` — user clicked "New Chat".
    - ``copy_chat_requested()`` — user clicked "Copy Chat".
    - ``closed()`` — user closed the dock; controller can persist
      the state via ``opt_ai_sidebar_open``.
* Public slots for the controller to drive:
    - ``append_user_message(text)``
    - ``append_assistant_message(text, *, is_streaming=False)``
    - ``set_status(text)``
    - ``set_busy(busy)``
    - ``clear_transcript()``

The actual chat send/receive backend (provider resolution,
streaming, replay logging) is extracted from
``gui/main_window.py`` in a follow-up — this widget is the contract
the controller targets.

**Why ``QTextBrowser.setMarkdown``?**  tkinter's ``Text`` widget
renders markdown as literal characters (``# heading`` shows as
``# heading``).  Qt's ``QTextBrowser`` parses markdown to rich text
natively — code blocks get monospace, ``**bold**`` actually bolds,
links become clickable.  Headline visual upgrade per
``docs/pyside6-migration-plan.md`` rationale for the AI BRANCH port.

Theming: the sidebar inherits the active QSS via objectName
selectors (``chatSidebarDock``, ``chatTranscript``, ``chatInput``,
``chatSendButton``, etc.).  Per branch identity guardrail #4, AI
BRANCH may extend the QSS for chat-specific styling but stays
within MAIN's palette.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QKeyEvent, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from PySide6.QtCore import QEvent


_DEFAULT_TITLE = "Assistant"
_DEFAULT_STATUS = "Ready"


class _ChatInput(QPlainTextEdit):
    """Input field that submits on Enter and inserts a newline on
    Shift+Enter.  Mirrors the tkinter ``_handle_ai_chat_return``
    binding."""

    submit_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("chatInput")
        self.setPlaceholderText("Ask the assistant…")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        # ~2 lines tall by default; resizes if the user types more.
        font = self.font()
        metric_height = self.fontMetrics().lineSpacing()
        self.setFixedHeight(metric_height * 2 + 16)

    def keyPressEvent(self, event: "QKeyEvent") -> None:  # noqa: N802 — Qt convention
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                # Shift+Enter inserts a newline.
                super().keyPressEvent(event)
                return
            # Plain Enter submits.
            self.submit_requested.emit()
            return
        super().keyPressEvent(event)


class ChatSidebar(QDockWidget):
    """Docked panel for the AI assistant chat.

    Lives as a ``QDockWidget`` pinned to the main window's right edge.
    Docking (rather than a separate window) means the central content
    reflows to the left when the chat opens, and the user can drag the
    splitter to resize its width.  It is deliberately **locked** to the
    right area and **not** floatable — the user asked for a panel that
    stays put, not a free-floating window.

    The "assistant is always available, even while the identity step
    is open" feature is *not* achieved by detaching this from the main
    window (a docked panel is part of the main window, so any modal
    dialog would freeze it).  Instead, the workflow dialogs are run
    **non-modally** via ``gui_qt.dialogs._modeless.exec_modeless`` and
    the workflow buttons are soft-locked for the dialog's lifetime.
    That keeps this dock fully interactive mid-dialog.

    Construction is cheap and Qt-only — the widget knows nothing
    about providers, credentials, or the rip controller.  All those
    plug in via the public signals/slots.

    Typical wiring::

        sidebar = ChatSidebar(parent=main_window)
        main_window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, sidebar)
        sidebar.message_submitted.connect(controller.on_chat_prompt)
        controller.assistant_chunk.connect(sidebar.append_assistant_message)
        controller.busy_changed.connect(sidebar.set_busy)
    """

    message_submitted = Signal(str)
    suggest_requested = Signal()
    new_chat_requested = Signal()
    copy_chat_requested = Signal()
    mode_changed = Signal(str)  # emits "off" / "cloud" / "local"
    web_search_toggled = Signal(bool)  # 🌐 Web toggle on/off
    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("chatSidebarDock")
        self.setWindowTitle(_DEFAULT_TITLE)
        # Lock the panel to the right edge: it may be closed (the title-
        # bar ✕ hides it and fires ``closed``) but not dragged to
        # another area or floated off into its own window.  Horizontal
        # resize still works via the splitter handle between the dock
        # and the central widget — that's independent of these flags.
        self.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
        self.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)

        body = QWidget()
        body.setObjectName("chatSidebarBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(10, 10, 10, 10)
        body_layout.setSpacing(8)

        # ── Header row: title + status ─────────────────────────────
        header_row = QHBoxLayout()
        header_row.setSpacing(8)

        title_label = QLabel(_DEFAULT_TITLE)
        title_label.setObjectName("chatSidebarTitle")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title_label.setFont(title_font)
        header_row.addWidget(title_label)

        header_row.addStretch(1)

        self._status_label = QLabel(_DEFAULT_STATUS)
        self._status_label.setObjectName("chatSidebarStatus")
        self._status_label.setProperty("state", "ready")
        header_row.addWidget(self._status_label)

        body_layout.addLayout(header_row)

        # ── Mode picker row: Off / Cloud / Local ───────────────────
        # Lets the user flip the AI backend without leaving the
        # sidebar.  Writes to ``opt_ai_mode`` via the controller's
        # signal/slot wiring.  Three values match the cfg key:
        #   "off"   — no AI calls; the on-device fallback still
        #             runs for "what's happening?" prompts.
        #   "cloud" — try the active cloud provider first, fall
        #             back to local if the cloud provider isn't
        #             configured (matches ChatController._resolve_provider).
        #   "local" — local provider only (Ollama).
        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)

        mode_label = QLabel("AI mode:")
        mode_label.setObjectName("chatSidebarModeLabel")
        mode_row.addWidget(mode_label)

        self._mode_combo = QComboBox()
        self._mode_combo.setObjectName("chatSidebarModeCombo")
        # Tuple shape: (cfg-value, user-facing label).  The label is
        # what the user sees; the cfg-value is what we write.
        self._MODE_OPTIONS: tuple[tuple[str, str], ...] = (
            ("off",   "Off"),
            ("cloud", "Cloud"),
            ("local", "Local"),
        )
        for value, label in self._MODE_OPTIONS:
            self._mode_combo.addItem(label, userData=value)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_row.addWidget(self._mode_combo)

        # 🌐 Web toggle — when on, the assistant searches the web
        # (DuckDuckGo) and TMDB for the question before answering.
        self._web_toggle = QCheckBox("🌐 Web")
        self._web_toggle.setObjectName("chatWebToggle")
        self._web_toggle.setToolTip(
            "Let the assistant look things up online (DuckDuckGo + TMDB) "
            "for this question instead of guessing.  Slower while it "
            "searches."
        )
        self._web_toggle.toggled.connect(self._on_web_search_toggled)
        mode_row.addWidget(self._web_toggle)

        mode_row.addStretch(1)

        body_layout.addLayout(mode_row)

        # ── Transcript: QTextBrowser with markdown rendering ──────
        self._transcript = QTextBrowser()
        self._transcript.setObjectName("chatTranscript")
        self._transcript.setOpenExternalLinks(True)
        self._transcript.setReadOnly(True)
        body_layout.addWidget(self._transcript, stretch=1)

        # ── Input field ────────────────────────────────────────────
        self._input = _ChatInput()
        self._input.submit_requested.connect(self._on_submit)
        body_layout.addWidget(self._input)

        # ── Action row ─────────────────────────────────────────────
        action_row = QHBoxLayout()
        action_row.setSpacing(6)

        self._suggest_btn = QPushButton("Suggest Next Step")
        self._suggest_btn.setObjectName("chatSuggestButton")
        self._suggest_btn.clicked.connect(self.suggest_requested)
        action_row.addWidget(self._suggest_btn)

        self._new_btn = QPushButton("New Chat")
        self._new_btn.setObjectName("chatNewButton")
        self._new_btn.clicked.connect(self.new_chat_requested)
        action_row.addWidget(self._new_btn)

        self._copy_btn = QPushButton("Copy Chat")
        self._copy_btn.setObjectName("chatCopyButton")
        self._copy_btn.clicked.connect(self.copy_chat_requested)
        action_row.addWidget(self._copy_btn)

        action_row.addStretch(1)

        self._send_btn = QPushButton("Send")
        self._send_btn.setObjectName("chatSendButton")
        self._send_btn.setDefault(True)
        self._send_btn.clicked.connect(self._on_submit)
        action_row.addWidget(self._send_btn)

        body_layout.addLayout(action_row)

        self.setWidget(body)

        # Track the live in-progress assistant message for streaming.
        # When set, ``append_assistant_message(text, is_streaming=True)``
        # appends to this position rather than adding a new bubble.
        self._streaming_position: int | None = None

    # ── Mode picker ────────────────────────────────────────────────

    def set_mode(self, mode: str) -> None:
        """Seed the mode combo without firing ``mode_changed``.

        Used by the controller at startup to render the cfg's current
        ``opt_ai_mode`` value into the picker without re-saving it.
        Falls back to "cloud" if the cfg has an unknown value.
        """
        normalized = str(mode or "").strip().lower()
        target_idx = 1  # default to "cloud"
        for idx, (value, _label) in enumerate(self._MODE_OPTIONS):
            if value == normalized:
                target_idx = idx
                break
        # Block signals during the seed so we don't fire mode_changed
        # back at the controller for a value the controller already
        # knows about.
        self._mode_combo.blockSignals(True)
        try:
            self._mode_combo.setCurrentIndex(target_idx)
        finally:
            self._mode_combo.blockSignals(False)

    def current_mode(self) -> str:
        """Return the currently-selected mode value
        (``"off"``/``"cloud"``/``"local"``)."""
        data = self._mode_combo.currentData()
        if data:
            return str(data)
        # Fallback for the case where userData wasn't set somehow.
        idx = self._mode_combo.currentIndex()
        if 0 <= idx < len(self._MODE_OPTIONS):
            return self._MODE_OPTIONS[idx][0]
        return "cloud"

    def _on_mode_changed(self, _idx: int) -> None:
        """Fire ``mode_changed`` so the controller can write
        ``opt_ai_mode`` and persist."""
        self.mode_changed.emit(self.current_mode())

    def set_web_search(self, enabled: bool) -> None:
        """Seed the 🌐 Web toggle from cfg without firing the signal."""
        self._web_toggle.blockSignals(True)
        try:
            self._web_toggle.setChecked(bool(enabled))
        finally:
            self._web_toggle.blockSignals(False)

    def web_search_enabled(self) -> bool:
        """Return whether the 🌐 Web toggle is currently on."""
        return self._web_toggle.isChecked()

    def _on_web_search_toggled(self, checked: bool) -> None:
        """Fire ``web_search_toggled`` so the controller can write
        ``opt_ai_web_search`` and persist."""
        self.web_search_toggled.emit(bool(checked))

    # ── Public slots ───────────────────────────────────────────────

    def append_user_message(self, text: str) -> None:
        """Append a user-authored message to the transcript."""
        if not text:
            return
        self._append_block("You", text, role="user")

    def append_assistant_message(
        self, text: str, *, is_streaming: bool = False,
    ) -> None:
        """Append an assistant message to the transcript.

        When ``is_streaming=True``, subsequent calls accumulate into
        the same message block instead of creating new ones.  Pass a
        non-streaming call (the default) to finalize the streaming
        block — or call ``finalize_streaming()`` explicitly.
        """
        if not text:
            return
        if is_streaming and self._streaming_position is not None:
            # Append to the live block.
            cursor = self._transcript.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertMarkdown(text)
            self._scroll_to_end()
            return

        self._append_block("Assistant", text, role="assistant")
        if is_streaming:
            # Mark the current end so further streaming chunks land here.
            self._streaming_position = self._transcript.document().characterCount() - 1
        else:
            self._streaming_position = None

    def finalize_streaming(self) -> None:
        """Clear the streaming marker so the next assistant message
        starts a fresh block."""
        self._streaming_position = None

    def set_status(self, text: str, *, state: str = "ready") -> None:
        """Update the status label text + ``state`` QSS property.

        Standard states: ``"ready"``, ``"busy"``, ``"error"``.
        Other strings render as ``"ready"``-styled by default; QSS
        files can target arbitrary state names via property selectors.
        """
        self._status_label.setText(text or _DEFAULT_STATUS)
        self._status_label.setProperty("state", state)
        # Force QSS to re-evaluate the property selector.
        style = self._status_label.style()
        if style is not None:
            style.unpolish(self._status_label)
            style.polish(self._status_label)

    def set_busy(self, busy: bool) -> None:
        """Disable the Send button + show "Thinking…" status while a
        request is in flight.  Mirrors tkinter's
        ``ai_chat_send_btn.configure(state="disabled")`` pattern."""
        self._send_btn.setEnabled(not busy)
        self._suggest_btn.setEnabled(not busy)
        if busy:
            self.set_status("Thinking…", state="busy")
        else:
            self.set_status(_DEFAULT_STATUS, state="ready")

    def clear_transcript(self) -> None:
        """Empty the transcript — used by "New Chat"."""
        self._transcript.clear()
        self._streaming_position = None
        self.set_status(_DEFAULT_STATUS, state="ready")

    def transcript_text(self) -> str:
        """Return the transcript as plain text (for Copy Chat)."""
        return self._transcript.toPlainText()

    def transcript_markdown(self) -> str:
        """Return the transcript as markdown."""
        return self._transcript.toMarkdown()

    # ── Internal helpers ───────────────────────────────────────────

    def _on_submit(self) -> None:
        prompt = self._input.toPlainText().strip()
        if not prompt:
            self._input.setFocus()
            return
        # Don't clear here — the controller may want to validate
        # first (e.g., onboarding gate) before consuming the prompt.
        # ``clear_input()`` is the explicit caller-driven hook.
        self.message_submitted.emit(prompt)

    def clear_input(self) -> None:
        """Clear the input field.  Call from the controller after
        accepting a prompt (mirrors tkinter's
        ``ai_chat_input.delete("1.0", "end")`` pattern)."""
        self._input.clear()
        self._input.setFocus()

    def focus_input(self) -> None:
        """Move keyboard focus to the input field."""
        self._input.setFocus()

    def _append_block(self, label: str, text: str, *, role: str) -> None:
        """Insert one role-labeled message block into the transcript.

        Format::

            **You**

            <user message rendered as markdown>

            ---

            **Assistant**

            <assistant message rendered as markdown>

            ---
        """
        cursor = self._transcript.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        # Insert a thin divider before non-first blocks.
        if self._transcript.document().characterCount() > 1:
            cursor.insertHtml('<hr style="margin:8px 0;border:0;border-top:1px solid #44556680;">')

        cursor.insertHtml(
            f'<p style="margin:0 0 4px 0;"><b>{label}</b></p>'
        )
        # Body rendered as markdown so ``**bold**``, code fences,
        # lists, and links all render natively.
        cursor.insertMarkdown(text)
        self._scroll_to_end()

    def _scroll_to_end(self) -> None:
        sb = self._transcript.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    # ── Close hook ─────────────────────────────────────────────────

    def closeEvent(self, event: "QEvent") -> None:  # noqa: N802 — Qt convention
        """Emit ``closed`` when the dock's title-bar ✕ hides it, so the
        controller can persist ``opt_ai_sidebar_open=False``."""
        self.closed.emit()
        super().closeEvent(event)
