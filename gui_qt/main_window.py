"""Main window shell — PySide6 port (sub-phase 3c-i).

Provides the top-level ``QMainWindow`` shell that orchestrates the
leaf widgets (``LogPane``, ``StatusBar``) into the JellyRip layout.
Implements the controller's ``UIAdapter`` Protocol so the existing
``RipperController`` can drive this window the same way it drives
``gui/main_window.py``'s ``JellyRipperGUI``.

**Scope of 3c-i** — chrome, leaf wiring, UIAdapter implementation,
status / progress / log methods.

**Out of scope (deferred to 3c-ii / 3c-iii):**

* Workflow launcher click handlers — buttons exist with the right
  objectNames, but their ``clicked`` signal is wired to a stub that
  logs "TODO 3c-ii" rather than calling controller methods.
* Modal dialogs (``show_info``, ``show_error``, ``ask_yesno``,
  ``ask_input``, ``ask_space_override``, ``show_temp_manager``,
  ``show_disc_tree``, ``ask_tv_setup``, ``ask_movie_setup``).  Each
  raises ``NotImplementedError`` with a pointer to the brief.
* Drive scan / refresh logic — that's controller-side work invoked
  by 3c-ii's workflow launchers.
* Settings dialog and theme picker — Phase 3d.
* MKV preview — Phase 3e.

The shell is intentionally launchable in this incomplete state: any
caller that doesn't trigger a workflow button or a dialog method
will see a fully-rendered window with the wizard accessible (via
``setup_wizard.show_*`` calls) and the leaf widgets responsive.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from gui_qt.dialogs import (
    DuplicateResolutionChoice,
    MovieSessionSetup,
    TVSessionSetup,
    ask_duplicate_resolution as _ask_duplicate_resolution,
    ask_input as _ask_input,
    ask_movie_setup as _ask_movie_setup,
    ask_space_override as _ask_space_override,
    ask_tv_setup as _ask_tv_setup,
    ask_yesno as _ask_yesno,
    show_disc_tree as _show_disc_tree,
    show_error as _show_error,
    show_extras_picker as _show_extras_picker,
    show_file_list as _show_file_list,
    show_info as _show_info,
    show_temp_manager as _show_temp_manager,
)
from gui_qt.formatters import status_role_for_message
from gui_qt.log_pane import LogPane
from gui_qt.status_bar import StatusBar
from gui_qt.thread_safety import Invoker, run_on_main, submit_to_main
from gui_qt.tray_icon import JellyRipTray
from shared.runtime import APP_DISPLAY_NAME, APP_VARIANT

if TYPE_CHECKING:
    from gui_qt.ai_chat_sidebar import ChatSidebar
    from shared.event import Event


# ---------------------------------------------------------------------------
# Workflow mode buttons — declarative table.  3c-ii will populate the
# ``handler`` slot with controller-bound callables.
# ---------------------------------------------------------------------------


# Each entry: (label, role, objectName).  Role determines the QSS
# coloring (go / info / alt / warn) — same role names used in
# ``gui_qt/themes.py``.  ObjectName is what the QSS file targets.
#
# Leading glyphs follow the canonical mapping in
# ``docs/symbol-library.md`` Section 1 — monochrome Unicode rather
# than colored emoji so the rhythm stays consistent with the
# utility toolbar and so the same glyphs look right across all six
# themes (some color-emoji renderings clash with light themes).
# Convention: two spaces between glyph and label.
_PRIMARY_BUTTONS: tuple[tuple[str, str, str], ...] = (
    ("⏺  Rip TV Show Disc",      "go",   "modeGoTv"),       # U+23FA Record
    ("⏵  Rip Movie Disc",        "go",   "modeGoMovie"),    # U+23F5 Play
    ("⇣  Dump All Titles",       "info", "modeInfoDump"),   # U+21E3 Dashed down
)

_SECONDARY_BUTTONS: tuple[tuple[str, str, str], ...] = (
    ("▤  Organize Existing MKVs",            "alt",  "modeAltOrganize"),  # U+25A4 List square
    ("⚒  Prep MKVs For FFmpeg / HandBrake",  "warn", "modeWarnPrep"),     # U+2692 Hammer + pick
)

_UTILITY_BUTTONS: tuple[tuple[str, str], ...] = (
    ("⚙  Settings",      "utilSettings"),  # U+2699 Gear
    ("✦  AI Providers",  "utilAIProvider"),  # U+2726 Star — AI BRANCH only
    ("☰  Chat",          "utilAIChat"),      # U+2630 Trigram — AI BRANCH only
    ("⇡  Check Updates", "utilUpdates"),   # U+21E1 Dashed up
    ("⎘  Copy Log",      "utilCopyLog"),   # U+2398 Next page
    ("→  Browse Folder", "utilBrowse"),    # U+2192 Right arrow
)


class MainWindow(QMainWindow):
    """Top-level shell.  Owns the leaf widgets and implements the
    controller's ``UIAdapter`` Protocol.

    Construction:

        win = MainWindow(cfg, theme_tag_colors=theme_colors)
        win.show()

    The cfg dict is forwarded to the leaf widgets that need it
    (``LogPane`` reads ``opt_log_cap_lines`` / ``opt_log_trim_lines``).
    ``theme_tag_colors`` is the active theme's prompt/answer color
    map; if omitted, the log pane falls back to the ``dark_github``
    defaults.
    """

    # Emitted when a workflow button is clicked.  3c-ii wires this
    # to the controller; 3c-i has no listeners.  Carries the
    # objectName of the clicked button so the controller can route.
    workflow_button_clicked = Signal(str)

    # Emitted when a utility chip is clicked.  Same shape.
    utility_button_clicked = Signal(str)

    # Emitted when the drive refresh button is clicked.  3c-ii wires
    # this to the controller's drive-scan path.
    drive_refresh_clicked = Signal()

    def __init__(
        self,
        cfg: Mapping[str, Any] | None = None,
        theme_tag_colors: Mapping[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._cfg = cfg or {}

        # Thread-safety invoker — owned by the GUI thread, used by the
        # set_status / set_progress / append_log / dialog methods to
        # marshal calls from worker threads onto the GUI thread.  See
        # gui_qt/thread_safety.py for the pattern.
        self._invoker = Invoker(self)

        self.setObjectName("mainWindow")
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.resize(960, 720)
        self.setMinimumSize(800, 600)

        # ---- Central widget + root layout -----------------------------
        central = QWidget()
        central.setObjectName("mainCentral")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- Header band ---------------------------------------------
        root.addWidget(self._build_header())

        # ---- Drive row -----------------------------------------------
        root.addWidget(self._build_drive_row())

        # ---- Utility toolbar (Settings / Updates / Copy Log / Browse) -
        # Lives outside ``central`` because ``QMainWindow`` puts the
        # toolbar in its own dock area, above the central widget.  The
        # toolbar sits at the very top of the window — the platform-
        # native location for utility actions and where users look
        # for them first.  Replaces the loose chip row that lived
        # under the drive picker pre-2026-05-04.
        self.addToolBar(
            Qt.ToolBarArea.TopToolBarArea,
            self._build_utility_toolbar(),
        )

        # ---- Primary workflow button row -----------------------------
        root.addWidget(
            self._build_button_row(_PRIMARY_BUTTONS, "primaryButtonRow")
        )

        # ---- Secondary workflow button row ---------------------------
        root.addWidget(
            self._build_button_row(_SECONDARY_BUTTONS, "secondaryButtonRow")
        )

        # ---- Status bar (between buttons and log) --------------------
        self._status_bar = StatusBar()
        # Strip the status bar's own outer margins since we control
        # spacing at the root layout.  But keep the internal
        # label+progress horizontal layout.
        root.addWidget(self._status_bar)

        # ---- Stop session row (red destructive button) ---------------
        root.addWidget(self._build_stop_row())

        # ---- Log panel header + log pane -----------------------------
        root.addWidget(self._build_log_panel_header())

        self._log_pane = LogPane(
            cfg=self._cfg,
            tag_colors=theme_tag_colors,
        )
        root.addWidget(self._log_pane, stretch=1)

        # Optional system-tray companion — wired by ``app.py`` after
        # construction.  ``None`` means no tray (headless / VM /
        # tests); the status/progress methods below tolerate that.
        self._tray: "JellyRipTray | None" = None
        # Track the latest known progress so the tray tooltip can
        # combine status text with current percent on either update.
        self._last_progress_pct: int | None = None

        # AI chat sidebar — AI BRANCH only.  Lazily constructed on
        # first show via ``ensure_chat_sidebar()`` so MainWindow
        # construction stays fast and the dock doesn't render at
        # startup unless the user (or the persisted
        # ``opt_ai_sidebar_open`` cfg key) asks for it.  See
        # ``gui_qt/ai_chat_sidebar.py`` for the widget itself.
        self._chat_sidebar: "ChatSidebar | None" = None

    # ------------------------------------------------------------------
    # Layout builders
    # ------------------------------------------------------------------

    def _build_header(self) -> QWidget:
        """App header: accent-colored title + small monospace subtitle.
        Mirrors the ``.jr-header`` block in
        ``docs/design/themes/qt-mock.jsx``.
        """
        wrap = QFrame()
        wrap.setObjectName("appHeader")

        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 22, 0, 20)
        lay.setSpacing(4)

        title = QLabel(APP_DISPLAY_NAME)
        title.setObjectName("appHeaderTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(title)

        # Hero subtitle.  AI BRANCH renders the variant tag so users
        # can tell at a glance which fork they're running; MAIN keeps
        # the toolkit name ("PYSIDE6") for the same purpose.
        subtitle_text = "AI EDITION" if APP_VARIANT == "ai" else "PYSIDE6"
        subtitle = QLabel(subtitle_text)
        subtitle.setObjectName("appHeaderSubtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(subtitle)

        return wrap

    def _build_drive_row(self) -> QWidget:
        """Drive row: ``Drive:`` label + combo + refresh button."""
        wrap = QFrame()
        wrap.setObjectName("driveRow")

        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(24, 18, 24, 4)
        lay.setSpacing(10)

        label = QLabel("DRIVE:")
        label.setObjectName("driveLabel")
        lay.addWidget(label)

        self._drive_combo = QComboBox()
        self._drive_combo.setObjectName("driveCombo")
        self._drive_combo.setEditable(False)
        # 3c-ii populates the combo with real drives via the
        # controller's drive-scan path.  For 3c-i the combo shows a
        # placeholder so the row isn't visually empty.
        # Placeholder shown before the drive scan completes.  The
        # drive_handler replaces this with real entries on first refresh.
        self._drive_combo.addItem("No optical drive found — click ↻ to scan.")
        self._drive_combo.setEnabled(False)
        lay.addWidget(self._drive_combo, stretch=1)

        refresh = QPushButton("↻")
        refresh.setObjectName("driveRefresh")
        refresh.setFixedWidth(36)
        refresh.setToolTip("Refresh drive list")
        refresh.clicked.connect(self.drive_refresh_clicked.emit)
        lay.addWidget(refresh)

        return wrap

    def _build_utility_toolbar(self) -> QToolBar:
        """Top utility toolbar: Settings / Check Updates / Copy Log /
        Browse Folder.

        Implemented as a real ``QToolBar`` rather than a row of
        ``QPushButton`` chips so the actions land in the platform-
        native location (above the workflow buttons) and benefit
        from ``QToolBar``'s overflow handling — if the window is
        narrow, Qt automatically tucks the actions into a
        ``>>`` overflow menu instead of letting them clip.

        Each action gets the same ``objectName`` the chip row used
        (``utilSettings`` / ``utilUpdates`` / ``utilCopyLog`` /
        ``utilBrowse``) so the existing
        ``UtilityHandler.handle_util*`` dispatch and the QSS
        selectors keep working.  ``self._utility_buttons`` maps
        objectName → ``QAction`` for tests + handler wiring.
        """
        toolbar = QToolBar("Utilities", self)
        toolbar.setObjectName("utilityToolBar")
        # Keep the toolbar pinned in place — it's the primary
        # navigation surface, not a draggable accessory.
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        # Text-only style; we have no icons on the chips today and
        # the leading glyph in each label (⚙ ⇡ ⎘ →) reads as one.
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)

        self._utility_buttons: dict[str, QAction] = {}
        for label_text, object_name in _UTILITY_BUTTONS:
            action = QAction(label_text, self)
            action.setObjectName(object_name)
            action.triggered.connect(
                lambda _checked=False, name=object_name:
                    self.utility_button_clicked.emit(name)
            )
            toolbar.addAction(action)
            # Set the same objectName on the underlying QToolButton
            # so the existing QSS rules (``QPushButton#utilSettings``
            # etc.) can be migrated to ``QToolButton#utilSettings``
            # without a separate per-action mapping.
            tool_button = toolbar.widgetForAction(action)
            if tool_button is not None:
                tool_button.setObjectName(object_name)
            self._utility_buttons[object_name] = action

        return toolbar

    def _build_button_row(
        self,
        spec: tuple[tuple[str, str, str], ...],
        row_object_name: str,
    ) -> QWidget:
        """Build a row of workflow mode buttons from a declarative
        spec.  ``spec`` items are ``(label, role, objectName)``.

        The ``role`` argument doesn't directly affect this widget —
        it's there so the QSS file can pick up a ``modeGo*`` /
        ``modeInfo*`` / ``modeAlt*`` / ``modeWarn*`` objectName and
        color the button per the active theme's role.
        """
        wrap = QFrame()
        wrap.setObjectName(row_object_name)

        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(24, 18, 24, 0)
        lay.setSpacing(10)

        # Track buttons by objectName for tests + future click-handler
        # wiring.
        if not hasattr(self, "_workflow_buttons"):
            self._workflow_buttons: dict[str, QPushButton] = {}

        for label_text, _role, object_name in spec:
            btn = QPushButton(label_text)
            btn.setObjectName(object_name)
            btn.setMinimumHeight(56)
            btn.clicked.connect(
                lambda _checked=False, name=object_name:
                    self._on_workflow_button(name)
            )
            self._workflow_buttons[object_name] = btn
            lay.addWidget(btn, stretch=1)

        return wrap

    def _build_stop_row(self) -> QWidget:
        """Stop Session row — right-aligned, red destructive button.
        Maps to the ``danger`` role in the QSS theme."""
        wrap = QFrame()
        wrap.setObjectName("stopRow")

        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(24, 4, 24, 10)
        lay.addStretch(1)

        # ``⏹`` U+23F9 Stop square — canonical stop transport glyph
        # per docs/symbol-library.md Section 1.
        self._stop_button = QPushButton("⏹  Stop Session")
        self._stop_button.setObjectName("stopButton")
        self._stop_button.setEnabled(False)  # 3c-ii enables when work is in flight
        self._stop_button.clicked.connect(
            lambda: self._on_workflow_button("stopSession")
        )
        lay.addWidget(self._stop_button)

        return wrap

    def _build_log_panel_header(self) -> QWidget:
        """Tiny header above the log pane: ``LIVE LOG`` label + LED
        indicator.  The LED's pulsing animation isn't ported in 3c-i
        — that's polish for a later session.  3c-i just gets the
        static label + dot."""
        wrap = QFrame()
        wrap.setObjectName("logPanelHeader")

        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(24, 14, 24, 6)
        lay.setSpacing(8)

        label = QLabel("LIVE LOG")
        label.setObjectName("logLabel")
        lay.addWidget(label)

        lay.addStretch(1)

        led = QLabel("● streaming")
        led.setObjectName("logLed")
        lay.addWidget(led)
        self._log_led = led

        return wrap

    # ------------------------------------------------------------------
    # UIAdapter Protocol — forwards events to the leaf widgets.
    # ------------------------------------------------------------------
    #
    # The Protocol shape is in ``ui/adapters.py``:
    #
    #     handle_event(event)
    #     on_progress(job_id, value)
    #     on_log(job_id, message)
    #     on_error(job_id, error)
    #     on_complete(job_id)
    #
    # The tkinter implementation in ``gui/main_window.py`` (lines
    # 282-313) just dispatches event types and forwards to ``set_*``
    # methods.  We mirror that exactly.

    def handle_event(self, event: "Event") -> None:
        """Dispatch a controller event to the appropriate handler."""
        if event.type == "progress":
            percent = event.data.get("percent")
            if isinstance(percent, (int, float)):
                self.on_progress(event.job_id, float(percent))
            return

        if event.type == "log":
            self.on_log(event.job_id, str(event.data.get("message", "")))
            return

        if event.type == "done":
            self.on_complete(event.job_id)
            return

        if event.type == "error":
            raw_error = event.data.get("error", "Unknown error")
            error = (
                raw_error
                if isinstance(raw_error, Exception)
                else Exception(str(raw_error))
            )
            self.on_error(event.job_id, error)

    def on_progress(self, _job_id: str, value: float) -> None:
        self.set_progress(value)

    def on_log(self, _job_id: str, message: str) -> None:
        self.append_log(message)

    def on_error(self, job_id: str, error: Exception) -> None:
        # 3c-i: log the error inline; the modal error dialog lands
        # in 3c-ii.  This is degraded behavior but won't lose the
        # error — the user still sees it in the log pane.
        prefix = f"Job {job_id}: " if job_id else ""
        self.append_log(f"ERROR — {prefix}{error}")

    def on_complete(self, _job_id: str) -> None:
        self.set_progress(100)

    # ------------------------------------------------------------------
    # Status / progress / log methods called by the controller.
    # ------------------------------------------------------------------

    def set_status(self, msg: str) -> None:
        """Set the status text.  Auto-classifies via
        ``status_role_for_message`` so the QSS theme can color it.

        Thread-safe: marshals onto the GUI thread when called from a
        worker."""
        submit_to_main(self._invoker, self._set_status_main, msg)

    def _set_status_main(self, msg: str) -> None:
        text = str(msg).strip() or "Ready"
        self._status_bar.set_status(text)
        if self._tray is not None:
            self._tray.update_tooltip(text, self._last_progress_pct)

    def set_progress(
        self,
        value: float | None,
        *,
        current_bytes: int | None = None,
        total_bytes: int | None = None,
    ) -> None:
        """Set determinate progress in 0-100.  Negative or ``None``
        treats as 0 (matches tkinter's defensive coercion at line
        7521 of gui/main_window.py).

        When the controller knows byte totals (e.g., during a
        MakeMKV rip where the title's size is known from the scan),
        passing ``current_bytes`` + ``total_bytes`` makes the bar
        render "X.X GB / Y.Y GB · NN%" instead of just the percent.

        Thread-safe."""
        submit_to_main(
            self._invoker, self._set_progress_main,
            value, current_bytes, total_bytes,
        )

    def _set_progress_main(
        self,
        value: float | None,
        current_bytes: int | None = None,
        total_bytes: int | None = None,
    ) -> None:
        if value is None or value < 0:
            self._status_bar.set_progress(
                0, 100,
                current_bytes=current_bytes, total_bytes=total_bytes,
            )
            self._last_progress_pct = 0
        else:
            self._status_bar.set_progress(
                int(value), 100,
                current_bytes=current_bytes, total_bytes=total_bytes,
            )
            self._last_progress_pct = int(value)
        if self._tray is not None:
            self._tray.update_tooltip(
                self._status_bar.label_text,
                self._last_progress_pct,
            )

    def start_indeterminate(self) -> None:
        """Switch progress bar into the marquee / busy mode.

        Thread-safe."""
        submit_to_main(
            self._invoker,
            lambda: self._status_bar.set_progress(0, 0),
        )

    def stop_indeterminate(self) -> None:
        """Return progress bar to determinate mode at 0.

        Thread-safe."""
        submit_to_main(
            self._invoker,
            lambda: self._status_bar.set_progress(0, 100),
        )

    def append_log(self, msg: str) -> None:
        """Append a log line.  Mirrors
        ``JellyRipperGUI.append_log`` — single-line append.

        Thread-safe — most append_log callers are worker threads
        (the controller's log path)."""
        submit_to_main(self._invoker, self._log_pane.append, str(msg))

    def set_tray(self, tray: "JellyRipTray | None") -> None:
        """Attach (or detach) the system-tray companion.  Wired by
        ``gui_qt/app.py`` after the window exists; tests can pass
        ``None`` to disable."""
        self._tray = tray

    def set_tray_enabled(self, enabled: bool) -> None:
        """Toggle the system-tray companion at runtime.

        When ``enabled=True`` and no tray exists, constructs one.
        When ``enabled=False``, hides + drops the existing tray.
        Used by the Appearance tab for click-to-apply.

        Persists nothing — the tab caller is responsible for the
        cfg write.  Defensive against double-toggles: hiding when
        already None is a no-op.
        """
        from gui_qt.tray_icon import JellyRipTray
        from shared.runtime import APP_DISPLAY_NAME
        if enabled:
            if self._tray is not None:
                return
            self._tray = JellyRipTray(self, app_name=APP_DISPLAY_NAME)
        else:
            if self._tray is None:
                return
            self._tray.hide()
            self._tray = None

    def notify_tray_complete(self, body: str | None = None) -> None:
        """Show the tray's success balloon — call from the
        controller when a session ends cleanly.  No-op if no tray
        is attached."""
        if self._tray is not None:
            if body is None:
                self._tray.notify_complete()
            else:
                self._tray.notify_complete(body=body)

    def notify_tray_failure(self, body: str | None = None) -> None:
        """Show the tray's failure balloon.  No-op without a tray."""
        if self._tray is not None:
            if body is None:
                self._tray.notify_failure()
            else:
                self._tray.notify_failure(body=body)

    # ------------------------------------------------------------------
    # Test hooks — read-only access to internals.
    # ------------------------------------------------------------------

    @property
    def log_pane(self) -> LogPane:
        return self._log_pane

    @property
    def status_bar(self) -> StatusBar:
        return self._status_bar

    @property
    def drive_combo(self) -> QComboBox:
        return self._drive_combo

    @property
    def workflow_buttons(self) -> Mapping[str, QPushButton]:
        return dict(self._workflow_buttons)

    @property
    def utility_buttons(self) -> Mapping[str, QAction]:
        """Map of utility-action objectName → ``QAction``.

        Named ``utility_buttons`` for legacy compatibility — the
        chip row used ``QPushButton``s before the 2026-05-04
        ``QToolBar`` migration.  Tests trigger an action via
        ``mw.utility_buttons["utilCopyLog"].trigger()``."""
        return dict(self._utility_buttons)

    @property
    def stop_button(self) -> QPushButton:
        return self._stop_button

    # ------------------------------------------------------------------
    # AI chat sidebar — AI BRANCH only (Phase 4b).
    # ------------------------------------------------------------------

    def ensure_chat_sidebar(self) -> "ChatSidebar":
        """Lazily construct the chat sidebar dock.

        Returns the existing instance on subsequent calls so the
        controller (and toolbar handler) get a stable reference to
        connect signals to.  Idempotent.
        """
        if self._chat_sidebar is not None:
            return self._chat_sidebar
        from gui_qt.ai_chat_sidebar import ChatSidebar
        # Docked to the right edge.  Central content reflows left when
        # it opens; the user resizes width by dragging the splitter.
        # It stays interactive while a workflow dialog is open because
        # those dialogs run non-modally (gui_qt/dialogs/_modeless.
        # exec_modeless) — not by detaching the dock from this window.
        sidebar = ChatSidebar(parent=self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, sidebar)
        try:
            width = int(self._cfg.get("opt_ai_sidebar_width", 360) or 360)
        except (TypeError, ValueError):
            width = 360
        # resizeDocks is the reliable way to size a freshly-added dock.
        try:
            self.resizeDocks([sidebar], [width], Qt.Orientation.Horizontal)
        except Exception:
            pass
        # Hidden until the user opens it via the ☰ Chat chip — matches
        # the lazy "closed until asked for" contract.
        sidebar.hide()
        self._chat_sidebar = sidebar
        return sidebar

    def toggle_chat_sidebar(self) -> bool:
        """Toggle the sidebar dock's visibility.  Constructs it on
        first call.  Returns the new ``isVisible()`` state.
        """
        sidebar = self.ensure_chat_sidebar()
        new_state = not sidebar.isVisible()
        sidebar.setVisible(new_state)
        if new_state:
            sidebar.raise_()
            sidebar.focus_input()
        return new_state

    @property
    def chat_sidebar(self) -> "ChatSidebar | None":
        """The chat sidebar window if constructed, else ``None``.  Tests
        and the controller use this to introspect state without
        triggering construction."""
        return self._chat_sidebar

    # ------------------------------------------------------------------
    # Workflow-dialog soft-lock.
    #
    # Workflow dialogs (identity step, duplicate resolution, space
    # override, …) run *non-modally* so the docked AI chat stays usable
    # while they're open (see gui_qt/dialogs/_modeless.exec_modeless).
    # Non-modal means the main window is still clickable, so we disable
    # the controls that would desync a running workflow — the mode
    # buttons, Stop, and the drive picker — for the dialog's lifetime,
    # then restore each control's exact prior enabled-state.  Ref-counted
    # so back-to-back dialogs in one workflow don't fight over the
    # snapshot.  Called on the GUI thread from exec_modeless — never
    # from a worker thread — so touching widgets here is safe.
    # ------------------------------------------------------------------

    def begin_workflow_dialog(self) -> None:
        """Soft-lock workflow controls while a non-modal workflow
        dialog is open.  Ref-counted; safe to nest."""
        self._workflow_dialog_depth = getattr(self, "_workflow_dialog_depth", 0) + 1
        if self._workflow_dialog_depth != 1:
            return
        guarded: list[QWidget] = list(self._workflow_buttons.values())
        for attr in ("_stop_button", "_drive_combo"):
            widget = getattr(self, attr, None)
            if widget is not None:
                guarded.append(widget)
        snapshot: list[tuple[QWidget, bool]] = []
        for widget in guarded:
            snapshot.append((widget, widget.isEnabled()))
            widget.setEnabled(False)
        self._workflow_dialog_snapshot = snapshot

    def end_workflow_dialog(self) -> None:
        """Release the soft-lock, restoring each control's prior
        enabled-state, once the outermost dialog closes."""
        depth = getattr(self, "_workflow_dialog_depth", 0)
        if depth <= 0:
            return
        depth -= 1
        self._workflow_dialog_depth = depth
        if depth != 0:
            return
        for widget, was_enabled in getattr(self, "_workflow_dialog_snapshot", []):
            try:
                widget.setEnabled(was_enabled)
            except RuntimeError:
                pass  # widget already deleted
        self._workflow_dialog_snapshot = []

    def get_chat_ui_snapshot(self) -> dict[str, object]:
        """Snapshot the live UI state into a dict the chat
        controller's on-device fallback path consumes.

        Mirrors the tkinter ``_get_ai_sidebar_snapshot`` shape so
        ``build_ui_help_fallback`` reads the same keys regardless of
        which UI built the snapshot.  Resilient to missing widgets:
        missing fields land as empty strings or zero so the helper
        can still produce a sensible answer.
        """
        snapshot: dict[str, object] = {
            "status": "",
            "selected_drive": "",
            "ai_mode": str(self._cfg.get("opt_ai_mode", "cloud") or "cloud"),
            "abort_button_state": "",
            "progress_percent": 0.0,
            "live_log_tail": "",
        }
        try:
            sb = self._status_bar
            snapshot["status"] = sb.label_text
            try:
                snapshot["progress_percent"] = float(sb.progress_value or 0)
            except Exception:
                snapshot["progress_percent"] = 0.0
        except Exception:
            pass
        try:
            stop_btn = self._stop_button
            snapshot["abort_button_state"] = (
                "normal" if stop_btn.isEnabled() else "disabled"
            )
        except Exception:
            pass
        try:
            drive_combo = getattr(self, "_drive_combo", None)
            if drive_combo is not None:
                snapshot["selected_drive"] = drive_combo.currentText()
        except Exception:
            pass
        try:
            log_text = self._log_pane.get_text()
            # Just the tail — the controller's worker thread can
            # truncate further if needed for token budget.
            snapshot["live_log_tail"] = log_text[-2200:] if log_text else ""
        except Exception:
            pass
        return snapshot

    # ------------------------------------------------------------------
    # 3c-ii territory — stubbed dialog methods.
    # ------------------------------------------------------------------
    #
    # These raise NotImplementedError because:
    #
    #   1. They're never called by 3c-i code paths (no workflow
    #      buttons are wired this session).
    #   2. Each one needs a proper Qt dialog — designing them in
    #      3c-i would mean either rushed dialogs or drift between
    #      3c-i and 3c-ii.
    #
    # Each stub names the brief section it belongs to so a future
    # session can find the right context.

    def show_info(self, title: str, msg: str) -> None:
        """Informational message dialog.  Delegates to
        ``gui_qt.dialogs.show_info``.

        Thread-safe — modal dialog runs on the GUI thread; worker
        thread (if any) blocks until the user dismisses it."""
        run_on_main(self._invoker, _show_info, self, title, msg)

    def show_error(self, title: str, msg: str) -> None:
        """Error message dialog.  Delegates to
        ``gui_qt.dialogs.show_error``.  Callers that want recovery-
        guidance text should pre-format ``msg`` via
        ``ui.dialogs.friendly_error`` (toolkit-agnostic helper).

        Thread-safe."""
        run_on_main(self._invoker, _show_error, self, title, msg)

    def ask_yesno(self, prompt: str) -> bool:
        """Yes/No confirmation.  Delegates to
        ``gui_qt.dialogs.ask_yesno``.  Default = No (less destructive).

        Thread-safe — worker thread blocks until user answers."""
        return run_on_main(self._invoker, _ask_yesno, self, prompt)

    def ask_input(
        self,
        label: str,
        prompt: str,
        default: str = "",
        *args: Any,
        **kwargs: Any,
    ) -> str | None:
        """Text input dialog.  Delegates to ``gui_qt.dialogs.ask_input``.
        Returns the entered text, ``""`` on OK with empty input, or
        ``None`` on Cancel.

        Thread-safe."""
        return run_on_main(self._invoker, _ask_input, self, label, prompt, default)

    def ask_space_override(
        self,
        required_gb: float,
        free_gb: float,
        *args: Any,
        **kwargs: Any,
    ) -> bool:
        """Not-enough-space override dialog.  Returns ``True`` iff the
        user explicitly chose Proceed Anyway; ``False`` on cancel.

        Thread-safe."""
        return run_on_main(
            self._invoker, _ask_space_override, self, required_gb, free_gb,
        )

    def ask_duplicate_resolution(
        self,
        prompt: str,
        retry_text: str = "Swap and Retry",
        bypass_text: str = "Not a Dup",
        stop_text: str = "Stop",
    ) -> DuplicateResolutionChoice:
        """Three-way duplicate-disc prompt.  Returns ``"retry"``,
        ``"bypass"``, or ``"stop"``.  Mirrors the tkinter signature
        at ``gui/main_window.py:4243``.

        Thread-safe."""
        def _call() -> DuplicateResolutionChoice:
            return _ask_duplicate_resolution(
                self,
                prompt,
                retry_text=retry_text,
                bypass_text=bypass_text,
                stop_text=stop_text,
            )
        return run_on_main(self._invoker, _call)

    def ask_tv_setup(
        self,
        default_title: str = "",
        default_year: str = "",
        default_season: str = "1",
        default_starting_disc: str = "1",
        default_metadata_provider: str = "TMDB",
        default_metadata_id: str = "",
        default_episode_mapping: str = "auto",
        default_multi_episode: str = "auto",
        default_specials: str = "ask",
        default_replace_existing: bool = False,
        **kwargs: Any,
    ) -> "TVSessionSetup | None":
        """TV setup dialog.  Delegates to
        ``gui_qt.dialogs.session_setup.ask_tv_setup``.  Returns the
        populated ``TVSessionSetup`` on OK or ``None`` on Cancel.

        Thread-safe."""
        def _call() -> "TVSessionSetup | None":
            return _ask_tv_setup(
                self,
                default_title=default_title,
                default_year=default_year,
                default_season=default_season,
                default_starting_disc=default_starting_disc,
                default_metadata_provider=default_metadata_provider,
                default_metadata_id=default_metadata_id,
                default_episode_mapping=default_episode_mapping,
                default_multi_episode=default_multi_episode,
                default_specials=default_specials,
                default_replace_existing=default_replace_existing,
            )
        return run_on_main(self._invoker, _call)

    def ask_movie_setup(
        self,
        default_title: str = "",
        default_year: str = "",
        default_metadata_provider: str = "TMDB",
        default_metadata_id: str = "",
        **kwargs: Any,
    ) -> "MovieSessionSetup | None":
        """Movie setup dialog.  Delegates to
        ``gui_qt.dialogs.session_setup.ask_movie_setup``.  Returns the
        populated ``MovieSessionSetup`` on OK or ``None`` on Cancel.

        Thread-safe."""
        def _call() -> "MovieSessionSetup | None":
            return _ask_movie_setup(
                self,
                default_title=default_title,
                default_year=default_year,
                default_metadata_provider=default_metadata_provider,
                default_metadata_id=default_metadata_id,
            )
        return run_on_main(self._invoker, _call)

    def show_disc_tree(
        self,
        disc_titles: "Sequence[dict[str, Any]]",
        is_tv: bool,
        preview_callback: "Callable[[int], None] | None" = None,
        *args: Any,
        **kwargs: Any,
    ) -> "list[str] | None":
        """Disc-tree selector — multi-select from scanned titles.
        Delegates to ``gui_qt.dialogs.show_disc_tree``.

        Returns ``list[str]`` of selected title IDs (controller calls
        ``int()`` on each), or ``None`` on cancel.

        Thread-safe."""
        def _call() -> "list[str] | None":
            return _show_disc_tree(
                self, disc_titles, is_tv, preview_callback,
            )
        return run_on_main(self._invoker, _call)

    def show_temp_manager(
        self,
        old_folders: "Sequence[Any]",
        engine: Any,
        log_fn: "Callable[[str], None]",
    ) -> None:
        """Temp Session Manager dialog.  Side-effect-only — returns
        ``None``.  Empty ``old_folders`` short-circuits without
        opening the dialog (matches tkinter at
        ``gui/main_window.py:5930``).

        Thread-safe."""
        def _call() -> None:
            return _show_temp_manager(self, old_folders, engine, log_fn)
        return run_on_main(self._invoker, _call)

    # ------------------------------------------------------------------
    # Wizard-step wrappers — controller calls these.  Delegate to the
    # already-ported step functions in ``gui_qt.setup_wizard``.  Lazy
    # imports here are vestigial — kept because they break a heavy
    # PySide6 widget-class load until the wizard is actually opened,
    # not because of any tkinter coupling.  ``gui_qt.setup_wizard``
    # imports its dataclasses from ``shared.wizard_types`` (Phase
    # 3h-shared-types, 2026-05-04) and has zero ``gui/*`` references.
    # ------------------------------------------------------------------

    def show_scan_results_step(
        self,
        classified: Any,
        drive_info: Any = None,
    ) -> Any:
        """Step 1: scan results + classification.  Returns
        ``'movie'`` / ``'tv'`` / ``'standard'`` / ``None``.

        Thread-safe.  Mirrors the tkinter contract at
        ``gui/main_window.py:4675``."""
        from gui_qt.setup_wizard import show_scan_results

        def _call() -> Any:
            return show_scan_results(self, classified, drive_info)
        return run_on_main(self._invoker, _call)

    def show_content_mapping_step(self, classified: Any) -> Any:
        """Step 3: content mapping.  Returns ``ContentSelection`` or
        ``None``.

        Thread-safe."""
        from gui_qt.setup_wizard import show_content_mapping

        def _call() -> Any:
            return show_content_mapping(self, classified)
        return run_on_main(self._invoker, _call)

    def show_extras_classification_step(
        self, extra_titles: Any,
    ) -> Any:
        """Step 4: extras classification.  Returns
        ``ExtrasAssignment`` or ``None``.

        Thread-safe."""
        from gui_qt.setup_wizard import show_extras_classification

        def _call() -> Any:
            return show_extras_classification(self, extra_titles)
        return run_on_main(self._invoker, _call)

    def show_output_plan_step(
        self,
        base_folder: str,
        main_label: str,
        extras_map: Any,
        detail_lines: Any = None,
        header_text: str | None = None,
        subtitle_text: str | None = None,
        confirm_text: str | None = None,
    ) -> bool:
        """Step 5: output plan review.  Returns ``True`` on confirm,
        ``False`` on cancel.

        Thread-safe."""
        from gui_qt.setup_wizard import show_output_plan

        def _call() -> bool:
            return show_output_plan(
                self,
                base_folder=base_folder,
                main_label=main_label,
                extras_map=extras_map,
                detail_lines=detail_lines,
                header_text=header_text,
                subtitle_text=subtitle_text,
                confirm_text=confirm_text,
            )
        return run_on_main(self._invoker, _call)

    # ------------------------------------------------------------------
    # Folder picker — non-modal-dialog dialog method
    # ------------------------------------------------------------------

    def ask_directory(
        self,
        title: str,
        prompt: str,
        initialdir: str = "",
    ) -> str | None:
        """Native folder picker.  Returns the selected path or
        ``None`` if cancelled.  Mirrors the tkinter signature
        at ``gui/main_window.py:4761``.

        Thread-safe."""
        from PySide6.QtWidgets import QFileDialog
        import os

        def _call() -> str | None:
            chosen = QFileDialog.getExistingDirectory(
                self,
                f"{title}: {prompt}" if prompt else title,
                initialdir or os.path.expanduser("~"),
            )
            return chosen if chosen else None
        return run_on_main(self._invoker, _call)

    # ------------------------------------------------------------------
    # Picker dialogs — stubbed for a future 3c-iii pass
    # ------------------------------------------------------------------

    def show_extras_picker(
        self,
        title: str,
        prompt: str,
        options: "Sequence[str]",
    ) -> "list[int] | None":
        """Multi-select picker, all items pre-selected.  Returns the
        list of selected 0-based indices on Confirm, or ``None`` on
        Cancel / Esc / window close.  Mirrors tkinter at
        ``gui/main_window.py:5858``.

        Thread-safe."""
        def _call() -> "list[int] | None":
            return _show_extras_picker(self, title, prompt, options)
        return run_on_main(self._invoker, _call)

    def show_file_list(
        self,
        title: str,
        prompt: str,
        options: "Sequence[str]",
    ) -> "list[str]":
        """Multi-select file-list picker, first item pre-selected.
        Returns the list of selected texts on Confirm, or ``[]``
        (empty list, *not* ``None``) on Cancel / Esc / window close.
        Empty-and-cancel are intentionally indistinguishable — the
        controller uses ``if not selected`` to detect both.  Mirrors
        tkinter at ``gui/main_window.py:5788``.

        Thread-safe."""
        def _call() -> "list[str]":
            return _show_file_list(self, title, prompt, options)
        return run_on_main(self._invoker, _call)

    # ------------------------------------------------------------------
    # Internal — workflow button click handler stub.
    # ------------------------------------------------------------------

    def _on_workflow_button(self, object_name: str) -> None:
        """Stub click handler for workflow buttons.  3c-ii replaces
        this with controller-bound calls (e.g., ``modeGoMovie`` →
        ``controller.run_movie_rip``).

        Phase 3c-i emitted a "TODO 3c-ii" log line here so manual
        runs without a controller didn't feel unresponsive.  Now
        that the controller is wired (``WorkflowLauncher``), the
        log line was just engineering noise on every click — the
        controller's own session-start log lines fire microseconds
        later and tell the user what they need.
        """
        self.workflow_button_clicked.emit(object_name)
