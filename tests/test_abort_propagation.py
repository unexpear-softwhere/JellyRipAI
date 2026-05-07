"""Abort-propagation tests (engine contract + workflow respect).

Closes the cross-cutting criterion *"Abort propagation"* in
[docs/workflow-stabilization-criteria.md](../docs/workflow-stabilization-criteria.md):

    Aborting mid-flow (``engine.abort_event.set()`` via the Stop
    Session button) leaves the session in a recoverable state —
    partial outputs cleaned up, session metadata accurately reflects
    "aborted" status, no zombie temp folders.

Two layers:

1. **Engine-level** — pin ``RipperEngine.abort()`` semantics
   (idempotent, terminates running subprocess, kill-after-timeout
   fallback, defensive on missing/exited process), ``reset_abort()``
   clearing the flag, ``abort_flag`` property, and
   ``cleanup_partial_files`` respecting ``opt_clean_partials_startup``.
2. **Workflow-level** — pin that each workflow's abort checkpoints
   actually short-circuit the flow when the flag is set.  Includes
   an explicit gap-pin for ``run_organize``: it has only ONE abort
   check (after-the-fact, post-move), so once the user has provided
   folder/title/year inputs the workflow does NOT honor an abort
   during ``analyze_files`` / ``_select_and_move``.

Behavior-first.  No real subprocess starts.  Survives the planned
PySide6 migration per decision #5 in
``docs/pyside6-migration-plan.md``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from engine.ripper_engine import RipperEngine
from tests.test_behavior_guards import _controller_with_engine


# --------------------------------------------------------------------------
# Engine-level: abort() / reset_abort() / abort_flag / cleanup_partial_files
# --------------------------------------------------------------------------


def _engine(**cfg_overrides):
    cfg = {
        "makemkvcon_path": "makemkvcon",
        "ffprobe_path": "ffprobe",
        "opt_makemkv_global_args": "",
        "opt_makemkv_rip_args": "",
        "opt_drive_index": 0,
    }
    cfg.update(cfg_overrides)
    return RipperEngine(cfg)


class _FakeProc:
    """Minimal stand-in for ``subprocess.Popen``-like current_process.

    ``poll_returns`` is a sequence of values returned by successive
    ``poll()`` calls; ``wait_raises`` toggles whether wait() raises
    ``subprocess.TimeoutExpired`` (to exercise the kill fallback)."""

    def __init__(self, poll_returns=(None,), wait_raises=False):
        self._poll_returns = iter(list(poll_returns) + [0] * 10)
        self._wait_raises = wait_raises
        self.terminated = False
        self.killed = False
        self.waited = False

    def poll(self):
        return next(self._poll_returns)

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.waited = True
        if self._wait_raises:
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout or 0)

    def kill(self):
        self.killed = True


def test_abort_sets_event_when_no_current_process():
    """``abort()`` with ``current_process=None`` sets the event without
    raising.  Defensive: aborting before any rip starts is safe."""
    engine = _engine()
    engine.current_process = None

    assert not engine.abort_event.is_set()
    engine.abort()
    assert engine.abort_event.is_set()


def test_abort_is_idempotent():
    """Calling ``abort()`` twice on an already-aborted engine is a
    no-op — does not raise, does not double-terminate any process."""
    engine = _engine()
    fake = _FakeProc(poll_returns=(None,))
    engine.current_process = fake

    engine.abort()
    assert engine.abort_event.is_set()
    assert fake.terminated is True

    fake.terminated = False  # reset spy
    engine.current_process = fake  # still set
    engine.abort()  # second call

    assert fake.terminated is False, (
        "second abort() should not re-terminate the process"
    )


def test_abort_terminates_running_subprocess():
    """When ``current_process`` is running (``poll() is None``),
    ``abort()`` calls ``terminate()`` and waits."""
    engine = _engine()
    fake = _FakeProc(poll_returns=(None,))
    engine.current_process = fake

    engine.abort()

    assert fake.terminated is True
    assert fake.waited is True
    assert fake.killed is False


def test_abort_kills_after_terminate_timeout():
    """If ``terminate()`` doesn't end the process in 5s, the engine
    falls back to ``kill()``.  Pins the kill-after-timeout escalation
    in ``ripper_engine.py:494-505``."""
    engine = _engine()
    fake = _FakeProc(poll_returns=(None,), wait_raises=True)
    engine.current_process = fake

    engine.abort()

    assert fake.terminated is True, "terminate must be tried first"
    assert fake.killed is True, (
        "kill must be invoked when terminate's wait times out"
    )


def test_abort_skips_terminate_when_process_already_exited():
    """When ``poll() != None`` the process has already exited;
    ``abort()`` skips terminate.  Pins the ``if proc.poll() is None``
    guard in ``ripper_engine.py:496``."""
    engine = _engine()
    fake = _FakeProc(poll_returns=(0,))  # exit code already returned
    engine.current_process = fake

    engine.abort()

    assert engine.abort_event.is_set(), "flag still set"
    assert fake.terminated is False, (
        "must not terminate a process that has already exited"
    )


def test_abort_swallows_exceptions_from_terminate():
    """``abort()`` catches all exceptions from terminate/wait/kill.
    Pins the defensive ``except Exception: pass`` outer block in
    ``ripper_engine.py:495-506`` — abort must always set the flag
    even if subprocess cleanup blows up."""
    engine = _engine()

    class _AngryProc:
        def poll(self): return None
        def terminate(self): raise OSError("simulated error")
        def wait(self, timeout=None): raise RuntimeError("never reached")

    engine.current_process = _AngryProc()
    engine.abort()  # must not raise

    assert engine.abort_event.is_set(), (
        "flag must still be set even when subprocess cleanup raises"
    )


def test_reset_abort_clears_the_event():
    """``reset_abort()`` clears the flag so the next run can proceed.
    Pins the reset semantics relied on at the start of every workflow."""
    engine = _engine()
    engine.abort_event.set()
    assert engine.abort_event.is_set()

    engine.reset_abort()

    assert not engine.abort_event.is_set()


def test_abort_flag_property_reflects_event_state():
    """The ``abort_flag`` property is a thin alias for
    ``abort_event.is_set()`` (``ripper_engine.py:482-484``).  Both
    must always agree."""
    engine = _engine()
    assert engine.abort_flag is False

    engine.abort_event.set()
    assert engine.abort_flag is True

    engine.abort_event.clear()
    assert engine.abort_flag is False


def test_cleanup_partial_files_removes_partials_only(tmp_path):
    """``cleanup_partial_files`` removes ``.partial`` files but NOT
    completed ``.mkv`` files.  Pins the no-data-loss invariant: the
    cleanup mechanism must never touch good output."""
    engine = _engine(opt_clean_partials_startup=True)

    root = tmp_path / "rip_root"
    root.mkdir()
    completed = root / "title_t00.mkv"
    partial1 = root / "title_t00.mkv.partial"
    nested_partial = root / "subfolder" / "title_t01.mkv.partial"
    nested_partial.parent.mkdir()
    completed.write_text("good content")
    partial1.write_text("partial 1")
    nested_partial.write_text("partial 2")

    engine.cleanup_partial_files(str(root), lambda _msg: None)

    assert completed.exists(), (
        "completed .mkv MUST NOT be removed by cleanup_partial_files"
    )
    assert not partial1.exists()
    assert not nested_partial.exists(), (
        "cleanup_partial_files must walk nested folders recursively"
    )


def test_cleanup_partial_files_respects_opt_clean_partials_startup_off(
    tmp_path,
):
    """When ``opt_clean_partials_startup=False``, the cleanup is a
    no-op even if ``.partial`` files exist.  Pins user opt-out
    respect — some users want to inspect partials manually."""
    engine = _engine(opt_clean_partials_startup=False)

    root = tmp_path / "rip_root"
    root.mkdir()
    partial = root / "title_t00.mkv.partial"
    partial.write_text("would normally be removed")

    engine.cleanup_partial_files(str(root), lambda _msg: None)

    assert partial.exists(), (
        "opt_clean_partials_startup=False must suppress the cleanup"
    )


def test_cleanup_partial_files_defensive_on_missing_directory(tmp_path):
    """``cleanup_partial_files`` on a non-existent path is a no-op.
    Pins the ``if not os.path.isdir(root_path): return`` guard at
    ``ripper_engine.py:394-395`` — defensive against drives that
    have disappeared."""
    engine = _engine(opt_clean_partials_startup=True)
    nonexistent = tmp_path / "definitely_gone"

    # Must not raise
    engine.cleanup_partial_files(str(nonexistent), lambda _msg: None)


# --------------------------------------------------------------------------
# Workflow-level: each workflow respects abort_event (or doesn't)
# --------------------------------------------------------------------------


def test_run_smart_rip_aborted_before_scan_returns_without_running_job(
    tmp_path, monkeypatch,
):
    """``run_smart_rip`` polls ``abort_event`` before ``scan_with_retry``.
    If aborted there, ``run_job`` is never called.  Pins the contract
    that ``Stop Session`` between confirm-disc and scan-start is
    honored."""
    controller, engine = _controller_with_engine()

    run_job_calls: list = []

    # Set abort during cleanup_partial_files (first abort-checked step
    # AFTER the SM reset, so the flag-already-set guard fires).
    def _cleanup_then_abort(*_a, **_k):
        engine.abort_event.set()

    monkeypatch.setattr(
        engine, "cleanup_partial_files", _cleanup_then_abort
    )
    monkeypatch.setattr(
        controller, "_prompt_run_path_overrides", lambda _f: {}
    )
    monkeypatch.setattr(
        engine, "run_job",
        lambda _job: run_job_calls.append(_job)
        or SimpleNamespace(success=True, errors=[]),
    )

    engine.cfg["temp_folder"] = str(tmp_path / "temp")
    engine.cfg["movies_folder"] = str(tmp_path / "movies")
    engine.cfg["tv_folder"] = str(tmp_path / "tv")
    for k in ("temp_folder", "movies_folder", "tv_folder"):
        os.makedirs(engine.cfg[k], exist_ok=True)

    controller.run_smart_rip()

    assert run_job_calls == [], (
        "run_job must NOT be called when abort_event is set before scan"
    )


def test_run_organize_does_not_check_abort_during_analyze(
    tmp_path, monkeypatch,
):
    """**Audit finding (2026-05-03)**: ``run_organize`` has only ONE
    abort_event check, and it's after-the-fact (only used to decide
    whether to log "Move stopped before completion").  The workflow
    does NOT poll abort during ``analyze_files`` or ``_select_and_move``.

    This test pins the **current** gap: setting abort_event right
    before ``analyze_files`` does NOT short-circuit the workflow —
    ``_select_and_move`` still fires.  If the code is updated to
    add an abort check between analyze and move, this test will
    fail loudly and the user can update the assertion to reflect
    the new contract."""
    controller, engine = _controller_with_engine()

    source = tmp_path / "src"
    source.mkdir()
    (source / "a.mkv").write_text("data")

    engine.cfg["temp_folder"] = str(tmp_path / "temp")
    engine.cfg["movies_folder"] = str(tmp_path / "movies")
    os.makedirs(engine.cfg["temp_folder"])
    os.makedirs(engine.cfg["movies_folder"])

    inputs = iter([str(source), "m", "Chosen Movie", "", "2024"])
    controller.gui.ask_input = (
        lambda _l, _p, default_value="": next(inputs)
    )
    controller.gui.ask_yesno = lambda _p: False
    controller.gui.show_info = lambda *_a, **_k: None

    monkeypatch.setattr(
        controller, "_prompt_run_path_overrides", lambda _f: {}
    )

    # Set abort_event right before analyze_files runs.
    select_move_calls: list = []
    def _analyze_then_abort(*_a, **_k):
        engine.abort_event.set()  # would short-circuit a respectful flow
        return [(str(source / "a.mkv"), 60.0, 100.0)]

    monkeypatch.setattr(engine, "analyze_files", _analyze_then_abort)
    monkeypatch.setattr(
        controller, "_select_and_move",
        lambda *_a, **_k: select_move_calls.append(1) or True,
    )
    monkeypatch.setattr(controller, "write_session_summary", lambda: None)
    monkeypatch.setattr(controller, "flush_log", lambda: None)

    controller.run_organize()

    assert select_move_calls == [1], (
        "AUDIT FINDING: run_organize does NOT poll abort_event between "
        "analyze_files and _select_and_move; the move proceeds even "
        "after abort is set.  If this assertion ever fails, the "
        "workflow has been updated to honor abort here — which is "
        "good!  Update the assertion to reflect the new contract."
    )


def test_run_dump_all_aborted_before_rip_returns_without_running_job(
    tmp_path, monkeypatch,
):
    """``run_dump_all`` polls ``abort_event`` before kicking off the
    rip.  If aborted there, ``run_job`` is never called.  Pins the
    abort respect for the dump-all flow (parallel to smart_rip)."""
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    temp_root.mkdir()

    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False

    run_job_calls: list = []

    # The single-disc dump path doesn't call cleanup_partial_files,
    # so we set abort inside ask_dump_setup — the immediately-following
    # check at controller.py:1346 (`if abort_event.is_set() or
    # dump_setup is None`) trips and the workflow returns BEFORE
    # any rip starts.
    from shared.session_setup_types import DumpSessionSetup
    def _setup_and_abort(**_kw):
        engine.abort_event.set()
        return DumpSessionSetup(
            multi_disc=False,
            disc_name="Test",
            disc_count=1,
            custom_disc_names="",
            batch_title="",
        )
    controller.gui.ask_dump_setup = _setup_and_abort

    monkeypatch.setattr(
        controller, "_prompt_run_path_overrides", lambda _f: {}
    )
    controller.gui.ask_yesno = lambda _p: False
    controller.gui.show_info = lambda *_a, **_k: None

    monkeypatch.setattr(
        engine, "run_job",
        lambda _job: run_job_calls.append(_job)
        or SimpleNamespace(success=True, errors=[]),
    )

    controller.run_dump_all()

    assert run_job_calls == [], (
        "run_dump_all must NOT call run_job when abort fires before "
        "the rip starts"
    )


def test_run_disc_inner_aborted_before_disc_prompt_returns_early(
    tmp_path, monkeypatch,
):
    """``_run_disc_inner`` polls ``abort_event`` after the SM reset.
    Aborting there returns early without firing the disc-info
    prompt or scan.  Pins the abort respect for the manual TV/Movie
    flows."""
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    for p in (temp_root, movies_root):
        p.mkdir(parents=True)

    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)
    engine.cfg["opt_show_temp_manager"] = False

    monkeypatch.setattr(
        controller, "_prompt_run_path_overrides", lambda _f: {}
    )

    def _cleanup_then_abort(*_a, **_k):
        engine.abort_event.set()
    monkeypatch.setattr(
        engine, "cleanup_partial_files", _cleanup_then_abort
    )

    scan_calls: list = []
    monkeypatch.setattr(
        controller, "scan_with_retry",
        lambda: scan_calls.append(1) or [],
    )

    controller.run_movie_disc()

    assert scan_calls == [], (
        "_run_disc_inner must NOT scan when abort fires before the "
        "scan step (post-SM-reset cleanup_partial_files)"
    )


def test_engine_abort_called_before_run_job_terminates_subprocess():
    """End-to-end engine contract: setting abort while a fake
    ``current_process`` is running terminates it.  Pins the
    bidirectional flow — workflow-level abort propagates down to
    subprocess termination, which is what ``Stop Session`` needs to
    actually cancel a long-running rip."""
    engine = _engine()
    fake = _FakeProc(poll_returns=(None,))
    engine.current_process = fake

    # Simulate the workflow detecting abort and calling engine.abort()
    # — exactly what controller.py does via gui's Stop Session button.
    engine.abort()

    assert engine.abort_event.is_set()
    assert fake.terminated is True, (
        "abort() must terminate the running subprocess so the rip "
        "actually halts (not just flag-set without subprocess kill)"
    )
