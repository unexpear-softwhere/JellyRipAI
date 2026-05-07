"""Status bar widget for the PySide6 GUI.

Replaces the tkinter status frame in ``gui/main_window.py`` (a label
+ ``ttk.Progressbar`` packed in a row) with a Qt-native
``QStatusBar`` containing a ``QLabel`` and ``QProgressBar``.

Public API the controller calls:

* ``set_status(text, role=None)`` — set the visible status text.  When
  ``role`` is ``None``, it's classified automatically from the message
  via ``gui_qt.formatters.status_role_for_message``.  The role becomes
  the label's ``objectName`` so the active QSS theme can color it.
* ``set_progress(current, total)`` — update the progress bar.  Pass
  ``total=0`` to switch to indeterminate (busy) mode.
* ``reset()`` — clear progress, set status to "Ready".

The colors come entirely from the active QSS theme — Python only
sets the ``role`` and the QSS files target the resulting objectNames
(``statusReady``, ``statusError``, ``statusWarn``, ``statusBusy``).

For determinate progress, the bar shows a percent.  For
indeterminate, ``QProgressBar`` is set to range ``(0, 0)`` which Qt
renders as the busy / "marquee" pattern.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QWidget,
)

from gui_qt.formatters import status_role_for_message

if TYPE_CHECKING:
    from gui_qt.formatters import StatusRole


# Map role → objectName for the status label.  Used by the QSS theme
# to color the label per state.  Keep these names in sync with any
# QSS rules that target them (currently the build_qss.py template
# doesn't emit these — Phase 3c-i shell session will add them when
# wiring the status bar into the main window).
_ROLE_OBJECT_NAMES: dict["StatusRole", str] = {
    "ready": "statusReady",
    "error": "statusError",
    "warn":  "statusWarn",
    "busy":  "statusBusy",
}


def _humanize_bytes(n: int) -> str:
    """Format ``n`` bytes as a short human-friendly string.  Picks
    the unit so the value lands in the 0.1–999 range — mirrors what
    ``shared/runtime.format_bytes_short`` does, but kept local to
    avoid a cross-package dependency from the status bar.
    """
    if n < 1_000_000:
        return f"{n / 1_000:.0f} KB"
    if n < 1_000_000_000:
        return f"{n / 1_000_000:.1f} MB"
    return f"{n / 1_000_000_000:.1f} GB"


class StatusBar(QWidget):
    """Status bar — text label + progress bar in a horizontal row.

    Implemented as a plain ``QWidget`` rather than ``QStatusBar``
    because the main window will embed this above the log pane (not
    as the conventional bottom bar).  Same widget shape as the
    tkinter ``status_frame``.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statusBar")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        self._label = QLabel("Ready")
        self._label.setObjectName("statusReady")  # initial role
        layout.addWidget(self._label, stretch=1)

        self._progress = QProgressBar()
        self._progress.setObjectName("statusProgress")
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        # Default format — bare percent.  When ``set_progress`` gets
        # byte hints, the format is rewritten to "X.X GB / Y.Y GB · NN%"
        # for that update only; the next update without bytes reverts
        # back to this default.
        self._default_format = "%p%"
        self._progress.setFormat(self._default_format)
        self._progress.setFixedWidth(220)  # widened from 180 for the byte-format text
        layout.addWidget(self._progress)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_status(
        self,
        text: str,
        role: "StatusRole | None" = None,
    ) -> None:
        """Set the status text and classify into a UI role.

        ``role`` is normally inferred from the message via
        ``status_role_for_message``.  Callers can pass an explicit
        role to override (e.g., the controller wants to force a
        message into the warn lane regardless of its content).
        """
        self._label.setText(text)
        chosen_role = role or status_role_for_message(text)
        self._label.setObjectName(_ROLE_OBJECT_NAMES[chosen_role])
        # Qt only re-applies QSS to a widget when its objectName
        # changes if we ask for a style refresh.
        self._label.style().unpolish(self._label)
        self._label.style().polish(self._label)

    def set_progress(
        self,
        current: int,
        total: int,
        *,
        current_bytes: int | None = None,
        total_bytes: int | None = None,
    ) -> None:
        """Update the progress bar.

        * ``total > 0`` → determinate mode: bar shows ``current/total``
          as a percent.
        * ``total == 0`` → indeterminate mode: bar renders the
          marquee animation.  Useful for "scanning" or "waiting" when
          we can't compute a percentage.

        When both ``current_bytes`` and ``total_bytes`` are given (and
        positive), the bar's text format switches from "NN%" to
        "X.X GB / Y.Y GB · NN%" for this update.  Useful during a
        MakeMKV rip where the controller knows the byte total from
        the scan and can compute current bytes from the percent.
        Subsequent calls without byte hints revert to the bare
        percent format.

        Negative values clamp to zero — defensive against off-by-one
        from the controller.
        """
        current = max(0, current)
        if total <= 0:
            self._progress.setRange(0, 0)  # busy / indeterminate
            self._progress.setFormat(self._default_format)
            return
        self._progress.setRange(0, total)
        self._progress.setValue(min(current, total))

        if current_bytes is not None and total_bytes is not None and total_bytes > 0:
            self._progress.setFormat(
                f"{_humanize_bytes(max(0, current_bytes))} / "
                f"{_humanize_bytes(total_bytes)} · %p%"
            )
        else:
            self._progress.setFormat(self._default_format)

    def reset(self) -> None:
        """Clear progress, return to Ready state.  Called by the
        controller between workflow runs."""
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFormat(self._default_format)
        self.set_status("Ready")

    # ------------------------------------------------------------------
    # Read-only test hooks
    # ------------------------------------------------------------------

    @property
    def label_text(self) -> str:
        """Current label text — for tests."""
        return self._label.text()

    @property
    def label_role(self) -> str:
        """Current label objectName (statusReady / statusError /
        statusWarn / statusBusy) — for tests and theming drift
        guards."""
        return self._label.objectName()

    @property
    def progress_value(self) -> int:
        return self._progress.value()

    @property
    def progress_maximum(self) -> int:
        return self._progress.maximum()

    @property
    def progress_format(self) -> str:
        """Current format string applied to the progress bar — for
        tests asserting the byte-level vs percent-only modes."""
        return self._progress.format()
