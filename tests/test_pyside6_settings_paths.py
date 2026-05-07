"""Tests for the Paths settings tab (added 2026-05-05 on AI BRANCH).

Pins that:

* The tab exists and shows one row per documented cfg key
  (makemkvcon, ffprobe, ffmpeg, handbrake, temp_folder, tv_folder,
  movies_folder).
* Every row is a QLineEdit + Browse button pair, with the QLineEdit
  pre-filled from cfg.
* ``apply()`` writes every QLineEdit's value back into cfg AND calls
  ``save_cfg`` (best-effort).
* ``cancel()`` resets the QLineEdit values back to the snapshot so
  re-opening Settings doesn't show the abandoned edits.
* Empty input is allowed (saves empty string so the engine resolver
  falls through to registry / known-locations / bundled binary).
* The Settings dialog hosts both Appearance + Paths tabs, in that
  order.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QLineEdit, QPushButton

from gui_qt.settings.tab_paths import PathsTab, _PATH_ROWS


@pytest.fixture
def cfg() -> dict:
    return {
        "makemkvcon_path": r"C:\Program Files (x86)\MakeMKV\makemkvcon.exe",
        "ffprobe_path": r"C:\tools\ffprobe.exe",
        "ffmpeg_path": "",
        "handbrake_path": "",
        "temp_folder": r"C:\Users\me\AppData\Local\Temp",
        "tv_folder": r"D:\TV Shows",
        "movies_folder": r"D:\Movies",
    }


# ─── Construction ──────────────────────────────────────────────────


def test_paths_tab_renders_one_row_per_documented_key(qtbot, cfg):
    tab = PathsTab(cfg=cfg)
    qtbot.addWidget(tab)

    # Every documented key has a QLineEdit pre-filled from cfg.
    for key, _label, _kind, _filter in _PATH_ROWS:
        edit = tab.findChild(QLineEdit, f"settingsPath_{key}")
        assert edit is not None, f"missing QLineEdit for {key!r}"
        assert edit.text() == str(cfg.get(key, "") or "")


def test_paths_tab_has_browse_button_per_row(qtbot, cfg):
    tab = PathsTab(cfg=cfg)
    qtbot.addWidget(tab)
    for key, _label, _kind, _filter in _PATH_ROWS:
        btn = tab.findChild(QPushButton, f"settingsPathBrowse_{key}")
        assert btn is not None, f"missing Browse button for {key!r}"
        assert btn.text() == "Browse…"


def test_paths_tab_handles_missing_cfg_keys(qtbot):
    """If cfg doesn't have a path key (fresh install), the field
    just renders empty rather than crashing."""
    tab = PathsTab(cfg={})
    qtbot.addWidget(tab)
    for key, _label, _kind, _filter in _PATH_ROWS:
        edit = tab.findChild(QLineEdit, f"settingsPath_{key}")
        assert edit is not None
        assert edit.text() == ""


# ─── apply() / cancel() ────────────────────────────────────────────


def test_apply_writes_every_edit_back_to_cfg(qtbot, cfg):
    tab = PathsTab(cfg=cfg)
    qtbot.addWidget(tab)

    # User types new paths in.
    tab._edits["makemkvcon_path"].setText(r"C:\new\makemkvcon.exe")
    tab._edits["temp_folder"].setText(r"E:\rip-temp")
    tab._edits["movies_folder"].setText("")  # cleared

    tab.apply()

    assert cfg["makemkvcon_path"] == r"C:\new\makemkvcon.exe"
    assert cfg["temp_folder"] == r"E:\rip-temp"
    assert cfg["movies_folder"] == ""
    # Untouched fields should keep their snapshot value.
    assert cfg["ffprobe_path"] == r"C:\tools\ffprobe.exe"


def test_apply_calls_save_cfg(qtbot, cfg):
    saved: list[dict] = []

    def fake_save(c):
        saved.append(dict(c))

    tab = PathsTab(cfg=cfg, save_cfg=fake_save)
    qtbot.addWidget(tab)
    tab._edits["ffmpeg_path"].setText(r"C:\ffmpeg.exe")
    tab.apply()

    assert len(saved) == 1
    assert saved[0]["ffmpeg_path"] == r"C:\ffmpeg.exe"


def test_apply_strips_whitespace(qtbot, cfg):
    """Whitespace-only input collapses to empty so the engine
    resolver falls through to defaults."""
    tab = PathsTab(cfg=cfg)
    qtbot.addWidget(tab)
    tab._edits["ffmpeg_path"].setText("   \t  ")

    tab.apply()
    assert cfg["ffmpeg_path"] == ""


def test_apply_save_failure_does_not_break(qtbot, cfg):
    """If save_cfg raises, the cfg mutation already applied — apply
    must not propagate the exception."""
    def boom(_c):
        raise OSError("disk full")

    tab = PathsTab(cfg=cfg, save_cfg=boom)
    qtbot.addWidget(tab)
    tab._edits["temp_folder"].setText(r"E:\new")

    # Should NOT raise.
    tab.apply()
    assert cfg["temp_folder"] == r"E:\new"


def test_cancel_resets_edits_to_snapshot(qtbot, cfg):
    """User edits then cancels — line edits revert to the original
    cfg values (not the user's edits) and cfg stays clean."""
    tab = PathsTab(cfg=cfg)
    qtbot.addWidget(tab)
    original = cfg["makemkvcon_path"]
    tab._edits["makemkvcon_path"].setText(r"C:\junk.exe")

    tab.cancel()

    # Field reset to snapshot.
    assert tab._edits["makemkvcon_path"].text() == original
    # cfg untouched (cancel never writes).
    assert cfg["makemkvcon_path"] == original


# ─── Dialog integration ────────────────────────────────────────────


def test_settings_dialog_includes_paths_tab(qtbot, cfg):
    """Paths tab is reachable from the Settings dialog.  Tab order
    is pinned more strictly by ``test_pyside6_settings_tabs.py``;
    here we just confirm Paths exists."""
    from gui_qt.settings.dialog import SettingsDialog
    from gui_qt.themes import theme_ids

    dlg = SettingsDialog(
        cfg=cfg,
        list_themes=lambda: list(theme_ids()),
        load_theme=lambda _name: None,
    )
    qtbot.addWidget(dlg)

    tabs = dlg._tabs
    tab_titles = [tabs.tabText(i) for i in range(tabs.count())]
    assert "Paths" in tab_titles
    assert "Appearance" in tab_titles


def test_settings_dialog_ok_applies_paths_tab(qtbot, cfg):
    """OK on the Settings dialog must apply BOTH tabs — the
    Appearance changes AND the Paths changes — not just whichever
    tab is currently visible."""
    from gui_qt.settings.dialog import SettingsDialog
    from gui_qt.themes import theme_ids

    saved: list[dict] = []
    dlg = SettingsDialog(
        cfg=cfg,
        list_themes=lambda: list(theme_ids()),
        load_theme=lambda _name: None,
        save_cfg=lambda c: saved.append(dict(c)),
    )
    qtbot.addWidget(dlg)

    # Edit the Paths tab without ever switching to it.
    dlg.paths_tab._edits["temp_folder"].setText(r"E:\via-ok")
    # Click OK programmatically.
    dlg._on_ok()

    assert cfg["temp_folder"] == r"E:\via-ok"
    # Should have at least one save_cfg call (Paths tab fires it;
    # AppearanceTab may also fire its own).
    assert any(c["temp_folder"] == r"E:\via-ok" for c in saved)


def test_settings_dialog_cancel_reverts_paths_tab(qtbot, cfg):
    """Cancel on the Settings dialog must reset the Paths tab's
    edits without writing them to cfg."""
    from gui_qt.settings.dialog import SettingsDialog
    from gui_qt.themes import theme_ids

    dlg = SettingsDialog(
        cfg=cfg,
        list_themes=lambda: list(theme_ids()),
        load_theme=lambda _name: None,
    )
    qtbot.addWidget(dlg)
    original = cfg["temp_folder"]

    dlg.paths_tab._edits["temp_folder"].setText(r"E:\not-saved")
    dlg._on_cancel()

    # cfg untouched.
    assert cfg["temp_folder"] == original
