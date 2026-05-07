"""Disk-space pre-check tests (engine-level + workflow-integration).

Closes the cross-cutting criterion *"Disk-space pre-checks fire before
destructive work"* in
[docs/workflow-stabilization-criteria.md](../docs/workflow-stabilization-criteria.md):

    Disk-space pre-checks fire before destructive work: per
    `opt_check_dest_space` and `opt_warn_low_space` — workflows must
    call these gates before write-heavy operations.

Pre-existing coverage: zero.  ``ask_space_override`` was a no-op in
``DummyGUI`` and existing tests bypassed disk-space checks via
``opt_check_dest_space=False``.  This file pins the actual contract.

Two layers:

1. **Engine-level** (``RipperEngine.check_disk_space``) — pure-function
   tests of the block/warn/ok status decision tree, with
   ``shutil.disk_usage`` monkeypatched so the tests are deterministic
   on any drive.
2. **Workflow-integration** (``run_smart_rip``) — pin that the
   controller actually calls ``check_disk_space`` BEFORE the rip
   subprocess starts when conditions hold, that "block" stops the
   rip, that "warn" prompts ``ask_space_override`` only when
   ``opt_warn_low_space`` is True, and that the user's accept/decline
   choice is honored.

Behavior-first.  No GUI/Tk touches, no real ``shutil.disk_usage``
calls.  Survives the planned PySide6 migration per decision #5 in
``docs/pyside6-migration-plan.md``.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from engine.ripper_engine import RipperEngine
from utils.classifier import ClassifiedTitle
from shared.session_setup_types import MovieSessionSetup

from tests.test_behavior_guards import _controller_with_engine


# --------------------------------------------------------------------------
# Smart-rip wiring helper (inlined here so the file is self-contained on
# both MAIN and AI BRANCH — AI BRANCH does not have
# tests/test_pipeline_state_trajectory.py).  Mirrors the helper of the
# same name in MAIN's test_pipeline_state_trajectory.py.
# --------------------------------------------------------------------------


def _wire_smart_rip_movie_happy_path(
    controller, engine, monkeypatch, tmp_path,
    *,
    main_indices_only: bool = True,
):
    """Stand up a ``run_smart_rip()`` movie flow that reaches COMPLETED.

    Mirrors the setup in
    ``test_run_smart_rip_wizard_flow_completes_movie`` in
    test_behavior_guards.py, factored so individual tests can override
    one fake (e.g., to make ``check_disk_space`` return 'block') and
    exercise that branch.
    """
    from shared.wizard_types import ContentSelection

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    tv_root = tmp_path / "tv"
    for p in (temp_root, movies_root, tv_root):
        p.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)
    engine.cfg["tv_folder"] = str(tv_root)

    controller.gui.show_info = lambda *_a, **_k: None
    controller.gui.ask_yesno = lambda _p: True

    controller.gui.show_scan_results_step = lambda _cl, _di=None: "movie"
    controller.gui.ask_movie_setup = lambda **_kw: MovieSessionSetup(
        title="Chosen Movie", year="2024", edition="",
        metadata_provider="TMDB", metadata_id="",
        replace_existing=False, keep_raw=False, extras_mode="ask",
    )
    controller.gui.show_content_mapping_step = lambda _cl: ContentSelection(
        main_title_ids=[0],
        extra_title_ids=[],
        skip_title_ids=[1] if main_indices_only else [],
    )
    controller.gui.show_output_plan_step = lambda *_a, **_k: True

    disc_titles = [
        {"id": 0, "name": "Main Feature",
         "duration": "120:00", "size": "4.0 GB",
         "duration_seconds": 7200, "size_bytes": 4_000_000_000},
        {"id": 1, "name": "Bonus",
         "duration": "10:00", "size": "0.6 GB",
         "duration_seconds": 600, "size_bytes": 600_000_000},
    ]
    analyzed = [(str(temp_root / "main.mkv"), 7200.0, 4000.0)]

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "write_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "update_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)

    main_ct = ClassifiedTitle(
        title=disc_titles[0], score=0.80, label="MAIN",
        confidence=0.95, reasons=["longest duration"],
    )
    extra_ct = ClassifiedTitle(
        title=disc_titles[1], score=0.20, label="EXTRA",
        confidence=0.95, reasons=["short duration"],
    )
    monkeypatch.setattr(
        "controller.controller.classify_and_pick_main",
        lambda *_a, **_k: (main_ct, [main_ct, extra_ct]),
    )
    monkeypatch.setattr(
        "controller.controller.format_classification_log",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        engine, "run_job",
        lambda _job: SimpleNamespace(success=True, errors=[]),
    )
    monkeypatch.setattr(
        controller, "_normalize_rip_result",
        lambda *_a, **_k: (True, [item[0] for item in analyzed]),
    )
    monkeypatch.setattr(
        controller, "_stabilize_ripped_files",
        lambda *_a, **_k: (True, False),
    )
    monkeypatch.setattr(
        controller, "_verify_expected_sizes",
        lambda *_a, **_k: ("pass", "ok"),
    )
    monkeypatch.setattr(
        controller, "_verify_container_integrity",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(engine, "analyze_files", lambda *_a, **_k: analyzed)
    engine.last_title_file_map = {}

    monkeypatch.setattr(
        engine, "move_files",
        lambda *_a, **_k: (True, _a[10] if len(_a) > 10 else 0,
                           [str(tmp_path / "library" / "main.mkv")]),
    )

    return temp_root, movies_root


# --------------------------------------------------------------------------
# Engine-level: RipperEngine.check_disk_space
# --------------------------------------------------------------------------


def _engine(**cfg_overrides):
    """Build a minimal RipperEngine for check_disk_space tests."""
    cfg = {
        "makemkvcon_path": "makemkvcon",
        "ffprobe_path": "ffprobe",
        "opt_makemkv_global_args": "",
        "opt_makemkv_rip_args": "",
        "opt_drive_index": 0,
        "opt_hard_block_gb": 20,  # default per ripper_engine.py:1154
    }
    cfg.update(cfg_overrides)
    return RipperEngine(cfg)


def _patch_disk_usage(monkeypatch, free_bytes):
    """Monkeypatch shutil.disk_usage to return a controlled free-byte
    count.  Returns the tuple shape ``shutil.disk_usage`` produces."""
    monkeypatch.setattr(
        "engine.ripper_engine.shutil.disk_usage",
        lambda _path: SimpleNamespace(
            total=free_bytes * 4,
            used=free_bytes * 3,
            free=free_bytes,
        ),
    )


def test_check_disk_space_ok_when_free_above_required(tmp_path, monkeypatch):
    """Free space above required → status='ok', returns the actual
    free + required values for callers to log."""
    engine = _engine()
    _patch_disk_usage(monkeypatch, 100 * (1024**3))  # 100 GB free

    status, free, required = engine.check_disk_space(
        str(tmp_path), 30 * (1024**3), lambda _msg: None
    )

    assert status == "ok"
    assert free == 100 * (1024**3)
    assert required == 30 * (1024**3)


def test_check_disk_space_warn_when_below_required_above_hard_floor(
    tmp_path, monkeypatch,
):
    """Free space below required but above hard_floor (20 GB default)
    → status='warn'.  Caller decides whether to honor it via
    ``opt_warn_low_space``."""
    engine = _engine()  # hard_floor = 20 GB
    _patch_disk_usage(monkeypatch, 25 * (1024**3))  # 25 GB free

    status, _free, _required = engine.check_disk_space(
        str(tmp_path), 30 * (1024**3), lambda _msg: None
    )

    assert status == "warn"


def test_check_disk_space_block_when_below_hard_floor(
    tmp_path, monkeypatch,
):
    """Free space below hard_floor → status='block'.  Workflows must
    not proceed past this — the rip would surely fail."""
    engine = _engine()  # hard_floor = 20 GB
    _patch_disk_usage(monkeypatch, 5 * (1024**3))  # 5 GB free

    status, _free, _required = engine.check_disk_space(
        str(tmp_path), 30 * (1024**3), lambda _msg: None
    )

    assert status == "block"


def test_check_disk_space_path_missing_returns_ok_with_warning(
    tmp_path, monkeypatch,
):
    """Path doesn't exist (e.g., disconnected drive) → 'ok' with a
    warning log.  The controller continues; the missing path will
    surface via the actual write attempt later."""
    nonexistent = tmp_path / "definitely_not_there"
    engine = _engine()

    logs: list[str] = []
    status, free, required = engine.check_disk_space(
        str(nonexistent), 30 * (1024**3), logs.append
    )

    assert status == "ok"
    assert free == 0
    assert required == 30 * (1024**3)
    assert any("does not exist" in m for m in logs)


def test_check_disk_space_disk_usage_exception_returns_ok_with_warning(
    tmp_path, monkeypatch,
):
    """``shutil.disk_usage`` raises (e.g., permission denied on a
    network share) → status='ok' with a warning log.  Same defensive
    fallback as path-missing."""
    monkeypatch.setattr(
        "engine.ripper_engine.shutil.disk_usage",
        lambda _path: (_ for _ in ()).throw(
            PermissionError("simulated denied")
        ),
    )
    engine = _engine()

    logs: list[str] = []
    status, _free, _required = engine.check_disk_space(
        str(tmp_path), 30 * (1024**3), logs.append
    )

    assert status == "ok"
    assert any("could not check disk space" in m for m in logs)


def test_check_disk_space_custom_hard_block_gb_honored(
    tmp_path, monkeypatch,
):
    """``opt_hard_block_gb`` overrides the 20 GB default.  Setting it
    to 100 GB means 25 GB free → 'block' (was 'warn' under default)."""
    engine = _engine(opt_hard_block_gb=100)
    _patch_disk_usage(monkeypatch, 25 * (1024**3))

    status, _free, _required = engine.check_disk_space(
        str(tmp_path), 30 * (1024**3), lambda _msg: None
    )

    assert status == "block", (
        "raising hard_block_gb to 100 should turn a 25 GB free "
        "result into block"
    )


# --------------------------------------------------------------------------
# Workflow-integration: run_smart_rip respects opt_scan_disc_size and
# the disk-space gate decisions
# --------------------------------------------------------------------------


def _wire_smart_rip_with_disk_check(
    controller, engine, monkeypatch, tmp_path,
):
    """Wire run_smart_rip happy-path AND re-enable opt_scan_disc_size
    so the disk-space pre-check actually fires.  The base helper
    disables it for state-machine tests; we want it on here."""
    _wire_smart_rip_movie_happy_path(
        controller, engine, monkeypatch, tmp_path
    )
    engine.cfg["opt_scan_disc_size"] = True


def test_run_smart_rip_calls_check_disk_space_before_run_job(
    tmp_path, monkeypatch,
):
    """When opt_scan_disc_size=True and selected_size>0, the
    controller invokes ``engine.check_disk_space`` BEFORE the rip
    subprocess starts.  Pins call order: check then rip, never rip
    then check."""
    controller, engine = _controller_with_engine()
    _wire_smart_rip_with_disk_check(
        controller, engine, monkeypatch, tmp_path
    )

    call_order: list[str] = []

    original_run_job = engine.run_job

    def _spy_check(_path, _required, _on_log):
        call_order.append("check_disk_space")
        return ("ok", 200 * (1024**3), 4 * (1024**3))

    def _spy_run_job(job):
        call_order.append("run_job")
        return original_run_job(job)

    monkeypatch.setattr(engine, "check_disk_space", _spy_check)
    monkeypatch.setattr(engine, "run_job", _spy_run_job)

    controller.run_smart_rip()

    assert "check_disk_space" in call_order, (
        "engine.check_disk_space must be called when "
        "opt_scan_disc_size=True"
    )
    assert "run_job" in call_order
    assert call_order.index("check_disk_space") < call_order.index("run_job"), (
        f"check_disk_space must come before run_job; "
        f"order was {call_order}"
    )


def test_run_smart_rip_skips_disk_check_when_opt_scan_disc_size_off(
    tmp_path, monkeypatch,
):
    """opt_scan_disc_size=False → temp-space pre-check is bypassed.
    Pins this gotcha: the temp-space check is gated by
    ``opt_scan_disc_size``, NOT by a dedicated disk-space option."""
    controller, engine = _controller_with_engine()
    _wire_smart_rip_with_disk_check(
        controller, engine, monkeypatch, tmp_path
    )
    engine.cfg["opt_scan_disc_size"] = False

    check_calls: list[tuple] = []

    def _spy_check(*args, **_kwargs):
        check_calls.append(args)
        return ("ok", 200 * (1024**3), 4 * (1024**3))

    monkeypatch.setattr(engine, "check_disk_space", _spy_check)

    controller.run_smart_rip()

    assert check_calls == [], (
        "check_disk_space should NOT be called when "
        "opt_scan_disc_size=False"
    )


def test_run_smart_rip_block_status_stops_before_rip(
    tmp_path, monkeypatch,
):
    """check_disk_space returns 'block' → ``run_job`` is never called,
    ``show_error`` is fired with the friendly low-space message.  Pins
    the data-loss gate: critically low space prevents destructive work."""
    controller, engine = _controller_with_engine()
    _wire_smart_rip_with_disk_check(
        controller, engine, monkeypatch, tmp_path
    )

    run_job_calls: list = []
    error_dialogs: list[tuple[str, str]] = []

    monkeypatch.setattr(
        engine, "check_disk_space",
        lambda *_a, **_kw: ("block", 5 * (1024**3), 30 * (1024**3)),
    )
    monkeypatch.setattr(
        engine, "run_job",
        lambda job: run_job_calls.append(job)
        or SimpleNamespace(success=True, errors=[]),
    )
    controller.gui.show_error = (
        lambda title, msg: error_dialogs.append((title, msg))
    )

    controller.run_smart_rip()

    assert run_job_calls == [], (
        "run_job must NOT be called when disk-space check returns "
        "'block'"
    )
    assert error_dialogs, "show_error must be called on block"
    title, body = error_dialogs[0]
    assert "Critically Low Space" in title
    assert "GB free" in body and "Minimum" in body, (
        "block dialog should surface free GB and minimum threshold "
        "so the user knows how much to free up"
    )


def test_run_smart_rip_warn_with_opt_warn_low_space_prompts_user(
    tmp_path, monkeypatch,
):
    """check_disk_space='warn' + opt_warn_low_space=True → controller
    asks the user via ``ask_space_override``.  User accepts →
    rip proceeds.  Pins the warn-prompt branch."""
    controller, engine = _controller_with_engine()
    _wire_smart_rip_with_disk_check(
        controller, engine, monkeypatch, tmp_path
    )
    engine.cfg["opt_warn_low_space"] = True

    override_calls: list[tuple[float, float]] = []

    def _accept(required, free):
        override_calls.append((required, free))
        return True  # user accepts the warning

    monkeypatch.setattr(
        engine, "check_disk_space",
        lambda *_a, **_kw: (
            "warn", 25 * (1024**3), 30 * (1024**3)
        ),
    )

    run_job_calls: list = []
    monkeypatch.setattr(
        engine, "run_job",
        lambda job: run_job_calls.append(job)
        or SimpleNamespace(success=True, errors=[]),
    )
    controller.gui.ask_space_override = _accept

    controller.run_smart_rip()

    assert override_calls, (
        "ask_space_override must be called on 'warn' status when "
        "opt_warn_low_space=True"
    )
    assert run_job_calls, (
        "run_job must run when the user accepts the warn-space prompt"
    )


def test_run_smart_rip_warn_with_user_decline_stops_before_rip(
    tmp_path, monkeypatch,
):
    """check_disk_space='warn' + user declines override → ``run_job``
    is NOT called.  Pins user-cancel respect at the warn boundary."""
    controller, engine = _controller_with_engine()
    _wire_smart_rip_with_disk_check(
        controller, engine, monkeypatch, tmp_path
    )
    engine.cfg["opt_warn_low_space"] = True

    monkeypatch.setattr(
        engine, "check_disk_space",
        lambda *_a, **_kw: (
            "warn", 25 * (1024**3), 30 * (1024**3)
        ),
    )

    run_job_calls: list = []
    monkeypatch.setattr(
        engine, "run_job",
        lambda job: run_job_calls.append(job)
        or SimpleNamespace(success=True, errors=[]),
    )
    controller.gui.ask_space_override = lambda _r, _f: False  # decline

    controller.run_smart_rip()

    assert run_job_calls == [], (
        "run_job must NOT run when the user declines the warn-space "
        "prompt"
    )


def test_run_smart_rip_warn_with_opt_warn_low_space_off_skips_prompt(
    tmp_path, monkeypatch,
):
    """check_disk_space='warn' + opt_warn_low_space=False → controller
    does NOT ask the user; rip proceeds silently with the warning
    only logged.  Pins the user-suppresses-warn branch."""
    controller, engine = _controller_with_engine()
    _wire_smart_rip_with_disk_check(
        controller, engine, monkeypatch, tmp_path
    )
    engine.cfg["opt_warn_low_space"] = False

    monkeypatch.setattr(
        engine, "check_disk_space",
        lambda *_a, **_kw: (
            "warn", 25 * (1024**3), 30 * (1024**3)
        ),
    )

    override_calls: list[tuple[float, float]] = []
    controller.gui.ask_space_override = (
        lambda r, f: override_calls.append((r, f)) or True
    )
    run_job_calls: list = []
    monkeypatch.setattr(
        engine, "run_job",
        lambda job: run_job_calls.append(job)
        or SimpleNamespace(success=True, errors=[]),
    )

    controller.run_smart_rip()

    assert override_calls == [], (
        "ask_space_override must NOT be called when "
        "opt_warn_low_space=False — the warn is silenced"
    )
    assert run_job_calls, (
        "run_job must run when warn is silenced — the user opted "
        "out of warn prompts"
    )
