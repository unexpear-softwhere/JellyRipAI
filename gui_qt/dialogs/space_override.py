"""Not-enough-disk-space override dialog.

Shows a warning with required vs. free GB and asks the user whether
to proceed anyway.  Mirrors ``JellyRipperGUI.ask_space_override``
(``gui/main_window.py:5331``).

Returns ``True`` if the user explicitly chose to proceed despite
the warning, ``False`` if they cancelled / hit Esc / closed the
window.  Default is **Cancel** — pinned because proceeding with
insufficient space risks data loss; the user must opt in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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


class _SpaceOverrideDialog(QDialog):
    """Class-encapsulated so tests can construct without running the
    modal event loop."""

    def __init__(
        self,
        required_gb: float,
        free_gb: float,
        parent: "QWidget | None" = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("spaceOverrideDialog")
        self.setWindowTitle("Not Enough Space")
        self.setModal(True)

        self.proceed = False  # default = cancel

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 18)
        outer.setSpacing(8)

        # Headline — warn icon + text.
        headline = QLabel("⚠  NOT ENOUGH DISK SPACE")
        headline.setObjectName("spaceOverrideHeadline")
        headline.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(headline)

        # Body — required / free / consequence.
        body = QLabel(
            f"Required:  {required_gb:.1f} GB\n"
            f"Free:         {free_gb:.1f} GB\n\n"
            f"This may cause the rip to fail\n"
            f"or produce incomplete files."
        )
        body.setObjectName("spaceOverrideBody")
        body.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(body)

        # Buttons — Cancel (default) | Proceed Anyway.
        button_row = QHBoxLayout()
        button_row.setSpacing(10)

        cancel = QPushButton("Cancel")
        cancel.setObjectName("cancelButton")
        cancel.setDefault(True)  # Enter = Cancel — defensive default
        cancel.clicked.connect(self._on_cancel)
        button_row.addWidget(cancel)

        button_row.addStretch(1)

        proceed_btn = QPushButton("Proceed Anyway")
        proceed_btn.setObjectName("dangerProceedButton")
        proceed_btn.clicked.connect(self._on_proceed)
        button_row.addWidget(proceed_btn)

        outer.addLayout(button_row)

        # Cache for tests
        self._cancel_button = cancel
        self._proceed_button = proceed_btn

    def _on_proceed(self) -> None:
        self.proceed = True
        self.accept()

    def _on_cancel(self) -> None:
        self.proceed = False
        self.reject()

    def keyPressEvent(self, event):  # noqa: N802 (Qt convention)
        if event.key() == Qt.Key.Key_Escape:
            self._on_cancel()
            return
        super().keyPressEvent(event)


def ask_space_override(
    parent: "QWidget | None",
    required_gb: float,
    free_gb: float,
) -> bool:
    """Show the warning modally; return ``True`` iff the user opted
    to proceed despite the warning.  Returns ``False`` on Cancel /
    Esc / window close."""
    dialog = _SpaceOverrideDialog(required_gb, free_gb, parent)
    dialog.exec()
    return dialog.proceed
