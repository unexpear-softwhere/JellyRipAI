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
    "hover", "selection", "selectionFg",
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
# Built-in starting themes
#
# Just two clean, high-contrast bases — one dark, one light.  They're
# meant as starting points: users fork and recolor them in the Theme
# Maker (gui_qt/dialogs/theme_maker.py), which saves custom themes as
# JSON under %APPDATA%\\JellyRipAI\\themes\\.
# ---------------------------------------------------------------------------


THEMES: List[Theme] = [
    # ----------------------------------------------------------------
    # Basic Dark — clean neutral dark (proven, high-contrast base)
    # ----------------------------------------------------------------
    Theme(
        id="basic_dark",
        name="Basic Dark",
        subtitle="Clean neutral dark",
        family="dark",
        notes=(
            "A plain, high-contrast dark theme — a starting point to "
            "recolor in the Theme Maker."
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
            "hover": "#1f2933", "selection": "#1f6feb", "selectionFg": "#ffffff",
            "logBg": "#161b22", "promptFg": "#f0e68c", "answerFg": "#90ee90",
            "shadow": "rgba(0, 0, 0, 0.4)",
        },
    ),

    # ----------------------------------------------------------------
    # Basic Light — clean white (high-contrast base)
    # ----------------------------------------------------------------
    Theme(
        id="basic_light",
        name="Basic Light",
        subtitle="Clean white",
        family="light",
        notes=(
            "A plain, high-contrast light theme — a starting point to "
            "recolor in the Theme Maker."
        ),
        tokens={
            "bg": "#ffffff", "card": "#f3f5f7",
            "input": "#ffffff", "border": "#d4d9e0",
            "fg": "#1c2026", "muted": "#5a6470", "accent": "#1565c0",
            "go": "#1f7a3d",     "goFg": "#ffffff",
            "info": "#1565c0",   "infoFg": "#ffffff",
            "alt": "#6a3fbf",    "altFg": "#ffffff",
            "warn": "#8a6300",   "warnFg": "#ffffff",
            "danger": "#b3261e", "dangerFg": "#ffffff",
            "hover": "#e9edf1", "selection": "#1565c0", "selectionFg": "#ffffff",
            "logBg": "#f3f5f7", "promptFg": "#7a5c10", "answerFg": "#1a6b35",
            "shadow": "rgba(20, 24, 31, 0.10)",
        },
    ),

    # ----------------------------------------------------------------
    # Monokai — vivid classic editor palette
    # ----------------------------------------------------------------
    Theme(
        id="monokai",
        name="Monokai",
        subtitle="Vivid classic editor",
        family="dark",
        notes=(
            "Monokai's warm-charcoal surface with vivid CTAs — lime-green "
            "primary, sky-blue secondary, magenta-pink tertiary, orange "
            "caution, red destructive. High energy."
        ),
        tokens={
            "bg": "#272822", "card": "#2d2e27",
            "input": "#3a3b32", "border": "#49483e",
            "fg": "#f8f8f2", "muted": "#a6a28c", "accent": "#66d9ef",
            "go": "#5a9e1f",     "goFg": "#f8f8f2",
            "info": "#2c8fb5",   "infoFg": "#f8f8f2",
            "alt": "#c01c6e",    "altFg": "#f8f8f2",
            "warn": "#b8731a",   "warnFg": "#f8f8f2",
            "danger": "#d12d2d", "dangerFg": "#f8f8f2",
            "hover": "#3a3b32", "selection": "#49483e", "selectionFg": "#ffffff",
            "logBg": "#2d2e27", "promptFg": "#e6db74", "answerFg": "#a6e22e",
            "shadow": "rgba(0, 0, 0, 0.5)",
        },
    ),

    # ----------------------------------------------------------------
    # Rosé Pine — muted soho-vibe, no harsh primaries
    # ----------------------------------------------------------------
    Theme(
        id="rose_pine",
        name="Rosé Pine",
        subtitle="Muted soho-vibe dark",
        family="dark",
        notes=(
            "Rosé Pine's plum-charcoal surface with soft natural CTAs — "
            "pine primary, foam secondary, iris tertiary, gold caution, "
            "love (rose-red) destructive. Calm and muted throughout."
        ),
        tokens={
            "bg": "#191724", "card": "#1f1d2e",
            "input": "#26233a", "border": "#403d52",
            "fg": "#e0def4", "muted": "#908caa", "accent": "#9ccfd8",
            "go": "#3d7068",     "goFg": "#e0def4",
            "info": "#4a8a93",   "infoFg": "#e0def4",
            "alt": "#8479b3",    "altFg": "#e0def4",
            "warn": "#a08431",   "warnFg": "#191724",
            "danger": "#b4637a", "dangerFg": "#e0def4",
            "hover": "#26233a", "selection": "#403d52", "selectionFg": "#ffffff",
            "logBg": "#1f1d2e", "promptFg": "#f6c177", "answerFg": "#9ccfd8",
            "shadow": "rgba(0, 0, 0, 0.45)",
        },
    ),

    # ----------------------------------------------------------------
    # Tokyo Night — cool deep-blue surface, neon-ish CTAs
    # ----------------------------------------------------------------
    Theme(
        id="tokyo_night",
        name="Tokyo Night",
        subtitle="Cool deep-blue night",
        family="dark",
        notes=(
            "Tokyo Night's deep blue-black surface with cool neon CTAs — "
            "blue primary, cyan secondary, purple tertiary, orange caution, "
            "red destructive. Modern and crisp."
        ),
        tokens={
            "bg": "#1a1b26", "card": "#1f2030",
            "input": "#24283b", "border": "#363b54",
            "fg": "#c0caf5", "muted": "#787c99", "accent": "#7aa2f7",
            "go": "#3d59a1",     "goFg": "#c0caf5",
            "info": "#2f7c93",   "infoFg": "#c0caf5",
            "alt": "#7a5cc0",    "altFg": "#c0caf5",
            "warn": "#b3791f",   "warnFg": "#1a1b26",
            "danger": "#c14a5a", "dangerFg": "#c0caf5",
            "hover": "#24283b", "selection": "#363b54", "selectionFg": "#ffffff",
            "logBg": "#1f2030", "promptFg": "#e0af68", "answerFg": "#9ece6a",
            "shadow": "rgba(0, 0, 0, 0.5)",
        },
    ),

    # ----------------------------------------------------------------
    # Catppuccin Mocha — soft pastel surface, gentle CTAs
    # ----------------------------------------------------------------
    Theme(
        id="catppuccin_mocha",
        name="Catppuccin Mocha",
        subtitle="Soft pastel dark",
        family="dark",
        notes=(
            "Catppuccin's cozy mocha surface with pastel CTAs — mauve "
            "primary, sapphire secondary, teal tertiary, peach caution, "
            "red destructive. Low-glare and friendly."
        ),
        tokens={
            "bg": "#1e1e2e", "card": "#181825",
            "input": "#313244", "border": "#45475a",
            "fg": "#cdd6f4", "muted": "#9399b2", "accent": "#cba6f7",
            "go": "#8839ef",     "goFg": "#f5e0dc",
            "info": "#3a6cc9",   "infoFg": "#f5e0dc",
            "alt": "#1a8f8f",    "altFg": "#f5e0dc",
            "warn": "#b06a2c",   "warnFg": "#f5e0dc",
            "danger": "#c4344a", "dangerFg": "#f5e0dc",
            "hover": "#313244", "selection": "#45475a", "selectionFg": "#ffffff",
            "logBg": "#181825", "promptFg": "#f9e2af", "answerFg": "#a6e3a1",
            "shadow": "rgba(0, 0, 0, 0.45)",
        },
    ),

    # ----------------------------------------------------------------
    # Everforest Dark — warm green-grey surface, earthy CTAs
    # ----------------------------------------------------------------
    Theme(
        id="everforest_dark",
        name="Everforest Dark",
        subtitle="Warm forest low-contrast",
        family="dark",
        notes=(
            "Everforest's soft green-grey surface with earthy CTAs — green "
            "primary, aqua secondary, blue tertiary, orange caution, red "
            "destructive. Comfortable for long sessions."
        ),
        tokens={
            "bg": "#2d353b", "card": "#272e33",
            "input": "#374247", "border": "#4a555b",
            "fg": "#d3c6aa", "muted": "#9da9a0", "accent": "#a7c080",
            "go": "#4f7a52",     "goFg": "#fdf6e3",
            "info": "#3a8a82",   "infoFg": "#fdf6e3",
            "alt": "#4d7a99",    "altFg": "#fdf6e3",
            "warn": "#b07a2c",   "warnFg": "#fdf6e3",
            "danger": "#c2433a", "dangerFg": "#fdf6e3",
            "hover": "#374247", "selection": "#4a555b", "selectionFg": "#ffffff",
            "logBg": "#272e33", "promptFg": "#dbbc7f", "answerFg": "#a7c080",
            "shadow": "rgba(0, 0, 0, 0.45)",
        },
    ),

    # ----------------------------------------------------------------
    # Synthwave — retro neon on deep indigo
    # ----------------------------------------------------------------
    Theme(
        id="synthwave",
        name="Synthwave",
        subtitle="Retro neon outrun",
        family="dark",
        notes=(
            "Deep indigo night with retro neon CTAs — magenta primary, cyan "
            "secondary, purple tertiary, amber caution, hot red destructive. "
            "High-energy 80s vibe."
        ),
        tokens={
            "bg": "#1a132f", "card": "#221a3d",
            "input": "#2d2350", "border": "#3f3370",
            "fg": "#f0e6ff", "muted": "#a596c8", "accent": "#ff5dc8",
            "go": "#c81d8e",     "goFg": "#ffffff",
            "info": "#1c8fb0",   "infoFg": "#ffffff",
            "alt": "#7a3fd0",    "altFg": "#ffffff",
            "warn": "#b87a14",   "warnFg": "#1a132f",
            "danger": "#e0344a", "dangerFg": "#ffffff",
            "hover": "#2d2350", "selection": "#3f3370", "selectionFg": "#ffffff",
            "logBg": "#221a3d", "promptFg": "#ffcf4d", "answerFg": "#52e0c4",
            "shadow": "rgba(0, 0, 0, 0.55)",
        },
    ),

    # ----------------------------------------------------------------
    # Ayu Mirage — slate-blue surface, warm-leaning CTAs
    # ----------------------------------------------------------------
    Theme(
        id="ayu_mirage",
        name="Ayu Mirage",
        subtitle="Soft slate-blue mid-dark",
        family="dark",
        notes=(
            "Ayu Mirage's muted slate-blue surface with warm-leaning CTAs — "
            "orange primary, blue secondary, purple tertiary, yellow caution, "
            "red destructive. Balanced mid-dark."
        ),
        tokens={
            "bg": "#1f2430", "card": "#232834",
            "input": "#2b3140", "border": "#3b4252",
            "fg": "#cbccc6", "muted": "#8a8f99", "accent": "#ffcc66",
            "go": "#c47a1f",     "goFg": "#1f2430",
            "info": "#3a7fc4",   "infoFg": "#ffffff",
            "alt": "#8a6fd0",    "altFg": "#ffffff",
            "warn": "#b0922c",   "warnFg": "#1f2430",
            "danger": "#c44a4a", "dangerFg": "#ffffff",
            "hover": "#2b3140", "selection": "#3b4252", "selectionFg": "#ffffff",
            "logBg": "#232834", "promptFg": "#ffcc66", "answerFg": "#87d96c",
            "shadow": "rgba(0, 0, 0, 0.4)",
        },
    ),

    # ----------------------------------------------------------------
    # IBM Carbon — near-black grey surface, crisp product CTAs
    # ----------------------------------------------------------------
    Theme(
        id="carbon",
        name="IBM Carbon",
        subtitle="Crisp product grey",
        family="dark",
        notes=(
            "IBM Carbon's near-black grey surface with crisp product CTAs — "
            "blue primary, teal secondary, purple tertiary, yellow caution, "
            "red destructive. Enterprise-clean."
        ),
        tokens={
            "bg": "#161616", "card": "#1f1f1f",
            "input": "#262626", "border": "#393939",
            "fg": "#f4f4f4", "muted": "#a8a8a8", "accent": "#78a9ff",
            "go": "#2f6ce5",     "goFg": "#ffffff",
            "info": "#197a78",   "infoFg": "#ffffff",
            "alt": "#7a4fd0",    "altFg": "#ffffff",
            "warn": "#a67a14",   "warnFg": "#161616",
            "danger": "#da1e28", "dangerFg": "#ffffff",
            "hover": "#262626", "selection": "#393939", "selectionFg": "#ffffff",
            "logBg": "#1f1f1f", "promptFg": "#f1c21b", "answerFg": "#42be65",
            "shadow": "rgba(0, 0, 0, 0.55)",
        },
    ),

    # ----------------------------------------------------------------
    # Palenight — muted indigo surface, soft material CTAs
    # ----------------------------------------------------------------
    Theme(
        id="palenight",
        name="Palenight",
        subtitle="Muted material indigo",
        family="dark",
        notes=(
            "Material Palenight's muted indigo surface with soft CTAs — "
            "indigo primary, cyan secondary, green tertiary, coral caution, "
            "pink destructive. Mellow and rounded."
        ),
        tokens={
            "bg": "#292d3e", "card": "#222637",
            "input": "#323750", "border": "#444a68",
            "fg": "#c6cce6", "muted": "#8c92b8", "accent": "#82aaff",
            "go": "#5a6fd0",     "goFg": "#ffffff",
            "info": "#2d8aa8",   "infoFg": "#ffffff",
            "alt": "#4f9a6f",    "altFg": "#ffffff",
            "warn": "#c47038",   "warnFg": "#ffffff",
            "danger": "#c44a72", "dangerFg": "#ffffff",
            "hover": "#323750", "selection": "#444a68", "selectionFg": "#ffffff",
            "logBg": "#222637", "promptFg": "#ffcb6b", "answerFg": "#c3e88d",
            "shadow": "rgba(0, 0, 0, 0.45)",
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
