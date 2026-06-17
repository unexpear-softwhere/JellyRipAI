"""Disc-tree picker watch controls — watch-before-rip (2026-06-12).

The picker has a "Watch in VLC" button: the FULL selected title rips
to a disposable local-temp file, plays, and is deleted when the
player closes.  There is deliberately NO sample-length control —
partial-title rips proved unreliable on protected discs ("it cant do
parts of titles reliably", 2026-06-12), so watching always means the
whole title.  These tests pin:

- The button appears only when a watch callback is wired.
- The button watches the *selected* row; no selection is a no-op.
- Right-click delivers the title id to the callback.
- The callback receives exactly one argument (the title id) — the
  controller's ``preview_title`` reads the full-title default from
  config.

Complements ``test_pyside6_dialogs_disc_tree.py`` (selection/cancel
contract) and ``test_preview_before_rip.py`` (config + API +
cleanup).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

pytest.importorskip("pytestqt")

from gui_qt.dialogs.disc_tree import _DiscTreeDialog


def _title(tid: int, name: str = "X") -> dict:
    return {
        "id": tid,
        "name": name,
        "duration": "0:30:00",
        "size": "1.2 GB",
        "chapters": 5,
        "recommended": False,
        "classification": "",
    }


def test_watch_button_absent_without_callback(qtbot):
    """No callback (bulk-pick paths) → no Watch button."""
    d = _DiscTreeDialog([_title(0)], is_tv=False, preview_callback=None)
    qtbot.addWidget(d)
    assert d._preview_button is None


def test_watch_button_present_with_callback(qtbot):
    d = _DiscTreeDialog(
        [_title(0)], is_tv=False, preview_callback=lambda *a: None,
    )
    qtbot.addWidget(d)
    assert d._preview_button is not None
    assert "Watch in VLC" in d._preview_button.text()


def test_watch_button_watches_selected_row(qtbot):
    """The button invokes the callback with the SELECTED row's title
    ID — and only the ID: full-title watching has no length arg."""
    titles = [_title(3, "A"), _title(8, "B")]
    calls: list[tuple] = []
    d = _DiscTreeDialog(
        titles, is_tv=True,
        preview_callback=lambda *args: calls.append(args),
    )
    qtbot.addWidget(d)
    d._tree.setCurrentItem(d._tree.topLevelItem(1))  # tid=8
    d._on_preview_clicked()
    assert calls == [(8,)]


def test_watch_button_no_selection_is_a_no_op(qtbot):
    """Watch with nothing selected does nothing (no crash, no call)."""
    calls: list = []
    d = _DiscTreeDialog(
        [_title(0)], is_tv=False,
        preview_callback=lambda *a: calls.append(a),
    )
    qtbot.addWidget(d)
    d._tree.setCurrentItem(None)
    d._on_preview_clicked()
    assert calls == []


def test_right_click_invokes_watch_callback(qtbot):
    """Right-clicking a row delivers its title ID to the callback —
    the original preview pathway, now full-title watching."""
    titles = [_title(42)]
    calls: list[int] = []
    d = _DiscTreeDialog(
        titles, is_tv=False, preview_callback=calls.append,
    )
    qtbot.addWidget(d)
    d.show()
    pos = d._tree.visualItemRect(d._tree.topLevelItem(0)).center()
    d._tree.customContextMenuRequested.emit(pos)
    assert calls == [42]
