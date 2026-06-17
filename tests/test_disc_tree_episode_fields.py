"""Per-title episode-number & name fields in the disc picker (2026-06-13).

The picker grid carries an editable "Ep #" and "Episode name" column
for TV discs, so numbering/naming happen once — right next to each
title's duration/size — instead of via blind comma-separated prompts
after the rip ("give info per title … each title has its own field …
all of it in a window like the identify step").  These pin:

- TV rows pre-fill a 1..N episode-number guess in title-number order.
- ``episode_numbers()`` reads the (possibly edited) cells, keyed by
  title id; a blank / non-numeric cell is omitted (an "extra").
- Movies hide both columns, so both reader maps come back empty.
- The name column keeps working independently of the number column.

Complements ``test_disc_tree_preview_controls.py`` (watch controls).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

pytest.importorskip("pytestqt")

from gui_qt.dialogs.disc_tree import (  # noqa: E402
    _COL_NAME,
    _COL_NUM,
    _DiscTreeDialog,
)


def _title(tid: int, name: str = "X") -> dict:
    return {
        "id": tid,
        "name": name,
        "duration": "0:22:00",
        "size": "600 MB",
        "chapters": 5,
        "recommended": False,
        "classification": "",
    }


def test_tv_episode_numbers_blank_until_typed(qtbot):
    """No auto-fill (2026-06-13): TV rows start with an empty Ep # — you
    type each number yourself, and a row left blank stays an extra."""
    d = _DiscTreeDialog(
        [_title(2), _title(0), _title(5)], is_tv=True, preview_callback=None,
    )
    qtbot.addWidget(d)
    assert d.episode_numbers() == {}


def test_episode_numbers_reads_edited_cells(qtbot):
    """Editing the Ep # cell changes what ``episode_numbers()`` reports;
    a cleared cell drops out (that title is treated as an extra)."""
    d = _DiscTreeDialog(
        [_title(0), _title(1), _title(2)], is_tv=True, preview_callback=None,
    )
    qtbot.addWidget(d)
    # Scrambled, like a disc whose play order != broadcast order.
    d._items_by_id["0"].setText(_COL_NUM, "4")
    d._items_by_id["1"].setText(_COL_NUM, "3")
    d._items_by_id["2"].setText(_COL_NUM, "")  # extra → omitted
    assert d.episode_numbers() == {0: 4, 1: 3}


def test_movie_hides_episode_columns(qtbot):
    """Movies name by title/year — both per-title columns are hidden and
    both reader maps come back empty."""
    d = _DiscTreeDialog([_title(0)], is_tv=False, preview_callback=None)
    qtbot.addWidget(d)
    assert d._tree.isColumnHidden(_COL_NUM)
    assert d._tree.isColumnHidden(_COL_NAME)
    assert d.episode_numbers() == {}
    assert d.episode_names() == {}


def test_name_and_number_columns_are_independent(qtbot):
    """The name column works alongside the number column; with no
    auto-fill, a row you only named (no number) reports no number."""
    d = _DiscTreeDialog(
        [_title(0), _title(1)], is_tv=True, preview_callback=None,
    )
    qtbot.addWidget(d)
    d._items_by_id["0"].setText(_COL_NAME, "Pilot")  # named, not numbered
    d._items_by_id["1"].setText(_COL_NUM, "9")
    assert d.episode_names() == {0: "Pilot"}
    assert d.episode_numbers() == {1: 9}


def test_picker_cell_clipboard_helpers(qtbot):
    """Right-click Cut/Copy/Paste on an editable cell moves text via the
    clipboard (whole-cell), collapsing any pasted newlines to spaces."""
    from PySide6.QtWidgets import QApplication

    d = _DiscTreeDialog([_title(0), _title(1)], is_tv=True, preview_callback=None)
    qtbot.addWidget(d)
    a = d._items_by_id["0"]
    b = d._items_by_id["1"]
    a.setText(_COL_NAME, "Pilot")

    d._cell_copy(a, _COL_NAME)
    assert QApplication.clipboard().text() == "Pilot"

    d._cell_paste(b, _COL_NAME)
    assert b.text(_COL_NAME) == "Pilot"

    d._cell_cut(a, _COL_NAME)
    assert a.text(_COL_NAME) == ""
    assert QApplication.clipboard().text() == "Pilot"

    QApplication.clipboard().setText("Line1\nLine2")
    d._cell_paste(b, _COL_NAME)
    assert b.text(_COL_NAME) == "Line1 Line2"


def test_single_click_opens_editor_on_editable_cell(qtbot, monkeypatch):
    """A single click on an Ep # / Episode name cell opens the inline
    editor — so you don't have to discover the double-click that the
    tester couldn't (2026-06-14)."""
    d = _DiscTreeDialog([_title(0)], is_tv=True, preview_callback=None)
    qtbot.addWidget(d)
    opened = []
    monkeypatch.setattr(d._tree, "editItem", lambda it, col: opened.append(col))
    item = d._items_by_id["0"]
    d._on_item_clicked(item, _COL_NUM)
    d._on_item_clicked(item, _COL_NAME)
    assert opened == [_COL_NUM, _COL_NAME]
