"""Startup splash screen for the PySide6 path.

Replaces ``main._NullStartupWindow`` (the deliberate no-op installed
when the tkinter splash was retired in the 2026-05-04 v1-blocker
fix) with a real Qt splash.  ``QSplashScreen`` is intentionally
simpler than the tkinter ``_StartupWindow`` it replaces — no
SecureTk wrapper, no Tcl/Tk runtime files, no module-level
``import tkinter``.  The splash and the main window share a single
``QApplication`` instance.

API mirrors what ``main.py`` already calls on the legacy startup
helper:

* ``set_status(message)`` — update the status line, force a paint
  pass so the new text is actually on screen before the next slow
  import.
* ``close()`` — tear down without a window handoff.  Used by the
  ``finally:`` guard in ``main.py`` if ``run_qt_app`` raises before
  the main window exists.
* ``finish_for(window)`` — Qt-native handoff: hides the splash and
  transfers focus to ``window``.  ``run_qt_app`` calls this after
  ``window.show()`` so the splash fades cleanly into the real UI.

If anything in this module raises during construction (no display
in a headless test runner, Qt failing to load on a stripped VM,
etc.), ``main.py``'s call site catches and falls back to the
``_NullStartupWindow`` no-op.  The splash is a UX nicety, not a
correctness requirement.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QSplashScreen, QWidget


# Brand colors — pulled from the ``dark_github`` theme tokens in
# ``gui_qt/themes.py`` so the splash matches the default theme even
# though no QSS has loaded yet.  Hardcoded here (not imported) so
# the splash module stays self-contained — building the splash
# must not pull in the full theme system.
_BG_COLOR     = "#0d1117"
_ACCENT_COLOR = "#58a6ff"
_FG_COLOR     = "#c9d1d9"
_MUTED_COLOR  = "#8b949e"

# Splash dimensions — wide enough for the longest status string we
# emit during startup, tall enough for title + subtitle + status.
_SPLASH_WIDTH  = 480
_SPLASH_HEIGHT = 240


def _build_pixmap(version: str | None = None):
    """Render the splash background as a ``QPixmap``.

    Drawn programmatically rather than loaded from a file so the
    bundle doesn't need to ship an image asset.  Layout:

        +------------------------------+
        |                              |
        |          JellyRip            |   <- accent, large
        |                              |
        |          PySide6             |   <- muted, small
        |                              |
        |  v1.0.20 — Loading...        |   <- placeholder; real
        |                              |       text comes via
        +------------------------------+       showMessage().
    """
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QColor, QFont, QPainter, QPixmap

    pm = QPixmap(_SPLASH_WIDTH, _SPLASH_HEIGHT)
    pm.fill(QColor(_BG_COLOR))

    p = QPainter(pm)
    try:
        # Title — "JellyRip" centered, accent color, large
        title_font = QFont("Segoe UI", 36, QFont.Weight.Bold)
        p.setFont(title_font)
        p.setPen(QColor(_ACCENT_COLOR))
        p.drawText(
            QRect(0, 60, _SPLASH_WIDTH, 60),
            Qt.AlignmentFlag.AlignCenter,
            "JellyRip",
        )

        # Subtitle — "PySide6" small, muted, just below title
        subtitle_font = QFont("Segoe UI", 9)
        subtitle_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 3.0)
        p.setFont(subtitle_font)
        p.setPen(QColor(_MUTED_COLOR))
        p.drawText(
            QRect(0, 125, _SPLASH_WIDTH, 20),
            Qt.AlignmentFlag.AlignCenter,
            "PYSIDE6",
        )

        # Version line — bottom-left, small, muted.  Optional
        # because main.py may not have read the version yet at
        # splash-construction time.
        if version:
            version_font = QFont("Segoe UI", 8)
            p.setFont(version_font)
            p.setPen(QColor(_MUTED_COLOR))
            p.drawText(
                QRect(20, _SPLASH_HEIGHT - 30, _SPLASH_WIDTH - 40, 20),
                Qt.AlignmentFlag.AlignLeft,
                f"v{version}",
            )
    finally:
        # Always end the painter — Qt deadlocks on the next QPainter
        # against the same pixmap if we don't.
        p.end()

    return pm


class JellyRipSplash:
    """Wraps ``QSplashScreen`` to match the legacy startup-window
    API (``set_status`` / ``close``) plus a Qt-native ``finish_for``
    handoff.

    Constructing this object also creates the ``QApplication`` if
    one doesn't exist yet.  ``run_qt_app`` will reuse the same
    instance because it already does
    ``QApplication.instance() or QApplication(sys.argv)``.
    """

    def __init__(self, version: str | None = None) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QApplication, QSplashScreen

        self._app = QApplication.instance() or QApplication(sys.argv)
        self._splash: QSplashScreen = QSplashScreen(_build_pixmap(version))
        # Stay-on-top so the splash isn't buried by other windows
        # the user has open.  Frameless is the QSplashScreen default.
        self._splash.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        # Status-message position — bottom-center, muted color so it
        # doesn't compete with the title.
        self._message_alignment = (
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter
        )
        self._message_color = QColor(_FG_COLOR)
        self._splash.show()
        # processEvents so the splash actually paints before the
        # next slow import call returns.
        self._app.processEvents()

    # ------------------------------------------------------------------
    # API mirroring main.py's legacy startup-window contract
    # ------------------------------------------------------------------

    def set_status(self, message: str) -> None:
        """Update the splash status line and force a paint pass."""
        self._splash.showMessage(
            str(message),
            int(self._message_alignment),
            self._message_color,
        )
        self._app.processEvents()

    def close(self) -> None:
        """Tear down the splash without a main-window handoff.
        Safe to call multiple times — the second call is a no-op
        because Qt's close() is idempotent."""
        self._splash.close()

    # ------------------------------------------------------------------
    # Qt-native handoff
    # ------------------------------------------------------------------

    def finish_for(self, window: "QWidget") -> None:
        """Hide the splash and transfer focus to ``window`` once the
        main window is on screen.  ``run_qt_app`` calls this right
        after ``window.show()`` so the splash fades cleanly into the
        real UI.

        Equivalent to ``self.close()`` if ``window`` is somehow
        ``None`` — defensive against a controller that constructs
        the splash but bails before MainWindow is built.
        """
        if window is None:
            self.close()
            return
        self._splash.finish(window)
