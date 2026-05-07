"""Cross-workflow state-machine behavior audit.

**Updated 2026-05-03 to reflect Option B-lite implementation.**  The
earlier version of this file pinned an *audit finding*: that
``run_dump_all`` and ``run_organize`` did not touch the SM at all,
leaking a prior FAILED state into the next run's session-summary
log line via ``controller/session.py:write_session_summary``.

After the user direction *"close the SM-trajectory checkbox — Option
B-lite"*, the gap is closed.  All five workflow entry points now
follow consistent SM contracts:

==========================  ============  ===================  =====================
Workflow                    Resets SM?    Walks intermediates?  Terminal SM state?
==========================  ============  ===================  =====================
``run_smart_rip``           Yes           Yes (full)           COMPLETED or FAILED
``_run_disc_inner`` (manual Yes           No                   COMPLETED (forced) or
   TV / Movie disc)                                            FAILED (via inherited
                                                                _state_fail calls)
``run_dump_all`` (single)   **Yes (new)** No                   COMPLETED or FAILED
                                                                (per disc / per phase)
``_run_dump_all_multi``     **Yes (new)** No                   COMPLETED or FAILED
                                                                (loop break = fail)
``run_organize``            **Yes (new)** No                   COMPLETED or FAILED
                                                                (move_ok branches)
==========================  ============  ===================  =====================

The "no intermediates" rows reflect a deliberate design choice: those
flows don't have SCANNED→RIPPED→…→MOVED phases the way smart rip does,
so reset+complete-or-fail is the right shape.  No leaked-state bug
remains because every workflow now resets at entry.

Behavior-first.  No GUI/Tk touches, no real subprocess calls.
Survives the planned PySide6 migration per decision #5 in
``docs/pyside6-migration-plan.md``.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from utils.state_machine import SessionState

# Reuse the helpers/fixtures established in test_behavior_guards.py — no
# need to duplicate _controller_with_engine, DummyGUI, _engine_cfg.
from tests.test_behavior_guards import (
    DummyGUI,
    _controller_with_engine,
)
from shared.session_setup_types import DumpSessionSetup


# --------------------------------------------------------------------------
# Spy helpers
# --------------------------------------------------------------------------


class _SMSpy:
    """Wraps the controller's SM-touching methods to count invocations.

    Use ``install()`` after the controller is built so the spy sees every
    call from the workflow under test.  All counters start at 0.
    """

    def __init__(self, controller):
        self.controller = controller
        self.transition_count = 0
        self.fail_count = 0
        self.reset_count = 0
        self.complete_count = 0
        self.transitions: list[SessionState] = []
        self.fail_reasons: list[str] = []
        self._installed = False

    def install(self):
        if self._installed:
            return
        self._installed = True

        c = self.controller
        original_transition = c._state_transition
        original_fail = c._state_fail
        original_reset = c._reset_state_machine
        original_complete = c.sm.complete

        def _spy_transition(new_state):
            self.transition_count += 1
            self.transitions.append(new_state)
            return original_transition(new_state)

        def _spy_fail(reason):
            self.fail_count += 1
            self.fail_reasons.append(str(reason))
            return original_fail(reason)

        def _patch_complete_on_current_sm():
            """``_reset_state_machine`` rebuilds ``self.sm`` from
            scratch, so any earlier patch on ``c.sm.complete`` is
            lost when reset fires.  Re-patch the new instance every
            time we need to track complete calls."""
            inner_complete = c.sm.complete

            def _spy_complete():
                self.complete_count += 1
                return inner_complete()

            c.sm.complete = _spy_complete

        def _spy_reset():
            self.reset_count += 1
            result = original_reset()
            # The reset just replaced c.sm with a fresh SM; re-spy.
            _patch_complete_on_current_sm()
            return result

        c._state_transition = _spy_transition
        c._state_fail = _spy_fail
        c._reset_state_machine = _spy_reset
        _patch_complete_on_current_sm()

    @property
    def total_calls(self) -> int:
        return (
            self.transition_count
            + self.fail_count
            + self.reset_count
            + self.complete_count
        )


def _engine_cfg(**overrides):
    """Mirror the helper in test_behavior_guards.py without importing it
    directly — the import there is a private fixture."""
    cfg = {
        "makemkvcon_path": "makemkvcon",
        "ffprobe_path": "ffprobe",
        "opt_makemkv_global_args": "",
        "opt_makemkv_rip_args": "",
        "opt_drive_index": 0,
        "opt_auto_retry": True,
        "opt_retry_attempts": 3,
        "opt_clean_mkv_before_retry": True,
    }
    cfg.update(overrides)
    return cfg


# --------------------------------------------------------------------------
# run_organize — resets at entry, complete/fail at terminal points
# --------------------------------------------------------------------------


def test_run_organize_resets_sm_on_entry_even_when_cancelled(
    tmp_path, monkeypatch
):
    """Cancel at path-overrides → workflow reset fires at the very
    top, leaving SM at INIT after the cancel return.  Pins the
    leak-fix: a prior FAILED state from another workflow MUST be
    cleared at the start of run_organize, even if the workflow is
    immediately cancelled."""
    controller, _engine = _controller_with_engine()

    # Pre-load FAILED so we can prove the reset fires.
    controller.sm.fail("simulated_prior_failure")
    assert controller.sm.state is SessionState.FAILED

    spy = _SMSpy(controller)
    spy.install()

    monkeypatch.setattr(
        controller, "_prompt_run_path_overrides", lambda _f: None
    )
    # Set up minimum scaffolding to reach _prompt_run_path_overrides
    source = tmp_path / "src"
    source.mkdir()
    (source / "a.mkv").write_text("data")
    inputs = iter([str(source), "m"])
    controller.gui.ask_input = (
        lambda _l, _p, default_value="": next(inputs)
    )
    controller.gui.ask_yesno = lambda _p: False

    controller.run_organize()

    assert spy.reset_count == 1, (
        "run_organize must reset the SM exactly once at entry, "
        "even when the workflow is cancelled before doing real work"
    )
    assert spy.complete_count == 0
    assert spy.fail_count == 0
    assert controller.sm.state is SessionState.INIT, (
        "after reset (and no further transitions), SM is at INIT — "
        "the leaked FAILED state is GONE"
    )


def test_run_organize_happy_path_resets_and_completes(
    tmp_path, monkeypatch
):
    """Happy organize → reset at entry, sm.complete() on
    move_ok=True path.  Final state is COMPLETED.  Closes the
    cross-cutting criterion *"state machine reaches a terminal
    state"* for run_organize."""
    controller, engine = _controller_with_engine()

    # Pre-load FAILED to prove reset clears it.
    controller.sm.fail("simulated_prior_failure")
    assert controller.sm.state is SessionState.FAILED

    spy = _SMSpy(controller)
    spy.install()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    source = temp_root / "src"
    source.mkdir(parents=True)
    movies_root.mkdir()
    mkv = source / "a.mkv"
    mkv.write_text("data")

    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)
    engine.cfg["opt_auto_delete_temp"] = False
    engine.cfg["opt_auto_delete_session_metadata"] = False

    inputs = iter([str(source), "m", "Chosen Movie", "", "2024"])
    controller.gui.ask_input = (
        lambda _l, _p, default_value="": next(inputs)
    )
    controller.gui.ask_yesno = lambda _p: False
    controller.gui.show_info = lambda *_a, **_k: None

    monkeypatch.setattr(
        controller, "_prompt_run_path_overrides", lambda _f: {}
    )
    monkeypatch.setattr(
        engine, "analyze_files",
        lambda *_a, **_k: [(str(mkv), 60.0, 100.0)],
    )
    monkeypatch.setattr(
        controller, "_select_and_move", lambda *_a, **_k: True,
    )
    monkeypatch.setattr(controller, "write_session_summary", lambda: None)
    monkeypatch.setattr(controller, "flush_log", lambda: None)

    controller.run_organize()

    assert spy.reset_count == 1, "reset fires exactly once"
    assert spy.complete_count == 1, "sm.complete() fires on success"
    assert spy.fail_count == 0
    assert spy.transition_count == 0, (
        "organize doesn't walk intermediate states (no SCANNED→RIPPED) — "
        "it has no rip phase; reset+complete is the contract"
    )
    assert controller.sm.state is SessionState.COMPLETED, (
        "successful organize ends at COMPLETED — closes the criterion"
    )


def test_run_organize_move_failure_lands_on_failed(
    tmp_path, monkeypatch
):
    """When ``_select_and_move`` returns False, ``_state_fail`` fires
    and the SM ends at FAILED.  Pins the failure-path SM contract."""
    controller, engine = _controller_with_engine()
    spy = _SMSpy(controller)
    spy.install()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    source = temp_root / "src"
    source.mkdir(parents=True)
    movies_root.mkdir()
    mkv = source / "a.mkv"
    mkv.write_text("data")

    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)
    engine.cfg["opt_auto_delete_temp"] = False
    engine.cfg["opt_auto_delete_session_metadata"] = False

    inputs = iter([str(source), "m", "Chosen Movie", "", "2024"])
    controller.gui.ask_input = (
        lambda _l, _p, default_value="": next(inputs)
    )
    controller.gui.ask_yesno = lambda _p: False
    controller.gui.show_info = lambda *_a, **_k: None

    monkeypatch.setattr(
        controller, "_prompt_run_path_overrides", lambda _f: {}
    )
    monkeypatch.setattr(
        engine, "analyze_files",
        lambda *_a, **_k: [(str(mkv), 60.0, 100.0)],
    )
    monkeypatch.setattr(
        controller, "_select_and_move", lambda *_a, **_k: False,
    )
    monkeypatch.setattr(controller, "write_session_summary", lambda: None)
    monkeypatch.setattr(controller, "flush_log", lambda: None)

    controller.run_organize()

    assert spy.reset_count == 1
    assert spy.fail_count == 1
    assert spy.fail_reasons == ["organize_move_failed"]
    assert controller.sm.state is SessionState.FAILED


# --------------------------------------------------------------------------
# run_dump_all — resets at entry, complete/fail at terminal points
# --------------------------------------------------------------------------


def test_run_dump_all_resets_sm_on_entry_even_when_cancelled(
    tmp_path, monkeypatch
):
    """Cancel at path-overrides → workflow reset fires at the very
    top, leaving SM at INIT.  Pins the leak-fix for run_dump_all
    (parallel to run_organize)."""
    controller, _engine = _controller_with_engine()

    controller.sm.fail("simulated_prior_failure")
    assert controller.sm.state is SessionState.FAILED

    spy = _SMSpy(controller)
    spy.install()

    monkeypatch.setattr(
        controller, "_prompt_run_path_overrides", lambda _f: None
    )

    controller.run_dump_all()

    assert spy.reset_count == 1
    assert spy.fail_count == 0
    assert spy.complete_count == 0
    assert controller.sm.state is SessionState.INIT, (
        "leaked FAILED state cleared by reset at entry"
    )


def test_run_dump_all_happy_path_resets_and_completes(
    tmp_path, monkeypatch
):
    """Happy single-disc dump → reset at entry, sm.complete() at
    flow end.  Final state is COMPLETED.  Closes the cross-cutting
    criterion for run_dump_all."""
    controller, engine = _controller_with_engine()

    controller.sm.fail("simulated_prior_failure")
    assert controller.sm.state is SessionState.FAILED

    spy = _SMSpy(controller)
    spy.install()

    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    part1 = tmp_path / "title_t00.mkv"
    part1.write_bytes(b"a" * 10)

    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False

    controller.gui.ask_dump_setup = lambda **_kw: DumpSessionSetup(
        multi_disc=False,
        disc_name="Test Dump Disc",
        disc_count=1,
        custom_disc_names="",
        batch_title="",
    )
    controller.gui.ask_yesno = (
        lambda prompt: False
    )
    controller.gui.show_info = lambda *_a, **_k: None

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "write_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "update_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(
        engine, "run_job",
        lambda _job: (
            setattr(engine, "last_title_file_map", {0: [str(part1)]})
            or SimpleNamespace(success=True, errors=[])
        ),
    )
    monkeypatch.setattr(
        controller, "_normalize_rip_result",
        lambda *_a, **_k: (True, [str(part1)]),
    )
    monkeypatch.setattr(
        controller, "_stabilize_ripped_files",
        lambda *_a, **_k: (True, False),
    )
    monkeypatch.setattr(
        controller, "_verify_container_integrity",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(controller, "write_session_summary", lambda: None)
    monkeypatch.setattr(controller, "flush_log", lambda: None)

    controller.run_dump_all()

    assert spy.reset_count == 1
    assert spy.complete_count == 1, "sm.complete() fires on success"
    assert spy.fail_count == 0
    assert spy.transition_count == 0, (
        "dump_all doesn't walk intermediate states — reset+complete "
        "is the contract"
    )
    assert controller.sm.state is SessionState.COMPLETED


def test_run_dump_all_rip_failure_lands_on_failed(
    tmp_path, monkeypatch
):
    """When the rip subprocess fails, ``_state_fail("dump_rip_failed")``
    fires and ``sm.complete()`` (later) is a no-op because FAILED is
    sticky.  Pins the failure-path SM contract for single-disc dump."""
    controller, engine = _controller_with_engine()
    spy = _SMSpy(controller)
    spy.install()

    temp_root = tmp_path / "temp"
    temp_root.mkdir()

    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False

    controller.gui.ask_dump_setup = lambda **_kw: DumpSessionSetup(
        multi_disc=False,
        disc_name="Test Dump Disc",
        disc_count=1,
        custom_disc_names="",
        batch_title="",
    )
    controller.gui.ask_yesno = lambda _p: False
    controller.gui.show_info = lambda *_a, **_k: None

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "write_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(
        engine, "run_job",
        lambda _job: SimpleNamespace(success=False, errors=["disc error"]),
    )
    monkeypatch.setattr(
        controller, "_normalize_rip_result",
        lambda *_a, **_k: (False, []),
    )
    monkeypatch.setattr(controller, "flush_log", lambda: None)

    controller.run_dump_all()

    assert spy.reset_count == 1
    assert spy.fail_count == 1
    assert "dump_rip_failed" in spy.fail_reasons
    assert controller.sm.state is SessionState.FAILED


# --------------------------------------------------------------------------
# _run_disc_inner — resets at start, completes at end, no intermediates
# --------------------------------------------------------------------------


def test_run_disc_inner_cancel_at_path_overrides_does_not_touch_sm(
    monkeypatch,
):
    """Cancel BEFORE path-overrides commit → no SM interaction.  Pins
    that the SM reset only fires after path_overrides is non-None."""
    controller, _engine = _controller_with_engine()
    spy = _SMSpy(controller)
    spy.install()

    monkeypatch.setattr(
        controller, "_prompt_run_path_overrides", lambda _f: None
    )

    controller.run_movie_disc()

    assert spy.total_calls == 0


def test_run_disc_inner_resets_then_aborts_before_any_transition(
    tmp_path, monkeypatch
):
    """When path-overrides are accepted but abort fires immediately
    after the SM reset, the only SM interaction is the reset call —
    no intermediate transitions, no fail, no complete.  Pins the
    contract that ``_run_disc_inner`` walks INIT (post-reset) and
    nothing else when aborted that early."""
    controller, engine = _controller_with_engine()
    spy = _SMSpy(controller)
    spy.install()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    tv_root = tmp_path / "tv"
    for p in (temp_root, movies_root, tv_root):
        p.mkdir(parents=True)

    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)
    engine.cfg["tv_folder"] = str(tv_root)
    engine.cfg["opt_show_temp_manager"] = False

    monkeypatch.setattr(
        controller, "_prompt_run_path_overrides", lambda _f: {}
    )

    # cleanup_partial_files runs after the reset; flip abort there so
    # the next ``if abort_event.is_set(): return`` fires (l. 2764).
    def _cleanup_then_abort(*_a, **_k):
        engine.abort_event.set()
    monkeypatch.setattr(
        engine, "cleanup_partial_files", _cleanup_then_abort
    )

    controller.run_movie_disc()

    assert spy.reset_count == 1, (
        f"_run_disc_inner should reset SM exactly once before abort; "
        f"got {spy.reset_count}"
    )
    assert spy.transition_count == 0, (
        "no SCANNED→RIPPED→… transitions should fire when aborted "
        "right after reset"
    )
    assert spy.fail_count == 0
    assert spy.complete_count == 0
    # SM is back at INIT after reset.
    assert controller.sm.state is SessionState.INIT


def test_run_disc_inner_tv_variant_also_resets_only(
    tmp_path, monkeypatch
):
    """TV-disc entry point uses the same _run_disc_inner code path —
    pin that ``run_tv_disc`` resets the SM exactly once and then
    aborts cleanly with no further interaction.  Symmetric to the
    movie variant above."""
    controller, engine = _controller_with_engine()
    spy = _SMSpy(controller)
    spy.install()

    temp_root = tmp_path / "temp"
    tv_root = tmp_path / "tv"
    for p in (temp_root, tv_root):
        p.mkdir(parents=True)

    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["tv_folder"] = str(tv_root)
    engine.cfg["opt_show_temp_manager"] = False

    monkeypatch.setattr(
        controller, "_prompt_run_path_overrides", lambda _f: {}
    )

    def _cleanup_then_abort(*_a, **_k):
        engine.abort_event.set()
    monkeypatch.setattr(
        engine, "cleanup_partial_files", _cleanup_then_abort
    )

    controller.run_tv_disc()

    assert spy.reset_count == 1
    assert spy.transition_count == 0
    assert spy.fail_count == 0
    assert spy.complete_count == 0
    assert controller.sm.state is SessionState.INIT


def test_run_disc_inner_reset_clears_prior_failed_state(
    tmp_path, monkeypatch
):
    """Unlike run_organize/run_dump_all, _run_disc_inner DOES reset
    so a leaked FAILED state from a prior session is cleared.  Pins
    the asymmetric contract: this is the one place outside
    run_smart_rip where the SM gets reset properly."""
    controller, engine = _controller_with_engine()

    # Pre-load FAILED.
    controller.sm.fail("simulated_prior_failure")
    assert controller.sm.state is SessionState.FAILED

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

    controller.run_movie_disc()

    # The reset cleared FAILED → INIT.
    assert controller.sm.state is SessionState.INIT, (
        "_run_disc_inner's _reset_state_machine call MUST clear a "
        "leaked FAILED state from the previous run."
    )


# --------------------------------------------------------------------------
# run_smart_rip — sanity check that this audit's spy approach works
# --------------------------------------------------------------------------


def test_smart_rip_path_overrides_cancel_does_not_touch_sm(monkeypatch):
    """Sanity check: even ``run_smart_rip`` doesn't touch SM if cancelled
    at path-overrides (the reset is at l. 703, after the cancel guard
    at l. 694).  Pins this small contract — useful as a baseline for
    comparing against the other workflows."""
    controller, _engine = _controller_with_engine()
    spy = _SMSpy(controller)
    spy.install()

    monkeypatch.setattr(
        controller, "_prompt_run_path_overrides", lambda _f: None
    )

    controller.run_smart_rip()

    assert spy.total_calls == 0, (
        "run_smart_rip cancelled at path-overrides should not "
        "touch the SM (the reset comes after the cancel guard)"
    )
