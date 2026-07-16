"""Per-title audio-track selection.

The disc picker shows, per title, a multi-select dropdown of the
scanned audio tracks (all kept by default; uncheck to drop).  For any
title the user trims, the ripped file is remuxed down to the kept
tracks with ffmpeg (video / subtitles / chapters preserved).  Titles
left at "keep all" are untouched, so default rips are unchanged.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── engine: keep-map bookkeeping ────────────────────────────────────────
def test_set_rip_audio_keep_cleans_dedups_and_replaces():
    from engine.ripper_engine import RipperEngine

    eng = RipperEngine({})
    assert eng._rip_audio_keep == {}  # default: keep everything

    n = eng.set_rip_audio_keep({0: [1, 0, 0], "2": [3], 4: []})
    assert n == 3
    assert eng._rip_audio_keep == {0: [0, 1], 2: [3], 4: []}

    # A later disc REPLACES (never merges) — an empty map clears it.
    assert eng.set_rip_audio_keep({}) == 0
    assert eng._rip_audio_keep == {}


# ── engine: the ffmpeg remux ────────────────────────────────────────────
def _engine_with_fakes(monkeypatch, audio_count):
    from engine.ripper_engine import RipperEngine

    eng = RipperEngine({})
    monkeypatch.setattr(eng, "_get_ffmpeg", lambda: "ffmpeg.exe")
    monkeypatch.setattr(
        eng, "_count_audio_streams", lambda _p: audio_count
    )
    return eng


def test_strip_audio_tracks_maps_kept_and_replaces(tmp_path, monkeypatch):
    import engine.ripper_engine as re_mod

    eng = _engine_with_fakes(monkeypatch, audio_count=3)
    f = tmp_path / "title.mkv"
    f.write_bytes(b"x" * 4096)

    captured: dict = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        with open(cmd[-1], "wb") as out:  # ffmpeg writes the temp
            out.write(b"y" * 4096)
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(re_mod.subprocess, "run", fake_run)

    assert eng.strip_audio_tracks(str(f), [0, 2]) is True
    cmd = captured["cmd"]
    assert "0:a:0" in cmd and "0:a:2" in cmd
    assert "0:a:1" not in cmd            # the dropped track
    assert "0:v?" in cmd and "0:s?" in cmd and "-map_chapters" in cmd
    assert f.read_bytes() == b"y" * 4096  # replaced by the trimmed temp
    assert not os.path.exists(str(f) + ".audiotrim.mkv")  # temp cleaned


def test_strip_skips_when_all_tracks_kept(tmp_path, monkeypatch):
    import engine.ripper_engine as re_mod

    eng = _engine_with_fakes(monkeypatch, audio_count=2)
    f = tmp_path / "title.mkv"
    f.write_bytes(b"x" * 4096)

    def boom(*a, **k):
        raise AssertionError("ffmpeg must not run when keeping all tracks")

    monkeypatch.setattr(re_mod.subprocess, "run", boom)
    assert eng.strip_audio_tracks(str(f), [0, 1]) is False
    assert f.read_bytes() == b"x" * 4096  # untouched


def test_strip_keeps_original_when_ffmpeg_fails(tmp_path, monkeypatch):
    import engine.ripper_engine as re_mod

    eng = _engine_with_fakes(monkeypatch, audio_count=3)
    f = tmp_path / "title.mkv"
    f.write_bytes(b"original" * 512)

    def fail_run(cmd, **kw):  # non-zero, no output
        class R:
            returncode = 1
        return R()

    monkeypatch.setattr(re_mod.subprocess, "run", fail_run)
    assert eng.strip_audio_tracks(str(f), [0]) is False
    assert f.read_bytes() == b"original" * 512  # good rip preserved
    assert not os.path.exists(str(f) + ".audiotrim.mkv")


def test_strip_no_ffmpeg_is_safe(tmp_path, monkeypatch):
    from engine.ripper_engine import RipperEngine

    eng = RipperEngine({})
    monkeypatch.setattr(eng, "_get_ffmpeg", lambda: "")
    f = tmp_path / "t.mkv"
    f.write_bytes(b"x" * 100)
    assert eng.strip_audio_tracks(str(f), [0]) is False


# ── rip_ops: only trimmed titles get remuxed ────────────────────────────
def test_maybe_strip_only_trims_mapped_titles():
    from engine.rip_ops import _maybe_strip_title_audio

    calls: list = []

    class FakeEngine:
        _rip_audio_keep = {5: [0]}

        def strip_audio_tracks(self, f, keep, on_log=None):
            calls.append((f, keep))
            return True

    eng = FakeEngine()
    _maybe_strip_title_audio(eng, 5, ["a.mkv"], None)   # trimmed
    _maybe_strip_title_audio(eng, 9, ["b.mkv"], None)   # keep-all → skip
    assert calls == [("a.mkv", [0])]


# ── dialog: the per-title checkable dropdown ────────────────────────────
def test_audio_track_label():
    from gui_qt.dialogs.disc_tree import _audio_track_label

    assert (
        _audio_track_label(
            {"lang_name": "English", "codec": "AC3", "channels": "6"}, 0
        )
        == "English · AC3 · 6ch"
    )
    assert _audio_track_label({"lang": "spa"}, 1) == "spa"
    assert _audio_track_label({}, 2) == "Track 3"


def test_audio_combo_defaults_all_checked(qtbot):
    from gui_qt.dialogs.disc_tree import _AudioTracksCombo

    combo = _AudioTracksCombo([
        {"lang": "eng", "lang_name": "English", "codec": "AC3", "channels": "6"},
        {"lang": "spa", "lang_name": "Spanish", "codec": "AC3", "channels": "2"},
    ])
    qtbot.addWidget(combo)
    assert combo.total_tracks == 2
    assert combo.checked_indices() == [0, 1]
    assert combo.lineEdit().text() == "All audio (2)"


def test_audio_combo_uncheck_updates_indices_and_summary(qtbot):
    from gui_qt.dialogs.disc_tree import _AudioTracksCombo

    combo = _AudioTracksCombo([
        {"lang": "eng", "lang_name": "English"},
        {"lang": "spa", "lang_name": "Spanish"},
        {"lang": "fra", "lang_name": "French"},
    ])
    qtbot.addWidget(combo)
    combo.set_checked_indices_for_test([0])
    assert combo.checked_indices() == [0]
    assert combo.lineEdit().text() == "English"

    combo.set_checked_indices_for_test([0, 2])
    assert combo.checked_indices() == [0, 2]
    assert combo.lineEdit().text() == "2 of 3 tracks"

    combo.set_checked_indices_for_test([])
    assert combo.checked_indices() == []
    assert combo.lineEdit().text() == "No audio"


def _titles_with_audio():
    return [
        {"id": 0, "name": "T1", "duration": "0:22:00", "size": "0.9 GB",
         "chapters": 6, "audio_tracks": [
             {"lang": "eng", "lang_name": "English"},
             {"lang": "spa", "lang_name": "Spanish"}]},
        {"id": 1, "name": "T2", "duration": "0:22:00", "size": "0.9 GB",
         "chapters": 6, "audio_tracks": [
             {"lang": "eng", "lang_name": "English"}]},
    ]


def test_dialog_builds_combo_per_title_with_audio(qtbot):
    from gui_qt.dialogs.disc_tree import _DiscTreeDialog

    dlg = _DiscTreeDialog(_titles_with_audio(), True)
    qtbot.addWidget(dlg)
    assert set(dlg._audio_combos.keys()) == {"0", "1"}
    # Nothing trimmed by default.
    assert dlg.audio_selections() == {}


def test_dialog_reports_only_trimmed_titles(qtbot):
    from gui_qt.dialogs.disc_tree import _DiscTreeDialog

    dlg = _DiscTreeDialog(_titles_with_audio(), True)
    qtbot.addWidget(dlg)
    # Trim title 0 to English only; leave title 1 at keep-all.
    dlg._audio_combos["0"].set_checked_indices_for_test([0])

    assert dlg.audio_selections() == {0: [0]}
    dlg._on_ok()
    assert dlg.audio_selections_value == {0: [0]}


def test_audio_combo_opens_dropdown_on_box_click(qtbot):
    """A click anywhere on the (read-only, editable) box must open the
    popup — otherwise an editable combo only opens from the tiny arrow
    and reads as 'the dropdown doesn't drop down'."""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    from gui_qt.dialogs.disc_tree import _AudioTracksCombo

    combo = _AudioTracksCombo([
        {"lang": "eng", "lang_name": "English"},
        {"lang": "spa", "lang_name": "Spanish"},
    ])
    qtbot.addWidget(combo)
    calls = {"show": 0, "hide": 0}
    combo.showPopup = lambda: calls.__setitem__("show", calls["show"] + 1)
    combo.hidePopup = lambda: calls.__setitem__("hide", calls["hide"] + 1)

    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease, QPointF(5, 5),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    assert combo.eventFilter(combo.lineEdit(), release) is True
    assert calls["show"] == 1  # box click opened it


def test_dialog_no_audio_tracks_gets_no_combo(qtbot):
    from gui_qt.dialogs.disc_tree import _DiscTreeDialog

    dlg = _DiscTreeDialog(
        [{"id": 0, "name": "T", "duration": "0:22", "size": "0.9 GB",
          "chapters": 6}],
        True,
    )
    qtbot.addWidget(dlg)
    assert dlg._audio_combos == {}
    assert dlg.audio_selections() == {}
