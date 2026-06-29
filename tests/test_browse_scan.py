"""Browse-folder per-file analysis helper (pure, no Qt)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import transcode.browse_scan as bs


def test_human_size():
    assert bs.human_size(0) == "0 B"
    assert bs.human_size(2048) == "2 KB"
    assert "GB" in bs.human_size(4 * 1024 ** 3)


def test_human_length():
    assert bs.human_length(0) == "—"
    assert bs.human_length(125) == "2:05"
    assert bs.human_length(3661) == "1:01:01"


def test_analyze_mkv_for_browse(monkeypatch):
    monkeypatch.setattr(bs, "probe_media_for_recommendation", lambda p, e: {
        "path": p,
        "name": "movie.mkv",
        "size_bytes": 4 * 1024 ** 3,
        "duration_seconds": 1320.0,
        "video_codec": "h264",
        "width": 1920,
        "height": 1080,
    })
    monkeypatch.setattr(bs, "build_ffmpeg_recommendations", lambda a: {
        "recommended_id": "balanced",
        "advisory": "",
        "recommendations": [{
            "id": "balanced", "label": "Best Overall", "crf": 20,
            "preset": "medium", "profile_data": {"video": {}, "audio": {}},
        }],
    })

    info = bs.analyze_mkv_for_browse("/some/movie.mkv", "ffprobe")
    assert info["name"] == "movie.mkv"
    assert info["codec"] == "H264"
    assert info["resolution"] == "1920x1080"
    assert info["size_text"].endswith("GB")
    assert info["length_text"] == "22:00"
    assert info["recommended_id"] == "balanced"
    assert "Best Overall" in info["suggestion"]
    # Cached for building a job later without re-probing.
    assert info["analysis"]["video_codec"] == "h264"
    assert info["recommendation"]["crf"] == 20
