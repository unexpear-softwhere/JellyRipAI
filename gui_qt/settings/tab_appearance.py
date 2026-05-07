"""Appearance tab — consolidated UI customization.

Replaces the standalone ``tab_themes.py`` (Phase 3d theme picker)
with a single tab that exposes:

* The 6 QSS theme picker (was the old ``ThemesTab``)
* Visual touches added 2026-05-04:
    - Color-code warn/error log lines
    - Severity glyph (⚠/✗) on warn/error log lines
    - Disc-state glyph (◉/⊚/◌) in the drive picker
* Window companions:
    - System-tray icon
    - Startup splash screen (next-launch only)

**Design principles** (per ``docs/handoffs/appearance-tab-spec.md``):

1. **Live preview, not live apply.**  Every control instantly
   updates the running widget so the user can see the result —
   but **does NOT touch cfg**.  cfg is reserved for OK.
2. **OK commits, Cancel reverts.**  OK reads every widget's
   current state, writes it to cfg, persists to disk, closes.
   Cancel walks the snapshot taken at construction and reverses
   every runtime change so the visible app returns to its
   pre-dialog state.  cfg is never touched on Cancel.
3. **Snapshot-on-open.**  The tab snapshots every cfg key at
   ``__init__``.  Both ``apply()`` and ``cancel()`` lean on the
   snapshot — apply uses it to know what to write back if the
   user dismisses without opening (no diff to save), cancel uses
   it to drive the runtime revert.
4. **Defaults match current behavior** — every checkbox defaults
   to ON so users with old ``config.json`` files see no change.

The pure helpers ``normalize_theme_choice`` / ``format_theme_label``
stay in this module so existing imports keep working.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Mapping

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui_qt.themes import THEMES_BY_ID, theme_ids

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Pure helpers — Qt-free so tests can exercise them without widgets
# ---------------------------------------------------------------------------


def normalize_theme_choice(
    cfg_value: str | None,
    available_themes: list[str],
) -> str:
    """Coerce a cfg value into one of the available themes.

    Pure function — testable without Qt.

    * If ``cfg_value`` is in ``available_themes``, returns it.
    * If ``cfg_value`` is missing or unknown, falls back to
      ``"dark_github"`` if available, otherwise the first entry.
    * If ``available_themes`` is empty, returns ``""`` (defensive —
      caller should hide the tab in this case).
    """
    if not available_themes:
        return ""
    if cfg_value and cfg_value in available_themes:
        return cfg_value
    if "dark_github" in available_themes:
        return "dark_github"
    return available_themes[0]


def format_theme_label(theme_id: str) -> str:
    """Render a one-line label for the picker list.

    Format: ``{Name} — {family}``, falling back to the theme id if
    it's not in ``THEMES_BY_ID`` (e.g., a future custom theme on
    disk that doesn't have Python token metadata).
    """
    theme = THEMES_BY_ID.get(theme_id)
    if theme is None:
        return theme_id
    return f"{theme.name} — {theme.family}"


# Cfg keys this tab is allowed to write.  Each one is snapshotted
# at construction so ``cancel()`` can revert.
_APPEARANCE_KEYS = (
    "opt_pyside6_theme",
    "opt_log_color_levels",
    "opt_log_glyph_prefix",
    "opt_drive_state_glyph",
    "opt_tray_icon_enabled",
    "opt_show_splash",
)


# ---------------------------------------------------------------------------
# The tab widget
# ---------------------------------------------------------------------------


class AppearanceTab(QWidget):
    """Appearance / theme / UI customization tab.

    Construction:

        tab = AppearanceTab(
            cfg=cfg,
            list_themes=lambda: gui_qt.theme.list_themes(),
            load_theme=lambda name: gui_qt.theme.load_theme(app, name),
            save_cfg=save_config,
            window=mw,  # so toggles can poke runtime state
        )

    The ``window`` argument is the live ``MainWindow`` so checkbox
    toggles can apply immediately (live preview).  Tests can pass
    ``None`` and the tab degrades to "cfg only — no live preview".
    """

    def __init__(
        self,
        cfg: dict[str, Any],
        list_themes: Callable[[], list[str]],
        load_theme: Callable[[str], None],
        save_cfg: Callable[[Mapping[str, Any]], None] | None = None,
        window: "Any | None" = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("appearanceTab")

        self._cfg = cfg
        self._list_themes = list_themes
        self._load_theme = load_theme
        self._save_cfg = save_cfg
        self._window = window

        # Snapshot every cfg key we can mutate so cancel() can
        # walk the snapshot and revert via the same setters the
        # controls use.  Stored as a frozen dict at init.
        self._snapshot: dict[str, Any] = {
            key: cfg.get(key) for key in _APPEARANCE_KEYS
        }

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 14)
        outer.setSpacing(12)

        subtitle = QLabel(
            "Changes apply instantly.  OK saves them; Cancel reverts."
        )
        subtitle.setObjectName("appearanceSubtitle")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        # ----- Theme section -------------------------------------------
        outer.addWidget(self._section_header("THEME"))

        self._list = QListWidget()
        self._list.setObjectName("themesPickerList")
        self._populate_list()
        outer.addWidget(self._list)

        self._notes_label = QLabel("")
        self._notes_label.setObjectName("themesNotesLabel")
        self._notes_label.setWordWrap(True)
        self._notes_label.setMinimumHeight(48)
        outer.addWidget(self._notes_label)

        # Connect AFTER populate so the initial setCurrentItem during
        # populate doesn't fire a redundant load_theme (the current
        # theme is already loaded by app.py at startup).
        self._list.currentItemChanged.connect(self._on_theme_changed)
        self._refresh_notes_for_current()

        # ----- Log Pane section ----------------------------------------
        outer.addWidget(self._section_header("LOG PANE"))

        self._cb_color_levels = self._make_checkbox(
            text="Color-code warnings and errors",
            cfg_key="opt_log_color_levels",
            on_change=self._on_log_color_changed,
            object_name="appearanceColorLevels",
        )
        outer.addWidget(self._cb_color_levels)

        self._cb_glyph_prefix = self._make_checkbox(
            text="Show severity glyph (⚠/✗) on warn/error log lines",
            cfg_key="opt_log_glyph_prefix",
            on_change=self._on_log_glyph_changed,
            object_name="appearanceGlyphPrefix",
        )
        outer.addWidget(self._cb_glyph_prefix)

        # ----- Drive Picker section ------------------------------------
        outer.addWidget(self._section_header("DRIVE PICKER"))

        self._cb_drive_glyph = self._make_checkbox(
            text="Show disc-state glyph (◉ inserted / ⊚ empty / ◌ unavailable)",
            cfg_key="opt_drive_state_glyph",
            on_change=self._on_drive_glyph_changed,
            object_name="appearanceDriveGlyph",
        )
        outer.addWidget(self._cb_drive_glyph)

        # ----- Window section ------------------------------------------
        outer.addWidget(self._section_header("WINDOW"))

        self._cb_tray = self._make_checkbox(
            text="System-tray icon (recommended for long rips)",
            cfg_key="opt_tray_icon_enabled",
            on_change=self._on_tray_changed,
            object_name="appearanceTrayIcon",
        )
        outer.addWidget(self._cb_tray)

        # Splash takes effect on next launch — surface a hint
        # directly under the checkbox.
        splash_wrap = QWidget()
        splash_lay = QVBoxLayout(splash_wrap)
        splash_lay.setContentsMargins(0, 0, 0, 0)
        splash_lay.setSpacing(2)
        self._cb_splash = self._make_checkbox(
            text="Startup splash screen",
            cfg_key="opt_show_splash",
            on_change=self._on_splash_changed,
            object_name="appearanceSplash",
        )
        splash_hint = QLabel("    Takes effect on next launch.")
        splash_hint.setObjectName("appearanceHint")
        splash_lay.addWidget(self._cb_splash)
        splash_lay.addWidget(splash_hint)
        outer.addWidget(splash_wrap)

        outer.addStretch(1)

    # ------------------------------------------------------------------
    # Section header helper — divider + uppercase label
    # ------------------------------------------------------------------

    def _section_header(self, text: str) -> QWidget:
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        divider = QFrame()
        divider.setObjectName("sectionDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFixedHeight(1)
        lay.addWidget(divider)
        header = QLabel(text)
        header.setObjectName("sectionHeader")
        lay.addWidget(header)
        return wrap

    # ------------------------------------------------------------------
    # Checkbox factory — wires cfg write + live-apply on toggle
    # ------------------------------------------------------------------

    def _make_checkbox(
        self,
        text: str,
        cfg_key: str,
        on_change: Callable[[bool], None],
        object_name: str,
    ) -> QCheckBox:
        """Build a checkbox bound to ``cfg_key``.

        Initial state from cfg.  On user toggle, only the live
        runtime hook fires — cfg is NOT modified here.  ``apply()``
        is the single point that writes cfg + persists to disk;
        ``cancel()`` re-fires the hook with the snapshotted value
        to revert the runtime widget.
        """
        cb = QCheckBox(text)
        cb.setObjectName(object_name)
        cb.setChecked(bool(self._cfg.get(cfg_key, True)))

        def handler(state: int, fn=on_change) -> None:
            new = state == Qt.CheckState.Checked.value
            try:
                fn(new)
            except Exception:
                # Live-preview failures shouldn't block the toggle.
                pass

        cb.stateChanged.connect(handler)
        return cb

    # ------------------------------------------------------------------
    # Theme list — selection + initial population
    # ------------------------------------------------------------------

    def _populate_list(self) -> None:
        available = self._list_themes()
        chosen = normalize_theme_choice(
            self._cfg.get("opt_pyside6_theme"),
            available,
        )
        for theme_id in theme_ids():
            if theme_id not in available:
                continue
            item = QListWidgetItem(format_theme_label(theme_id))
            item.setData(Qt.ItemDataRole.UserRole, theme_id)
            self._list.addItem(item)
            if theme_id == chosen:
                self._list.setCurrentItem(item)
        for theme_id in available:
            if theme_id not in THEMES_BY_ID:
                item = QListWidgetItem(format_theme_label(theme_id))
                item.setData(Qt.ItemDataRole.UserRole, theme_id)
                self._list.addItem(item)
                if theme_id == chosen:
                    self._list.setCurrentItem(item)

    def _on_theme_changed(self, *_args: Any) -> None:
        """Live preview only — swap the QSS at runtime.

        Does **not** touch cfg.  OK reads the selected row at
        commit time and writes it; Cancel reloads the snapshotted
        original.  Pure preview semantics.
        """
        self._refresh_notes_for_current()
        chosen = self.selected_theme_id()
        if not chosen:
            return
        try:
            self._load_theme(chosen)
        except Exception:
            return

    def _refresh_notes_for_current(self) -> None:
        chosen = self.selected_theme_id()
        theme = THEMES_BY_ID.get(chosen)
        if theme is None:
            self._notes_label.setText("")
            return
        self._notes_label.setText(f"{theme.subtitle}\n\n{theme.notes}")

    def selected_theme_id(self) -> str:
        item = self._list.currentItem()
        if item is None:
            return ""
        return item.data(Qt.ItemDataRole.UserRole) or ""

    # ------------------------------------------------------------------
    # Live-apply handlers — wired to runtime state via self._window
    # ------------------------------------------------------------------

    def _on_log_color_changed(self, enabled: bool) -> None:
        if self._window is None:
            return
        log_pane = getattr(self._window, "log_pane", None)
        if log_pane is not None and hasattr(log_pane, "set_color_levels_enabled"):
            log_pane.set_color_levels_enabled(enabled)

    def _on_log_glyph_changed(self, enabled: bool) -> None:
        if self._window is None:
            return
        log_pane = getattr(self._window, "log_pane", None)
        if log_pane is not None and hasattr(log_pane, "set_glyph_prefix_enabled"):
            log_pane.set_glyph_prefix_enabled(enabled)

    def _on_drive_glyph_changed(self, _enabled: bool) -> None:
        if self._window is None:
            return
        # ``_drive_handler`` is the attr stashed by ``app.py`` on the
        # window during construction.  ``refresh_labels`` re-formats
        # the combo using current cfg — no rescan needed.
        drive_handler = getattr(self._window, "_drive_handler", None)
        if drive_handler is not None and hasattr(drive_handler, "refresh_labels"):
            drive_handler.refresh_labels()

    def _on_tray_changed(self, enabled: bool) -> None:
        if self._window is None:
            return
        if hasattr(self._window, "set_tray_enabled"):
            self._window.set_tray_enabled(enabled)

    def _on_splash_changed(self, _enabled: bool) -> None:
        # Splash is a next-launch-only toggle — there's no live
        # state to poke.  The cfg write happened in the checkbox
        # handler; that's enough.
        return

    # ------------------------------------------------------------------
    # Dialog API — apply on OK, cancel walks the snapshot
    # ------------------------------------------------------------------

    def apply(self) -> None:
        """Commit every widget's current state to cfg + disk.

        This is the only point in the tab's lifecycle that mutates
        cfg.  Read each widget, write the corresponding cfg key,
        then persist.  If the user opened the dialog and clicked
        OK without touching anything, this is effectively a no-op
        save (existing values written back unchanged).
        """
        chosen = self.selected_theme_id()
        if chosen:
            self._cfg["opt_pyside6_theme"] = chosen
        self._cfg["opt_log_color_levels"]  = self._cb_color_levels.isChecked()
        self._cfg["opt_log_glyph_prefix"]  = self._cb_glyph_prefix.isChecked()
        self._cfg["opt_drive_state_glyph"] = self._cb_drive_glyph.isChecked()
        self._cfg["opt_tray_icon_enabled"] = self._cb_tray.isChecked()
        self._cfg["opt_show_splash"]       = self._cb_splash.isChecked()

        if self._save_cfg is None:
            return
        try:
            self._save_cfg(self._cfg)
        except Exception:
            # Persist failure is recoverable — runtime state already
            # reflects the user's choices.  Next launch will load
            # the prior on-disk values.  Caller can decide to log.
            pass

    def cancel(self) -> None:
        """Reverse every runtime preview to the snapshotted state.

        cfg is **not** modified here — preview semantics mean cfg
        was never written during the dialog session.  We only need
        to revert the runtime widgets the live-preview hooks
        touched.

        For each control, compare the current widget state to the
        snapshot.  Only fire the revert hook if they differ —
        avoids redundant calls when the user opens the dialog and
        cancels without touching anything.
        """
        # Theme — if currently-selected differs from the snapshot,
        # reload the original QSS.
        original_theme = self._snapshot.get("opt_pyside6_theme")
        current_theme = self.selected_theme_id()
        if (
            original_theme
            and current_theme
            and current_theme != original_theme
        ):
            try:
                self._load_theme(original_theme)
            except Exception:
                pass

        # Checkboxes — re-fire each live hook with the snapshotted
        # value so the runtime widget follows the revert.  cfg is
        # untouched.
        controls: list[tuple[QCheckBox, str, Callable[[bool], None]]] = [
            (self._cb_color_levels, "opt_log_color_levels",  self._on_log_color_changed),
            (self._cb_glyph_prefix, "opt_log_glyph_prefix",  self._on_log_glyph_changed),
            (self._cb_drive_glyph,  "opt_drive_state_glyph", self._on_drive_glyph_changed),
            (self._cb_tray,         "opt_tray_icon_enabled", self._on_tray_changed),
            (self._cb_splash,       "opt_show_splash",       self._on_splash_changed),
        ]
        for cb, key, handler in controls:
            snap_val = bool(self._snapshot.get(key, True))
            if cb.isChecked() == snap_val:
                continue
            try:
                handler(snap_val)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Backward-compat alias — ``ThemesTab`` was the pre-2026-05-04 name
# ---------------------------------------------------------------------------


# Kept as an alias so external imports (notebooks, contributor
# branches, etc.) keep working.  Production code uses
# ``AppearanceTab`` directly.
ThemesTab = AppearanceTab
