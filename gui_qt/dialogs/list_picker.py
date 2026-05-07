"""List-picker dialogs — ``show_extras_picker`` and ``show_file_list``.

Both are multi-select listboxes with Select All / Deselect All
helpers, used by the controller's bulk-selection workflows.  They
look the same on screen but differ in three ways the controller
relies on:

================  ================  =================  ==============
Dialog            Pre-selection     Return value       Cancel value
================  ================  =================  ==============
extras_picker     all items         list[int] indices  None
file_list         first item only   list[str] texts    []
================  ================  =================  ==============

Mirrors tkinter at ``gui/main_window.py:5858`` (extras_picker) and
``gui/main_window.py:5788`` (file_list).

Controller usage:

* ``show_extras_picker`` returns ``None`` on cancel — controller does
  ``if chosen is None: ... cancelled`` (controller.py:2712).
* ``show_file_list`` returns ``[]`` on cancel — controller does
  ``if not selected: ... cancelled`` (controller.py:3855).  Empty
  list and cancel are indistinguishable at the call site, by design.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


# Mode literals — internal to this module.
_PreselectMode = Literal["all", "first"]
_ReturnMode = Literal["indices", "texts"]


class _ListPickerDialog(QDialog):
    """Multi-select listbox with header + helper buttons.

    Used by both ``show_extras_picker`` and ``show_file_list``; the
    differences are encoded in the ``preselect`` and ``return_mode``
    arguments.
    """

    def __init__(
        self,
        title: str,
        prompt: str,
        options: Sequence[str],
        *,
        preselect: _PreselectMode,
        return_mode: _ReturnMode,
        parent: "QWidget | None" = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("listPickerDialog")
        self.setWindowTitle(title or "Select")
        self.setModal(True)
        self.resize(640, 480)

        self._return_mode = return_mode
        # ``confirmed`` distinguishes Confirm from cancel/close.  The
        # caller (the public functions below) reads ``selection_indices``
        # / ``selection_texts`` only when ``confirmed`` is True; on
        # cancel they get the per-dialog cancel value (None or []).
        self.confirmed = False
        self.selection_indices: list[int] = []
        self.selection_texts: list[str] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 14)
        outer.setSpacing(8)

        # Prompt — word-wrapped (tkinter uses wraplength=500).
        prompt_label = QLabel(prompt or "")
        prompt_label.setObjectName("stepSubtitle")
        prompt_label.setWordWrap(True)
        outer.addWidget(prompt_label)

        # The list itself — multi-select via the standard extended
        # mode (Shift / Ctrl click ranges).
        self._list = QListWidget()
        self._list.setObjectName("listPickerList")
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        for opt in options:
            QListWidgetItem(str(opt), self._list)

        # Pre-selection per mode.
        if self._list.count() > 0:
            if preselect == "all":
                for i in range(self._list.count()):
                    self._list.item(i).setSelected(True)
            elif preselect == "first":
                self._list.item(0).setSelected(True)

        outer.addWidget(self._list, stretch=1)

        # Helper button row — Select All / Deselect All.
        helper_row = QHBoxLayout()

        select_all = QPushButton("Select All")
        select_all.setObjectName("listPickerSelectAll")
        select_all.clicked.connect(self._select_all)
        helper_row.addWidget(select_all)

        deselect_all = QPushButton("Deselect All")
        deselect_all.setObjectName("listPickerDeselectAll")
        deselect_all.clicked.connect(self._deselect_all)
        helper_row.addWidget(deselect_all)

        helper_row.addStretch(1)
        outer.addLayout(helper_row)

        # Confirm + Cancel.
        button_row = QHBoxLayout()

        cancel = QPushButton("Cancel")
        cancel.setObjectName("cancelButton")
        cancel.clicked.connect(self._on_cancel)
        button_row.addWidget(cancel)

        button_row.addStretch(1)

        confirm = QPushButton("Confirm")
        confirm.setObjectName("confirmButton")
        confirm.setDefault(True)
        confirm.clicked.connect(self._on_confirm)
        button_row.addWidget(confirm)

        outer.addLayout(button_row)

        # Cache for tests.
        self._select_all_btn = select_all
        self._deselect_all_btn = deselect_all
        self._confirm_btn = confirm
        self._cancel_btn = cancel

    # ------------------------------------------------------------------
    # Helper-button handlers
    # ------------------------------------------------------------------

    def _select_all(self) -> None:
        for i in range(self._list.count()):
            self._list.item(i).setSelected(True)

    def _deselect_all(self) -> None:
        self._list.clearSelection()

    # ------------------------------------------------------------------
    # Confirm / cancel / Esc
    # ------------------------------------------------------------------

    def _gather_selection(self) -> None:
        """Walk the list and collect indices + texts."""
        self.selection_indices = []
        self.selection_texts = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.isSelected():
                self.selection_indices.append(i)
                self.selection_texts.append(item.text())

    def _on_confirm(self) -> None:
        self._gather_selection()
        self.confirmed = True
        self.accept()

    def _on_cancel(self) -> None:
        self.confirmed = False
        self.reject()

    def keyPressEvent(self, event):  # noqa: N802 (Qt convention)
        if event.key() == Qt.Key.Key_Escape:
            self._on_cancel()
            return
        super().keyPressEvent(event)


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def show_extras_picker(
    parent: "QWidget | None",
    title: str,
    prompt: str,
    options: Sequence[str],
) -> list[int] | None:
    """Multi-select picker, all items pre-selected.  Returns the
    list of selected 0-based indices on confirm, or ``None`` on
    cancel / close.

    Mirrors ``gui/main_window.py:5858``.
    """
    dialog = _ListPickerDialog(
        title=title,
        prompt=prompt,
        options=options,
        preselect="all",
        return_mode="indices",
        parent=parent,
    )
    dialog.exec()
    if not dialog.confirmed:
        return None
    return list(dialog.selection_indices)


def show_file_list(
    parent: "QWidget | None",
    title: str,
    prompt: str,
    options: Sequence[str],
) -> list[str]:
    """Multi-select picker, first item pre-selected.  Returns the
    list of selected texts on confirm, or ``[]`` (empty list) on
    cancel / close.  Pinned: empty list and cancel are intentionally
    indistinguishable — the controller's call site uses
    ``if not selected`` to detect both.

    Mirrors ``gui/main_window.py:5788``.
    """
    dialog = _ListPickerDialog(
        title=title,
        prompt=prompt,
        options=options,
        preselect="first",
        return_mode="texts",
        parent=parent,
    )
    dialog.exec()
    if not dialog.confirmed:
        return []
    return list(dialog.selection_texts)
