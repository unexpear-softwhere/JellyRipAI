"""Log pane widget for the PySide6 GUI.

Replaces the tkinter ``ScrolledText`` log used in
``gui/main_window.py`` with a Qt-native ``QPlainTextEdit``.  Same
public API the controller already uses:

* ``append(text, tag=None)`` — append one log line.  ``tag`` is
  ``"prompt"`` / ``"answer"`` / ``None`` and controls inline color.
* ``get_text()`` — return the full log content as a string for the
  Copy Log workflow.

Behavior pinned by tests:

* **Autoscroll-when-at-bottom.**  Scroll position only auto-advances
  if the user was already near the bottom (within 5%).  If they've
  scrolled up to read history, appending a new line doesn't yank
  them back down.
* **Line-cap trim.**  When line count exceeds ``opt_log_cap_lines``
  (default 300_000), trim down to ``opt_log_trim_lines``
  (default 200_000) by removing leading blocks.  Prevents unbounded
  memory growth during long sessions.
* **Per-line color tagging.**  ``tag="prompt"`` colors the line with
  the prompt color; ``tag="answer"`` colors it with the answer color.
  Colors come from a ``tag_colors`` dict passed at construction time,
  which the main window populates from the active theme's ``promptFg``
  / ``answerFg`` tokens.  If no colors are passed, defaults match the
  ``dark_github`` theme.

Why per-character formatting via ``QTextCharFormat`` instead of HTML
spans: ``QPlainTextEdit`` is plain-text-first; HTML class names get
stripped on round-trip.  ``QTextCharFormat`` is the documented Qt
way to color individual blocks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QWidget

if TYPE_CHECKING:
    pass


# Defaults — kept in sync with shared/runtime.py DEFAULTS so a
# stand-alone widget without cfg still behaves sensibly.
DEFAULT_LOG_CAP_LINES = 300_000
DEFAULT_LOG_TRIM_LINES = 200_000

# Autoscroll threshold — match tkinter's `yview()[1] > 0.95` behavior.
_AT_BOTTOM_RATIO = 0.95

# Default tag colors — match the ``dark_github`` theme's ``promptFg``
# (#f0e68c) and ``answerFg`` (#90ee90).  Real callers should pass an
# explicit dict from the active theme so the colors stay in sync with
# the rest of the QSS.
#
# ``warn`` and ``error`` are auto-applied by the level classifier
# below — callers don't need to pass a tag for them.  Picked to be
# legible on every theme background (amber for warn, salmon for
# error).
_DEFAULT_TAG_COLORS = {
    "prompt": "#f0e68c",
    "answer": "#90ee90",
    "warn":   "#f0a44b",
    "error":  "#ff6b6b",
}


def _classify_log_line(text: str) -> str | None:
    """Auto-detect a level tag from the leading content of a log
    line.  Returns ``"error"`` / ``"warn"`` or ``None`` (info — no
    tag).  Mirrors the bucketing used by ``status_role_for_message``
    so the live log stays consistent with the status bar.

    Detection is intentionally conservative — only fires on lines
    whose leading word makes the level unambiguous.  We don't want
    to color, e.g., a benign "no errors found" line red.
    """
    stripped = text.lstrip()
    # Strip a leading bracketed timestamp if present
    # (``[11:27:46] foo``).  Only strip when the bracket content
    # looks like a clock — ``[12:34:56]`` or ``[12:34]`` — so we
    # don't accidentally eat ``[WARN]`` / ``[ERROR]`` prefixes that
    # the level classifier needs to see.
    if stripped.startswith("[") and "]" in stripped:
        bracket_end = stripped.index("]")
        inner = stripped[1:bracket_end]
        # Cheap timestamp check: at least one colon and content
        # composed of digits / colons / dots / spaces.
        is_timestamp = (
            ":" in inner
            and all(c.isdigit() or c in ":. " for c in inner)
        )
        if is_timestamp:
            stripped = stripped[bracket_end + 1:].lstrip()
    upper = stripped.upper()
    if upper.startswith(("ERROR", "FAILED", "FAIL ", "FAIL:", "EXCEPTION", "TRACEBACK")):
        return "error"
    if upper.startswith(("ERROR —", "ERROR -")):
        return "error"
    if upper.startswith(("WARNING", "WARN ", "WARN:", "[WARN", "[WARNING")):
        return "warn"
    if upper.startswith(("CANCELLED", "ABORTED")):
        return "warn"
    return None


# Severity glyphs prefixed to warn / error log lines.  From
# docs/symbol-library.md Section 1.6.  Plain info lines stay
# unprefixed — adding ⓘ to every line would be visual noise.  The
# trailing two spaces match the button-label convention so the
# rhythm is consistent across the UI.
_LEVEL_GLYPHS: dict[str, str] = {
    "warn":  "⚠  ",   # U+26A0 Warning sign
    "error": "✗  ",   # U+2717 Cross
}


# ---------------------------------------------------------------------------
# Pure helper for the autoscroll heuristic — testable without Qt.
# ---------------------------------------------------------------------------


def is_scrolled_to_bottom(scroll_value: int, scroll_max: int) -> bool:
    """Return True if the user is "near the bottom" of the scroll range.

    Pure function — takes scrollbar values and returns a bool.  Pinned
    by tests so the autoscroll behavior is independent of widget
    state.

    The threshold matches the tkinter implementation: bottom of the
    visible area within 5% of the document end.  When ``scroll_max``
    is 0 the document fits without scrolling — treat that as "at
    bottom" since appending a line just extends the visible area.
    """
    if scroll_max <= 0:
        return True
    return scroll_value >= int(scroll_max * _AT_BOTTOM_RATIO)


# ---------------------------------------------------------------------------
# The widget
# ---------------------------------------------------------------------------


class LogPane(QPlainTextEdit):
    """Live-log display.  Read-only, monospace, autoscrolls when the
    user is at the bottom.  Trims old lines to a configurable cap.

    Construction:

        log = LogPane(cfg, tag_colors=theme_colors, parent=window)
        log.append("MakeMKV started")
        log.append("? Title 02 keep? (y/n)", tag="prompt")
        log.append("y", tag="answer")
    """

    def __init__(
        self,
        cfg: Mapping[str, object] | None = None,
        tag_colors: Mapping[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("logPane")
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setUndoRedoEnabled(False)

        self._cfg = cfg or {}

        # Appearance toggles read from cfg at construction.  Both
        # default True so the live UI matches previous behavior.
        # Setters below let the Appearance tab flip them at runtime
        # for click-to-apply preview.
        self._color_levels_enabled: bool = bool(
            self._cfg.get("opt_log_color_levels", True)
        )
        self._glyph_prefix_enabled: bool = bool(
            self._cfg.get("opt_log_glyph_prefix", True)
        )

        # Pre-build QTextCharFormat objects for each tag.  The
        # foreground color comes from the per-instance ``tag_colors``
        # dict (theme-driven).  Updating the theme at runtime would
        # need a separate ``set_tag_colors`` method, deferred to the
        # 3d theme picker.
        merged = dict(_DEFAULT_TAG_COLORS)
        if tag_colors:
            merged.update(tag_colors)
        self._tag_colors = merged

        self._tag_formats: dict[str, QTextCharFormat] = {}
        for tag, color_hex in merged.items():
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color_hex))
            self._tag_formats[tag] = fmt

    # ------------------------------------------------------------------
    # Public API — matches what gui/main_window.py callers use.
    # ------------------------------------------------------------------

    def append(self, text: str, tag: str | None = None) -> None:
        """Append a log line.  ``tag`` is ``"prompt"`` / ``"answer"``
        / ``None``.

        Mirrors the contract of ``JellyRipperGUI._append_log_text_main``:
        each call appends one line; embedded newlines are preserved
        verbatim; autoscroll only fires if the user was already near
        the bottom; trim happens before scrolling.
        """
        # Capture autoscroll state BEFORE the insertion.
        sb = self.verticalScrollBar()
        was_at_bottom = is_scrolled_to_bottom(sb.value(), sb.maximum())

        line = text.rstrip("\n")

        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        # If the document already has content, prefix with a newline
        # so the new line lands in its own block.
        if not self.document().isEmpty():
            cursor.insertText("\n")

        # If the caller didn't pass an explicit tag, classify the
        # line so warn/error lines stand out during long batches.
        # Auto-classification only runs when level-coloring is
        # enabled — disabled means the user wants plain output, so
        # we skip the classifier entirely.  Explicit tags
        # ("prompt"/"answer") still take precedence regardless.
        if tag in self._tag_formats:
            effective_tag = tag
        elif self._color_levels_enabled or self._glyph_prefix_enabled:
            effective_tag = _classify_log_line(line)
        else:
            effective_tag = None

        # Severity glyph prefix (⚠ / ✗) for auto-classified warn /
        # error lines.  Color signals severity in the UI; the glyph
        # also survives in copy-log output so an issue-paste in
        # plain text still flags the level.  Both signals are
        # independently toggleable via the Appearance tab.
        if (
            tag is None
            and self._glyph_prefix_enabled
            and effective_tag in _LEVEL_GLYPHS
        ):
            line = _LEVEL_GLYPHS[effective_tag] + line

        # Apply the per-block color only when the auto-classification
        # came from a level (warn/error) AND coloring is enabled, OR
        # when the caller passed an explicit tag (prompt/answer —
        # those bypass the toggle since they're caller-driven).
        apply_color = (
            (tag is not None and tag in self._tag_formats)
            or (
                tag is None
                and self._color_levels_enabled
                and effective_tag in self._tag_formats
            )
        )
        if apply_color:
            cursor.insertText(line, self._tag_formats[effective_tag])
        else:
            cursor.insertText(line)

        # Trim BEFORE scrolling so the scroll-to-end after trim shows
        # the latest line, not an interior offset.
        self._trim_to_cap()

        if was_at_bottom:
            self._scroll_to_end()

    def get_text(self) -> str:
        """Return the full log content as plain text.  Used by the
        Copy Log button."""
        return self.toPlainText()

    def clear(self) -> None:
        """Clear the log.  Pinned as a documented part of the public
        API in case 3c-ii's workflow controller wants to start fresh
        between sessions."""
        super().clear()

    # ------------------------------------------------------------------
    # Appearance toggles — wired to the Appearance tab for click-to-apply
    # ------------------------------------------------------------------

    def set_color_levels_enabled(self, enabled: bool) -> None:
        """Toggle warn/error coloring on subsequent ``append`` calls.
        Existing log content keeps its colors; only new lines are
        affected.  Live-preview path from the Appearance tab."""
        self._color_levels_enabled = bool(enabled)

    def set_glyph_prefix_enabled(self, enabled: bool) -> None:
        """Toggle the ⚠/✗ glyph prefix on subsequent warn/error
        lines.  Existing content unchanged."""
        self._glyph_prefix_enabled = bool(enabled)

    @property
    def color_levels_enabled(self) -> bool:
        return self._color_levels_enabled

    @property
    def glyph_prefix_enabled(self) -> bool:
        return self._glyph_prefix_enabled

    # ------------------------------------------------------------------
    # Test hooks — read-only access to the per-tag color map and the
    # last-applied char format on a block.
    # ------------------------------------------------------------------

    @property
    def tag_colors(self) -> Mapping[str, str]:
        """Effective tag colors (after merging defaults with whatever
        was passed at construction).  Read-only view — for tests and
        the picker UI."""
        return dict(self._tag_colors)

    def block_color_at(self, block_number: int) -> str | None:
        """Return the foreground color hex of the block at ``block_number``,
        or ``None`` if no per-block color was applied (i.e., the line
        used the default text color).

        Pinned for tests so we can verify that ``append(..., tag="prompt")``
        actually attached the prompt color to the resulting block.
        """
        block = self.document().findBlockByNumber(block_number)
        if not block.isValid():
            return None
        # Walk the fragments in this block; if any has an explicit
        # foreground color, return that.
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            fmt = fragment.charFormat()
            brush = fmt.foreground()
            if brush.style() != Qt.BrushStyle.NoBrush:
                color = brush.color()
                return color.name()  # "#rrggbb"
            it += 1
        return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _trim_to_cap(self) -> None:
        """If the document has more lines than ``opt_log_cap_lines``,
        delete leading blocks down to ``opt_log_trim_lines``.
        """
        cap = int(self._cfg.get("opt_log_cap_lines", DEFAULT_LOG_CAP_LINES))
        if self.blockCount() <= cap:
            return

        trim = int(
            self._cfg.get("opt_log_trim_lines", DEFAULT_LOG_TRIM_LINES)
        )
        to_remove = self.blockCount() - trim
        if to_remove <= 0:
            return

        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        for _ in range(to_remove):
            cursor.movePosition(
                QTextCursor.MoveOperation.NextBlock,
                QTextCursor.MoveMode.KeepAnchor,
            )
        cursor.removeSelectedText()

    def _scroll_to_end(self) -> None:
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())
