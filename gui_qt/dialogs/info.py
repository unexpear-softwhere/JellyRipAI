"""Info / error dialogs.

Thin wrappers over ``QMessageBox.information`` / ``QMessageBox.critical``
to match the tkinter ``messagebox.showinfo`` / ``messagebox.showerror``
contract that the controller has been calling.

Callers that want recovery-guidance error formatting should pass the
output of ``ui.dialogs.friendly_error`` as the ``message`` — that
helper is toolkit-agnostic and adds the WCAG 3.3.3 recovery
suggestion text.  The dialog itself stays simple.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QMessageBox

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


def show_info(
    parent: "QWidget | None",
    title: str,
    message: str,
) -> None:
    """Show an informational message box.  Blocks until the user
    dismisses it."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle(title or "Info")
    box.setText(message)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()


def show_error(
    parent: "QWidget | None",
    title: str,
    message: str,
) -> None:
    """Show an error message box.  Blocks until dismissed.

    For recovery-guidance text, callers wrap the message body via
    ``ui.dialogs.friendly_error`` before calling this — the helper
    is shared with the tkinter path so both render the same body.
    """
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle(title or "Error")
    box.setText(message)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()
