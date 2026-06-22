"""Transcode options dialog — choose the FFmpeg encode settings.

Lets the user pick, in one place, the four choices that matter for an
FFmpeg re-encode:

* **Quality** — which CRF tier the recommendation uses (Save Space /
  Best Overall / Keep More Quality).
* **Video codec** — H.265 (smaller) or H.264 (most compatible).
* **Encoder** — CPU (best quality) or a hardware GPU encoder.  Only
  GPU options that this FFmpeg build actually exposes are offered, so
  the list never advertises an encoder that would fail.
* **Audio** — keep the original tracks (lossless) or re-encode to AAC
  to save space.

Returns a plain ``dict`` of the chosen values, or ``None`` on cancel.
The launcher maps these onto each file's recommendation ``profile_data``
before the job is queued.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from gui_qt.dialogs._modeless import exec_modeless

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


# (value, label) — the first entry in each group is the default.
_QUALITY: tuple[tuple[str, str], ...] = (
    ("balanced", "Best Overall — recommended first try"),
    ("smaller_file", "Save Space — smallest file, most quality risk"),
    ("higher_quality", "Keep More Quality — safest, least shrink"),
)
_CODEC: tuple[tuple[str, str], ...] = (
    ("h265", "H.265 / HEVC — smaller files (recommended)"),
    ("h264", "H.264 — larger, but plays on almost anything"),
)
_AUDIO: tuple[tuple[str, str], ...] = (
    ("copy", "Keep original audio (lossless, larger)"),
    ("aac", "Re-encode audio to AAC (smaller)"),
)


class _TranscodeOptionsDialog(QDialog):
    """Modal options chooser for the Prep MKVs → FFmpeg flow."""

    def __init__(
        self,
        *,
        file_count: int,
        output_root: str,
        gpu_options: Sequence[tuple[str, str]],
        parent: "QWidget | None" = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("transcodeOptionsDialog")
        self.setWindowTitle("Transcode Options")
        self.setWindowModality(Qt.WindowModality.WindowModal)

        # Public output: the chosen values, or None on cancel.
        self.result_options: dict | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(8)

        intro = QLabel(
            f"Re-encode {file_count} MKV(s) with FFmpeg.  Your originals "
            f"are kept untouched — output goes to:\n{output_root}"
        )
        intro.setObjectName("stepSubtitle")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        # CPU is always available; GPU encoders are appended only when
        # this FFmpeg build actually exposes them (detected by the caller).
        encoder_options: list[tuple[str, str]] = [
            ("cpu", "CPU — best quality, slower (libx26x)"),
        ]
        encoder_options.extend(gpu_options)

        self._groups: dict[str, list[tuple[str, QRadioButton]]] = {}
        self._add_group(outer, "quality", "Quality", _QUALITY)
        self._add_group(outer, "codec", "Video codec", _CODEC)
        self._add_group(outer, "hw_accel", "Encoder", encoder_options)
        self._add_group(outer, "audio", "Audio", _AUDIO)

        button_row = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.setObjectName("cancelButton")
        cancel.clicked.connect(self.reject)
        button_row.addWidget(cancel)
        button_row.addStretch(1)
        start = QPushButton("Start Transcode")
        start.setObjectName("confirmButton")
        start.setDefault(True)
        start.clicked.connect(self._on_start)
        button_row.addWidget(start)
        outer.addLayout(button_row)

        try:
            from gui_qt.ui_polish import apply_pointing_cursors
            apply_pointing_cursors(self)
        except Exception:  # noqa: BLE001 — cursor polish is cosmetic
            pass

    def _add_group(
        self,
        outer: QVBoxLayout,
        key: str,
        title: str,
        options: Sequence[tuple[str, str]],
    ) -> None:
        title_label = QLabel(title)
        title_label.setObjectName("optionGroupLabel")
        outer.addWidget(title_label)
        group = QButtonGroup(self)
        buttons: list[tuple[str, QRadioButton]] = []
        for position, (value, text) in enumerate(options):
            radio = QRadioButton(text)
            if position == 0:  # first option is the default
                radio.setChecked(True)
            group.addButton(radio)
            outer.addWidget(radio)
            buttons.append((value, radio))
        self._groups[key] = buttons

    def _selected(self, key: str) -> str:
        buttons = self._groups[key]
        for value, radio in buttons:
            if radio.isChecked():
                return value
        return buttons[0][0]

    def _on_start(self) -> None:
        self.result_options = {
            "quality": self._selected("quality"),
            "codec": self._selected("codec"),
            "hw_accel": self._selected("hw_accel"),
            "audio": self._selected("audio"),
        }
        self.accept()

    def keyPressEvent(self, event):  # noqa: N802 (Qt convention)
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)


def ask_transcode_options(
    parent: "QWidget | None",
    *,
    file_count: int,
    output_root: str,
    gpu_options: Sequence[tuple[str, str]],
) -> dict | None:
    """Show the options chooser modally.  Returns the chosen values as a
    dict (``quality`` / ``codec`` / ``hw_accel`` / ``audio``), or ``None``
    if the user cancels."""
    dialog = _TranscodeOptionsDialog(
        file_count=file_count,
        output_root=output_root,
        gpu_options=list(gpu_options),
        parent=parent,
    )
    exec_modeless(dialog)
    return dialog.result_options
