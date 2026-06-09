"""Tests for the custom-theme store + runtime QSS renderer.

Covers the Theme Maker's data layer: validation (only complete,
well-formed color sets are accepted), the save/list/get/delete
round-trip, export→import sharing, and that the runtime renderer turns
a token set into real QSS (and rejects an incomplete one).
"""

from __future__ import annotations

import json

import pytest

from gui_qt import custom_themes
from gui_qt.qss_render import render_qss_from_tokens
from gui_qt.themes import THEMES_BY_ID


@pytest.fixture
def tmp_themes(tmp_path, monkeypatch):
    """Redirect the custom-themes dir to a temp folder (hermetic)."""
    d = tmp_path / "themes"
    d.mkdir()
    monkeypatch.setattr(custom_themes, "themes_dir", lambda: d)
    return d


def _good_theme() -> dict:
    return {
        "name": "Midnight",
        "family": "dark",
        "tokens": dict(THEMES_BY_ID["basic_dark"].tokens),
    }


def test_validate_accepts_complete_theme():
    ok, msg = custom_themes.validate(_good_theme())
    assert ok, msg


def test_validate_rejects_missing_tokens():
    ok, msg = custom_themes.validate({"name": "x", "tokens": {"bg": "#000000"}})
    assert not ok
    assert "missing" in msg


def test_validate_rejects_non_hex():
    t = _good_theme()
    t["tokens"]["fg"] = "red"
    ok, msg = custom_themes.validate(t)
    assert not ok
    assert "fg" in msg


def test_save_list_get_delete_roundtrip(tmp_themes):
    tid = custom_themes.save_custom(_good_theme())
    assert tid == "custom_midnight"
    assert tid in [c["id"] for c in custom_themes.list_custom()]
    got = custom_themes.get_custom(tid)
    assert got is not None
    assert got["name"] == "Midnight"
    custom_themes.delete_custom(tid)
    assert custom_themes.get_custom(tid) is None


def test_export_then_import_roundtrip(tmp_themes, tmp_path):
    dest = tmp_path / "shared.json"
    custom_themes.export_custom(_good_theme(), dest)
    data = json.loads(dest.read_text(encoding="utf-8"))
    assert data["tokens"]["bg"]
    imported = custom_themes.import_theme(dest)
    assert imported["id"] in [c["id"] for c in custom_themes.list_custom()]


def test_import_rejects_non_theme(tmp_themes, tmp_path):
    bad = tmp_path / "notatheme.json"
    bad.write_text('{"hello": "world"}', encoding="utf-8")
    with pytest.raises(ValueError):
        custom_themes.import_theme(bad)


def test_render_from_tokens_produces_qss():
    qss = render_qss_from_tokens(
        THEMES_BY_ID["basic_light"].tokens, id="basic_light", family="light",
    )
    assert "QMainWindow" in qss
    assert len(qss) > 1000


def test_render_from_tokens_rejects_incomplete():
    with pytest.raises(ValueError):
        render_qss_from_tokens({"bg": "#000000"})


def test_default_theme_id_is_a_real_builtin():
    """``DEFAULTS['opt_pyside6_theme']`` must name a shipping theme.
    It pointed at the long-deleted ``dark_github`` for a while —
    masked at runtime by the fallback, but every fresh config
    persisted a dangling id."""
    from shared.runtime import DEFAULTS
    assert DEFAULTS["opt_pyside6_theme"] in THEMES_BY_ID
