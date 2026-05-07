"""System-tray companion for the JellyRip main window.

Long rips (30-90 minutes per Blu-ray) mean the user often minimises
the window and walks away.  The tray icon lets them:

* See a tooltip with the current status / progress without
  switching focus.
* Get a balloon notification when a job completes or fails.
* Click the icon to restore the window from any state (minimised,
  hidden, behind other apps).

**Additive only** — the main window stays the source of truth.
The tray is a thin peripheral; if ``QSystemTrayIcon.isSystemTrayAvailable``
returns False (rare on modern Windows; common on stripped-down VMs)
the tray helper silently no-ops so the rest of the app keeps
working.

Wired from ``gui_qt/app.py`` after the main window is constructed.
The window forwards ``set_status`` / ``set_progress`` /
``notify_complete`` / ``notify_failure`` to ``JellyRipTray.update_*``;
those methods all tolerate being called when the tray isn't
available.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QStyle

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


# Keep the tray tooltip short — Windows truncates after ~127 chars.
_TOOLTIP_MAX = 120


class JellyRipTray(QObject):
    """Wraps ``QSystemTrayIcon`` with a JellyRip-shaped facade.

    Construct after the main window exists; ``window`` is the widget
    to restore on tray click.  Lifetime is bound to the QApplication —
    keep a reference on the window so it isn't garbage-collected.

    All public methods are no-ops if the platform reports no system
    tray.  This keeps the rest of the app's wiring simple — no
    ``if tray is not None`` guards at every call site.
    """

    def __init__(
        self,
        window: "QWidget",
        app_name: str = "JellyRip",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._window = window
        self._app_name = app_name

        self._available = QSystemTrayIcon.isSystemTrayAvailable()
        if not self._available:
            self._tray: QSystemTrayIcon | None = None
            return

        # Reuse the window's icon when available; otherwise fall
        # back to the platform's standard "media" icon so the tray
        # entry isn't a blank square.
        icon = window.windowIcon()
        if icon.isNull():
            style = QApplication.style()
            icon = style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)

        self._tray = QSystemTrayIcon(icon, parent=self)
        self._tray.setToolTip(self._app_name)
        self._tray.activated.connect(self._on_activated)

        # Right-click menu — Show / Quit.  Single-left-click on the
        # icon also restores via ``_on_activated`` below.
        menu = QMenu()
        show_action = QAction("Show JellyRip", self)
        show_action.triggered.connect(self._restore_window)
        menu.addAction(show_action)

        menu.addSeparator()

        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(quit_action)

        self._tray.setContextMenu(menu)
        self._tray.show()

    # ------------------------------------------------------------------
    # Public API — main-window forwards these.
    # ------------------------------------------------------------------

    @property
    def is_available(self) -> bool:
        """True if the platform has a usable tray.  Tests and
        defensive callers can branch on this; the default callers
        don't need to."""
        return self._available

    def update_tooltip(
        self,
        status: str,
        progress_pct: int | None = None,
    ) -> None:
        """Update the tray tooltip — what the user sees on hover.

        Format: ``JellyRip — {status}`` or, when a percent is given,
        ``JellyRip — {status} ({pct}%)``.  Truncates to keep
        Windows happy.
        """
        if self._tray is None:
            return
        text = f"{self._app_name} — {status}"
        if progress_pct is not None and 0 <= progress_pct <= 100:
            text = f"{text} ({progress_pct}%)"
        if len(text) > _TOOLTIP_MAX:
            text = text[: _TOOLTIP_MAX - 1] + "…"
        self._tray.setToolTip(text)

    def notify_complete(
        self,
        title: str = "Rip complete",
        body: str = "All discs finished successfully.",
    ) -> None:
        """Show a balloon notification on success.  Times out after
        the system default (~5 s on Windows)."""
        if self._tray is None:
            return
        self._tray.showMessage(
            title, body, QSystemTrayIcon.MessageIcon.Information,
        )

    def notify_failure(
        self,
        title: str = "Rip failed",
        body: str = "Check the log for details.",
    ) -> None:
        """Show a balloon notification on error.  Uses the Critical
        icon so Windows highlights it in the notification centre."""
        if self._tray is None:
            return
        self._tray.showMessage(
            title, body, QSystemTrayIcon.MessageIcon.Critical,
        )

    def hide(self) -> None:
        """Remove the tray icon — call on app shutdown so the icon
        doesn't linger as a ghost in the tray."""
        if self._tray is None:
            return
        self._tray.hide()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Single-left-click or double-click on the tray icon
        restores the main window.  Right-click is reserved for the
        context menu (Qt handles that path itself)."""
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._restore_window()

    def _restore_window(self) -> None:
        """Bring the window back from minimised / hidden state and
        raise it to the front."""
        win = self._window
        win.showNormal()
        win.raise_()
        win.activateWindow()
