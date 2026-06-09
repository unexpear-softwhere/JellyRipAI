"""Move-validation ordering + degraded-success gate pins.

Two engine defenses against corrupt files reaching the library:

1. ``move_file_atomic`` must validate the STAGED ``.partial`` copy
   BEFORE it takes the final name.  The old order validated after
   ``os.replace``, so a failed validation left the corrupt file
   sitting at the final library path under its real name (media
   servers index it immediately) while the session reported failure.
   In the non-atomic mode (``opt_atomic_move=False``) the move has
   already consumed the source, so a failed validation quarantines
   the destination as ``<name>.failed-validation`` instead.

2. ``rip_selected_titles``' degraded-success path (MakeMKV errored
   but produced output) must reject outputs far short of the scanned
   title size — a 60%-of-a-movie truncation is not a salvage.  When
   the scan size is unknown the legacy accept-with-warning behavior
   is preserved (pinned by the existing behavior-guard tests).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.ripper_engine import RipperEngine


def _engine(**overrides):
    cfg = {
        "makemkvcon_path": "makemkvcon",
        "ffprobe_path": "ffprobe",
        "opt_makemkv_global_args": "",
        "opt_makemkv_rip_args": "",
        "opt_drive_index": 0,
        "opt_move_verify_retries": 1,
        "opt_fsync": False,
    }
    cfg.update(overrides)
    return RipperEngine(cfg)


# ---------------------------------------------------------------------------
# 1a. Atomic mode: bad staged copy never takes the final name
# ---------------------------------------------------------------------------


def test_atomic_move_failed_probe_never_creates_destination(
    tmp_path, monkeypatch,
):
    engine = _engine(opt_atomic_move=True)
    src = tmp_path / "src" / "Movie (2024).mkv"
    src.parent.mkdir()
    src.write_bytes(b"x" * 4096)
    final = tmp_path / "library" / "Movie (2024).mkv"
    final.parent.mkdir()

    monkeypatch.setattr(engine, "_quick_ffprobe_ok", lambda _p, _l: False)

    logs: list[str] = []
    ok = engine.move_file_atomic(str(src), str(final), logs.append)

    assert ok is False
    assert not final.exists(), (
        "a copy that failed validation must never appear at the "
        "final library path"
    )
    assert not Path(str(final) + ".partial").exists(), \
        "the staged .partial must be cleaned up"
    assert src.exists(), "the source must be retained for retry"
    assert "staged copy" in (engine.last_move_error or "").lower()


def test_atomic_move_good_copy_lands_at_destination(tmp_path, monkeypatch):
    engine = _engine(opt_atomic_move=True)
    src = tmp_path / "src" / "Movie (2024).mkv"
    src.parent.mkdir()
    src.write_bytes(b"x" * 4096)
    final = tmp_path / "library" / "Movie (2024).mkv"
    final.parent.mkdir()

    monkeypatch.setattr(engine, "_quick_ffprobe_ok", lambda _p, _l: True)

    ok = engine.move_file_atomic(str(src), str(final), lambda _m: None)

    assert ok is True
    assert final.exists() and final.stat().st_size == 4096
    assert not src.exists(), "source is removed after a verified move"


# ---------------------------------------------------------------------------
# 1b. Non-atomic mode: failed validation quarantines the destination
# ---------------------------------------------------------------------------


def test_nonatomic_move_failed_probe_quarantines_destination(
    tmp_path, monkeypatch,
):
    engine = _engine(opt_atomic_move=False)
    src = tmp_path / "src" / "Movie (2024).mkv"
    src.parent.mkdir()
    src.write_bytes(b"x" * 4096)
    final = tmp_path / "library" / "Movie (2024).mkv"
    final.parent.mkdir()

    monkeypatch.setattr(engine, "_quick_ffprobe_ok", lambda _p, _l: False)

    logs: list[str] = []
    ok = engine.move_file_atomic(str(src), str(final), logs.append)

    assert ok is False
    assert not final.exists(), (
        "the corrupt file must not stay at the library path under "
        "its real name"
    )
    assert Path(str(final) + ".failed-validation").exists(), (
        "the bad destination is quarantined, not deleted — the bytes "
        "may still be salvageable"
    )


# ---------------------------------------------------------------------------
# 2. Degraded-success gate
# ---------------------------------------------------------------------------


def test_degraded_output_far_below_expected_size_is_rejected(
    tmp_path, monkeypatch,
):
    engine = _engine(opt_auto_retry=False, opt_retry_attempts=1)
    engine._last_scan_title_bytes = {0: 1_000_000}  # scan says ~1 MB
    monkeypatch.setattr(
        engine, "_wait_for_drive_ready", lambda *_a, **_k: True,
    )

    def fake_run(_cmd, _on_progress, _on_log):
        Path(tmp_path, "title_t00.mkv").write_bytes(b"x" * 10_000)  # 1%
        return False  # MakeMKV errored

    monkeypatch.setattr(engine, "_run_rip_process", fake_run)

    logs: list[str] = []
    success, failed = engine.rip_selected_titles(
        str(tmp_path), [0], on_progress=lambda _p: None, on_log=logs.append
    )

    assert success is False
    assert failed == [1]
    assert engine.last_degraded_titles == []
    assert not Path(tmp_path, "title_t00.mkv").exists(), \
        "the truncated output is removed so a retry can't double-count it"
    assert any("rejecting degraded output" in m for m in logs)


def test_degraded_output_above_floor_is_still_accepted(
    tmp_path, monkeypatch,
):
    engine = _engine(opt_auto_retry=False, opt_retry_attempts=1)
    engine._last_scan_title_bytes = {0: 10_000}  # scan says ~10 KB
    monkeypatch.setattr(
        engine, "_wait_for_drive_ready", lambda *_a, **_k: True,
    )

    def fake_run(_cmd, _on_progress, _on_log):
        Path(tmp_path, "title_t00.mkv").write_bytes(b"x" * 9_000)  # 90%
        return False

    monkeypatch.setattr(engine, "_run_rip_process", fake_run)

    success, failed = engine.rip_selected_titles(
        str(tmp_path), [0], on_progress=lambda _p: None,
        on_log=lambda _m: None,
    )

    assert success is True
    assert failed == []
    assert engine.last_degraded_titles == [1]
