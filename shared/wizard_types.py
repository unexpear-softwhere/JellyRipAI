"""Shared types and pure helpers used by the rip-flow setup wizard.

These were originally defined in ``gui/setup_wizard.py`` (the tkinter
implementation) and re-exported by ``gui_qt/setup_wizard.py`` for the
PySide6 port.  Phase 3h (tkinter retirement, 2026-05-04) lifted them
to this neutral home so the dataclasses, constants, and pure helpers
have no GUI-toolkit coupling.

Imported by:

* ``gui_qt/setup_wizard.py`` — the Qt wizard (production)
* ``gui_qt/dialogs/`` — dialog modules that consume ``ContentSelection``
  / ``ExtrasAssignment`` / ``OutputPlan``
* ``gui_qt/workflow_launchers.py``
* ``controller/*`` — wherever the controller passes wizard results
* ``tests/test_pyside6_setup_wizard_*.py`` — Qt wizard tests
* ``tests/test_*`` — behavior-first tests that exercise the
  controller's interaction with these types

Nothing in here imports tkinter, PySide6, or any GUI module.  Pure
Python + standard library only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Display strings for classification labels
# ---------------------------------------------------------------------------

# The internal ``ct.label`` values stay uppercase (they're enum-style code
# constants), but user-facing rendering uses title case per Finding #4 in
# docs/ux-copy-and-accessibility-plan.md — ALL-CAPS reads as "this is a
# code constant" rather than "this is a category."
_LABEL_DISPLAY = {
    "MAIN":      "Main",
    "DUPLICATE": "Duplicate",
    "EXTRA":     "Extra",
    "UNKNOWN":   "Unknown",
}


def _label_display(label: str) -> str:
    """Return the user-facing display string for a classification label.

    Falls back to ``.capitalize()`` if a new label gets added without a
    matching entry — defensive default that keeps rendering reasonable
    rather than silently shipping ALL-CAPS again.
    """
    return _LABEL_DISPLAY.get(label, label.capitalize())


# ---------------------------------------------------------------------------
# Jellyfin extras categories
# ---------------------------------------------------------------------------

# Per https://jellyfin.org/docs/general/server/media/movies/#extras
JELLYFIN_EXTRAS_CATEGORIES = [
    "Extras",
    "Behind The Scenes",
    "Deleted Scenes",
    "Featurettes",
    "Interviews",
    "Trailers",
    "Other",
]


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ContentSelection:
    """Result of Step 3 — which titles to rip and their roles."""
    main_title_ids: list[int] = field(default_factory=list)
    extra_title_ids: list[int] = field(default_factory=list)
    skip_title_ids: list[int] = field(default_factory=list)


@dataclass
class ExtrasAssignment:
    """Result of Step 4 — maps title IDs to Jellyfin extras categories."""
    assignments: dict[int, str] = field(default_factory=dict)


@dataclass
class OutputPlan:
    """Result of Step 5 — the planned folder structure."""
    base_folder: str = ""
    main_file_label: str = ""
    extras: dict[str, list[str]] = field(default_factory=dict)
    confirmed: bool = False


# ---------------------------------------------------------------------------
# Pure formatting helpers
# ---------------------------------------------------------------------------

def _format_duration(seconds: float) -> str:
    """Render a duration in seconds as a short ``Xh YYm`` or ``Mm`` string.

    Returns ``"?"`` if the input is non-positive (used as a fallback when
    MakeMKV's metadata is missing).
    """
    if seconds <= 0:
        return "?"
    h = int(seconds) // 3600
    m = (int(seconds) % 3600) // 60
    if h > 0:
        return f"{h}h {m:02d}m"
    return f"{m}m"


def _format_size(size_bytes: float) -> str:
    """Render a byte count as ``X.X GB`` or ``Y MB``.

    Returns ``"?"`` if the input is non-positive.
    """
    if size_bytes <= 0:
        return "?"
    gb = size_bytes / (1024 ** 3)
    if gb >= 1.0:
        return f"{gb:.1f} GB"
    return f"{size_bytes / (1024 ** 2):.0f} MB"


# ---------------------------------------------------------------------------
# Output-plan tree builder
# ---------------------------------------------------------------------------

def build_output_tree(
    base_folder: str,
    main_label: str,
    extras_map: dict[str, list[str]],
) -> list[str]:
    """Build a flat list of lines representing the output folder tree.

    Example output::

        Movies/
          Inception (2010)/
            Inception (2010).mkv
            Behind The Scenes/
              Making Of.mkv
            Featurettes/
              Dream Explained.mkv
    """
    root_name = os.path.basename(base_folder)
    parent_name = os.path.basename(os.path.dirname(base_folder))
    lines: list[str] = []
    lines.append(f"{parent_name}/")
    lines.append(f"  {root_name}/")
    lines.append(f"    {main_label}")

    for category in sorted(extras_map.keys()):
        files = extras_map[category]
        if not files:
            continue
        lines.append(f"    {category}/")
        for f in files:
            lines.append(f"      {f}")

    return lines


__all__ = [
    "ContentSelection",
    "ExtrasAssignment",
    "OutputPlan",
    "JELLYFIN_EXTRAS_CATEGORIES",
    "build_output_tree",
    "_format_duration",
    "_format_size",
    "_label_display",
    "_LABEL_DISPLAY",
]
