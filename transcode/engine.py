import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime

from .ffmpeg_builder import FFmpegBuilder
from .handbrake_builder import HandBrakeBuilder

from shared.ai_diagnostics import (
    ProcessCapture, diag_exception, diag_process, diag_record,
)


FFMPEG_SOURCE_MODE_SAFE_COPY = "safe_copy"
FFMPEG_SOURCE_MODE_FAST_DIRECT = "fast_direct"
TRANSCODE_ABORT_RETURN_CODE = -2
_FFMPEG_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")
_FFMPEG_COPY_CHUNK_SIZE = 8 * 1024 * 1024
_FFMPEG_COPY_LOG_PERCENT_STEP = 1


class TranscodeAbortRequested(Exception):
    """Raised when the shared app abort flag stops a transcode job."""


def normalize_ffmpeg_source_mode(value: str | None) -> str:
    token = str(value or "").strip().lower()
    if token in {FFMPEG_SOURCE_MODE_FAST_DIRECT, "fast", "direct", "read_original"}:
        return FFMPEG_SOURCE_MODE_FAST_DIRECT
    return FFMPEG_SOURCE_MODE_SAFE_COPY


def describe_ffmpeg_source_mode(value: str | None) -> str:
    mode = normalize_ffmpeg_source_mode(value)
    if mode == FFMPEG_SOURCE_MODE_FAST_DIRECT:
        return (
            "Reads the original MKV directly and still writes a separate output file. "
            "Faster and lighter on free space, but FFmpeg works against the original path during the transcode."
        )
    return (
        "Copies the source MKV to a temporary working file before the transcode starts. "
        "Safest for the original file, but it needs extra free space roughly equal to the source size."
    )


def _emit_feedback(feedback_cb, message: str) -> None:
    if not callable(feedback_cb):
        return
    try:
        feedback_cb(message)
    except Exception:
        return


def _emit_progress(progress_cb, **payload) -> None:
    if not callable(progress_cb):
        return
    try:
        progress_cb(payload)
    except Exception:
        return


def _parse_ffmpeg_time_seconds(line: str) -> float | None:
    match = _FFMPEG_TIME_RE.search(str(line or ""))
    if not match:
        return None
    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    return (hours * 3600) + (minutes * 60) + seconds


def _format_duration(seconds: float | None) -> str:
    if seconds is None or seconds <= 0:
        return "00:00"
    total_seconds = int(max(0, round(seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _format_bytes(num_bytes: int) -> str:
    value = float(max(0, int(num_bytes)))
    units = ["B", "KB", "MB", "GB", "TB"]
    index = 0
    while value >= 1024.0 and index < len(units) - 1:
        value /= 1024.0
        index += 1
    if index == 0:
        return f"{int(value)} {units[index]}"
    return f"{value:.2f} {units[index]}"


class TranscodeEngine:
    def __init__(
        self,
        log_dir: str = "logs",
        ffmpeg_exe: str = "ffmpeg",
        ffprobe_exe: str = "",
        handbrake_exe: str = "HandBrakeCLI",
        ffmpeg_source_mode: str = FFMPEG_SOURCE_MODE_SAFE_COPY,
        temp_root: str | None = None,
        abort_event=None,
    ):
        self.log_dir = log_dir
        self.ffmpeg_exe = ffmpeg_exe or "ffmpeg"
        self.ffprobe_exe = ffprobe_exe or ""
        self.handbrake_exe = handbrake_exe or "HandBrakeCLI"
        self.ffmpeg_source_mode = normalize_ffmpeg_source_mode(ffmpeg_source_mode)
        self.temp_root = os.path.normpath(str(temp_root)) if str(temp_root or "").strip() else ""
        self.abort_event = abort_event
        self._active_staging_dir = ""
        os.makedirs(self.log_dir, exist_ok=True)

    def _abort_requested(self) -> bool:
        return bool(self.abort_event is not None and self.abort_event.is_set())

    def _raise_if_aborted(self) -> None:
        if self._abort_requested():
            raise TranscodeAbortRequested("Transcode job was aborted.")

    @staticmethod
    def _process_is_running(proc) -> bool:
        try:
            return proc.poll() is None
        except AttributeError:
            return False

    def _terminate_process(self, proc) -> None:
        try:
            if not self._process_is_running(proc):
                return
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
        except Exception:
            pass

    def _watch_abort_for_process(self, proc) -> None:
        abort_event = self.abort_event
        if abort_event is None:
            return

        def _watch() -> None:
            while self._process_is_running(proc):
                if abort_event.wait(timeout=0.25):
                    self._terminate_process(proc)
                    return

        threading.Thread(target=_watch, daemon=True).start()

    def _watch_stall(self, proc, stall_state, stall_timeout, feedback_cb=None) -> None:
        """Background thread that checks for output stalls during transcode."""
        def _watch() -> None:
            try:
                while self._process_is_running(proc):
                    time.sleep(5.0)
                    if not self._process_is_running(proc):
                        break
                    elapsed = time.time() - stall_state["last_output_time"]
                    if elapsed > stall_timeout and not stall_state["warned"]:
                        stall_state["warned"] = True
                        msg = (
                            f"No output for {int(elapsed)}s — "
                            "process may be stalled or processing a complex segment."
                        )
                        _emit_feedback(feedback_cb, msg)
                        diag_record(
                            "warning", "stall_timeout", msg,
                            details={"elapsed_seconds": elapsed, "timeout": stall_timeout},
                        )
            except Exception:
                pass  # Never crash the watchdog thread

        threading.Thread(target=_watch, daemon=True).start()

    def _build_command(self, job, *, input_path: str | None = None):
        effective_input_path = input_path or job.input_path
        backend = getattr(job, "backend", "ffmpeg")
        if backend == "handbrake":
            options = getattr(job, "backend_options", {}) or {}
            builder = HandBrakeBuilder(
                effective_input_path,
                job.output_path,
                options.get("preset", "Fast 1080p30"),
                getattr(job, "metadata", {}),
                executable_path=self.handbrake_exe,
                settings=options,
            )
            return builder.build_command()

        if getattr(job, "profile", None) is None:
            raise ValueError("FFmpeg transcode jobs require a profile.")
        builder = FFmpegBuilder(
            job.profile,
            effective_input_path,
            job.output_path,
            job.metadata,
            executable_path=self.ffmpeg_exe,
        )
        return builder.build_command()

    def _resolve_temp_root(self) -> str:
        root = self.temp_root or tempfile.gettempdir()
        os.makedirs(root, exist_ok=True)
        return root

    def _resolve_source_duration_seconds(self, job) -> float | None:
        metadata = getattr(job, "metadata", {})
        if not isinstance(metadata, dict):
            metadata = None
        for key in ("source_duration_seconds", "duration_seconds"):
            raw_value = metadata.get(key) if metadata is not None else None
            try:
                duration = float(raw_value)
            except (TypeError, ValueError):
                continue
            if duration > 0:
                return duration
        probed_duration = self._probe_duration_seconds(getattr(job, "input_path", ""))
        if probed_duration and metadata is not None:
            metadata["source_duration_seconds"] = probed_duration
        return probed_duration

    def _probe_duration_seconds(self, input_path: str) -> float | None:
        ffprobe_exe = os.path.normpath(str(self.ffprobe_exe or ""))
        source_path = os.path.normpath(str(input_path or ""))
        if not ffprobe_exe or not os.path.isfile(ffprobe_exe):
            return None
        if not source_path or not os.path.isfile(source_path):
            return None
        creationflags = 0x08000000 if os.name == "nt" else 0
        try:
            proc = subprocess.run(
                [
                    ffprobe_exe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    source_path,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15,
                creationflags=creationflags,
            )
        except Exception:
            return None
        if proc.returncode != 0:
            return None
        try:
            duration = float((proc.stdout or "").strip())
        except (TypeError, ValueError):
            return None
        return duration if duration > 0 else None

    def _stage_ffmpeg_input_copy(self, job, logf, feedback_cb=None, progress_cb=None):
        self._raise_if_aborted()
        source_path = os.path.normpath(str(getattr(job, "input_path", "") or ""))
        if not source_path:
            raise ValueError("FFmpeg transcode jobs require an input path.")
        if not os.path.isfile(source_path):
            raise FileNotFoundError(f"FFmpeg source file was not found: {source_path}")

        staging_root = self._resolve_temp_root()
        staging_dir = tempfile.mkdtemp(prefix="JellyRipFFmpeg_", dir=staging_root)
        self._active_staging_dir = staging_dir
        staged_input_path = os.path.join(staging_dir, os.path.basename(source_path))
        total_bytes = os.path.getsize(source_path)
        source_name = os.path.basename(source_path)

        logf.write(f"ORIGINAL_INPUT: {source_path}\n")
        logf.write(f"STAGING_ROOT: {staging_root}\n")
        logf.write(f"STAGING_DIR: {staging_dir}\n")
        logf.write(
            "SOURCE_SAFETY: Safe copy mode never moves, renames, or overwrites the original source file.\n"
        )
        logf.flush()

        start_message = (
            f"Copying source to temp folder: {source_name} "
            f"({_format_bytes(total_bytes)})"
        )
        _emit_feedback(feedback_cb, start_message)
        _emit_progress(
            progress_cb,
            phase="copy",
            percent=0.0,
            message=start_message,
            bytes_copied=0,
            bytes_total=total_bytes,
            staging_dir=staging_dir,
        )

        copied_bytes = 0
        last_percent_bucket = -1
        with open(source_path, "rb") as src, open(staged_input_path, "wb") as dst:
            while True:
                self._raise_if_aborted()
                chunk = src.read(_FFMPEG_COPY_CHUNK_SIZE)
                if not chunk:
                    break
                self._raise_if_aborted()
                dst.write(chunk)
                copied_bytes += len(chunk)
                if total_bytes <= 0:
                    continue
                percent = min(100.0, (copied_bytes / total_bytes) * 100.0)
                percent_bucket = int(percent // _FFMPEG_COPY_LOG_PERCENT_STEP)
                if (
                    copied_bytes >= total_bytes
                    or percent_bucket > last_percent_bucket
                ):
                    progress_message = (
                        f"Copying source to temp: {percent_bucket}% "
                        f"({_format_bytes(copied_bytes)} / {_format_bytes(total_bytes)})"
                    )
                    logf.write(f"{progress_message}\n")
                    logf.flush()
                    _emit_feedback(feedback_cb, progress_message)
                    _emit_progress(
                        progress_cb,
                        phase="copy",
                        percent=percent,
                        message=progress_message,
                        bytes_copied=copied_bytes,
                        bytes_total=total_bytes,
                        staging_dir=staging_dir,
                    )
                    last_percent_bucket = percent_bucket
        self._raise_if_aborted()
        shutil.copystat(source_path, staged_input_path)

        ready_message = "Safe working copy is ready. Starting FFmpeg."
        logf.write(f"WORKING_INPUT: {staged_input_path}\n")
        logf.write(f"{ready_message}\n")
        logf.flush()
        _emit_feedback(feedback_cb, ready_message)
        _emit_progress(
            progress_cb,
            phase="copy",
            percent=100.0,
            message=ready_message,
            bytes_copied=total_bytes,
            bytes_total=total_bytes,
            staging_dir=staging_dir,
        )
        return staged_input_path, staging_dir

    def _resolve_ffmpeg_source_mode(self, job) -> str:
        metadata = getattr(job, "metadata", {})
        if isinstance(metadata, dict):
            return normalize_ffmpeg_source_mode(
                metadata.get("ffmpeg_source_mode") or self.ffmpeg_source_mode
            )
        return self.ffmpeg_source_mode

    def run_job(self, job, dry_run=False, feedback_cb=None, progress_cb=None):
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        log_path = os.path.join(self.log_dir, f"transcode_{timestamp}.txt")
        working_input_path = None
        staging_dir = None
        if dry_run:
            cmd = self._build_command(job)
            print("[DRY RUN] Command:", " ".join(cmd))
            return cmd
        try:
            with open(log_path, "w", encoding="utf-8") as logf:
                backend = getattr(job, "backend", "ffmpeg")
                _emit_progress(
                    progress_cb,
                    phase="prepare",
                    percent=0.0,
                    message=f"Preparing {backend} job for {os.path.basename(job.output_path)}",
                )
                if backend == "ffmpeg":
                    ffmpeg_source_mode = self._resolve_ffmpeg_source_mode(job)
                    logf.write(f"FFMPEG_SOURCE_MODE: {ffmpeg_source_mode}\n")
                    logf.write(
                        f"FFMPEG_SOURCE_MODE_DESC: {describe_ffmpeg_source_mode(ffmpeg_source_mode)}\n"
                    )
                    if ffmpeg_source_mode == FFMPEG_SOURCE_MODE_SAFE_COPY:
                        working_input_path, staging_dir = self._stage_ffmpeg_input_copy(
                            job,
                            logf,
                            feedback_cb=feedback_cb,
                            progress_cb=progress_cb,
                        )
                    else:
                        working_input_path = job.input_path
                        direct_message = "Using the original source file directly for FFmpeg."
                        logf.write(f"INPUT: {job.input_path}\n")
                        logf.write(f"WORKING_INPUT: {job.input_path}\n")
                        logf.write(f"{direct_message}\n")
                        logf.flush()
                        _emit_feedback(feedback_cb, direct_message)
                        _emit_progress(
                            progress_cb,
                            phase="copy",
                            percent=100.0,
                            message=direct_message,
                        )
                else:
                    logf.write(f"INPUT: {job.input_path}\n")
                self._raise_if_aborted()
                cmd = self._build_command(job, input_path=working_input_path)
                logf.write(f"BACKEND: {backend}\n")
                logf.write("COMMAND: " + " ".join(cmd) + "\n")
                logf.write(
                    f"OUTPUT: {job.output_path}\n"
                    f"PROFILE: {getattr(job.profile, 'name', '')}\n"
                )
                logf.write(f"METADATA: {repr(getattr(job, 'metadata', {}))}\n\n")
                logf.flush()
                launch_message = f"Starting {backend} process for {os.path.basename(job.output_path)}"
                _emit_feedback(feedback_cb, launch_message)
                _emit_progress(
                    progress_cb,
                    phase="launch",
                    message=launch_message,
                )
                _diag_capture = ProcessCapture(
                    command=list(cmd),
                    start_time=datetime.now().isoformat(),
                    working_directory=os.getcwd(),
                )
                _diag_raw_lines: list[str] = []
                _transcode_start = time.time()

                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                self._watch_abort_for_process(proc)
                duration_seconds = self._resolve_source_duration_seconds(job)
                last_encode_bucket = -1
                last_runtime_bucket = -1
                _STALL_TIMEOUT = 120  # seconds with no output
                stall_state = {"last_output_time": time.time(), "warned": False}
                self._watch_stall(proc, stall_state, _STALL_TIMEOUT, feedback_cb)
                if proc.stdout is not None:
                    for line in proc.stdout:
                        stall_state["last_output_time"] = time.time()
                        stall_state["warned"] = False
                        if self._abort_requested():
                            self._terminate_process(proc)
                            break
                        logf.write(line)
                        logf.flush()
                        if len(_diag_raw_lines) < 5000:
                            _diag_raw_lines.append(line.rstrip())
                        if backend != "ffmpeg":
                            continue
                        encoded_seconds = _parse_ffmpeg_time_seconds(line)
                        if encoded_seconds is None:
                            continue
                        if duration_seconds and duration_seconds > 0:
                            percent = min(100.0, (encoded_seconds / duration_seconds) * 100.0)
                            percent_bucket = int(percent // 5)
                            progress_message = (
                                f"FFmpeg progress: {percent:.0f}% "
                                f"({_format_duration(encoded_seconds)} / {_format_duration(duration_seconds)})"
                            )
                            _emit_progress(
                                progress_cb,
                                phase="encode",
                                percent=percent,
                                message=progress_message,
                                current_seconds=encoded_seconds,
                                total_seconds=duration_seconds,
                            )
                            if percent_bucket > last_encode_bucket:
                                _emit_feedback(feedback_cb, progress_message)
                                last_encode_bucket = percent_bucket
                        else:
                            runtime_bucket = int(encoded_seconds // 60)
                            runtime_message = (
                                f"FFmpeg is running: {_format_duration(encoded_seconds)} encoded so far"
                            )
                            _emit_progress(
                                progress_cb,
                                phase="encode",
                                message=runtime_message,
                                current_seconds=encoded_seconds,
                            )
                            if runtime_bucket > last_runtime_bucket:
                                _emit_feedback(feedback_cb, runtime_message)
                                last_runtime_bucket = runtime_bucket
                proc.wait()

                # Finalize diagnostic capture
                _diag_capture.exit_code = proc.returncode
                _diag_capture.end_time = datetime.now().isoformat()
                _diag_capture.duration_seconds = time.time() - _transcode_start
                _diag_capture.stdout = "\n".join(_diag_raw_lines[-200:])
                _diag_capture.stall_detected = stall_state["warned"]

                if self._abort_requested():
                    logf.write("\nABORTED: Transcode job was aborted.\n")
                    logf.flush()
                    _emit_feedback(feedback_cb, "Transcode job aborted.")
                    _emit_progress(
                        progress_cb,
                        phase="aborted",
                        message="Transcode job aborted.",
                    )
                    return TRANSCODE_ABORT_RETURN_CODE, log_path
                logf.write(f"\nExit code: {proc.returncode}\n")
                logf.flush()

                # AI diagnostics: report failures
                if proc.returncode != 0:
                    diag_process(_diag_capture, success=False,
                                 category="subprocess_nonzero_exit")
                elif stall_state["warned"]:
                    diag_record(
                        "warning", "stall_timeout",
                        f"{backend} completed but had output stalls (exit {proc.returncode})",
                        details=_diag_capture.to_dict(),
                    )

                return proc.returncode, log_path
        except TranscodeAbortRequested as exc:
            with open(log_path, "a", encoding="utf-8") as logf:
                logf.write(f"\nABORTED: {exc}\n")
            _emit_feedback(feedback_cb, "Transcode job aborted.")
            _emit_progress(
                progress_cb,
                phase="aborted",
                message="Transcode job aborted.",
            )
            return TRANSCODE_ABORT_RETURN_CODE, log_path
        except Exception as exc:
            with open(log_path, "a", encoding="utf-8") as logf:
                logf.write(f"\nERROR: {repr(exc)}\n")
            diag_exception(exc, context="TranscodeEngine.run_job")
            return -1, log_path
        finally:
            cleanup_dir = staging_dir or self._active_staging_dir
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
            self._active_staging_dir = ""
