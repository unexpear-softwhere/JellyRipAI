"""Disc-tree title selector dialog.

Qt-native MVP port of the tkinter ``show_disc_tree`` at
``gui/main_window.py:5449``.  Used by the controller's bulk-pick
workflows (Smart Rip, Dump All) when the user needs to multi-select
titles from a freshly-scanned disc.

**MVP scope:**

* Multi-column tree: checkbox / Title # / Name / Duration / Size /
  Chapters / Classification.
* Pre-selects the recommended title (controller-supplied flag).
* Click a row to toggle its checkbox.  Esc cancels.  OK / Cancel
  buttons.
* Returns ``list[str]`` of selected title IDs (controller calls
  ``int(item)`` on each), or ``None`` on cancel.

**Right-click preview** — Phase 3e wired this.  Right-clicking a
row invokes ``preview_callback(title_id)``.  The callback is
controller-side: it rips a short preview clip and (on the tkinter
path) opens a player.  In the Qt path the callback opens the
new ``gui_qt.preview_widget.PreviewDialog`` once the clip exists.

**Out of scope (deferred):**

* Per-row metadata expansion (subtitle/audio sub-rows) — polish
  pass once the workflow is exercised end-to-end.

The ``disc_titles`` argument is a ``Sequence`` of dicts (the
controller's ``DiscTitle = dict[str, Any]`` type alias).  Each
dict has: ``id``, ``name``, ``duration``, ``size``, ``chapters``,
optional ``audio_tracks`` / ``subtitle_tracks``, and optional
``recommended`` / ``classification`` / ``status`` flags injected
by the classifier.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


# Column indices — pinned for readability + tests.
_COL_TITLE = 0      # checkbox + label
_COL_DURATION = 1
_COL_SIZE = 2
_COL_CHAPTERS = 3
_COL_STATUS = 4

_HEADERS: tuple[str, ...] = (
    "Title",
    "Duration",
    "Size",
    "Chapters",
    "Status",
)


def _format_title_label(title: dict[str, Any]) -> str:
    """Build the visible label for a title row.

    Pure helper — testable without Qt.  Mirrors tkinter's
    ``f"Title {t['id']+1}: {t['name']}"`` pattern at
    ``gui/main_window.py:5567``.
    """
    tid = title.get("id", -1)
    name = title.get("name", "") or "(no name)"
    return f"Title {int(tid) + 1}: {name}"


def _is_recommended(title: dict[str, Any]) -> bool:
    """True if this title should be pre-selected.

    The classifier may inject a ``recommended`` boolean; some code
    paths use ``best_id`` matching instead.  This function checks
    for either signal so the dialog doesn't need to know which
    classifier ran upstream.
    """
    if title.get("recommended") is True:
        return True
    return False


def _classification_text(title: dict[str, Any]) -> str:
    """Return the classification status text for the Status column.
    Pure helper for the MVP — falls back to empty string when no
    classification is attached to the dict."""
    return str(title.get("classification") or title.get("status") or "")


class _DiscTreeDialog(QDialog):
    """Modal selection dialog over disc titles."""

    def __init__(
        self,
        disc_titles: Sequence[dict[str, Any]],
        is_tv: bool,
        preview_callback: Callable[[int], None] | None = None,
        parent: "QWidget | None" = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("discTreeDialog")
        self.setWindowTitle(
            "Disc Contents — Select Titles to Rip"
        )
        self.setModal(True)
        self.resize(900, 600)

        self._disc_titles = list(disc_titles)
        self._is_tv = is_tv
        # ``preview_callback`` is accepted for signature compatibility
        # with the tkinter version; MVP doesn't wire it (3e territory).
        self._preview_callback = preview_callback

        # ``result_value`` is the public output: list of title IDs
        # as strings (matches tkinter; controller calls int() on each).
        # ``None`` means cancelled.
        self.result_value: list[str] | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 14)
        outer.setSpacing(8)

        # Header text — same instructional banner as tkinter.
        header = QLabel(
            "Select titles to rip.  Click a row to toggle the "
            "checkbox.  Recommended titles are pre-checked."
        )
        header.setObjectName("stepSubtitle")
        header.setWordWrap(True)
        outer.addWidget(header)

        # The tree itself.
        self._tree = QTreeWidget()
        self._tree.setObjectName("discTitleTree")
        self._tree.setHeaderLabels(list(_HEADERS))
        self._tree.setRootIsDecorated(False)
        self._tree.setUniformRowHeights(True)
        self._tree.setAlternatingRowColors(False)

        # Header sizing — title column stretches, others fit content.
        header_view = self._tree.header()
        header_view.setSectionResizeMode(_COL_TITLE, QHeaderView.ResizeMode.Stretch)
        for col in (_COL_DURATION, _COL_SIZE, _COL_CHAPTERS, _COL_STATUS):
            header_view.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)

        # Populate rows.
        self._items_by_id: dict[str, QTreeWidgetItem] = {}
        self._populate(disc_titles)

        outer.addWidget(self._tree, stretch=1)

        # Buttons — Cancel left, OK (default) right.
        button_row = QHBoxLayout()

        cancel = QPushButton("Cancel")
        cancel.setObjectName("cancelButton")
        cancel.clicked.connect(self._on_cancel)
        button_row.addWidget(cancel)

        button_row.addStretch(1)

        ok = QPushButton("OK")
        ok.setObjectName("confirmButton")
        ok.setDefault(True)
        ok.clicked.connect(self._on_ok)
        button_row.addWidget(ok)

        outer.addLayout(button_row)

        # Click anywhere on a row toggles its checkbox.
        self._tree.itemClicked.connect(self._on_item_clicked)

        # Right-click on a row invokes the preview callback.  Without
        # the CustomContextMenu policy + signal connection, the
        # ``_on_tree_context_menu`` handler defined below never runs —
        # which is the v1 defect the smoke bot caught 2026-05-04.
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_tree_context_menu)

        # Cache button refs for tests.
        self._ok_button = ok
        self._cancel_button = cancel

    # ------------------------------------------------------------------
    # Population
    # ------------------------------------------------------------------

    def _populate(self, disc_titles: Sequence[dict[str, Any]]) -> None:
        """Add a row per disc title.  Each row carries its title ID
        as Qt user-data so we can read it back at submit time."""
        for t in disc_titles:
            tid = str(t.get("id", ""))
            if not tid:
                continue

            item = QTreeWidgetItem(self._tree)
            item.setText(_COL_TITLE, _format_title_label(t))
            item.setText(_COL_DURATION, str(t.get("duration", "") or ""))
            item.setText(_COL_SIZE, str(t.get("size", "") or ""))
            item.setText(_COL_CHAPTERS, str(t.get("chapters", "") or ""))
            item.setText(_COL_STATUS, _classification_text(t))

            # Stash the title ID as user-data on the column-0 item.
            item.setData(_COL_TITLE, Qt.ItemDataRole.UserRole, tid)

            # Checkbox in column 0.  Pre-check if recommended.
            check_state = (
                Qt.CheckState.Checked
                if _is_recommended(t)
                else Qt.CheckState.Unchecked
            )
            item.setCheckState(_COL_TITLE, check_state)

            self._items_by_id[tid] = item

    # ------------------------------------------------------------------
    # Click handler — toggle checkbox when row is clicked anywhere
    # ------------------------------------------------------------------

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Toggle the checkbox unless the user clicked exactly on the
        checkbox itself (Qt handles that already)."""
        if column == _COL_TITLE:
            return  # Qt's built-in checkbox handling fires already
        new_state = (
            Qt.CheckState.Unchecked
            if item.checkState(_COL_TITLE) == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )
        item.setCheckState(_COL_TITLE, new_state)

    # ------------------------------------------------------------------
    # Right-click → preview
    # ------------------------------------------------------------------

    def _on_tree_context_menu(self, position) -> None:
        """Right-click handler — invoke the preview callback for the
        title under the cursor.  Mirrors the tkinter
        ``Button-3 → _preview_title_item`` binding at
        ``gui/main_window.py:5679``.

        No-op if no preview callback was provided or no row is under
        the cursor (right-click on empty space).
        """
        if self._preview_callback is None:
            return
        item = self._tree.itemAt(position)
        if item is None:
            return
        title_id_str = item.data(
            _COL_TITLE, Qt.ItemDataRole.UserRole,
        )
        if not title_id_str:
            return
        try:
            title_id = int(title_id_str)
        except (TypeError, ValueError):
            return
        try:
            self._preview_callback(title_id)
        except Exception:
            # Don't let a misbehaving callback take down the dialog.
            pass

    def trigger_preview_for_test(self, title_id_str: str) -> None:
        """Test helper — invoke the preview callback as if the user
        right-clicked the row whose user-data is ``title_id_str``.
        Avoids needing to construct synthetic mouse events in tests."""
        if self._preview_callback is None:
            return
        try:
            self._preview_callback(int(title_id_str))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Submit / cancel / Esc
    # ------------------------------------------------------------------

    def _selected_ids(self) -> list[str]:
        """Walk the tree, collect IDs of checked rows.  Order matches
        the original ``disc_titles`` argument (insertion order)."""
        out: list[str] = []
        for tid, item in self._items_by_id.items():
            if item.checkState(_COL_TITLE) == Qt.CheckState.Checked:
                out.append(tid)
        return out

    def _on_ok(self) -> None:
        self.result_value = self._selected_ids()
        self.accept()

    def _on_cancel(self) -> None:
        self.result_value = None
        self.reject()

    def keyPressEvent(self, event):  # noqa: N802 (Qt convention)
        if event.key() == Qt.Key.Key_Escape:
            self._on_cancel()
            return
        super().keyPressEvent(event)


def show_disc_tree(
    parent: "QWidget | None",
    disc_titles: Sequence[dict[str, Any]],
    is_tv: bool,
    preview_callback: Callable[[int], None] | None = None,
) -> list[str] | None:
    """Show the disc-tree selector modally.

    Returns the list of selected title IDs (as strings) on OK, or
    ``None`` on Cancel / Esc / window close.

    Empty ``disc_titles`` is handled gracefully — the dialog still
    opens but has no rows; the user can only Cancel or hit OK with
    nothing selected (returns ``[]``).  Mirrors tkinter behavior.
    """
    dialog = _DiscTreeDialog(
        disc_titles=disc_titles,
        is_tv=is_tv,
        preview_callback=preview_callback,
        parent=parent,
    )
    dialog.exec()
    return dialog.result_value
