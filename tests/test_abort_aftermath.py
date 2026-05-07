"""Abort aftermath tests — pins the post-abort cleanup contract.

**Updated 2026-05-03 to reflect the implementation of workflow-level
abort cleanup.**  The earlier version of this file documented audit
*findings* (a gap between the criteria doc and the code).  After the
user direction *"remove that resume thing we said its bad and make
it match the docks"*, the gap is closed: abort-cleanup is now a
real path, the criteria doc matches the code, and these tests pin
the new contract.

The contract:

1. **On user abort during a rip workflow**, the workflow's outer
   ``try/finally`` calls ``_finalize_abort_cleanup_if_needed()``,
   which:
   - Marks the session ``status="aborted"``, ``phase="aborted"`` via
     ``mark_session_aborted`` (mirrors ``mark_session_failed``).
   - Wipes partial output files (``.mkv`` / ``.partial``) so no
     half-written disc data sits in the temp folder.
   - Keeps the metadata file as a tombstone for diagnostics.
2. **``find_resumable_sessions`` filters out** ``phase="aborted"``,
   so the user does NOT see aborted sessions in the resume picker.
3. **Engine-level ``abort_event.set()`` alone does NOT clean up.**
   Cleanup is workflow-driven; setting the flag without running
   through a workflow's try/finally is a no-op on the cleanup side.
   This is intentional — the engine doesn't know which workflow
   owns the current rip_path.
4. **Resume after FAILURE still works** (it's the failure path's
   ``_preserve_partial_session`` call that records resumable state).
   Resume after USER ABORT does NOT — the user explicitly chose
   to stop, so resume-from-where-they-left-off is intentionally
   unavailable.

Behavior-first.  No GUI/Tk touches, no real subprocess.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from engine.ripper_engine import RipperEngine
from tests.test_behavior_guards import _controller_with_engine


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


# --------------------------------------------------------------------------
# Helper: mark_session_aborted contract (mirror of mark_session_failed)
# --------------------------------------------------------------------------


def test_mark_session_aborted_writes_aborted_status_and_phase(tmp_path):
    """``mark_session_aborted`` writes ``status="aborted"`` and
    ``phase="aborted"`` so the metadata accurately reflects the user's
    Stop Session click.  Mirrors ``mark_session_failed`` but with
    aborted-not-failed semantics."""
    from controller.session_recovery import mark_session_aborted

    engine = _engine()
    rip_path = tmp_path / "session"
    rip_path.mkdir()
    engine.write_temp_metadata(
        str(rip_path), title="Movie", disc_number=1, phase="ripping",
    )

    wiped: set[str] = set()
    mark_session_aborted(
        engine, str(rip_path),
        wiped_session_paths=wiped,
        log_fn=lambda _msg: None,
    )

    meta = engine.read_temp_metadata(str(rip_path))
    assert meta is not None
    assert meta["status"] == "aborted"
    assert meta["phase"] == "aborted"


def test_mark_session_aborted_wipes_partial_outputs(tmp_path):
    """``mark_session_aborted`` removes ``.mkv`` and ``.partial`` files
    inside the rip folder.  No half-written disc data left behind.
    Pins the no-zombie-temp-folder contract."""
    from controller.session_recovery import mark_session_aborted

    engine = _engine()
    rip_path = tmp_path / "session"
    rip_path.mkdir()
    engine.write_temp_metadata(
        str(rip_path), title="Movie", disc_number=1, phase="ripping",
    )
    partial_mkv = rip_path / "title_t00.mkv"
    partial_mkv.write_text("half a rip")
    partial_marker = rip_path / "title_t00.mkv.partial"
    partial_marker.write_text("growing")

    wiped: set[str] = set()
    mark_session_aborted(
        engine, str(rip_path),
        wiped_session_paths=wiped,
        log_fn=lambda _msg: None,
    )

    assert not partial_mkv.exists(), (
        ".mkv inside aborted session must be wiped"
    )
    assert not partial_marker.exists(), (
        ".partial inside aborted session must be wiped"
    )
    # Metadata tombstone is preserved.
    assert (rip_path / "_rip_meta.json").exists()


def test_mark_session_aborted_is_idempotent(tmp_path):
    """Calling ``mark_session_aborted`` twice is safe — the
    ``wiped_session_paths`` set guards against double-wipe.  Pins
    the same idempotency pattern that ``mark_session_failed`` uses."""
    from controller.session_recovery import mark_session_aborted

    engine = _engine()
    rip_path = tmp_path / "session"
    rip_path.mkdir()
    engine.write_temp_metadata(
        str(rip_path), title="X", disc_number=1, phase="ripping",
    )

    wipe_count = {"n": 0}
    original_wipe = engine.wipe_session_outputs

    def _spy_wipe(path, log_fn):
        wipe_count["n"] += 1
        return original_wipe(path, log_fn)
    engine.wipe_session_outputs = _spy_wipe

    wiped: set[str] = set()
    mark_session_aborted(
        engine, str(rip_path),
        wiped_session_paths=wiped, log_fn=lambda _: None,
    )
    mark_session_aborted(
        engine, str(rip_path),
        wiped_session_paths=wiped, log_fn=lambda _: None,
    )

    assert wipe_count["n"] == 1, (
        f"second mark_session_aborted must NOT re-wipe; got "
        f"{wipe_count['n']} calls"
    )


def test_mark_session_failed_writes_failed_not_aborted(tmp_path):
    """``mark_session_failed`` writes ``status="failed"`` /
    ``phase="failed"`` — distinct from ``mark_session_aborted``.
    Pins that the two paths produce DIFFERENT metadata so callers
    (and the resume picker) can tell them apart."""
    from controller.session_recovery import mark_session_failed

    engine = _engine()
    rip_path = tmp_path / "session"
    rip_path.mkdir()
    engine.write_temp_metadata(
        str(rip_path), title="Movie", disc_number=1, phase="ripping",
    )

    wiped: set[str] = set()
    mark_session_failed(
        engine, str(rip_path),
        wiped_session_paths=wiped, log_fn=lambda _: None,
        metadata={"title": "Movie", "media_type": "movie"},
    )

    meta = engine.read_temp_metadata(str(rip_path))
    assert meta is not None
    assert meta["status"] == "failed", (
        "mark_session_failed must write 'failed' not 'aborted'"
    )
    assert meta["phase"] == "failed"


# --------------------------------------------------------------------------
# find_resumable_sessions filter: aborted sessions are NOT resumable
# --------------------------------------------------------------------------


def test_find_resumable_sessions_skips_aborted_phase(tmp_path):
    """Aborted sessions do NOT appear in the resume picker.  Pins
    the filter at ``ripper_engine.py:546`` that excludes
    ``phase="aborted"`` alongside ``complete``/``organized``.  This
    is what makes user-cancel actually mean "cancelled" — without
    it, aborted sessions would leak back into the picker.

    Note: ``phase="failed"`` is NOT filtered — failed sessions still
    show in the picker so users can retry a crashed rip.  Only
    user-explicit *abort* makes a session non-resumable."""
    engine = _engine()
    temp_root = tmp_path / "temp"
    temp_root.mkdir()

    aborted = temp_root / "Aborted_Session"
    aborted.mkdir()
    engine.write_temp_metadata(
        str(aborted), title="Aborted", disc_number=1, phase="ripping",
    )
    # Mark it aborted via update.
    engine.update_temp_metadata(
        str(aborted), status="aborted", phase="aborted",
    )
    pending = temp_root / "Failed_Session"
    pending.mkdir()
    engine.write_temp_metadata(
        str(pending), title="Failed", disc_number=1, phase="failed",
    )
    engine.update_temp_metadata(
        str(pending), status="failed", phase="failed",
    )

    found = engine.find_resumable_sessions(str(temp_root))
    titles = sorted(meta["title"] for _, _, meta, _ in found)

    assert "Aborted" not in titles, (
        "phase='aborted' must be filtered out of resume picker"
    )
    assert titles == ["Failed"], (
        f"only 'Failed' (phase=failed, retryable) should remain; "
        f"got {titles}"
    )


# --------------------------------------------------------------------------
# Engine-level: setting abort_event alone does NOT clean up
# --------------------------------------------------------------------------


def test_engine_abort_event_alone_does_not_modify_metadata(tmp_path):
    """Engine-level: setting ``abort_event`` directly (without
    running through a workflow's try/finally) does NOT trigger
    cleanup.  This is intentional — the engine doesn't know which
    workflow owns the current rip_path.  Cleanup is the workflow's
    job."""
    engine = _engine()
    rip_path = tmp_path / "session"
    rip_path.mkdir()
    engine.write_temp_metadata(
        str(rip_path), title="X", disc_number=1, phase="ripping",
    )

    engine.abort_event.set()  # equivalent to "Stop Session"
    # ... but no workflow ran the cleanup hook ...

    meta = engine.read_temp_metadata(str(rip_path))
    assert meta is not None
    assert meta["phase"] == "ripping", (
        "engine.abort_event.set() alone does NOT change metadata "
        "phase — workflow's finally hook is what does the cleanup"
    )
    # The folder and metadata file are still there.
    assert rip_path.exists()
    assert (rip_path / "_rip_meta.json").exists()


def test_engine_cleanup_partial_files_still_gated_on_opt(tmp_path):
    """``cleanup_partial_files`` (next-launch cleanup) remains
    gated on ``opt_clean_partials_startup`` — UNCHANGED by the
    workflow-level abort cleanup work.  These are two distinct
    cleanup paths: the new one is workflow-driven on abort, the
    old one is launch-driven via cfg flag."""
    engine = _engine(opt_clean_partials_startup=False)
    rip_path = tmp_path / "session"
    rip_path.mkdir()
    partial = rip_path / "title.mkv.partial"
    partial.write_text("growing")

    engine.cleanup_partial_files(str(rip_path), lambda _: None)

    assert partial.exists(), (
        "opt_clean_partials_startup=False still suppresses "
        "next-launch cleanup; the new workflow abort path doesn't "
        "change this"
    )


# --------------------------------------------------------------------------
# Workflow-level: the new cleanup contract via _finalize_abort_cleanup_if_needed
# --------------------------------------------------------------------------


def test_finalize_abort_cleanup_marks_aborted_when_flag_set(tmp_path):
    """When abort_event is set AND ``_current_rip_path`` is populated
    AND the metadata phase is non-terminal, the cleanup hook marks
    the session aborted and wipes outputs.  Pins the
    ``_finalize_abort_cleanup_if_needed`` contract."""
    controller, engine = _controller_with_engine()

    rip_path = tmp_path / "session"
    rip_path.mkdir()
    engine.write_temp_metadata(
        str(rip_path), title="Movie", disc_number=1, phase="ripping",
    )
    (rip_path / "title.mkv").write_text("partial rip")

    # Simulate a workflow at the point where it set _current_rip_path
    # and then the user clicked Stop Session.
    controller._current_rip_path = str(rip_path)
    engine.abort_event.set()

    controller._finalize_abort_cleanup_if_needed()

    meta = engine.read_temp_metadata(str(rip_path))
    assert meta is not None
    assert meta["status"] == "aborted"
    assert meta["phase"] == "aborted"
    assert not (rip_path / "title.mkv").exists(), (
        "partial outputs must be wiped"
    )
    # _current_rip_path is reset so the next run starts clean.
    assert controller._current_rip_path is None


def test_finalize_abort_cleanup_skips_when_no_rip_path(tmp_path):
    """When ``_current_rip_path`` is None (no session active), the
    hook is a no-op even if the abort flag is set.  Pins the early
    return at the start of ``_finalize_abort_cleanup_if_needed``."""
    controller, engine = _controller_with_engine()
    controller._current_rip_path = None
    engine.abort_event.set()

    controller._finalize_abort_cleanup_if_needed()  # must not raise


def test_finalize_abort_cleanup_skips_terminal_phases(tmp_path):
    """When the session's phase is already terminal (complete /
    organized / failed / aborted), the hook does NOT overwrite it
    with "aborted".  Pins the phase-check guard — protects sessions
    that finished or failed cleanly before the user clicked Stop."""
    controller, engine = _controller_with_engine()

    for phase in ("complete", "organized", "failed", "aborted"):
        rip_path = tmp_path / f"session_{phase}"
        rip_path.mkdir()
        engine.write_temp_metadata(
            str(rip_path), title="X", disc_number=1, phase=phase,
        )
        engine.update_temp_metadata(str(rip_path), phase=phase)

        controller._current_rip_path = str(rip_path)
        engine.abort_event.set()

        controller._finalize_abort_cleanup_if_needed()

        meta = engine.read_temp_metadata(str(rip_path))
        assert meta is not None
        assert meta["phase"] == phase, (
            f"terminal phase '{phase}' must not be overwritten "
            f"with 'aborted'"
        )

        # Reset for next iteration.
        engine.reset_abort()
        controller._current_rip_path = None


def test_finalize_abort_cleanup_skips_when_flag_not_set(tmp_path):
    """When abort_event is NOT set, the hook is a no-op even if
    ``_current_rip_path`` is populated.  Pins the second early
    return — this is what happens on the SUCCESS path of every
    workflow (abort flag clean, hook fires from finally, no-op)."""
    controller, engine = _controller_with_engine()

    rip_path = tmp_path / "session"
    rip_path.mkdir()
    engine.write_temp_metadata(
        str(rip_path), title="Movie", disc_number=1, phase="ripping",
    )

    controller._current_rip_path = str(rip_path)
    # abort flag NOT set
    assert not engine.abort_event.is_set()

    controller._finalize_abort_cleanup_if_needed()

    meta = engine.read_temp_metadata(str(rip_path))
    assert meta is not None
    assert meta["phase"] == "ripping", (
        "no abort → no cleanup; phase stays at the workflow's value"
    )


# --------------------------------------------------------------------------
# End-to-end: workflow run with abort triggers the cleanup
# --------------------------------------------------------------------------


def test_run_smart_rip_aborted_mid_flow_marks_session_aborted(
    tmp_path, monkeypatch,
):
    """End-to-end: ``run_smart_rip`` opens a session, the user
    clicks Stop, the workflow's outer finally fires the cleanup
    hook, and the session ends up marked aborted with outputs
    wiped.  This is the new contract that closes the
    abort-propagation criterion."""
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    tv_root = tmp_path / "tv"
    for p in (temp_root, movies_root, tv_root):
        p.mkdir(parents=True)

    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)
    engine.cfg["tv_folder"] = str(tv_root)
    engine.cfg["opt_show_temp_manager"] = False

    # Pretend a session was opened (write_temp_metadata fired)
    # and _current_rip_path was set to mark the workflow as having
    # an active session.
    rip_path = temp_root / "Disc_aborted"
    rip_path.mkdir()
    engine.write_temp_metadata(
        str(rip_path), title="Movie", disc_number=1, phase="ripping",
    )
    (rip_path / "partial.mkv").write_text("half a rip")

    # Simulate the workflow reaching its abort-check by having
    # cleanup_partial_files (called inside _run_smart_rip_inner)
    # assign current_rip_path then trip abort.  This mirrors the
    # actual workflow flow.
    def _cleanup_then_set_path_and_abort(*_a, **_k):
        controller._current_rip_path = str(rip_path)
        engine.abort_event.set()
    monkeypatch.setattr(
        engine, "cleanup_partial_files", _cleanup_then_set_path_and_abort
    )
    monkeypatch.setattr(
        controller, "_prompt_run_path_overrides", lambda _f: {}
    )

    controller.run_smart_rip()

    # The workflow's outer finally fired the cleanup hook.
    meta = engine.read_temp_metadata(str(rip_path))
    assert meta is not None
    assert meta["phase"] == "aborted", (
        "run_smart_rip's outer finally must mark the session aborted"
    )
    assert meta["status"] == "aborted"
    assert not (rip_path / "partial.mkv").exists(), (
        "partial outputs wiped by the cleanup hook"
    )
    # And it does NOT show in the resume picker.
    found = engine.find_resumable_sessions(str(temp_root))
    assert found == [], (
        "aborted session must NOT appear in the resume picker"
    )


# --------------------------------------------------------------------------
# Resume after FAILURE still works (separate path from abort)
# --------------------------------------------------------------------------


def test_partial_session_via_preserve_still_resumable(tmp_path):
    """Sessions explicitly preserved via ``_preserve_partial_session``
    (e.g., partial-rip success path) are still resumable.  Pins
    that the new abort cleanup does NOT break the existing partial-
    rip resume flow — only USER ABORT is non-resumable; partial
    success and failure are different paths."""
    controller, engine = _controller_with_engine()
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    rip_path = temp_root / "Partial_Session"
    rip_path.mkdir()
    engine.write_temp_metadata(
        str(rip_path), title="Show", disc_number=2,
        media_type="tv", phase="ripping",
        selected_titles=[0, 1, 2, 3],
    )

    controller._preserve_partial_session(
        str(rip_path),
        title="Show", year="2008", media_type="tv", season=1,
        selected_titles=[0, 1, 2, 3],
        completed_titles=[0, 1],
        failed_titles=[],
    )

    found = engine.find_resumable_sessions(str(temp_root))
    assert len(found) == 1
    _, _, meta, _ = found[0]
    assert meta["phase"] == "partial", (
        "partial sessions remain resumable; only aborted ones are filtered"
    )
    assert meta["completed_titles"] == [0, 1]


def test_organize_uses_completed_session_phase_to_filter_from_resume(
    tmp_path,
):
    """After a successful organize, ``delete_temp_metadata`` removes
    the rip-meta sidecar — the session no longer appears in the
    resume picker.  This is unchanged by the new abort cleanup;
    pinned here for completeness."""
    engine = _engine()
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    rip_path = temp_root / "Disc_2026-05-03"
    rip_path.mkdir()
    engine.write_temp_metadata(
        str(rip_path), title="Movie", disc_number=1, phase="ripping",
    )

    # Before cleanup: discoverable.
    assert len(engine.find_resumable_sessions(str(temp_root))) == 1

    # Successful organize → delete_temp_metadata.
    engine.delete_temp_metadata(str(rip_path), on_log=lambda _: None)

    # After cleanup: not discoverable.
    assert engine.find_resumable_sessions(str(temp_root)) == []
