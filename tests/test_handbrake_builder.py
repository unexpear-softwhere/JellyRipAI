"""HandBrakeCLI builder — explicit settings + encoder mapping.

The builder used to be a stub that only knew a named preset.  These
pin the real behaviour: it maps codec/hw_accel to the right HandBrake
encoder, emits an explicit -e/-q/--encoder-preset/-E command when given
settings, and still supports the legacy named-preset path.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from transcode.handbrake_builder import HandBrakeBuilder, handbrake_encoder


def test_encoder_mapping():
    assert handbrake_encoder("h265", "cpu") == "x265"
    assert handbrake_encoder("h264", "cpu") == "x264"
    assert handbrake_encoder("h265", "nvenc") == "nvenc_h265"
    assert handbrake_encoder("h264", "nvenc") == "nvenc_h264"
    assert handbrake_encoder("h265", "qsv") == "qsv_h265"
    assert handbrake_encoder("h264", "qsv") == "qsv_h264"
    assert handbrake_encoder("h265", "amf") == "vce_h265"
    # Unknown combo falls back to x265.
    assert handbrake_encoder("???", "???") == "x265"


def test_explicit_settings_build_real_command():
    builder = HandBrakeBuilder(
        "in.mkv", "out.mkv", executable_path="HandBrakeCLI",
        settings={
            "encoder": "x265", "quality": 20,
            "encoder_preset": "slow", "audio": "aac",
        },
    )
    cmd = builder.build_command()
    assert cmd[0] == "HandBrakeCLI"
    assert cmd[cmd.index("-i") + 1] == "in.mkv"
    assert cmd[cmd.index("-o") + 1] == "out.mkv"
    assert cmd[cmd.index("-e") + 1] == "x265"
    assert cmd[cmd.index("-q") + 1] == "20"
    assert cmd[cmd.index("--encoder-preset") + 1] == "slow"
    assert cmd[cmd.index("-E") + 1] == "av_aac"
    assert "av_mkv" in cmd
    assert "--all-audio" in cmd and "--all-subtitles" in cmd


def test_copy_audio_and_preset_fallback():
    # Audio copy passes -E copy.
    copy_cmd = HandBrakeBuilder(
        "in", "out", settings={"encoder": "x264", "audio": "copy"},
    ).build_command()
    assert copy_cmd[copy_cmd.index("-E") + 1] == "copy"

    # No explicit encoder → legacy named-preset command.
    preset_cmd = HandBrakeBuilder("in", "out", preset="Fast 1080p30").build_command()
    assert "--preset" in preset_cmd
    assert preset_cmd[preset_cmd.index("--preset") + 1] == "Fast 1080p30"
    assert "-e" not in preset_cmd
