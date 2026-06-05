"""Dev tool: (re)generate the built-in themes' committed QSS artifacts.

The QSS template + renderer now live in ``gui_qt/qss_render.py`` so the
running app — and the Theme Maker's live preview — can render any token
set (including user-made themes) at runtime.  This script just writes
the built-in themes to ``gui_qt/qss/{id}.qss`` for diffing / historical
artifacts.  Re-run after editing tokens in ``gui_qt/themes.py``:

    python tools/build_qss.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make ``import gui_qt`` work when running this script directly.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from gui_qt.qss_render import render_qss  # noqa: E402
from gui_qt.themes import THEMES  # noqa: E402


def write_all(qss_dir: Path) -> list[Path]:
    """Render every built-in theme and write ``qss_dir/{id}.qss``."""
    qss_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for theme in THEMES:
        out = qss_dir / f"{theme.id}.qss"
        out.write_text(render_qss(theme), encoding="utf-8")
        written.append(out)
    return written


def main(argv: list[str] | None = None) -> int:
    qss_dir = _REPO_ROOT / "gui_qt" / "qss"
    paths = write_all(qss_dir)
    print(f"Wrote {len(paths)} QSS files under {qss_dir}:")
    for p in paths:
        print(f"  - {p.name}  ({p.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
