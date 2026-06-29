"""FolderBrowseWindow job-building logic (the right-click → queue path)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

_REC = {
    "id": "balanced", "label": "Best Overall", "crf": 20, "preset": "medium",
    "profile_name": "hevc_balanced", "details": "H.265 CRF 20",
    "profile_data": {"video": {}, "audio": {}},
}


def _make_window(qtbot, monkeypatch):
    # Stop the background scan from touching the disk.
    import gui_qt.workflow_launchers as wl
    monkeypatch.setattr(wl, "find_mkv_files", lambda folder: [])
    from gui_qt.dialogs.folder_browse import FolderBrowseWindow

    win = FolderBrowseWindow(
        "X",
        ffmpeg_exe="ffmpeg",
        ffprobe_exe="ffprobe",
        handbrake_exe="HandBrakeCLI",
        gpu_options=[("nvenc", "NVIDIA GPU (NVENC) — much faster")],
    )
    qtbot.addWidget(win)
    return win


def _patch_recs(monkeypatch):
    import transcode.recommendations as recs
    monkeypatch.setattr(
        recs, "build_ffmpeg_recommendations",
        lambda a: {"recommended_id": "balanced", "recommendations": [dict(_REC)]},
    )


def _info():
    return {
        "path": "C:/media/movie.mkv", "name": "movie.mkv", "size_bytes": 100,
        "recommended_id": "balanced", "analysis": {"path": "C:/media/movie.mkv"},
    }


def test_spacesaver_builds_ffmpeg_h265_cpu_copy(qtbot, monkeypatch):
    win = _make_window(qtbot, monkeypatch)
    _patch_recs(monkeypatch)

    import transcode.queue_builder as qb
    from transcode.queue_builder import QueueBuildResult
    from core.pipeline import TranscodeJob

    captured: dict = {}

    def _fake_job(*, plan, analysis, recommendation, ffmpeg_source_mode, ffmpeg_exe):
        captured["rec"] = recommendation
        captured["out"] = plan["output_path"]
        job = TranscodeJob(analysis["path"], plan["output_path"], None, backend="ffmpeg")
        return QueueBuildResult(jobs=[job], queue_detail="x")

    monkeypatch.setattr(qb, "build_recommendation_job", _fake_job)

    job = win._build_job(_info(), None)  # space-saver default

    assert job is not None
    assert job.backend == "ffmpeg"
    video = captured["rec"]["profile_data"]["video"]
    assert video["codec"] == "h265"
    assert video["hw_accel"] == "cpu"
    assert captured["rec"]["profile_data"]["audio"]["mode"] == "copy"
    # Output lands next to the source with a clear suffix.
    assert "[H.265]" in captured["out"]


def test_options_apply_codec_gpu_audio_to_ffmpeg(qtbot, monkeypatch):
    win = _make_window(qtbot, monkeypatch)
    _patch_recs(monkeypatch)

    import transcode.queue_builder as qb
    from transcode.queue_builder import QueueBuildResult
    from core.pipeline import TranscodeJob

    captured: dict = {}

    def _fake_job(*, plan, analysis, recommendation, ffmpeg_source_mode, ffmpeg_exe):
        captured["rec"] = recommendation
        return QueueBuildResult(
            jobs=[TranscodeJob(analysis["path"], plan["output_path"], None)],
            queue_detail="x",
        )

    monkeypatch.setattr(qb, "build_recommendation_job", _fake_job)

    opts = {"quality": "balanced", "codec": "h264", "hw_accel": "nvenc",
            "audio": "aac", "backend": "ffmpeg"}
    win._build_job(_info(), opts)

    video = captured["rec"]["profile_data"]["video"]
    assert video["codec"] == "h264"
    assert video["hw_accel"] == "nvenc"
    assert captured["rec"]["profile_data"]["audio"]["mode"] == "aac"


def test_handbrake_backend_builds_handbrake_job(qtbot, monkeypatch):
    win = _make_window(qtbot, monkeypatch)
    _patch_recs(monkeypatch)
    from transcode.handbrake_builder import handbrake_encoder

    opts = {"quality": "balanced", "codec": "h265", "hw_accel": "nvenc",
            "audio": "copy", "backend": "handbrake"}
    job = win._build_job(_info(), opts)

    assert job is not None
    assert job.backend == "handbrake"
    assert job.backend_options["encoder"] == handbrake_encoder("h265", "nvenc")
    assert job.backend_options["audio"] == "copy"
    assert job.backend_options["quality"] == 20  # rec crf


def test_on_completed_keeps_original_as_old_and_reveals(qtbot, monkeypatch, tmp_path):
    win = _make_window(qtbot, monkeypatch)

    orig = tmp_path / "movie.mkv"
    orig.write_bytes(b"x" * 1000)
    out = tmp_path / "movie [H.265].mkv"
    out.write_bytes(b"x" * 400)

    info = {
        "path": str(orig), "name": "movie.mkv", "size_bytes": 1000,
        "size_text": "1 KB", "duration_seconds": 60.0, "length_text": "1:00",
        "codec": "H264", "resolution": "1920x1080",
        "suggestion": "H.265 Best Overall", "recommended_id": "balanced",
        "recommendation": {}, "analysis": {"path": str(orig)},
    }
    win._on_row_ready(info)

    revealed: dict = {}
    monkeypatch.setattr(win, "_reveal_file", lambda p: revealed.update(path=p))

    win._on_completed(str(orig), str(out), 600.0)

    old = tmp_path / "movie [OLD].mkv"
    assert old.exists(), "original is kept, renamed to [OLD]"
    assert not orig.exists(), "original no longer under its old name"
    assert out.exists(), "new smaller file left in place"
    # The row + lookup map follow the rename.
    row = win._row_for_path(str(old))
    assert row >= 0
    assert win._table.item(row, 0).text() == "movie [OLD].mkv"
    assert str(old) in win._info_by_path
    assert str(orig) not in win._info_by_path
    # Explorer is opened on the NEW file.
    assert revealed["path"] == str(out)
