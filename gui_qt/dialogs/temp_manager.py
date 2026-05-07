"""Temp Session Manager dialog.

Shows leftover temp/working folders from prior rip sessions, lets
the user multi-select and delete them.  Mirrors the tkinter
``show_temp_manager`` at ``gui/main_window.py:5929``.

**Contract:**

* Inputs: ``old_folders`` (sequence of either 4-tuples
  ``(full_path, name, file_count, size_bytes)`` or bare path
  strings), ``engine`` (to read ``read_temp_metadata(full_path)``
  for per-folder status), ``log_fn`` (callable to log delete
  results from the worker thread).
* Returns: ``None``.  Side-effect-only dialog.
* Empty input → returns immediately without opening (matches tkinter).

**Threading model:**

* Dialog construction + display happens on the GUI thread
  (caller's responsibility to marshal there via ``MainWindow``'s
  thread-safe wrapper).
* Delete runs on a daemon worker thread so a large folder tree
  doesn't block the UI.  The dialog closes BEFORE the delete
  starts so the user gets immediate feedback.

**Status colors** — a ``status`` string from the metadata maps to
an objectName so QSS owns the color:

* ``ripped`` → ``tempStatusRipped`` (success / green)
* ``ripping`` / ``organizing`` → ``tempStatusBusy`` (amber)
* ``organized`` → ``tempStatusOrganized`` (info / blue)
* default / unknown → ``tempStatusUnknown`` (danger / red)

The dot/asterisk character itself is rendered via a ``QLabel`` so
the QSS files can target it.
"""

from __future__ import annotations

import os
import shutil
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    pass


# Status string → QSS objectName (for color theming).
_STATUS_OBJECT_NAMES: dict[str, str] = {
    "ripped":     "tempStatusRipped",
    "ripping":    "tempStatusBusy",
    "organizing": "tempStatusBusy",
    "organized":  "tempStatusOrganized",
}
_DEFAULT_STATUS_OBJECT_NAME = "tempStatusUnknown"


# ---------------------------------------------------------------------------
# Pure helpers (testable without Qt)
# ---------------------------------------------------------------------------


@dataclass
class _NormalizedFolder:
    """4-field record for one temp folder, normalized from either a
    4-tuple or a path string."""
    full_path: str
    name: str
    file_count: int
    size_bytes: int


def normalize_folders(
    old_folders: Sequence[Any],
) -> list[_NormalizedFolder]:
    """Coerce a heterogeneous ``old_folders`` sequence into uniform
    ``_NormalizedFolder`` records.  Mirrors the inline normalization
    at ``gui/main_window.py:5946-5957``.

    Accepts:

    * 4-tuples / 4-lists ``(full_path, name, file_count, size)``
    * Bare path strings — name derived from basename, counts default
      to 0.
    """
    out: list[_NormalizedFolder] = []
    for entry in old_folders:
        if isinstance(entry, (tuple, list)) and len(entry) == 4:
            full_path, name, file_count, size = entry
            out.append(
                _NormalizedFolder(
                    full_path=str(full_path),
                    name=str(name),
                    file_count=int(file_count or 0),
                    size_bytes=int(size or 0),
                )
            )
        else:
            full_path = os.path.normpath(str(entry))
            name = os.path.basename(full_path.rstrip("\\/")) or full_path
            out.append(
                _NormalizedFolder(
                    full_path=full_path,
                    name=name,
                    file_count=0,
                    size_bytes=0,
                )
            )
    return out


def status_object_name(status: str) -> str:
    """Map a metadata status string to a QSS-targetable objectName.
    Pure helper for tests + theming drift guards."""
    return _STATUS_OBJECT_NAMES.get(status, _DEFAULT_STATUS_OBJECT_NAME)


def format_folder_summary(
    folder: _NormalizedFolder,
    timestamp_text: str,
    status_text: str,
) -> str:
    """Build the second-line summary text mirroring tkinter's
    ``Ripped: {ts}   Files: {count}   Size: {gb} GB   Status: {s}``
    pattern."""
    gb = folder.size_bytes / (1024 ** 3)
    return (
        f"Ripped: {timestamp_text}   "
        f"Files: {folder.file_count}   "
        f"Size: {gb:.1f} GB   "
        f"Status: {status_text}"
    )


def _read_metadata_safe(engine: Any, full_path: str) -> dict[str, Any]:
    """Read engine metadata for a folder; return ``{}`` if engine
    doesn't have ``read_temp_metadata`` or the call fails."""
    fn = getattr(engine, "read_temp_metadata", None)
    if fn is None:
        return {}
    try:
        result = fn(full_path)
    except Exception:
        return {}
    return result or {}


# ---------------------------------------------------------------------------
# The dialog
# ---------------------------------------------------------------------------


class _TempManagerDialog(QDialog):
    """Multi-select folder picker for deleting leftover temp sessions."""

    def __init__(
        self,
        old_folders: Sequence[Any],
        engine: Any,
        log_fn: Callable[[str], None],
        *,
        deleter: Callable[[str], None] | None = None,
        thread_runner: Callable[[Callable[[], None]], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("tempManagerDialog")
        self.setWindowTitle("Temp Session Manager")
        self.setModal(True)
        self.resize(740, 540)

        self._engine = engine
        self._log_fn = log_fn
        # ``deleter`` and ``thread_runner`` are testable seams —
        # production uses ``shutil.rmtree`` and ``threading.Thread``.
        self._deleter: Callable[[str], None] = deleter or shutil.rmtree
        self._thread_runner = thread_runner or self._default_thread_runner
        self._normalized: list[_NormalizedFolder] = normalize_folders(old_folders)
        # Per-row check state.  Parallel list to ``_normalized`` so
        # tests can verify state without walking the widget tree.
        self._checks: list[QCheckBox] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(15, 15, 15, 12)
        outer.setSpacing(8)

        title = QLabel("Temp Sessions")
        title.setObjectName("stepHeader")
        outer.addWidget(title)

        subtitle = QLabel(
            "Leftover disc folders in your temp directory.\n"
            "Check the ones you want to delete."
        )
        subtitle.setObjectName("stepSubtitle")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        # Scrollable rows
        scroll = QScrollArea()
        scroll.setObjectName("tempManagerScroll")
        scroll.setWidgetResizable(True)

        body = QWidget()
        body.setObjectName("tempManagerBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(4, 6, 4, 6)
        body_layout.setSpacing(4)

        for folder in self._normalized:
            row = self._build_row(folder)
            body_layout.addWidget(row)

        body_layout.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, stretch=1)

        # Button row — Select All / Deselect All / Delete Selected /
        # Close.
        button_row = QHBoxLayout()
        button_row.setSpacing(6)

        self._select_all_btn = QPushButton("Select All")
        self._select_all_btn.setObjectName("listPickerSelectAll")
        self._select_all_btn.clicked.connect(self._select_all)
        button_row.addWidget(self._select_all_btn)

        self._deselect_all_btn = QPushButton("Deselect All")
        self._deselect_all_btn.setObjectName("listPickerDeselectAll")
        self._deselect_all_btn.clicked.connect(self._deselect_all)
        button_row.addWidget(self._deselect_all_btn)

        button_row.addStretch(1)

        self._delete_btn = QPushButton("Delete Selected")
        self._delete_btn.setObjectName("dangerProceedButton")
        self._delete_btn.clicked.connect(self._delete_selected)
        button_row.addWidget(self._delete_btn)

        self._close_btn = QPushButton("Close")
        self._close_btn.setObjectName("cancelButton")
        self._close_btn.clicked.connect(self._on_close)
        button_row.addWidget(self._close_btn)

        outer.addLayout(button_row)

    # ------------------------------------------------------------------
    # Row construction
    # ------------------------------------------------------------------

    def _build_row(self, folder: _NormalizedFolder) -> QFrame:
        meta = _read_metadata_safe(self._engine, folder.full_path)
        status_text = str(meta.get("status", "unknown"))
        title_text = str(meta.get("title", "Unknown"))
        timestamp_text = str(meta.get("timestamp", folder.name))

        row = QFrame()
        row.setObjectName("tempManagerRow")

        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(8, 6, 8, 6)
        row_layout.setSpacing(8)

        # Checkbox.
        check = QCheckBox()
        check.setObjectName("tempManagerCheckbox")
        self._checks.append(check)
        row_layout.addWidget(check)

        # Status indicator — colored asterisk.  Color comes from QSS
        # via the per-status objectName.
        status_dot = QLabel("*")
        status_dot.setObjectName(status_object_name(status_text))
        row_layout.addWidget(status_dot)

        # Info column — title (bold) over a one-line summary.
        info = QFrame()
        info.setObjectName("tempManagerRowInfo")
        info_layout = QVBoxLayout(info)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(2)

        title_label = QLabel(title_text)
        title_label.setObjectName("tempManagerTitle")
        info_layout.addWidget(title_label)

        summary = QLabel(format_folder_summary(folder, timestamp_text, status_text))
        summary.setObjectName("tempManagerSummary")
        info_layout.addWidget(summary)

        row_layout.addWidget(info, stretch=1)

        return row

    # ------------------------------------------------------------------
    # Public read-only test hooks
    # ------------------------------------------------------------------

    @property
    def normalized_folders(self) -> list[_NormalizedFolder]:
        return list(self._normalized)

    @property
    def check_boxes(self) -> list[QCheckBox]:
        return list(self._checks)

    @property
    def selected_folders(self) -> list[_NormalizedFolder]:
        """Folders whose row checkbox is currently checked."""
        return [
            self._normalized[i]
            for i, cb in enumerate(self._checks)
            if cb.isChecked()
        ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _select_all(self) -> None:
        for cb in self._checks:
            cb.setChecked(True)

    def _deselect_all(self) -> None:
        for cb in self._checks:
            cb.setChecked(False)

    def _on_close(self) -> None:
        self.accept()

    def _default_thread_runner(self, fn: Callable[[], None]) -> None:
        threading.Thread(target=fn, daemon=True, name="temp-delete").start()

    # ------------------------------------------------------------------
    # Delete flow
    # ------------------------------------------------------------------

    def _delete_selected(self) -> None:
        """Close the dialog first, then start a worker thread that
        deletes the selected folders.  Mirrors tkinter's
        "destroy first, delete in background" pattern at
        ``gui/main_window.py:6080``."""
        selected = self.selected_folders
        # Close immediately so the user sees the dialog disappear
        # even if the deletion is slow.
        self.accept()

        if not selected:
            return

        log_fn = self._log_fn
        deleter = self._deleter

        def _worker() -> None:
            for folder in selected:
                try:
                    deleter(folder.full_path)
                    log_fn(f"Deleted temp folder: {folder.name}")
                except Exception as e:  # noqa: BLE001
                    log_fn(f"Could not delete {folder.name}: {e}")

        self._thread_runner(_worker)

    def keyPressEvent(self, event):  # noqa: N802 (Qt convention)
        if event.key() == Qt.Key.Key_Escape:
            self._on_close()
            return
        super().keyPressEvent(event)


# ---------------------------------------------------------------------------
# Public function — matches tkinter signature
# ---------------------------------------------------------------------------


def show_temp_manager(
    parent: QWidget | None,
    old_folders: Sequence[Any],
    engine: Any,
    log_fn: Callable[[str], None],
    *,
    deleter: Callable[[str], None] | None = None,
    thread_runner: Callable[[Callable[[], None]], None] | None = None,
) -> None:
    """Show the Temp Session Manager modally.  Returns ``None`` —
    side-effect-only dialog.  If ``old_folders`` is empty, returns
    immediately without opening the window (matches tkinter).

    ``deleter`` and ``thread_runner`` are testable seams; production
    callers use the defaults (``shutil.rmtree`` + a daemon
    ``threading.Thread``).
    """
    if not old_folders:
        return None
    dialog = _TempManagerDialog(
        old_folders=old_folders,
        engine=engine,
        log_fn=log_fn,
        deleter=deleter,
        thread_runner=thread_runner,
        parent=parent,
    )
    dialog.exec()
    return None
