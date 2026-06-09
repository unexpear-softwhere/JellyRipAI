"""Dismissal-path pins: the title-bar ✕ must behave like Cancel.

Qt routes the title-bar ✕ (and Esc, by default) through
``QDialog.reject()``.  Two dialogs did their cleanup only in explicit
button handlers, so ✕ skipped it:

* Settings: the per-tab ``cancel()`` revert never ran — a previewed
  theme (or tray/log-color toggle) stayed live at runtime while cfg
  kept the old values ("my theme keeps resetting" on next launch).
* MKV preview: the media source was never released — the file handle
  stayed open for the rest of the session, blocking temp-file
  deletion on Windows.

Both dialogs now override ``reject()`` so every dismissal converges
on the cleanup; these tests call ``reject()`` directly (exactly what
the ✕ triggers).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

pytest.importorskip("pytestqt")


def test_settings_titlebar_close_runs_every_tab_cancel(qtbot):
    from gui_qt.settings.dialog import SettingsDialog
    from gui_qt.themes import theme_ids

    dlg = SettingsDialog(
        cfg={},
        list_themes=lambda: list(theme_ids()),
        load_theme=lambda _name: None,
    )
    qtbot.addWidget(dlg)

    expected = sum(
        1 for i in range(dlg._tabs.count())
        if hasattr(dlg._tabs.widget(i), "cancel")
    )
    assert expected > 0, "settings dialog should have cancellable tabs"

    cancelled: list[str] = []
    for i in range(dlg._tabs.count()):
        tab = dlg._tabs.widget(i)
        if hasattr(tab, "cancel"):
            tab.cancel = (
                lambda name=type(tab).__name__: cancelled.append(name)
            )

    dlg.reject()  # what the title-bar ✕ triggers

    assert len(cancelled) == expected, (
        f"reject() ran cancel() on {len(cancelled)}/{expected} tabs — "
        f"the ✕ path must revert every tab"
    )


def test_preview_titlebar_close_releases_media_handle(qtbot, tmp_path):
    from PySide6.QtCore import QUrl

    from gui_qt.preview_widget import PreviewDialog

    d = PreviewDialog(mkv_path="")
    qtbot.addWidget(d)
    d.player.setSource(QUrl.fromLocalFile(str(tmp_path / "x.mkv")))
    assert not d.player.source().isEmpty()

    d.reject()  # what the title-bar ✕ triggers

    assert d.player.source().isEmpty(), (
        "✕-close must release the media source or the MKV stays "
        "locked for the rest of the session"
    )
