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
    ``False`` on No or Esc / window close."""
    answer = QMessageBox.question(
        parent,
        title or "Confirm",
        prompt,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,  # default = No (less destructive)
    )
    return answer == QMessageBox.StandardButton.Yes


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
    text, ok = QInputDialog.getText(
        parent,
        label or "Input",
        prompt,
        text=default or "",
    )
    if not ok:
        return None
    return text
