"""Resume-support integration tests (engine round-trip + controller wiring).

Closes the cross-cutting criterion *"Resume support"* in
[docs/workflow-stabilization-criteria.md](../docs/workflow-stabilization-criteria.md):

    When a session fails or is aborted mid-rip, the partial-session
    metadata correctly captures ``failed_titles`` /
    ``completed_titles`` / ``phase``, and the resume prompt offers
    the user the right choice.  Pinned by
    ``tests/test_session_recovery.py`` for the data layer; the
    workflow integration is what stabilizes.

The data-layer functions (``mark_session_failed``,
``select_resumable_session``, ``build_resume_prompt``,
``restore_selected_titles``, ``map_title_ids_to_analyzed_indices``)
are already covered in ``test_session_recovery.py`` (5 tests).  This
file fills the **integration** gap:

1. **Engine round-trip** — ``write_temp_metadata`` /
   ``update_temp_metadata`` / ``read_temp_metadata`` /
   ``find_resumable_sessions`` actually agree on the metadata shape
   and discoverability rules.
2. **Resume-detection rules** — ``find_resumable_sessions`` skips
   complete/organized sessions, returns partial/ripping ones, and
   handles missing metadata defensively.
3. **Controller wiring** — ``_preserve_partial_session`` writes the
   right metadata payload to ``engine.update_temp_metadata`` so the
   downstream resume picker has the data it needs.

Behavior-first.  No GUI/Tk touches, no real subprocess.  Survives
the planned PySide6 migration per decision #5.
"""

from __future__ import annotations

import json
import os
import sys

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
# Engine round-trip: write_temp_metadata → read_temp_metadata
# --------------------------------------------------------------------------


def test_write_temp_metadata_writes_full_shape(tmp_path):
    """``write_temp_metadata`` writes the contract shape:
    title/year/media_type/season/selected_titles/episode_names/
    episode_numbers/completed_titles/phase/dest_folder/disc_number/
    timestamp/file_count/status.  Missing any of these breaks the
    resume picker."""
    engine = _engine()
    rip_path = tmp_path / "session"
    rip_path.mkdir()

    engine.write_temp_metadata(
        str(rip_path),
        title="Movie",
        disc_number=1,
        media_type="movie",
        year="2024",
        selected_titles=[0, 1],
        completed_titles=[0],
        phase="ripping",
        dest_folder=str(tmp_path / "movies" / "Movie (2024)"),
    )

    meta_file = rip_path / "_rip_meta.json"
    assert meta_file.exists()

    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    assert meta["title"] == "Movie"
    assert meta["year"] == "2024"
    assert meta["media_type"] == "movie"
    assert meta["selected_titles"] == [0, 1]
    assert meta["completed_titles"] == [0]
    assert meta["phase"] == "ripping"
    assert meta["disc_number"] == 1
    assert meta["status"] == "ripping"
    assert "timestamp" in meta
    assert meta["file_count"] == 0  # nothing ripped yet


def test_update_temp_metadata_preserves_existing_fields(tmp_path):
    """``update_temp_metadata`` is a partial-update — fields not
    passed in remain at their previous values.  Pins the merge
    semantics at ``ripper_engine.py:633-636``."""
    engine = _engine()
    rip_path = tmp_path / "session"
    rip_path.mkdir()

    engine.write_temp_metadata(
        str(rip_path),
        title="Movie",
        disc_number=1,
        media_type="movie",
        year="2024",
        selected_titles=[0, 1, 2],
    )

    # Update only status — title/year/selected_titles must persist.
    engine.update_temp_metadata(str(rip_path), status="partial")

    meta = engine.read_temp_metadata(str(rip_path))
    assert meta is not None
    assert meta["status"] == "partial"
    assert meta["title"] == "Movie", "title must survive a partial update"
    assert meta["year"] == "2024"
    assert meta["selected_titles"] == [0, 1, 2]


def test_read_temp_metadata_returns_none_on_missing_file(tmp_path):
    """``read_temp_metadata`` returns None when no metadata file is
    present.  Pins the defensive failure path so callers don't have
    to wrap every read in try/except."""
    engine = _engine()
    empty = tmp_path / "no_meta_here"
    empty.mkdir()

    assert engine.read_temp_metadata(str(empty)) is None


def test_update_temp_metadata_recounts_mkv_files(tmp_path):
    """``update_temp_metadata`` recounts ``.mkv`` files on every call.
    Pins the file-count refresh at ``ripper_engine.py:624-632``."""
    engine = _engine()
    rip_path = tmp_path / "session"
    rip_path.mkdir()

    engine.write_temp_metadata(str(rip_path), title="X", disc_number=1)
    # write_temp_metadata sets file_count=0 directly; verify
    assert engine.read_temp_metadata(str(rip_path))["file_count"] == 0

    # Now drop two MKVs into the folder and call update.
    (rip_path / "title_t00.mkv").write_text("a")
    (rip_path / "title_t01.mkv").write_text("b")

    engine.update_temp_metadata(str(rip_path), status="ripping")

    meta = engine.read_temp_metadata(str(rip_path))
    assert meta is not None
    assert meta["file_count"] == 2


# --------------------------------------------------------------------------
# Resume-detection rules: find_resumable_sessions
# --------------------------------------------------------------------------


def test_find_resumable_sessions_returns_partial_session(tmp_path):
    """A session written with ``phase="ripping"`` or any phase other
    than ``complete``/``organized`` is discoverable.  Pins
    ``ripper_engine.py:546`` filter."""
    engine = _engine()
    temp_root = tmp_path / "temp"
    temp_root.mkdir()

    session = temp_root / "Disc_2026-05-03"
    session.mkdir()
    engine.write_temp_metadata(
        str(session), title="Movie", disc_number=1,
        media_type="movie", phase="ripping",
    )

    found = engine.find_resumable_sessions(str(temp_root))

    assert len(found) == 1
    full, name, meta, _mkv_count = found[0]
    assert os.path.normpath(full) == os.path.normpath(str(session))
    assert name == "Disc_2026-05-03"
    assert meta["title"] == "Movie"


def test_find_resumable_sessions_skips_complete_and_organized(tmp_path):
    """Sessions marked ``phase="complete"`` or ``phase="organized"``
    are filtered out — they're already done.  Pins the
    ``not in {"complete", "organized"}`` predicate."""
    engine = _engine()
    temp_root = tmp_path / "temp"
    temp_root.mkdir()

    completed = temp_root / "Done_Disc"
    completed.mkdir()
    engine.write_temp_metadata(
        str(completed), title="Done", disc_number=1, phase="complete",
    )
    organized = temp_root / "Organized_Disc"
    organized.mkdir()
    engine.write_temp_metadata(
        str(organized), title="Org", disc_number=1, phase="organized",
    )
    pending = temp_root / "Pending_Disc"
    pending.mkdir()
    engine.write_temp_metadata(
        str(pending), title="Pending", disc_number=1, phase="ripping",
    )

    found = engine.find_resumable_sessions(str(temp_root))
    titles = sorted(meta["title"] for _, _, meta, _ in found)

    assert titles == ["Pending"], (
        f"only 'Pending' should be returned; got {titles}"
    )


def test_find_resumable_sessions_skips_folders_without_metadata(tmp_path):
    """Folders without a `_rip_meta.json` are not resumable — they
    might be user-created or stale.  Pins the defensive
    ``read_temp_metadata`` fall-through."""
    engine = _engine()
    temp_root = tmp_path / "temp"
    temp_root.mkdir()

    bare = temp_root / "no_metadata"
    bare.mkdir()
    (bare / "random.mkv").write_text("a")

    found = engine.find_resumable_sessions(str(temp_root))

    assert found == []


def test_find_resumable_sessions_returns_empty_on_missing_temp_root(
    tmp_path,
):
    """Non-existent temp_root → empty list, no exception.  Pins the
    ``if not os.path.isdir(temp_root): return`` guard."""
    engine = _engine()
    nonexistent = tmp_path / "definitely_gone"

    found = engine.find_resumable_sessions(str(nonexistent))

    assert found == []


def test_find_resumable_sessions_counts_mkv_files(tmp_path):
    """The fourth tuple element is the .mkv file count (used by the
    resume picker to show progress).  Pins the os.walk count."""
    engine = _engine()
    temp_root = tmp_path / "temp"
    temp_root.mkdir()

    session = temp_root / "session"
    session.mkdir()
    engine.write_temp_metadata(
        str(session), title="X", disc_number=1, phase="ripping",
    )
    (session / "a.mkv").write_text("a")
    (session / "b.mkv").write_text("b")
    (session / "subdir").mkdir()
    (session / "subdir" / "c.mkv").write_text("c")
    (session / "not_an_mkv.txt").write_text("ignored")

    found = engine.find_resumable_sessions(str(temp_root))

    assert len(found) == 1
    _full, _name, _meta, mkv_count = found[0]
    assert mkv_count == 3, (
        f"recursive count of .mkv files should be 3; got {mkv_count}"
    )


# --------------------------------------------------------------------------
# Controller wiring: _preserve_partial_session writes the right metadata
# --------------------------------------------------------------------------


def test_preserve_partial_session_writes_partial_status_and_phase(tmp_path):
    """``_preserve_partial_session`` calls ``engine.update_temp_metadata``
    with ``status="partial"`` and ``phase="partial"`` plus all the
    resume-relevant fields.  Pins the integration handshake between
    the controller and the data layer."""
    controller, engine = _controller_with_engine()

    rip_path = tmp_path / "session"
    rip_path.mkdir()
    engine.write_temp_metadata(
        str(rip_path), title="Movie", disc_number=1,
        media_type="movie", phase="ripping",
    )

    controller._preserve_partial_session(
        str(rip_path),
        title="Movie",
        year="2024",
        media_type="movie",
        season=None,
        selected_titles=[0, 1, 2],
        completed_titles=[0],
        failed_titles=[1, 2],
        dest_folder=str(tmp_path / "movies" / "Movie (2024)"),
    )

    meta = engine.read_temp_metadata(str(rip_path))
    assert meta is not None
    assert meta["status"] == "partial"
    assert meta["phase"] == "partial"
    assert meta["title"] == "Movie"
    assert meta["year"] == "2024"
    assert meta["selected_titles"] == [0, 1, 2]
    assert meta["completed_titles"] == [0]
    assert meta["failed_titles"] == [1, 2]
    assert "dest_folder" in meta


def test_preserve_partial_session_normalizes_failed_title_strings(tmp_path):
    """``failed_titles`` may arrive as strings (e.g., ``"1"``) when
    surfaced from rip subprocess output.  ``_preserve_partial_session``
    normalizes them to ints.  Pins the int-coercion at
    ``legacy_compat.py:205-209``."""
    controller, engine = _controller_with_engine()

    rip_path = tmp_path / "session"
    rip_path.mkdir()
    engine.write_temp_metadata(
        str(rip_path), title="X", disc_number=1, phase="ripping",
    )

    controller._preserve_partial_session(
        str(rip_path),
        title="X", year=None, media_type="movie",
        failed_titles=["1", "2", 3],  # mixed types
    )

    meta = engine.read_temp_metadata(str(rip_path))
    assert meta is not None
    assert meta["failed_titles"] == [1, 2, 3], (
        "string title IDs must be normalized to int"
    )


def test_preserve_partial_session_defaults_empty_lists(tmp_path):
    """``None`` inputs for selected/completed/failed titles default
    to empty lists (not ``None`` in JSON).  Pins the
    ``list(selected_titles or [])`` pattern — downstream readers
    expect lists, not nulls."""
    controller, engine = _controller_with_engine()

    rip_path = tmp_path / "session"
    rip_path.mkdir()
    engine.write_temp_metadata(
        str(rip_path), title="X", disc_number=1, phase="ripping",
    )

    controller._preserve_partial_session(
        str(rip_path), title="X", year=None, media_type="movie",
        # All None — defaults must kick in
    )

    meta = engine.read_temp_metadata(str(rip_path))
    assert meta is not None
    assert meta["selected_titles"] == []
    assert meta["completed_titles"] == []
    assert meta["failed_titles"] == []


def test_preserve_partial_session_logs_user_visible_message(tmp_path):
    """``_preserve_partial_session`` logs ``"Partial session preserved
    at: <rip_path>"`` so the user knows the data is there for resume.
    Pins the user-visible feedback at ``legacy_compat.py:223``."""
    controller, engine = _controller_with_engine()

    rip_path = tmp_path / "session"
    rip_path.mkdir()
    engine.write_temp_metadata(
        str(rip_path), title="X", disc_number=1, phase="ripping",
    )

    controller._preserve_partial_session(
        str(rip_path), title="X", year=None, media_type="movie",
    )

    assert any(
        "Partial session preserved at:" in m
        and str(rip_path) in m
        for m in controller.gui.messages
    ), (
        "expected user-visible 'Partial session preserved' log "
        "with the rip path"
    )


# --------------------------------------------------------------------------
# Round-trip: workflow writes partial → find_resumable_sessions discovers
# --------------------------------------------------------------------------


def test_partial_session_written_by_controller_is_discoverable(tmp_path):
    """End-to-end: controller writes a partial session → engine's
    ``find_resumable_sessions`` discovers it with the right metadata
    intact.  Pins the round-trip integrity that resume support
    depends on."""
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    rip_path = temp_root / "Disc_2026-05-03"
    rip_path.mkdir()

    # Start a session — initial metadata written.
    engine.write_temp_metadata(
        str(rip_path), title="Movie", disc_number=1,
        media_type="movie", year="2024", phase="ripping",
        selected_titles=[0, 1, 2],
    )

    # Partway through, two titles fail.  Controller preserves partial.
    controller._preserve_partial_session(
        str(rip_path),
        title="Movie", year="2024", media_type="movie",
        selected_titles=[0, 1, 2],
        completed_titles=[0],
        failed_titles=[1, 2],
        dest_folder=str(tmp_path / "movies" / "Movie (2024)"),
    )

    # Next launch: find_resumable_sessions discovers the partial.
    found = engine.find_resumable_sessions(str(temp_root))

    assert len(found) == 1, "the partial session must be discoverable"
    full, name, meta, _mkv_count = found[0]
    assert meta["title"] == "Movie"
    assert meta["phase"] == "partial"
    assert meta["status"] == "partial"
    assert meta["selected_titles"] == [0, 1, 2]
    assert meta["completed_titles"] == [0]
    assert meta["failed_titles"] == [1, 2]


# --------------------------------------------------------------------------
# delete_temp_metadata closes out a session cleanly
# --------------------------------------------------------------------------


def test_delete_temp_metadata_removes_rip_meta(tmp_path):
    """After a successful organize, ``delete_temp_metadata`` removes
    the ``_rip_meta.json`` sidecar so the session no longer shows
    up in the resume picker.  Pins the cleanup contract used by
    ``_cleanup_success_session_metadata``."""
    engine = _engine()
    rip_path = tmp_path / "session"
    rip_path.mkdir()

    engine.write_temp_metadata(
        str(rip_path), title="X", disc_number=1, phase="complete",
    )
    meta_file = rip_path / "_rip_meta.json"
    assert meta_file.exists()

    engine.delete_temp_metadata(str(rip_path), on_log=lambda _: None)

    assert not meta_file.exists(), (
        "_rip_meta.json must be removed by delete_temp_metadata"
    )
