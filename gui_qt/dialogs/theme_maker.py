"""Theme Maker — build/edit a custom theme by picking colors.

Opens from Settings → Appearance.  Every color the theme touches is a
swatch that opens Qt's gradient color picker; the whole app recolors
**live** as you go (no contrast nanny — the preview shows exactly what
you'll get, you decide).  Save stores it as JSON under
``%APPDATA%\\JellyRipAI\\themes\\`` and selects it; Export writes that
JSON anywhere so you can share it (GitHub / itch / a file) and others
can Import it back.

A theme is pure color data, so there's nothing executable here — see
``gui_qt.custom_themes`` for the storage + validation.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gui_qt import custom_themes
from gui_qt.qss_render import render_qss_from_tokens

# Token → friendly label, grouped for the editor.  ``shadow`` is left
# out on purpose: it's an ``rgba(...)`` drop-shadow string, not a flat
# swatch, so we carry it through unchanged from the base theme.
_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("Surfaces", (
        ("bg", "Window background"),
        ("card", "Panel / card"),
        ("input", "Input field"),
        ("border", "Borders"),
    )),
    ("Text", (
        ("fg", "Text"),
        ("muted", "Muted / secondary text"),
        ("accent", "Accent / links / focus"),
    )),
    ("Buttons", (
        ("go", "Primary button"),
        ("goFg", "Primary button text"),
        ("info", "Secondary button"),
        ("infoFg", "Secondary button text"),
        ("alt", "Alt button"),
        ("altFg", "Alt button text"),
        ("warn", "Caution button"),
        ("warnFg", "Caution button text"),
        ("danger", "Destructive button"),
        ("dangerFg", "Destructive button text"),
    )),
    ("Selection & hover", (
        ("hover", "Hovered row"),
        ("selection", "Selected row"),
        ("selectionFg", "Selected row text"),
    )),
    ("Log / chat", (
        ("logBg", "Log background"),
        ("promptFg", "Your messages"),
        ("answerFg", "AI messages"),
    )),
)


class ThemeMakerDialog(QDialog):
    """Edit a token set with live full-app preview; save / export."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        base_tokens: dict[str, str],
        base_name: str = "My Theme",
        base_family: str = "dark",
        on_saved: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("themeMakerDialog")
        self.setWindowTitle("Theme Maker")
        self.setMinimumWidth = self.setMinimumWidth  # noqa: keep linters calm
        self.setMinimumSize(460, 560)

        self._tokens: dict[str, str] = dict(base_tokens)
        self._on_saved = on_saved
        self._swatches: dict[str, QPushButton] = {}
        self._app = QApplication.instance()
        # Snapshot the active stylesheet so Cancel / close restores it.
        self._original_qss = self._app.styleSheet() if self._app else ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        intro = QLabel(
            "Pick a color for anything below — the app recolors live as "
            "you go.  Save it to your themes, or Export to share."
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)

        # ── Name + family row ──────────────────────────────────────
        meta_row = QHBoxLayout()
        meta_row.addWidget(QLabel("Name:"))
        self._name_edit = QLineEdit(base_name)
        self._name_edit.setObjectName("themeMakerName")
        meta_row.addWidget(self._name_edit, stretch=1)
        meta_row.addWidget(QLabel("Base:"))
        self._family_combo = QComboBox()
        self._family_combo.addItem("Dark", userData="dark")
        self._family_combo.addItem("Light", userData="light")
        self._family_combo.setCurrentIndex(0 if base_family != "light" else 1)
        meta_row.addWidget(self._family_combo)
        outer.addLayout(meta_row)

        # ── Scrollable swatch groups ───────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(10)

        for group_name, entries in _GROUPS:
            body_lay.addWidget(self._group_header(group_name))
            grid = QGridLayout()
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(6)
            for row, (token, label) in enumerate(entries):
                grid.addWidget(QLabel(label), row, 0)
                swatch = self._make_swatch(token)
                grid.addWidget(swatch, row, 1)
            grid_host = QWidget()
            grid_host.setLayout(grid)
            body_lay.addWidget(grid_host)

        body_lay.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, stretch=1)

        # ── Action buttons ─────────────────────────────────────────
        btns = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.setObjectName("themeMakerSave")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._on_save)
        export_btn = QPushButton("Export…")
        export_btn.clicked.connect(self._on_export)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(save_btn)
        btns.addWidget(export_btn)
        btns.addStretch(1)
        btns.addWidget(cancel_btn)
        outer.addLayout(btns)

    # ── Builders ───────────────────────────────────────────────────

    @staticmethod
    def _group_header(text: str) -> QLabel:
        lbl = QLabel(text.upper())
        f = lbl.font()
        f.setBold(True)
        lbl.setFont(f)
        return lbl

    def _make_swatch(self, token: str) -> QPushButton:
        btn = QPushButton()
        btn.setFixedHeight(24)
        btn.setMinimumWidth(96)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._swatches[token] = btn
        self._paint_swatch(token)
        btn.clicked.connect(lambda _checked=False, t=token: self._pick(t))
        return btn

    def _paint_swatch(self, token: str) -> None:
        btn = self._swatches.get(token)
        if btn is None:
            return
        value = str(self._tokens.get(token, "#000000"))
        # Readable label color on the swatch.
        c = QColor(value)
        text_color = "#000000" if c.isValid() and c.lightnessF() > 0.55 else "#ffffff"
        btn.setText(value)
        btn.setStyleSheet(
            f"QPushButton {{ background-color: {value}; color: {text_color};"
            f" border: 1px solid #888; border-radius: 4px; }}"
        )

    # ── Color picking + live preview ───────────────────────────────

    def _pick(self, token: str) -> None:
        current = QColor(str(self._tokens.get(token, "#000000")))
        chosen = QColorDialog.getColor(
            current if current.isValid() else QColor("#000000"),
            self,
            f"Pick color — {token}",
        )
        if not chosen.isValid():
            return
        self._tokens[token] = chosen.name()  # "#rrggbb"
        self._paint_swatch(token)
        self._apply_preview()

    def _family(self) -> str:
        return str(self._family_combo.currentData() or "dark")

    def _apply_preview(self) -> None:
        if self._app is None:
            return
        try:
            qss = render_qss_from_tokens(
                self._tokens,
                id="__preview__",
                name=self._name_edit.text().strip() or "Preview",
                family=self._family(),
            )
            self._app.setStyleSheet(qss)
        except Exception:
            # A half-built token set should never crash the preview.
            pass

    # ── Save / export / cancel ─────────────────────────────────────

    def _theme_dict(self) -> dict[str, Any]:
        name = self._name_edit.text().strip() or "My Theme"
        return {
            "id": custom_themes.slugify(name),
            "name": name,
            "family": self._family(),
            "tokens": dict(self._tokens),
        }

    def _on_save(self) -> None:
        try:
            theme_id = custom_themes.save_custom(self._theme_dict())
        except Exception as exc:
            QMessageBox.warning(self, "Couldn't save theme", str(exc))
            return
        if self._on_saved is not None:
            try:
                self._on_saved(theme_id)
            except Exception:
                pass
        self.accept()  # leave the (saved) theme applied

    def _on_export(self) -> None:
        theme = self._theme_dict()
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export theme",
            f"{theme['id']}.json",
            "JellyRip theme (*.json)",
        )
        if not path:
            return
        try:
            custom_themes.export_custom(theme, path)
        except Exception as exc:
            QMessageBox.warning(self, "Couldn't export theme", str(exc))
            return
        QMessageBox.information(
            self, "Theme exported",
            f"Saved to:\n{path}\n\nShare that file — others can Import it.",
        )

    def reject(self) -> None:  # Cancel button / Esc
        if self._app is not None:
            self._app.setStyleSheet(self._original_qss)
        super().reject()

    def closeEvent(self, event: Any) -> None:  # window ✕
        if self._app is not None and self.result() != QDialog.DialogCode.Accepted:
            self._app.setStyleSheet(self._original_qss)
        super().closeEvent(event)


def open_theme_maker(
    parent: QWidget | None,
    *,
    base_tokens: dict[str, str],
    base_name: str = "My Theme",
    base_family: str = "dark",
    on_saved: Callable[[str], None] | None = None,
) -> int:
    """Construct + exec the Theme Maker.  Returns the dialog result."""
    dlg = ThemeMakerDialog(
        parent,
        base_tokens=base_tokens,
        base_name=base_name,
        base_family=base_family,
        on_saved=on_saved,
    )
    return dlg.exec()
