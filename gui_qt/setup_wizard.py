"""Setup wizard — PySide6 port (sub-phase 3b, in progress).

Public API mirrors ``gui/setup_wizard.py`` so the controller can
swap implementations without changing call sites.

**Port status (2026-05-03 — Phase 3b complete)**:

- Step 1 ``show_scan_results`` — **PORTED**
- Step 3 ``show_content_mapping`` — **PORTED**
- Step 4 ``show_extras_classification`` — **PORTED**
- Step 5 ``show_output_plan`` — **PORTED**

See ``docs/handoffs/phase-3b-port-setup-wizard.md`` for the per-step
plan and ``STATUS.md`` for current state.

**Theming hooks** — every styled widget gets a ``setObjectName`` so
QSS files in ``gui_qt/qss/`` can target it.  Don't bake colors
into Python here.  The 6 themes for these object names landed in
sub-phase 3a-themes (also 2026-05-03) and are generated from
``gui_qt/themes.py`` via ``tools/build_qss.py``.
"""

from __future__ import annotations

import os
from typing import Any, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

# Phase 3h (2026-05-04) — these types moved out of the tkinter
# ``gui/`` package into a neutral home so the wizard could shed its
# last source-level dependency on tkinter.  They're pure Python; no
# GUI-toolkit coupling.
from shared.wizard_types import (
    ContentSelection,
    ExtrasAssignment,
    JELLYFIN_EXTRAS_CATEGORIES,
    OutputPlan,
    build_output_tree,
    _format_duration,
    _format_size,
    _label_display,
)

__all__ = [
    "ContentSelection",
    "ExtrasAssignment",
    "JELLYFIN_EXTRAS_CATEGORIES",
    "OutputPlan",
    "build_output_tree",
    "show_scan_results",
    "show_content_mapping",
    "show_extras_classification",
    "show_output_plan",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _add_section_header(layout: QVBoxLayout, text: str) -> None:
    """Section divider + label, matching the tkinter _section_header
    visual.  The QSS file controls colors and spacing; Python only
    sets structure and ``objectName`` hooks."""
    divider = QFrame()
    divider.setObjectName("sectionDivider")
    divider.setFrameShape(QFrame.Shape.HLine)
    divider.setFixedHeight(1)
    layout.addWidget(divider)

    header = QLabel(text)
    header.setObjectName("sectionHeader")
    layout.addWidget(header)


def _build_output_tree_widget(
    base_folder: str,
    main_label: str,
    extras_map: dict[str, list[str]],
) -> QTreeWidget:
    """Build a navigable folder-structure tree for the OutputPlan
    dialog.

    Replaces the previous ``QPlainTextEdit`` rendering of
    ``build_output_tree`` lines (kept around as a re-exported
    helper for callers that want a flat-text representation —
    e.g., session-summary logs).  The hierarchy is fixed at three
    levels:

    * Library-parent folder (e.g., ``Movies/``)
    * The actual movie/show folder (e.g., ``Inception (2010)/``)
    * Main file + per-category subfolders, with files under each

    Empty extras categories are omitted — same rule the flat-text
    builder uses, pinned by ``test_build_output_tree_omits_empty_extras_categories``.
    """
    root_name = os.path.basename(base_folder)
    parent_name = os.path.basename(os.path.dirname(base_folder))

    tree = QTreeWidget()
    tree.setObjectName("outputTreeView")
    tree.setHeaderHidden(True)
    tree.setRootIsDecorated(True)
    tree.setIndentation(20)
    # Items aren't user-editable — the dialog is informational.
    # ``QTreeWidgetItem``'s default flags don't include
    # ``ItemIsEditable`` so explicit handling isn't needed; the
    # ``test_tree_view_items_not_editable`` test pins the contract.

    parent_item = QTreeWidgetItem(tree, [f"{parent_name}/"])
    folder_item = QTreeWidgetItem(parent_item, [f"{root_name}/"])
    QTreeWidgetItem(folder_item, [main_label])

    for category in sorted(extras_map.keys()):
        files = extras_map[category]
        if not files:
            continue
        category_item = QTreeWidgetItem(folder_item, [f"{category}/"])
        for f in files:
            QTreeWidgetItem(category_item, [f])

    # Expand everything by default — the dialog is informational,
    # and the user wants to see the full structure at a glance
    # without having to click each disclosure arrow.
    tree.expandAll()

    # Cap the visible height so dialogs don't grow unbounded for
    # movies with many extras.  Approximate row height is ~22px;
    # show up to 20 rows then scroll.  Counting visible rows after
    # ``expandAll`` requires walking the tree — close enough to
    # estimate from the underlying data.
    visible_rows = (
        2  # parent + folder
        + 1  # main file
        + sum(1 + len(files) for files in extras_map.values() if files)
    )
    cap = min(20, visible_rows + 1)
    tree.setFixedHeight(max(80, cap * 22))

    return tree


def _summary_text_for(classified: list) -> str:
    """Build the summary line ('N titles  M main  K extras  ...').
    Pure function — no Qt dependency.  Pinned by tests so the line
    shape matches the tkinter implementation exactly."""
    main_count = sum(1 for ct in classified if ct.label == "MAIN")
    extra_count = sum(1 for ct in classified if ct.label == "EXTRA")
    dup_count = sum(1 for ct in classified if ct.label == "DUPLICATE")
    unknown_count = sum(1 for ct in classified if ct.label == "UNKNOWN")

    parts = [f"{len(classified)} titles"]
    if main_count:
        parts.append(f"{main_count} main")
    if extra_count:
        parts.append(f"{extra_count} extras")
    if dup_count:
        parts.append(f"{dup_count} duplicates")
    if unknown_count:
        parts.append(f"{unknown_count} unknown")
    return "  ".join(parts)


# ---------------------------------------------------------------------------
# Step 1 — Scan Results
# ---------------------------------------------------------------------------


class _ScanResultsDialog(QDialog):
    """Dialog implementing Step 1 of the setup wizard.

    Encapsulated as a class so tests can construct without running
    the modal event loop, inspect the widgets, and simulate button
    clicks via ``qtbot``.
    """

    def __init__(
        self,
        classified: list,
        drive_info: dict | None,
        parent: QWidget | None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("wizardDialog")
        self.setWindowTitle("Scan Results")
        self.setModal(True)

        # Result populated when the user clicks a media-type button
        # or cancels.  None means cancelled.
        self.result_value: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 14)

        # ---- Header ----
        title = QLabel("Step 1: Scan Results")
        title.setObjectName("stepHeader")
        root.addWidget(title)

        subtitle = QLabel(
            "JellyRip has scanned and classified the disc titles."
        )
        subtitle.setObjectName("stepSubtitle")
        root.addWidget(subtitle)

        # ---- Drive info (conditional) ----
        if drive_info:
            self._build_drive_info_section(root, drive_info)

        # ---- Classified titles list ----
        _add_section_header(root, "CLASSIFIED TITLES")
        self._build_titles_list(root, classified)

        # ---- Summary ----
        summary = QLabel(_summary_text_for(classified))
        summary.setObjectName("scanSummaryRow")
        root.addWidget(summary)

        # ---- Media-type selection ----
        _add_section_header(root, "WHAT IS THIS DISC?")
        self._build_media_type_buttons(root)

        # ---- Cancel ----
        cancel_divider = QFrame()
        cancel_divider.setObjectName("sectionDivider")
        cancel_divider.setFrameShape(QFrame.Shape.HLine)
        cancel_divider.setFixedHeight(1)
        root.addWidget(cancel_divider)

        cancel_row = QHBoxLayout()
        cancel_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("cancelButton")
        cancel_btn.clicked.connect(self._cancel)
        cancel_row.addWidget(cancel_btn)
        cancel_row.addStretch(1)
        root.addLayout(cancel_row)

    def _build_drive_info_section(
        self,
        layout: QVBoxLayout,
        drive_info: dict,
    ) -> None:
        disc_type = drive_info.get("disc_type")
        libre = drive_info.get("libre_drive")
        if not (disc_type or libre):
            return
        _add_section_header(layout, "DRIVE STATUS")

        if disc_type:
            disc_label = QLabel(f"Disc type: {disc_type}")
            disc_label.setObjectName("driveDiscType")
            layout.addWidget(disc_label)

        # LibreDrive status with inline gloss — closes Finding #11.
        # Object names per state so QSS can color them differently
        # (green/amber/red) without putting colors in Python.
        if libre == "enabled":
            text = "LibreDrive: enabled — disc decryption ready"
            obj = "libreEnabled"
        elif libre == "possible":
            text = "LibreDrive: possible — firmware patch may help"
            obj = "librePossible"
        elif libre == "unavailable":
            text = "LibreDrive: not available — UHD discs may not work"
            obj = "libreUnavailable"
        else:
            return

        libre_label = QLabel(text)
        libre_label.setObjectName(obj)
        layout.addWidget(libre_label)

    def _build_titles_list(
        self,
        layout: QVBoxLayout,
        classified: list,
    ) -> None:
        """Scrollable list of classified titles.  Qt's QScrollArea
        replaces the manual tkinter Canvas+Scrollbar pattern (much
        simpler)."""
        scroll = QScrollArea()
        scroll.setObjectName("classifiedTitlesScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Cap height around 8 rows so the dialog doesn't grow huge
        # on large discs.  Each row ~28-32px in default styling.
        scroll.setMinimumHeight(min(340, max(60, len(classified) * 32)))

        body = QWidget()
        body.setObjectName("classifiedTitlesBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(2)

        for ct in classified:
            row = self._build_title_row(ct)
            body_layout.addWidget(row)
        body_layout.addStretch(1)
        scroll.setWidget(body)
        layout.addWidget(scroll)

    def _build_title_row(self, ct) -> QWidget:
        """One title row — label + confidence + title # + dur/size +
        status + reason.  Each cell gets an objectName so QSS can
        target columns individually for alignment / typography."""
        row = QWidget()
        row.setObjectName("classifiedTitleRow")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(4, 2, 4, 2)
        row_layout.setSpacing(8)

        # Classification label (e.g. "Main", "Duplicate", "Extra").
        # Object name carries the label so QSS can color by category
        # without Python knowing the colors.
        label_cell = QLabel(_label_display(ct.label))
        label_cell.setObjectName(f"classificationLabel_{ct.label}")
        label_cell.setMinimumWidth(96)
        row_layout.addWidget(label_cell)

        # Confidence percent
        pct = int(ct.confidence * 100)
        pct_cell = QLabel(f"{pct}%")
        pct_cell.setObjectName("confidencePercent")
        pct_cell.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        pct_cell.setMinimumWidth(40)
        row_layout.addWidget(pct_cell)

        # Title number
        title_cell = QLabel(f"Title {ct.title_id + 1}")
        title_cell.setObjectName("titleNumber")
        title_cell.setMinimumWidth(80)
        row_layout.addWidget(title_cell)

        # Duration + size
        dur = _format_duration(
            float(ct.title.get("duration_seconds", 0) or 0)
        )
        size = _format_size(float(ct.title.get("size_bytes", 0) or 0))
        ds_cell = QLabel(f"{dur}  {size}")
        ds_cell.setObjectName("durationSize")
        row_layout.addWidget(ds_cell)

        # Status text (recommended / not recommended)
        status_cell = QLabel(ct.status_text)
        status_cell.setObjectName(
            "titleStatusRecommended"
            if ct.recommended
            else "titleStatus"
        )
        row_layout.addWidget(status_cell)

        # Why text — kept dim so it doesn't compete with status
        why_cell = QLabel(ct.why_text)
        why_cell.setObjectName("titleReason")
        row_layout.addWidget(why_cell, stretch=1)

        return row

    def _build_media_type_buttons(self, layout: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(12)

        movie_btn = QPushButton("Movie")
        movie_btn.setObjectName("primaryButton")
        movie_btn.clicked.connect(lambda: self._select("movie"))
        row.addWidget(movie_btn)

        tv_btn = QPushButton("TV Show")
        tv_btn.setObjectName("primaryButton")
        tv_btn.clicked.connect(lambda: self._select("tv"))
        row.addWidget(tv_btn)

        std_btn = QPushButton("Standard")
        std_btn.setObjectName("secondaryButton")
        std_btn.clicked.connect(lambda: self._select("standard"))
        row.addWidget(std_btn)

        row.addStretch(1)
        layout.addLayout(row)

    def _select(self, media_type: str) -> None:
        self.result_value = media_type
        self.accept()

    def _cancel(self) -> None:
        self.result_value = None
        self.reject()

    def keyPressEvent(self, event):  # noqa: N802 (Qt convention)
        # Esc cancels — matches the tkinter behavior.
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()
            return
        super().keyPressEvent(event)


def show_scan_results(
    parent: Any,
    classified: list,
    drive_info: dict | None = None,
) -> str | None:
    """Show scan + classification results modally.

    Returns ``"movie"``, ``"tv"``, ``"standard"``, or ``None`` if
    the user cancelled.  Mirrors the tkinter version's signature
    and return shape exactly so the controller doesn't need to know
    which implementation it's calling.
    """
    dialog = _ScanResultsDialog(
        classified=classified,
        drive_info=drive_info,
        parent=parent if isinstance(parent, QWidget) else None,
    )
    dialog.exec()
    return dialog.result_value


# ---------------------------------------------------------------------------
# Step 3 — Content Mapping
# ---------------------------------------------------------------------------


def _default_check_state_for(ct) -> bool:
    """Pre-check MAIN (recommended titles) and valid EXTRA, leave
    DUPLICATE / UNKNOWN / invalid titles unchecked.  Mirrors
    tkinter's ``ct.recommended or (ct.valid and ct.label == "EXTRA")``.

    Pure function — pinned by tests so the default-on rule stays
    explicit and consistent with the tkinter version.
    """
    return bool(ct.recommended or (ct.valid and ct.label == "EXTRA"))


def _build_content_selection(classified, checked_ids) -> ContentSelection:
    """Aggregate the checked title IDs into a ``ContentSelection``.

    Pure function (no Qt) so the aggregation rule is testable
    without a dialog.  Mirrors the tkinter ``_submit`` logic:

    - Unchecked → skip_title_ids
    - Checked + MAIN → main_title_ids
    - Checked + EXTRA / UNKNOWN → extra_title_ids
    - Checked + DUPLICATE → extra_title_ids (user explicitly opted in)
    """
    main_ids: list[int] = []
    extra_ids: list[int] = []
    skip_ids: list[int] = []
    checked_set = set(int(t) for t in checked_ids)

    for ct in classified:
        tid = ct.title_id
        if tid not in checked_set:
            skip_ids.append(tid)
        elif ct.label == "MAIN":
            main_ids.append(tid)
        else:
            # EXTRA, UNKNOWN, DUPLICATE all land here when checked.
            # The tkinter version explicitly groups DUPLICATE as
            # extras when the user opts in.
            extra_ids.append(tid)

    return ContentSelection(
        main_title_ids=main_ids,
        extra_title_ids=extra_ids,
        skip_title_ids=skip_ids,
    )


class _ContentMappingDialog(QDialog):
    """Dialog implementing Step 3 of the setup wizard.

    Per-row checkboxes determine which titles to rip; submit
    aggregates into a ``ContentSelection``.  Submit refuses if
    nothing is checked (matches tkinter behavior — prevents the
    user accidentally proceeding with zero titles selected).
    """

    def __init__(
        self,
        classified: list,
        parent: QWidget | None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("wizardDialog")
        self.setWindowTitle("Content Mapping")
        self.setModal(True)

        self.result_value: ContentSelection | None = None

        # Track the QCheckBox per title id so submit can read state.
        self._check_boxes: dict[int, QCheckBox] = {}
        self._classified = list(classified)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 14)

        # ---- Header ----
        title = QLabel("Step 3: Content Mapping")
        title.setObjectName("stepHeader")
        root.addWidget(title)

        subtitle = QLabel(
            "Select which titles to rip. "
            "Main is pre-selected; duplicates are unchecked."
        )
        subtitle.setObjectName("stepSubtitle")
        root.addWidget(subtitle)

        # ---- Title list ----
        _add_section_header(root, "TITLES")
        self._build_titles_list(root)

        # ---- Buttons ----
        cancel_divider = QFrame()
        cancel_divider.setObjectName("sectionDivider")
        cancel_divider.setFrameShape(QFrame.Shape.HLine)
        cancel_divider.setFixedHeight(1)
        root.addWidget(cancel_divider)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("cancelButton")
        cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(cancel_btn)

        next_btn = QPushButton("Next  →")
        # confirmButton (green/go semantics, same as Step 5's Start
        # Rip).  QSS targets one objectName for both.
        next_btn.setObjectName("confirmButton")
        next_btn.setDefault(True)  # Enter triggers submit
        next_btn.clicked.connect(self._submit)
        btn_row.addWidget(next_btn)

        btn_row.addStretch(1)
        root.addLayout(btn_row)

    def _build_titles_list(self, layout: QVBoxLayout) -> None:
        scroll = QScrollArea()
        scroll.setObjectName("classifiedTitlesScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMinimumHeight(
            min(380, max(60, len(self._classified) * 36))
        )

        body = QWidget()
        body.setObjectName("classifiedTitlesBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(2)

        for ct in self._classified:
            row = self._build_title_row(ct)
            body_layout.addWidget(row)
        body_layout.addStretch(1)
        scroll.setWidget(body)
        layout.addWidget(scroll)

    def _build_title_row(self, ct) -> QWidget:
        row = QWidget()
        row.setObjectName("contentMappingRow")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(4, 2, 4, 2)
        row_layout.setSpacing(8)

        checkbox = QCheckBox()
        checkbox.setObjectName("contentMappingCheckbox")
        checkbox.setChecked(_default_check_state_for(ct))
        self._check_boxes[ct.title_id] = checkbox
        row_layout.addWidget(checkbox)

        title_cell = QLabel(f"Title {ct.title_id + 1}")
        title_cell.setObjectName("titleNumber")
        title_cell.setMinimumWidth(72)
        row_layout.addWidget(title_cell)

        # Classification label — objectName encodes the category so
        # QSS can color it (same convention as Step 1).
        label_cell = QLabel(_label_display(ct.label))
        label_cell.setObjectName(f"classificationLabel_{ct.label}")
        label_cell.setMinimumWidth(96)
        row_layout.addWidget(label_cell)

        pct_cell = QLabel(f"({int(ct.confidence * 100)}%)")
        pct_cell.setObjectName("confidencePercent")
        pct_cell.setMinimumWidth(48)
        row_layout.addWidget(pct_cell)

        dur = _format_duration(
            float(ct.title.get("duration_seconds", 0) or 0)
        )
        size = _format_size(float(ct.title.get("size_bytes", 0) or 0))
        ds_cell = QLabel(f"{dur}  {size}")
        ds_cell.setObjectName("durationSize")
        row_layout.addWidget(ds_cell)

        status_cell = QLabel(ct.status_text)
        status_cell.setObjectName(
            "titleStatusRecommended"
            if ct.recommended
            else "titleStatus"
        )
        row_layout.addWidget(status_cell)

        why_cell = QLabel(ct.why_text)
        why_cell.setObjectName("titleReason")
        row_layout.addWidget(why_cell, stretch=1)

        # Click anywhere on the row toggles the checkbox — pins
        # the tkinter row-click UX.  Implemented via mousePressEvent
        # on the row widget.
        def _toggle(_event=None, _cb=checkbox):
            _cb.setChecked(not _cb.isChecked())

        row.mousePressEvent = _toggle  # type: ignore[method-assign]

        return row

    def _submit(self) -> None:
        checked_ids = [
            tid
            for tid, cb in self._check_boxes.items()
            if cb.isChecked()
        ]
        if not checked_ids:
            # Match tkinter: silently refuse (don't accept the
            # dialog).  The user has to either check at least one
            # title or hit Cancel.
            return

        selection = _build_content_selection(
            self._classified, checked_ids
        )
        # Defensive: even if checked_ids was non-empty, the
        # aggregation could still produce empty main+extra (e.g.,
        # if the user checked nothing — already guarded above, but
        # belt-and-suspenders).
        if not selection.main_title_ids and not selection.extra_title_ids:
            return

        self.result_value = selection
        self.accept()

    def _cancel(self) -> None:
        self.result_value = None
        self.reject()

    def keyPressEvent(self, event):  # noqa: N802 (Qt convention)
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()
            return
        super().keyPressEvent(event)


def show_content_mapping(
    parent: Any,
    classified: list,
) -> ContentSelection | None:
    """Show the content-mapping step modally.

    Returns a ``ContentSelection`` with the user's choices, or
    ``None`` if cancelled.  Mirrors the tkinter signature exactly.
    """
    dialog = _ContentMappingDialog(
        classified=classified,
        parent=parent if isinstance(parent, QWidget) else None,
    )
    dialog.exec()
    return dialog.result_value


# ---------------------------------------------------------------------------
# Step 4 — Extras Classification
# ---------------------------------------------------------------------------
#
# Per-extra dropdown selecting a Jellyfin category from
# ``JELLYFIN_EXTRAS_CATEGORIES``.  Returns ``ExtrasAssignment``
# mapping ``title_id`` → category.  Pattern matches Steps 3 / 5 — a
# pure helper for testability without Qt, then the dialog class.


def _build_extras_assignment(
    extra_titles: Sequence,
    row_choices: dict[int, str],
) -> "ExtrasAssignment":
    """Build the ``ExtrasAssignment`` for the user's combo selections.

    Pure function — no Qt dependency.  Inputs:

    * ``extra_titles`` — the ordered list of ``ClassifiedTitle``
      objects shown in the dialog.  Their ``title_id`` is the key.
    * ``row_choices`` — mapping ``{title_id: chosen_category}``
      assembled from each row's ``QComboBox.currentText()``.

    Output: ``ExtrasAssignment(assignments=...)`` with the same shape
    the tkinter implementation produces.  Tests import this directly
    to verify aggregation without instantiating widgets.

    The function defends against rows that lost their combo state by
    falling back to the default category (``"Extras"``) — that
    keeps behavior consistent with tkinter's ``StringVar`` default.
    """
    default = "Extras"
    assignments: dict[int, str] = {}
    for ct in extra_titles:
        tid = ct.title_id
        chosen = row_choices.get(tid, default)
        # Empty/None choice (shouldn't happen with QComboBox but
        # defend anyway) → default.
        assignments[tid] = chosen if chosen else default
    return ExtrasAssignment(assignments=assignments)


class _ExtrasClassificationDialog(QDialog):
    """Step 4 dialog — assign each extra title to a Jellyfin category.

    One row per title, with: ``Title N`` label, duration + size,
    arrow glyph, ``QComboBox`` populated from
    ``JELLYFIN_EXTRAS_CATEGORIES``.  Defaults to ``"Extras"`` (matches
    tkinter).  Submit gathers all combo selections into an
    ``ExtrasAssignment``; cancel/Esc returns ``None``.

    objectName scheme matches Steps 3 / 5: ``stepHeader`` /
    ``stepSubtitle`` for the chrome, ``confirmButton`` for the green
    "Next" go-button, ``cancelButton`` for the muted Cancel.  Per-row
    ``extrasRow`` and ``extrasCategoryCombo`` for QSS row targeting.
    """

    def __init__(
        self,
        extra_titles: Sequence,
        parent: "QWidget | None" = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("wizardDialog")
        self.setWindowTitle("Extras Classification")
        self.setModal(True)

        self._extra_titles = list(extra_titles)
        self._combos: dict[int, QComboBox] = {}
        self.result_value: "ExtrasAssignment | None" = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 14)
        outer.setSpacing(6)

        title = QLabel("Step 4: Classify Extras")
        title.setObjectName("stepHeader")
        outer.addWidget(title)

        subtitle = QLabel(
            "Assign each extra to a Jellyfin category for correct "
            "folder placement."
        )
        subtitle.setObjectName("stepSubtitle")
        outer.addWidget(subtitle)

        _add_section_header(outer, "EXTRAS")

        # Body — one row per extra title, vertically stacked.
        body_scroll = QScrollArea()
        body_scroll.setObjectName("classifiedTitlesScroll")
        body_scroll.setWidgetResizable(True)

        body = QWidget()
        body.setObjectName("classifiedTitlesBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(4, 6, 4, 6)
        body_layout.setSpacing(3)

        for ct in self._extra_titles:
            row = self._build_row(ct)
            body_layout.addWidget(row)

        body_layout.addStretch(1)
        body_scroll.setWidget(body)
        outer.addWidget(body_scroll, stretch=1)

        # Buttons — Cancel left, Next (green) right.  Same row pattern
        # as Steps 3 / 5; QSS controls the green via #confirmButton.
        cancel_divider = QFrame()
        cancel_divider.setObjectName("sectionDivider")
        cancel_divider.setFrameShape(QFrame.Shape.HLine)
        cancel_divider.setFixedHeight(1)
        outer.addWidget(cancel_divider)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("cancelButton")
        cancel_btn.clicked.connect(self._cancel)
        button_row.addWidget(cancel_btn)

        button_row.addStretch(1)

        next_btn = QPushButton("Next  →")
        next_btn.setObjectName("confirmButton")
        next_btn.setDefault(True)  # Enter triggers Next, matches tkinter
        next_btn.clicked.connect(self._submit)
        button_row.addWidget(next_btn)

        outer.addLayout(button_row)

        self.resize(560, 480)

    def _build_row(self, ct) -> QWidget:
        """Build one row: ``Title N | duration size | → | combo``.

        Mirrors the tkinter row layout in ``gui/setup_wizard.py``
        (lines ~605-640) but with Qt widgets and objectNames for
        QSS targeting.
        """
        tid = ct.title_id
        dur = _format_duration(float(ct.title.get("duration_seconds", 0) or 0))
        size = _format_size(float(ct.title.get("size_bytes", 0) or 0))

        row = QFrame()
        row.setObjectName("classifiedTitleRow")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(4, 3, 4, 3)
        row_layout.setSpacing(8)

        title_cell = QLabel(f"Title {tid + 1}")
        title_cell.setObjectName("titleNumber")
        title_cell.setMinimumWidth(70)
        row_layout.addWidget(title_cell)

        ds_cell = QLabel(f"{dur}  {size}")
        ds_cell.setObjectName("durationSize")
        ds_cell.setMinimumWidth(140)
        row_layout.addWidget(ds_cell)

        arrow_cell = QLabel("→")
        arrow_cell.setObjectName("durationSize")  # same muted role
        row_layout.addWidget(arrow_cell)

        combo = QComboBox()
        combo.setObjectName("extrasCategoryCombo")
        combo.addItems(list(JELLYFIN_EXTRAS_CATEGORIES))
        # Default to "Extras" (matches tkinter's StringVar(value="Extras")).
        # If JELLYFIN_EXTRAS_CATEGORIES ever loses "Extras" the index
        # falls back to 0 rather than crashing.
        try:
            combo.setCurrentIndex(JELLYFIN_EXTRAS_CATEGORIES.index("Extras"))
        except ValueError:
            combo.setCurrentIndex(0)
        combo.setMinimumWidth(180)
        self._combos[tid] = combo
        row_layout.addWidget(combo)

        row_layout.addStretch(1)

        return row

    def _submit(self) -> None:
        row_choices = {
            tid: combo.currentText()
            for tid, combo in self._combos.items()
        }
        self.result_value = _build_extras_assignment(
            self._extra_titles, row_choices
        )
        self.accept()

    def _cancel(self) -> None:
        self.result_value = None
        self.reject()

    def keyPressEvent(self, event):  # noqa: N802 (Qt convention)
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()
            return
        super().keyPressEvent(event)


def show_extras_classification(
    parent: Any,
    extra_titles: Sequence,
) -> "ExtrasAssignment | None":
    """Show the extras-classification step modally.

    Returns an ``ExtrasAssignment`` with the user's category choices,
    or ``None`` if cancelled.  Mirrors the tkinter signature exactly.

    If ``extra_titles`` is empty there's nothing to classify — return
    an empty assignment without showing the dialog (matches tkinter).
    """
    if not extra_titles:
        return ExtrasAssignment()

    dialog = _ExtrasClassificationDialog(
        extra_titles=extra_titles,
        parent=parent if isinstance(parent, QWidget) else None,
    )
    dialog.exec()
    return dialog.result_value


# ---------------------------------------------------------------------------
# Step 5 — Output Plan
# ---------------------------------------------------------------------------


class _OutputPlanDialog(QDialog):
    """Dialog implementing Step 5 of the setup wizard — the final
    "this is exactly what JellyRip will create" review screen.

    Sub-phase 3e adds the MKV preview button into this dialog; do
    NOT add it here.  3b is structural only.
    """

    def __init__(
        self,
        base_folder: str,
        main_label: str,
        extras_map: dict,
        detail_lines: Sequence[str] | None,
        header_text: str,
        subtitle_text: str,
        confirm_text: str,
        parent: QWidget | None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("wizardDialog")
        self.setWindowTitle("Output Plan")
        self.setModal(True)

        self.result_value: bool = False

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 14)

        # ---- Header ----
        title = QLabel(header_text)
        title.setObjectName("stepHeader")
        root.addWidget(title)

        subtitle = QLabel(subtitle_text)
        subtitle.setObjectName("stepSubtitle")
        root.addWidget(subtitle)

        # ---- Optional session-summary detail lines ----
        if detail_lines:
            _add_section_header(root, "SESSION SUMMARY")
            for line in detail_lines:
                detail = QLabel(line)
                detail.setObjectName("sessionDetailLine")
                root.addWidget(detail)

        # ---- Folder structure tree ----
        _add_section_header(root, "FOLDER STRUCTURE")

        tree_view = _build_output_tree_widget(
            base_folder, main_label, dict(extras_map),
        )
        root.addWidget(tree_view)

        # ---- Destination path ----
        dest_label = QLabel(f"Destination: {base_folder}")
        dest_label.setObjectName("destinationPath")
        root.addWidget(dest_label)

        # ---- Buttons ----
        cancel_divider = QFrame()
        cancel_divider.setObjectName("sectionDivider")
        cancel_divider.setFrameShape(QFrame.Shape.HLine)
        cancel_divider.setFixedHeight(1)
        root.addWidget(cancel_divider)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("cancelButton")
        cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(cancel_btn)

        confirm_btn = QPushButton(confirm_text)
        # confirmButton is a distinct objectName from primaryButton
        # because the tkinter version uses GREEN here (not the
        # primary blue) for go/start semantics.  QSS can color it
        # green without affecting other primary buttons.
        confirm_btn.setObjectName("confirmButton")
        confirm_btn.setDefault(True)  # Enter triggers Start Rip
        confirm_btn.clicked.connect(self._confirm)
        btn_row.addWidget(confirm_btn)

        btn_row.addStretch(1)
        root.addLayout(btn_row)

    def _confirm(self) -> None:
        self.result_value = True
        self.accept()

    def _cancel(self) -> None:
        self.result_value = False
        self.reject()

    def keyPressEvent(self, event):  # noqa: N802 (Qt convention)
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()
            return
        # Enter is handled by the default button; explicit handling
        # not needed.  If the user is in the tree-view (which is
        # readonly), Enter still triggers via the default button.
        super().keyPressEvent(event)


def show_output_plan(
    parent: Any,
    base_folder: str,
    main_label: str,
    extras_map: dict,
    detail_lines: Sequence[str] | None = None,
    header_text: str = "Step 5: Output Plan",
    subtitle_text: str = (
        "This is exactly what JellyRip will create. "
        "No guessing, no surprises."
    ),
    confirm_text: str = "Start Rip",
) -> bool:
    """Show the planned output folder structure modally.

    Returns ``True`` if the user confirmed (Start Rip / Enter),
    ``False`` if cancelled (Cancel button / Esc / window close).

    Mirrors the tkinter ``show_output_plan`` signature exactly so
    ``gui/main_window.py:show_output_plan_step`` can be ported in
    Phase 3c without changing its call shape.

    Per migration plan decision #4, sub-phase 3e adds an MKV
    preview button into this dialog.  3b builds the structural
    review only — preview lands later.
    """
    dialog = _OutputPlanDialog(
        base_folder=base_folder,
        main_label=main_label,
        extras_map=extras_map,
        detail_lines=detail_lines,
        header_text=header_text,
        subtitle_text=subtitle_text,
        confirm_text=confirm_text,
        parent=parent if isinstance(parent, QWidget) else None,
    )
    dialog.exec()
    return dialog.result_value
