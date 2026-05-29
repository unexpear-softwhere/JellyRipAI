from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.ripper_engine import RipperEngine
from utils.makemkv_log import (
    MakeMKVMessageCoalescer,
    analyze_makemkv_messages,
)


def _engine_cfg(**overrides):
    cfg = {
        "makemkvcon_path": "makemkvcon",
        "ffprobe_path": "ffprobe",
        "opt_makemkv_global_args": "",
        "opt_makemkv_info_args": "",
        "opt_makemkv_rip_args": "",
        "opt_drive_index": 0,
        "opt_auto_retry": True,
        "opt_retry_attempts": 3,
        "opt_clean_mkv_before_retry": True,
    }
    cfg.update(overrides)
    return cfg


def test_analyze_makemkv_messages_detects_completed_with_errors():
    summary = analyze_makemkv_messages(
        [
            "Error 'Scsi error - HARDWARE ERROR:TIMEOUT ON LOGICAL UNIT' occurred while reading 'BD-RE BUFFALO Optical Drive BN14 MO1P93A2235' at offset '6735872'",
            "Error 'Scsi error - NOT READY:LOGICAL UNIT IS IN PROCESS OF BECOMING READY' occurred while reading '/VIDEO_TS/VTS_05_1.VOB' at offset '0'",
            "Error 'Scsi error - NOT READY:LOGICAL UNIT IS IN PROCESS OF BECOMING READY' occurred while reading '/VIDEO_TS/VTS_13_1.VOB' at offset '0'",
            "Profile parsing error: Invalid token '+sel:all(-sel:mvc)'",
            "Title #2/2 has length of 5 seconds which is less than minimum title length of 200 seconds and was therefore skipped",
            "Operation successfully completed",
        ]
    )

    assert summary.scsi_error_count == 3
    assert summary.hardware_timeout_count == 1
    assert summary.not_ready_count == 2
    assert summary.profile_error_count == 1
    assert summary.short_title_skip_count == 1
    assert summary.completed_with_errors is True
    assert summary.affected_paths["/VIDEO_TS/VTS_05_1.VOB"] == 1

    summary_lines = summary.build_summary_lines(phase="scan", exit_code=0)
    assert any("disc-read error" in line for line in summary_lines)
    assert any("completed with logged errors" in line for line in summary_lines)


def test_makemkv_message_coalescer_collapses_repeats():
    coalescer = MakeMKVMessageCoalescer()
    repeated = (
        "Error 'Scsi error - NOT READY:LOGICAL UNIT IS IN PROCESS OF "
        "BECOMING READY' occurred while reading '/VIDEO_TS/VTS_13_1.VOB' at offset '0'"
    )

    emitted = []
    emitted.extend(coalescer.feed(repeated))
    emitted.extend(coalescer.feed(repeated))
    emitted.extend(coalescer.feed(repeated))
    emitted.extend(coalescer.feed("Operation successfully completed"))
    emitted.extend(coalescer.flush())

    assert emitted == [
        repeated,
        "Previous MakeMKV message repeated 2 more time(s).",
        "Operation successfully completed",
    ]


def test_scan_disc_logs_actionable_summary_and_preserves_results(monkeypatch):
    engine = RipperEngine(_engine_cfg())
    logs: list[str] = []

    class _FakeStdout:
        def __init__(self):
            self._lines = iter(
                [
                    'MSG:0,0,0,"","Error \'Scsi error - NOT READY:LOGICAL UNIT IS IN PROCESS OF BECOMING READY\' occurred while reading \'/VIDEO_TS/VTS_05_1.VOB\' at offset \'0\'"\n',
                    'MSG:0,0,0,"","Profile parsing error: Invalid token \'+sel:all(-sel:mvc)\'"\n',
                    'MSG:0,0,0,"","Operation successfully completed"\n',
                    'CINFO:2,0,"Demo Disc"\n',
                    'TINFO:0,2,0,"Main Feature"\n',
                    'TINFO:0,9,0,"01:30:00"\n',
                    'TINFO:0,8,0,"12"\n',
                    'TINFO:0,11,0,"10.0 GB"\n',
                    "",
                ]
            )

        def readline(self):
            return next(self._lines, "")

    class _FakeProc:
        def __init__(self):
            self.stdout = _FakeStdout()
            self.returncode = 0

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            _ = timeout
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(
        "engine.ripper_engine.subprocess.Popen",
        lambda *args, **kwargs: _FakeProc(),
    )

    result = engine.scan_disc(logs.append, lambda _value: None)

    assert result
    assert engine.last_scan_issue_summary is not None
    assert engine.last_scan_issue_summary.completed_with_errors is True
    assert any("disc-read error" in line for line in logs)
    assert any("completed with logged errors" in line for line in logs)


def test_scan_disc_returns_none_on_nonzero_exit(monkeypatch):
    engine = RipperEngine(_engine_cfg())
    logs: list[str] = []

    class _FakeStdout:
        def __init__(self):
            self._lines = iter(
                [
                    'CINFO:2,0,"Broken Disc"\n',
                    'TINFO:0,2,0,"Partial Title"\n',
                    "",
                ]
            )

        def readline(self):
            return next(self._lines, "")

    class _FakeProc:
        def __init__(self):
            self.stdout = _FakeStdout()
            self.returncode = 1

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            _ = timeout
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(
        "engine.ripper_engine.subprocess.Popen",
        lambda *args, **kwargs: _FakeProc(),
    )

    result = engine.scan_disc(logs.append, lambda _value: None)

    assert result is None
    assert any("scan failed (exit code 1)" in line.lower() for line in logs)

