"""Settings dialog shell — QDialog hosting a QTabWidget.

Currently exposes only the **Appearance** tab (see
``gui_qt/settings/tab_appearance.py``).  The other tabs
(everyday / advanced / expert profiles) are pending — see
``docs/handoffs/phase-3d-port-settings-tabs.md`` for the brief.

**Buttons:** OK (commit + close), Cancel (revert + close).  As of
2026-05-04 the Apply button is gone — every control on the
Appearance tab applies its change instantly (click-to-apply), so
Apply was redundant.  See ``docs/handoffs/appearance-tab-spec.md``
design principles 1–3 for the rationale.

The dialog calls ``apply()`` on every tab when OK is pressed
(commits the in-memory cfg to disk).  It calls ``cancel()`` on
every tab when Cancel/Esc fires (reverts runtime state to the
snapshot taken at construction).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Mapping

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
)

from gui_qt.settings.tab_appearance import AppearanceTab
from gui_qt.settings.tab_everyday import EverydayTab
from gui_qt.settings.tab_paths import PathsTab
from gui_qt.settings.tab_reliability import ReliabilityTab

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


class SettingsDialog(QDialog):
    """Modal settings dialog.  Currently shows the Appearance tab only."""

    def __init__(
        self,
        cfg: dict[str, Any],
        list_themes: Callable[[], list[str]],
        load_theme: Callable[[str], None],
        save_cfg: Callable[[Mapping[str, Any]], None] | None = None,
        window: "Any | None" = None,
        parent: "QWidget | None" = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("settingsDialog")
        self.setWindowTitle("JellyRip Settings")
        self.setModal(True)
        self.resize(560, 620)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Tab host.
        self._tabs = QTabWidget()
        self._tabs.setObjectName("settingsTabs")

        # Tab order: Everyday first (most-touched), then Paths,
        # Reliability, Appearance.  Appearance lives last because
        # its preview-on-click semantics make it feel like a polish
        # tab; users open Settings looking for behavior toggles.
        self._everyday_tab = EverydayTab(cfg=cfg, save_cfg=save_cfg)
        self._tabs.addTab(self._everyday_tab, "Everyday")

        self._paths_tab = PathsTab(cfg=cfg, save_cfg=save_cfg)
        self._tabs.addTab(self._paths_tab, "Paths")

        self._reliability_tab = ReliabilityTab(cfg=cfg, save_cfg=save_cfg)
        self._tabs.addTab(self._reliability_tab, "Reliability")

        self._appearance_tab = AppearanceTab(
            cfg=cfg,
            list_themes=list_themes,
            load_theme=load_theme,
            save_cfg=save_cfg,
            window=window,
        )
        self._tabs.addTab(self._appearance_tab, "Appearance")

        outer.addWidget(self._tabs, stretch=1)

        # Button row — Cancel left, OK right.  No Apply: each tab
        # applies its changes live as the user toggles.
        button_row = QHBoxLayout()
        button_row.setContentsMargins(16, 10, 16, 12)
        button_row.setSpacing(8)

        cancel = QPushButton("Cancel")
        cancel.setObjectName("cancelButton")
        cancel.clicked.connect(self._on_cancel)
        button_row.addWidget(cancel)

        button_row.addStretch(1)

        ok = QPushButton("OK")
        ok.setObjectName("confirmButton")
        ok.setDefault(True)
        ok.clicked.connect(self._on_ok)
        button_row.addWidget(ok)

        outer.addLayout(button_row)

        # Test hooks
        self._cancel_btn = cancel
        self._ok_btn = ok

    @property
    def appearance_tab(self) -> AppearanceTab:
        return self._appearance_tab

    @property
    def paths_tab(self) -> PathsTab:
        return self._paths_tab

    @property
    def everyday_tab(self) -> EverydayTab:
        return self._everyday_tab

    @property
    def reliability_tab(self) -> ReliabilityTab:
        return self._reliability_tab

    # Backward-compat alias for code/tests that still reference
    # the pre-2026-05-04 name.
    @property
    def themes_tab(self) -> AppearanceTab:
        return self._appearance_tab

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _on_ok(self) -> None:
        """Commit every tab's in-memory cfg to disk, then close.
        Click-to-apply means runtime state already matches the
        user's choices; ``apply()`` is just the persist step."""
        for i in range(self._tabs.count()):
            tab = self._tabs.widget(i)
            if hasattr(tab, "apply"):
                tab.apply()
        self.accept()

    def _on_cancel(self) -> None:
        """Ask each tab to revert any preview-applied changes, then
        close.  The themes tab uses this to restore the original
        QSS if the user previewed but didn't commit."""
        for i in range(self._tabs.count()):
            tab = self._tabs.widget(i)
            if hasattr(tab, "cancel"):
                tab.cancel()
        self.reject()

    def keyPressEvent(self, event):  # noqa: N802 (Qt convention)
        if event.key() == Qt.Key.Key_Escape:
            self._on_cancel()
            return
        super().keyPressEvent(event)


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def show_settings(
    parent: "QWidget | None",
    cfg: dict[str, Any],
    *,
    list_themes: Callable[[], list[str]] | None = None,
    load_theme: Callable[[str], None] | None = None,
    save_cfg: Callable[[Mapping[str, Any]], None] | None = None,
    window: "Any | None" = None,
) -> bool:
    """Show the settings dialog.  Returns ``True`` if the user
    pressed OK, ``False`` on Cancel / Esc.

    Default ``list_themes`` and ``load_theme`` lazy-import from
    ``gui_qt.theme`` and ``QApplication.instance()`` so callers
    don't have to pass them in production.

    ``window`` is the live ``MainWindow`` — passed through to the
    Appearance tab so its checkbox toggles can poke runtime state
    (live preview).  When ``None``, the tab degrades to "cfg only"
    and toggles still persist but don't affect the running widgets
    until next launch.
    """
    if list_themes is None or load_theme is None:
        from PySide6.QtWidgets import QApplication
        from gui_qt.theme import list_themes as _list, load_theme as _load
        app = QApplication.instance()

        def _default_load(name: str) -> None:
            if app is not None:
                _load(app, name)

        list_themes = list_themes or _list
        load_theme = load_theme or _default_load

    dialog = SettingsDialog(
        cfg=cfg,
        list_themes=list_themes,
        load_theme=load_theme,
        save_cfg=save_cfg,
        window=window if window is not None else parent,
        parent=parent,
    )
    result = dialog.exec()
    return result == 1
