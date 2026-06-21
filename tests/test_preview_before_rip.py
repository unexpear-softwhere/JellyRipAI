"""Pins for the watch-before-rip preview feature (2026-06-10).

Covers the configurable-length contract at the data/API layer (the
Qt picker controls are pinned in
``test_disc_tree_preview_controls.py``):

- The two config defaults exist with the documented values.
- ``preview_title`` accepts an optional ``preview_seconds`` override
  so the picker's length spinner can drive the sample length.

Qt-free and network-free — pure introspection.
"""

from __future__ import annotations

import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.runtime import DEFAULTS
from controller import legacy_compat


def test_preview_config_defaults_present():
    """The gate ships enabled, and the default watch length is 0 —
    meaning the FULL title rips and plays (2026-06-12: "i want it to
    be full thing not a preview")."""
    assert DEFAULTS.get("opt_offer_preview_before_rip") is True
    assert DEFAULTS.get("opt_preview_seconds") == 0


def _find_preview_title():
    """Locate the ``preview_title`` method regardless of which mixin
    class hosts it (the class name differs subtly between forks)."""
    for obj in vars(legacy_compat).values():
        if isinstance(obj, type) and "preview_title" in obj.__dict__:
            return obj.__dict__["preview_title"]
    raise AssertionError("preview_title method not found in legacy_compat")


def test_preview_title_accepts_optional_seconds_override():
    """``preview_title(title_id, preview_seconds=None)`` — the picker's
    length spinner passes its value here; None falls back to the
    configured ``opt_preview_seconds`` default."""
    sig = inspect.signature(_find_preview_title())
    assert "preview_seconds" in sig.parameters, (
        "preview_title must accept a preview_seconds override so the "
        "picker's length spinner can set the sample length"
    )
    assert sig.parameters["preview_seconds"].default is None


def test_preview_rips_to_local_temp_not_configured_temp_root(
    monkeypatch, tmp_path,
):
    """Preview clips must rip to the LOCAL system temp dir, never the
    configured temp root.  Field failure 2026-06-12: a temp root on a
    network share (``\\\\DESKTOP-…\\MediaHub\\temp``) made VLC fail to
    open the clip — VLC refuses ``file://`` MRLs that carry a remote
    host ("VLC is unable to open the MRL 'file://DESKTOP-…/…'")."""
    import threading as _threading

    from controller.controller import RipperController

    class _GUI:
        def __init__(self):
            self.statuses = []
            self.logs = []
            self.rip_thread = None

        def append_log(self, message):
            self.logs.append(message)

        def set_status(self, message):
            self.statuses.append(message)

    class _Engine:
        def __init__(self):
            # The poisonous config: temp root on a UNC network share.
            self.cfg = {
                "temp_folder": r"\\DESKTOP-FPSMMN4\MediaHub\temp",
                "opt_preview_seconds": 40,
                "opt_debug_state": False,
            }
            self.abort_event = _threading.Event()
            self.preview_dirs = []

        def rip_preview_title(self, preview_dir, _tid, _secs, _log):
            self.preview_dirs.append(str(preview_dir))
            return False  # stop the flow before any player launches

        def reset_abort(self):
            pass

    class _ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            if self._target is not None:
                self._target()

    monkeypatch.setattr(legacy_compat.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(
        legacy_compat.tempfile, "gettempdir", lambda: str(tmp_path)
    )

    engine = _Engine()
    controller = RipperController(engine, _GUI())
    monkeypatch.setattr(
        controller, "_safe_glob", lambda *_a, **_kw: [], raising=False
    )

    controller.preview_title(0)

    assert engine.preview_dirs, "rip_preview_title was never invoked"
    preview_dir = engine.preview_dirs[0]
    assert not preview_dir.startswith("\\\\"), (
        f"preview dir must never live on the UNC temp root: {preview_dir}"
    )
    assert preview_dir.startswith(str(tmp_path)), (
        f"preview dir must live under the local system temp: {preview_dir}"
    )


def test_sample_clip_deleted_after_player_closes(monkeypatch, tmp_path):
    """SAMPLE mode (positive seconds — config escape hatch only): the
    partial clip is useless afterward, so it must be removed once the
    player exits.  A cleanup thread waits on the player process; with
    the immediate-thread fake everything runs synchronously, so by
    the time ``preview_title`` returns the clip and its (now empty)
    folder must both be gone.  Full-title watches are NOT deleted —
    see ``test_full_watch_keeps_file_and_registers_for_reuse``."""
    import threading as _threading
    from types import SimpleNamespace

    from controller.controller import RipperController

    class _GUI:
        def __init__(self):
            self.statuses = []
            self.logs = []
            self.rip_thread = None

        def append_log(self, message):
            self.logs.append(str(message))

        def set_status(self, message):
            self.statuses.append(str(message))

    class _Engine:
        def __init__(self):
            self.cfg = {
                "temp_folder": r"\\DESKTOP-FPSMMN4\MediaHub\temp",
                "opt_preview_seconds": 40,
                "opt_allow_path_tool_resolution": False,
                "opt_debug_state": False,
            }
            self.abort_event = _threading.Event()
            self.preview_file = ""

        def rip_preview_title(self, preview_dir, _tid, _secs, _log):
            os.makedirs(preview_dir, exist_ok=True)
            self.preview_file = os.path.join(preview_dir, "preview.mkv")
            with open(self.preview_file, "wb") as f:
                f.write(b"x" * 1024)
            return True

        def analyze_files(self, files, _log):
            return [(files[0], 40.0, 0.0)]

        def reset_abort(self):
            pass

    class _ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            if self._target is not None:
                self._target()

    class _Player:
        def __init__(self):
            self.waited = False

        def wait(self):
            self.waited = True

    player = _Player()
    monkeypatch.setattr(legacy_compat.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(
        legacy_compat.tempfile, "gettempdir", lambda: str(tmp_path)
    )
    monkeypatch.setattr(legacy_compat._sys, "platform", "win32")
    monkeypatch.setattr(
        legacy_compat,
        "resolve_vlc",
        lambda *, allow_path_lookup=False: SimpleNamespace(
            path=r"C:\Trusted\vlc.exe", source="known location",
        ),
    )
    monkeypatch.setattr(
        legacy_compat.subprocess, "Popen", lambda *_a, **_kw: player
    )

    engine = _Engine()
    controller = RipperController(engine, _GUI())
    monkeypatch.setattr(
        controller,
        "_safe_glob",
        lambda *_a, **_kw: (
            [engine.preview_file] if engine.preview_file else []
        ),
        raising=False,
    )

    controller.preview_title(0)

    assert player.waited, "cleanup must wait for the player to exit"
    assert not os.path.exists(engine.preview_file), (
        "preview clip must be deleted once the player closes"
    )
    assert not os.path.exists(os.path.dirname(engine.preview_file)), (
        "the emptied preview folder should be removed too"
    )


def test_full_watch_keeps_file_and_registers_for_reuse(
    monkeypatch, tmp_path,
):
    """FULL-title watch (seconds=0, the default): the rip is real, so
    the file is KEPT and registered — a still-checked title is moved
    into the session instead of ripping twice (2026-06-12: "if we are
    doing the full title i dont think we need to delete it")."""
    import threading as _threading
    from types import SimpleNamespace

    from controller.controller import RipperController

    class _GUI:
        def __init__(self):
            self.statuses = []
            self.logs = []
            self.rip_thread = None

        def append_log(self, message):
            self.logs.append(str(message))

        def set_status(self, message):
            self.statuses.append(str(message))

    class _Engine:
        def __init__(self):
            self.cfg = {
                "temp_folder": r"\\DESKTOP-FPSMMN4\MediaHub\temp",
                "opt_preview_seconds": 0,  # full title (the default)
                "opt_allow_path_tool_resolution": False,
                "opt_debug_state": False,
            }
            self.abort_event = _threading.Event()
            self.preview_file = ""

        def rip_preview_title(self, preview_dir, _tid, _secs, _log):
            os.makedirs(preview_dir, exist_ok=True)
            self.preview_file = os.path.join(preview_dir, "show_t00.mkv")
            with open(self.preview_file, "wb") as f:
                f.write(b"x" * 2048)
            return True

        def analyze_files(self, files, _log):
            return [(files[0], 1440.0, 0.0)]

        def reset_abort(self):
            pass

    class _ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            if self._target is not None:
                self._target()

    class _Player:
        def __init__(self):
            self.waited = False

        def wait(self):
            self.waited = True

    player = _Player()
    monkeypatch.setattr(legacy_compat.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(
        legacy_compat.tempfile, "gettempdir", lambda: str(tmp_path)
    )
    monkeypatch.setattr(legacy_compat._sys, "platform", "win32")
    monkeypatch.setattr(
        legacy_compat,
        "resolve_vlc",
        lambda *, allow_path_lookup=False: SimpleNamespace(
            path=r"C:\Trusted\vlc.exe", source="known location",
        ),
    )
    monkeypatch.setattr(
        legacy_compat.subprocess, "Popen", lambda *_a, **_kw: player
    )

    engine = _Engine()
    controller = RipperController(engine, _GUI())
    monkeypatch.setattr(
        controller,
        "_safe_glob",
        lambda *_a, **_kw: (
            [engine.preview_file] if engine.preview_file else []
        ),
        raising=False,
    )

    controller.preview_title(0)

    assert os.path.exists(engine.preview_file), (
        "a full-title watch rip must be KEPT for reuse"
    )
    assert getattr(controller, "_watched_rips", {}) == {
        0: engine.preview_file
    }
    assert not player.waited, (
        "full-title watches must not spawn the deletion thread"
    )


def test_purge_preview_temp_sweeps_leftover_clips(monkeypatch, tmp_path):
    """Startup purge (2026-06-13): a full-title watch the user never
    continued past, or a failed/0-byte preview, lingers in local temp —
    so launch sweeps the whole preview root.  Hermetic via a fake
    gettempdir."""
    from controller.legacy_compat import purge_preview_temp

    monkeypatch.setattr(
        legacy_compat.tempfile, "gettempdir", lambda: str(tmp_path)
    )
    # Fork-agnostic: MAIN uses "JellyRip", the AI fork "JellyRipAI".
    preview_root = tmp_path / legacy_compat._PREVIEW_TEMP_SUBDIR / "preview"
    (preview_root / "t00").mkdir(parents=True)
    (preview_root / "t00" / "preview.mkv").write_bytes(b"")   # 0-byte leftover
    (preview_root / "t03").mkdir(parents=True)
    (preview_root / "t03" / "show_t03.mkv").write_bytes(b"x" * 4096)

    purge_preview_temp()

    assert not preview_root.exists(), "the whole preview root must be swept"
    # Idempotent / safe when already absent.
    purge_preview_temp()


def test_reuse_watched_rips_moves_selected_and_discards_unchecked(
    tmp_path,
):
    """``_reuse_watched_rips``: a watched title that is still selected
    moves into the session rip folder (no second rip); a watched
    title the user unchecked is deleted (their rule)."""
    import threading as _threading

    from controller.controller import RipperController

    class _GUI:
        def __init__(self):
            self.logs = []
            self.rip_thread = None

        def append_log(self, message):
            self.logs.append(str(message))

        def set_status(self, message):
            pass

    class _Engine:
        def __init__(self):
            self.cfg = {"opt_debug_state": False}
            self.abort_event = _threading.Event()

    # Two completed watch rips on disk, each in its per-title folder.
    kept = tmp_path / "watch" / "t00" / "show_t00.mkv"
    dropped = tmp_path / "watch" / "t01" / "show_t01.mkv"
    for f in (kept, dropped):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x" * 1024)

    rip_path = tmp_path / "rip"
    rip_path.mkdir()

    controller = RipperController(_Engine(), _GUI())
    controller._watched_rips = {0: str(kept), 1: str(dropped)}

    reused = controller._reuse_watched_rips([0], str(rip_path))

    assert reused == {0}
    assert (rip_path / "show_t00.mkv").exists(), (
        "the still-selected watched rip must move into the session"
    )
    assert not kept.exists()
    assert not dropped.exists(), (
        "the unchecked watched rip must be deleted"
    )
    assert getattr(controller, "_watched_rips", {}) == {}


def test_watched_reuse_survives_pre_rip_purge(tmp_path):
    """Regression (2026-06-21 data loss): a watched rip the user KEPT must
    survive the rip's pre-rip purge of the session folder.

    The old flow moved reused files into the session folder BEFORE the
    rip, and ``rip_selected_titles`` purges that folder first
    (``_purge_rip_target_files`` deletes every ``*.mkv``) — so the kept
    title was wiped, dropped from the output and the move, and its only
    copy deleted on cleanup.  The fix peeks which titles are reusable up
    front (``_watched_reuse_ids``, no move) and moves them in only AFTER
    the rip.  This pins both halves: the peek must not move, and a move
    after the purge must land the file alongside the freshly ripped one.
    """
    import os as _os
    import glob as _glob
    import threading as _threading

    from controller.controller import RipperController

    class _GUI:
        def __init__(self):
            self.logs = []

        def append_log(self, message):
            self.logs.append(str(message))

        def set_status(self, message):
            pass

    class _Engine:
        def __init__(self):
            self.cfg = {"opt_debug_state": False}
            self.abort_event = _threading.Event()

    watched = tmp_path / "watch" / "t00" / "show_t00.mkv"
    watched.parent.mkdir(parents=True, exist_ok=True)
    watched.write_bytes(b"x" * 2048)

    rip_path = tmp_path / "rip"
    rip_path.mkdir()

    controller = RipperController(_Engine(), _GUI())
    controller._watched_rips = {0: str(watched)}

    # 1) Pre-rip PEEK reports title 0 as reusable WITHOUT moving it.
    assert controller._watched_reuse_ids([0]) == {0}
    assert watched.exists(), "peek must not move the watched rip"
    assert not list(rip_path.glob("*.mkv")), "peek must not stage anything"

    # 2) The rip purges the session folder (mimics
    #    engine._purge_rip_target_files), then writes the OTHER titles.
    for f in _glob.glob(str(rip_path / "**" / "*.mkv"), recursive=True):
        _os.remove(f)
    (rip_path / "show_t01.mkv").write_bytes(b"y" * 2048)  # a ripped title

    # 3) The reuse-move runs AFTER the purge; the kept rip survives
    #    alongside the freshly ripped title.
    reused = controller._reuse_watched_rips([0], str(rip_path))
    assert reused == {0}
    outputs = sorted(p.name for p in rip_path.glob("*.mkv"))
    assert outputs == ["show_t00.mkv", "show_t01.mkv"], (
        "the kept watched rip (t00) must survive the purge and land next "
        f"to the ripped title (t01); got {outputs}"
    )
    assert not watched.exists(), "it should have moved out of preview temp"
