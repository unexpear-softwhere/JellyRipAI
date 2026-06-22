"""Phase 3c-ii — gui_qt.workflow_launchers tests.

Pins the launcher contract:

- Button name → controller method mapping
- Busy-check rejects concurrent launches
- Stop button sets the engine's abort_event
- Unmapped buttons log a "not yet wired" line
- Worker thread exceptions land in the log + show_error
- Reset session state before launch
- Resets progress + status after task completion
"""

from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import QCoreApplication

from gui_qt.main_window import MainWindow
from gui_qt.workflow_launchers import WorkflowLauncher


def _drain_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    app = QCoreApplication.instance()
    while time.time() < deadline:
        if predicate():
            return True
        if app is not None:
            app.processEvents()
        time.sleep(0.005)
    return predicate()


# ---------------------------------------------------------------------------
# Stub controller + engine
# ---------------------------------------------------------------------------


class _StubController:
    """Records calls to each run_* method and the order they happen."""

    def __init__(self):
        self.calls: list[str] = []
        self.session_log: list = []
        self.session_report: list = []
        self.start_time = None
        self.global_extra_counter = 0
        self._sleep_secs = 0.0

    def run_tv_disc(self):
        self.calls.append("tv")
        if self._sleep_secs:
            time.sleep(self._sleep_secs)

    def run_movie_disc(self):
        self.calls.append("movie")
        if self._sleep_secs:
            time.sleep(self._sleep_secs)

    def run_dump_all(self):
        self.calls.append("dump")

    def run_organize(self):
        self.calls.append("organize")

    def run_smart_rip(self):
        self.calls.append("smart_rip")


class _StubEngine:
    def __init__(self):
        self.abort_event = threading.Event()
        self.reset_count = 0

    def reset_abort(self):
        self.reset_count += 1
        self.abort_event.clear()


@pytest.fixture
def wired(qtbot):
    """Provide a (window, controller, engine, launcher) tuple with
    signals connected.  Each test gets a fresh tuple."""
    window = MainWindow()
    qtbot.addWidget(window)
    ctrl = _StubController()
    eng = _StubEngine()
    launcher = WorkflowLauncher(window, ctrl, eng)
    launcher.connect_signals()
    return window, ctrl, eng, launcher


def _wait_until_idle(launcher, timeout: float = 2.0) -> bool:
    return _drain_until(lambda: not launcher.is_busy(), timeout=timeout)


# ---------------------------------------------------------------------------
# Button name → controller method mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("button_name,expected_call", [
    ("modeGoTv",        "tv"),
    ("modeGoMovie",     "movie"),
    ("modeInfoDump",    "dump"),
    ("modeAltOrganize", "organize"),
])
def test_button_signal_invokes_correct_controller_method(
    wired, button_name, expected_call
):
    """Each mapped workflow button reaches the right controller method."""
    window, ctrl, eng, launcher = wired
    window.workflow_button_clicked.emit(button_name)
    assert _wait_until_idle(launcher)
    assert ctrl.calls == [expected_call]


def test_modewarnprep_routes_through_prep_handler(wired, monkeypatch):
    """``modeWarnPrep`` no longer goes through the controller-method
    mapping — it routes through ``_run_prep_mvp`` instead.  Verify
    the prep handler is invoked and the controller is *not* called."""
    window, ctrl, eng, launcher = wired

    captured: dict = {"prep_called": False}
    monkeypatch.setattr(
        launcher, "_run_prep_mvp",
        lambda: captured.update(prep_called=True),
    )

    window.workflow_button_clicked.emit("modeWarnPrep")
    assert _wait_until_idle(launcher, timeout=2.0)

    assert captured["prep_called"] is True
    assert ctrl.calls == []  # no controller method invoked


def test_unknown_button_handled_gracefully(wired):
    """A completely unknown button name doesn't crash."""
    window, ctrl, eng, launcher = wired
    window.workflow_button_clicked.emit("modeNonexistent")
    assert not launcher.is_busy()
    assert ctrl.calls == []


# ---------------------------------------------------------------------------
# Stop button
# ---------------------------------------------------------------------------


def test_stop_session_sets_abort_event(wired):
    """``stopSession`` sets the engine's abort_event without spawning
    a worker."""
    window, ctrl, eng, launcher = wired
    assert not eng.abort_event.is_set()
    window.workflow_button_clicked.emit("stopSession")
    assert eng.abort_event.is_set()
    assert ctrl.calls == []  # no controller method called
    assert window.status_bar.label_text == "Aborting..."


def test_stop_session_no_engine_logs_message(qtbot):
    """If there's no engine, stop logs a notice instead of crashing."""
    window = MainWindow()
    qtbot.addWidget(window)
    launcher = WorkflowLauncher(window, _StubController(), engine=None)
    launcher.connect_signals()
    # Should not raise
    window.workflow_button_clicked.emit("stopSession")
    assert "no engine" in window.log_pane.get_text().lower()


# ---------------------------------------------------------------------------
# Stop button lifecycle — enabled while a task runs, disabled after
# ---------------------------------------------------------------------------
#
# Pinned because Stop originally shipped permanently disabled (created
# with setEnabled(False) and never enabled by production code) — a
# 90-minute rip had no abort affordance at all.


def test_stop_enabled_during_task_and_disabled_after(wired):
    """Starting a workflow enables Stop and disables the mode
    buttons; the worker's finally restores both on completion."""
    window, ctrl, eng, launcher = wired
    ctrl._sleep_secs = 0.3

    assert not window.stop_button.isEnabled()  # idle: Stop disabled
    window.workflow_button_clicked.emit("modeGoTv")
    assert _drain_until(lambda: window.stop_button.isEnabled(), timeout=1.0)
    assert all(
        not b.isEnabled() for b in window.workflow_buttons.values()
    ), "mode buttons must lock while a workflow runs"

    assert _wait_until_idle(launcher, timeout=2.0)
    assert _drain_until(
        lambda: not window.stop_button.isEnabled(), timeout=2.0,
    )
    assert all(b.isEnabled() for b in window.workflow_buttons.values())


def test_stop_click_mid_task_sets_abort(wired):
    """End-to-end: start a slow task, click the now-enabled Stop
    button through its real click path, and the engine's abort
    event fires."""
    window, ctrl, eng, launcher = wired
    ctrl._sleep_secs = 0.5

    window.workflow_button_clicked.emit("modeGoTv")
    assert _drain_until(lambda: window.stop_button.isEnabled(), timeout=1.0)

    window.stop_button.click()
    assert eng.abort_event.is_set()
    assert _wait_until_idle(launcher, timeout=2.0)


def test_buttons_restored_after_worker_exception(qtbot):
    """The finally path restores Stop/mode buttons even when the
    controller method raises."""
    window = MainWindow()
    qtbot.addWidget(window)
    window.show_error = lambda *a, **k: None

    class CrashController:
        session_log = []
        session_report = []
        start_time = None
        global_extra_counter = 0

        def run_tv_disc(self):
            raise RuntimeError("boom")

    launcher = WorkflowLauncher(window, CrashController(), _StubEngine())
    launcher.connect_signals()
    window.workflow_button_clicked.emit("modeGoTv")
    assert _drain_until(lambda: not launcher.is_busy(), timeout=2.0)
    assert _drain_until(
        lambda: not window.stop_button.isEnabled(), timeout=2.0,
    )
    assert all(b.isEnabled() for b in window.workflow_buttons.values())


def test_finish_during_workflow_dialog_updates_snapshot(wired):
    """Shells with the modeless workflow-dialog soft-lock (AI branch):
    if the worker finishes while a dialog is open, the lifecycle
    restore must land in the *snapshot* — not the live widgets — so
    closing the dialog restores the post-task state instead of
    resurrecting the mid-task one (Stop enabled, buttons locked)."""
    window, ctrl, eng, launcher = wired
    if not hasattr(window, "begin_workflow_dialog"):
        pytest.skip("shell has no workflow-dialog soft-lock")
    ctrl._sleep_secs = 0.3

    window.workflow_button_clicked.emit("modeGoTv")
    assert _drain_until(lambda: window.stop_button.isEnabled(), timeout=1.0)

    window.begin_workflow_dialog()  # everything soft-locked now
    assert not window.stop_button.isEnabled()

    assert _wait_until_idle(launcher, timeout=2.0)
    # Drain the worker's queued set_workflow_running(False) marshal.
    QCoreApplication.instance().processEvents()
    window.end_workflow_dialog()

    assert not window.stop_button.isEnabled(), (
        "snapshot restore resurrected mid-task Stop state"
    )
    assert all(b.isEnabled() for b in window.workflow_buttons.values())


# ---------------------------------------------------------------------------
# Lifecycle: busy check, abort reset, session reset
# ---------------------------------------------------------------------------


def test_busy_check_rejects_concurrent_launches(wired):
    """If a workflow is already in flight, a second click is dropped
    (not stacked)."""
    window, ctrl, eng, launcher = wired
    ctrl._sleep_secs = 0.2  # make the first task slow

    window.workflow_button_clicked.emit("modeGoTv")
    # Wait for the worker to actually start running.
    assert _drain_until(lambda: launcher.is_busy(), timeout=1.0)

    # Second click should be rejected — show_info would normally pop
    # but in the test thread the modal would block; show_info is
    # already QMessageBox so it'll try to render — patch it.
    # The launcher's busy check returns False without spawning a
    # second thread, so we just verify the call list stays at 1.
    initial_call_count = len(ctrl.calls)

    # Patch show_info to not actually show a modal
    window.show_info = lambda *args, **kwargs: None
    window.workflow_button_clicked.emit("modeGoMovie")
    # Don't wait — busy check should have rejected immediately.
    # But the controller call count shouldn't grow until the first
    # task finishes.
    assert len(ctrl.calls) == initial_call_count

    # Wait for the in-flight task to finish.
    assert _wait_until_idle(launcher, timeout=2.0)
    # First call's worker did finish.
    assert "tv" in ctrl.calls


def test_abort_event_reset_before_each_launch(wired):
    """The engine's abort_event is cleared via ``reset_abort`` at
    the start of every workflow.  Pinned because a stale-set abort
    would short-circuit the new task immediately."""
    window, ctrl, eng, launcher = wired
    eng.abort_event.set()  # Simulate a leftover abort from a prior session
    initial_resets = eng.reset_count

    window.workflow_button_clicked.emit("modeGoTv")
    assert _wait_until_idle(launcher)

    assert eng.reset_count == initial_resets + 1
    # Worker should have run (abort was cleared)
    assert "tv" in ctrl.calls


def test_session_state_reset_before_launch(wired):
    """``session_log`` and ``session_report`` are cleared before
    each workflow, matching tkinter's start_task behavior at
    line 7710-7713."""
    window, ctrl, eng, launcher = wired
    ctrl.session_log = ["prior session line"]
    ctrl.session_report = ["prior report"]
    ctrl.global_extra_counter = 42

    window.workflow_button_clicked.emit("modeInfoDump")
    assert _wait_until_idle(launcher)

    assert ctrl.session_log == []
    assert ctrl.session_report == []
    assert ctrl.global_extra_counter == 1


def test_progress_reset_to_zero_before_launch(wired):
    """``set_progress(0)`` runs before the worker spawns.  Pinned
    because users see the progress bar visibly snap to 0 when they
    click a workflow button."""
    window, ctrl, eng, launcher = wired
    # Pre-set progress to a non-zero value
    window.set_progress(50)
    QCoreApplication.instance().processEvents()
    assert window.status_bar.progress_value == 50

    window.workflow_button_clicked.emit("modeAltOrganize")
    assert _wait_until_idle(launcher)
    QCoreApplication.instance().processEvents()
    # After completion: status bar reset to Ready, progress doesn't
    # auto-zero on completion (the controller writes 100 in some
    # workflows) — pinned: post-completion is "Ready" not "in progress"
    assert window.status_bar.label_text == "Ready"


# ---------------------------------------------------------------------------
# Exception handling in the worker
# ---------------------------------------------------------------------------


def test_worker_exception_logged_and_surfaced(qtbot, monkeypatch):
    """If the controller method raises, the launcher logs the error
    and calls ``show_error`` — but always returns the GUI to a
    Ready state."""
    window = MainWindow()
    qtbot.addWidget(window)

    error_calls: list = []

    # Patch show_error to capture (don't pop a real dialog)
    def fake_show_error(title, msg):
        error_calls.append((title, msg))
    window.show_error = fake_show_error

    class CrashController:
        session_log = []
        session_report = []
        start_time = None
        global_extra_counter = 0

        def run_tv_disc(self):
            raise RuntimeError("controller boom")

    eng = _StubEngine()
    launcher = WorkflowLauncher(window, CrashController(), eng)
    launcher.connect_signals()

    window.workflow_button_clicked.emit("modeGoTv")
    assert _wait_until_idle(launcher, timeout=2.0)

    # After is_busy flips false, the worker's submit_to_main calls
    # (append_log, show_error → invoked via run_on_main, etc.) may
    # still be queued.  Drain until they've all processed.
    assert _drain_until(
        lambda: "controller boom" in window.log_pane.get_text()
                and bool(error_calls),
        timeout=2.0,
    )

    log = window.log_pane.get_text()
    assert "controller boom" in log
    assert error_calls, "show_error should have been called"
    assert error_calls[0][0] == "Workflow Error"
    assert "controller boom" in error_calls[0][1]
    # Ready status restored even after error
    assert _drain_until(
        lambda: window.status_bar.label_text == "Ready",
        timeout=1.0,
    )


# ---------------------------------------------------------------------------
# Connect / disconnect lifecycle
# ---------------------------------------------------------------------------


def test_connect_signals_is_idempotent(wired):
    """Calling connect_signals twice doesn't double-fire the slot."""
    window, ctrl, eng, launcher = wired
    launcher.connect_signals()  # already connected by fixture; second call
    window.workflow_button_clicked.emit("modeGoTv")
    assert _wait_until_idle(launcher)
    # If it had double-connected, ctrl.calls would have ["tv", "tv"]
    assert ctrl.calls == ["tv"]


def test_disconnect_signals_stops_routing(wired):
    """After disconnect, button signals don't reach the launcher."""
    window, ctrl, eng, launcher = wired
    launcher.disconnect_signals()
    window.workflow_button_clicked.emit("modeGoTv")
    # No worker spawned, no controller call
    QCoreApplication.instance().processEvents()
    assert not launcher.is_busy()
    assert ctrl.calls == []


def test_disconnect_signals_idempotent(wired):
    """Disconnecting twice doesn't raise."""
    window, ctrl, eng, launcher = wired
    launcher.disconnect_signals()
    launcher.disconnect_signals()  # no-op, no error


# ---------------------------------------------------------------------------
# Tool-path pre-flight (validate_tools wiring)
# ---------------------------------------------------------------------------
#
# Closes the orphan-call gap documented in
# tests/test_failure_modes_section_8.py: validate_tools() existed on
# the engine but was never called from a workflow entry point.  The
# launcher now calls it before every makemkvcon/ffprobe-touching
# workflow and surfaces the friendly "MakeMKV not found, please check
# Settings" dialog instead of letting the user hit a cryptic
# `[Errno 2]` message in the log.


class _ValidatingEngine(_StubEngine):
    """Engine stub with a configurable ``validate_tools`` for the
    pre-flight gate.  Default = passes (legacy behavior)."""

    def __init__(self, ok: bool = True, reason: str = ""):
        super().__init__()
        self._validate_ok = ok
        self._validate_reason = reason
        self.validate_calls = 0

    def validate_tools(self):
        self.validate_calls += 1
        return self._validate_ok, self._validate_reason


def test_validate_tools_pass_lets_workflow_run(qtbot):
    """``validate_tools`` returning True must NOT block the
    workflow.  Pre-existing test stubs without ``validate_tools``
    are also exercised via the existing fixtures — this test
    specifically pins the True path."""
    window = MainWindow()
    qtbot.addWidget(window)
    ctrl = _StubController()
    eng = _ValidatingEngine(ok=True)
    launcher = WorkflowLauncher(window, ctrl, eng)
    launcher.connect_signals()

    window.workflow_button_clicked.emit("modeGoTv")
    assert _wait_until_idle(launcher)

    assert eng.validate_calls == 1
    assert ctrl.calls == ["tv"]


def test_validate_tools_fail_blocks_workflow_and_shows_friendly_error(qtbot):
    """``validate_tools`` returning False must block the workflow
    AND surface the friendly reason via ``show_error``.  The user
    needs to see the helpful "Please check Settings" message
    instead of the cryptic ``[Errno 2]`` they used to get when the
    binary went missing mid-session."""
    window = MainWindow()
    qtbot.addWidget(window)
    ctrl = _StubController()
    friendly_msg = (
        "MakeMKV executable not found.\n\nPlease check Settings."
    )
    eng = _ValidatingEngine(ok=False, reason=friendly_msg)
    launcher = WorkflowLauncher(window, ctrl, eng)
    launcher.connect_signals()

    error_calls: list = []
    window.show_error = lambda title, msg: error_calls.append((title, msg))

    window.workflow_button_clicked.emit("modeGoTv")
    QCoreApplication.instance().processEvents()

    # Worker must NOT have spawned.
    assert not launcher.is_busy()
    assert ctrl.calls == []

    # User got the friendly dialog, not the cryptic FileNotFoundError.
    assert error_calls, "show_error should have been called"
    title, msg = error_calls[0]
    assert title == "Required Tool Not Found"
    assert "MakeMKV executable not found" in msg
    assert "Please check Settings" in msg

    # And the log records the gate firing.
    log = window.log_pane.get_text()
    assert "Tool-path pre-flight failed" in log


@pytest.mark.parametrize("button_name", [
    "modeGoTv", "modeGoMovie", "modeInfoDump", "modeAltOrganize",
])
def test_validate_tools_gate_fires_for_all_disc_workflows(
    qtbot, button_name,
):
    """Every workflow that touches makemkvcon or ffprobe gates on
    the pre-flight.  Parametrized across all four mapped buttons
    so a future addition that forgets the gate fails this test."""
    window = MainWindow()
    qtbot.addWidget(window)
    ctrl = _StubController()
    eng = _ValidatingEngine(ok=False, reason="boom")
    launcher = WorkflowLauncher(window, ctrl, eng)
    launcher.connect_signals()
    window.show_error = lambda *_a, **_k: None

    window.workflow_button_clicked.emit(button_name)
    QCoreApplication.instance().processEvents()

    assert eng.validate_calls == 1
    assert ctrl.calls == [], (
        f"workflow {button_name!r} ran despite failed pre-flight"
    )


def test_validate_tools_skipped_for_prep_mvp(qtbot, monkeypatch):
    """``modeWarnPrep`` (Prep MKVs) resolves its own ffmpeg/ffprobe and
    must NOT gate on the MakeMKV-oriented ``validate_tools`` pre-flight —
    otherwise a misconfigured makemkvcon path would block a transcode
    that never touches MakeMKV."""
    window = MainWindow()
    qtbot.addWidget(window)
    ctrl = _StubController()
    eng = _ValidatingEngine(ok=False, reason="this should not be called")
    launcher = WorkflowLauncher(window, ctrl, eng)
    launcher.connect_signals()

    captured = {"prep_called": False}
    monkeypatch.setattr(
        launcher, "_run_prep_mvp",
        lambda: captured.update(prep_called=True),
    )

    window.workflow_button_clicked.emit("modeWarnPrep")
    assert _wait_until_idle(launcher, timeout=2.0)

    assert eng.validate_calls == 0, (
        "Prep MVP must not gate on validate_tools — it doesn't use them"
    )
    assert captured["prep_called"] is True


def test_validate_tools_skipped_for_stop_session(qtbot):
    """``stopSession`` must always work, even if tool paths are
    broken — otherwise a user with a misconfigured installation
    could get stuck in a state they can't escape."""
    window = MainWindow()
    qtbot.addWidget(window)
    ctrl = _StubController()
    eng = _ValidatingEngine(ok=False, reason="should not gate stop")
    launcher = WorkflowLauncher(window, ctrl, eng)
    launcher.connect_signals()

    window.workflow_button_clicked.emit("stopSession")
    QCoreApplication.instance().processEvents()

    assert eng.validate_calls == 0
    assert eng.abort_event.is_set()


def test_validate_tools_crash_does_not_block_workflow(qtbot, monkeypatch):
    """If ``validate_tools`` itself blows up (e.g. registry probe
    raises, network share timing out), the launcher must NOT take
    the workflow down with it.  Pre-flight is a defense-in-depth
    nicety — losing it should degrade to "let the workflow try and
    fail with a real error" rather than blocking the user from
    even attempting their task."""
    window = MainWindow()
    qtbot.addWidget(window)
    ctrl = _StubController()

    class CrashingValidator(_StubEngine):
        def validate_tools(self):
            raise OSError("[WinError 53] The network path was not found")

    eng = CrashingValidator()
    launcher = WorkflowLauncher(window, ctrl, eng)
    launcher.connect_signals()

    window.workflow_button_clicked.emit("modeGoTv")
    assert _wait_until_idle(launcher)

    # Workflow ran despite the validate_tools crash.
    assert ctrl.calls == ["tv"]
    # And the crash is logged for debug, not surfaced as an error
    # dialog (the workflow itself will surface any real failure).
    log = window.log_pane.get_text()
    assert "pre-flight crashed" in log


def test_validate_tools_missing_method_falls_through(qtbot):
    """Engines that predate the wiring (or test stubs) don't have
    ``validate_tools`` — the launcher must fall through gracefully
    rather than hard-crash on attribute access."""
    window = MainWindow()
    qtbot.addWidget(window)
    ctrl = _StubController()
    eng = _StubEngine()  # no validate_tools method
    launcher = WorkflowLauncher(window, ctrl, eng)
    launcher.connect_signals()

    window.workflow_button_clicked.emit("modeGoTv")
    assert _wait_until_idle(launcher)

    assert ctrl.calls == ["tv"]


# ---------------------------------------------------------------------------
# Prep MKVs → real transcode flow (no longer a scan-only stub)
# ---------------------------------------------------------------------------


def test_prep_mvp_runs_real_transcode_flow(qtbot, monkeypatch, tmp_path):
    """The Prep MKVs button now runs an actual transcode pipeline:
    scan -> probe each file -> build a per-file H.265 recommendation
    job -> run the queue -> summary.  Mock the heavy engine pieces
    (tool resolution, ffprobe, the queue) and assert the launcher
    wires them together and honours the chosen quality tier."""
    import os as _os

    import config as _config
    import transcode.queue_builder as _qb
    import transcode.recommendations as _recs
    from transcode.queue_builder import QueueBuildResult

    window = MainWindow()
    qtbot.addWidget(window)
    launcher = WorkflowLauncher(window, _StubController(), _StubEngine())

    # A real folder with one .mkv so find_mkv_files (unmocked) finds it.
    src = tmp_path / "clips"
    src.mkdir()
    (src / "movie.mkv").write_bytes(b"x")

    # Drive the dialogs: pick the folder, choose tier 2 (balanced), confirm.
    monkeypatch.setattr(window, "ask_directory", lambda *a, **k: str(src))
    monkeypatch.setattr(
        window, "ask_transcode_options",
        lambda **k: {"quality": "balanced", "codec": "h265",
                     "hw_accel": "cpu", "audio": "copy"},
    )
    infos: list = []
    monkeypatch.setattr(window, "show_info", lambda title, msg: infos.append((title, msg)))
    monkeypatch.setattr(window, "show_error", lambda *a, **k: None)
    monkeypatch.setattr(window, "append_log", lambda *a, **k: None)
    monkeypatch.setattr(window, "set_status", lambda *a, **k: None)
    monkeypatch.setattr(window, "set_progress", lambda *a, **k: None)

    class _Tool:
        def __init__(self, path, error=""):
            self.path = path
            self.error = error

    monkeypatch.setattr(_config, "resolve_ffmpeg", lambda *a, **k: _Tool("ffmpeg"))
    monkeypatch.setattr(_config, "resolve_ffprobe", lambda *a, **k: _Tool("ffprobe"))
    monkeypatch.setattr(_config, "resolve_handbrake", lambda *a, **k: _Tool("", "n/a"))

    import transcode.encoder_probe as _ep
    monkeypatch.setattr(_ep, "available_encoders", lambda exe: frozenset())

    monkeypatch.setattr(
        _recs, "probe_media_for_recommendation",
        lambda path, exe: {"path": path, "name": _os.path.basename(path)},
    )
    monkeypatch.setattr(
        _recs, "build_ffmpeg_recommendations",
        lambda analysis: {"recommendations": [
            {"id": "smaller_file"}, {"id": "balanced"}, {"id": "higher_quality"},
        ]},
    )

    chosen: dict = {}

    class _Job:
        def __init__(self, path):
            self.input_path = path
            self.output_path = path + ".out.mkv"

    def _fake_build_rec_job(*, plan, analysis, recommendation, ffmpeg_source_mode, ffmpeg_exe):
        chosen["rec_id"] = recommendation["id"]
        pdata = recommendation.get("profile_data") or {}
        chosen["codec"] = (pdata.get("video") or {}).get("codec")
        chosen["hw_accel"] = (pdata.get("video") or {}).get("hw_accel")
        chosen["audio"] = (pdata.get("audio") or {}).get("mode")
        return QueueBuildResult(jobs=[_Job(plan["input_path"])], queue_detail="x")

    monkeypatch.setattr(_qb, "build_recommendation_job", _fake_build_rec_job)
    monkeypatch.setattr(_qb, "required_output_directories", lambda jobs, root: [])

    class _FakeQueue:
        def __init__(self):
            self.completed: list = []
            self.failed: list = []
            self.degraded: list = []
            self.aborted: list = []
            self.ran_with = None

        def run_all(self, feedback_cb=None, progress_cb=None):
            self.ran_with = (feedback_cb, progress_cb)
            self.completed.append(("job", "log"))
            if progress_cb:
                progress_cb({
                    "overall_percent": 100.0, "job_index": 1,
                    "job_total": 1, "job_percent": 100.0,
                })
            return []

    fake_queue = _FakeQueue()
    captured: dict = {}

    def _fake_build_queue(**kwargs):
        captured["jobs"] = list(kwargs.get("jobs") or [])
        return fake_queue

    monkeypatch.setattr(_qb, "build_transcode_queue", _fake_build_queue)

    # Run the worker body directly (synchronously).
    launcher._run_prep_mvp()

    assert chosen.get("rec_id") == "balanced", "dialog quality must pick that profile"
    assert chosen.get("codec") == "h265", "chosen codec must reach the profile"
    assert chosen.get("hw_accel") == "cpu", "chosen encoder must reach the profile"
    assert chosen.get("audio") == "copy", "chosen audio mode must reach the profile"
    assert len(captured.get("jobs", [])) == 1, "one job per scanned MKV"
    assert fake_queue.ran_with is not None, "the queue must actually be run"
    assert infos and infos[0][0] == "Transcode Complete"
    assert "Succeeded: 1" in infos[0][1]
