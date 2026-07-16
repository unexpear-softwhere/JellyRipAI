"""Picker thumbnails — background frame sampling for the disc picker.

The "Select Titles to Rip" picker shows an Explorer-style video frame
beside each title.  Titles aren't files yet at picker time, so frames
come from quiet MakeMKV mini-rips (engine ``rip_thumbnail_sample``),
cached per disc+title; the dialog worker streams them in and stops —
freeing the drive — the moment the user answers the dialog.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── engine: mini-rip command + stop conditions ──────────────────────────
class _FakeSampleEngine:
    """Engine double for rip_thumbnail_sample's collaborator surface."""

    cfg = {"opt_makemkv_global_args": "", "opt_makemkv_rip_args": ""}

    def __init__(self, drive_ready=True):
        self.captured: dict = {}
        self._drive_ready = drive_ready
        self.probes = 0

    def _get_makemkvcon(self):
        return "makemkvcon.exe"

    def get_disc_target(self):
        return "disc:0"

    def _wait_for_drive_ready(self, _on_log, *, context):
        self.probes += 1
        self.captured["probe_context"] = context
        return self._drive_ready

    def _run_sample_process(self, cmd, watch_dir, **kw):
        self.captured["cmd"] = cmd
        self.captured["watch_dir"] = watch_dir
        self.captured.update(kw)
        return True


def test_rip_thumbnail_sample_builds_quiet_robot_cmd():
    from engine.rip_ops import rip_thumbnail_sample

    eng = _FakeSampleEngine()
    out_dir = os.path.join(
        os.environ.get("TEMP", "."), "thumb_sample_test_dir"
    )
    ok = rip_thumbnail_sample(
        eng, out_dir, 7, min_bytes=123, max_seconds=45,
    )
    assert ok is True
    cmd = eng.captured["cmd"]
    # Robot mode, right title, right output dir.
    assert "-r" in cmd and "mkv" in cmd and "7" in cmd
    assert eng.captured["watch_dir"] == out_dir
    assert eng.captured["min_bytes"] == 123
    assert eng.captured["max_seconds"] == 45
    assert eng.probes == 1  # drive settles before every launch
    assert os.path.isdir(out_dir)  # created for MakeMKV
    os.rmdir(out_dir)


def test_rip_thumbnail_sample_skips_when_drive_not_ready():
    """A drive still settling from the previous sample's kill must skip
    the title (to be retried) instead of launching a doomed MakeMKV —
    the root cause of the spotty-thumbnails field failure."""
    from engine.rip_ops import rip_thumbnail_sample

    eng = _FakeSampleEngine(drive_ready=False)
    ok = rip_thumbnail_sample(
        eng, "unused_dir", 3, min_bytes=1, max_seconds=1,
    )
    assert ok is False
    assert "cmd" not in eng.captured  # MakeMKV never launched


def test_run_sample_process_stops_when_bytes_exist(tmp_path):
    """A long-running process is terminated as soon as the watch dir
    holds enough MKV bytes — the loop must not wait out max_seconds."""
    from engine.ripper_engine import RipperEngine

    eng = RipperEngine({})
    (tmp_path / "disc_t00.mkv").write_bytes(b"x" * 2048)

    cmd = [sys.executable, "-c", "import time; time.sleep(60)"]
    start = time.time()
    ok = eng._run_sample_process(
        cmd, str(tmp_path), min_bytes=1024, max_seconds=60,
    )
    elapsed = time.time() - start
    assert ok is True
    assert elapsed < 30  # killed on bytes, not the 60s cap


def test_run_sample_process_cancel_event_stops_it(tmp_path):
    from engine.ripper_engine import RipperEngine

    eng = RipperEngine({})
    cancel = threading.Event()
    cancel.set()  # cancelled before it produces anything

    cmd = [sys.executable, "-c", "import time; time.sleep(60)"]
    start = time.time()
    ok = eng._run_sample_process(
        cmd, str(tmp_path), min_bytes=10**9, max_seconds=60,
        cancel_event=cancel,
    )
    assert time.time() - start < 30
    assert ok is False  # nothing was ripped


# ── controller: cache + lock behavior ───────────────────────────────────
def _make_controller(tmp_path, monkeypatch):
    """Minimal LegacyControllerMixin host with a fake engine."""
    from controller.legacy_compat import LegacyControllerMixin

    class Host(LegacyControllerMixin):
        def __init__(self):
            self.engine = types.SimpleNamespace(
                last_disc_info={"title": "TEST_DISC"},
                abort_event=threading.Event(),
                rip_thumbnail_sample=self._no_sample,
            )
            self._preview_lock = threading.Lock()

        def _no_sample(self, *a, **k):
            raise AssertionError("drive should not be touched")

        def log(self, *_a):  # pragma: no cover - silence
            pass

    # Redirect the thumbs root into the test's temp dir.
    import controller.legacy_compat as lc
    monkeypatch.setattr(
        lc, "_thumbs_root", lambda: str(tmp_path / "thumbs")
    )
    return Host()


def test_thumbnail_title_returns_cached_jpg_without_drive(
    tmp_path, monkeypatch
):
    host = _make_controller(tmp_path, monkeypatch)
    jpg = host._thumb_cache_path(4)
    os.makedirs(os.path.dirname(jpg), exist_ok=True)
    with open(jpg, "wb") as f:
        f.write(b"\xff\xd8fakejpg")

    got = host.thumbnail_title(4)
    assert got == jpg  # cache hit; fake engine would raise if touched


def test_thumbnail_title_skips_when_preview_lock_held(
    tmp_path, monkeypatch
):
    host = _make_controller(tmp_path, monkeypatch)
    host._preview_lock.acquire()  # a Watch rip owns the drive
    try:
        assert host.thumbnail_title(2) is None
    finally:
        host._preview_lock.release()


def test_thumb_cache_path_sanitizes_disc_label(tmp_path, monkeypatch):
    host = _make_controller(tmp_path, monkeypatch)
    host.engine.last_disc_info = {"title": 'BAD/label: "quoted"?'}
    path = host._thumb_cache_path(3)
    leaf = os.path.basename(os.path.dirname(path))
    assert all(c.isalnum() or c in "_.-" for c in leaf)
    assert os.path.basename(path) == "t03.jpg"


# ── pre-generation: thumbnails load as part of the scan step ────────────
def _pregen_host(tmp_path, monkeypatch):
    host = _make_controller(tmp_path, monkeypatch)
    host.logged = []
    host.log = host.logged.append
    host.gui = types.SimpleNamespace(set_status=lambda *_a: None)
    host.sampled = []
    # Record which titles get sampled; simulate success by writing the
    # cache file (that's thumbnail_title's success contract).
    def fake_thumb(tid, cancel_event=None):
        host.sampled.append(int(tid))
        jpg = host._thumb_cache_path(int(tid))
        os.makedirs(os.path.dirname(jpg), exist_ok=True)
        with open(jpg, "wb") as f:
            f.write(b"\xff\xd8jpg")
        return jpg
    host.thumbnail_title = fake_thumb
    return host


def test_pregenerate_samples_every_missing_title(tmp_path, monkeypatch):
    host = _pregen_host(tmp_path, monkeypatch)
    titles = [{"id": 0}, {"id": 2}, {"id": 5}]

    host.pregenerate_picker_thumbnails(titles)

    assert host.sampled == [0, 2, 5]
    assert any("all 3 in place" in ln for ln in host.logged)


def test_pregenerate_retries_transient_failures(tmp_path, monkeypatch):
    """A title whose first sample fails (drive settling) is retried on
    the next pass — a single spotty pass must not be the final word."""
    host = _pregen_host(tmp_path, monkeypatch)
    real_thumb = host.thumbnail_title
    fail_once = {2}

    def flaky(tid, cancel_event=None):
        if int(tid) in fail_once:
            fail_once.discard(int(tid))
            host.sampled.append(int(tid))
            return None  # transient failure
        return real_thumb(tid, cancel_event)

    host.thumbnail_title = flaky
    host.pregenerate_picker_thumbnails([{"id": 1}, {"id": 2}])

    # Title 2 attempted twice (fail, then success on the retry pass).
    assert host.sampled == [1, 2, 2]
    assert any("retrying 1 missed" in ln for ln in host.logged)
    assert any("all 2 in place" in ln for ln in host.logged)


def test_pregenerate_reports_unsampleable_titles(tmp_path, monkeypatch):
    """A title that can never sample is reported plainly (with its
    human 1-based number) instead of the picker just opening with a
    silent hole."""
    host = _pregen_host(tmp_path, monkeypatch)
    real_thumb = host.thumbnail_title

    def broken_title_3(tid, cancel_event=None):
        if int(tid) == 2:  # title id 2 = human "title 3"
            host.sampled.append(2)
            return None
        return real_thumb(tid, cancel_event)

    host.thumbnail_title = broken_title_3
    host.pregenerate_picker_thumbnails([{"id": 1}, {"id": 2}])

    assert host.sampled.count(2) == 3  # tried on every pass
    assert any(
        "title(s) 3 couldn't be sampled" in ln for ln in host.logged
    )


def test_pregenerate_skips_cached_titles(tmp_path, monkeypatch):
    host = _pregen_host(tmp_path, monkeypatch)
    # Title 1 already cached from an earlier pass.
    jpg = host._thumb_cache_path(1)
    os.makedirs(os.path.dirname(jpg), exist_ok=True)
    with open(jpg, "wb") as f:
        f.write(b"\xff\xd8jpg")

    host.pregenerate_picker_thumbnails([{"id": 1}, {"id": 4}])
    assert host.sampled == [4]  # only the miss is sampled

    host.sampled.clear()
    host.pregenerate_picker_thumbnails([{"id": 1}, {"id": 4}])
    assert host.sampled == []  # second pass: everything cached
    assert any("all cached" in ln for ln in host.logged)


def test_pregenerate_stops_on_session_abort(tmp_path, monkeypatch):
    host = _pregen_host(tmp_path, monkeypatch)
    host.engine.abort_event.set()

    host.pregenerate_picker_thumbnails([{"id": 0}, {"id": 1}])

    assert host.sampled == []  # aborted before sampling anything
    assert any("aborted" in ln for ln in host.logged)


# ── cleanup: nothing accumulates across runs ────────────────────────────
def test_purge_thumbs_temp_sweeps_cache_and_stray_samples(
    tmp_path, monkeypatch
):
    import controller.legacy_compat as lc

    root = tmp_path / "thumbs"
    monkeypatch.setattr(lc, "_thumbs_root", lambda: str(root))
    # A cached disc folder + a stray mini-rip sample dir a hard kill
    # left behind — both must go at startup.
    (root / "SOME_DISC").mkdir(parents=True)
    (root / "SOME_DISC" / "t00.jpg").write_bytes(b"\xff\xd8x")
    (root / "sample_t03_abc").mkdir()
    (root / "sample_t03_abc" / "disc_t03.mkv").write_bytes(b"x" * 64)

    lc.purge_thumbs_temp()

    assert not root.exists()


def test_purge_thumbs_temp_survives_missing_root(tmp_path, monkeypatch):
    import controller.legacy_compat as lc

    monkeypatch.setattr(
        lc, "_thumbs_root", lambda: str(tmp_path / "never_made")
    )
    lc.purge_thumbs_temp()  # must not raise


# ── dialog: opens fully populated from a pre-resolved map ───────────────
def _titles(n=3):
    return [
        {"id": i, "name": f"Title {i + 1}", "duration": "0:22:00",
         "size": "0.9 GB", "chapters": 6}
        for i in range(n)
    ]


def _make_jpg(tmp_path) -> str:
    from PySide6.QtGui import QImage

    out = str(tmp_path / "frame.jpg")
    img = QImage(8, 8, QImage.Format.Format_RGB32)
    img.fill(0xFF00FF00)
    img.save(out, "JPG")
    return out


def test_dialog_opens_with_all_thumbnails_in_place(qtbot, tmp_path):
    """Icons come from a pre-resolved map and are set while rows are
    built — nothing loads in after the window appears."""
    from gui_qt.dialogs.disc_tree import _COL_TITLE, _DiscTreeDialog

    jpg = _make_jpg(tmp_path)
    dlg = _DiscTreeDialog(
        _titles(3), True, thumbnails={0: jpg, 1: jpg, 2: jpg},
    )
    qtbot.addWidget(dlg)

    # Immediately after construction — no event loop, no waiting.
    for item in dlg._items_by_id.values():
        assert not item.icon(_COL_TITLE).isNull()
        assert item.sizeHint(_COL_TITLE).height() >= 72


def test_dialog_missing_thumbnail_leaves_row_imageless(qtbot, tmp_path):
    from gui_qt.dialogs.disc_tree import _COL_TITLE, _DiscTreeDialog

    jpg = _make_jpg(tmp_path)
    dlg = _DiscTreeDialog(_titles(3), True, thumbnails={1: jpg})
    qtbot.addWidget(dlg)

    assert dlg._items_by_id["0"].icon(_COL_TITLE).isNull()
    assert not dlg._items_by_id["1"].icon(_COL_TITLE).isNull()
    assert dlg._items_by_id["2"].icon(_COL_TITLE).isNull()
    # Rows still sized uniformly for the thumbnail column.
    assert (
        dlg._items_by_id["0"].sizeHint(_COL_TITLE).height() >= 72
    )


def test_dialog_without_thumbnails_keeps_default_rows(qtbot):
    from gui_qt.dialogs.disc_tree import _COL_TITLE, _DiscTreeDialog

    dlg = _DiscTreeDialog(_titles(2), True)  # no thumbnails wired
    qtbot.addWidget(dlg)
    first = next(iter(dlg._items_by_id.values()))
    assert first.sizeHint(_COL_TITLE).height() <= 0  # untouched default


# ── ordering + the picker hand-off map ──────────────────────────────────
def test_pregenerate_samples_in_title_order(tmp_path, monkeypatch):
    """disc_titles arrive in the scan's RANKED order (best first); the
    log must still read title 1, 2, 3… — the order the picker shows."""
    host = _pregen_host(tmp_path, monkeypatch)
    ranked = [{"id": 4}, {"id": 5}, {"id": 3}, {"id": 0}, {"id": 1}]

    host.pregenerate_picker_thumbnails(ranked)

    assert host.sampled == [0, 1, 3, 4, 5]


def test_collect_picker_thumbnails_returns_only_cached(
    tmp_path, monkeypatch
):
    host = _make_controller(tmp_path, monkeypatch)
    jpg = host._thumb_cache_path(2)
    os.makedirs(os.path.dirname(jpg), exist_ok=True)
    with open(jpg, "wb") as f:
        f.write(b"\xff\xd8jpg")

    got = host.collect_picker_thumbnails([{"id": 1}, {"id": 2}])
    assert got == {2: jpg}  # only the cached title; no drive access
