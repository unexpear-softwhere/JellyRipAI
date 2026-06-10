from typing import Dict, Any, List
from .profiles import TranscodeProfile


# x265-params color keys → equivalent ffmpeg output-level flags.
# Only these three have a direct cross-encoder equivalent; encoder-specific
# tuning keys (hdr-opt, deblock, sao, …) are intentionally omitted because
# they have no meaningful GPU encoder counterpart.
_X265_COLOR_TO_FFMPEG: Dict[str, str] = {
    "colorprim":   "-color_primaries",
    "transfer":    "-color_trc",
    "colormatrix": "-colorspace",
}


def _color_flags_for_gpu(x265_params: str) -> List[str]:
    """Translate color-metadata keys from an x265-params string into the
    ffmpeg output-level flags understood by GPU encoders (nvenc, qsv, amf).

    Only ``colorprim``, ``transfer``, and ``colormatrix`` are translated.
    Encoder-specific keys (``hdr-opt``, ``deblock``, ``sao``, …) are dropped
    because they are libx265-only and have no GPU equivalent.

    Example::

        _color_flags_for_gpu(
            "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:hdr-opt=1"
        )
        # → ['-color_primaries', 'bt2020', '-color_trc', 'smpte2084',
        #    '-colorspace', 'bt2020nc']
    """
    flags: List[str] = []
    for part in x265_params.split(":"):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        ffmpeg_flag = _X265_COLOR_TO_FFMPEG.get(key.strip())
        if ffmpeg_flag:
            flags += [ffmpeg_flag, value.strip()]
    return flags


def _resolve_auto_prefer(video: str, ffmpeg_exe: str) -> str:
    """Resolve ``hw_accel="auto_prefer"`` to a real encoder name.

    Probes this FFmpeg build (cached) and picks the best AVAILABLE GPU
    encoder for the codec, else the CPU encoder.  The old behavior
    hardcoded NVENC, so the shipped default profile failed at launch on
    every non-NVIDIA machine ("Unknown encoder") — and the
    encoder_unavailable fallback rule never fires for launch failures,
    so there was no recovery.  CPU is also the answer when the probe
    fails entirely: libx26x exists in every build.
    """
    from .encoder_probe import available_encoders

    prefix = "hevc" if video == "h265" else "h264"
    known = available_encoders(ffmpeg_exe)
    for gpu in (f"{prefix}_nvenc", f"{prefix}_qsv", f"{prefix}_amf"):
        if gpu in known:
            return gpu
    return "libx265" if video == "h265" else "libx264"


class FFmpegBuilder:
    def __init__(
        self,
        profile: TranscodeProfile,
        input_path: str,
        output_path: str,
        metadata: Dict[str, Any] | None = None,
        executable_path: str = "ffmpeg",
    ):
        self.profile = profile
        self.input_path = input_path
        self.output_path = output_path
        self.metadata = metadata or {}
        self.executable_path = executable_path or "ffmpeg"

    def build_command(self) -> List[str]:
        """
        Build ffmpeg command based on profile:
        - Prefer remux/copy if possible (video: copy, audio: copy, subtitles: copy)
        - Always preserve all metadata and chapters (-map_metadata 0 -map_chapters 0)
        - Only transcode when necessary
        """
        # ── Video ──────────────────────────────────────────────────────────────
        video       = self.profile.get("video", "codec")
        mode        = self.profile.get("video", "mode")
        crf         = self.profile.get("video", "crf")
        preset      = self.profile.get("video", "preset")
        hw_accel    = self.profile.get("video", "hw_accel")
        tune        = self.profile.get("video", "tune")
        vid_profile = self.profile.get("video", "video_profile")
        pix_fmt     = self.profile.get("video", "pix_fmt")
        keyint      = self.profile.get("video", "keyint")
        bframes     = self.profile.get("video", "bframes")
        refs        = self.profile.get("video", "refs")
        extra_video = self.profile.get("video", "extra_video_params")

        # ── Audio ──────────────────────────────────────────────────────────────
        audio_mode        = self.profile.get("audio", "mode")
        audio_language    = self.profile.get("audio", "language")
        audio_tracks      = self.profile.get("audio", "tracks")
        audio_bitrate     = self.profile.get("audio", "bitrate")
        audio_channels    = self.profile.get("audio", "channels")
        audio_sample_rate = self.profile.get("audio", "sample_rate")
        audio_downmix     = self.profile.get("audio", "downmix", False)

        # ── Subtitles ──────────────────────────────────────────────────────────
        sub_mode     = self.profile.get("subtitles", "mode")
        sub_burn     = self.profile.get("subtitles", "burn", False)
        sub_language = self.profile.get("subtitles", "language")

        # ── Output ────────────────────────────────────────────────────────────
        container = self.profile.get("output", "container")
        overwrite = bool(self.profile.get("output", "overwrite", False))

        # -nostdin: ffmpeg otherwise reads the console — a stray
        # keypress can pause/quit the encode, and the exists-prompt
        # ("File ... already exists. Overwrite? [y/N]") blocks forever.
        # The explicit overwrite flag makes collisions deterministic:
        # -y honors the profile's output.overwrite, -n errors out
        # immediately instead of prompting.
        base = [
            self.executable_path,
            "-nostdin",
            "-y" if overwrite else "-n",
            "-i", self.input_path,
        ]

        # ── Advanced ──────────────────────────────────────────────────────────
        extra_output_args = self.profile.get("advanced", "extra_output_args")

        # ── Remux / copy fast-path ─────────────────────────────────────────────
        if video == "copy" and audio_mode == "copy" and not sub_burn:
            cmd = base + [
                "-map", "0",
                "-c", "copy",
                "-map_metadata", "0",
                "-map_chapters", "0",
                "-disposition:s:0", "default",
                self.output_path,
            ]
            return cmd

        # ── Transcode mode ─────────────────────────────────────────────────────
        cmd = list(base)

        # Resolve video codec name from hw_accel + codec choice
        if hw_accel and hw_accel.startswith("nvenc"):
            vcodec = "h264_nvenc" if video == "h264" else "hevc_nvenc"
        elif hw_accel and hw_accel.startswith("qsv"):
            vcodec = "h264_qsv" if video == "h264" else "hevc_qsv"
        elif hw_accel and hw_accel.startswith("amf"):
            vcodec = "h264_amf" if video == "h264" else "hevc_amf"
        elif hw_accel and hw_accel == "auto_prefer":
            vcodec = _resolve_auto_prefer(video, self.executable_path)
        elif video == "h264":
            vcodec = "libx264"
        elif video == "h265":
            vcodec = "libx265"
        else:
            vcodec = video  # fallback (copy, av1, etc.)

        # ── Stream mapping ─────────────────────────────────────────────────────
        # Subtitle burn changes the entire map structure: the video must pass
        # through filter_complex (overlay), and -map 0 cannot be used because
        # it would include the raw unfiltered video alongside the overlay output.
        # The two paths therefore use completely separate mapping strategies.
        if sub_burn:
            # Build the overlay filter.  ffmpeg decodes subtitle bitmaps (PGS)
            # and renders text subtitles (ASS/SRT) onto the video frames.
            sub_sel = (
                f"0:s:m:language:{sub_language}" if sub_language else "0:s:0"
            )
            cmd += ["-filter_complex", f"[0:v][{sub_sel}]overlay[v]"]
            cmd += ["-map", "[v]"]          # filtered video output
            # Audio: direct positive maps — no -map 0, so no negative needed
            if audio_language:
                cmd += ["-map", f"0:a:m:language:{audio_language}"]
            elif audio_tracks == "main":
                cmd += ["-map", "0:a:0"]
            else:
                cmd += ["-map", "0:a"]      # all audio tracks
            # No subtitle streams — they are burned into the video
        else:
            # Normal path: -map 0 selects all streams; use negative maps to
            # exclude then re-add specific audio or subtitle streams without
            # creating duplicates.
            cmd += ["-map", "0"]
            # Subtitle streams
            if sub_mode == "none":
                cmd += ["-sn"]
            else:
                cmd += ["-c:s", "copy"]
                if sub_language:
                    cmd += ["-map", "-0:s"]
                    cmd += ["-map", f"0:s:m:language:{sub_language}"]
                elif sub_mode == "forced":
                    cmd += ["-map", "-0:s"]
                    cmd += ["-map", "0:s:m:forced"]
                # sub_mode == "all": -map 0 already covers every subtitle stream
            # Audio tracks
            if audio_language:
                cmd += ["-map", "-0:a"]
                cmd += ["-map", f"0:a:m:language:{audio_language}"]
            elif audio_tracks == "main":
                cmd += ["-map", "-0:a"]
                cmd += ["-map", "0:a:0"]
            # audio_tracks == "all": -map 0 already includes all audio

        # ── Video codec and quality ────────────────────────────────────────────
        cmd += ["-c:v", vcodec]

        if mode == "crf" and crf is not None:
            # -crf is a libx264/libx265 private option; GPU encoders
            # silently DISCARD it and fall back to their defaults
            # (NVENC ≈ 2 Mbps — 4K HDR mush with no error).  Map the
            # quality number onto each encoder's own constant-quality
            # knob instead.
            if vcodec.endswith("_nvenc"):
                cmd += ["-rc", "vbr", "-cq", str(crf), "-b:v", "0"]
            elif vcodec.endswith("_qsv"):
                cmd += ["-global_quality", str(crf)]
            elif vcodec.endswith("_amf"):
                cmd += [
                    "-rc", "cqp",
                    "-qp_i", str(crf),
                    "-qp_p", str(crf),
                    "-qp_b", str(crf),
                ]
            else:
                cmd += ["-crf", str(crf)]

        if preset:
            cmd += ["-preset", preset]

        # Tune (x264/x265 only; GPU encoders may ignore or reject it)
        if tune and vcodec in ("libx265", "libx264"):
            cmd += ["-tune", tune]

        if vid_profile:
            cmd += ["-profile:v", vid_profile]

        if pix_fmt:
            cmd += ["-pix_fmt", pix_fmt]

        if keyint is not None:
            cmd += ["-g", str(keyint)]

        if bframes is not None:
            cmd += ["-bf", str(bframes)]

        if refs is not None:
            cmd += ["-refs", str(refs)]

        if extra_video:
            if vcodec == "libx265":
                cmd += ["-x265-params", extra_video]
            elif vcodec == "libx264":
                cmd += ["-x264-opts", extra_video]
            else:
                # GPU encoder: x265-params are not valid, but translate any
                # color metadata so HDR tagging is not silently lost when the
                # user switches hw_accel in the custom editor.
                cmd += _color_flags_for_gpu(extra_video)

        # ── Audio codec and options ────────────────────────────────────────────
        if audio_mode == "copy":
            cmd += ["-c:a", "copy"]
        elif audio_mode == "aac":
            cmd += ["-c:a", "aac"]
        elif audio_mode == "ac3":
            cmd += ["-c:a", "ac3"]
        elif audio_mode == "eac3":
            cmd += ["-c:a", "eac3"]
        elif audio_mode == "mp3":
            cmd += ["-c:a", "libmp3lame"]
        elif audio_mode == "opus":
            cmd += ["-c:a", "libopus"]
        elif audio_mode == "flac":
            cmd += ["-c:a", "flac"]

        if audio_mode != "copy" and audio_bitrate:
            cmd += ["-b:a", f"{audio_bitrate}k"]

        if audio_channels:
            cmd += ["-ac", str(audio_channels)]
        elif audio_downmix:
            cmd += ["-ac", "2"]

        if audio_sample_rate:
            cmd += ["-ar", str(audio_sample_rate)]

        # ── Metadata & chapters ────────────────────────────────────────────────
        cmd += ["-map_metadata", "0"]
        cmd += ["-map_chapters", "0"]
        # Only set subtitle disposition when the output has subtitle streams
        if not sub_burn and sub_mode != "none":
            cmd += ["-disposition:s:0", "default"]

        # ── Extra output args (user-supplied raw flags) ────────────────────────
        if extra_output_args:
            cmd += extra_output_args.split()

        cmd += [self.output_path]
        return cmd
