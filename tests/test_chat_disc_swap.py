"""Disc-swap reconciliation in the chat controller.

When a tester swaps discs between rips without re-scanning, the engine's
``last_classification`` stays on the previously-scanned disc, so the chat
facts described the OLD disc (a tester hit exactly this: the assistant
kept naming ABOMINABLE after Tom & Jerry was inserted).
``ChatController._reconcile_inserted_disc`` drops the stale disc/title
facts when the scanned disc's label is absent from the live drive-bar
text, and adds a context note instead.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

pytest.importorskip("PySide6")

from gui_qt.chat_controller import ChatController


def _drive_bar(label: str) -> str:
    # Mirrors gui_qt.formatters.format_drive_label's shape.
    return (
        f"Drive 0: BD-RE BUFFALO | Disc: ◉ {label} "
        "| Path: F: | State: ready (2)"
    )


def test_drops_stale_disc_facts_when_disc_swapped():
    facts = {
        "state": "idle",
        "disc": {"disc_title": "ABOMINABLE", "volume_id": "ABOMINABLE"},
        "titles": [{"id": 2, "label": "MAIN"}],
        "drive": {"disc_name": "ABOMINABLE"},
        "scan_issue_summary": {"scsi_error_count": 5},
        "session": {"prior": "x"},
        "drive_bar": _drive_bar("TOM_AND_JERRY"),
    }
    out = ChatController._reconcile_inserted_disc(facts)
    # Stale scanned-disc facts are gone.
    for key in ("disc", "titles", "drive", "scan_issue_summary", "session"):
        assert key not in out, key
    # A note explains the swap, naming both discs.
    note = out.get("disc_context_note", "")
    assert "ABOMINABLE" in note
    assert "TOM_AND_JERRY" in note
    # Non-disc facts + the live drive bar survive.
    assert out["state"] == "idle"
    assert out["drive_bar"] == facts["drive_bar"]


def test_keeps_facts_when_same_disc_inserted():
    facts = {
        "disc": {"disc_title": "ABOMINABLE"},
        "titles": [{"id": 2}],
        "drive_bar": _drive_bar("ABOMINABLE"),
    }
    out = ChatController._reconcile_inserted_disc(facts)
    assert out["disc"] == {"disc_title": "ABOMINABLE"}
    assert out["titles"] == [{"id": 2}]
    assert "disc_context_note" not in out


def test_noop_without_drive_bar_signal():
    # No drive-bar text → can't tell if swapped → leave facts untouched.
    facts = {"disc": {"disc_title": "ABOMINABLE"}, "titles": [{"id": 2}]}
    out = ChatController._reconcile_inserted_disc(facts)
    assert out == facts


def test_noop_without_scanned_disc():
    # Drive bar present but nothing scanned yet → nothing to reconcile.
    facts = {"drive_bar": _drive_bar("TOM_AND_JERRY"), "state": "idle"}
    out = ChatController._reconcile_inserted_disc(facts)
    assert out == facts
