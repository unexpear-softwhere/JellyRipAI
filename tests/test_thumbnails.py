"""engine.thumbnails.generate_thumbnail — command + behavior + a real run."""

from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import engine.thumbnails as th
from engine.thumbnails import generate_thumbnail


# ── unit: command construction + success (mocked ffmpeg) ───────────────
def test_builds_expected_ffmpeg_command_and_succeeds(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    def fake_run(cmd, **_kw):
        calls.append(list(cmd))
        # simulate ffmpeg writing a non-empty image to the output path
        with open(cmd[-1], "wb") as fh:
            fh.write(b"\xff\xd8\xff\xe0jpeg")
        return object()

    monkeypatch.setattr(th.subprocess, "run", fake_run)
    out = str(tmp_path / "thumb.jpg")

    ok = generate_thumbnail("movie.mkv", out, "ffmpeg", width=200)

    assert ok is True
    # succeeded on the first seek → only one ffmpeg call
    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[0] == "ffmpeg"
    assert "-i" in cmd and "movie.mkv" in cmd
    assert cmd[cmd.index("-frames:v") + 1] == "1"
    assert "-update" in cmd
    assert any("scale=" in a and "200" in a for a in cmd), cmd
    assert cmd[-1] == out


# ── unit: no output ever → tries every seek, returns False ─────────────
def test_returns_false_when_no_frame_written(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    def fake_run(cmd, **_kw):
        calls.append(list(cmd))  # writes nothing
        return object()

    monkeypatch.setattr(th.subprocess, "run", fake_run)
    out = str(tmp_path / "thumb.jpg")

    ok = generate_thumbnail("movie.mkv", out, "ffmpeg")

    assert ok is False
    assert len(calls) == 3  # exhausted all seek candidates
    assert not os.path.exists(out)


# ── unit: ffmpeg raising (bad exe / timeout) is swallowed ──────────────
def test_swallows_subprocess_errors(monkeypatch, tmp_path):
    def boom(_cmd, **_kw):
        raise FileNotFoundError("ffmpeg not found")

    monkeypatch.setattr(th.subprocess, "run", boom)
    assert generate_thumbnail("x.mkv", str(tmp_path / "t.jpg"), "ffmpeg") is False


# ── integration: real ffmpeg on a generated clip (skips if unavailable) ─
def _real_ffmpeg() -> str | None:
    try:
        from config import resolve_ffmpeg
        path = resolve_ffmpeg(None, allow_path_lookup=True).path
    except Exception:
        path = ""
    return path if path and os.path.exists(path) else None


def test_real_extraction_produces_valid_jpg(tmp_path):
    ffmpeg = _real_ffmpeg()
    if not ffmpeg:
        pytest.skip("no resolvable ffmpeg for the integration test")
    clip = str(tmp_path / "clip.mp4")
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i",
         "testsrc=duration=3:size=320x240:rate=10", clip],
        capture_output=True, timeout=60,
    )
    if not os.path.exists(clip):
        pytest.skip("could not generate a test clip")

    out = str(tmp_path / "thumb.jpg")
    # 3 min / 20 s seeks fall past this 3-second clip; the 1 s fallback hits.
    assert generate_thumbnail(clip, out, ffmpeg, width=160) is True
    assert os.path.getsize(out) > 0
