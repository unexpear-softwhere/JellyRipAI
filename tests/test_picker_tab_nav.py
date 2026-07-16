"""Disc-picker keyboard numbering: Tab moves editing down the Ep#
column (Shift+Tab up), so episode numbers can be typed straight down
the list.  Regressed when the Audio column's cell widgets broke Qt's
default tab-editing; restored with explicit navigation.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _tv_titles(n=4):
    return [
        {"id": i, "name": f"Title {i + 1}", "duration": "0:22:00",
         "size": "0.9 GB", "chapters": 6}
        for i in range(n)
    ]


def test_tab_moves_editing_down_the_number_column(qtbot):
    from gui_qt.dialogs.disc_tree import _COL_NUM, _DiscTreeDialog

    dlg = _DiscTreeDialog(_tv_titles(4), True)  # TV → Ep# column shown
    qtbot.addWidget(dlg)

    dlg._on_editor_tab(0, _COL_NUM, True)  # Tab from row 0
    assert dlg._tree.indexOfTopLevelItem(dlg._tree.currentItem()) == 1
    assert dlg._tree.currentColumn() == _COL_NUM

    dlg._on_editor_tab(1, _COL_NUM, False)  # Shift+Tab from row 1
    assert dlg._tree.indexOfTopLevelItem(dlg._tree.currentItem()) == 0


def test_tab_clamps_at_the_ends(qtbot):
    from gui_qt.dialogs.disc_tree import _COL_NUM, _DiscTreeDialog

    dlg = _DiscTreeDialog(_tv_titles(3), True)
    qtbot.addWidget(dlg)
    dlg._tree.setCurrentItem(dlg._tree.topLevelItem(2), _COL_NUM)

    dlg._on_editor_tab(2, _COL_NUM, True)  # Tab past the last row → no move
    assert dlg._tree.indexOfTopLevelItem(dlg._tree.currentItem()) == 2


def test_delegate_tab_emits_navigate_signal(qtbot):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QLineEdit

    from gui_qt.dialogs.disc_tree import _COL_NUM, _EditableColumnsDelegate

    d = _EditableColumnsDelegate()
    d._editing_row = 2
    d._editing_col = _COL_NUM
    editor = QLineEdit()
    qtbot.addWidget(editor)
    got: list = []
    d.tab_navigate.connect(lambda r, c, f: got.append((r, c, f)))

    tab = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier
    )
    assert d.eventFilter(editor, tab) is True  # consumes the Tab
    assert got == [(2, _COL_NUM, True)]

    got.clear()
    backtab = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Backtab,
        Qt.KeyboardModifier.NoModifier,
    )
    d.eventFilter(editor, backtab)
    assert got == [(2, _COL_NUM, False)]  # Shift+Tab → backward


def test_audio_combo_is_not_tab_focusable(qtbot):
    """The audio dropdowns must be click-focus only so they don't snag
    Tab navigation between the Ep#/name cells."""
    from PySide6.QtCore import Qt

    from gui_qt.dialogs.disc_tree import _DiscTreeDialog

    titles = [{
        "id": 0, "name": "Title 1", "duration": "0:22:00",
        "size": "0.9 GB", "chapters": 6,
        "audio_tracks": [{"lang": "eng", "lang_name": "English"}],
    }]
    dlg = _DiscTreeDialog(titles, True)
    qtbot.addWidget(dlg)
    combo = next(iter(dlg._audio_combos.values()))
    assert combo.focusPolicy() == Qt.FocusPolicy.ClickFocus
