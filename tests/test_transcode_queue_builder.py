import os
import io
import threading


def test_describe_profile_summarizes_default_profile(tmp_path):
    from transcode.profiles import ProfileLoader, describe_profile

    loader = ProfileLoader(str(tmp_path / "profiles.json"))

    summary = describe_profile(loader.get_profile())

    assert "Video: H.265, CRF 22, medium preset, auto GPU/CPU" in summary
    assert "Audio: copy all tracks" in summary
    assert "Subtitles: copy all" in summary
    assert "Skip: under 7 GB, already H.265" in summary
    assert "Metadata: preserve" in summary


def test_build_transcode_plan_preserves_relative_mkv_paths(tmp_path):
    from transcode.planner import build_transcode_plan

    source_root = tmp_path / "source"
    output_root = tmp_path / "output"
    first = source_root / "Season 01" / "episode01.mkv"
    second = source_root / "Extras" / "featurette.mkv"
    first.parent.mkdir(parents=True, exist_ok=True)
    second.parent.mkdir(parents=True, exist_ok=True)
    first.write_text("x", encoding="utf-8")
    second.write_text("x", encoding="utf-8")

    plans = build_transcode_plan(
        str(source_root),
        [str(first), str(second), str(first)],
        str(output_root),
    )

    assert [plan["relative_path"] for plan in plans] == [
        os.path.normpath("Season 01\\episode01.mkv"),
        os.path.normpath("Extras\\featurette.mkv"),
    ]
    assert [plan["output_relative_path"] for plan in plans] == [
        os.path.normpath("Season 01\\episode01.mkv"),
        os.path.normpath("Extras\\featurette.mkv"),
    ]
    assert [plan["output_path"] for plan in plans] == [
        os.path.normpath(str(output_root / "Season 01" / "episode01.mkv")),
        os.path.normpath(str(output_root / "Extras" / "featurette.mkv")),
    ]


def test_build_transcode_plan_falls_back_to_basename_for_outside_path(tmp_path):
    from transcode.planner import build_transcode_plan

    source_root = tmp_path / "source"
    outside_file = tmp_path / "other" / "movie.mkv"
    outside_file.parent.mkdir(parents=True, exist_ok=True)
    outside_file.write_text("x", encoding="utf-8")

    plans = build_transcode_plan(
        str(source_root),
        [str(outside_file)],
        str(tmp_path / "output"),
    )

    assert len(plans) == 1
    assert plans[0]["relative_path"] == "movie.mkv"
    assert plans[0]["output_relative_path"] == "movie.mkv"


def test_suggest_transcode_output_root_mentions_backend(tmp_path):
    from transcode.planner import suggest_transcode_output_root

    scan_root = tmp_path / "My MKVs"
    scan_root.mkdir()

    ffmpeg_output = suggest_transcode_output_root(str(scan_root), "ffmpeg")
    handbrake_output = suggest_transcode_output_root(str(scan_root), "handbrake")

    assert ffmpeg_output.endswith("My MKVs - FFmpeg Output")
    assert handbrake_output.endswith("My MKVs - HandBrake Output")


def test_build_queue_jobs_for_ffmpeg_uses_scan_metadata(tmp_path):
    from transcode.planner import build_transcode_plan
    from transcode.profiles import ProfileLoader
    from transcode.queue_builder import build_queue_jobs

    source_root = tmp_path / "source"
    output_root = tmp_path / "output"
    input_path = source_root / "Season 01" / "episode01.mkv"
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_text("x", encoding="utf-8")

    loader = ProfileLoader(str(tmp_path / "profiles.json"))
    plans = build_transcode_plan(str(source_root), [str(input_path)], str(output_root))
    result = build_queue_jobs(
        plans=plans,
        profile_loader=loader,
        backend="ffmpeg",
        option_value=loader.default or "",
        ffmpeg_source_mode="safe_copy",
        selected_entries=[
            {
                "path": str(input_path),
                "duration_seconds": 123.4,
            }
        ],
    )

    assert result.queue_detail == "Profile: Balanced (Recommended) | Source: Safe (Copy First)"
    assert len(result.jobs) == 1
    job = result.jobs[0]
    assert job.backend == "ffmpeg"
    assert job.metadata["source_relative_path"] == os.path.normpath("Season 01\\episode01.mkv")
    assert job.metadata["ffmpeg_source_mode"] == "safe_copy"
    assert job.metadata["source_duration_seconds"] == 123.4


def test_build_recommendation_job_returns_plain_job_and_queue_detail(tmp_path):
    from transcode.planner import build_transcode_plan
    from transcode.queue_builder import build_recommendation_job

    source_root = tmp_path / "source"
    output_root = tmp_path / "output"
    input_path = source_root / "movie.mkv"
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_text("x", encoding="utf-8")

    plan = build_transcode_plan(str(source_root), [str(input_path)], str(output_root))[0]
    result = build_recommendation_job(
        plan=plan,
        analysis={
            "path": str(input_path),
            "name": "movie.mkv",
            "video_codec": "mpeg2video",
            "width": 1920,
            "height": 1080,
            "bitrate_bps": 8_000_000,
            "duration_seconds": 5400.0,
        },
        recommendation={
            "id": "balanced",
            "label": "Balanced",
            "details": "Good tradeoff",
            "crf": 20,
            "preset": "slow",
            "profile_name": "Balanced",
            "profile_data": {
                "video": {
                    "codec": "h265",
                    "mode": "crf",
                    "crf": 20,
                    "bitrate": None,
                    "preset": "slow",
                    "hw_accel": "cpu",
                },
                "audio": {
                    "mode": "copy",
                    "language": None,
                    "tracks": "all",
                },
                "subtitles": {
                    "mode": "all",
                    "burn": False,
                    "language": None,
                },
                "output": {
                    "container": "mkv",
                    "naming": "{title}_{profile}",
                    "overwrite": False,
                    "auto_increment": True,
                },
                "constraints": {
                    "skip_if_below_gb": None,
                    "skip_if_codec_matches": False,
                },
                "metadata": {
                    "preserve": True,
                },
            },
        },
        ffmpeg_source_mode="safe_copy",
    )

    assert len(result.jobs) == 1
    job = result.jobs[0]
    assert job.input_path == str(input_path)
    assert job.backend == "ffmpeg"
    assert job.metadata["recommendation_id"] == "balanced"
    assert job.metadata["source_resolution"] == "1920x1080"
    assert result.queue_detail == (
        "Balanced recommendation | CRF 20 | preset slow | source Safe (Copy First)"
    )


def test_ffmpeg_builder_uses_custom_executable(tmp_path):
    from transcode.ffmpeg_builder import FFmpegBuilder
    from transcode.profiles import ProfileLoader

    loader = ProfileLoader(str(tmp_path / "profiles.json"))
    profile = loader.get_profile()
    cmd = FFmpegBuilder(
        profile,
        "input.mkv",
        "output.mkv",
        executable_path="C:\\Tools\\ffmpeg.exe",
    ).build_command()

    assert cmd[0] == "C:\\Tools\\ffmpeg.exe"
    assert cmd[-1] == "output.mkv"


def test_handbrake_engine_dry_run_uses_preset_and_custom_executable(tmp_path):
    from core.pipeline import TranscodeJob
    from transcode.engine import TranscodeEngine

    job = TranscodeJob(
        "input.mkv",
        "output.mkv",
        profile=None,
        backend="handbrake",
        backend_options={"preset": "Fast 1080p30"},
    )
    engine = TranscodeEngine(
        log_dir=str(tmp_path / "logs"),
        handbrake_exe="C:\\Tools\\HandBrakeCLI.exe",
    )
    cmd = engine.run_job(job, dry_run=True)

    assert cmd[0] == "C:\\Tools\\HandBrakeCLI.exe"
    assert "--preset" in cmd
    assert "Fast 1080p30" in cmd


def test_ffmpeg_engine_stages_temp_copy_and_leaves_original_untouched(tmp_path, monkeypatch):
    from core.pipeline import TranscodeJob
    from transcode.engine import TranscodeEngine
    from transcode.profiles import ProfileLoader

    source_dir = tmp_path / "source"
    output_dir = tmp_path / "output"
    temp_root = tmp_path / "temp"
    input_path = source_dir / "movie.mkv"
    output_path = output_dir / "movie_h265.mkv"
    source_dir.mkdir(parents=True, exist_ok=True)
    input_path.write_text("original-mkv-data", encoding="utf-8")

    loader = ProfileLoader(str(tmp_path / "profiles.json"))
    job = TranscodeJob(
        str(input_path),
        str(output_path),
        loader.get_profile(),
    )
    engine = TranscodeEngine(
        log_dir=str(tmp_path / "logs"),
        ffmpeg_exe="C:\\Tools\\ffmpeg.exe",
        temp_root=str(temp_root),
    )

    captured = {}

    class _Proc:
        def __init__(self, cmd):
            captured["cmd"] = cmd
            # Input path located via -i (the builder now prefixes
            # -nostdin and the overwrite flag, so positions shift).
            staged_input = cmd[cmd.index("-i") + 1]
            captured["staged_input"] = staged_input
            assert staged_input != str(input_path)
            assert os.path.exists(staged_input)
            assert os.path.commonpath([staged_input, str(temp_root)]) == str(temp_root)
            assert os.path.commonpath([staged_input, str(output_dir)]) != str(output_dir)
            with open(staged_input, "r", encoding="utf-8") as fh:
                assert fh.read() == "original-mkv-data"
            self.stdout = io.StringIO("ffmpeg output\n")
            self.returncode = 0

        def wait(self):
            return self.returncode

    monkeypatch.setattr(
        "transcode.engine.subprocess.Popen",
        lambda cmd, stdout, stderr, text: _Proc(cmd),
    )

    ret, log_path = engine.run_job(job)

    assert ret == 0
    assert captured["cmd"][0] == "C:\\Tools\\ffmpeg.exe"
    assert captured["cmd"][-1] == str(output_path)
    assert os.path.exists(input_path)
    assert not os.path.exists(captured["staged_input"])
    log_text = (tmp_path / "logs" / os.path.basename(log_path)).read_text(encoding="utf-8")
    assert "FFMPEG_SOURCE_MODE: safe_copy" in log_text
    assert f"ORIGINAL_INPUT: {input_path}" in log_text
    assert f"STAGING_ROOT: {temp_root}" in log_text
    assert "WORKING_INPUT:" in log_text


def test_ffmpeg_engine_fast_mode_reads_original_directly(tmp_path, monkeypatch):
    from core.pipeline import TranscodeJob
    from transcode.engine import TranscodeEngine
    from transcode.profiles import ProfileLoader

    input_path = tmp_path / "movie.mkv"
    output_path = tmp_path / "movie_h265.mkv"
    input_path.write_text("original-mkv-data", encoding="utf-8")

    loader = ProfileLoader(str(tmp_path / "profiles.json"))
    job = TranscodeJob(
        str(input_path),
        str(output_path),
        loader.get_profile(),
        metadata={"ffmpeg_source_mode": "fast_direct"},
    )
    engine = TranscodeEngine(
        log_dir=str(tmp_path / "logs"),
        ffmpeg_exe="C:\\Tools\\ffmpeg.exe",
    )

    captured = {}

    class _Proc:
        def __init__(self, cmd):
            captured["cmd"] = cmd
            self.stdout = io.StringIO("ffmpeg output\n")
            self.returncode = 0

        def wait(self):
            return self.returncode

    monkeypatch.setattr(
        "transcode.engine.subprocess.Popen",
        lambda cmd, stdout, stderr, text: _Proc(cmd),
    )

    ret, log_path = engine.run_job(job)

    assert ret == 0
    cmd = captured["cmd"]
    assert cmd[cmd.index("-i") + 1] == str(input_path)
    log_text = (tmp_path / "logs" / os.path.basename(log_path)).read_text(encoding="utf-8")
    assert "FFMPEG_SOURCE_MODE: fast_direct" in log_text
    assert f"INPUT: {input_path}" in log_text
    assert f"WORKING_INPUT: {input_path}" in log_text
    assert "ORIGINAL_INPUT:" not in log_text


def test_ffmpeg_engine_reports_copy_and_encode_progress(tmp_path, monkeypatch):
    from core.pipeline import TranscodeJob
    from transcode.engine import TranscodeEngine
    from transcode.profiles import ProfileLoader

    input_path = tmp_path / "movie.mkv"
    output_path = tmp_path / "movie_h265.mkv"
    input_path.write_bytes(b"x" * (1024 * 1024))

    loader = ProfileLoader(str(tmp_path / "profiles.json"))
    job = TranscodeJob(
        str(input_path),
        str(output_path),
        loader.get_profile(),
        metadata={"source_duration_seconds": 100.0},
    )
    engine = TranscodeEngine(
        log_dir=str(tmp_path / "logs"),
        ffmpeg_exe="C:\\Tools\\ffmpeg.exe",
        temp_root=str(tmp_path / "temp"),
    )

    progress_events = []
    feedback_messages = []

    class _Proc:
        def __init__(self, cmd):
            self.stdout = io.StringIO(
                "frame=1 fps=1.0 time=00:00:25.00 bitrate=1000.0kbits/s\n"
                "frame=2 fps=1.0 time=00:00:50.00 bitrate=1000.0kbits/s\n"
                "frame=3 fps=1.0 time=00:01:40.00 bitrate=1000.0kbits/s\n"
            )
            self.returncode = 0

        def wait(self):
            return self.returncode

    monkeypatch.setattr(
        "transcode.engine.subprocess.Popen",
        lambda cmd, stdout, stderr, text: _Proc(cmd),
    )

    ret, _log_path = engine.run_job(
        job,
        feedback_cb=feedback_messages.append,
        progress_cb=progress_events.append,
    )

    assert ret == 0
    copy_events = [event for event in progress_events if event.get("phase") == "copy"]
    encode_events = [event for event in progress_events if event.get("phase") == "encode"]
    assert copy_events
    assert encode_events
    assert copy_events[0]["percent"] == 0.0
    assert copy_events[-1]["percent"] == 100.0
    assert round(encode_events[0]["percent"], 1) == 25.0
    assert round(encode_events[-1]["percent"], 1) == 100.0
    assert any("Copying source to temp" in message for message in feedback_messages)
    assert any("FFmpeg progress: 50%" in message for message in feedback_messages)


def test_ffmpeg_safe_copy_logs_only_when_displayed_percent_changes(tmp_path, monkeypatch):
    from core.pipeline import TranscodeJob
    from transcode.engine import TranscodeEngine
    from transcode.profiles import ProfileLoader

    input_path = tmp_path / "movie.mkv"
    output_path = tmp_path / "movie_h265.mkv"
    input_path.write_bytes(b"x" * 250)

    loader = ProfileLoader(str(tmp_path / "profiles.json"))
    job = TranscodeJob(
        str(input_path),
        str(output_path),
        loader.get_profile(),
    )
    engine = TranscodeEngine(
        log_dir=str(tmp_path / "logs"),
        ffmpeg_exe="C:\\Tools\\ffmpeg.exe",
        temp_root=str(tmp_path / "temp"),
    )
    feedback_messages = []

    class _Proc:
        stdout = io.StringIO("")
        returncode = 0

        def wait(self):
            return self.returncode

    tick = {"value": 0.0}

    def _fake_monotonic():
        tick["value"] += 2.0
        return tick["value"]

    monkeypatch.setattr("transcode.engine._FFMPEG_COPY_CHUNK_SIZE", 1)
    monkeypatch.setattr("transcode.engine.time.monotonic", _fake_monotonic)
    monkeypatch.setattr(
        "transcode.engine.subprocess.Popen",
        lambda cmd, stdout, stderr, text: _Proc(),
    )

    ret, _log_path = engine.run_job(job, feedback_cb=feedback_messages.append)

    assert ret == 0
    copy_lines = [
        message for message in feedback_messages
        if str(message).startswith("Copying source to temp:")
    ]
    displayed_percents = [
        int(str(message).split(": ", 1)[1].split("%", 1)[0])
        for message in copy_lines
    ]
    assert displayed_percents[0] == 0
    assert displayed_percents[-1] == 100
    assert displayed_percents == sorted(set(displayed_percents))


def test_ffmpeg_engine_aborts_during_safe_copy(tmp_path, monkeypatch):
    from core.pipeline import TranscodeJob
    from transcode.engine import TRANSCODE_ABORT_RETURN_CODE, TranscodeEngine
    from transcode.profiles import ProfileLoader

    input_path = tmp_path / "movie.mkv"
    output_path = tmp_path / "movie_h265.mkv"
    temp_root = tmp_path / "temp"
    input_path.write_bytes(b"0123456789abcdef")
    abort_event = threading.Event()

    loader = ProfileLoader(str(tmp_path / "profiles.json"))
    job = TranscodeJob(
        str(input_path),
        str(output_path),
        loader.get_profile(),
    )
    engine = TranscodeEngine(
        log_dir=str(tmp_path / "logs"),
        ffmpeg_exe="C:\\Tools\\ffmpeg.exe",
        temp_root=str(temp_root),
        abort_event=abort_event,
    )

    feedback_messages = []

    def _feedback(message):
        feedback_messages.append(message)
        if str(message).startswith("Copying source to temp:"):
            abort_event.set()

    def _unexpected_popen(*args, **kwargs):
        raise AssertionError("FFmpeg should not start after safe-copy abort")

    monkeypatch.setattr("transcode.engine._FFMPEG_COPY_CHUNK_SIZE", 4)
    monkeypatch.setattr("transcode.engine.subprocess.Popen", _unexpected_popen)

    ret, log_path = engine.run_job(job, feedback_cb=_feedback)

    assert ret == TRANSCODE_ABORT_RETURN_CODE
    assert input_path.exists()
    assert not output_path.exists()
    assert not list(temp_root.glob("JellyRipFFmpeg_*"))
    assert any("Transcode job aborted" in message for message in feedback_messages)
    assert "ABORTED: Transcode job was aborted." in open(
        log_path,
        encoding="utf-8",
    ).read()


def test_ffmpeg_engine_probes_duration_when_metadata_is_missing(tmp_path, monkeypatch):
    from core.pipeline import TranscodeJob
    from transcode.engine import TranscodeEngine
    from transcode.profiles import ProfileLoader

    input_path = tmp_path / "movie.mkv"
    output_path = tmp_path / "movie_h265.mkv"
    ffprobe_path = tmp_path / "ffprobe.exe"
    input_path.write_bytes(b"x" * 1024)
    ffprobe_path.write_text("stub", encoding="utf-8")

    loader = ProfileLoader(str(tmp_path / "profiles.json"))
    job = TranscodeJob(
        str(input_path),
        str(output_path),
        loader.get_profile(),
    )
    engine = TranscodeEngine(
        log_dir=str(tmp_path / "logs"),
        ffmpeg_exe="C:\\Tools\\ffmpeg.exe",
        ffprobe_exe=str(ffprobe_path),
        temp_root=str(tmp_path / "temp"),
    )

    class _RunResult:
        returncode = 0
        stdout = "120.0\n"
        stderr = ""

    class _Proc:
        def __init__(self, cmd):
            self.stdout = io.StringIO(
                "frame=1 fps=1.0 time=00:01:00.00 bitrate=1000.0kbits/s\n"
            )
            self.returncode = 0

        def wait(self):
            return self.returncode

    monkeypatch.setattr("transcode.engine.subprocess.run", lambda *args, **kwargs: _RunResult())
    monkeypatch.setattr(
        "transcode.engine.subprocess.Popen",
        lambda cmd, stdout, stderr, text: _Proc(cmd),
    )

    progress_events = []
    ret, _log_path = engine.run_job(job, progress_cb=progress_events.append)

    assert ret == 0
    encode_events = [event for event in progress_events if event.get("phase") == "encode"]
    assert encode_events
    assert round(encode_events[0]["percent"], 1) == 50.0
    assert job.metadata["source_duration_seconds"] == 120.0


def test_transcode_queue_maps_ffmpeg_progress_to_overall_percent(tmp_path, monkeypatch):
    from core.pipeline import TranscodeJob
    from transcode.profiles import ProfileLoader
    from transcode.queue import TranscodeQueue

    loader = ProfileLoader(str(tmp_path / "profiles.json"))
    queue = TranscodeQueue(log_dir=str(tmp_path / "logs"))
    job = TranscodeJob(
        "input.mkv",
        "output.mkv",
        loader.get_profile(),
        metadata={"ffmpeg_source_mode": "safe_copy", "source_duration_seconds": 100.0},
    )
    queue.add_job(job)

    def _fake_run_job(job, dry_run=False, feedback_cb=None, progress_cb=None):
        progress_cb({"phase": "copy", "percent": 50.0, "message": "copy halfway"})
        progress_cb({"phase": "copy", "percent": 100.0, "message": "copy done"})
        progress_cb({"phase": "encode", "percent": 50.0, "message": "encode halfway"})
        return 0, str(tmp_path / "logs" / "sample.log")

    monkeypatch.setattr(queue.engine, "run_job", _fake_run_job)

    progress_events = []
    result = queue.run_next(progress_cb=progress_events.append)

    assert result[0] == 0
    overall_progress = [round(event["overall_percent"], 1) for event in progress_events]
    assert 12.5 in overall_progress
    assert 25.0 in overall_progress
    assert 62.5 in overall_progress
    assert overall_progress[-1] == 100.0


def test_transcode_queue_records_aborted_jobs(tmp_path, monkeypatch):
    from core.pipeline import TranscodeJob
    from transcode.engine import TRANSCODE_ABORT_RETURN_CODE
    from transcode.profiles import ProfileLoader
    from transcode.queue import TranscodeQueue

    loader = ProfileLoader(str(tmp_path / "profiles.json"))
    queue = TranscodeQueue(log_dir=str(tmp_path / "logs"))
    job = TranscodeJob(
        "input.mkv",
        "output.mkv",
        loader.get_profile(),
        metadata={"ffmpeg_source_mode": "safe_copy"},
    )
    queue.add_job(job)

    def _fake_run_job(job, dry_run=False, feedback_cb=None, progress_cb=None):
        return TRANSCODE_ABORT_RETURN_CODE, str(tmp_path / "logs" / "sample.log")

    monkeypatch.setattr(queue.engine, "run_job", _fake_run_job)

    feedback_messages = []
    progress_events = []
    result = queue.run_next(
        feedback_cb=feedback_messages.append,
        progress_cb=progress_events.append,
    )

    assert result[0] == TRANSCODE_ABORT_RETURN_CODE
    assert queue.aborted == [(job, str(tmp_path / "logs" / "sample.log"))]
    assert queue.failed == []
    assert any(message.startswith("ABORTED:") for message in feedback_messages)
    assert progress_events[-1]["phase"] == "aborted"


def test_transcode_queue_owns_private_abort_event_by_default(tmp_path):
    from transcode.queue import TranscodeQueue

    first = TranscodeQueue(log_dir=str(tmp_path / "logs_a"))
    second = TranscodeQueue(log_dir=str(tmp_path / "logs_b"))
    shared_event = threading.Event()
    third = TranscodeQueue(log_dir=str(tmp_path / "logs_c"), abort_event=shared_event)

    assert first.abort_event is first.engine.abort_event
    assert second.abort_event is second.engine.abort_event
    assert first.abort_event is not second.abort_event
    assert third.abort_event is shared_event
    assert third.engine.abort_event is shared_event


def test_transcode_queue_abort_cancels_pending_jobs(tmp_path, monkeypatch):
    from core.pipeline import TranscodeJob
    from transcode.engine import TRANSCODE_ABORT_RETURN_CODE
    from transcode.profiles import ProfileLoader
    from transcode.queue import TranscodeQueue

    loader = ProfileLoader(str(tmp_path / "profiles.json"))
    queue = TranscodeQueue(log_dir=str(tmp_path / "logs"))
    first_job = TranscodeJob("first.mkv", "first_out.mkv", loader.get_profile())
    second_job = TranscodeJob("second.mkv", "second_out.mkv", loader.get_profile())
    queue.add_job(first_job)
    queue.add_job(second_job)

    def _fake_run_job(job, dry_run=False, feedback_cb=None, progress_cb=None):
        queue.abort()
        return TRANSCODE_ABORT_RETURN_CODE, str(tmp_path / "logs" / "first.log")

    monkeypatch.setattr(queue.engine, "run_job", _fake_run_job)

    results = queue.run_all()

    assert len(results) == 1
    assert results[0][0] == TRANSCODE_ABORT_RETURN_CODE
    assert queue.jobs == []
    assert [job for job, _log_path in queue.aborted] == [first_job, second_job]
    assert queue.failed == []


def test_pipeline_controller_supports_handbrake_jobs_without_profile(tmp_path):
    from core.pipeline import PipelineController
    from transcode.profiles import ProfileLoader

    loader = ProfileLoader(str(tmp_path / "profiles.json"))
    controller = PipelineController(loader)
    output_path = tmp_path / "out" / "movie.mkv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("existing", encoding="utf-8")

    added = controller.add_job(
        "input.mkv",
        str(output_path),
        backend="handbrake",
        backend_options={"preset": "Fast 1080p30"},
    )

    assert added is True
    job = controller.get_queue()[0]
    assert job.profile is None
    assert job.backend == "handbrake"
    assert job.backend_options["preset"] == "Fast 1080p30"
    assert job.output_path.endswith("_1.mkv")


def test_choose_available_output_path_auto_increments(tmp_path):
    from core.pipeline import choose_available_output_path

    output_path = tmp_path / "movie.mkv"
    output_path.write_text("existing", encoding="utf-8")

    chosen = choose_available_output_path(str(output_path))

    assert chosen.endswith("_1.mkv")
