"""
Tests for transcode.command_validator and FFmpegBuilder command correctness.

Two layers:
  - Validator unit tests: craft commands directly and assert the right
    errors/warnings are detected.
  - FFmpegBuilder integration tests: build real commands from profiles and
    run them through the validator to confirm they are structurally sound.
"""

import pytest
from transcode.command_validator import validate_ffmpeg_command
from transcode.profiles import normalize_profile_data, TranscodeProfile
from transcode.ffmpeg_builder import FFmpegBuilder


# ── helpers ──────────────────────────────────────────────────────────────────

def _build_cmd(**section_overrides) -> list[str]:
    """Build an FFmpeg command from a normalised profile with section overrides."""
    data = normalize_profile_data(section_overrides)
    profile = TranscodeProfile("test", data)
    return FFmpegBuilder(profile, "input.mkv", "output.mkv").build_command()


# ── Validator unit tests ──────────────────────────────────────────────────────

def test_valid_default_command_passes():
    cmd = [
        "ffmpeg", "-i", "input.mkv",
        "-map", "0",
        "-c:v", "libx265", "-crf", "20", "-preset", "medium",
        "-c:a", "copy",
        "-c:s", "copy",
        "-map_metadata", "0", "-map_chapters", "0",
        "output.mkv",
    ]
    result = validate_ffmpeg_command(cmd)
    assert result.ok
    assert not result.errors
    assert not result.warnings


def test_map0_plus_specific_audio_without_negative_is_error():
    cmd = [
        "ffmpeg", "-i", "input.mkv",
        "-map", "0",        # selects everything
        "-map", "0:a:0",    # duplicates audio already in -map 0
        "-c:v", "libx265", "output.mkv",
    ]
    result = validate_ffmpeg_command(cmd)
    assert not result.ok
    assert any("duplicate" in e.lower() or "-map 0" in e for e in result.errors)


def test_map0_with_negative_then_specific_audio_passes():
    cmd = [
        "ffmpeg", "-i", "input.mkv",
        "-map", "0",
        "-map", "-0:a",     # exclude all audio
        "-map", "0:a:0",    # re-add just the first track
        "-c:v", "libx265", "output.mkv",
    ]
    result = validate_ffmpeg_command(cmd)
    assert result.ok, result.errors


def test_map0_plus_language_audio_without_negative_is_error():
    cmd = [
        "ffmpeg", "-i", "input.mkv",
        "-map", "0",
        "-map", "0:a:m:language:eng",
        "-c:v", "libx265", "output.mkv",
    ]
    result = validate_ffmpeg_command(cmd)
    assert not result.ok


def test_map0_plus_type_level_audio_map_is_not_an_error():
    # "-map 0:a" is redundant with "-map 0" but not a conflict — it doesn't
    # select a *specific* stream, it just re-selects the audio type.
    cmd = [
        "ffmpeg", "-i", "input.mkv",
        "-map", "0",
        "-map", "0:a",
        "-c:v", "libx265", "output.mkv",
    ]
    result = validate_ffmpeg_command(cmd)
    assert result.ok, result.errors


def test_subtitle_burn_with_cs_is_error():
    cmd = [
        "ffmpeg", "-i", "input.mkv",
        "-filter_complex", "[0:s:0]scale=iw:ih[sub];[0:v][sub]overlay",
        "-c:s", "mov_text",
        "output.mkv",
    ]
    result = validate_ffmpeg_command(cmd)
    assert not result.ok
    assert any("burn" in e.lower() or "subtitle" in e.lower() for e in result.errors)


def test_subtitle_burn_without_cs_passes():
    # Correct burn structure: filter_complex overlay, -map "[v]", explicit audio
    cmd = [
        "ffmpeg", "-i", "input.mkv",
        "-filter_complex", "[0:v][0:s:0]overlay[v]",
        "-map", "[v]",
        "-map", "0:a",
        "-c:v", "libx265",
        "-c:a", "copy",
        "output.mkv",
    ]
    result = validate_ffmpeg_command(cmd)
    assert result.ok, result.errors


def test_gpu_encoder_with_x265_params_is_warning_not_error():
    cmd = [
        "ffmpeg", "-i", "input.mkv",
        "-map", "0",
        "-c:v", "hevc_nvenc",
        "-x265-params", "colorprim=bt2020:transfer=smpte2084",
        "output.mkv",
    ]
    result = validate_ffmpeg_command(cmd)
    assert result.ok   # warnings don't block
    assert any("x265-params" in w for w in result.warnings)


def test_crf_with_copy_codec_is_error():
    cmd = [
        "ffmpeg", "-i", "input.mkv",
        "-c:v", "copy", "-crf", "20",
        "output.mkv",
    ]
    result = validate_ffmpeg_command(cmd)
    assert not result.ok
    assert any("crf" in e.lower() for e in result.errors)


def test_pix_fmt_with_copy_codec_is_warning():
    cmd = [
        "ffmpeg", "-i", "input.mkv",
        "-c:v", "copy", "-pix_fmt", "yuv420p",
        "output.mkv",
    ]
    result = validate_ffmpeg_command(cmd)
    assert result.ok   # warning only
    assert any("pix_fmt" in w for w in result.warnings)


def test_libopus_in_mp4_is_error():
    # libopus is not part of the ISO Base Media spec; FFmpeg fails at the
    # mux stage.  This was previously a warning — it is now a blocking error
    # because users were still able to queue broken encodes.
    cmd = [
        "ffmpeg", "-i", "input.mkv",
        "-map", "0", "-c:v", "libx265", "-c:a", "libopus",
        "output.mp4",
    ]
    result = validate_ffmpeg_command(cmd)
    assert not result.ok
    assert any("opus" in e.lower() for e in result.errors)


def test_subtitle_copy_in_mp4_is_warning():
    cmd = [
        "ffmpeg", "-i", "input.mkv",
        "-map", "0", "-c:v", "libx265", "-c:s", "copy",
        "output.mp4",
    ]
    result = validate_ffmpeg_command(cmd)
    assert result.ok   # warning only
    assert any("mp4" in w.lower() and "subtitle" in w.lower() for w in result.warnings)


def test_missing_input_flag_is_error():
    cmd = ["ffmpeg", "-c:v", "libx265", "output.mkv"]
    result = validate_ffmpeg_command(cmd)
    assert not result.ok
    assert any("-i" in e for e in result.errors)


def test_empty_command_is_error():
    result = validate_ffmpeg_command([])
    assert not result.ok


# ── FFmpegBuilder integration tests ──────────────────────────────────────────
# Each test builds a real command through FFmpegBuilder and confirms the
# validator finds no structural errors. These tests would have failed before
# the -map and subtitle-burn fixes were applied.

def test_builder_default_all_streams_passes_validation():
    cmd = _build_cmd()
    result = validate_ffmpeg_command(cmd)
    assert result.ok, f"Validator errors: {result.errors}"


def test_builder_main_audio_track_no_map_conflict():
    """audio.tracks=main must use a negative map, not a bare duplicate."""
    cmd = _build_cmd(audio={"tracks": "main"})
    result = validate_ffmpeg_command(cmd)
    assert result.ok, f"Validator errors: {result.errors}"
    # Confirm the negative map is actually present
    assert "-0:a" in cmd


def test_builder_audio_language_filter_no_map_conflict():
    """audio.language=eng must use a negative map before the language map."""
    cmd = _build_cmd(audio={"language": "eng"})
    result = validate_ffmpeg_command(cmd)
    assert result.ok, f"Validator errors: {result.errors}"
    assert "-0:a" in cmd


def test_builder_subtitle_burn_no_cs_flag():
    """Burned subtitles must use overlay filter, not -c:s, not -map 0."""
    cmd = _build_cmd(subtitles={"burn": True})
    result = validate_ffmpeg_command(cmd)
    assert result.ok, f"Validator errors: {result.errors}"
    assert "-c:s" not in cmd
    # Overlay filter must be present and its output must be mapped
    assert "-filter_complex" in cmd
    fc_val = cmd[cmd.index("-filter_complex") + 1]
    assert "overlay" in fc_val
    assert "[v]" in cmd          # filter output mapped
    assert "-map" not in cmd or "[v]" in cmd   # video comes from filter, not raw
    # -map 0 must NOT be present (would include raw unfiltered video)
    maps = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map" and i + 1 < len(cmd)]
    assert "0" not in maps


def test_builder_subtitle_language_filter_no_map_conflict():
    """subtitles.language=eng must use a negative map before the language map."""
    cmd = _build_cmd(subtitles={"language": "eng"})
    result = validate_ffmpeg_command(cmd)
    assert result.ok, f"Validator errors: {result.errors}"
    assert "-0:s" in cmd


def test_builder_forced_subtitles_no_map_conflict():
    """subtitles.mode=forced must use a negative map before the forced map."""
    cmd = _build_cmd(subtitles={"mode": "forced"})
    result = validate_ffmpeg_command(cmd)
    assert result.ok, f"Validator errors: {result.errors}"
    assert "-0:s" in cmd


# ── Color metadata translation: unit tests ───────────────────────────────────

def test_color_flags_for_gpu_translates_hdr10_params():
    from transcode.ffmpeg_builder import _color_flags_for_gpu
    flags = _color_flags_for_gpu(
        "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:hdr-opt=1"
    )
    # hdr-opt=1 is x265-specific and must be dropped
    assert flags == [
        "-color_primaries", "bt2020",
        "-color_trc", "smpte2084",
        "-colorspace", "bt2020nc",
    ]


def test_color_flags_for_gpu_translates_hlg_params():
    from transcode.ffmpeg_builder import _color_flags_for_gpu
    flags = _color_flags_for_gpu(
        "colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc"
    )
    assert flags == [
        "-color_primaries", "bt2020",
        "-color_trc", "arib-std-b67",
        "-colorspace", "bt2020nc",
    ]


def test_color_flags_for_gpu_drops_non_color_params():
    from transcode.ffmpeg_builder import _color_flags_for_gpu
    # Encoder-specific tuning — nothing translatable
    assert _color_flags_for_gpu("deblock=-1,-1:sao=0:no-sao=1") == []


def test_color_flags_for_gpu_returns_empty_for_empty_string():
    from transcode.ffmpeg_builder import _color_flags_for_gpu
    assert _color_flags_for_gpu("") == []


# ── Color metadata translation: builder integration tests ────────────────────

def test_builder_nvenc_hdr10_emits_ffmpeg_color_flags_not_x265_params():
    """Switching hw_accel to nvenc must translate HDR color metadata to
    ffmpeg-level flags instead of silently dropping x265-params."""
    cmd = _build_cmd(video={
        "codec": "h265",
        "hw_accel": "nvenc",
        "extra_video_params": (
            "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:hdr-opt=1"
        ),
    })
    assert "-x265-params" not in cmd
    assert "-color_primaries" in cmd
    assert "bt2020" in cmd
    assert "-color_trc" in cmd
    assert "smpte2084" in cmd
    assert "-colorspace" in cmd
    assert "bt2020nc" in cmd
    result = validate_ffmpeg_command(cmd)
    assert result.ok, f"Validator errors: {result.errors}"
    # Builder no longer emits -x265-params for GPU encoders, so the
    # GPU+x265-params warning must not appear on a builder-generated command.
    assert not any("x265-params" in w for w in result.warnings)


def test_builder_qsv_hlg_emits_correct_color_trc():
    cmd = _build_cmd(video={
        "codec": "h265",
        "hw_accel": "qsv",
        "extra_video_params": (
            "colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc"
        ),
    })
    assert "-x265-params" not in cmd
    assert "-color_trc" in cmd
    assert "arib-std-b67" in cmd


def test_builder_cpu_libx265_still_uses_x265_params():
    """libx265 path must be unaffected — x265-params must still be emitted."""
    cmd = _build_cmd(video={
        "codec": "h265",
        "hw_accel": "cpu",
        "extra_video_params": (
            "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:hdr-opt=1"
        ),
    })
    assert "-x265-params" in cmd
    assert "-color_primaries" not in cmd  # ffmpeg-level flags not needed for libx265


def test_builder_gpu_non_color_extra_params_are_dropped_gracefully():
    """Non-color x265 tuning params must be silently dropped for GPU encoders,
    not cause errors or be emitted as garbage flags."""
    cmd = _build_cmd(video={
        "codec": "h265",
        "hw_accel": "nvenc",
        "extra_video_params": "deblock=-1,-1:sao=0",
    })
    assert "-x265-params" not in cmd
    assert "deblock" not in " ".join(cmd)
    result = validate_ffmpeg_command(cmd)
    assert result.ok, f"Validator errors: {result.errors}"


def test_builder_subtitle_burn_with_language_no_cs_flag():
    """Burn + language filter: overlay filter must use the language selector."""
    cmd = _build_cmd(subtitles={"burn": True, "language": "eng"})
    result = validate_ffmpeg_command(cmd)
    assert result.ok, f"Validator errors: {result.errors}"
    assert "-c:s" not in cmd
    fc_val = cmd[cmd.index("-filter_complex") + 1]
    assert "language:eng" in fc_val
    assert "overlay" in fc_val


# ── Encoder availability check (check 9) ─────────────────────────────────────

def test_encoder_available_passes_when_encoder_found(monkeypatch):
    from transcode.encoder_probe import available_encoders
    # Simulate a build that includes libx265.
    monkeypatch.setattr(
        "transcode.encoder_probe.available_encoders",
        lambda exe="ffmpeg": frozenset({"libx265", "libx264", "aac"}),
    )
    cmd = [
        "ffmpeg", "-i", "input.mkv",
        "-c:v", "libx265", "-crf", "22",
        "output.mkv",
    ]
    result = validate_ffmpeg_command(cmd, ffmpeg_exe="ffmpeg")
    assert result.ok, result.errors


def test_encoder_unavailable_is_error(monkeypatch):
    monkeypatch.setattr(
        "transcode.encoder_probe.available_encoders",
        lambda exe="ffmpeg": frozenset({"libx264", "aac"}),  # no libx265
    )
    cmd = [
        "ffmpeg", "-i", "input.mkv",
        "-c:v", "libx265", "-crf", "22",
        "output.mkv",
    ]
    result = validate_ffmpeg_command(cmd, ffmpeg_exe="ffmpeg")
    assert not result.ok
    assert any("libx265" in e and "not available" in e for e in result.errors)


def test_encoder_check_skipped_when_probe_returns_empty(monkeypatch):
    # An empty frozenset means the probe failed (binary missing, etc.).
    # The validator must not block execution in this case — treat as unknown.
    monkeypatch.setattr(
        "transcode.encoder_probe.available_encoders",
        lambda exe="ffmpeg": frozenset(),
    )
    cmd = [
        "ffmpeg", "-i", "input.mkv",
        "-c:v", "libx265", "-crf", "22",
        "output.mkv",
    ]
    result = validate_ffmpeg_command(cmd, ffmpeg_exe="ffmpeg")
    assert result.ok, result.errors


def test_encoder_check_skipped_when_no_ffmpeg_exe():
    # Without ffmpeg_exe the check is opt-in — must not run.
    cmd = [
        "ffmpeg", "-i", "input.mkv",
        "-c:v", "totally_fake_encoder",
        "output.mkv",
    ]
    result = validate_ffmpeg_command(cmd)
    assert result.ok, result.errors


# ── auto_prefer resolution + GPU quality flags + stdin discipline ────────────


def test_auto_prefer_falls_back_to_cpu_when_no_gpu_encoder(monkeypatch):
    """auto_prefer used to hardcode NVENC, so the shipped default
    profile failed at launch ("Unknown encoder") on every non-NVIDIA
    machine — and encoder launch failures never reach the
    encoder_unavailable fallback rule."""
    monkeypatch.setattr(
        "transcode.encoder_probe.available_encoders",
        lambda exe="ffmpeg": frozenset({"libx265", "libx264", "aac"}),
    )
    cmd = _build_cmd(video={"codec": "h265", "hw_accel": "auto_prefer"})
    assert "libx265" in cmd
    assert not any(str(c).endswith("_nvenc") for c in cmd)


def test_auto_prefer_picks_available_gpu_encoder(monkeypatch):
    monkeypatch.setattr(
        "transcode.encoder_probe.available_encoders",
        lambda exe="ffmpeg": frozenset({"hevc_qsv", "libx265"}),
    )
    cmd = _build_cmd(video={"codec": "h265", "hw_accel": "auto_prefer"})
    assert "hevc_qsv" in cmd


def test_gpu_encoder_gets_cq_not_crf():
    """``-crf`` is a libx264/libx265 private option; GPU encoders
    silently discard it and encode at their defaults (NVENC ≈ 2 Mbps
    mush with no error).  The quality number must map to the GPU
    encoder's own constant-quality knob."""
    cmd = _build_cmd(video={
        "codec": "h265", "hw_accel": "nvenc", "mode": "crf", "crf": 20,
    })
    assert "-crf" not in cmd
    assert "-cq" in cmd
    assert cmd[cmd.index("-cq") + 1] == "20"


def test_qsv_encoder_gets_global_quality():
    cmd = _build_cmd(video={
        "codec": "h265", "hw_accel": "qsv", "mode": "crf", "crf": 22,
    })
    assert "-crf" not in cmd
    assert "-global_quality" in cmd


def test_cpu_encoder_still_gets_crf():
    cmd = _build_cmd(video={
        "codec": "h265", "hw_accel": "cpu", "mode": "crf", "crf": 20,
    })
    assert "-crf" in cmd


def test_every_command_has_nostdin_and_overwrite_discipline(monkeypatch):
    """ffmpeg reads the console without -nostdin (a stray keypress can
    pause the encode; an exists-prompt blocks forever), and without an
    explicit -y/-n a runtime collision hangs at "Overwrite? [y/N]"."""
    monkeypatch.setattr(
        "transcode.encoder_probe.available_encoders",
        lambda exe="ffmpeg": frozenset({"libx265"}),
    )
    cmd = _build_cmd()
    assert "-nostdin" in cmd
    assert ("-n" in cmd) or ("-y" in cmd)

    copy_cmd = _build_cmd(
        video={"codec": "copy"}, audio={"mode": "copy"},
    )
    assert "-nostdin" in copy_cmd
    assert ("-n" in copy_cmd) or ("-y" in copy_cmd)
