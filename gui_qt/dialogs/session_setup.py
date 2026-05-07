"""Session-setup dialogs for movie / TV rips.

Qt-native ports of what used to live in
``gui/session_setup_dialog.py``.

Returns ``MovieSessionSetup`` / ``TVSessionSetup`` dataclasses
imported from ``shared.session_setup_types``.

**Scope:** matches the original tkinter contract — same return
dataclasses, same default values, same fields.  Polish (auto-show
edition custom entry, real-time validation feedback, etc.) is
deferred to a polish pass.  3c-ii's bar was "users can complete the
form and the controller gets a valid setup object".  Phase 3h
(2026-05-04) collapsed the duplicated dataclasses into
``shared/session_setup_types.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from shared.session_setup_types import (
    MovieSessionSetup,
    TVSessionSetup,
)

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


# ---------------------------------------------------------------------------
# Form options — kept in sync with the tkinter source-of-truth.  If
# tkinter changes its options, update here too.  Phase 3h's tkinter
# removal collapses this duplication.
# ---------------------------------------------------------------------------


_METADATA_PROVIDERS: tuple[str, ...] = ("TMDB", "OpenDB")

_EDITION_OPTIONS: tuple[str, ...] = (
    "",
    "Theatrical Cut",
    "Director's Cut",
    "Extended Cut",
    "Unrated",
    "Custom…",
)

_EXTRAS_MODES: tuple[str, ...] = ("ask", "keep", "skip")
_EPISODE_MAPPING_MODES: tuple[str, ...] = ("auto", "manual")
_MULTI_EPISODE_MODES: tuple[str, ...] = ("auto", "split", "merge")
_SPECIALS_MODES: tuple[str, ...] = ("ask", "season0", "skip")


# ---------------------------------------------------------------------------
# Pure validators — testable without Qt
# ---------------------------------------------------------------------------


class _MovieFields(NamedTuple):
    title: str
    year: str
    edition: str
    metadata_provider: str
    metadata_id: str
    replace_existing: bool
    keep_raw: bool
    extras_mode: str


class _TVFields(NamedTuple):
    title: str
    year: str
    season: str
    starting_disc: str
    episode_mapping: str
    metadata_provider: str
    metadata_id: str
    multi_episode: str
    specials: str
    replace_existing: bool
    keep_raw: bool


def validate_movie_fields(fields: _MovieFields) -> str | None:
    """Return ``None`` if the inputs make a valid setup, otherwise a
    user-facing error message.

    Pure function — no Qt dependency.  Pinned by tests so the
    validation policy is independent of widget state.
    """
    if not fields.title.strip():
        return "Movie title is required."
    if fields.year and not _is_numeric_year(fields.year):
        return f"Release year {fields.year!r} must be numeric (e.g., 2024)."
    if fields.metadata_provider not in _METADATA_PROVIDERS:
        return f"Unknown metadata provider {fields.metadata_provider!r}."
    if fields.extras_mode not in _EXTRAS_MODES:
        return f"Unknown extras mode {fields.extras_mode!r}."
    return None


def validate_tv_fields(fields: _TVFields) -> str | None:
    """Pure validator for TV fields."""
    if not fields.title.strip():
        return "Show title is required."
    if fields.year and not _is_numeric_year(fields.year):
        return f"Release year {fields.year!r} must be numeric."
    if not _is_nonneg_int(fields.season):
        return f"Season must be a non-negative integer (got {fields.season!r})."
    if not _is_positive_int(fields.starting_disc):
        return f"Starting disc must be ≥ 1 (got {fields.starting_disc!r})."
    if fields.episode_mapping not in _EPISODE_MAPPING_MODES:
        return f"Unknown episode mapping {fields.episode_mapping!r}."
    if fields.multi_episode not in _MULTI_EPISODE_MODES:
        return f"Unknown multi-episode mode {fields.multi_episode!r}."
    if fields.specials not in _SPECIALS_MODES:
        return f"Unknown specials mode {fields.specials!r}."
    if fields.metadata_provider not in _METADATA_PROVIDERS:
        return f"Unknown metadata provider {fields.metadata_provider!r}."
    return None


def _is_numeric_year(s: str) -> bool:
    s = s.strip()
    if not s:
        return False
    return s.isdigit() and 1800 <= int(s) <= 2200


def _is_nonneg_int(s: str) -> bool:
    s = s.strip()
    if not s:
        return False
    if not s.lstrip("-").isdigit():
        return False
    return int(s) >= 0


def _is_positive_int(s: str) -> bool:
    s = s.strip()
    if not s:
        return False
    return s.isdigit() and int(s) >= 1


# ---------------------------------------------------------------------------
# Movie setup dialog
# ---------------------------------------------------------------------------


class _MovieSetupDialog(QDialog):
    """Movie rip setup form — title, year, edition, metadata, options."""

    def __init__(
        self,
        default_title: str,
        default_year: str,
        default_metadata_provider: str,
        default_metadata_id: str,
        parent: "QWidget | None" = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("movieSetupDialog")
        self.setWindowTitle("Movie — Library Identity")
        self.setModal(True)

        self.result_value: MovieSessionSetup | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 14)
        outer.setSpacing(8)

        title_label = QLabel("Step 2: Library Identity")
        title_label.setObjectName("stepHeader")
        outer.addWidget(title_label)

        subtitle = QLabel("What does this become in Jellyfin?  (* required)")
        subtitle.setObjectName("stepSubtitle")
        outer.addWidget(subtitle)

        form = QFormLayout()
        form.setVerticalSpacing(6)

        self._title_edit = QLineEdit(default_title)
        self._title_edit.setObjectName("movieTitleEdit")
        form.addRow("Movie title*:", self._title_edit)

        self._year_edit = QLineEdit(default_year)
        self._year_edit.setObjectName("movieYearEdit")
        self._year_edit.setMaxLength(4)
        form.addRow("Release year:", self._year_edit)

        self._edition_combo = QComboBox()
        self._edition_combo.addItems(list(_EDITION_OPTIONS))
        self._edition_combo.setObjectName("movieEditionCombo")
        form.addRow("Edition:", self._edition_combo)

        self._edition_custom_edit = QLineEdit()
        self._edition_custom_edit.setObjectName("movieEditionCustomEdit")
        self._edition_custom_edit.setPlaceholderText("Custom edition label…")
        self._edition_custom_edit.setEnabled(False)
        self._edition_combo.currentTextChanged.connect(
            self._on_edition_changed,
        )
        form.addRow("", self._edition_custom_edit)

        self._meta_provider_combo = QComboBox()
        self._meta_provider_combo.addItems(list(_METADATA_PROVIDERS))
        self._meta_provider_combo.setObjectName("movieMetaProviderCombo")
        if default_metadata_provider in _METADATA_PROVIDERS:
            self._meta_provider_combo.setCurrentText(default_metadata_provider)
        form.addRow("Metadata provider:", self._meta_provider_combo)

        self._meta_id_edit = QLineEdit(default_metadata_id)
        self._meta_id_edit.setObjectName("movieMetaIdEdit")
        self._meta_id_edit.setPlaceholderText(
            "Optional — leave blank to look up by title/year"
        )
        form.addRow("Metadata ID:", self._meta_id_edit)

        outer.addLayout(form)

        # Options
        self._replace_check = QCheckBox("Replace existing files in library")
        self._replace_check.setObjectName("movieReplaceCheck")
        outer.addWidget(self._replace_check)

        self._keep_raw_check = QCheckBox("Keep raw MKV after transcoding")
        self._keep_raw_check.setObjectName("movieKeepRawCheck")
        outer.addWidget(self._keep_raw_check)

        extras_row = QHBoxLayout()
        extras_row.addWidget(QLabel("Extras handling:"))
        self._extras_combo = QComboBox()
        self._extras_combo.addItems(list(_EXTRAS_MODES))
        self._extras_combo.setObjectName("movieExtrasCombo")
        extras_row.addWidget(self._extras_combo)
        extras_row.addStretch(1)
        outer.addLayout(extras_row)

        # Validation error label (hidden until OK fails)
        self._error_label = QLabel("")
        self._error_label.setObjectName("formError")
        self._error_label.setStyleSheet(
            ""  # color comes from QSS; just make the label exist
        )
        self._error_label.setVisible(False)
        outer.addWidget(self._error_label)

        # Buttons
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

        # Cache for tests
        self._ok_button = ok
        self._cancel_button = cancel

    def _on_edition_changed(self, text: str) -> None:
        self._edition_custom_edit.setEnabled(text == "Custom…")
        if text != "Custom…":
            self._edition_custom_edit.clear()

    def _gather(self) -> _MovieFields:
        """Collect form fields into a NamedTuple for validation."""
        edition = self._edition_combo.currentText()
        if edition == "Custom…":
            edition = self._edition_custom_edit.text().strip()
        return _MovieFields(
            title=self._title_edit.text(),
            year=self._year_edit.text(),
            edition=edition,
            metadata_provider=self._meta_provider_combo.currentText(),
            metadata_id=self._meta_id_edit.text(),
            replace_existing=self._replace_check.isChecked(),
            keep_raw=self._keep_raw_check.isChecked(),
            extras_mode=self._extras_combo.currentText(),
        )

    def _on_ok(self) -> None:
        fields = self._gather()
        error = validate_movie_fields(fields)
        if error:
            self._error_label.setText(error)
            self._error_label.setVisible(True)
            return
        self.result_value = MovieSessionSetup(
            title=fields.title.strip(),
            year=fields.year.strip(),
            edition=fields.edition.strip(),
            metadata_provider=fields.metadata_provider,
            metadata_id=fields.metadata_id.strip(),
            replace_existing=fields.replace_existing,
            keep_raw=fields.keep_raw,
            extras_mode=fields.extras_mode,
        )
        self.accept()

    def _on_cancel(self) -> None:
        self.result_value = None
        self.reject()

    def keyPressEvent(self, event):  # noqa: N802 (Qt convention)
        if event.key() == Qt.Key.Key_Escape:
            self._on_cancel()
            return
        super().keyPressEvent(event)


def ask_movie_setup(
    parent: "QWidget | None",
    default_title: str = "",
    default_year: str = "",
    default_metadata_provider: str = "TMDB",
    default_metadata_id: str = "",
) -> MovieSessionSetup | None:
    """Show the movie setup dialog modally.  Returns the populated
    ``MovieSessionSetup`` on OK, or ``None`` on Cancel / Esc."""
    dialog = _MovieSetupDialog(
        default_title=default_title,
        default_year=default_year,
        default_metadata_provider=default_metadata_provider,
        default_metadata_id=default_metadata_id,
        parent=parent,
    )
    dialog.exec()
    return dialog.result_value


# ---------------------------------------------------------------------------
# TV setup dialog
# ---------------------------------------------------------------------------


class _TVSetupDialog(QDialog):
    """TV rip setup form."""

    def __init__(
        self,
        default_title: str,
        default_year: str,
        default_season: str,
        default_starting_disc: str,
        default_metadata_provider: str,
        default_metadata_id: str,
        default_episode_mapping: str,
        default_multi_episode: str,
        default_specials: str,
        default_replace_existing: bool,
        parent: "QWidget | None" = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("tvSetupDialog")
        self.setWindowTitle("TV — Library Identity")
        self.setModal(True)

        self.result_value: TVSessionSetup | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 14)
        outer.setSpacing(8)

        title_label = QLabel("Step 2: Library Identity")
        title_label.setObjectName("stepHeader")
        outer.addWidget(title_label)

        subtitle = QLabel("What does this become in Jellyfin?  (* required)")
        subtitle.setObjectName("stepSubtitle")
        outer.addWidget(subtitle)

        form = QFormLayout()
        form.setVerticalSpacing(6)

        self._title_edit = QLineEdit(default_title)
        self._title_edit.setObjectName("tvTitleEdit")
        form.addRow("Show title*:", self._title_edit)

        self._year_edit = QLineEdit(default_year)
        self._year_edit.setObjectName("tvYearEdit")
        self._year_edit.setMaxLength(4)
        form.addRow("First-air year:", self._year_edit)

        self._season_edit = QLineEdit(default_season)
        self._season_edit.setObjectName("tvSeasonEdit")
        form.addRow("Season:", self._season_edit)

        self._disc_edit = QLineEdit(default_starting_disc)
        self._disc_edit.setObjectName("tvStartingDiscEdit")
        form.addRow("Starting disc:", self._disc_edit)

        self._mapping_combo = QComboBox()
        self._mapping_combo.addItems(list(_EPISODE_MAPPING_MODES))
        self._mapping_combo.setObjectName("tvEpisodeMappingCombo")
        if default_episode_mapping in _EPISODE_MAPPING_MODES:
            self._mapping_combo.setCurrentText(default_episode_mapping)
        form.addRow("Episode mapping:", self._mapping_combo)

        self._multi_combo = QComboBox()
        self._multi_combo.addItems(list(_MULTI_EPISODE_MODES))
        self._multi_combo.setObjectName("tvMultiEpisodeCombo")
        if default_multi_episode in _MULTI_EPISODE_MODES:
            self._multi_combo.setCurrentText(default_multi_episode)
        form.addRow("Multi-episode mode:", self._multi_combo)

        self._specials_combo = QComboBox()
        self._specials_combo.addItems(list(_SPECIALS_MODES))
        self._specials_combo.setObjectName("tvSpecialsCombo")
        if default_specials in _SPECIALS_MODES:
            self._specials_combo.setCurrentText(default_specials)
        form.addRow("Specials handling:", self._specials_combo)

        self._meta_provider_combo = QComboBox()
        self._meta_provider_combo.addItems(list(_METADATA_PROVIDERS))
        self._meta_provider_combo.setObjectName("tvMetaProviderCombo")
        if default_metadata_provider in _METADATA_PROVIDERS:
            self._meta_provider_combo.setCurrentText(default_metadata_provider)
        form.addRow("Metadata provider:", self._meta_provider_combo)

        self._meta_id_edit = QLineEdit(default_metadata_id)
        self._meta_id_edit.setObjectName("tvMetaIdEdit")
        self._meta_id_edit.setPlaceholderText(
            "Optional — leave blank to look up by title/year"
        )
        form.addRow("Metadata ID:", self._meta_id_edit)

        outer.addLayout(form)

        self._replace_check = QCheckBox("Replace existing files in library")
        self._replace_check.setObjectName("tvReplaceCheck")
        self._replace_check.setChecked(default_replace_existing)
        outer.addWidget(self._replace_check)

        self._keep_raw_check = QCheckBox("Keep raw MKV after transcoding")
        self._keep_raw_check.setObjectName("tvKeepRawCheck")
        outer.addWidget(self._keep_raw_check)

        self._error_label = QLabel("")
        self._error_label.setObjectName("formError")
        self._error_label.setVisible(False)
        outer.addWidget(self._error_label)

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

        self._ok_button = ok
        self._cancel_button = cancel

    def _gather(self) -> _TVFields:
        return _TVFields(
            title=self._title_edit.text(),
            year=self._year_edit.text(),
            season=self._season_edit.text(),
            starting_disc=self._disc_edit.text(),
            episode_mapping=self._mapping_combo.currentText(),
            metadata_provider=self._meta_provider_combo.currentText(),
            metadata_id=self._meta_id_edit.text(),
            multi_episode=self._multi_combo.currentText(),
            specials=self._specials_combo.currentText(),
            replace_existing=self._replace_check.isChecked(),
            keep_raw=self._keep_raw_check.isChecked(),
        )

    def _on_ok(self) -> None:
        fields = self._gather()
        error = validate_tv_fields(fields)
        if error:
            self._error_label.setText(error)
            self._error_label.setVisible(True)
            return
        self.result_value = TVSessionSetup(
            title=fields.title.strip(),
            year=fields.year.strip(),
            season=int(fields.season.strip()),
            starting_disc=int(fields.starting_disc.strip()),
            episode_mapping=fields.episode_mapping,
            metadata_provider=fields.metadata_provider,
            metadata_id=fields.metadata_id.strip(),
            multi_episode=fields.multi_episode,
            specials=fields.specials,
            replace_existing=fields.replace_existing,
            keep_raw=fields.keep_raw,
        )
        self.accept()

    def _on_cancel(self) -> None:
        self.result_value = None
        self.reject()

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self._on_cancel()
            return
        super().keyPressEvent(event)


def ask_tv_setup(
    parent: "QWidget | None",
    default_title: str = "",
    default_year: str = "",
    default_season: str = "1",
    default_starting_disc: str = "1",
    default_metadata_provider: str = "TMDB",
    default_metadata_id: str = "",
    default_episode_mapping: str = "auto",
    default_multi_episode: str = "auto",
    default_specials: str = "ask",
    default_replace_existing: bool = False,
) -> TVSessionSetup | None:
    """Show the TV setup dialog modally."""
    dialog = _TVSetupDialog(
        default_title=default_title,
        default_year=default_year,
        default_season=default_season,
        default_starting_disc=default_starting_disc,
        default_metadata_provider=default_metadata_provider,
        default_metadata_id=default_metadata_id,
        default_episode_mapping=default_episode_mapping,
        default_multi_episode=default_multi_episode,
        default_specials=default_specials,
        default_replace_existing=default_replace_existing,
        parent=parent,
    )
    dialog.exec()
    return dialog.result_value
