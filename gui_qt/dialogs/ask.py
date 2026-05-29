"""Inline confirmation + text input prompts.

* ``ask_yesno`` is a thin wrapper over ``QMessageBox.question``
  matching ``ui.dialogs.ask_yes_no``'s tkinter contract.
* ``ask_input`` is a wrapper over ``QInputDialog.getText`` matching
  the tkinter ``ask_input`` method's contract: returns the entered
  string, an empty string on Skip-with-empty-input, or ``None`` on
  Cancel.

Both dialogs assume GUI-thread invocation; cross-thread marshaling
is the caller's responsibility (see ``gui_qt/dialogs/__init__.py``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QInputDialog, QMessageBox

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


def ask_yesno(
    parent: "QWidget | None",
    prompt: str,
    *,
    title: str = "Confirm",
) -> bool:
    """Yes/No confirmation dialog.  Returns ``True`` on Yes,
    ``False`` on No or Esc / window close.

    Built as an instance (not the static ``QMessageBox.question``)
    so it can be window-modal — the static convenience methods are
    application-modal, which would freeze the standalone AI chat
    window.  Parented to the main window by the caller, so the
    workflow still blocks until answered."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(title or "Confirm")
    box.setText(prompt)
    box.setStandardButtons(
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    )
    box.setDefaultButton(QMessageBox.StandardButton.No)  # less destructive
    box.setWindowModality(Qt.WindowModality.WindowModal)
    result = box.exec()
    return result == QMessageBox.StandardButton.Yes


def ask_input(
    parent: "QWidget | None",
    label: str,
    prompt: str,
    default: str = "",
) -> str | None:
    """Text input dialog.

    Returns the entered text, an empty string if the user clicked OK
    with an empty field, or ``None`` if they cancelled.

    Mirrors the tkinter contract: ``label`` is the dialog window
    title (matches tk's behavior), ``prompt`` is the inline
    instruction text, ``default`` pre-fills the field.
    """
    # Built as an instance (not the static ``QInputDialog.getText``)
    # so it can be window-modal — the static method is application-
    # modal and would freeze the standalone AI chat window.
    dlg = QInputDialog(parent)
    dlg.setWindowTitle(label or "Input")
    dlg.setLabelText(prompt)
    dlg.setTextValue(default or "")
    dlg.setWindowModality(Qt.WindowModality.WindowModal)
    accepted = dlg.exec()
    if not accepted:
        return None
    return dlg.textValue()
