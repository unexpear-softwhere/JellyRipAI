"""HandBrakeCLI command builder.

Two modes:

* **Explicit** — when ``settings["encoder"]`` is given, build a command
  that honours the chosen codec/encoder, quality (RF), encoder preset,
  and audio handling.  This is what the Prep MKVs flow uses so HandBrake
  respects the same options the FFmpeg path does.
* **Preset** — when no explicit encoder is given, fall back to a named
  HandBrake preset (the original behaviour).

All modes keep every audio + subtitle track and chapter markers, and
output Matroska.
"""

from typing import Any, Dict, List


# (codec, hw_accel) → HandBrakeCLI encoder name.
_HB_ENCODER: Dict[tuple, str] = {
    ("h265", "cpu"): "x265",
    ("h264", "cpu"): "x264",
    ("h265", "nvenc"): "nvenc_h265",
    ("h264", "nvenc"): "nvenc_h264",
    ("h265", "qsv"): "qsv_h265",
    ("h264", "qsv"): "qsv_h264",
    ("h265", "amf"): "vce_h265",
    ("h264", "amf"): "vce_h264",
}


def handbrake_encoder(codec: str, hw_accel: str) -> str:
    """Map a ``(codec, hw_accel)`` pair to the HandBrakeCLI encoder name
    (e.g. ``("h265", "nvenc") -> "nvenc_h265"``).  Defaults to ``x265``."""
    key = (str(codec or "h265").lower(), str(hw_accel or "cpu").lower())
    return _HB_ENCODER.get(key, "x265")


class HandBrakeBuilder:
    def __init__(
        self,
        input_path: str,
        output_path: str,
        preset: str = "Fast 1080p30",
        metadata: Dict[str, Any] | None = None,
        executable_path: str = "HandBrakeCLI",
        *,
        settings: Dict[str, Any] | None = None,
    ):
        self.input_path = input_path
        self.output_path = output_path
        self.preset = preset or "Fast 1080p30"
        self.metadata = metadata or {}
        self.executable_path = executable_path or "HandBrakeCLI"
        # Explicit encode settings (encoder / quality / encoder_preset /
        # audio).  Present → explicit mode; absent → named-preset mode.
        self.settings = settings or {}

    def build_command(self) -> List[str]:
        cmd = [
            self.executable_path,
            "-i", self.input_path,
            "-o", self.output_path,
        ]

        encoder = str(self.settings.get("encoder") or "").strip()
        if encoder:
            # ── Explicit mode: honour the chosen options ───────────────
            cmd += ["-e", encoder]
            quality = self.settings.get("quality")
            if quality is not None:
                cmd += ["-q", str(quality)]
            enc_preset = str(self.settings.get("encoder_preset") or "").strip()
            if enc_preset:
                cmd += ["--encoder-preset", enc_preset]
            audio = str(self.settings.get("audio") or "copy").strip().lower()
            cmd += ["-E", "copy" if audio == "copy" else "av_aac"]
            cmd += [
                "-f", "av_mkv",
                "--markers",
                "--all-audio",
                "--all-subtitles",
            ]
        else:
            # ── Preset mode (back-compat) ──────────────────────────────
            cmd += [
                "--preset", self.preset,
                "--format", "av_mkv",
                "--markers",
                "--all-audio",
                "--all-subtitles",
            ]

        if self.metadata.get("optimize_for_web"):
            cmd.append("--optimize")
        return cmd
