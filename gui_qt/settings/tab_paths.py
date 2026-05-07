"""Paths tab — binary + folder configuration.

Lets the user point JellyRip at:

* ``makemkvcon_path`` — required for ripping; auto-detected if blank.
* ``ffprobe_path`` — required for validation; auto-detected if blank.
* ``ffmpeg_path`` — optional, only needed for the FFmpeg transcode
  workflow; bundled binary used if blank.
* ``handbrake_path`` — optional, only needed for HandBrake transcoding.
* ``temp_folder`` — where in-flight rips land before they're moved
  into the library.
* ``tv_folder`` — Jellyfin TV library root.
* ``movies_folder`` — Jellyfin movie library root.

Design principles match ``tab_appearance.py``:

1. **OK commits, Cancel reverts.**  Path edits do not affect runtime
   state until OK fires; Cancel discards them.
2. **Snapshot-on-open.**  Construction reads every cfg key once and
   keeps it as the revert target.
3. **Empty input is allowed** — saves an empty string, which lets
   the engine's resolver fall back to registry / known-locations /
   bundled binary.

This is a minimal Paths tab — no validation that the user-typed path
actually exists.  The engine's path resolver already handles missing
files gracefully (Section 8 of the smoke report); blocking the
dialog on a typo would just frustrate users on first-run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Mapping

from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    pass


# Tuple shape per row: (cfg-key, user-facing label, kind, file-filter)
# kind = "file" → opens an open-file dialog with the given filter
# kind = "dir"  → opens a folder picker
_PATH_ROWS: tuple[tuple[str, str, str, str], ...] = (
    ("makemkvcon_path", "MakeMKV (makemkvcon)",  "file", "Executable (*.exe);;All files (*)"),
    ("ffprobe_path",    "ffprobe",                "file", "Executable (*.exe);;All files (*)"),
    ("ffmpeg_path",     "ffmpeg (optional)",      "file", "Executable (*.exe);;All files (*)"),
    ("handbrake_path",  "HandBrakeCLI (optional)","file", "Executable (*.exe);;All files (*)"),
    ("temp_folder",     "Temp / staging folder",  "dir",  ""),
    ("tv_folder",       "TV library folder",      "dir",  ""),
    ("movies_folder",   "Movies library folder",  "dir",  ""),
)


class PathsTab(QWidget):
    """Form layout with one row per path-style cfg key."""

    def __init__(
        self,
        cfg: dict[str, Any],
        save_cfg: Callable[[Mapping[str, Any]], None] | None = None,
        parent: "QWidget | None" = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("settingsTabPaths")

        self._cfg = cfg
        self._save_cfg = save_cfg
        # Snapshot every key we own at construction.  ``cancel()``
        # writes these back if the user dismisses with edits in
        # flight; ``apply()`` writes the live edit values.
        self._snapshot: dict[str, str] = {
            key: str(cfg.get(key, "") or "")
            for key, _label, _kind, _filt in _PATH_ROWS
        }
        self._edits: dict[str, QLineEdit] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        intro = QLabel(
            "Where to find external tools and where to put rip output.  "
            "Leave a tool path empty to use the bundled binary or the "
            "auto-detected install location."
        )
        intro.setObjectName("settingsPathsIntro")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        form_host = QFrame()
        form_host.setObjectName("settingsPathsFormHost")
        form = QFormLayout(form_host)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)
        form.setLabelAlignment(_label_align())

        for key, label, kind, file_filter in _PATH_ROWS:
            row = self._build_row(key, kind, file_filter)
            form.addRow(label + ":", row)

        outer.addWidget(form_host, stretch=1)
        outer.addStretch(1)

    # ── Row builder ────────────────────────────────────────────────

    def _build_row(self, key: str, kind: str, file_filter: str) -> QWidget:
        host = QWidget()
        row_layout = QHBoxLayout(host)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)

        edit = QLineEdit(self._snapshot[key])
        edit.setObjectName(f"settingsPath_{key}")
        edit.setPlaceholderText("(use bundled / auto-detect)")
        row_layout.addWidget(edit, stretch=1)
        self._edits[key] = edit

        browse_btn = QPushButton("Browse…")
        browse_btn.setObjectName(f"settingsPathBrowse_{key}")
        browse_btn.clicked.connect(
            lambda _checked=False, k=key, knd=kind, flt=file_filter:
                self._on_browse(k, knd, flt)
        )
        row_layout.addWidget(browse_btn)

        return host

    def _on_browse(self, key: str, kind: str, file_filter: str) -> None:
        edit = self._edits.get(key)
        if edit is None:
            return
        current = edit.text().strip()

        if kind == "dir":
            chosen = QFileDialog.getExistingDirectory(
                self,
                "Choose folder",
                current or "",
            )
        else:
            chosen, _selected_filter = QFileDialog.getOpenFileName(
                self,
                "Choose file",
                current or "",
                file_filter or "All files (*)",
            )

        if chosen:
            edit.setText(chosen)

    # ── Dialog lifecycle hooks ─────────────────────────────────────

    def apply(self) -> None:
        """OK pressed.  Write every edit's current value to cfg and
        (best-effort) persist via ``save_cfg``.

        Empty strings are saved as empty so the engine resolver
        falls through to registry / known-locations / bundled binary.
        Whitespace-only input collapses to empty.
        """
        for key, edit in self._edits.items():
            value = edit.text().strip()
            self._cfg[key] = value
        if self._save_cfg is not None:
            try:
                self._save_cfg(self._cfg)
            except Exception:
                # Don't crash the dialog if save fails — the cfg
                # mutation already applied; user can retry from
                # Settings or fix the file path manually.
                pass

    def cancel(self) -> None:
        """Cancel pressed.  No runtime state changed (the field
        edits are widget-local until apply); reset the inputs back
        to the snapshot in case the user re-opens the dialog
        without restarting."""
        for key, edit in self._edits.items():
            edit.setText(self._snapshot.get(key, ""))


def _label_align():
    """Wrapped so older PySide6 Qt-stub variants don't choke on the
    enum lookup at module import time."""
    from PySide6.QtCore import Qt
    return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
