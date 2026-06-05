"""User-made custom themes: list / load / save / export / import.

A custom theme is a small JSON file under
``%APPDATA%\\JellyRipAI\\themes\\<id>.json``::

    {
      "id": "custom_midnight",
      "name": "Midnight",
      "family": "dark",
      "tokens": { "bg": "#0d1117", "fg": "#e6e8ec", ... }
    }

It is **pure data** — color strings only, no code — so importing a
theme someone shared (GitHub, itch, a Discord file) is safe: we
validate the keys + values on load and reject anything that isn't a
complete, well-formed token set.  The Theme Maker dialog
(``gui_qt/dialogs/theme_maker.py``) is the UI that creates these.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from gui_qt.themes import TOKEN_KEYS
from shared.runtime import get_config_dir

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_ID_RE = re.compile(r"^[a-z0-9_]+$")


def themes_dir() -> Path:
    """The per-profile custom-themes directory (created on demand)."""
    d = Path(get_config_dir()) / "themes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _theme_path(theme_id: str) -> Path:
    return themes_dir() / f"{theme_id}.json"


def slugify(name: str) -> str:
    """Turn a display name into a safe, lowercase theme id."""
    s = re.sub(r"[^a-z0-9]+", "_", str(name or "").strip().lower()).strip("_")
    return f"custom_{s}" if s else "custom_theme"


def validate(data: Any) -> tuple[bool, str]:
    """Return ``(ok, reason)``.  A valid theme is a dict with a
    ``tokens`` mapping covering every ``TOKEN_KEYS`` entry, each a
    ``#RRGGBB`` color (the ``shadow`` token is an ``rgba(...)`` string
    and is exempt from the hex check)."""
    if not isinstance(data, dict):
        return False, "not a theme object"
    tokens = data.get("tokens")
    if not isinstance(tokens, dict):
        return False, "missing 'tokens'"
    missing = [k for k in TOKEN_KEYS if k not in tokens]
    if missing:
        return False, f"missing colors: {', '.join(missing[:6])}"
    for k in TOKEN_KEYS:
        if k == "shadow":
            continue  # rgba(...) string, not a hex swatch
        if not _HEX_RE.match(str(tokens.get(k, ""))):
            return False, f"{k} is not a #RRGGBB color: {tokens.get(k)!r}"
    return True, ""


def list_custom() -> list[dict[str, Any]]:
    """Return all valid custom themes (each with an ``id``)."""
    out: list[dict[str, Any]] = []
    for p in sorted(themes_dir().glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        ok, _ = validate(data)
        if ok:
            data.setdefault("id", p.stem)
            out.append(data)
    return out


def get_custom(theme_id: str) -> dict[str, Any] | None:
    """Return one custom theme by id, or ``None`` if missing/invalid."""
    p = _theme_path(theme_id)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    ok, _ = validate(data)
    if not ok:
        return None
    data.setdefault("id", theme_id)
    return data


def save_custom(theme: dict[str, Any]) -> str:
    """Validate + persist a custom theme; returns its id."""
    theme = dict(theme)
    tid = str(theme.get("id") or "").strip()
    if not _ID_RE.match(tid):
        tid = slugify(theme.get("name", ""))
    theme["id"] = tid
    ok, msg = validate(theme)
    if not ok:
        raise ValueError(msg)
    _theme_path(tid).write_text(
        json.dumps(theme, indent=2), encoding="utf-8"
    )
    return tid


def delete_custom(theme_id: str) -> None:
    p = _theme_path(theme_id)
    if p.is_file():
        p.unlink()


def export_custom(theme: dict[str, Any], dest: str | Path) -> None:
    """Write a theme to an arbitrary path (for sharing)."""
    ok, msg = validate(theme)
    if not ok:
        raise ValueError(msg)
    Path(dest).write_text(json.dumps(theme, indent=2), encoding="utf-8")


def import_theme(src: str | Path) -> dict[str, Any]:
    """Load + validate a shared theme file, then save it as a custom
    theme.  Raises ``ValueError`` with a friendly reason if the file
    isn't a valid theme.  Returns the imported (saved) theme dict."""
    try:
        data = json.loads(Path(src).read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"could not read theme file ({exc})") from exc
    ok, msg = validate(data)
    if not ok:
        raise ValueError(f"not a valid theme file: {msg}")
    if not data.get("name"):
        data["name"] = Path(src).stem
    if not _ID_RE.match(str(data.get("id", ""))):
        data["id"] = slugify(data["name"])
    save_custom(data)
    return data
