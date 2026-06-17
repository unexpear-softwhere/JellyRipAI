"""Small interaction-polish helpers shared across the Qt UI.

Qt Style Sheets can't set the mouse cursor (the ``cursor`` property is
a web-CSS thing Qt ignores), so the pointing-hand cursor on clickable
controls has to be applied in code.  Rather than touch every button at
its construction site, we walk a finished window/dialog once and set the
cursor on every button-like widget under it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractButton, QWidget


def apply_pointing_cursors(root: QWidget) -> None:
    """Give every button under ``root`` the pointing-hand cursor.

    Covers ``QPushButton`` / ``QToolButton`` / ``QCheckBox`` /
    ``QRadioButton`` (all ``QAbstractButton`` subclasses).  Call once
    after a window or dialog has built its widget tree; safe to call
    again if more buttons are added later (idempotent).
    """
    for btn in root.findChildren(QAbstractButton):
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
    if isinstance(root, QAbstractButton):
        root.setCursor(Qt.CursorShape.PointingHandCursor)
