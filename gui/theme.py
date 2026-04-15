"""Shared GUI theme primitives for JellyRip."""

from __future__ import annotations

import re
from collections.abc import Mapping


APP_THEME: dict[str, str] = {
    "window_bg": "#091321",
    "header_bg": "#112540",
    "header_border": "#31465f",
    "surface": "#13253d",
    "surface_alt": "#0d192a",
    "surface_deep": "#0a1320",
    "panel_border": "#31465f",
    "title": "#27b8ff",
    "text": "#f5f9ff",
    "muted": "#a8b7ca",
    "muted_soft": "#7f92ab",
    "input_bg": "#f6f8fc",
    "input_fg": "#0f1726",
    "toolbar_button": "#14263d",
    "toolbar_button_active": "#425470",
    "toolbar_button_text": "#f2f7ff",
    "toolbar_button_muted": "#d3ddec",
    "green": "#05b53f",
    "teal": "#0fa19a",
    "blue": "#2b63f2",
    "purple": "#a400ff",
    "orange": "#ff5a00",
    "abort": "#ff2f42",
    "ready_text": "#27d8ff",
    "progress_fill": "#00ee88",
    "progress_trough": "#122338",
    "log_bg": "#0d1522",
    "log_text": "#00f082",
    "sash": "#223a57",
    "sash_active": "#2f5278",
    "pill_idle_bg": "#182a44",
    "pill_idle_border": "#37506f",
    "pill_active_bg": "#12311f",
    "pill_active_border": "#00ee88",
    "pill_warn_bg": "#3d2d10",
    "pill_warn_border": "#ffb34d",
    "pill_error_bg": "#411720",
    "pill_error_border": "#ff6c7b",
}

THEME_EDITOR_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Core",
        (
            ("window_bg", "Window background"),
            ("header_bg", "Header background"),
            ("header_border", "Header border"),
            ("surface", "Panel background"),
            ("surface_alt", "Log panel background"),
            ("surface_deep", "Input background"),
            ("panel_border", "Panel border"),
            ("title", "Accent title"),
            ("text", "Primary text"),
            ("muted", "Muted text"),
            ("toolbar_button", "Toolbar button"),
            ("toolbar_button_active", "Toolbar button hover"),
            ("toolbar_button_text", "Toolbar button text"),
            ("toolbar_button_muted", "Toolbar muted text"),
            ("log_bg", "Log background"),
            ("log_text", "Log text"),
            ("progress_fill", "Progress fill"),
            ("progress_trough", "Progress trough"),
        ),
    ),
    (
        "Actions",
        (
            ("green", "TV rip button"),
            ("teal", "Movie rip button"),
            ("blue", "Dump button"),
            ("purple", "Organize button"),
            ("orange", "Prep button"),
            ("abort", "Abort button"),
        ),
    ),
    (
        "Status",
        (
            ("ready_text", "Ready text"),
            ("pill_idle_bg", "Idle pill background"),
            ("pill_idle_border", "Idle pill border"),
            ("pill_active_bg", "Active pill background"),
            ("pill_active_border", "Active pill border"),
            ("pill_warn_bg", "Warning pill background"),
            ("pill_warn_border", "Warning pill border"),
            ("pill_error_bg", "Error pill background"),
            ("pill_error_border", "Error pill border"),
        ),
    ),
)

_HEX_COLOR_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")


DIALOG_THEME: dict[str, str] = {
    "window_bg": APP_THEME["window_bg"],
    "surface": APP_THEME["surface"],
    "surface_alt": APP_THEME["surface_alt"],
    "surface_deep": APP_THEME["surface_deep"],
    "border": APP_THEME["panel_border"],
    "text": APP_THEME["text"],
    "muted": APP_THEME["muted"],
    "accent": APP_THEME["title"],
    "primary_button_bg": APP_THEME["green"],
    "primary_button_fg": "#ffffff",
    "secondary_button_bg": APP_THEME["toolbar_button"],
    "secondary_button_fg": APP_THEME["text"],
    "accent_button_bg": APP_THEME["blue"],
    "accent_button_fg": "#ffffff",
    "danger_button_bg": APP_THEME["abort"],
    "danger_button_fg": "#ffffff",
    "success_fg": "#7fffb0",
    "warning_fg": APP_THEME["pill_warn_border"],
    "danger_fg": APP_THEME["pill_error_border"],
    "warning_bg": APP_THEME["pill_warn_bg"],
    "danger_bg": APP_THEME["pill_error_bg"],
    "danger_text": "#ffdbe2",
    "input_bg": APP_THEME["surface_deep"],
    "input_fg": APP_THEME["text"],
}


CLASSIFICATION_LABEL_COLORS: dict[str, str] = {
    "MAIN": DIALOG_THEME["accent"],
    "DUPLICATE": DIALOG_THEME["warning_fg"],
    "EXTRA": DIALOG_THEME["muted"],
    "UNKNOWN": APP_THEME["orange"],
}
def normalize_theme_color(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    token = value.strip()
    if not token or not _HEX_COLOR_RE.fullmatch(token):
        return None
    if not token.startswith("#"):
        token = f"#{token}"
    return token.lower()


def sanitize_theme_overrides(raw: object) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        return {}
    cleaned: dict[str, str] = {}
    for key, value in raw.items():
        if key not in APP_THEME:
            continue
        normalized = normalize_theme_color(value)
        if normalized is not None:
            cleaned[str(key)] = normalized
    return cleaned


def build_app_theme(overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    theme = dict(APP_THEME)
    theme.update(sanitize_theme_overrides(overrides))
    return theme


def dialog_palette() -> dict[str, str]:
    return dict(DIALOG_THEME)
