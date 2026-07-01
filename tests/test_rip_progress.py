"""Rip progress — size weighting + the engine→UI callback wiring.

Guards the fix for "rip progress didn't show in the app or the log": the
engine's run_job used to hand the rip an ``on_progress=lambda: None`` no-op.
"""

from __future__ import annotations

import os
import sys
import threading
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.rip_ops import _rip_dir_bytes, rip_size_poller, size_weighted_progress


# ── size weighting ─────────────────────────────────────────────────────
def test_size_weighting_advances_with_title_size():
    # title 0 is huge (4 GB), titles 1-2 tiny (1 GB each) → 6 GB total
    tb = [4000, 1000, 1000]

    # halfway through the big title → 2000 / 6000
    pct, cur, tot = size_weighted_progress(tb, 0, 50.0)
    assert (tot, cur) == (6000, 2000)
    assert round(pct, 1) == 33.3

    # starting title 1 (the 4 GB title is done) → 4000/6000 = 66.7%,
    # NOT the count-based 33% it used to show.
    pct, cur, _tot = size_weighted_progress(tb, 1, 0.0)
    assert cur == 4000 and round(pct, 1) == 66.7

    # last title complete → 100%
    pct, cur, _tot = size_weighted_progress(tb, 2, 100.0)
    assert cur == 6000 and pct == 100.0


def test_unknown_sizes_signal_count_fallback():
    pct, cur, tot = size_weighted_progress([0, 0, 0], 1, 50.0)
    assert (pct, cur, tot) == (0.0, 0, 0)  # total 0 → caller uses count weighting


# ── file-size bar: the poller that moves the bar even with no MakeMKV ticks ─
def test_rip_dir_bytes_sums_only_mkv(tmp_path):
    (tmp_path / "a.mkv").write_bytes(b"x" * 100)
    (tmp_path / "b.mkv").write_bytes(b"y" * 200)
    (tmp_path / "notes.txt").write_bytes(b"z" * 999)  # not counted
    assert _rip_dir_bytes(str(tmp_path)) == 300


def test_rip_dir_bytes_missing_dir_is_zero():
    assert _rip_dir_bytes(os.path.join("no", "such", "dir")) == 0


def test_rip_size_poller_reports_pct_and_logs(tmp_path):
    # 1500 of 3000 expected bytes on disk → the bar should read 50%.
    (tmp_path / "disc_t00.mkv").write_bytes(b"x" * 1500)
    stop, abort = threading.Event(), threading.Event()
    pcts: list = []
    logs: list = []

    def on_progress(p):
        pcts.append(p)
        stop.set()  # one poll, then unwind

    rip_size_poller(str(tmp_path), 3000, on_progress, logs.append, stop, abort)
    assert pcts == [50]
    assert logs and "(50%)" in logs[-1]  # progress also shows in the log


def test_rip_size_poller_caps_at_99(tmp_path):
    # More on disk than expected must not blow past 99 (100 is the caller's).
    (tmp_path / "disc_t00.mkv").write_bytes(b"x" * 5000)
    stop, abort = threading.Event(), threading.Event()
    pcts: list = []

    def on_progress(p):
        pcts.append(p)
        stop.set()

    rip_size_poller(str(tmp_path), 1000, on_progress, lambda _l: None, stop, abort)
    assert pcts == [99]


def test_rip_size_poller_stops_on_abort(tmp_path):
    (tmp_path / "disc_t00.mkv").write_bytes(b"x" * 1000)
    stop, abort = threading.Event(), threading.Event()
    abort.set()  # aborted before it ever polls
    pcts: list = []
    rip_size_poller(str(tmp_path), 1000, pcts.append, lambda _l: None, stop, abort)
    assert pcts == []


# ── clean log: log MakeMKV's resolved message, not its %1 format template ──
def test_msg_regex_extracts_resolved_message_even_with_commas():
    from engine.ripper_engine import _MSG_MESSAGE_RE

    line = (
        'MSG:3307,0,3,"Title #4 was added (6 cell(s), 0:22:01)",'
        '"Title #%1 was added (%2 cell(s), %3)","4","6","0:22:01"'
    )
    m = _MSG_MESSAGE_RE.match(line)
    assert m and m.group(1) == "Title #4 was added (6 cell(s), 0:22:01)"


def test_msg_regex_prefers_resolved_over_format_template():
    from engine.ripper_engine import _MSG_MESSAGE_RE

    line = 'MSG:1005,0,1,"MakeMKV v1.18.4 started","%1 started","MakeMKV v1.18.4"'
    m = _MSG_MESSAGE_RE.match(line)
    assert m.group(1) == "MakeMKV v1.18.4 started"  # not "%1 started"


# ── wiring: run_job must forward the real callback, not a no-op ─────────
def test_run_job_forwards_wired_progress_callback(monkeypatch):
    from engine.ripper_engine import RipperEngine

    eng = RipperEngine({})
    captured: dict = {}

    def fake_rip_selected(self, rip_path, title_ids, on_progress, on_log):
        captured["on_progress"] = on_progress
        return True, []

    monkeypatch.setattr(RipperEngine, "rip_selected_titles", fake_rip_selected)

    sentinel = lambda *a, **k: None  # noqa: E731
    eng._rip_progress_cb = sentinel

    job = types.SimpleNamespace(source="1,2", output="out")
    eng.run_job(job)

    # the exact wired callback reaches the rip — not a discarded lambda
    assert captured["on_progress"] is sentinel


def test_run_job_defaults_to_safe_noop_when_unwired(monkeypatch):
    from engine.ripper_engine import RipperEngine

    eng = RipperEngine({})  # nothing wired
    captured: dict = {}

    def fake_rip_selected(self, rip_path, title_ids, on_progress, on_log):
        captured["on_progress"] = on_progress
        return True, []

    monkeypatch.setattr(RipperEngine, "rip_selected_titles", fake_rip_selected)
    eng.run_job(types.SimpleNamespace(source="1", output="out"))

    # single-arg no-op — safe to call, no crash (legacy callback shape)
    captured["on_progress"](50)


def test_run_job_forwards_log_live_when_wired(monkeypatch):
    from engine.ripper_engine import RipperEngine

    eng = RipperEngine({})

    def fake_rip_selected(self, rip_path, title_ids, on_progress, on_log):
        on_log("Ripping title 1 (1/1)...")
        return True, []

    monkeypatch.setattr(RipperEngine, "rip_selected_titles", fake_rip_selected)
    live: list = []
    eng._rip_log_cb = live.append
    result = eng.run_job(types.SimpleNamespace(source="1", output="out"))

    assert "Ripping title 1 (1/1)..." in live           # forwarded live to UI
    assert "Ripping title 1 (1/1)..." in result.outputs  # still buffered too
