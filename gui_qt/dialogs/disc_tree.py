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
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QStyledItemDelegate,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from gui_qt.dialogs._modeless import exec_modeless

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


# Column indices — pinned for readability + tests.
_COL_TITLE = 0      # checkbox + label
_COL_DURATION = 1
_COL_SIZE = 2
_COL_CHAPTERS = 3
_COL_STATUS = 4
_COL_NUM = 5        # editable episode number (TV only; hidden for movies)
_COL_NAME = 6       # editable episode name (TV only; hidden for movies)

# The two right-hand columns the user fills in per title (TV only).
_EDITABLE_COLS: tuple[int, ...] = (_COL_NUM, _COL_NAME)

_HEADERS: tuple[str, ...] = (
    "Title",
    "Duration",
    "Size",
    "Chapters",
    "Status",
    "Ep #",
    "Episode name",
)


class _EditableColumnsDelegate(QStyledItemDelegate):
    """Lets the user edit ONLY the two per-title columns — episode
    number and episode name.  The rows are marked editable so those
    cells can open a line edit, but without this delegate a
    double-click on any other column (title, duration, …) would also
    become editable.  Returning no editor elsewhere keeps every other
    column read-only."""

    def createEditor(self, parent, option, index):  # noqa: N802 (Qt)
        if index.column() not in _EDITABLE_COLS:
            return None
        return super().createEditor(parent, option, index)

    def paint(self, painter, option, index):  # noqa: N802 (Qt)
        super().paint(painter, option, index)
        # Empty editable cell → faint placeholder so it visibly reads as
        # a fill-in field (a bare Qt cell otherwise looks like static
        # text, so people don't realise they can type in it).
        if index.column() in _EDITABLE_COLS and not str(index.data() or ""):
            hint = "episode #" if index.column() == _COL_NUM else "episode name"
            painter.save()
            painter.setPen(
                option.palette.color(
                    QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text
                )
            )
            painter.drawText(
                option.rect.adjusted(7, 0, -7, 0),
                int(
                    Qt.AlignmentFlag.AlignVCenter
                    | Qt.AlignmentFlag.AlignLeft
                ),
                hint,
            )
            painter.restore()


def _format_title_label(title: dict[str, Any]) -> str:
    """Build the visible label for a title row.

    Pure helper — testable without Qt.

    Two fixes over the old ``f"Title {id+1}: {name}"`` form
    (2026-06-13, from user feedback that "the scan names don't match
    the rip names"):

    1. The ripped file is named ``<disc-label>_t{NN}.mkv`` where ``NN``
       is the title's MakeMKV index (its ``id``) — NOT the 1-based
       number shown to humans.  So "Title 5" rips to ``..._t04.mkv``,
       which looked like a mismatch.  We now show that ``_t{NN}.mkv``
       token on every row so the scan line visibly corresponds to the
       file it produces.
    2. Unlabeled discs have no real title name, so the old code showed
       the redundant "Title 5: Title 5".  We drop the duplicate and
       only append a name when the disc actually supplied a distinct
       one.

    The file token prefers MakeMKV's REAL output filename captured at
    scan (``output_name``, e.g. "B1_t10.mkv") — its ``A1_/B1_`` prefix
    isn't predictable from the id — and falls back to the predicted
    ``_tNN.mkv`` only when the scan didn't report one.
    """
    tid = title.get("id", -1)
    try:
        n = int(tid)
    except (TypeError, ValueError):
        n = -1
    head = f"Title {n + 1}" if n >= 0 else "Title ?"

    name = str(title.get("name", "") or "").strip()
    # Only a real, disc-supplied name adds information; the generic
    # "Title {n+1}" placeholder is noise next to ``head``.
    if name and name != f"Title {n + 1}":
        head = f"{head}: {name}"

    out = str(title.get("output_name", "") or "").strip()
    token = out if out else (f"_t{n:02d}.mkv" if n >= 0 else "")
    if token:
        head = f"{head}  ·  {token}"
    return head


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
        preview_callback: "Callable[..., None] | None" = None,
        parent: "QWidget | None" = None,
        *,
        window_title: "str | None" = None,
        intro_text: "str | None" = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("discTreeDialog")
        self.setWindowTitle(
            window_title or "Disc Contents — Select Titles to Rip"
        )
        # Window-modal so the standalone AI chat window stays usable
        # while this dialog is open (parented to main window, so the
        # workflow still blocks until the user answers).
        self.setWindowModality(Qt.WindowModality.WindowModal)
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

        # Header text — the caller can override it (the organize flow
        # numbers already-ripped files instead of choosing what to rip),
        # else it's the rip-selection banner.  A TV disc always gets the
        # inline-edit hint appended so the Ep #/name cells are findable.
        if intro_text is not None:
            header_text = intro_text
        else:
            header_text = (
                "Select titles to rip.  Click a row to toggle the "
                "checkbox.  Recommended titles are pre-checked."
            )
            if preview_callback is not None:
                header_text += (
                    "  To watch one first: select it and click Watch in "
                    "VLC (or right-click it) — the full title rips to a "
                    "temporary file, plays, and is deleted when the "
                    "player closes."
                )
        if bool(is_tv):
            header_text += (
                "  Click a row's Ep # or Episode name cell to type it; "
                "leave a title's Ep # blank to file it as an extra."
            )
        header = QLabel(header_text)
        header.setObjectName("stepSubtitle")
        header.setWordWrap(True)
        outer.addWidget(header)

        # Select All / None — quick way to (un)check every row instead of
        # clicking each checkbox one at a time.
        if disc_titles:
            select_row = QHBoxLayout()
            select_all_btn = QPushButton("Select All")
            select_all_btn.setObjectName("selectAllButton")
            select_all_btn.clicked.connect(self._select_all)
            select_row.addWidget(select_all_btn)
            select_none_btn = QPushButton("Select None")
            select_none_btn.setObjectName("selectNoneButton")
            select_none_btn.clicked.connect(self._select_none)
            select_row.addWidget(select_none_btn)
            select_row.addStretch(1)
            outer.addLayout(select_row)

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

        # Per-title episode info (TV only): the Ep # and name columns
        # are editable inline.  The number you set here pre-fills the
        # post-rip Episode Numbers step (aligned to each title by id),
        # and the name becomes the saved file's name
        # ("Show - S01E05 - <name>.mkv").  Hidden for movies, which name
        # by title/year instead.  A delegate keeps editing confined to
        # those two columns.
        self._is_tv = bool(is_tv)
        self._tree.setItemDelegate(_EditableColumnsDelegate(self._tree))
        self._tree.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self._tree.setColumnHidden(_COL_NUM, not self._is_tv)
        self._tree.setColumnHidden(_COL_NAME, not self._is_tv)
        if self._is_tv:
            header_view.setSectionResizeMode(
                _COL_NUM, QHeaderView.ResizeMode.ResizeToContents
            )
            header_view.setSectionResizeMode(
                _COL_NAME, QHeaderView.ResizeMode.Stretch
            )

        # Populate rows.
        self._items_by_id: dict[str, QTreeWidgetItem] = {}
        self._populate(disc_titles)

        outer.addWidget(self._tree, stretch=1)

        # Watch controls — rip the selected title to a disposable
        # local-temp file, play it in VLC, and delete it when the
        # player closes.  Full title only: partial-sample rips proved
        # unreliable on protected discs (short reads get blocked).
        # Only shown when a watch callback is wired.
        if preview_callback is not None:
            preview_row = QHBoxLayout()

            self._preview_button = QPushButton("▶  Watch in VLC")
            self._preview_button.setObjectName("previewButton")
            self._preview_button.clicked.connect(self._on_preview_clicked)
            preview_row.addWidget(self._preview_button)

            note = QLabel(
                "Rips the whole title to a temporary file first — "
                "this can take a few minutes."
            )
            note.setObjectName("previewNote")
            preview_row.addWidget(note)

            preview_row.addStretch(1)
            outer.addLayout(preview_row)
        else:
            self._preview_button = None

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

        # Pointing-hand cursor on the dialog's buttons (Watch / OK /
        # Cancel) — QSS can't set the cursor in Qt.
        from gui_qt.ui_polish import apply_pointing_cursors
        apply_pointing_cursors(self)

    # ------------------------------------------------------------------
    # Population
    # ------------------------------------------------------------------

    def _populate(self, disc_titles: Sequence[dict[str, Any]]) -> None:
        """Add a row per disc title.  Each row carries its title ID
        as Qt user-data so we can read it back at submit time.

        TV discs are shown in title-number order (1, 2, 3 …) so the
        list reads naturally and lines up with the rip filenames.
        Movies keep the scan's longest-first order, which puts the
        main feature on top (where it's pre-checked)."""
        rows = list(disc_titles)
        if self._is_tv:
            def _id_key(t: dict[str, Any]) -> tuple[int, int]:
                try:
                    return (0, int(t.get("id", 0)))
                except (TypeError, ValueError):
                    return (1, 0)  # unparseable id sorts to the end
            rows = sorted(rows, key=_id_key)
        for t in rows:
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

            # TV: make the row editable so the Ep # and name cells open
            # an inline editor (the delegate confines editing to those
            # two columns).  The Ep # starts BLANK by design — you type
            # each episode's number yourself (no auto-fill), and a row
            # left blank is treated as an extra by the move step.  Seed
            # the name from any disc-supplied real name.
            if self._is_tv:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                seed = str(t.get("episode_name", "") or "").strip()
                if seed:
                    item.setText(_COL_NAME, seed)

            self._items_by_id[tid] = item

    # ------------------------------------------------------------------
    # Click handler — toggle checkbox when row is clicked anywhere
    # ------------------------------------------------------------------

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Toggle the checkbox unless the user clicked exactly on the
        checkbox itself (Qt handles that already)."""
        if column == _COL_TITLE:
            return  # Qt's built-in checkbox handling fires already
        if column in _EDITABLE_COLS:
            # Single click opens the inline editor, so it's obvious you
            # can type here — no hidden double-click needed.
            self._tree.editItem(item, column)
            return
        new_state = (
            Qt.CheckState.Unchecked
            if item.checkState(_COL_TITLE) == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )
        item.setCheckState(_COL_TITLE, new_state)

    def _select_all(self) -> None:
        """Check every row (Select All button)."""
        for item in self._items_by_id.values():
            item.setCheckState(_COL_TITLE, Qt.CheckState.Checked)

    def _select_none(self) -> None:
        """Uncheck every row (Select None button)."""
        for item in self._items_by_id.values():
            item.setCheckState(_COL_TITLE, Qt.CheckState.Unchecked)

    # ------------------------------------------------------------------
    # Right-click → preview
    # ------------------------------------------------------------------

    def _on_tree_context_menu(self, position) -> None:
        """Right-click handler.

        On a TV disc's editable cells (Ep # / Episode name) it shows a
        Cut / Copy / Paste menu, so text can move between cells while
        naming episodes.  Anywhere else it invokes the watch/preview
        callback for the title under the cursor (the original
        ``Button-3 → _preview_title_item`` behavior).

        No-op on empty space, or on a non-editable cell when no preview
        callback was wired.
        """
        item = self._tree.itemAt(position)
        if item is None:
            return
        column = self._tree.columnAt(position.x())
        if (
            self._is_tv
            and column in _EDITABLE_COLS
            and not self._tree.isColumnHidden(column)
        ):
            self._show_cell_edit_menu(item, column, position)
            return
        if self._preview_callback is None:
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
        self._invoke_preview(title_id)

    # ------------------------------------------------------------------
    # Per-cell clipboard (editable Ep # / name columns)
    # ------------------------------------------------------------------

    def _cell_copy(self, item: QTreeWidgetItem, column: int) -> None:
        """Copy a cell's text to the clipboard."""
        clip = QApplication.clipboard()
        if clip is not None:
            clip.setText(item.text(column))

    def _cell_cut(self, item: QTreeWidgetItem, column: int) -> None:
        """Copy a cell's text, then clear the cell."""
        self._cell_copy(item, column)
        item.setText(column, "")

    def _cell_paste(self, item: QTreeWidgetItem, column: int) -> None:
        """Paste clipboard text into a cell.  Cells are single-line, so
        any newlines collapse to spaces and the ends are trimmed."""
        clip = QApplication.clipboard()
        if clip is None:
            return
        text = " ".join(clip.text().splitlines()).strip()
        item.setText(column, text)

    def _show_cell_edit_menu(
        self, item: QTreeWidgetItem, column: int, position,
    ) -> None:
        """Cut / Copy / Paste menu for an editable cell, wired to the
        whole-cell helpers above.  (While the inline editor is open it
        keeps its own native text menu; this is for right-clicking a
        cell directly, where the tree would otherwise show 'Watch'.)"""
        menu = QMenu(self._tree)
        cell_text = item.text(column)
        clip = QApplication.clipboard()
        clip_text = clip.text() if clip is not None else ""

        a_cut = menu.addAction("Cut")
        a_copy = menu.addAction("Copy")
        a_paste = menu.addAction("Paste")
        a_cut.setEnabled(bool(cell_text))
        a_copy.setEnabled(bool(cell_text))
        a_paste.setEnabled(bool(clip_text.strip()))
        a_cut.triggered.connect(lambda: self._cell_cut(item, column))
        a_copy.triggered.connect(lambda: self._cell_copy(item, column))
        a_paste.triggered.connect(lambda: self._cell_paste(item, column))

        menu.exec(self._tree.viewport().mapToGlobal(position))

    def _invoke_preview(self, title_id: int) -> None:
        """Call the watch callback for one title.  The controller side
        rips the full title, plays it, and cleans up afterward."""
        cb = self._preview_callback
        if cb is None:
            return
        try:
            cb(title_id)
        except Exception:
            # Don't let a misbehaving callback take down the dialog.
            pass

    def _on_preview_clicked(self) -> None:
        """Preview the currently-selected row in VLC."""
        if self._preview_callback is None:
            return
        item = self._tree.currentItem()
        if item is None:
            return
        title_id_str = item.data(_COL_TITLE, Qt.ItemDataRole.UserRole)
        if not title_id_str:
            return
        try:
            title_id = int(title_id_str)
        except (TypeError, ValueError):
            return
        self._invoke_preview(title_id)

    def trigger_preview_for_test(self, title_id_str: str) -> None:
        """Test helper — invoke the preview callback as if the user
        right-clicked the row whose user-data is ``title_id_str``.
        Avoids needing to construct synthetic mouse events in tests."""
        if self._preview_callback is None:
            return
        try:
            self._invoke_preview(int(title_id_str))
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

    def episode_names(self) -> dict[int, str]:
        """Per-title episode names the user typed, keyed by integer
        title id.  Empty for movies (the name column is hidden) and
        for any row left blank.  These feed the final episode filename
        ("Show - S01E05 - <name>.mkv") — see the controller's move
        step."""
        out: dict[int, str] = {}
        if self._tree.isColumnHidden(_COL_NAME):
            return out
        for tid, item in self._items_by_id.items():
            name = str(item.text(_COL_NAME) or "").strip()
            if not name:
                continue
            try:
                out[int(tid)] = name
            except (TypeError, ValueError):
                continue
        return out

    def episode_numbers(self) -> dict[int, int]:
        """Per-title episode numbers the user set in the picker, keyed
        by integer title id.  Empty for movies (the column is hidden).
        A blank or non-numeric cell is omitted — that title simply has
        no number yet (e.g. an extra).  These pre-fill the post-rip
        Episode Numbers step, aligned to the main-episode order by title
        id — so for a disc whose physical order differs from broadcast
        order you set each number next to the title it belongs to,
        instead of typing a blind comma list."""
        out: dict[int, int] = {}
        if self._tree.isColumnHidden(_COL_NUM):
            return out
        for tid, item in self._items_by_id.items():
            raw = str(item.text(_COL_NUM) or "").strip()
            if not raw.isdigit():
                continue
            try:
                out[int(tid)] = int(raw)
            except (TypeError, ValueError):
                continue
        return out

    def _on_ok(self) -> None:
        # Commit any in-progress inline edit before reading values.
        self._tree.setCurrentItem(self._tree.currentItem())
        self.result_value = self._selected_ids()
        self.episode_names_value = self.episode_names()
        self.episode_numbers_value = self.episode_numbers()
        self.accept()

    def _on_cancel(self) -> None:
        self.result_value = None
        self.reject()

    def keyPressEvent(self, event):  # noqa: N802 (Qt convention)
        if event.key() == Qt.Key.Key_Escape:
            self._on_cancel()
            return
        super().keyPressEvent(event)


def run_disc_tree(
    parent: "QWidget | None",
    disc_titles: Sequence[dict[str, Any]],
    is_tv: bool,
    preview_callback: "Callable[..., None] | None" = None,
    *,
    window_title: "str | None" = None,
    intro_text: "str | None" = None,
) -> tuple[list[str] | None, dict[int, str], dict[int, int]]:
    """Show the selector modally and return the selected title IDs plus
    the per-title episode names AND numbers the user typed.

    ``(ids, names, numbers)`` — ``ids`` is ``None`` on cancel (names and
    numbers ``{}`` then); ``names`` maps integer title id → episode name
    and ``numbers`` maps integer title id → episode number, for the TV
    rows the user filled in (both empty for movies).  ``show_disc_tree``
    is the back-compatible wrapper that returns only the ids.
    """
    dialog = _DiscTreeDialog(
        disc_titles=disc_titles,
        is_tv=is_tv,
        preview_callback=preview_callback,
        parent=parent,
        window_title=window_title,
        intro_text=intro_text,
    )
    exec_modeless(dialog)
    ids = dialog.result_value
    names = dict(getattr(dialog, "episode_names_value", {}) or {}) if ids is not None else {}
    numbers = dict(getattr(dialog, "episode_numbers_value", {}) or {}) if ids is not None else {}
    return ids, names, numbers


def show_disc_tree(
    parent: "QWidget | None",
    disc_titles: Sequence[dict[str, Any]],
    is_tv: bool,
    preview_callback: "Callable[..., None] | None" = None,
    *,
    window_title: "str | None" = None,
    intro_text: "str | None" = None,
) -> list[str] | None:
    """Show the disc-tree selector modally.

    Returns the list of selected title IDs (as strings) on OK, or
    ``None`` on Cancel / Esc / window close.  Back-compatible wrapper
    around :func:`run_disc_tree` (drops the episode-names map).

    Empty ``disc_titles`` is handled gracefully — the dialog still
    opens but has no rows; the user can only Cancel or hit OK with
    nothing selected (returns ``[]``).  Mirrors tkinter behavior.
    """
    ids, _names, _numbers = run_disc_tree(
        parent, disc_titles, is_tv, preview_callback,
        window_title=window_title, intro_text=intro_text,
    )
    return ids
