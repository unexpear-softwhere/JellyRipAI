"""Duplicate-disc resolution dialog.

Three-way prompt for the duplicate-disc handling workflow.  Returns
one of:

* ``"retry"`` — user wants to swap discs and retry detection
* ``"bypass"`` — user confirms it's *not* a duplicate, proceed with rip
* ``"stop"`` — user wants to abort the workflow

Mirrors ``JellyRipperGUI._ask_duplicate_resolution_modal`` at
``gui/main_window.py:4264``.  Custom button labels are supported
because the tkinter implementation passes per-call labels for
context (e.g., "Swap and Retry" vs. "Swap, Then Continue").

Default actions:

* Enter / default button → Retry (matches tkinter line 4297)
* Esc / window close → Stop
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


# Public type alias for the return value.  Importable by callers
# that want to type-check their handling.
DuplicateResolutionChoice = Literal["retry", "bypass", "stop"]


class _DuplicateResolutionDialog(QDialog):
    def __init__(
        self,
        prompt: str,
        retry_text: str,
        bypass_text: str,
        stop_text: str,
        parent: "QWidget | None" = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("duplicateResolutionDialog")
        self.setWindowTitle("Duplicate Disc Check")
        self.setModal(True)

        # Default = stop (so Esc / close → stop, matching tkinter)
        self.choice: DuplicateResolutionChoice = "stop"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(10)

        # Prompt body — wraps automatically; same role styling as
        # other body labels.
        prompt_label = QLabel(prompt)
        prompt_label.setObjectName("duplicatePrompt")
        prompt_label.setWordWrap(True)
        outer.addWidget(prompt_label)

        # Buttons — Retry (green default) | Bypass (blue) | Stop (red).
        # Three different roles map to confirmButton / primaryButton /
        # cancelButton-style objectNames so QSS picks up theme colors.
        button_row = QHBoxLayout()
        button_row.setSpacing(8)

        retry = QPushButton(retry_text)
        retry.setObjectName("confirmButton")  # green/go role
        retry.setDefault(True)  # Enter triggers retry
        retry.clicked.connect(lambda: self._finish("retry"))
        button_row.addWidget(retry)

        bypass = QPushButton(bypass_text)
        bypass.setObjectName("primaryButton")  # blue/info role
        bypass.clicked.connect(lambda: self._finish("bypass"))
        button_row.addWidget(bypass)

        stop = QPushButton(stop_text)
        stop.setObjectName("dangerProceedButton")  # red/danger role
        stop.clicked.connect(lambda: self._finish("stop"))
        button_row.addWidget(stop)

        outer.addLayout(button_row)

        # Cache for tests
        self._retry_button = retry
        self._bypass_button = bypass
        self._stop_button = stop

    def _finish(self, choice: DuplicateResolutionChoice) -> None:
        self.choice = choice
        if choice == "stop":
            self.reject()
        else:
            self.accept()

    def keyPressEvent(self, event):  # noqa: N802 (Qt convention)
        if event.key() == Qt.Key.Key_Escape:
            self._finish("stop")
            return
        super().keyPressEvent(event)


def ask_duplicate_resolution(
    parent: "QWidget | None",
    prompt: str,
    *,
    retry_text: str = "Swap and Retry",
    bypass_text: str = "Not a Dup",
    stop_text: str = "Stop",
) -> DuplicateResolutionChoice:
    """Show the three-way prompt modally.  Returns the user's
    choice.  Default labels match tkinter's defaults at
    ``gui/main_window.py:4244``."""
    dialog = _DuplicateResolutionDialog(
        prompt=prompt,
        retry_text=retry_text,
        bypass_text=bypass_text,
        stop_text=stop_text,
        parent=parent,
    )
    dialog.exec()
    return dialog.choice
