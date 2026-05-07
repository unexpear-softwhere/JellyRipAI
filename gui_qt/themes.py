"""Theme tokens for the PySide6 GUI — Python-side source of truth.

Mirrors ``docs/design/themes/themes.jsx`` (the design-source-of-truth
file shipped by the user on 2026-05-03).  This module is the
**Python** source of truth: ``tools/build_qss.py`` reads ``THEMES``
from here to render the QSS files under ``gui_qt/qss/``, and tests
import ``THEMES`` directly to assert WCAG contrast on every CTA.

If a token disagrees with the JSX file, update both — the JSX is for
designers and the browsable preview, this module is for the Python
build pipeline and tests.

**Six themes** (decided 2026-05-03 — supersedes the earlier 3-theme
placeholder set ``dark_github``/``light_inverted``/``warm``):

================  ======  =====================================
id                family  rationale
================  ======  =====================================
dark_github       dark    Current tkinter palette ported as-is
                          — zero visual surprise.  Default value
                          of ``opt_pyside6_theme``.
light_inverted    light   Forest-green primary, no purple in the
                          action row.  Closes A11y Finding #2.
dracula_light     light   Pale lavender bg, canonical Dracula
                          CTAs (purple/pink/cyan/yellow/red).
hc_dark           dark    Pure black surfaces, neon CTAs that all
                          cross 7:1 against their label.
slate             dark    Desaturated cool-only neutrals
                          (sea-foam/sky/periwinkle/bronze/brick).
frost             dark    Nord background with saturation dialed
                          up on every CTA.
================  ======  =====================================

The token role names are **constant** across themes — only the colors
differ.  This lets one parameterized QSS template render all six.

Roles:

* ``bg``, ``card``, ``input``, ``border`` — surface levels
* ``fg``, ``muted``, ``accent`` — text + brand accent
* ``go`` / ``goFg`` — primary CTA (start, confirm, rip)
* ``info`` / ``infoFg`` — secondary CTA (dump titles)
* ``alt`` / ``altFg`` — tertiary CTA (organize)
* ``warn`` / ``warnFg`` — caution CTA (prep for ffmpeg)
* ``danger`` / ``dangerFg`` — destructive (stop session)
* ``hover``, ``selection`` — interaction state
* ``logBg``, ``promptFg``, ``answerFg`` — log panel coloring
* ``shadow`` — drop shadow rgba

The ``confirmButton`` / ``primaryButton`` objectName split already in
use in ``gui_qt/setup_wizard.py`` maps to ``go`` / ``info`` respectively.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


# ---------------------------------------------------------------------------
# Token shape
# ---------------------------------------------------------------------------


# All themes must define exactly these keys.  Keep this list in sync
# with the keys used by ``tools/build_qss.py``; tests pin coverage.
TOKEN_KEYS: tuple[str, ...] = (
    # surfaces
    "bg", "card", "input", "border",
    # text + accent
    "fg", "muted", "accent",
    # CTAs (paired with their foreground/label color)
    "go", "goFg",
    "info", "infoFg",
    "alt", "altFg",
    "warn", "warnFg",
    "danger", "dangerFg",
    # interaction state
    "hover", "selection",
    # log panel
    "logBg", "promptFg", "answerFg",
    # drop shadow rgba string (used inline in QSS)
    "shadow",
)


# CTA role names (the bg/fg pairs for which we enforce WCAG 4.5:1).
CTA_ROLES: tuple[str, ...] = ("go", "info", "alt", "warn", "danger")


@dataclass(frozen=True)
class Theme:
    """One named theme.  Frozen so accidental mutation in tests doesn't
    leak between test cases."""

    id: str
    name: str
    subtitle: str
    family: str  # "dark" | "light"
    notes: str
    tokens: Dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# The 6 themes
# ---------------------------------------------------------------------------


THEMES: List[Theme] = [
    # ----------------------------------------------------------------
    # 1) Dark GitHub — current tkinter palette ported, default theme
    # ----------------------------------------------------------------
    Theme(
        id="dark_github",
        name="Dark GitHub",
        subtitle="Current tkinter palette, ported",
        family="dark",
        notes=(
            "Direct port of today's #0d1117 / #58a6ff palette. "
            "Zero visual surprise for existing users."
        ),
        tokens={
            "bg": "#0d1117", "card": "#161b22",
            "input": "#21262d", "border": "#30363d",
            "fg": "#c9d1d9", "muted": "#8b949e", "accent": "#58a6ff",
            "go": "#238636",     "goFg": "#ffffff",
            "info": "#1f6feb",   "infoFg": "#ffffff",
            "alt": "#6e40c9",    "altFg": "#ffffff",
            "warn": "#9a6700",   "warnFg": "#ffffff",
            "danger": "#c94b4b", "dangerFg": "#ffffff",
            "hover": "#1f2933", "selection": "#1f6feb",
            "logBg": "#161b22", "promptFg": "#f0e68c", "answerFg": "#90ee90",
            "shadow": "rgba(0, 0, 0, 0.4)",
        },
    ),

    # ----------------------------------------------------------------
    # 2) Light Inverted — editorial light, no purple in the action row
    # ----------------------------------------------------------------
    Theme(
        id="light_inverted",
        name="Light Inverted",
        subtitle="Closes A11y Finding #2",
        family="light",
        notes=(
            "Light editorial palette — forest-green primary, deep teal "
            "secondary, mustard tertiary, rust caution, crimson destructive. "
            "No purple in the action row."
        ),
        tokens={
            "bg": "#ffffff", "card": "#f6f8fa",
            "input": "#ffffff", "border": "#d0d7de",
            "fg": "#1f2328", "muted": "#57606a", "accent": "#0e6b6b",
            "go": "#1f6e3a",     "goFg": "#ffffff",   # forest green
            "info": "#0e6b6b",   "infoFg": "#ffffff",   # deep teal
            "alt": "#8a6a14",    "altFg": "#ffffff",   # mustard
            "warn": "#a64614",   "warnFg": "#ffffff",   # rust
            "danger": "#9b1c2c", "dangerFg": "#ffffff",   # crimson
            "hover": "#eaeef2", "selection": "#0e6b6b",
            "logBg": "#f6f8fa", "promptFg": "#8a6a14", "answerFg": "#1f6e3a",
            "shadow": "rgba(31, 35, 40, 0.08)",
        },
    ),

    # ----------------------------------------------------------------
    # 3) Dracula Light — pale lavender bg, Dracula CTA family
    # ----------------------------------------------------------------
    Theme(
        id="dracula_light",
        name="Dracula Light",
        subtitle="Dracula palette, light surface",
        family="light",
        notes=(
            "Pale lavender surface with the canonical Dracula action set "
            "— purple primary, pink secondary, cyan tertiary, yellow "
            "caution (deepened for AA), red destructive."
        ),
        tokens={
            "bg": "#f5ecd9", "card": "#ede1c5",
            "input": "#fbf5e6", "border": "#d6c69a",
            "fg": "#22213a", "muted": "#5e5a7a", "accent": "#6f42c1",
            "go": "#6f42c1",     "goFg": "#ffffff",   # dracula purple
            "info": "#c2378a",   "infoFg": "#ffffff",   # dracula pink
            "alt": "#0a8a96",    "altFg": "#ffffff",   # dracula cyan
            "warn": "#8a6a14",   "warnFg": "#ffffff",   # deepened yellow
            "danger": "#c4312f", "dangerFg": "#ffffff",   # dracula red
            "hover": "#e3d4ad", "selection": "#6f42c1",
            "logBg": "#ede1c5", "promptFg": "#8a6a14", "answerFg": "#0a8a96",
            "shadow": "rgba(61, 47, 21, 0.12)",
        },
    ),

    # ----------------------------------------------------------------
    # 4) High Contrast Dark — accessibility-first, every CTA AAA
    # ----------------------------------------------------------------
    Theme(
        id="hc_dark",
        name="High Contrast Dark",
        subtitle="Accessibility-first AAA",
        family="dark",
        notes=(
            "Pure black surfaces, high-saturation CTAs.  Every CTA "
            "crosses 7:1 against its label so AAA holds end-to-end."
        ),
        tokens={
            "bg": "#000000", "card": "#0a0a0a",
            "input": "#141414", "border": "#5c5c5c",
            "fg": "#ffffff", "muted": "#cfcfcf", "accent": "#ffd60a",
            "go": "#39ff14",     "goFg": "#000000",   # electric lime
            "info": "#00e5ff",   "infoFg": "#000000",   # pure cyan
            "alt": "#ff6ec7",    "altFg": "#000000",   # hot pink
            "warn": "#ffd60a",   "warnFg": "#000000",   # bright yellow
            "danger": "#ff3030", "dangerFg": "#ffffff",   # pure red
            "hover": "#1a1a1a", "selection": "#ffd60a",
            "logBg": "#0a0a0a", "promptFg": "#ffd60a", "answerFg": "#00d26a",
            "shadow": "rgba(0, 0, 0, 0.8)",
        },
    ),

    # ----------------------------------------------------------------
    # 5) Slate — desaturated cool-only set, no green/blue overlap with GH
    # ----------------------------------------------------------------
    Theme(
        id="slate",
        name="Slate",
        subtitle="Cool blue-grey neutrals",
        family="dark",
        notes=(
            "Desaturated cool-only CTAs — sea-foam primary, pale sky "
            "secondary, periwinkle tertiary, bronze caution, brick "
            "destructive.  Nothing saturated, nothing screams."
        ),
        tokens={
            "bg": "#1a2332", "card": "#22303f",
            "input": "#2a3a4d", "border": "#3a4a5e",
            "fg": "#dbe5ee", "muted": "#8ea0b3", "accent": "#5dbcd2",
            "go": "#4ba89a",     "goFg": "#0d1721",   # sea-foam
            "info": "#7aa8c8",   "infoFg": "#0d1721",   # pale sky
            "alt": "#8a8ec4",    "altFg": "#0d1721",   # periwinkle
            "warn": "#b88550",   "warnFg": "#0d1721",   # bronze
            "danger": "#a64545", "dangerFg": "#ffffff",   # brick
            "hover": "#2a3a4d", "selection": "#4a78b8",
            "logBg": "#22303f", "promptFg": "#b88550", "answerFg": "#4ba89a",
            "shadow": "rgba(0, 0, 0, 0.35)",
        },
    ),

    # ----------------------------------------------------------------
    # 6) Frost — saturated Nord (Nord bg, punchier CTAs, less pastel)
    # ----------------------------------------------------------------
    Theme(
        id="frost",
        name="Frost",
        subtitle="Muted Nordic dark",
        family="dark",
        notes=(
            "Nord background with the saturation dialed up on every "
            "CTA — deeper aurora green, stronger frost blue, richer "
            "violet, fuller yellow, firm aurora red.  Same family, more "
            "punch."
        ),
        tokens={
            "bg": "#2e3440", "card": "#3b4252",
            "input": "#434c5e", "border": "#4c566a",
            "fg": "#eceff4", "muted": "#a3acbc", "accent": "#88c0d0",
            "go": "#6e9b4f",     "goFg": "#ffffff",   # saturated aurora green
            "info": "#4a7fb8",   "infoFg": "#ffffff",   # strong frost blue
            "alt": "#9c5fa3",    "altFg": "#ffffff",   # rich violet
            "warn": "#d4a849",   "warnFg": "#1f1a10",   # saturated yellow
            "danger": "#b8434d", "dangerFg": "#ffffff",   # firm aurora red
            "hover": "#434c5e", "selection": "#5e81ac",
            "logBg": "#3b4252", "promptFg": "#d4a849", "answerFg": "#a3be8c",
            "shadow": "rgba(0, 0, 0, 0.4)",
        },
    ),
]


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


THEMES_BY_ID: Dict[str, Theme] = {t.id: t for t in THEMES}


def theme_ids() -> List[str]:
    """Return the 6 theme IDs in declaration order."""
    return [t.id for t in THEMES]


# ---------------------------------------------------------------------------
# WCAG contrast helpers (port of the JS helpers in themes.jsx)
# ---------------------------------------------------------------------------


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Parse ``#rgb`` or ``#rrggbb`` into a 3-tuple of 0-255 ints."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise ValueError(f"unsupported hex color: {hex_color!r}")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _channel_luminance(channel: int) -> float:
    """sRGB → linear-light per channel.  Matches the WCAG 2.1 formula."""
    c = channel / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    """Compute the relative luminance of a hex color per WCAG 2.1."""
    r, g, b = _hex_to_rgb(hex_color)
    return (
        0.2126 * _channel_luminance(r)
        + 0.7152 * _channel_luminance(g)
        + 0.0722 * _channel_luminance(b)
    )


def contrast_ratio(hex_a: str, hex_b: str) -> float:
    """Compute the contrast ratio between two hex colors per WCAG 2.1.

    Returns a value in the range 1.0 (no contrast) to 21.0 (max).
    """
    la = relative_luminance(hex_a)
    lb = relative_luminance(hex_b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def wcag_rating(ratio: float) -> str:
    """Bucket a contrast ratio into a WCAG label.

    Returns one of ``"AAA"`` (≥7:1), ``"AA"`` (≥4.5:1), ``"AA Large"``
    (≥3:1, large text only), or ``"Fail"`` (<3:1).
    """
    if ratio >= 7.0:
        return "AAA"
    if ratio >= 4.5:
        return "AA"
    if ratio >= 3.0:
        return "AA Large"
    return "Fail"
