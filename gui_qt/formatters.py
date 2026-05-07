"""Pure-Python presentation helpers for the PySide6 GUI.

These are tkinter-free, Qt-free formatters that translate domain
objects (drive info, status messages, context strings) into display
strings or role names.  They can be tested without instantiating
any widgets.

**Why a parallel module instead of importing from gui/main_window.py:**

The tkinter ``JellyRipperGUI`` class has these helpers as methods (see
``tests/test_main_window_formatters.py`` for the existing 47 tests
that pin them).  Importing from ``gui/main_window.py`` would pull in
tkinter, which we explicitly don't want in the Qt path.

Per the migration plan, this duplication is intentional and short-
lived: when ``gui/main_window.py`` is retired in Phase 3h, the
tkinter copies go away and these become the single source of truth.
Until then, both implementations must produce the same output for the
same input — pinned by ``tests/test_pyside6_formatters.py``.

**Naming convention:** Module-level functions, not methods.  Names
drop the leading underscore from the original method names (since
module-level public functions don't need that signal).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from utils.helpers import MakeMKVDriveInfo


StatusRole = Literal["ready", "error", "warn", "busy"]


# ---------------------------------------------------------------------------
# Drive label
# ---------------------------------------------------------------------------


# Disc-state glyphs from docs/symbol-library.md Section 1.3.  The
# glyph prefixes the disc name in the drive picker / drive label so
# the user can see at a glance whether the drive is loaded, empty,
# or otherwise unavailable.  All three are monochrome Unicode and
# share width, so they don't shift the rest of the label as state
# changes.
_DISC_STATE_LOADED      = "◉"  # ◉ Fisheye — disc inserted, ready
_DISC_STATE_EMPTY       = "⊚"  # ⊚ Circled ring — empty tray
_DISC_STATE_UNAVAILABLE = "◌"  # ◌ Dotted circle — not present / busy


def _disc_state_glyph(state_code: int) -> str:
    """Pick the disc-state glyph for the drive label.

    State codes match ``MakeMKVDriveInfo.usability_state``:
    * ``2``   → ready (disc inserted)
    * ``0``   → empty (tray open / no disc)
    * other   → unavailable (drive busy, offline, etc.)
    """
    if state_code == 2:
        return _DISC_STATE_LOADED
    if state_code == 0:
        return _DISC_STATE_EMPTY
    return _DISC_STATE_UNAVAILABLE


def format_drive_label(
    drive: "MakeMKVDriveInfo",
    *,
    include_state_glyph: bool = True,
) -> str:
    """Format a ``MakeMKVDriveInfo`` for the drive picker dropdown.

    Output shape (with glyph):

        ``Drive {index}: {drive_name} | Disc: {state_glyph} {disc_name} | Path: {device_path} | State: {usability_state} ({state_code})``

    The state glyph (``◉`` / ``⊚`` / ``◌``) gives an at-a-glance
    visual cue about whether the drive is loaded.  It's prefixed to
    the disc name (not the whole label) so the field structure is
    unchanged for log parsers — the glyph is part of the
    ``disc_name`` cell.

    Pass ``include_state_glyph=False`` to drop the glyph and emit
    just ``Disc: {disc_name}``.  The Appearance tab toggles this
    via ``opt_drive_state_glyph``; the drive handler reads cfg and
    forwards the bool here.

    Missing fields fall back to safe defaults (``"Unknown drive"``,
    ``"No disc"``, ``f"disc:{index}"``).
    """
    drive_name = drive.drive_name or "Unknown drive"
    disc_name = drive.disc_name or "No disc"
    device_path = drive.device_path or f"disc:{drive.index}"
    state = f"{drive.usability_state} ({drive.state_code})"
    if include_state_glyph:
        disc_field = f"{_disc_state_glyph(drive.state_code)} {disc_name}"
    else:
        disc_field = disc_name
    return (
        f"Drive {drive.index}: {drive_name} | "
        f"Disc: {disc_field} | "
        f"Path: {device_path} | "
        f"State: {state}"
    )


# ---------------------------------------------------------------------------
# Trim a long string to a one-line context label
# ---------------------------------------------------------------------------


def trim_context_label(text: str, limit: int = 40) -> str:
    """Collapse internal whitespace and truncate with an ellipsis.

    Mirrors ``JellyRipperGUI._trim_context_label`` exactly.  Used to
    show a compact preview of free-text user input or filenames in
    contexts where a long string would wrap badly.

    Behavior:

    * Multi-whitespace (newlines, tabs, runs of spaces) collapses
      to a single space — the input might come from a multi-line
      log paste.
    * If the result is at most ``limit`` characters, return as-is.
    * Otherwise truncate to ``limit - 1`` chars and append ``"…"``.

    ``limit`` defaults to 40 to match the tkinter caller's default.
    """
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)] + "…"


# ---------------------------------------------------------------------------
# Status message → role classification
# ---------------------------------------------------------------------------


# Token sets are class-constants so callers (and tests) can introspect
# what triggers each role.  Keep these in lowercase since the
# classifier lowercases the input.
_ERROR_TOKENS = (
    "failed", "error", "missing", "invalid", "blocked", "unavailable",
)
_WARN_TOKENS = (
    "attention", "warning", "aborting", "cancelled", "canceled",
    "retry", "waiting",
)
_IDLE_NORMALIZED = {"", "ready", "idle"}


def status_role_for_message(msg: str | None) -> StatusRole:
    """Classify a status message into one of four UI roles.

    Returns:
        * ``"ready"`` — empty / idle / "Choose a mode" prompts
        * ``"error"`` — anything containing failure/error tokens
        * ``"warn"`` — anything containing warning/in-flight tokens
        * ``"busy"`` — anything else (a positive in-progress message)

    Mirrors the bucketing logic of
    ``JellyRipperGUI._main_status_style_for_message`` but returns a
    semantic role name instead of a tuple of tkinter color values.
    The Qt path uses the role name as a ``setObjectName`` value
    (``statusReady`` / ``statusError`` / ``statusWarn`` / ``statusBusy``)
    and the QSS files target those names — the colors live in QSS,
    not in Python.

    Bucketing precedence (matches tkinter):

    1. Idle / empty / "Choose a mode" → ``ready``
    2. Error tokens → ``error``
    3. Warn tokens → ``warn``
    4. Anything else → ``busy``
    """
    normalized = str(msg or "").strip().lower()
    if not normalized or normalized in _IDLE_NORMALIZED or "choose a mode" in normalized:
        return "ready"
    if any(token in normalized for token in _ERROR_TOKENS):
        return "error"
    if any(token in normalized for token in _WARN_TOKENS):
        return "warn"
    return "busy"
