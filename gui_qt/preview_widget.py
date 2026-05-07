"""MKV preview widget — Phase 3e (the v1-blocking feature).

Plays a short MKV preview clip inline so the user can verify they're
about to rip the right title before committing 30+ GB to disk.
This is the headline feature of the entire PySide6 migration per
migration plan decision #4.

**What this module ships:**

* ``PreviewDialog`` — modal QDialog with QVideoWidget + transport
  controls (play/pause, scrub slider, time labels).
* ``show_preview(parent, mkv_path)`` — public entry, blocks until
  the user closes the dialog.

**What this module DOESN'T do:**

* It does not rip the preview clip.  That's the controller's
  ``preview_title(title_id)`` which already exists in
  ``controller/legacy_compat.py:854``.
* It does not clean up the preview MKV file.  The controller owns
  the temp folder and runs cleanup post-session.

**Pure helpers** (Qt-free) at module level for testability:

* ``format_position_label(ms, total_ms)`` — "0:14 / 0:30" format.

The widget assumes ``QtMultimedia`` is available at import time.
On Windows the user's PyInstaller bundle (Phase 3f) needs to ship
the platform plugins; the widget itself is platform-agnostic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


# ---------------------------------------------------------------------------
# Pure helpers (Qt-free)
# ---------------------------------------------------------------------------


def format_position_label(position_ms: int, duration_ms: int) -> str:
    """Build the position/duration label "M:SS / M:SS".

    Pure function — testable without Qt.  Negative or zero values
    render as "0:00".  Duration of 0 (unknown) shows "0:00 / —".
    """
    pos = _format_ms(position_ms)
    if duration_ms <= 0:
        return f"{pos} / —"
    return f"{pos} / {_format_ms(duration_ms)}"


def _format_ms(ms: int) -> str:
    """Render milliseconds as M:SS (or H:MM:SS for ≥ 1h)."""
    if ms <= 0:
        return "0:00"
    total_seconds = ms // 1000
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


# ---------------------------------------------------------------------------
# The dialog
# ---------------------------------------------------------------------------


class PreviewDialog(QDialog):
    """Inline MKV preview player.

    Construction takes the file path to play.  The dialog opens with
    the video paused; the user clicks Play to start.  The Stop /
    Close button releases the player and rejects the dialog.
    """

    def __init__(
        self,
        mkv_path: str,
        parent: "QWidget | None" = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("previewDialog")
        self.setWindowTitle("MKV Preview")
        self.setModal(True)
        self.resize(720, 540)

        self._mkv_path = mkv_path
        # Cache the latest duration the player reported.  Used by
        # ``_refresh_position_label`` so tests + drag-state code
        # don't have to re-query ``player.duration()`` (which can
        # return 0 before the source is loaded).
        self._duration_ms: int = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 8)
        outer.setSpacing(6)

        # Video surface — fills most of the dialog.
        self._video_widget = QVideoWidget()
        self._video_widget.setObjectName("previewVideoWidget")
        outer.addWidget(self._video_widget, stretch=1)

        # Transport row: play/pause | scrub slider | position label.
        transport = QHBoxLayout()
        transport.setContentsMargins(12, 4, 12, 4)
        transport.setSpacing(8)

        self._play_button = QPushButton("▶")
        self._play_button.setObjectName("previewPlayButton")
        self._play_button.setFixedWidth(36)
        self._play_button.clicked.connect(self._toggle_play_pause)
        transport.addWidget(self._play_button)

        self._scrub = QSlider(Qt.Orientation.Horizontal)
        self._scrub.setObjectName("previewScrubSlider")
        self._scrub.setRange(0, 0)  # populated when duration is known
        self._scrub.sliderReleased.connect(self._on_scrub_released)
        transport.addWidget(self._scrub, stretch=1)

        self._position_label = QLabel("0:00 / —")
        self._position_label.setObjectName("previewPositionLabel")
        self._position_label.setFixedWidth(80)
        self._position_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        transport.addWidget(self._position_label)

        outer.addLayout(transport)

        # Bottom row — Close button (also releases the player).
        bottom = QHBoxLayout()
        bottom.setContentsMargins(12, 0, 12, 4)
        bottom.addStretch(1)
        self._close_button = QPushButton("Close")
        self._close_button.setObjectName("cancelButton")
        self._close_button.clicked.connect(self._on_close)
        bottom.addWidget(self._close_button)
        outer.addLayout(bottom)

        # Build the player + audio output.
        self._player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_output)
        self._player.setVideoOutput(self._video_widget)

        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_state_changed)
        self._player.errorOccurred.connect(self._on_player_error)

        # Load source.  Empty path → leave player idle (caller can
        # use the dialog as a "loading…" placeholder).
        if mkv_path:
            self._player.setSource(QUrl.fromLocalFile(mkv_path))

        # Track the last error message for tests + caller feedback.
        self.last_error: str = ""

    # ------------------------------------------------------------------
    # Public API + test hooks
    # ------------------------------------------------------------------

    @property
    def player(self) -> QMediaPlayer:
        return self._player

    @property
    def play_button(self) -> QPushButton:
        return self._play_button

    @property
    def scrub_slider(self) -> QSlider:
        return self._scrub

    @property
    def position_label(self) -> QLabel:
        return self._position_label

    def play(self) -> None:
        """Start playback.  Public so tests / callers can drive."""
        self._player.play()

    def pause(self) -> None:
        self._player.pause()

    # ------------------------------------------------------------------
    # Internal — slot handlers
    # ------------------------------------------------------------------

    def _toggle_play_pause(self) -> None:
        state = self._player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_scrub_released(self) -> None:
        self._player.setPosition(self._scrub.value())

    def _on_position_changed(self, position_ms: int) -> None:
        # Avoid fighting the user's drag — only update the slider
        # when they're not actively dragging it.
        if not self._scrub.isSliderDown():
            self._scrub.setValue(position_ms)
        self._refresh_position_label(position_ms)

    def _on_duration_changed(self, duration_ms: int) -> None:
        self._duration_ms = max(0, duration_ms)
        self._scrub.setRange(0, self._duration_ms)
        self._refresh_position_label(self._player.position())

    def _on_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._play_button.setText("⏸")
        else:
            self._play_button.setText("▶")

    def _on_player_error(self, error, error_string: str) -> None:
        # Capture so tests + callers can introspect.
        self.last_error = error_string or str(error)

    def _refresh_position_label(self, position_ms: int) -> None:
        self._position_label.setText(
            format_position_label(position_ms, self._duration_ms),
        )

    def _on_close(self) -> None:
        # Stop playback before rejecting so the file handle is
        # released promptly (matters on Windows where the controller
        # might want to delete the temp file right after).
        try:
            self._player.stop()
            self._player.setSource(QUrl())  # release handle
        except Exception:
            pass
        self.reject()

    def keyPressEvent(self, event):  # noqa: N802 (Qt convention)
        if event.key() == Qt.Key.Key_Escape:
            self._on_close()
            return
        if event.key() == Qt.Key.Key_Space:
            self._toggle_play_pause()
            return
        super().keyPressEvent(event)


def show_preview(
    parent: "QWidget | None",
    mkv_path: str,
) -> None:
    """Show the preview dialog modally.  Returns when the user
    closes it.  Side-effect-only — no return value."""
    dialog = PreviewDialog(mkv_path, parent=parent)
    dialog.exec()
