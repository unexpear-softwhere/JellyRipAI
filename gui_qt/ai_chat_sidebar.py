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

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
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
    model_selected = Signal(str)  # emits chosen model name, "" for Off
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

        # ── Model picker row ───────────────────────────────────────
        # Replaces the old Off/Cloud/Local "AI mode" combo.  Shows the
        # ACTIVE provider's usable models (chosen in the ✦ AI Providers
        # dialog) plus an "Off" entry at the top.  The controller
        # populates it via ``set_model_options`` and reacts to the
        # user's pick via ``model_selected``:
        #   ""           — Off (disable AI; controller writes opt_ai_mode="off").
        #   <model name>  — use that model on the active provider (the
        #                   controller writes the provider's model and
        #                   flips opt_ai_mode to its category).
        # Switching *which* provider (a cloud one, or local) stays in
        # the AI Providers dialog's "Set as Active" buttons — this
        # dropdown only changes the model within the active provider.
        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)

        model_label = QLabel("Model:")
        model_label.setObjectName("chatSidebarModelLabel")
        mode_row.addWidget(model_label)

        self._model_combo = QComboBox()
        self._model_combo.setObjectName("chatSidebarModelCombo")
        self._model_combo.setToolTip(
            "Pick the model for the active AI provider, or Off to "
            "disable the assistant.  Change providers in ✦ AI Providers."
        )
        self._model_combo.currentIndexChanged.connect(self._on_model_changed)
        mode_row.addWidget(self._model_combo, stretch=1)

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

        # ── Transcript: a scroll of chat bubbles ──────────────────
        # Standard-chat look: each message is a rounded bubble (a real
        # widget, so QSS border-radius applies — a QTextBrowser's rich
        # text can't round corners), with the sender's name attached
        # just above it.  User bubbles sit on the right, the assistant's
        # on the left.
        self._transcript = QScrollArea()
        self._transcript.setObjectName("chatTranscript")
        self._transcript.setWidgetResizable(True)
        self._transcript.setFrameShape(QFrame.Shape.NoFrame)
        self._transcript.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        vp = self._transcript.viewport()
        if vp is not None:
            vp.setAutoFillBackground(False)
        self._transcript_host = QWidget()
        self._transcript_host.setObjectName("chatTranscriptHost")
        self._bubbles = QVBoxLayout(self._transcript_host)
        self._bubbles.setContentsMargins(2, 2, 2, 2)
        self._bubbles.setSpacing(12)
        self._bubbles.addStretch(1)  # keep bubbles pinned to the top
        self._transcript.setWidget(self._transcript_host)
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

        # Message log (label, text, role) — the source of truth for
        # Copy Chat and for wiping on New Chat.  Plus the live streaming
        # bubble: while set, streaming chunks accumulate into it instead
        # of starting a new bubble.
        self._messages: "list[tuple[str, str, str]]" = []
        self._streaming_body: "QLabel | None" = None
        self._streaming_text: str = ""

    # ── Model picker ───────────────────────────────────────────────

    def set_model_options(
        self, options: "list[tuple]", current_value: str,
    ) -> None:
        """Populate the model dropdown WITHOUT firing ``model_selected``.

        ``options`` is a list of ``(value, label)`` or
        ``(value, label, enabled)`` tuples — ``value`` is ``""`` for the
        Off entry, else a model name.  Entries with ``enabled=False`` are
        rendered red and made unselectable (e.g. Ollama cloud models you
        aren't signed in for).  ``current_value`` is pre-selected.
        Called by the controller at startup and whenever the active
        provider/model changes, so it must not loop the change back at
        the controller.
        """
        self._model_combo.blockSignals(True)
        try:
            self._model_combo.clear()
            target_idx = 0
            model = self._model_combo.model()
            for idx, opt in enumerate(options):
                value, label = opt[0], opt[1]
                enabled = opt[2] if len(opt) > 2 else True
                self._model_combo.addItem(label, userData=value)
                if not enabled and hasattr(model, "item"):
                    # Disable so it can't be picked; the theme QSS rule
                    # (QComboBox QAbstractItemView::item:disabled) colours it
                    # with the muted token - no baked-in hex here.
                    item = model.item(idx)
                    if item is not None:
                        item.setEnabled(False)
                if value == current_value:
                    target_idx = idx
            self._model_combo.setCurrentIndex(target_idx)
        finally:
            self._model_combo.blockSignals(False)

    def current_model_choice(self) -> str:
        """Return the selected dropdown value (``""`` = Off, else a
        model name)."""
        data = self._model_combo.currentData()
        return str(data) if data is not None else ""

    def _on_model_changed(self, _idx: int) -> None:
        """Fire ``model_selected`` so the controller can apply the model
        + mode and persist."""
        self.model_selected.emit(self.current_model_choice())

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
        if is_streaming and self._streaming_body is not None:
            # Accumulate into the live bubble (QLabel needs the full
            # text each time, so we keep a running copy).
            self._streaming_text += text
            self._streaming_body.setText(self._streaming_text)
            if self._messages:
                lbl, _old, role = self._messages[-1]
                self._messages[-1] = (lbl, self._streaming_text, role)
            self._scroll_to_end()
            return

        row = self._append_block("Assistant", text, role="assistant")
        if is_streaming:
            self._streaming_body = getattr(row, "_body", None)
            self._streaming_text = text
        else:
            self._streaming_body = None
            self._streaming_text = ""

    def finalize_streaming(self) -> None:
        """Clear the streaming marker so the next assistant message
        starts a fresh bubble."""
        self._streaming_body = None
        self._streaming_text = ""

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
        for i in reversed(range(self._bubbles.count())):
            widget = self._bubbles.itemAt(i).widget()
            if widget is not None:  # leave the trailing stretch spacer
                self._bubbles.takeAt(i)
                widget.deleteLater()
        self._messages.clear()
        self._streaming_body = None
        self._streaming_text = ""
        self.set_status(_DEFAULT_STATUS, state="ready")

    def transcript_text(self) -> str:
        """Return the transcript as plain text (for Copy Chat)."""
        return "\n\n".join(
            f"{label}: {text}" for label, text, _role in self._messages
        )

    def transcript_markdown(self) -> str:
        """Return the transcript as markdown."""
        return "\n\n".join(
            f"**{label}**\n\n{text}" for label, text, _role in self._messages
        )

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

    def _append_block(self, label: str, text: str, *, role: str) -> QWidget:
        """Add one message bubble (sender name attached above it) and
        return its row widget — the row carries the body ``QLabel`` as
        ``._body`` so streaming can keep updating it."""
        row = self._make_bubble(label, text, role)
        # Insert before the trailing stretch so bubbles stack downward.
        self._bubbles.insertWidget(self._bubbles.count() - 1, row)
        self._messages.append((label, text, role))
        self._scroll_to_end()
        return row

    def _make_bubble(self, label: str, text: str, role: str) -> QWidget:
        """Build one chat row: the sender name attached just above a
        rounded message bubble, aligned to the sender's side (user on
        the right, assistant on the left).

        The bubble carries objectName ``chatBubbleBody`` + a
        ``bubbleRole`` property so the active theme's QSS colors it (sent
        = info accent, received = card surface) — no baked-in colors, so
        it tracks whatever theme is applied."""
        is_user = role == "user"

        name = QLabel(label)
        name.setObjectName("chatBubbleSender")

        body = QLabel()
        body.setObjectName("chatBubbleBody")
        body.setProperty("bubbleRole", "user" if is_user else "assistant")
        body.setTextFormat(Qt.TextFormat.MarkdownText)
        body.setText(text)
        body.setWordWrap(True)
        body.setMaximumWidth(340)
        body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        body.setOpenExternalLinks(True)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)  # name sits attached just above the bubble
        side = (
            Qt.AlignmentFlag.AlignRight if is_user
            else Qt.AlignmentFlag.AlignLeft
        )
        column.addWidget(name, 0, side)
        column.addWidget(body, 0, side)

        col_host = QWidget()
        col_host.setLayout(column)

        row = QWidget()
        row.setObjectName("chatBubbleRow")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(0)
        if is_user:
            row_layout.addStretch(1)
            row_layout.addWidget(col_host)
        else:
            row_layout.addWidget(col_host)
            row_layout.addStretch(1)

        row._body = body  # type: ignore[attr-defined]
        return row

    def _scroll_to_end(self) -> None:
        # Defer to after the layout settles so the scrollbar's maximum
        # reflects the just-added bubble.
        def _do() -> None:
            bar = self._transcript.verticalScrollBar()
            if bar is not None:
                bar.setValue(bar.maximum())

        QTimer.singleShot(0, _do)

    # ── Close hook ─────────────────────────────────────────────────

    def closeEvent(self, event: "QEvent") -> None:  # noqa: N802 — Qt convention
        """Emit ``closed`` when the dock's title-bar ✕ hides it, so the
        controller can persist ``opt_ai_sidebar_open=False``."""
        self.closed.emit()
        super().closeEvent(event)
