"""Video thumbnail extraction — pull one frame from a media file via ffmpeg.

For showing Explorer-style thumbnails in the disc/browse scan lists.  Pure
helper: give it a video path + an ffmpeg executable, get a small JPG frame
written to ``out_path``.  GUI-free and testable.

Note on scope: this is instant for already-ripped files (browse / organize
/ post-rip analysis).  It cannot thumbnail a disc *title* before it is
ripped — there is no file yet.
"""

from __future__ import annotations

import os
import subprocess

DEFAULT_WIDTH = 240

# Try well into the episode first (past intros/logos), then fall back
# earlier for short clips.
_SEEK_CANDIDATES: tuple[str, ...] = ("00:03:00", "00:00:20", "00:00:01")


def generate_thumbnail(
    video_path: str,
    out_path: str,
    ffmpeg_exe: str,
    *,
    width: int = DEFAULT_WIDTH,
    timeout: float = 30.0,
) -> bool:
    """Extract one frame from ``video_path`` to ``out_path`` (JPG).

    * fast keyframe seek (``-ss`` before ``-i``);
    * ``-update 1`` writes a single image cleanly (no ``%d`` warning);
    * ``scale=iw*sar:ih`` un-squishes anamorphic DVD frames (720x480
      stored, 16:9 displayed) before scaling to ``width``.

    Returns ``True`` if a non-empty file was produced, else ``False``.
    Never raises for a bad input / missing ffmpeg — returns ``False``.
    """
    for seek in _SEEK_CANDIDATES:
        try:
            subprocess.run(
                [
                    ffmpeg_exe, "-nostdin", "-y",
                    "-ss", seek,
                    "-i", video_path,
                    "-frames:v", "1",
                    "-update", "1",
                    "-vf", f"scale=iw*sar:ih,scale={int(width)}:-2",
                    out_path,
                ],
                capture_output=True,
                timeout=timeout,
            )
        except Exception:  # noqa: BLE001 — bad path / missing exe / timeout
            continue
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return True
    return False
