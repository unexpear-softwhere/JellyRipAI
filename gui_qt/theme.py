"""Theme loader for the PySide6 GUI.

Per migration plan decision #7 (equipable theme system from day 1),
themes live as separate QSS files under ``gui_qt/qss/`` and are
applied to the running ``QApplication`` at startup or via the
in-app theme picker (sub-phase 3d).

This module is small on purpose: list available themes, load one
by name, raise on unknown names.  Theme **content** lives in the QSS
files themselves; the QSS files are generated from
``gui_qt/themes.py`` via ``tools/build_qss.py`` (rerun the script
when tokens change).

**Empty / 0-byte ``.qss`` files are treated as placeholders** and
filtered out — they can't actually theme anything, so surfacing them
in the picker or letting them silently render an unstyled window
would be misleading.  This is how stale placeholders (like the
deprecated ``warm.qss``) get sidelined without breaking the loader.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication


THEME_DIR = Path(__file__).parent / "qss"


def _is_real_theme_file(path: Path) -> bool:
    """A ``.qss`` file counts as a real theme only if it has content.

    Zero-byte files are placeholders left over from scaffolding (or
    from deprecated themes pending deletion); they shouldn't appear
    in the picker because applying them would just clear the
    stylesheet, which is indistinguishable from "no theme loaded".
    """
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def list_themes() -> list[str]:
    """Return available theme names (without the .qss extension),
    sorted for stable order in pickers.

    Filters out empty ``.qss`` placeholder files — see
    ``_is_real_theme_file``.
    """
    return sorted(
        p.stem for p in THEME_DIR.glob("*.qss") if _is_real_theme_file(p)
    )


def load_theme(app: "QApplication", theme_name: str) -> None:
    """Apply the named QSS theme to the running ``QApplication``.

    Raises ``FileNotFoundError`` if the theme doesn't exist, is an
    empty placeholder, or can't be read (locked, permission-denied,
    or non-UTF-8 binary content).  Callers can surface a clear error
    rather than silently rendering a stylesheet-less window or — worse
    — crashing startup before the main window appears.

    Note: a non-empty file with **invalid QSS syntax** is NOT detected
    here; Qt's ``setStyleSheet`` swallows parse errors and degrades to
    the default look.  That's outside this function's contract.
    """
    qss_path = THEME_DIR / f"{theme_name}.qss"
    if not _is_real_theme_file(qss_path):
        raise FileNotFoundError(
            f"Theme not found: {theme_name!r} "
            f"(searched: {qss_path}). "
            f"Available: {', '.join(list_themes()) or '<none>'}"
        )
    try:
        qss_text = qss_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise FileNotFoundError(
            f"Theme {theme_name!r} could not be read ({type(exc).__name__}: "
            f"{exc}).  The file may be locked, permission-denied, or "
            f"corrupted.  Available: {', '.join(list_themes()) or '<none>'}"
        ) from exc
    app.setStyleSheet(qss_text)
