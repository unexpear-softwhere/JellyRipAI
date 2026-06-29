"""Per-file analysis for the Browse Folder media window.

Pure, GUI-free so it can be unit-tested: probe one MKV with ffprobe,
ask the recommendation engine what to do with it, and return a flat row
of display fields (+ the cached analysis/recommendation so a transcode
job can be built later without re-probing).
"""

from __future__ import annotations

from typing import Any

from transcode.recommendations import (
    build_ffmpeg_recommendations,
    probe_media_for_recommendation,
)


def human_size(num_bytes: float) -> str:
    """Bytes → a short human string (e.g. '4.2 GB')."""
    size = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def human_length(seconds: float) -> str:
    """Seconds → H:MM:SS / MM:SS."""
    total = int(round(seconds or 0))
    if total <= 0:
        return "—"
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def analyze_mkv_for_browse(path: str, ffprobe_exe: str) -> dict[str, Any]:
    """Probe one file and return a display row for the browse table.

    Keys: path, name, size_bytes, size_text, duration_seconds,
    length_text, codec, resolution, recommended_id, recommendation
    (the chosen rec dict, for building a job later), analysis (cached),
    and suggestion (a short human sentence).  Raises if ffprobe fails —
    the caller skips that file.
    """
    analysis = probe_media_for_recommendation(path, ffprobe_exe)
    recs = build_ffmpeg_recommendations(analysis)
    options = recs.get("recommendations") or []
    rec_id = str(recs.get("recommended_id") or "balanced")
    rec = next(
        (r for r in options if r.get("id") == rec_id),
        options[0] if options else {},
    )

    width = int(analysis.get("width") or 0)
    height = int(analysis.get("height") or 0)
    resolution = f"{width}x{height}" if width and height else "—"
    codec = str(analysis.get("video_codec") or "?").upper()

    # A short, honest one-liner: what to do + the engine's caveat.
    label = str(rec.get("label") or "Best Overall")
    crf = rec.get("crf")
    crf_text = f", CRF {crf}" if crf is not None else ""
    advisory = str(recs.get("advisory") or "").strip()
    suggestion = f"H.265 {label}{crf_text}"
    if advisory:
        suggestion = f"{suggestion} — {advisory.split('.')[0]}."

    return {
        "path": str(analysis.get("path") or path),
        "name": str(analysis.get("name") or path),
        "size_bytes": int(analysis.get("size_bytes") or 0),
        "size_text": human_size(analysis.get("size_bytes") or 0),
        "duration_seconds": float(analysis.get("duration_seconds") or 0.0),
        "length_text": human_length(analysis.get("duration_seconds") or 0.0),
        "codec": codec,
        "resolution": resolution,
        "recommended_id": rec_id,
        "recommendation": dict(rec),
        "analysis": analysis,
        "suggestion": suggestion,
    }
