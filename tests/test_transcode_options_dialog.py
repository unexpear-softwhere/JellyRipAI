"""Transcode options dialog — quality / codec / encoder / audio chooser.

Pins:
- Defaults are the first option of each group (balanced / h265 / cpu / copy).
- The encoder group offers CPU always, and a GPU only when the caller
  passes it (we never advertise an encoder this build can't run).
- Selecting non-default options round-trips through ``result_options``.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

pytest.importorskip("pytestqt")

from gui_qt.dialogs.transcode_options import _TranscodeOptionsDialog


def test_defaults_are_first_option(qtbot):
    dialog = _TranscodeOptionsDialog(
        file_count=3, output_root="X:/out", gpu_options=[],
    )
    qtbot.addWidget(dialog)
    dialog._on_start()
    assert dialog.result_options == {
        "quality": "balanced",
        "codec": "h265",
        "hw_accel": "cpu",
        "audio": "copy",
    }


def test_encoder_group_offers_only_detected_gpus(qtbot):
    # No GPU detected → CPU is the only encoder.
    cpu_only = _TranscodeOptionsDialog(
        file_count=1, output_root="X", gpu_options=[],
    )
    qtbot.addWidget(cpu_only)
    assert [v for v, _ in cpu_only._groups["hw_accel"]] == ["cpu"]

    # NVIDIA detected → CPU + NVENC, in that order.
    with_gpu = _TranscodeOptionsDialog(
        file_count=1, output_root="X",
        gpu_options=[("nvenc", "NVIDIA GPU (NVENC)")],
    )
    qtbot.addWidget(with_gpu)
    assert [v for v, _ in with_gpu._groups["hw_accel"]] == ["cpu", "nvenc"]


def test_non_default_selection_round_trips(qtbot):
    dialog = _TranscodeOptionsDialog(
        file_count=1, output_root="X",
        gpu_options=[("nvenc", "NVIDIA GPU (NVENC)")],
    )
    qtbot.addWidget(dialog)

    def _check(group_key: str, value: str) -> None:
        for candidate, radio in dialog._groups[group_key]:
            if candidate == value:
                radio.setChecked(True)
                return
        raise AssertionError(f"{value!r} not offered in {group_key!r}")

    _check("quality", "smaller_file")
    _check("codec", "h264")
    _check("hw_accel", "nvenc")
    _check("audio", "aac")
    dialog._on_start()

    assert dialog.result_options == {
        "quality": "smaller_file",
        "codec": "h264",
        "hw_accel": "nvenc",
        "audio": "aac",
    }
