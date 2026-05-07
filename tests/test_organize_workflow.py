"""Behavior-first tests for the Organize Existing MKVs workflow.

Closes the test-coverage gap called out in
``docs/workflow-stabilization-criteria.md`` Section 4: until this file
landed, the only ``run_organize`` test in the project was a single
narrow assertion in ``test_behavior_guards.py`` covering only session-
metadata cleanup.  These tests pin the rest of ``run_organize``'s
contract: cancellation paths, empty-source handling, media-type loop,
path-overrides cancellation, analyze_files-empty handling, abort
propagation, the source-under-temp-root safety property for
``opt_auto_delete_temp``, and the recursive-vs-non-recursive glob
pattern.

Behavior-first: no GUI/Tk touches, no filesystem operations beyond
``tmp_path``.  All tests survive the planned PySide6 migration per
decision #5 in ``docs/pyside6-migration-plan.md``.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from controller.controller import RipperController
from engine.ripper_engine import RipperEngine


class _OrganizeGUI:
    """Minimal GUI stub for ``run_organize`` that records every call.

    Scripted ``ask_input`` answers are consumed from an iterator; if the
    workflow asks for more input than was scripted, ``StopIteration``
    surfaces immediately so the test fails loudly rather than hanging.
    Deliberately does NOT define ``ask_directory`` so the controller
    falls through to the ``ask_input`` branch (see
    controller.py:2056).
    """

    def __init__(self, scripted_inputs=(), ask_yesno_returns=False):
        self._inputs = iter(list(scripted_inputs))
        self.messages: list[str] = []
        self.shown_info: list[tuple[str, str]] = []
        self.shown_errors: list[tuple[str, str]] = []
        self._ask_yesno_returns = ask_yesno_returns

    def append_log(self, msg):
        self.messages.append(msg)

    def set_status(self, _status):
        pass

    def set_progress(self, _value):
        pass

    def start_indeterminate(self):
        pass

    def stop_indeterminate(self):
        pass

    def ask_input(self, _label, _prompt, default_value=""):
        return next(self._inputs)

    def ask_yesno(self, _prompt):
        return self._ask_yesno_returns

    def show_info(self, title, msg):
        self.shown_info.append((title, msg))

    def show_error(self, title, msg):
        self.shown_errors.append((title, msg))


def _engine_cfg(**overrides):
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


def _build_controller(gui, cfg=None):
    engine = RipperEngine(cfg or _engine_cfg())
    return RipperController(engine, gui), engine


def _patch_session_helpers(controller, monkeypatch):
    """Neutralize finalization helpers so tests don't write logs."""
    monkeypatch.setattr(controller, "write_session_summary", lambda: None)
    monkeypatch.setattr(controller, "flush_log", lambda: None)


def test_organize_movie_happy_path_creates_folder_and_deletes_temp(
    tmp_path, monkeypatch
):
    """Happy path: source under temp_root + auto_delete_temp=True →
    movie folder + Extras folder created, temp source rmtreed,
    Done dialog shown."""
    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    source = temp_root / "Disc_2026-05-03_10-00-00"
    source.mkdir(parents=True)
    movies_root.mkdir()

    mkv = source / "title_t00.mkv"
    mkv.write_text("data")

    gui = _OrganizeGUI(scripted_inputs=[
        str(source),       # folder pick
        "m",               # media type
        "Chosen Movie",    # title
        "",                # metadata id
        "2024",            # year
    ])
    controller, engine = _build_controller(gui, _engine_cfg(
        temp_folder=str(temp_root),
        movies_folder=str(movies_root),
        opt_auto_delete_temp=True,
        opt_auto_delete_session_metadata=False,
    ))

    monkeypatch.setattr(
        controller, "_prompt_run_path_overrides", lambda _fields: {}
    )
    monkeypatch.setattr(
        engine, "analyze_files",
        lambda *_args, **_kwargs: [(str(mkv), 7200.0, 4000.0)],
    )
    monkeypatch.setattr(
        controller, "_select_and_move",
        lambda *_args, **_kwargs: True,
    )
    _patch_session_helpers(controller, monkeypatch)

    controller.run_organize()

    expected_folder = movies_root / "Chosen Movie (2024)"
    assert expected_folder.exists(), \
        "movie folder should be created under movies_root"
    assert (expected_folder / "Extras").exists(), \
        "Extras subfolder should be created"
    assert not source.exists(), \
        "source folder under temp_root should be auto-deleted"
    assert ("Done", "Organize complete!") in gui.shown_info


def test_organize_tv_happy_path_creates_season_and_extras_folders(
    tmp_path, monkeypatch
):
    """TV happy path: season folder + Extras subfolder created with
    correct ``Season NN`` zero-padded naming."""
    temp_root = tmp_path / "temp"
    tv_root = tmp_path / "tv"
    source = temp_root / "Disc_2026-05-03_10-00-00"
    source.mkdir(parents=True)
    tv_root.mkdir()

    mkv = source / "title_t00.mkv"
    mkv.write_text("data")

    gui = _OrganizeGUI(scripted_inputs=[
        str(source),       # folder pick
        "t",               # media type
        "Chosen Show",     # title
        "",                # metadata id
        "1",               # season number
    ])
    controller, engine = _build_controller(gui, _engine_cfg(
        temp_folder=str(temp_root),
        tv_folder=str(tv_root),
        opt_auto_delete_temp=False,  # keep source so test is robust
        opt_auto_delete_session_metadata=False,
    ))

    monkeypatch.setattr(
        controller, "_prompt_run_path_overrides", lambda _fields: {}
    )
    monkeypatch.setattr(
        engine, "analyze_files",
        lambda *_args, **_kwargs: [(str(mkv), 7200.0, 4000.0)],
    )
    monkeypatch.setattr(
        controller, "_select_and_move",
        lambda *_args, **_kwargs: True,
    )
    _patch_session_helpers(controller, monkeypatch)

    controller.run_organize()

    expected_show = tv_root / "Chosen Show"
    expected_season = expected_show / "Season 01"
    assert expected_season.exists(), \
        "Season 01 folder should be created (zero-padded)"
    assert (expected_season / "Extras").exists(), \
        "Extras subfolder should be created inside the season folder"


def test_organize_folder_selection_cancelled_returns_early(
    tmp_path, monkeypatch
):
    """Empty folder_path → 'Folder selection cancelled' log,
    no media-type prompt fires, no Done dialog."""
    gui = _OrganizeGUI(scripted_inputs=[""])  # cancelled folder pick
    controller, _engine = _build_controller(gui, _engine_cfg(
        temp_folder=str(tmp_path),
        movies_folder=str(tmp_path / "movies"),
    ))

    controller.run_organize()

    assert any(
        "Folder selection cancelled" in m for m in gui.messages
    ), "expected friendly cancellation log"
    assert gui.shown_info == [], \
        "Done dialog should NOT fire when folder pick is cancelled"


def test_organize_no_mkv_files_returns_early(tmp_path, monkeypatch):
    """Source folder with no .mkv files → 'No .mkv files found' log,
    no folder is created on disk, no Done dialog."""
    source = tmp_path / "empty_source"
    source.mkdir()

    gui = _OrganizeGUI(
        scripted_inputs=[str(source)],
        ask_yesno_returns=False,  # don't recurse
    )
    controller, _engine = _build_controller(gui, _engine_cfg(
        temp_folder=str(tmp_path),
        movies_folder=str(tmp_path / "movies"),
    ))

    controller.run_organize()

    assert any(
        "No .mkv files found" in m for m in gui.messages
    ), "expected friendly empty-source log"
    assert not (tmp_path / "movies").exists(), \
        "no movies_root folder should be auto-created"
    assert gui.shown_info == [], \
        "Done dialog should NOT fire when source is empty"


def test_organize_media_type_cancelled_returns_early(
    tmp_path, monkeypatch
):
    """Empty media-type input → 'Cancelled.' log, no path-overrides
    prompt fires, no Done dialog."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.mkv").write_text("data")

    gui = _OrganizeGUI(scripted_inputs=[
        str(source),
        "",  # cancelled media-type prompt
    ])
    controller, _engine = _build_controller(gui, _engine_cfg(
        temp_folder=str(tmp_path),
        movies_folder=str(tmp_path / "movies"),
    ))

    # If _prompt_run_path_overrides ever fires, the test should fail.
    monkeypatch.setattr(
        controller,
        "_prompt_run_path_overrides",
        lambda _fields: pytest.fail(
            "_prompt_run_path_overrides should not fire when media-type is cancelled"
        ),
    )

    controller.run_organize()

    # Log lines carry a "[HH:MM:SS] " timestamp prefix, so match by
    # substring. The exact terminal-period sentinel distinguishes
    # "Cancelled." from longer messages like "Cancelled before organize"
    # so we don't accidentally accept the path-overrides cancel string.
    assert any(m.endswith("Cancelled.") for m in gui.messages), \
        "expected explicit 'Cancelled.' log on media-type cancel"
    assert gui.shown_info == []


def test_organize_media_type_loops_until_valid(tmp_path, monkeypatch):
    """Invalid media-type input loops until valid one provided.
    Pins the validation loop in controller.py:2098-2110."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.mkv").write_text("data")

    gui = _OrganizeGUI(scripted_inputs=[
        str(source),
        "x",         # invalid
        "garbage",   # invalid
        "movie",     # valid → continues
        "Chosen Movie",
        "",
        "2024",
    ])
    controller, engine = _build_controller(gui, _engine_cfg(
        temp_folder=str(tmp_path),
        movies_folder=str(tmp_path / "movies"),
        opt_auto_delete_temp=False,
        opt_auto_delete_session_metadata=False,
    ))

    monkeypatch.setattr(
        controller, "_prompt_run_path_overrides", lambda _fields: {}
    )
    monkeypatch.setattr(
        engine, "analyze_files",
        lambda *_args, **_kwargs: [(str(source / "a.mkv"), 7200.0, 4000.0)],
    )
    monkeypatch.setattr(
        controller, "_select_and_move",
        lambda *_args, **_kwargs: True,
    )
    _patch_session_helpers(controller, monkeypatch)

    controller.run_organize()

    # Two invalid attempts produce two "Invalid media type" log lines.
    invalid_logs = [m for m in gui.messages if "Invalid media type" in m]
    assert len(invalid_logs) == 2, \
        f"expected 2 'Invalid media type' log lines, got {len(invalid_logs)}"
    # Workflow continued and reached the Done dialog.
    assert ("Done", "Organize complete!") in gui.shown_info


def test_organize_path_overrides_cancelled_returns_early(
    tmp_path, monkeypatch
):
    """``_prompt_run_path_overrides`` returning None → friendly
    'Cancelled before organize' log, no analyze_files/_select_and_move
    called, no Done dialog."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.mkv").write_text("data")

    gui = _OrganizeGUI(scripted_inputs=[str(source), "m"])
    controller, engine = _build_controller(gui, _engine_cfg(
        temp_folder=str(tmp_path),
        movies_folder=str(tmp_path / "movies"),
    ))

    monkeypatch.setattr(
        controller, "_prompt_run_path_overrides", lambda _fields: None
    )
    monkeypatch.setattr(
        engine, "analyze_files",
        lambda *_a, **_kw: pytest.fail(
            "analyze_files should not run when path-overrides cancelled"
        ),
    )

    controller.run_organize()

    assert any(
        "Cancelled before organize" in m for m in gui.messages
    ), "expected friendly path-overrides cancellation log"
    assert gui.shown_info == []


def test_organize_no_titles_after_analyze_returns_early(
    tmp_path, monkeypatch
):
    """``analyze_files`` returns empty → 'No files to process' log,
    no _select_and_move call, no Done dialog."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.mkv").write_text("data")

    gui = _OrganizeGUI(scripted_inputs=[
        str(source), "m", "Chosen Movie", "", "2024",
    ])
    controller, engine = _build_controller(gui, _engine_cfg(
        temp_folder=str(tmp_path),
        movies_folder=str(tmp_path / "movies"),
    ))

    monkeypatch.setattr(
        controller, "_prompt_run_path_overrides", lambda _fields: {}
    )
    monkeypatch.setattr(
        engine, "analyze_files", lambda *_a, **_kw: []
    )
    monkeypatch.setattr(
        controller, "_select_and_move",
        lambda *_a, **_kw: pytest.fail(
            "_select_and_move should not run when analyze_files returns empty"
        ),
    )

    controller.run_organize()

    assert any(
        "No files to process" in m for m in gui.messages
    ), "expected 'No files to process' log when analyze yields empty"
    assert gui.shown_info == []


def test_organize_select_and_move_failure_with_abort_logs_move_stopped(
    tmp_path, monkeypatch
):
    """When ``_select_and_move`` returns False AND the abort_event is
    set, the workflow logs 'Move stopped before completion' so the
    user knows why the move halted."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.mkv").write_text("data")

    gui = _OrganizeGUI(scripted_inputs=[
        str(source), "m", "Chosen Movie", "", "2024",
    ])
    controller, engine = _build_controller(gui, _engine_cfg(
        temp_folder=str(tmp_path),
        movies_folder=str(tmp_path / "movies"),
        opt_auto_delete_temp=False,
        opt_auto_delete_session_metadata=False,
    ))

    monkeypatch.setattr(
        controller, "_prompt_run_path_overrides", lambda _fields: {}
    )
    monkeypatch.setattr(
        engine, "analyze_files",
        lambda *_a, **_kw: [(str(source / "a.mkv"), 60.0, 100.0)],
    )

    def _move_then_abort(*_a, **_kw):
        engine.abort_event.set()
        return False

    monkeypatch.setattr(controller, "_select_and_move", _move_then_abort)
    _patch_session_helpers(controller, monkeypatch)

    controller.run_organize()

    assert any(
        "Move stopped before completion" in m for m in gui.messages
    ), "expected 'Move stopped before completion' log when move " \
       "returns False with abort_event set"


def test_organize_source_not_under_temp_root_preserves_folder(
    tmp_path, monkeypatch
):
    """Safety property: even with ``opt_auto_delete_temp=True``, a
    source folder that is NOT under temp_root must be preserved.
    Pins the ``startswith(temp_root)`` guard at controller.py:2214."""
    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    other_root = tmp_path / "external"      # NOT under temp_root
    source = other_root / "raw_mkvs"
    source.mkdir(parents=True)
    temp_root.mkdir()
    movies_root.mkdir()

    mkv = source / "a.mkv"
    mkv.write_text("data")

    gui = _OrganizeGUI(scripted_inputs=[
        str(source), "m", "Chosen Movie", "", "2024",
    ])
    controller, engine = _build_controller(gui, _engine_cfg(
        temp_folder=str(temp_root),
        movies_folder=str(movies_root),
        opt_auto_delete_temp=True,  # WOULD delete if under temp_root
        opt_auto_delete_session_metadata=False,
    ))

    monkeypatch.setattr(
        controller, "_prompt_run_path_overrides", lambda _fields: {}
    )
    monkeypatch.setattr(
        engine, "analyze_files",
        lambda *_a, **_kw: [(str(mkv), 60.0, 100.0)],
    )
    monkeypatch.setattr(
        controller, "_select_and_move", lambda *_a, **_kw: True,
    )
    _patch_session_helpers(controller, monkeypatch)

    controller.run_organize()

    assert source.exists(), \
        "source folder OUTSIDE temp_root must be preserved even with " \
        "opt_auto_delete_temp=True"
    assert mkv.exists(), \
        "source files must not be touched when source is outside temp_root"


def test_organize_recursive_glob_uses_double_star_when_user_says_yes(
    tmp_path, monkeypatch
):
    """When the user picks 'recurse subfolders too', the glob pattern
    must include ``**`` so nested .mkv files are found.  Pins the
    branch at controller.py:2076-2090."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.mkv").write_text("data")

    glob_calls: list[tuple[str, bool]] = []

    gui = _OrganizeGUI(
        scripted_inputs=[str(source)],
        ask_yesno_returns=True,  # YES → recurse
    )
    controller, _engine = _build_controller(gui, _engine_cfg(
        temp_folder=str(tmp_path),
        movies_folder=str(tmp_path / "movies"),
    ))

    def _capture(pattern, recursive=False, timeout=8.0, context="glob"):
        glob_calls.append((pattern, recursive))
        return []  # empty → workflow returns early after capture

    monkeypatch.setattr(controller, "_safe_glob", _capture)

    controller.run_organize()

    assert glob_calls, "_safe_glob should have been invoked"
    pattern, recursive = glob_calls[0]
    assert "**" in pattern and recursive is True, (
        f"recursive=YES branch should call _safe_glob with '**' pattern "
        f"and recursive=True; got pattern={pattern!r} recursive={recursive!r}"
    )


def test_organize_non_recursive_glob_omits_double_star(
    tmp_path, monkeypatch
):
    """Counterpart to the recursive test: when the user picks 'no, only
    this folder', the glob pattern must NOT include ``**``."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.mkv").write_text("data")

    glob_calls: list[tuple[str, bool]] = []

    gui = _OrganizeGUI(
        scripted_inputs=[str(source)],
        ask_yesno_returns=False,  # NO → flat
    )
    controller, _engine = _build_controller(gui, _engine_cfg(
        temp_folder=str(tmp_path),
        movies_folder=str(tmp_path / "movies"),
    ))

    def _capture(pattern, recursive=False, timeout=8.0, context="glob"):
        glob_calls.append((pattern, recursive))
        return []

    monkeypatch.setattr(controller, "_safe_glob", _capture)

    controller.run_organize()

    assert glob_calls, "_safe_glob should have been invoked"
    pattern, recursive = glob_calls[0]
    assert "**" not in pattern and recursive is False, (
        f"recursive=NO branch should call _safe_glob with flat pattern "
        f"and recursive=False; got pattern={pattern!r} recursive={recursive!r}"
    )
