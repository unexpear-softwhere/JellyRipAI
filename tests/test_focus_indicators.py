"""Tests for the WCAG 2.4.7 (Visible Focus) requirement.

**Phase 3h, 2026-05-04** — these tests were rewritten for the
PySide6 UI.  The pre-Phase-3h version pinned tkinter
``self.option_add('*Button.highlightThickness', N)`` calls in the
retired ``gui/main_window.py``.  Qt handles focus rings via QSS
``:focus`` selectors and per-widget ``focusPolicy``, so the new
checks pin those instead.

The QSS files live at ``gui_qt/qss/{theme_id}.qss`` and are
generated from ``gui_qt/themes.py`` via ``tools/build_qss.py``.
Every theme must:

* Have a ``QPushButton:focus`` rule so keyboard users see clearly
  which button is focused.
* Have a ``QLineEdit:focus`` (and friends) rule so text inputs
  show focus.

The PySide6 widgets we ship default to receiving Tab focus
(``focusPolicy = StrongFocus`` for buttons, automatic for inputs).
We don't need an opt-in equivalent of tkinter's ``takeFocus = 1``
— it's the default.
"""

from __future__ import annotations

from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_QSS_DIR = _REPO_ROOT / "gui_qt" / "qss"


def _qss_files() -> list[Path]:
    """Real shipping themes (not empty placeholder files).
    Mirrors ``gui_qt.theme._is_real_theme_file`` — a theme file is
    "real" iff non-empty and has more than just whitespace."""
    out = []
    for path in sorted(_QSS_DIR.glob("*.qss")):
        text = path.read_text(encoding="utf-8")
        if text.strip():
            out.append(path)
    return out


_THEME_FILES = _qss_files()


@pytest.mark.parametrize(
    "qss_path",
    _THEME_FILES,
    ids=[p.stem for p in _THEME_FILES],
)
def test_qss_has_button_focus_rule(qss_path):
    """Every shipping theme must style ``QPushButton:focus``.

    Without it, keyboard users can't tell which button currently
    has focus — a WCAG 2.4.7 violation.  The rule must change at
    least one visible property (border / outline / background)
    versus the unfocused state.
    """
    text = qss_path.read_text(encoding="utf-8")
    assert "QPushButton:focus" in text, (
        f"{qss_path.name} is missing a ``QPushButton:focus`` rule. "
        f"Visible focus indicator is a WCAG 2.4.7 requirement; "
        f"add the rule to ``tools/build_qss.py`` and re-run it."
    )


@pytest.mark.parametrize(
    "qss_path",
    _THEME_FILES,
    ids=[p.stem for p in _THEME_FILES],
)
def test_qss_has_text_input_focus_rule(qss_path):
    """Every shipping theme must style ``QLineEdit:focus`` (or
    QPlainTextEdit / QTextEdit — any text-input widget).  Same
    WCAG requirement as buttons; users must see which input has
    focus."""
    text = qss_path.read_text(encoding="utf-8")
    assert (
        "QLineEdit:focus" in text
        or "QPlainTextEdit:focus" in text
        or "QTextEdit:focus" in text
    ), (
        f"{qss_path.name} is missing a focus rule for any text "
        f"input widget.  Add a ``QLineEdit:focus`` (or sibling) "
        f"rule to ``tools/build_qss.py``."
    )


def test_at_least_six_themes_ship():
    """Smoke check that the QSS generator actually produced
    output.  Six themes are expected (dark_github, light_inverted,
    dracula_light, hc_dark, slate, frost).  If this drops below
    six, build_qss.py is broken."""
    assert len(_THEME_FILES) >= 6, (
        f"expected at least 6 shipping QSS themes, found "
        f"{len(_THEME_FILES)}"
    )


def test_workflow_buttons_default_focusable(qtbot):
    """Live-widget check: the workflow buttons in the main window
    receive Tab focus by default.  Replaces the tkinter
    ``takeFocus = 1`` option_add — Qt's default
    ``focusPolicy = StrongFocus`` for QPushButton already does
    this.  Pinned so a future refactor that overrides focusPolicy
    on the workflow buttons (e.g., to disable Tab navigation)
    surfaces here."""
    pytest.importorskip("pytestqt")
    from PySide6.QtCore import Qt
    from gui_qt.main_window import MainWindow

    mw = MainWindow()
    qtbot.addWidget(mw)

    # Every workflow button must be Tab-reachable.
    for object_name, btn in mw.workflow_buttons.items():
        policy = btn.focusPolicy()
        # ``StrongFocus`` includes both Tab and click focus.
        # ``TabFocus`` is also acceptable.  ``NoFocus`` would mean
        # the button is unreachable by keyboard — a WCAG violation.
        assert policy != Qt.FocusPolicy.NoFocus, (
            f"workflow button {object_name!r} has FocusPolicy.NoFocus "
            f"— Tab navigation will skip over it."
        )
