"""High-value behavior guard tests for the ripping pipeline."""

import os
import queue
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from controller.controller import IdentitySuggestion, RipperController
import controller.controller as controller_module
from controller.naming import build_fallback_title, build_movie_main_filename
from engine.ripper_engine import RipperEngine
from utils.fallback import handle_fallback
from utils.media import select_largest_file
from utils.session_result import normalize_session_result
from utils.state_machine import SessionState, SessionStateMachine
from utils.scoring import choose_best_title
from utils.classifier import ClassifiedTitle
from shared.session_setup_types import DumpSessionSetup, MovieSessionSetup, TVSessionSetup
from shared.wizard_types import JELLYFIN_EXTRAS_CATEGORIES
from utils.makemkv_log import MakeMKVIssueSummary


@pytest.fixture(autouse=True)
def _stub_drive_probe(monkeypatch):
    monkeypatch.setattr(
        RipperEngine,
        "_wait_for_drive_ready",
        lambda self, _on_log, *, context: True,
    )


class DummyGUI:
    def __init__(self):
        self.messages = []
        self.ai_messages = []
        self.ai_choices = []
        self.same_disc_prompts = []

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
        return default_value

    def show_info(self, _title, _msg):
        pass

    def show_error(self, _title, _msg):
        pass

    def ask_yesno(self, _prompt):
        return True

    def ask_space_override(self, _required, _free):
        return True

    def show_file_list(self, _title, _prompt, options):
        return [options[0]] if options else []

    def show_extras_picker(self, _title, _prompt, _options):
        return []

    def push_ai_chat_message(
        self,
        role,
        text,
        backend_tag="",
        open_sidebar=False,
    ):
        self.ai_messages.append(
            {
                "role": role,
                "text": text,
                "backend_tag": backend_tag,
                "open_sidebar": open_sidebar,
            }
        )

    def ask_ai_identity_choice(self, suggestion_text, *, backend_tag=""):
        self.ai_choices.append(
            {
                "text": suggestion_text,
                "backend_tag": backend_tag,
            }
        )
        return "edit"

    def show_disc_tree(self, *_args, **_kwargs):
        return []

    def show_scan_results_step(self, _classified, _drive_info=None):
        return "movie"

    def show_same_disc_prompt_step(self, context_summary=""):
        self.same_disc_prompts.append(context_summary)
        return "fresh"

    def show_content_mapping_step(self, classified):
        from shared.wizard_types import ContentSelection
        main_ids = [ct.title_id for ct in classified if ct.label == "MAIN"]
        extra_ids = [ct.title_id for ct in classified if ct.label == "EXTRA"]
        skip_ids = [ct.title_id for ct in classified if ct.label not in ("MAIN", "EXTRA")]
        return ContentSelection(main_title_ids=main_ids, extra_title_ids=extra_ids, skip_title_ids=skip_ids)

    def show_extras_classification_step(self, _extra_titles):
        from shared.wizard_types import ExtrasAssignment
        return ExtrasAssignment()

    def show_output_plan_step(
        self,
        _base_folder,
        _main_label,
        _extras_map,
        *,
        detail_lines=None,
        header_text="Step 5: Review Output Plan",
        subtitle_text="Review the destination, file naming, and extras layout before ripping.",
        confirm_text="Start Rip",
        suggested_folder=None,
    ):
        _ = detail_lines
        _ = header_text
        _ = subtitle_text
        _ = confirm_text
        _ = suggested_folder
        return True

    def ask_movie_setup(self, **_kwargs):
        from shared.session_setup_types import MovieSessionSetup
        return MovieSessionSetup(
            title="Untitled", year="2024", edition="",
            metadata_provider="TMDB", metadata_id="",
            replace_existing=False, keep_raw=False, extras_mode="ask",
        )

    def ask_tv_setup(self, **_kwargs):
        from shared.session_setup_types import TVSessionSetup
        return TVSessionSetup(
            title="Untitled", year="", season=1, starting_disc=1,
            episode_mapping="auto", metadata_provider="TMDB", metadata_id="",
            multi_episode="auto", specials="ask",
            replace_existing=False, keep_raw=False,
        )


class ScriptedSetupGUI(DummyGUI):
    def __init__(self, scripted_inputs, scripted_confirms):
        super().__init__()
        self._inputs = iter(scripted_inputs)
        self._confirms = iter(scripted_confirms)

    def ask_input(self, _label, _prompt, default_value=""):
        return next(self._inputs)

    def ask_yesno(self, _prompt):
        return next(self._confirms)


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


def _controller_with_engine(cfg=None):
    engine = RipperEngine(cfg or _engine_cfg())
    gui = DummyGUI()
    return RipperController(engine, gui), engine


def test_controller_imports_make_rip_folder_name():
    assert callable(controller_module.make_rip_folder_name)


def test_extras_categories_include_plain_extras():
    assert JELLYFIN_EXTRAS_CATEGORIES[0] == "Extras"


def test_unattended_setup_allows_editing_accidental_values():
    engine = RipperEngine(_engine_cfg())
    gui = ScriptedSetupGUI(
        scripted_inputs=[
            "2", "wrong title", "batch-a",   # first attempt (rejected)
            "3", "title1, title2", "batch-b",  # second attempt (accepted)
        ],
        scripted_confirms=[False, True],
    )
    controller = RipperController(engine, gui)

    total, per_disc_titles, batch_title = controller._collect_dump_all_multi_setup()

    assert total == 3
    assert per_disc_titles == ["title1", "title2"]
    assert batch_title == "batch-b"


def test_nonzero_exit_with_output_is_degraded_success(tmp_path, monkeypatch):
    """rip_selected_titles: non-zero exit + files produced = degraded success, not failure."""
    controller, engine = _controller_with_engine(
        _engine_cfg(opt_auto_retry=False, opt_retry_attempts=1)
    )

    def fake_run(_cmd, _on_progress, _on_log):
        Path(tmp_path, "title_t00.mkv").write_text("data")
        return False  # non-zero exit

    monkeypatch.setattr(engine, "_run_rip_process", fake_run)

    success, failed = engine.rip_selected_titles(
        str(tmp_path), [0], on_progress=lambda _p: None, on_log=controller.log
    )

    assert success is True
    assert failed == []
    assert engine.last_degraded_titles == [1]
    assert any("degraded success" in m for m in controller.gui.messages)


def test_partial_title_failure_forces_session_failure(tmp_path, monkeypatch):
    controller, engine = _controller_with_engine()
    Path(tmp_path, "file.mkv").write_text("ok")
    monkeypatch.setattr(engine, "_quick_ffprobe_ok", lambda _f, _l: True)

    normalized_success, _files = controller._normalize_rip_result(
        str(tmp_path), True, [1]
    )

    assert normalized_success is False


def test_ffprobe_cache_accumulates_entries(tmp_path, monkeypatch):
    _controller, engine = _controller_with_engine()
    first = tmp_path / "first.mkv"
    second = tmp_path / "second.mkv"
    first.write_bytes(b"a" * 10)
    second.write_bytes(b"b" * 20)

    class FakeProc:
        def poll(self):
            return 0

        def communicate(self, timeout=None):
            return ('{"format": {"duration": "60"}}', "")

    monkeypatch.setattr(
        "engine.ripper_engine.subprocess.Popen",
        lambda *args, **kwargs: FakeProc(),
    )

    engine._probe_file_duration_and_size(str(first), ffprobe="ffprobe")
    engine._probe_file_duration_and_size(str(second), ffprobe="ffprobe")

    assert len(engine._ffprobe_cache) == 2


def test_find_old_temp_folders_returns_ui_ready_rows(tmp_path):
    _controller, engine = _controller_with_engine()

    old = tmp_path / "Disc_2026-04-13_12-59-46"
    nested = old / "nested"
    nested.mkdir(parents=True)
    (old / "_rip_meta.json").write_text("{}", encoding="utf-8")
    (nested / "video.mkv").write_bytes(b"1234")
    (tmp_path / "empty").mkdir()

    rows = engine.find_old_temp_folders(str(tmp_path), timeout=0.2)

    row = next(item for item in rows if item[1] == old.name)
    assert row == (str(old), old.name, 2, 6)
    assert all(name != "empty" for _full, name, _count, _size in rows)


def test_find_old_temp_folders_skips_metadata_only_folder(tmp_path):
    _controller, engine = _controller_with_engine()

    meta_only = tmp_path / "meta_only"
    meta_only.mkdir()
    (meta_only / "_rip_meta.json").write_text("{}", encoding="utf-8")
    (meta_only / "session.state.json").write_text("{}", encoding="utf-8")

    rows = engine.find_old_temp_folders(str(tmp_path), timeout=0.2)

    assert rows == []


def test_zero_output_is_failure(tmp_path):
    controller, _engine = _controller_with_engine()

    normalized_success, files = controller._normalize_rip_result(
        str(tmp_path), True, []
    )

    assert normalized_success is False
    assert files == []


def test_abort_forces_failure_even_with_files(tmp_path, monkeypatch):
    controller, engine = _controller_with_engine()
    mkv = Path(tmp_path, "file.mkv")
    mkv.write_text("ok")

    monkeypatch.setattr(engine, "_quick_ffprobe_ok", lambda _f, _l: True)
    engine.abort()

    normalized_success, files = controller._normalize_rip_result(
        str(tmp_path), True, []
    )

    assert normalized_success is False
    assert files == [str(mkv)]


def test_normalize_session_result_abort_is_failure():
    assert normalize_session_result(
        abort=True,
        failed_titles=[],
        files=["a.mkv"],
        valid_files=["a.mkv"],
    ) is False


def test_normalize_session_result_failed_titles_is_failure():
    assert normalize_session_result(
        abort=False,
        failed_titles=[1],
        files=["a.mkv"],
        valid_files=["a.mkv"],
    ) is False


def test_normalize_session_result_mixed_validity_is_failure():
    assert normalize_session_result(
        abort=False,
        failed_titles=[],
        files=["a.mkv", "b.mkv"],
        valid_files=["a.mkv"],
    ) is False


def test_normalize_session_result_no_files_is_failure():
    assert normalize_session_result(
        abort=False,
        failed_titles=[],
        files=[],
        valid_files=[],
    ) is False


def test_normalize_session_result_full_success_passes():
    assert normalize_session_result(
        abort=False,
        failed_titles=[],
        files=["a.mkv", "b.mkv"],
        valid_files=["a.mkv", "b.mkv"],
    ) is True


def test_mixed_quality_output_fails(tmp_path, monkeypatch):
    controller, engine = _controller_with_engine()
    good = Path(tmp_path, "good.mkv")
    bad = Path(tmp_path, "bad.mkv")
    good.write_text("good")
    bad.write_text("bad")

    monkeypatch.setattr(
        engine,
        "_quick_ffprobe_ok",
        lambda f, _l: Path(f).name == "good.mkv",
    )

    normalized_success, files = controller._normalize_rip_result(
        str(tmp_path), True, []
    )

    assert normalized_success is False
    assert len(files) == 2


def test_wipe_preserves_metadata(tmp_path):
    engine = RipperEngine(_engine_cfg())

    meta = Path(tmp_path, "_rip_meta.json")
    mkv = Path(tmp_path, "file.mkv")
    partial = Path(tmp_path, "file.partial")

    meta.write_text("{}")
    mkv.write_text("data")
    partial.write_text("data")

    engine.wipe_session_outputs(str(tmp_path), lambda _m: None)

    assert meta.exists()
    assert not mkv.exists()
    assert not partial.exists()


def test_delete_temp_metadata_removes_session_jsons(tmp_path):
    engine = RipperEngine(_engine_cfg())
    rip_meta = tmp_path / "_rip_meta.json"
    state_meta = tmp_path / "session.state.json"
    rip_meta.write_text("{}", encoding="utf-8")
    state_meta.write_text("{}", encoding="utf-8")

    removed = engine.delete_temp_metadata(str(tmp_path))

    assert removed == 2
    assert not rip_meta.exists()
    assert not state_meta.exists()


def test_purge_removes_existing_files(tmp_path):
    engine = RipperEngine(_engine_cfg())
    old = Path(tmp_path, "old.mkv")
    old.write_text("data")

    engine._purge_rip_target_files(str(tmp_path), lambda _m: None)

    assert not old.exists()


def test_timestamp_mode_uses_timestamp_fallback():
    name = build_fallback_title(
        {"opt_naming_mode": "timestamp"},
        make_temp_title_fn=lambda: "Disc_2026-03-27",
        clean_name_fn=lambda s: s,
        choose_best_title_fn=lambda _titles, require_valid=False: (None, 0.0),
        disc_titles=[],
    )
    assert name.startswith("Disc_")


def test_auto_name_fallback_when_no_titles():
    name = build_fallback_title(
        {"opt_naming_mode": "auto-title"},
        make_temp_title_fn=lambda: "Disc_2026-03-27",
        clean_name_fn=lambda s: s,
        choose_best_title_fn=lambda _titles, require_valid=False: (None, 0.0),
        disc_titles=[],
    )
    assert name.startswith("Disc_")


def test_auto_title_with_timestamp_mode():
    titles = [
        {
            "id": 0,
            "name": "Test Movie",
            "duration_seconds": 4000,
            "size_bytes": 2_000_000_000,
            "chapters": 20,
            "audio_tracks": [1, 2],
            "subtitle_tracks": [1],
        }
    ]

    name = build_fallback_title(
        {"opt_naming_mode": "auto-title+timestamp"},
        make_temp_title_fn=lambda: "Disc_2026-03-27",
        clean_name_fn=lambda s: s,
        choose_best_title_fn=choose_best_title,
        disc_titles=titles,
    )

    assert "Test Movie" in name
    assert name.startswith("Test Movie_")


def test_scoring_prefers_main_title():
    titles = [
        {
            "duration_seconds": 5000,
            "size_bytes": 4_000_000_000,
            "chapters": 20,
            "audio_tracks": [1, 2],
            "subtitle_tracks": [1],
        },
        {
            "duration_seconds": 300,
            "size_bytes": 200_000_000,
            "chapters": 1,
            "audio_tracks": [1],
            "subtitle_tracks": [],
        },
    ]

    best, _score = choose_best_title(titles)
    assert best["duration_seconds"] == 5000


def test_scoring_rejects_fake_size_tie():
    titles = [
        {
            "duration_seconds": 5000,
            "size_bytes": 4_000_000_000,
            "chapters": 20,
            "audio_tracks": [1, 2],
            "subtitle_tracks": [1],
        },
        {
            "duration_seconds": 5000,
            "size_bytes": 100_000_000,
            "chapters": 1,
            "audio_tracks": [1],
            "subtitle_tracks": [],
        },
    ]

    best, _score = choose_best_title(titles)
    assert best["size_bytes"] == 4_000_000_000


def test_retry_stops_on_success(tmp_path, monkeypatch):
    engine = RipperEngine(_engine_cfg(opt_retry_attempts=3, opt_auto_retry=True))
    outcomes = iter([False, True])
    calls = []

    def fake_run(_cmd, _on_progress, _on_log):
        calls.append("run")
        success = next(outcomes)
        # When successful, create a dummy MKV file so file validation passes
        if success:
            Path(tmp_path, "output.mkv").write_text("dummy")
        return success

    monkeypatch.setattr(engine, "_run_rip_process", fake_run)

    result = engine.rip_all_titles(
        str(tmp_path), on_progress=lambda _p: None, on_log=lambda _m: None
    )

    assert result is True
    assert len(calls) == 2


def test_retry_exhaustion(tmp_path, monkeypatch):
    engine = RipperEngine(_engine_cfg(opt_retry_attempts=3, opt_auto_retry=True))
    calls = []

    def fake_run(_cmd, _on_progress, _on_log):
        calls.append("run")
        return False

    monkeypatch.setattr(engine, "_run_rip_process", fake_run)

    result = engine.rip_all_titles(
        str(tmp_path), on_progress=lambda _p: None, on_log=lambda _m: None
    )

    assert result is False
    assert len(calls) == 3


def test_rip_all_titles_nonzero_exit_with_output_is_failure(tmp_path, monkeypatch):
    engine = RipperEngine(_engine_cfg(opt_auto_retry=False, opt_retry_attempts=1))
    logs = []

    def fake_run(_cmd, _on_progress, _on_log):
        Path(tmp_path, "partial.mkv").write_text("partial")
        return False

    monkeypatch.setattr(engine, "_run_rip_process", fake_run)

    result = engine.rip_all_titles(
        str(tmp_path), on_progress=lambda _p: None, on_log=logs.append
    )

    assert result is False
    assert any(
        "forcing failure regardless of output" in line
        for line in logs
    )


def test_rip_selected_titles_success_without_output_logs_dir_contents_instead_of_crashing(
    tmp_path, monkeypatch
):
    engine = RipperEngine(_engine_cfg(opt_auto_retry=False, opt_retry_attempts=1))
    logs = []

    def fake_run(_cmd, _on_progress, _on_log):
        return True

    monkeypatch.setattr(engine, "_run_rip_process", fake_run)

    success, failed = engine.rip_selected_titles(
        str(tmp_path), [0], on_progress=lambda _p: None, on_log=logs.append
    )

    assert success is False
    assert failed == [1]
    assert any(
        "Rip target directory is empty after MakeMKV run" in line
        for line in logs
    )


def test_rip_all_titles_records_multi_file_title_groups_for_dump_all(
    tmp_path, monkeypatch
):
    engine = RipperEngine(_engine_cfg(opt_auto_retry=False, opt_retry_attempts=1))

    def fake_run(_cmd, _on_progress, _on_log):
        Path(tmp_path, "title_t00_part1.mkv").write_text("part1")
        Path(tmp_path, "title_t00_part2.mkv").write_text("part2")
        Path(tmp_path, "title_t01.mkv").write_text("main")
        return True

    monkeypatch.setattr(engine, "_run_rip_process", fake_run)

    result = engine.rip_all_titles(
        str(tmp_path), on_progress=lambda _p: None, on_log=lambda _m: None
    )

    assert result is True
    assert sorted(engine.last_title_file_map) == [0, 1]
    assert len(engine.last_title_file_map[0]) == 2
    assert len(engine.last_title_file_map[1]) == 1


def test_abort_stops_rip(tmp_path):
    engine = RipperEngine(_engine_cfg())
    engine.abort()

    success, failed = engine.rip_selected_titles(
        str(tmp_path), [0], on_progress=lambda _p: None, on_log=lambda _m: None
    )

    assert success is False
    assert failed == []


def test_session_failure_triggers_wipe_and_metadata(tmp_path):
    controller, engine = _controller_with_engine()
    engine.write_temp_metadata(str(tmp_path), "Title", 1)

    mkv = Path(tmp_path, "output.mkv")
    mkv.write_text("data")

    controller._mark_session_failed(str(tmp_path), title="Title", media_type="movie")

    meta = engine.read_temp_metadata(str(tmp_path))
    assert not mkv.exists()
    assert meta is not None
    assert meta.get("status") == "failed"
    assert meta.get("phase") == "failed"


def test_move_files_movie_with_extras_uses_clean_name_without_name_error(
    tmp_path, monkeypatch
):
    engine = RipperEngine(_engine_cfg(opt_check_dest_space=False))
    monkeypatch.setattr(engine, "_quick_ffprobe_ok", lambda _f, _l: True)

    src_main = Path(tmp_path, "01_sn11-D1.mkv")
    src_extra = Path(tmp_path, "02_sn13-F1.mkv")
    src_main.write_text("main")
    src_extra.write_text("extra")

    dest_folder = Path(tmp_path, "Movies", "Pitch Perfect 2 (2015)")
    extras_folder = dest_folder / "Extras"
    dest_folder.mkdir(parents=True, exist_ok=True)
    extras_folder.mkdir(parents=True, exist_ok=True)

    ok, next_extra_counter, moved_paths = engine.move_files(
        titles_list=[
            (str(src_main), 0, 0),
            (str(src_extra), 0, 0),
        ],
        main_indices=[0],
        episode_numbers=[],
        real_names=[],
        extra_indices=None,
        is_tv=False,
        title="Pitch Perfect 2",
        dest_folder=str(dest_folder),
        extras_folder=str(extras_folder),
        season=1,
        year="2015",
        extra_counter=1,
        on_progress=lambda _p: None,
        on_log=lambda _m: None,
    )

    assert ok is True
    assert next_extra_counter == 2
    assert len(moved_paths) == 2
    assert src_main.exists() is False
    assert src_extra.exists() is False


def test_move_files_movie_uses_edition_in_main_filename(tmp_path, monkeypatch):
    engine = RipperEngine(_engine_cfg(opt_check_dest_space=False))

    source = tmp_path / "raw_movie.mkv"
    source.write_text("main", encoding="utf-8")

    dest_folder = tmp_path / "Movies" / "Chosen Movie (2024) - Director's Cut"
    extras_folder = dest_folder / "Extras"
    dest_folder.mkdir(parents=True, exist_ok=True)
    extras_folder.mkdir(parents=True, exist_ok=True)

    captured: dict[str, object] = {}

    def fake_move_file_atomic(source, final_path, on_log, replace_existing=False):
        captured["source"] = source
        captured["final_path"] = final_path
        captured["replace_existing"] = replace_existing
        return True

    monkeypatch.setattr(engine, "move_file_atomic", fake_move_file_atomic)

    success, extra_counter, moved_paths = engine.move_files(
        titles_list=[(str(source), 7200.0, 4000.0)],
        main_indices=[0],
        episode_numbers=[],
        real_names=[],
        extra_indices=[],
        is_tv=False,
        title="Chosen Movie",
        dest_folder=str(dest_folder),
        extras_folder=str(extras_folder),
        season=0,
        year="2024",
        extra_counter=1,
        on_progress=lambda _percent: None,
        on_log=lambda _message: None,
        edition="Director's Cut",
    )

    expected_target = dest_folder / build_movie_main_filename(
        "Chosen Movie",
        "2024",
        "Director's Cut",
    )
    assert success is True
    assert extra_counter == 1
    assert moved_paths == [str(expected_target)]
    assert captured["final_path"] == str(expected_target)


def test_move_files_replaces_existing_main_when_requested(tmp_path, monkeypatch):
    engine = RipperEngine(_engine_cfg(opt_check_dest_space=False))

    source = tmp_path / "raw_episode.mkv"
    source.write_text("new", encoding="utf-8")

    dest_folder = tmp_path / "TV" / "Chosen Show" / "Season 01"
    extras_folder = dest_folder / "Extras"
    dest_folder.mkdir(parents=True, exist_ok=True)
    extras_folder.mkdir(parents=True, exist_ok=True)

    existing_target = dest_folder / "Chosen Show - S01E01 - Episode 01.mkv"
    existing_target.write_text("old", encoding="utf-8")

    monkeypatch.setattr(
        engine,
        "unique_path",
        lambda path: f"{path}.renamed",
    )

    captured: dict[str, object] = {}

    def fake_move_file_atomic(source, final_path, on_log, replace_existing=False):
        captured["source"] = source
        captured["final_path"] = final_path
        captured["replace_existing"] = replace_existing
        return True

    monkeypatch.setattr(engine, "move_file_atomic", fake_move_file_atomic)

    success, extra_counter, moved_paths = engine.move_files(
        titles_list=[(str(source), 1440.0, 1000.0)],
        main_indices=[0],
        episode_numbers=[1],
        real_names=[],
        extra_indices=[],
        is_tv=True,
        title="Chosen Show",
        dest_folder=str(dest_folder),
        extras_folder=str(extras_folder),
        season=1,
        year="0000",
        extra_counter=1,
        on_progress=lambda _percent: None,
        on_log=lambda _message: None,
        replace_main_existing=True,
    )

    assert success is True
    assert extra_counter == 1
    assert moved_paths == [str(existing_target)]
    assert captured["final_path"] == str(existing_target)
    assert captured["replace_existing"] is True


class TestComputeFileMinSize:
    """_compute_file_min_size: trusted expected vs. fallback floor."""

    _1_GB = 1 * 1024 ** 3
    _100_MB = 100 * 1024 * 1024
    _500_MB = 500 * 1024 * 1024

    def _min(self, expected, floor=_1_GB):
        return RipperController._compute_file_min_size(expected, floor)

    def test_credible_expected_overrides_1gb_floor(self):
        # 500 MB extra must NOT be forced to pass a 1 GB floor.
        result = self._min(self._500_MB)
        assert result < self._1_GB

    def test_credible_expected_is_half_of_expected(self):
        result = self._min(self._500_MB)
        assert result == self._500_MB // 2

    def test_zero_expected_falls_back_to_floor(self):
        assert self._min(0) == self._1_GB

    def test_garbage_small_expected_falls_back_to_floor(self):
        # 5 MB "expected" is a bad parse â€" must not be trusted.
        assert self._min(5 * 1024 * 1024) == self._1_GB

    def test_exactly_100mb_boundary_uses_floor(self):
        # Boundary value: exactly 100 MB is NOT > 100 MB, so falls back.
        assert self._min(self._100_MB) == self._1_GB

    def test_just_above_100mb_boundary_trusts_expected(self):
        just_above = self._100_MB + 1
        assert self._min(just_above) == just_above // 2

    def test_large_main_feature_trusts_expected(self):
        _7_GB = 7 * 1024 ** 3
        result = self._min(_7_GB)
        assert result == _7_GB // 2

    def test_inflated_expected_never_exceeds_expected(self):
        # Inflated playlist size: threshold must not exceed expected itself.
        _20_GB = 20 * 1024 ** 3
        result = self._min(_20_GB)
        assert result <= _20_GB


def test_map_title_ids_prefers_engine_tracked_file_map(tmp_path):
    controller, engine = _controller_with_engine()

    f_main = Path(tmp_path, "A.mkv")
    f_extra = Path(tmp_path, "B.mkv")
    f_main.write_text("m")
    f_extra.write_text("e")

    engine.last_title_file_map = {
        2: [str(f_main)],
        3: [str(f_extra)],
    }

    titles_list = [
        (str(f_extra), 0, 0),
        (str(f_main), 0, 0),
    ]
    mapped = controller._map_title_ids_to_analyzed_indices(titles_list, [2])
    assert mapped == [1]


def test_dump_output_summary_logs_multi_file_group_counts(tmp_path):
    controller, engine = _controller_with_engine()

    part1 = tmp_path / "title_t00_part1.mkv"
    part2 = tmp_path / "title_t00_part2.mkv"
    single = tmp_path / "title_t01.mkv"
    part1.write_bytes(b"a" * 10)
    part2.write_bytes(b"b" * 20)
    single.write_bytes(b"c" * 30)

    engine.last_title_file_map = {
        0: [str(part1), str(part2)],
        1: [str(single)],
    }

    group_count = controller._log_dump_output_summary(
        [str(part1), str(part2), str(single)]
    )

    assert group_count == 2
    assert any(
        "3 file(s) across 2 title group(s)" in line
        for line in controller.gui.messages
    )
    assert any("Title 1: 2 files" in line for line in controller.gui.messages)
    assert any("title_t00_part1.mkv" in line for line in controller.gui.messages)
    assert any("title_t00_part2.mkv" in line for line in controller.gui.messages)
    assert any("Title 2: 1 file" in line for line in controller.gui.messages)


def test_run_dump_all_reports_file_and_title_group_counts(tmp_path, monkeypatch):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    temp_root.mkdir(parents=True, exist_ok=True)
    part1 = tmp_path / "title_t00_part1.mkv"
    part2 = tmp_path / "title_t00_part2.mkv"
    single = tmp_path / "title_t01.mkv"
    part1.write_bytes(b"a" * 10)
    part2.write_bytes(b"b" * 20)
    single.write_bytes(b"c" * 30)

    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False

    confirms = iter([False, False])
    infos = []

    controller.gui.ask_yesno = lambda _prompt: next(confirms)
    controller.gui.ask_input = (
        lambda _label, _prompt, default_value="": "My Dump Disc"
    )
    controller.gui.show_info = (
        lambda title, message: infos.append((title, message))
    )

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "write_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "update_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(
        engine,
        "run_job",
        lambda _job: (
            setattr(
                engine,
                "last_title_file_map",
                {0: [str(part1), str(part2)], 1: [str(single)]},
            )
            or SimpleNamespace(success=True, errors=[])
        ),
    )
    monkeypatch.setattr(
        controller,
        "_normalize_rip_result",
        lambda *_a, **_k: (True, [str(part1), str(part2), str(single)]),
    )
    monkeypatch.setattr(
        controller,
        "_stabilize_ripped_files",
        lambda *_a, **_k: (True, False),
    )
    monkeypatch.setattr(
        controller,
        "_verify_container_integrity",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(controller, "write_session_summary", lambda: None)
    monkeypatch.setattr(controller, "flush_log", lambda: None)

    controller.run_dump_all()

    assert any(
        "3 file(s) across 2 title group(s)" in line
        for line in controller.gui.messages
    )
    assert infos
    assert "3 file(s) across 2 title group(s)" in infos[-1][1]


def test_run_dump_all_uses_dump_setup_for_multi_disc(tmp_path, monkeypatch):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    temp_root.mkdir(parents=True, exist_ok=True)
    engine.cfg["temp_folder"] = str(temp_root)

    dump_setup = DumpSessionSetup(
        multi_disc=True,
        disc_name="",
        disc_count=2,
        custom_disc_names="Disc A, Disc B",
        batch_title="Batch Name",
    )

    controller.gui.ask_dump_setup = lambda: dump_setup
    controller.gui.ask_yesno = lambda _prompt: (_ for _ in ()).throw(
        AssertionError("ask_yesno should not run when dump setup is available")
    )

    monkeypatch.setattr(controller, "_prompt_run_path_overrides", lambda _specs: {})
    monkeypatch.setattr(controller, "_init_session_paths", lambda _overrides: None)
    monkeypatch.setattr(controller, "_log_session_paths", lambda: None)
    monkeypatch.setattr(controller, "get_path", lambda _key: str(temp_root))

    seen = {}
    monkeypatch.setattr(
        controller,
        "_run_dump_all_multi",
        lambda root, setup=None: seen.update({"root": root, "setup": setup}),
    )

    controller.run_dump_all()

    assert seen["root"] == str(temp_root)
    assert seen["setup"] is dump_setup


def test_run_dump_all_single_uses_dump_setup_disc_name(tmp_path, monkeypatch):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    temp_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False

    dump_setup = DumpSessionSetup(
        multi_disc=False,
        disc_name="Named Disc",
        disc_count=1,
        custom_disc_names="",
        batch_title="",
    )

    controller.gui.ask_dump_setup = lambda: dump_setup
    controller.gui.ask_yesno = lambda _prompt: (_ for _ in ()).throw(
        AssertionError("ask_yesno should not run when dump setup is available")
    )
    controller.gui.ask_input = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("ask_input should not run when dump setup supplies a disc name")
    )
    info_calls: list[tuple[str, str]] = []
    controller.gui.show_info = (
        lambda title, message: info_calls.append((title, message))
    )

    monkeypatch.setattr(controller, "_prompt_run_path_overrides", lambda _specs: {})
    monkeypatch.setattr(controller, "_init_session_paths", lambda _overrides: None)
    monkeypatch.setattr(controller, "_log_session_paths", lambda: None)
    monkeypatch.setattr(controller, "get_path", lambda _key: str(temp_root))

    writes = []
    monkeypatch.setattr(
        engine,
        "write_temp_metadata",
        lambda path, title, disc_number, **_kwargs: writes.append((path, title, disc_number)),
    )
    monkeypatch.setattr(
        engine,
        "run_job",
        lambda _job: SimpleNamespace(success=False, errors=[]),
    )
    monkeypatch.setattr(
        controller,
        "_normalize_rip_result",
        lambda *_args, **_kwargs: (False, []),
    )
    monkeypatch.setattr(controller, "flush_log", lambda: None)

    controller.run_dump_all()

    assert writes
    assert writes[0][1] == "Named Disc"
    assert writes[0][2] == 1


def test_run_dump_all_multi_prefers_cached_disc_size_after_fingerprint(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    temp_root.mkdir()

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = True

    monkeypatch.setattr(engine, "reset_abort", lambda: None)
    monkeypatch.setattr(
        engine,
        "cleanup_partial_files",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        controller,
        "_collect_dump_all_multi_setup",
        lambda: (1, [], "Batch"),
    )
    monkeypatch.setattr(
        controller,
        "_wait_for_new_unique_disc",
        lambda *_args, **_kwargs: "fingerprint-1",
    )
    monkeypatch.setattr(
        engine,
        "write_temp_metadata",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        controller,
        "_safe_glob",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        engine,
        "run_job",
        lambda *_args, **_kwargs: SimpleNamespace(success=False),
    )
    monkeypatch.setattr(
        controller,
        "_normalize_rip_result",
        lambda *_args, **_kwargs: (False, []),
    )
    monkeypatch.setattr(controller, "write_session_summary", lambda: None)
    monkeypatch.setattr(controller, "flush_log", lambda: None)
    monkeypatch.setattr(
        controller.gui,
        "show_info",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        controller.gui,
        "show_error",
        lambda *_args, **_kwargs: None,
    )

    prefer_cached_calls = []

    def _fake_get_disc_size(
        _on_log,
        prefer_cached=False,
        timeout_seconds=None,
    ):
        _ = timeout_seconds
        prefer_cached_calls.append(prefer_cached)
        return None

    monkeypatch.setattr(engine, "get_disc_size", _fake_get_disc_size)

    controller._run_dump_all_multi(str(temp_root))

    assert prefer_cached_calls == [True]


def test_run_dump_all_multi_skips_cached_size_after_manual_advance(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    temp_root.mkdir()

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = True

    monkeypatch.setattr(engine, "reset_abort", lambda: None)
    monkeypatch.setattr(
        engine,
        "cleanup_partial_files",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        controller,
        "_collect_dump_all_multi_setup",
        lambda: (1, [], "Batch"),
    )
    monkeypatch.setattr(
        controller,
        "_wait_for_new_unique_disc",
        lambda *_args, **_kwargs: "manual-advance",
    )
    monkeypatch.setattr(
        engine,
        "write_temp_metadata",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        controller,
        "_safe_glob",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        engine,
        "run_job",
        lambda *_args, **_kwargs: SimpleNamespace(success=False),
    )
    monkeypatch.setattr(
        controller,
        "_normalize_rip_result",
        lambda *_args, **_kwargs: (False, []),
    )
    monkeypatch.setattr(controller, "write_session_summary", lambda: None)
    monkeypatch.setattr(controller, "flush_log", lambda: None)
    monkeypatch.setattr(
        controller.gui,
        "show_info",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        controller.gui,
        "show_error",
        lambda *_args, **_kwargs: None,
    )

    prefer_cached_calls = []

    def _fake_get_disc_size(
        _on_log,
        prefer_cached=False,
        timeout_seconds=None,
    ):
        _ = timeout_seconds
        prefer_cached_calls.append(prefer_cached)
        return None

    monkeypatch.setattr(engine, "get_disc_size", _fake_get_disc_size)

    controller._run_dump_all_multi(str(temp_root))

    assert prefer_cached_calls == [False]


def test_build_disc_fingerprint_uses_titles_beyond_top12(monkeypatch):
    controller, _engine = _controller_with_engine()

    base = [
        {
            "id": i,
            "duration_seconds": 10000 - i,
            "size_bytes": (10000 - i) * 1024,
            "chapters": 1,
            "audio_tracks": [1],
            "subtitle_tracks": [1],
        }
        for i in range(13)
    ]
    changed = [dict(t) for t in base]
    # Change only the 13th title signature; old top-12-only logic would collide.
    changed[12]["size_bytes"] += 123456

    monkeypatch.setattr(controller, "scan_with_retry", lambda: base)
    fp1 = controller._build_disc_fingerprint()
    monkeypatch.setattr(controller, "scan_with_retry", lambda: changed)
    fp2 = controller._build_disc_fingerprint()

    assert fp1 != fp2


def test_duplicate_resolution_prefers_custom_title_override_yes(monkeypatch):
    controller, _engine = _controller_with_engine()

    monkeypatch.setattr(
        controller.gui, "ask_yesno", lambda _p: True, raising=False
    )
    called = {"value": False}

    def _should_not_call(*_args, **_kwargs):
        called["value"] = True
        return "stop"

    monkeypatch.setattr(
        controller.gui,
        "ask_duplicate_resolution",
        _should_not_call,
        raising=False,
    )

    action = controller._resolve_duplicate_dump_disc(
        disc_number=2,
        total=2,
        per_disc_titles=["pitch perfect 2", "pitch perfect"],
    )

    assert action == "bypass"
    assert called["value"] is False


def test_wait_for_new_unique_disc_allows_manual_advance_when_unverified(monkeypatch):
    controller, _engine = _controller_with_engine()
    seen = set()

    monkeypatch.setattr(
        controller.gui, "show_info", lambda *_a, **_k: None, raising=False
    )
    monkeypatch.setattr(controller, "_wait_for_disc_state", lambda *_a, **_k: True)

    calls = iter([None, None])
    monkeypatch.setattr(controller, "_build_disc_fingerprint", lambda: next(calls))
    monkeypatch.setattr(
        controller.gui,
        "ask_duplicate_resolution",
        lambda *_a, **_k: "bypass",
        raising=False,
    )

    result = controller._wait_for_new_unique_disc(
        seen_fingerprints=seen,
        disc_number=2,
        total=4,
    )

    assert result == "manual-advance"
    assert any(
        "could not read disc fingerprint" in line.lower()
        for line in controller.session_report
    )


def test_wait_for_new_unique_disc_stop_after_unverified(monkeypatch):
    controller, _engine = _controller_with_engine()

    monkeypatch.setattr(
        controller.gui, "show_info", lambda *_a, **_k: None, raising=False
    )
    monkeypatch.setattr(controller, "_wait_for_disc_state", lambda *_a, **_k: True)

    calls = iter([None, None])
    monkeypatch.setattr(controller, "_build_disc_fingerprint", lambda: next(calls))
    monkeypatch.setattr(
        controller.gui,
        "ask_duplicate_resolution",
        lambda *_a, **_k: "stop",
        raising=False,
    )

    result = controller._wait_for_new_unique_disc(
        seen_fingerprints=set(),
        disc_number=2,
        total=4,
    )

    assert result is None
    assert any(
        "stopped after unverified disc prompt" in line.lower()
        for line in controller.session_report
    )


def test_wait_for_new_unique_disc_accepts_fast_identity_without_fingerprint(
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    engine._last_scan_target = engine.get_disc_target()
    engine._last_scan_timestamp = controller_module.time.time()
    engine._last_scan_total_bytes = 123456789
    engine.last_disc_info = {
        "volume_id": "DISC_A",
        "title_count": 4,
        "size_signature": "111,222,333,444",
    }

    monkeypatch.setattr(
        controller.gui, "show_info", lambda *_a, **_k: None, raising=False
    )
    monkeypatch.setattr(controller, "_disc_present", lambda **_k: True)
    monkeypatch.setattr(
        controller,
        "_build_disc_fingerprint",
        lambda: (_ for _ in ()).throw(
            AssertionError("full fingerprint should not run")
        ),
    )

    seen_fast = set()
    result = controller._wait_for_new_unique_disc(
        seen_fingerprints=set(),
        seen_disc_identities=seen_fast,
        disc_number=2,
        total=4,
    )

    assert result.startswith("volume:disc_a|")
    assert result in seen_fast


def test_wait_for_new_unique_disc_detects_duplicate_via_fast_identity(
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    engine._last_scan_target = engine.get_disc_target()
    engine._last_scan_timestamp = controller_module.time.time()
    engine._last_scan_total_bytes = 123456789
    engine.last_disc_info = {
        "volume_id": "DISC_A",
        "title_count": 4,
        "size_signature": "111,222,333,444",
    }

    fast_identity = controller._recent_disc_fast_identity()
    assert fast_identity is not None

    monkeypatch.setattr(
        controller.gui, "show_info", lambda *_a, **_k: None, raising=False
    )
    monkeypatch.setattr(controller, "_disc_present", lambda **_k: True)
    monkeypatch.setattr(
        controller,
        "_build_disc_fingerprint",
        lambda: (_ for _ in ()).throw(
            AssertionError("full fingerprint should not run")
        ),
    )

    result = controller._wait_for_new_unique_disc(
        seen_fingerprints=set(),
        seen_disc_identities={fast_identity},
        disc_number=2,
        total=4,
    )

    assert result == "duplicate"


def test_wait_for_disc_state_uses_fast_probe_for_removal(monkeypatch):
    controller, engine = _controller_with_engine()
    engine.cfg["opt_disc_presence_probe_seconds"] = 45

    seen = []

    def _fake_disc_present(probe_timeout_seconds=None):
        seen.append(probe_timeout_seconds)
        return False

    monkeypatch.setattr(controller, "_disc_present", _fake_disc_present)

    assert controller._wait_for_disc_state(
        want_present=False,
        timeout_seconds=10,
    ) is True
    assert seen == [8]


def test_wait_for_disc_state_uses_full_probe_for_insertion(monkeypatch):
    controller, engine = _controller_with_engine()
    engine.cfg["opt_disc_presence_probe_seconds"] = 45

    seen = []

    def _fake_disc_present(probe_timeout_seconds=None):
        seen.append(probe_timeout_seconds)
        return True

    monkeypatch.setattr(controller, "_disc_present", _fake_disc_present)

    assert controller._wait_for_disc_state(
        want_present=True,
        timeout_seconds=10,
    ) is True
    assert seen == [45]


def test_wait_for_new_unique_disc_uses_fast_pre_swapped_probe(monkeypatch):
    controller, engine = _controller_with_engine()
    engine.cfg["opt_disc_presence_probe_seconds"] = 45

    seen = []

    monkeypatch.setattr(
        controller.gui, "show_info", lambda *_a, **_k: None, raising=False
    )

    def _fake_disc_present(probe_timeout_seconds=None):
        seen.append(probe_timeout_seconds)
        return False

    monkeypatch.setattr(controller, "_disc_present", _fake_disc_present)
    monkeypatch.setattr(
        controller,
        "_wait_for_disc_state",
        lambda *_a, **_k: False,
    )

    result = controller._wait_for_new_unique_disc(
        seen_fingerprints=set(),
        disc_number=2,
        total=4,
    )

    assert result is None
    assert seen == [8]


def test_preview_title_finds_nested_mkv_output(tmp_path, monkeypatch):
    controller, engine = _controller_with_engine(_engine_cfg(
        temp_folder=str(tmp_path)
    ))
    opened = []

    def fake_rip_preview(rip_path, title_id, preview_seconds, on_log):
        _ = (rip_path, title_id, preview_seconds, on_log)
        nested = Path(rip_path) / "subdir"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "sample.mkv").write_text("ok")
        return True

    monkeypatch.setattr(engine, "rip_preview_title", fake_rip_preview)
    monkeypatch.setattr(
        engine,
        "analyze_files",
        lambda files, _log: [(files[0], 40, 100)] if files else [],
    )
    monkeypatch.setattr("controller.legacy_compat.shutil.which", lambda _x: None)
    real_isfile = controller_module.os.path.isfile
    monkeypatch.setattr(
        "controller.legacy_compat.os.path.isfile",
        lambda path: False if "VideoLAN\\VLC\\vlc.exe" in str(path) else real_isfile(path),
    )
    monkeypatch.setattr(
        "controller.legacy_compat.subprocess.Popen",
        lambda args: opened.append(args),
    )
    monkeypatch.setattr(
        "controller.legacy_compat.os.startfile",
        lambda path: opened.append(path),
        raising=False,
    )
    monkeypatch.setattr("controller.legacy_compat.time.sleep", lambda _x: None)

    # Run thread target inline for deterministic test behavior.
    class _ImmediateThread:
        def __init__(self, target, daemon=True):
            self._target = target
            self._daemon = daemon
            self._alive = False

        def start(self):
            self._alive = True
            self._target()
            self._alive = False

        def join(self, timeout=None):
            _ = timeout

        def is_alive(self):
            return self._alive

    monkeypatch.setattr("controller.legacy_compat.threading.Thread", _ImmediateThread)

    controller.preview_title(0)

    assert not any(
        "Preview failed: no preview file found." in m
        for m in controller.gui.messages
    )
    assert any(
        ("VLC not found; opening in default player:" in m) or
        ("Preview opened in VLC:" in m)
        for m in controller.gui.messages
    )
    assert any("Preview candidate:" in m for m in controller.gui.messages)
    assert len(opened) == 1


def test_state_machine_invalid_transition_raises():
    sm = SessionStateMachine()
    sm.transition(SessionState.SCANNED)

    import pytest
    with pytest.raises(RuntimeError):
        sm.transition(SessionState.MOVED)


def test_handle_fallback_blocked_in_strict_mode():
    controller, engine = _controller_with_engine()
    engine.cfg["opt_strict_mode"] = True
    controller.gui.ask_yesno = lambda _prompt: True

    result = handle_fallback(controller, "mapping missing", lambda: [1])

    assert result is None


def test_select_largest_file_prefers_biggest(tmp_path):
    a = tmp_path / "a.mkv"
    b = tmp_path / "b.mkv"
    a.write_bytes(b"a" * 10)
    b.write_bytes(b"b" * 100)

    selected = select_largest_file([str(a), str(b)])

    assert selected == str(b)


def test_get_disc_size_silent_process_times_out(monkeypatch):
    _controller, engine = _controller_with_engine()
    engine.cfg["opt_disc_info_timeout_seconds"] = 30
    engine.cfg["opt_stall_timeout_seconds"] = 10

    class _SilentStdout:
        def readline(self):
            return ""

        def close(self):
            pass

    class _SilentProc:
        def __init__(self):
            self.stdout = _SilentStdout()
            self.terminated = False
            self.killed = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            _ = timeout
            return 0

    class _EmptyQueue:
        def get(self, timeout=None):
            _ = timeout
            raise queue.Empty

    proc = _SilentProc()
    monkeypatch.setattr(
        "engine.ripper_engine.subprocess.Popen",
        lambda *args, **kwargs: proc,
    )
    monkeypatch.setattr(
        "engine.ripper_engine.queue_module.Queue",
        lambda: _EmptyQueue(),
    )

    tick = {"t": 0.0}

    def _fake_time():
        tick["t"] += 11.0
        return tick["t"]

    monkeypatch.setattr("engine.ripper_engine.time.time", _fake_time)

    logs = []
    result = engine.get_disc_size(logs.append)

    assert result is None
    assert proc.terminated or proc.killed
    assert any("disc-size scan timed out" in m.lower() for m in logs)


def test_get_disc_size_records_fast_disc_identity(monkeypatch):
    _controller, engine = _controller_with_engine()

    class _FakeStdout:
        def close(self):
            pass

    class _FakeProc:
        def __init__(self):
            self.stdout = _FakeStdout()

        def poll(self):
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

        def wait(self, timeout=None):
            _ = timeout
            return 0

    class _Queue:
        def __init__(self):
            self._items = iter([
                'CINFO:2,0,"Demo Disc"',
                'CINFO:32,0,"VOL001"',
                'TINFO:0,11,0,"10.0 GB"',
                'TINFO:1,11,0,"5.0 GB"',
            ])

        def get(self, timeout=None):
            _ = timeout
            try:
                return next(self._items)
            except StopIteration:
                raise queue.Empty

    monkeypatch.setattr(
        "engine.ripper_engine.subprocess.Popen",
        lambda *args, **kwargs: _FakeProc(),
    )
    monkeypatch.setattr(
        "engine.ripper_engine.queue_module.Queue",
        lambda: _Queue(),
    )
    monkeypatch.setattr(engine, "_stdout_reader", lambda *_args, **_kwargs: None)

    total = engine.get_disc_size(lambda _msg: None)

    assert total == 15_000_000_000
    assert engine.last_disc_info["title"] == "Demo Disc"
    assert engine.last_disc_info["volume_id"] == "VOL001"
    assert engine.last_disc_info["title_count"] == 2
    assert engine.last_disc_info["size_signature"]


def test_scan_disc_sets_and_clears_current_process(monkeypatch):
    _controller, engine = _controller_with_engine()

    class _FakeStdout:
        def __init__(self):
            self._lines = iter([
                'CINFO:2,0,"Demo Disc"\n',
                'TINFO:0,2,0,"Main Feature"\n',
                'TINFO:0,9,0,"01:30:00"\n',
                'TINFO:0,8,0,"12"\n',
                'TINFO:0,11,0,"10.0 GB"\n',
                "",
            ])

        def readline(self):
            return next(self._lines, "")

    class _FakeProc:
        def __init__(self):
            self.stdout = _FakeStdout()
            self.returncode = 0

        def wait(self, timeout=None):
            _ = timeout
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    proc = _FakeProc()
    seen = {"during_scan": False}

    monkeypatch.setattr(
        "engine.ripper_engine.subprocess.Popen",
        lambda *args, **kwargs: proc,
    )

    def _progress(_value):
        seen["during_scan"] = seen["during_scan"] or (
            engine.current_process is proc
        )

    result = engine.scan_disc(lambda _msg: None, _progress)

    assert seen["during_scan"] is True
    assert engine.current_process is None
    assert result
    assert engine.last_disc_info["title_count"] == 1
    assert engine.last_disc_info["size_signature"]


def test_prompt_run_path_overrides_uses_defaults_when_declined():
    controller, engine = _controller_with_engine()
    engine.cfg["temp_folder"] = r"C:\TempDefault"
    engine.cfg["movies_folder"] = r"C:\MoviesDefault"

    controller.gui.ask_yesno = lambda _prompt: False

    resolved = controller._prompt_run_path_overrides([
        ("temp_folder", "Temp Folder"),
        ("movies_folder", "Movies Folder"),
    ])

    assert resolved["temp_folder"] == os.path.normpath(r"C:\TempDefault")
    assert resolved["movies_folder"] == os.path.normpath(r"C:\MoviesDefault")


def test_prompt_run_path_overrides_accepts_custom_existing_path(tmp_path):
    controller, engine = _controller_with_engine()
    default_temp = str(tmp_path / "default-temp")
    custom_temp = str(tmp_path / "custom-temp")
    os.makedirs(default_temp, exist_ok=True)
    os.makedirs(custom_temp, exist_ok=True)
    engine.cfg["temp_folder"] = default_temp

    controller.gui.ask_yesno = lambda _prompt: True

    responses = iter([custom_temp])

    def ask_input(_label, _prompt, default_value=""):
        _ = default_value
        return next(responses)

    controller.gui.ask_input = ask_input

    resolved = controller._prompt_run_path_overrides([
        ("temp_folder", "Temp Folder"),
    ])

    assert resolved["temp_folder"] == os.path.normpath(custom_temp)


def test_prompt_run_path_overrides_directory_picker_cancel_keeps_default(tmp_path):
    controller, engine = _controller_with_engine()
    default_temp = str(tmp_path / "default-temp")
    os.makedirs(default_temp, exist_ok=True)
    engine.cfg["temp_folder"] = default_temp

    controller.gui.ask_yesno = lambda _prompt: True
    controller.gui.ask_directory = (
        lambda _title, _prompt, initialdir="": None
    )

    resolved = controller._prompt_run_path_overrides([
        ("temp_folder", "Temp Folder"),
    ])

    assert resolved["temp_folder"] == os.path.normpath(default_temp)
    assert any(
        "Opening folder picker" in m
        for m in controller.gui.messages
    )
    assert any(
        "Folder picker closed/cancelled" in m
        for m in controller.gui.messages
    )
    assert any(
        "Custom folders set, continuing..." in m
        for m in controller.gui.messages
    )


def test_validate_paths_blocks_temp_equals_movies(tmp_path):
    controller, _engine = _controller_with_engine()
    same = str(tmp_path / "same")

    err = controller._validate_paths(same, movies=same, tv=None)

    assert err is not None
    assert "cannot be the same" in err.lower()


def test_split_extras_picker_cancel_returns_no_extras(monkeypatch):
    controller, engine = _controller_with_engine()
    engine.cfg["opt_extras_folder_mode"] = "split"

    monkeypatch.setattr(
        controller.gui,
        "show_extras_picker",
        lambda *_args, **_kwargs: None,
        raising=False,
    )

    titles_list = [
        ("main.mkv", 3600, 5000),
        ("extra1.mkv", 600, 500),
        ("extra2.mkv", 700, 600),
    ]

    extra_indices, bonus_indices = controller._ask_extras_selection(
        titles_list,
        main_indices=[0],
    )

    assert extra_indices == []
    assert bonus_indices == []


def test_validate_paths_blocks_non_writable(monkeypatch, tmp_path):
    controller, _engine = _controller_with_engine()
    target = str(tmp_path / "no-write")
    os.makedirs(target, exist_ok=True)

    monkeypatch.setattr("controller.controller.os.path.exists", lambda p: True)
    monkeypatch.setattr("controller.controller.os.access", lambda p, mode: False)

    err = controller._validate_paths(target, movies=None, tv=None)

    assert err is not None
    assert "not writable" in err.lower()


def test_get_path_requires_initialized_session_paths():
    controller, _engine = _controller_with_engine()
    controller.session_paths = None

    import pytest
    with pytest.raises(RuntimeError):
        controller.get_path("temp")


def test_session_paths_initialized_and_accessible(tmp_path):
    controller, _engine = _controller_with_engine()
    custom_temp = str(tmp_path / "temp")
    os.makedirs(custom_temp, exist_ok=True)

    controller._init_session_paths({"temp_folder": custom_temp})

    assert controller.get_path("temp") == os.path.normpath(custom_temp)


def test_safe_glob_timeout_returns_empty_and_logs_warning(monkeypatch):
    controller, _engine = _controller_with_engine()

    def slow_glob(*_args, **_kwargs):
        import time
        time.sleep(0.2)
        return ["x.mkv"]

    monkeypatch.setattr("controller.legacy_compat.glob.glob", slow_glob)

    matches = controller._safe_glob("*.mkv", recursive=False, timeout=0.01, context="test glob")

    assert matches == []
    assert any("test glob timed out" in m.lower() for m in controller.gui.messages)


def test_movie_run_with_no_resume_keeps_clean_defaults(tmp_path, monkeypatch):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)

    controller.gui.ask_yesno = lambda _prompt: False
    info_calls: list[tuple[str, str]] = []
    controller.gui.show_info = (
        lambda title, message: info_calls.append((title, message))
    )
    monkeypatch.setattr(controller, "check_resume", lambda *_args, **_kwargs: None)

    # Track what ask_movie_setup receives as defaults — the movie disc flow
    # should NOT pre-fill defaults from resume state.
    setup_defaults = {}
    events = []
    disc_titles = [
        {
            "id": 0,
            "name": "Main Feature",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
            "chapters": 20,
            "audio_tracks": [1],
            "subtitle_tracks": [1],
        },
    ]

    def tracking_movie_setup(**kwargs):
        setup_defaults.update(kwargs)
        events.append("movie_setup")
        return MovieSessionSetup(
            title="Chosen Movie",
            year="2024",
            edition="",
            metadata_provider="TMDB",
            metadata_id="",
            replace_existing=False,
            keep_raw=False,
            extras_mode="ask",
        )

    controller.gui.ask_movie_setup = tracking_movie_setup
    controller.gui.show_error = lambda *_args, **_kwargs: None

    writes = []

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(
        engine,
        "write_temp_metadata",
        lambda path, title, disc_number, **kwargs: (
            writes.append((path, title, disc_number, kwargs)),
            engine.abort(),
        )[-1],
    )
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(
        controller,
        "scan_with_retry",
        lambda: (events.append("scan"), disc_titles)[1],
    )

    controller.run_movie_disc()

    assert events.index("scan") < events.index("movie_setup")
    assert setup_defaults["default_title"] == ""
    assert setup_defaults["default_year"] == "0000"
    assert setup_defaults["default_edition"] == ""
    assert setup_defaults["default_metadata_provider"] == "TMDB"
    assert setup_defaults["default_metadata_id"] == ""
    assert setup_defaults["default_replace_existing"] is False
    assert writes
    new_rip_path = os.path.normpath(writes[0][0])
    assert new_rip_path.startswith(os.path.normpath(str(temp_root)))


def test_movie_run_resume_selection_restores_disc_number_and_defaults(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    resume_root = temp_root / "resume-movie"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)
    resume_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)

    info_calls: list[tuple[str, str]] = []
    controller.gui.ask_yesno = lambda _prompt: False
    controller.gui.show_info = (
        lambda title, message: info_calls.append((title, message))
    )
    controller.gui.show_error = lambda *_args, **_kwargs: None

    setup_defaults = {}
    disc_titles = [
        {
            "id": 0,
            "name": "Main Feature",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
            "chapters": 20,
            "audio_tracks": [1],
            "subtitle_tracks": [1],
        },
    ]
    writes = []
    updates = []

    def tracking_movie_setup(**kwargs):
        setup_defaults.update(kwargs)
        return MovieSessionSetup(
            title=kwargs["default_title"],
            year=kwargs["default_year"],
            edition=kwargs["default_edition"],
            metadata_provider=kwargs["default_metadata_provider"],
            metadata_id=kwargs["default_metadata_id"],
            replace_existing=kwargs["default_replace_existing"],
            keep_raw=False,
            extras_mode="ask",
        )

    controller.gui.ask_movie_setup = tracking_movie_setup

    monkeypatch.setattr(
        controller,
        "check_resume",
        lambda *_args, **_kwargs: {
            "path": str(resume_root),
            "name": "resume-movie",
            "meta": {
                "media_type": "movie",
                "disc_number": 2,
                "title": "Resume Movie",
                "year": "2024",
                "edition": "Director's Cut",
                "metadata_provider": "TMDB",
                "metadata_id": "tmdb:12",
                "replace_existing": True,
            },
        },
    )

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(
        engine,
        "write_temp_metadata",
        lambda path, title, disc_number, **kwargs: (
            writes.append((path, title, disc_number, kwargs)),
            engine.abort(),
        )[-1],
    )
    monkeypatch.setattr(
        engine,
        "update_temp_metadata",
        lambda path, **kwargs: updates.append((path, kwargs)),
    )
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)

    controller.run_movie_disc()

    assert ("Insert Disc", "Insert disc 2 and click OK when ready.") in info_calls
    assert setup_defaults["default_title"] == "Resume Movie"
    assert setup_defaults["default_year"] == "2024"
    assert setup_defaults["default_edition"] == "Director's Cut"
    assert setup_defaults["default_metadata_provider"] == "TMDB"
    assert setup_defaults["default_metadata_id"] == "tmdb:12"
    assert setup_defaults["default_replace_existing"] is True
    assert writes
    assert writes[0][2] == 2
    assert updates
    assert updates[0] == (str(resume_root), {"phase": "organized"})


def test_movie_run_resume_selection_derives_opendb_provider_from_metadata_id(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    resume_root = temp_root / "resume-movie"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)
    resume_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)

    setup_defaults = {}
    disc_titles = [
        {
            "id": 0,
            "name": "Main Feature",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
            "chapters": 20,
            "audio_tracks": [1],
            "subtitle_tracks": [1],
        },
    ]

    controller.gui.ask_yesno = lambda _prompt: False
    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.show_error = lambda *_args, **_kwargs: None

    def tracking_movie_setup(**kwargs):
        setup_defaults.update(kwargs)
        return MovieSessionSetup(
            title=kwargs["default_title"],
            year=kwargs["default_year"],
            edition=kwargs["default_edition"],
            metadata_provider=kwargs["default_metadata_provider"],
            metadata_id=kwargs["default_metadata_id"],
            replace_existing=kwargs["default_replace_existing"],
            keep_raw=False,
            extras_mode="ask",
        )

    controller.gui.ask_movie_setup = tracking_movie_setup

    monkeypatch.setattr(
        controller,
        "check_resume",
        lambda *_args, **_kwargs: {
            "path": str(resume_root),
            "name": "resume-movie-opendb",
            "meta": {
                "media_type": "movie",
                "disc_number": 2,
                "title": "Resume Movie",
                "year": "2024",
                "edition": "",
                "metadata_id": "imdb:tt1234567",
                "replace_existing": False,
            },
        },
    )

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(
        engine,
        "write_temp_metadata",
        lambda *_a, **_k: engine.abort(),
    )
    monkeypatch.setattr(engine, "update_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)

    controller.run_movie_disc()

    assert setup_defaults["default_metadata_provider"] == "OpenDB"
    assert setup_defaults["default_metadata_id"] == "imdb:tt1234567"


def test_movie_run_raw_resume_preserves_replace_existing_in_new_session_metadata(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    resume_root = temp_root / "resume-movie"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)
    resume_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)

    updates = []
    disc_titles = [
        {
            "id": 0,
            "name": "Main Feature",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
            "chapters": 20,
            "audio_tracks": [1],
            "subtitle_tracks": [1],
        },
    ]

    controller.gui.ask_yesno = lambda _prompt: False
    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.show_error = lambda *_args, **_kwargs: None
    controller.gui.ask_movie_setup = None

    def ask_input(label, _prompt, default_value=""):
        return default_value

    controller.gui.ask_input = ask_input

    monkeypatch.setattr(
        controller,
        "check_resume",
        lambda *_args, **_kwargs: {
            "path": str(resume_root),
            "name": "resume-movie-raw",
            "meta": {
                "media_type": "movie",
                "disc_number": 2,
                "title": "Resume Movie",
                "year": "2024",
                "edition": "Director's Cut",
                "metadata_provider": "OpenDB",
                "metadata_id": "opendb:1396",
                "replace_existing": True,
            },
        },
    )

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(
        engine,
        "write_temp_metadata",
        lambda *_a, **_k: engine.abort(),
    )
    monkeypatch.setattr(
        engine,
        "update_temp_metadata",
        lambda path, **kwargs: updates.append((path, kwargs)),
    )
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)

    controller.run_movie_disc()

    assert updates[-1][1]["metadata_provider"] == "OpenDB"
    assert updates[-1][1]["metadata_id"] == "opendb:1396"
    assert updates[-1][1]["replace_existing"] is True


def test_movie_run_movie_setup_cancel_does_not_fall_through_to_done(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)

    info_calls: list[tuple[str, str]] = []
    controller.gui.show_info = (
        lambda title, message: info_calls.append((title, message))
    )
    controller.gui.show_error = lambda *_args, **_kwargs: None
    controller.gui.ask_yesno = lambda _prompt: False
    controller.gui.ask_movie_setup = lambda **_kw: None

    disc_titles = [
        {
            "id": 0,
            "name": "Main Feature",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
            "chapters": 20,
            "audio_tracks": [1],
            "subtitle_tracks": [1],
        },
    ]

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)
    monkeypatch.setattr(
        engine,
        "write_temp_metadata",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("write_temp_metadata should not be called after cancel")
        ),
    )

    controller.run_movie_disc()

    assert ("Insert Disc", "Insert disc 1 and click OK when ready.") in info_calls
    assert not any(
        title == "Done" and message == "Session complete!"
        for title, message in info_calls
    )
    assert controller.sm.state == SessionState.INIT


def test_movie_run_rip_confirm_cancel_no_retry_does_not_mark_session_complete(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["opt_confirm_before_rip"] = True

    info_calls: list[tuple[str, str]] = []
    controller.gui.show_info = (
        lambda title, message: info_calls.append((title, message))
    )
    controller.gui.show_error = lambda *_args, **_kwargs: None

    def _ask_yesno(prompt):
        if prompt.startswith("Rip "):
            return False
        if prompt == "Try again?":
            return False
        return False

    controller.gui.ask_yesno = _ask_yesno
    controller.gui.ask_movie_setup = lambda **_kw: MovieSessionSetup(
        title="Chosen Movie",
        year="2024",
        edition="",
        metadata_provider="TMDB",
        metadata_id="",
        replace_existing=False,
        keep_raw=False,
        extras_mode="ask",
    )

    disc_titles = [
        {
            "id": 0,
            "name": "Main Feature",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
            "chapters": 20,
            "audio_tracks": [1],
            "subtitle_tracks": [1],
        },
    ]

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "write_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "update_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)
    monkeypatch.setattr(
        controller,
        "_open_manual_disc_picker",
        lambda *_args, **_kwargs: ([0], 4_000_000_000),
    )
    monkeypatch.setattr(
        engine,
        "run_job",
        lambda _job: (_ for _ in ()).throw(
            AssertionError("run_job should not be called after rip confirm cancel")
        ),
    )

    controller.run_movie_disc()

    assert not any(
        title == "Done" and message == "Session complete!"
        for title, message in info_calls
    )
    assert controller.session_report == ["Rip cancelled by user."]
    assert controller.sm.state == SessionState.INIT


def test_movie_run_manual_picker_cancel_reports_explicit_stop_reason(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["opt_confirm_before_rip"] = False

    info_calls: list[tuple[str, str]] = []
    controller.gui.show_info = (
        lambda title, message: info_calls.append((title, message))
    )
    controller.gui.show_error = lambda *_args, **_kwargs: None
    controller.gui.ask_yesno = lambda _prompt: False
    controller.gui.ask_movie_setup = lambda **_kw: MovieSessionSetup(
        title="Chosen Movie",
        year="2024",
        edition="",
        metadata_provider="TMDB",
        metadata_id="",
        replace_existing=False,
        keep_raw=False,
        extras_mode="ask",
    )

    disc_titles = [
        {
            "id": 0,
            "name": "Main Feature",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
            "chapters": 20,
            "audio_tracks": [1],
            "subtitle_tracks": [1],
        },
    ]

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "write_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "update_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)
    monkeypatch.setattr(
        controller,
        "_open_manual_disc_picker",
        lambda *_args, **_kwargs: (None, 0),
    )
    monkeypatch.setattr(
        engine,
        "run_job",
        lambda _job: (_ for _ in ()).throw(
            AssertionError("run_job should not be called after picker cancel")
        ),
    )

    controller.run_movie_disc()

    assert not any(
        title == "Done" and message == "Session complete!"
        for title, message in info_calls
    )
    assert controller.session_report == ["Cancelled before title selection."]
    assert controller.sm.state == SessionState.INIT


def test_run_movie_disc_movie_setup_normalizes_numeric_metadata_id_with_provider(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)

    disc_titles = [
        {
            "id": 0,
            "name": "Main Feature",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
            "chapters": 20,
            "audio_tracks": [1],
            "subtitle_tracks": [1],
        },
    ]
    writes = []
    updates = []

    controller.gui.ask_yesno = lambda _prompt: False
    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_movie_setup = lambda **_kwargs: MovieSessionSetup(
        title="Chosen Movie",
        year="2024",
        edition="",
        metadata_provider="OpenDB",
        metadata_id="1396",
        replace_existing=True,
        keep_raw=False,
        extras_mode="ask",
    )

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(
        engine,
        "write_temp_metadata",
        lambda path, title, disc_number, **kwargs: (
            writes.append((path, title, disc_number, kwargs)),
            engine.abort(),
        )[-1],
    )
    monkeypatch.setattr(
        engine,
        "update_temp_metadata",
        lambda path, **kwargs: updates.append((path, kwargs)),
    )
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)

    controller.run_movie_disc()

    assert writes
    assert writes[0][3]["dest_folder"] == str(
        movies_root / "Chosen Movie (2024) [opendbid-1396]"
    )
    assert updates
    assert updates[-1][1]["metadata_provider"] == "OpenDB"
    assert updates[-1][1]["metadata_id"] == "opendb:1396"
    assert updates[-1][1]["replace_existing"] is True


def test_run_movie_disc_raw_metadata_id_is_persisted(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)

    disc_titles = [
        {
            "id": 0,
            "name": "Main Feature",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
        },
    ]
    updates = []

    def ask_input(label, _prompt, default_value=""):
        if label == "Title":
            return "Chosen Movie"
        if label == "Year":
            return "2024"
        if label == "Edition":
            return ""
        if label == "Metadata ID":
            return "tt1234567"
        return default_value

    controller.gui.ask_yesno = lambda _prompt: False
    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_input = ask_input
    controller.gui.ask_movie_setup = None

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(
        engine,
        "write_temp_metadata",
        lambda *_a, **_k: engine.abort(),
    )
    monkeypatch.setattr(
        engine,
        "update_temp_metadata",
        lambda path, **kwargs: updates.append((path, kwargs)),
    )
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)

    controller.run_movie_disc()

    assert updates
    assert updates[-1][1]["metadata_id"] == "imdb:tt1234567"


def test_run_movie_disc_raw_multi_disc_prefills_metadata_id_on_later_discs(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["opt_confirm_before_rip"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)

    disc_titles = [
        {
            "id": 0,
            "name": "Main Feature",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
        },
    ]
    metadata_defaults: list[str] = []
    prompt_state = {"disc": 0}
    another_disc_prompts = {"count": 0}
    write_calls = {"count": 0}

    def ask_input(label, _prompt, default_value=""):
        if label == "Title":
            prompt_state["disc"] += 1
            return "Chosen Movie" if prompt_state["disc"] == 1 else default_value
        if label == "Year":
            return "2024" if prompt_state["disc"] == 1 else default_value
        if label == "Edition":
            return default_value
        if label == "Metadata ID":
            metadata_defaults.append(default_value)
            return "tt1234567" if prompt_state["disc"] == 1 else default_value
        return default_value

    def ask_yesno(prompt):
        if prompt == "Another disc in this set?":
            another_disc_prompts["count"] += 1
            return another_disc_prompts["count"] == 1
        return False

    controller.gui.ask_yesno = ask_yesno
    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.show_error = lambda *_args, **_kwargs: None
    controller.gui.ask_input = ask_input
    controller.gui.ask_movie_setup = None

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)

    def fake_write_temp_metadata(*_args, **_kwargs):
        write_calls["count"] += 1
        if write_calls["count"] == 2:
            engine.abort()

    monkeypatch.setattr(engine, "write_temp_metadata", fake_write_temp_metadata)
    monkeypatch.setattr(engine, "update_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)
    monkeypatch.setattr(
        controller,
        "_open_manual_disc_picker",
        lambda *_args, **_kwargs: ([0], 4_000_000_000),
    )
    monkeypatch.setattr(
        engine,
        "run_job",
        lambda _job: SimpleNamespace(success=True, errors=[]),
    )
    monkeypatch.setattr(
        controller,
        "_normalize_rip_result",
        lambda *_a, **_k: (True, [str(temp_root / "main.mkv")]),
    )
    monkeypatch.setattr(
        controller,
        "_stabilize_ripped_files",
        lambda *_a, **_k: (True, False),
    )
    monkeypatch.setattr(
        controller,
        "_verify_expected_sizes",
        lambda *_a, **_k: ("pass", "ok"),
    )
    monkeypatch.setattr(
        controller,
        "_verify_container_integrity",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(
        engine,
        "analyze_files",
        lambda *_a, **_k: [(str(temp_root / "main.mkv"), 7200.0, 4000.0)],
    )
    monkeypatch.setattr(controller, "_select_and_move", lambda *_a, **_k: True)

    controller.run_movie_disc()

    assert metadata_defaults == ["", "imdb:tt1234567"]
    assert write_calls["count"] == 2


def test_run_movie_disc_raw_multi_disc_prefills_title_on_later_discs(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["opt_confirm_before_rip"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)

    disc_titles = [
        {
            "id": 0,
            "name": "Main Feature",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
        },
    ]
    title_defaults: list[str] = []
    prompt_state = {"disc": 0}
    another_disc_prompts = {"count": 0}
    write_calls = {"count": 0}

    def ask_input(label, _prompt, default_value=""):
        if label == "Title":
            title_defaults.append(default_value)
            prompt_state["disc"] += 1
            return "Chosen Movie" if prompt_state["disc"] == 1 else default_value
        if label == "Year":
            return "2024" if prompt_state["disc"] == 1 else default_value
        if label == "Edition":
            return ""
        if label == "Metadata ID":
            return ""
        return default_value

    def ask_yesno(prompt):
        if prompt == "Another disc in this set?":
            another_disc_prompts["count"] += 1
            return another_disc_prompts["count"] == 1
        return False

    controller.gui.ask_yesno = ask_yesno
    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.show_error = lambda *_args, **_kwargs: None
    controller.gui.ask_input = ask_input
    controller.gui.ask_movie_setup = None

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)

    def fake_write_temp_metadata(*_args, **_kwargs):
        write_calls["count"] += 1
        if write_calls["count"] == 2:
            engine.abort()

    monkeypatch.setattr(engine, "write_temp_metadata", fake_write_temp_metadata)
    monkeypatch.setattr(engine, "update_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)
    monkeypatch.setattr(
        controller,
        "_open_manual_disc_picker",
        lambda *_args, **_kwargs: ([0], 4_000_000_000),
    )
    monkeypatch.setattr(
        engine,
        "run_job",
        lambda _job: SimpleNamespace(success=True, errors=[]),
    )
    monkeypatch.setattr(
        controller,
        "_normalize_rip_result",
        lambda *_a, **_k: (True, [str(temp_root / "main.mkv")]),
    )
    monkeypatch.setattr(
        controller,
        "_stabilize_ripped_files",
        lambda *_a, **_k: (True, False),
    )
    monkeypatch.setattr(
        controller,
        "_verify_expected_sizes",
        lambda *_a, **_k: ("pass", "ok"),
    )
    monkeypatch.setattr(
        controller,
        "_verify_container_integrity",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(
        engine,
        "analyze_files",
        lambda *_a, **_k: [(str(temp_root / "main.mkv"), 7200.0, 4000.0)],
    )
    monkeypatch.setattr(controller, "_select_and_move", lambda *_a, **_k: True)

    controller.run_movie_disc()

    assert title_defaults == ["", "Chosen Movie"]
    assert write_calls["count"] == 2


def test_run_movie_disc_movie_setup_cleans_edition_in_destination_folder(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)

    disc_titles = [
        {
            "id": 0,
            "name": "Main Feature",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
        },
    ]
    writes = []

    controller.gui.ask_yesno = lambda _prompt: False
    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_movie_setup = lambda **_kwargs: MovieSessionSetup(
        title="Chosen Movie",
        year="2024",
        edition='Director: Cut / IMAX?',
        metadata_provider="TMDB",
        metadata_id="",
        replace_existing=False,
        keep_raw=False,
        extras_mode="ask",
    )

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(
        engine,
        "write_temp_metadata",
        lambda path, title, disc_number, **kwargs: (
            writes.append((path, title, disc_number, kwargs)),
            engine.abort(),
        )[-1],
    )
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)

    controller.run_movie_disc()

    assert writes
    assert writes[0][3]["dest_folder"] == str(
        movies_root / "Chosen Movie (2024) - Director Cut  IMAX"
    )


def test_build_tv_setup_defaults_prefers_current_then_session_then_library():
    controller, _engine = _controller_with_engine()

    defaults = controller._build_tv_setup_defaults(
        current_setup={
            "default_title": "Current Show",
            "default_episode_mapping": "manual",
            "default_replace_existing": False,
        },
        session_meta={
            "year": "2001",
            "disc_number": 3,
            "metadata_provider": "OpenDB",
            "metadata_id": "1396",
            "multi_episode": "merge",
            "specials": "season0",
            "replace_existing": True,
        },
        library_root=r"C:\TV\Library Show",
        library_state={2: [1, 2], 5: [1]},
    )

    assert defaults == {
        "default_title": "Current Show",
        "default_year": "2001",
        "default_season": "5",
        "default_starting_disc": "3",
        "default_metadata_provider": "OpenDB",
        "default_metadata_id": "1396",
        "default_episode_mapping": "manual",
        "default_multi_episode": "merge",
        "default_specials": "season0",
        "default_replace_existing": False,
    }


def test_tv_setup_defaults_from_setup_preserves_season_zero():
    controller, _engine = _controller_with_engine()

    class _Setup:
        title = "Example Show"
        year = ""
        season = 0
        starting_disc = 1
        metadata_provider = "TMDB"
        metadata_id = ""
        episode_mapping = "auto"
        multi_episode = "auto"
        specials = "season0"
        replace_existing = False

    defaults = controller._tv_setup_defaults_from_setup(_Setup())

    assert defaults["default_season"] == "0"
    assert defaults["default_specials"] == "season0"


def test_tv_run_with_no_resume_keeps_clean_defaults(tmp_path, monkeypatch):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    tv_root = tmp_path / "tv"
    temp_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["tv_folder"] = str(tv_root)

    monkeypatch.setattr(controller, "check_resume", lambda *_args, **_kwargs: None)

    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_yesno = lambda _prompt: False

    # Track what ask_tv_setup receives as defaults — the TV disc flow
    # should NOT pre-fill defaults from resume state.
    setup_defaults = {}
    events = []
    disc_titles = [
        {
            "id": 0,
            "name": "Episode Block",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
            "chapters": 20,
            "audio_tracks": [1],
            "subtitle_tracks": [1],
        },
    ]

    def tracking_tv_setup(**kwargs):
        setup_defaults.update(kwargs)
        events.append("tv_setup")
        return TVSessionSetup(
            title="Chosen Show",
            year="",
            season=1,
            starting_disc=1,
            episode_mapping="auto",
            metadata_provider="TMDB",
            metadata_id="",
            multi_episode="auto",
            specials="ask",
            replace_existing=False,
            keep_raw=False,
        )

    controller.gui.ask_tv_setup = tracking_tv_setup

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(
        engine,
        "write_temp_metadata",
        lambda *_a, **_k: engine.abort(),
    )
    monkeypatch.setattr(engine, "update_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(
        controller,
        "scan_with_retry",
        lambda: (events.append("scan"), disc_titles)[1],
    )

    controller.run_tv_disc()

    assert events.index("tv_setup") < events.index("scan")
    assert setup_defaults["default_title"] == ""
    assert setup_defaults["default_year"] == ""
    assert setup_defaults["default_season"] == "1"
    assert setup_defaults["default_starting_disc"] == "1"
    assert setup_defaults["default_metadata_provider"] == "TMDB"
    assert setup_defaults["default_metadata_id"] == ""
    assert setup_defaults["default_episode_mapping"] == "auto"
    assert setup_defaults["default_multi_episode"] == "auto"
    assert setup_defaults["default_specials"] == "ask"
    assert setup_defaults["default_replace_existing"] is False


def test_tv_run_resume_selection_restores_disc_number_defaults_and_marks_resume(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    tv_root = tmp_path / "tv"
    resume_root = temp_root / "Resume Show" / "Season 02" / "Disc_resume"
    temp_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)
    resume_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["tv_folder"] = str(tv_root)

    info_calls: list[tuple[str, str]] = []
    controller.gui.ask_yesno = lambda _prompt: False
    controller.gui.show_info = (
        lambda title, message: info_calls.append((title, message))
    )

    setup_defaults = {}
    disc_titles = [
        {
            "id": 0,
            "name": "Episode Block",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
            "chapters": 20,
            "audio_tracks": [1],
            "subtitle_tracks": [1],
        },
    ]
    writes = []
    updates = []

    def tracking_tv_setup(**kwargs):
        setup_defaults.update(kwargs)
        return TVSessionSetup(
            title=kwargs["default_title"],
            year=kwargs["default_year"],
            season=int(kwargs["default_season"]),
            starting_disc=int(kwargs["default_starting_disc"]),
            episode_mapping=kwargs["default_episode_mapping"],
            metadata_provider=kwargs["default_metadata_provider"],
            metadata_id=kwargs["default_metadata_id"],
            multi_episode=kwargs["default_multi_episode"],
            specials=kwargs["default_specials"],
            replace_existing=kwargs["default_replace_existing"],
            keep_raw=False,
        )

    controller.gui.ask_tv_setup = tracking_tv_setup

    monkeypatch.setattr(
        controller,
        "check_resume",
        lambda *_args, **_kwargs: {
            "path": str(resume_root),
            "name": "resume-tv",
            "meta": {
                "media_type": "tv",
                "disc_number": 2,
                "title": "Resume Show",
                "year": "2004",
                "season": 2,
                "metadata_provider": "OpenDB",
                "metadata_id": "opendb:1396",
                "episode_mapping": "manual",
                "multi_episode": "merge",
                "specials": "season0",
                "replace_existing": True,
            },
        },
    )

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(
        engine,
        "write_temp_metadata",
        lambda path, title, disc_number, **kwargs: (
            writes.append((path, title, disc_number, kwargs)),
            engine.abort(),
        )[-1],
    )
    monkeypatch.setattr(
        engine,
        "update_temp_metadata",
        lambda path, **kwargs: updates.append((path, kwargs)),
    )
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)

    controller.run_tv_disc()

    assert ("Insert Disc", "Insert disc 2 and click OK when ready.") in info_calls
    assert setup_defaults["default_title"] == "Resume Show"
    assert setup_defaults["default_year"] == "2004"
    assert setup_defaults["default_season"] == "2"
    assert setup_defaults["default_starting_disc"] == "2"
    assert setup_defaults["default_metadata_provider"] == "OpenDB"
    assert setup_defaults["default_metadata_id"] == "opendb:1396"
    assert setup_defaults["default_episode_mapping"] == "manual"
    assert setup_defaults["default_multi_episode"] == "merge"
    assert setup_defaults["default_specials"] == "season0"
    assert setup_defaults["default_replace_existing"] is True
    assert writes
    assert writes[0][2] == 2
    assert os.path.normpath(writes[0][0]).startswith(
        os.path.normpath(str(temp_root / "Resume Show" / "Season 02"))
    )
    assert updates
    assert updates[0] == (str(resume_root), {"phase": "organized"})


def test_tv_run_resume_selection_derives_opendb_provider_from_metadata_id(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    tv_root = tmp_path / "tv"
    resume_root = temp_root / "Resume Show" / "Season 02" / "Disc_resume"
    temp_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)
    resume_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["tv_folder"] = str(tv_root)

    setup_defaults = {}
    disc_titles = [
        {
            "id": 0,
            "name": "Episode Block",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
            "chapters": 20,
            "audio_tracks": [1],
            "subtitle_tracks": [1],
        },
    ]

    controller.gui.ask_yesno = lambda _prompt: False
    controller.gui.show_info = lambda *_args, **_kwargs: None

    def tracking_tv_setup(**kwargs):
        setup_defaults.update(kwargs)
        return TVSessionSetup(
            title=kwargs["default_title"],
            year=kwargs["default_year"],
            season=int(kwargs["default_season"]),
            starting_disc=int(kwargs["default_starting_disc"]),
            episode_mapping=kwargs["default_episode_mapping"],
            metadata_provider=kwargs["default_metadata_provider"],
            metadata_id=kwargs["default_metadata_id"],
            multi_episode=kwargs["default_multi_episode"],
            specials=kwargs["default_specials"],
            replace_existing=kwargs["default_replace_existing"],
            keep_raw=False,
        )

    controller.gui.ask_tv_setup = tracking_tv_setup

    monkeypatch.setattr(
        controller,
        "check_resume",
        lambda *_args, **_kwargs: {
            "path": str(resume_root),
            "name": "resume-tv-opendb",
            "meta": {
                "media_type": "tv",
                "disc_number": 2,
                "title": "Resume Show",
                "year": "2004",
                "season": 2,
                "metadata_id": "tvdb:79168",
                "episode_mapping": "manual",
                "multi_episode": "merge",
                "specials": "season0",
                "replace_existing": True,
            },
        },
    )

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(
        engine,
        "write_temp_metadata",
        lambda *_a, **_k: engine.abort(),
    )
    monkeypatch.setattr(engine, "update_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)

    controller.run_tv_disc()

    assert setup_defaults["default_metadata_provider"] == "OpenDB"
    assert setup_defaults["default_metadata_id"] == "tvdb:79168"


def test_tv_run_raw_resume_selection_prefills_metadata_id_and_marks_resume(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    tv_root = tmp_path / "tv"
    resume_root = temp_root / "Resume Show" / "Season 02" / "Disc_resume"
    temp_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)
    resume_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["tv_folder"] = str(tv_root)

    info_calls: list[tuple[str, str]] = []
    prompt_defaults: dict[str, list[str]] = {
        "Title": [],
        "Metadata ID": [],
        "Season": [],
    }
    writes = []
    updates = []
    disc_titles = [
        {
            "id": 0,
            "name": "Episode Block",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
            "chapters": 20,
            "audio_tracks": [1],
            "subtitle_tracks": [1],
        },
    ]

    controller.gui.show_info = (
        lambda title, message: info_calls.append((title, message))
    )
    controller.gui.ask_yesno = lambda _prompt: False
    controller.gui.ask_tv_setup = None

    def ask_input(label, _prompt, default_value=""):
        if label in prompt_defaults:
            prompt_defaults[label].append(str(default_value))
        return default_value

    controller.gui.ask_input = ask_input

    monkeypatch.setattr(
        controller,
        "check_resume",
        lambda *_args, **_kwargs: {
            "path": str(resume_root),
            "name": "resume-tv-raw",
            "meta": {
                "media_type": "tv",
                "disc_number": 2,
                "title": "Resume Show",
                "season": 2,
                "metadata_id": "tvdb:79168",
            },
        },
    )

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(
        engine,
        "write_temp_metadata",
        lambda path, title, disc_number, **kwargs: (
            writes.append((path, title, disc_number, kwargs)),
            engine.abort(),
        )[-1],
    )
    monkeypatch.setattr(
        engine,
        "update_temp_metadata",
        lambda path, **kwargs: updates.append((path, kwargs)),
    )
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)

    controller.run_tv_disc()

    assert ("Insert Disc", "Insert disc 2 and click OK when ready.") in info_calls
    assert prompt_defaults["Title"] == ["Resume Show"]
    assert prompt_defaults["Metadata ID"] == ["tvdb:79168"]
    assert prompt_defaults["Season"] == ["2"]
    assert writes
    assert writes[0][1] == "Resume Show"
    assert writes[0][2] == 2
    assert writes[0][3]["season"] == 2
    assert "[tvdbid-79168]" in writes[0][3]["dest_folder"]
    assert updates
    assert updates[0] == (str(resume_root), {"phase": "organized"})


def test_tv_run_raw_resume_selection_uses_saved_provider_hint_for_numeric_metadata(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    tv_root = tmp_path / "tv"
    resume_root = temp_root / "Resume Show" / "Season 02" / "Disc_resume"
    temp_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)
    resume_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["tv_folder"] = str(tv_root)

    prompt_defaults: dict[str, list[str]] = {
        "Title": [],
        "Metadata ID": [],
        "Season": [],
    }
    writes = []
    updates = []
    disc_titles = [
        {
            "id": 0,
            "name": "Episode Block",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
            "chapters": 20,
            "audio_tracks": [1],
            "subtitle_tracks": [1],
        },
    ]

    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_yesno = lambda _prompt: False
    controller.gui.ask_tv_setup = None

    def ask_input(label, _prompt, default_value=""):
        if label in prompt_defaults:
            prompt_defaults[label].append(str(default_value))
        if label == "Metadata ID":
            return "1396"
        return default_value

    controller.gui.ask_input = ask_input

    monkeypatch.setattr(
        controller,
        "check_resume",
        lambda *_args, **_kwargs: {
            "path": str(resume_root),
            "name": "resume-tv-raw-provider",
            "meta": {
                "media_type": "tv",
                "disc_number": 2,
                "title": "Resume Show",
                "season": 2,
                "metadata_provider": "OpenDB",
                "metadata_id": "opendb:1396",
            },
        },
    )

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(
        engine,
        "write_temp_metadata",
        lambda path, title, disc_number, **kwargs: (
            writes.append((path, title, disc_number, kwargs)),
            engine.abort(),
        )[-1],
    )
    monkeypatch.setattr(
        engine,
        "update_temp_metadata",
        lambda path, **kwargs: updates.append((path, kwargs)),
    )
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)

    controller.run_tv_disc()

    assert prompt_defaults["Metadata ID"] == ["opendb:1396"]
    assert writes
    assert "[opendbid-1396]" in writes[0][3]["dest_folder"]
    assert updates[-1][1]["metadata_provider"] == "OpenDB"
    assert updates[-1][1]["metadata_id"] == "opendb:1396"


def test_tv_run_raw_resume_preserves_extended_settings_in_new_session_metadata(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    tv_root = tmp_path / "tv"
    resume_root = temp_root / "Resume Show" / "Season 02" / "Disc_resume"
    temp_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)
    resume_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["tv_folder"] = str(tv_root)

    updates = []
    disc_titles = [
        {
            "id": 0,
            "name": "Episode Block",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
            "chapters": 20,
            "audio_tracks": [1],
            "subtitle_tracks": [1],
        },
    ]

    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_yesno = lambda _prompt: False
    controller.gui.ask_tv_setup = None

    def ask_input(label, _prompt, default_value=""):
        return default_value

    controller.gui.ask_input = ask_input

    monkeypatch.setattr(
        controller,
        "check_resume",
        lambda *_args, **_kwargs: {
            "path": str(resume_root),
            "name": "resume-tv-raw-extended",
            "meta": {
                "media_type": "tv",
                "disc_number": 2,
                "title": "Resume Show",
                "season": 2,
                "metadata_provider": "OpenDB",
                "metadata_id": "opendb:1396",
                "starting_disc": 2,
                "episode_mapping": "manual",
                "multi_episode": "merge",
                "specials": "season0",
                "replace_existing": True,
            },
        },
    )

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(
        engine,
        "write_temp_metadata",
        lambda *_a, **_k: engine.abort(),
    )
    monkeypatch.setattr(
        engine,
        "update_temp_metadata",
        lambda path, **kwargs: updates.append((path, kwargs)),
    )
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)

    controller.run_tv_disc()

    assert updates[-1][1]["metadata_provider"] == "OpenDB"
    assert updates[-1][1]["metadata_id"] == "opendb:1396"
    assert updates[-1][1]["starting_disc"] == 2
    assert updates[-1][1]["episode_mapping"] == "manual"
    assert updates[-1][1]["multi_episode"] == "merge"
    assert updates[-1][1]["specials"] == "season0"
    assert updates[-1][1]["replace_existing"] is True


def test_tv_run_existing_folder_prefills_library_defaults_and_extended_fields(
    tmp_path, monkeypatch
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    tv_root = tmp_path / "tv"
    library_root = tv_root / "Planet Earth"
    season_two = library_root / "Season 02"
    temp_root.mkdir(parents=True, exist_ok=True)
    season_two.mkdir(parents=True, exist_ok=True)
    (season_two / "Planet Earth - S02E01.mkv").write_text("", encoding="utf-8")

    disc_titles = [
        {
            "id": 0,
            "name": "Episode Block",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
        },
    ]

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["tv_folder"] = str(tv_root)

    def ask_yesno(prompt):
        if "Use custom folders for this run?" in prompt:
            return False
        if "Continue an existing show folder?" in prompt:
            return True
        raise AssertionError(f"Unexpected yes/no prompt: {prompt}")

    controller.gui.ask_yesno = ask_yesno
    controller.gui.ask_directory = (
        lambda _title, _prompt, initialdir="": str(library_root)
    )
    controller.gui.show_info = lambda *_args, **_kwargs: None

    setup_defaults = {}

    def tracking_tv_setup(**kwargs):
        setup_defaults.update(kwargs)
        return TVSessionSetup(
            title="Planet Earth",
            year="",
            season=2,
            starting_disc=1,
            episode_mapping="auto",
            metadata_provider="TMDB",
            metadata_id="",
            multi_episode="auto",
            specials="ask",
            replace_existing=False,
            keep_raw=False,
        )

    controller.gui.ask_tv_setup = tracking_tv_setup

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(
        engine,
        "write_temp_metadata",
        lambda *_a, **_k: engine.abort(),
    )
    monkeypatch.setattr(engine, "update_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)

    controller.run_tv_disc()

    assert setup_defaults["default_title"] == "Planet Earth"
    assert setup_defaults["default_year"] == ""
    assert setup_defaults["default_season"] == "2"
    assert setup_defaults["default_starting_disc"] == "1"
    assert setup_defaults["default_metadata_provider"] == "TMDB"
    assert setup_defaults["default_metadata_id"] == ""
    assert setup_defaults["default_episode_mapping"] == "auto"
    assert setup_defaults["default_multi_episode"] == "auto"
    assert setup_defaults["default_specials"] == "ask"
    assert setup_defaults["default_replace_existing"] is False


def test_run_tv_disc_tv_setup_applies_starting_disc_and_skips_raw_prompts(tmp_path, monkeypatch):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    tv_root = tmp_path / "tv"
    temp_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["tv_folder"] = str(tv_root)

    disc_titles = [
        {
            "id": 0,
            "name": "Episode Block",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
            "chapters": 20,
            "audio_tracks": [1],
            "subtitle_tracks": [1],
        },
    ]
    writes = []

    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_yesno = lambda _prompt: False
    controller.gui.ask_input = lambda *_args, **_kwargs: pytest.fail(
        "raw TV prompts should be skipped when ask_tv_setup is available"
    )
    controller.gui.ask_tv_setup = lambda **_kwargs: TVSessionSetup(
        title="Example Show",
        year="2004",
        season=2,
        starting_disc=3,
        episode_mapping="auto",
        metadata_provider="TMDB",
        metadata_id="tmdb:12345",
        multi_episode="auto",
        specials="ask",
        replace_existing=False,
        keep_raw=False,
    )

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(
        engine,
        "write_temp_metadata",
        lambda path, title, disc_number, **kwargs: (
            writes.append((path, title, disc_number, kwargs)),
            engine.abort(),
        )[-1],
    )
    monkeypatch.setattr(engine, "update_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)

    controller.run_tv_disc()

    assert writes
    assert writes[0][1] == "Example Show"
    assert writes[0][2] == 3
    assert writes[0][3]["season"] == 2
    assert "Season 02" in writes[0][0]


def test_run_tv_disc_tv_setup_applies_starting_disc_to_first_insert_prompt(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    tv_root = tmp_path / "tv"
    temp_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["tv_folder"] = str(tv_root)

    disc_titles = [
        {
            "id": 0,
            "name": "Episode Block",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
            "chapters": 20,
            "audio_tracks": [1],
            "subtitle_tracks": [1],
        },
    ]
    info_calls = []
    writes = []

    controller.gui.show_info = lambda title, msg: info_calls.append((title, msg))
    controller.gui.ask_yesno = lambda _prompt: False
    controller.gui.ask_input = lambda *_args, **_kwargs: pytest.fail(
        "raw TV prompts should be skipped when ask_tv_setup is available"
    )
    controller.gui.ask_tv_setup = lambda **_kwargs: TVSessionSetup(
        title="Example Show",
        year="2004",
        season=2,
        starting_disc=3,
        episode_mapping="auto",
        metadata_provider="TMDB",
        metadata_id="tmdb:12345",
        multi_episode="auto",
        specials="ask",
        replace_existing=False,
        keep_raw=False,
    )

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(
        engine,
        "write_temp_metadata",
        lambda path, title, disc_number, **kwargs: (
            writes.append((path, title, disc_number, kwargs)),
            engine.abort(),
        )[-1],
    )
    monkeypatch.setattr(engine, "update_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)

    controller.run_tv_disc()

    assert info_calls
    assert info_calls[0][0] == "Insert Disc"
    assert "Insert disc 3" in info_calls[0][1]
    assert writes
    assert writes[0][2] == 3


def test_run_tv_disc_tv_setup_skips_raw_season_prompt_on_later_discs(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    tv_root = tmp_path / "tv"
    temp_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_confirm_before_rip"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["tv_folder"] = str(tv_root)

    disc_titles = [
        {
            "id": 0,
            "name": "Episode Block",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
            "chapters": 20,
            "audio_tracks": [1],
            "subtitle_tracks": [1],
        },
    ]
    writes = []
    another_disc_prompts = {"count": 0}
    tv_setup_calls = {"count": 0}

    controller.gui.show_info = lambda *_args, **_kwargs: None

    def ask_yesno(prompt):
        if prompt.startswith("Continue an existing show folder?"):
            return False
        if prompt == "Another disc in this set?":
            another_disc_prompts["count"] += 1
            return another_disc_prompts["count"] == 1
        return False

    controller.gui.ask_yesno = ask_yesno
    controller.gui.ask_input = lambda *_args, **_kwargs: pytest.fail(
        "raw TV prompts should stay skipped across discs when ask_tv_setup is available"
    )

    def ask_tv_setup(**_kwargs):
        tv_setup_calls["count"] += 1
        return TVSessionSetup(
            title="Example Show",
            year="2004",
            season=2,
            starting_disc=1,
            episode_mapping="auto",
            metadata_provider="TMDB",
            metadata_id="tmdb:12345",
            multi_episode="auto",
            specials="ask",
            replace_existing=False,
            keep_raw=False,
        )

    controller.gui.ask_tv_setup = ask_tv_setup

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)

    def fake_write_temp_metadata(path, title, disc_number, **kwargs):
        writes.append((path, title, disc_number, kwargs))
        if len(writes) == 2:
            engine.abort()

    monkeypatch.setattr(engine, "write_temp_metadata", fake_write_temp_metadata)
    monkeypatch.setattr(engine, "update_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)
    monkeypatch.setattr(
        controller,
        "_open_manual_disc_picker",
        lambda *_a, **_k: ([0], 4_000_000_000),
    )
    monkeypatch.setattr(
        controller,
        "_show_manual_tv_output_plan",
        lambda **kwargs: SimpleNamespace(
            base_folder=kwargs["dest_folder"],
            confirmed=True,
        ),
    )
    monkeypatch.setattr(
        engine,
        "run_job",
        lambda _job: SimpleNamespace(success=True, errors=[]),
    )
    monkeypatch.setattr(
        controller,
        "_normalize_rip_result",
        lambda *_a, **_k: (True, [str(temp_root / "episode.mkv")]),
    )
    monkeypatch.setattr(
        controller,
        "_stabilize_ripped_files",
        lambda *_a, **_k: (True, False),
    )
    monkeypatch.setattr(
        controller,
        "_verify_expected_sizes",
        lambda *_a, **_k: ("pass", "ok"),
    )
    monkeypatch.setattr(
        controller,
        "_verify_container_integrity",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(
        engine,
        "analyze_files",
        lambda *_a, **_k: [(str(temp_root / "episode.mkv"), 7200.0, 4000.0)],
    )
    monkeypatch.setattr(controller, "_select_and_move", lambda *_a, **_k: True)

    controller.run_tv_disc()

    assert tv_setup_calls["count"] == 1
    assert [entry[2] for entry in writes] == [1, 2]
    assert all(entry[3]["season"] == 2 for entry in writes)


def test_run_tv_disc_tv_setup_normalizes_numeric_metadata_id_with_provider(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    tv_root = tmp_path / "tv"
    temp_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["tv_folder"] = str(tv_root)

    disc_titles = [
        {
            "id": 0,
            "name": "Episode Block",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
            "chapters": 20,
            "audio_tracks": [1],
            "subtitle_tracks": [1],
        },
    ]
    writes = []

    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_yesno = lambda _prompt: False
    controller.gui.ask_input = lambda *_args, **_kwargs: pytest.fail(
        "raw TV prompts should be skipped when ask_tv_setup is available"
    )
    controller.gui.ask_tv_setup = lambda **_kwargs: TVSessionSetup(
        title="Example Show",
        year="2004",
        season=2,
        starting_disc=1,
        episode_mapping="auto",
        metadata_provider="OpenDB",
        metadata_id="1396",
        multi_episode="auto",
        specials="ask",
        replace_existing=False,
        keep_raw=False,
    )

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(
        engine,
        "write_temp_metadata",
        lambda path, title, disc_number, **kwargs: (
            writes.append((path, title, disc_number, kwargs)),
            engine.abort(),
        )[-1],
    )
    monkeypatch.setattr(engine, "update_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)

    controller.run_tv_disc()

    assert writes
    assert writes[0][3]["dest_folder"] == str(
        tv_root / "Example Show [opendbid-1396]" / "Season 02"
    )


def test_run_tv_disc_tv_setup_preserves_season_zero_without_raw_reprompt(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    tv_root = tmp_path / "tv"
    temp_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["tv_folder"] = str(tv_root)

    disc_titles = [
        {
            "id": 0,
            "name": "Special Features",
            "duration": "45:00",
            "size": "2.0 GB",
            "duration_seconds": 2700,
            "size_bytes": 2_000_000_000,
            "chapters": 8,
            "audio_tracks": [1],
            "subtitle_tracks": [1],
        },
    ]
    writes = []

    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_yesno = lambda _prompt: False
    controller.gui.ask_input = lambda *_args, **_kwargs: pytest.fail(
        "season 0 from ask_tv_setup should not trigger raw TV prompts"
    )
    controller.gui.ask_tv_setup = lambda **_kwargs: TVSessionSetup(
        title="Example Show",
        year="2004",
        season=0,
        starting_disc=1,
        episode_mapping="auto",
        metadata_provider="TMDB",
        metadata_id="tmdb:12345",
        multi_episode="auto",
        specials="season0",
        replace_existing=False,
        keep_raw=False,
    )

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(
        engine,
        "write_temp_metadata",
        lambda path, title, disc_number, **kwargs: (
            writes.append((path, title, disc_number, kwargs)),
            engine.abort(),
        )[-1],
    )
    monkeypatch.setattr(engine, "update_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)

    controller.run_tv_disc()

    assert writes
    assert writes[0][1] == "Example Show"
    assert writes[0][2] == 1
    assert writes[0][3]["season"] == 0
    assert "Season 00" in writes[0][0]


def test_run_tv_disc_uses_review_output_plan_before_generic_confirm(tmp_path, monkeypatch):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    tv_root = tmp_path / "tv"
    temp_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["opt_confirm_before_rip"] = True
    engine.cfg["opt_smart_rip_mode"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["tv_folder"] = str(tv_root)

    disc_titles = [
        {
            "id": 0,
            "name": "Episode Block",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
            "chapters": 20,
            "audio_tracks": [1],
            "subtitle_tracks": [1],
        },
    ]
    captured_review = {}

    def fake_ask_tv_setup(**_kwargs):
        return TVSessionSetup(
            title="Example Show",
            year="2006",
            season=2,
            starting_disc=1,
            episode_mapping="manual",
            metadata_provider="TMDB",
            metadata_id="1396",
            multi_episode="merge",
            specials="season0",
            replace_existing=True,
            keep_raw=False,
        )

    def fake_show_output_plan_step(
        base_folder,
        main_label,
        extras_map,
        *,
        detail_lines=None,
        header_text="",
        subtitle_text="",
        confirm_text="",
        suggested_folder=None,
    ):
        captured_review["base_folder"] = base_folder
        captured_review["main_label"] = main_label
        captured_review["extras_map"] = extras_map
        captured_review["detail_lines"] = list(detail_lines or [])
        captured_review["header_text"] = header_text
        captured_review["subtitle_text"] = subtitle_text
        captured_review["confirm_text"] = confirm_text
        captured_review["suggested_folder"] = suggested_folder
        return False

    def fake_ask_yesno(prompt):
        if prompt.startswith("Use custom folders for this run?"):
            return False
        if prompt.startswith("Rip "):
            pytest.fail(
                "generic rip confirmation should not run after the TV review step"
            )
        return False

    info_calls: list[tuple[str, str]] = []
    controller.gui.show_info = (
        lambda title, message: info_calls.append((title, message))
    )
    controller.gui.ask_tv_setup = fake_ask_tv_setup
    controller.gui.show_output_plan_step = fake_show_output_plan_step
    controller.gui.ask_yesno = fake_ask_yesno

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "write_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "update_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)
    monkeypatch.setattr(
        controller,
        "_open_manual_disc_picker",
        lambda *_args, **_kwargs: ([0], 4_000_000_000),
    )
    monkeypatch.setattr(
        engine,
        "run_job",
        lambda _job: pytest.fail("rip should not start after cancelling review"),
    )

    controller.run_tv_disc()

    assert captured_review["header_text"] == "Step 3: Review Output Plan"
    assert captured_review["main_label"] == "Example Show - Season 02 episode files"
    assert captured_review["confirm_text"] == "Start Rip"
    assert captured_review["extras_map"] == {}
    assert any(
        line == "Metadata: TMDB 1396"
        for line in captured_review["detail_lines"]
    )
    assert any(
        line == "Show: Example Show (2006)"
        for line in captured_review["detail_lines"]
    )
    assert any(
        line == "Starting disc #: 1"
        for line in captured_review["detail_lines"]
    )
    assert any(
        line == "Episode mapping: Manual map"
        for line in captured_review["detail_lines"]
    )
    assert any(
        line == "Multi-ep titles: Merge to one"
        for line in captured_review["detail_lines"]
    )
    assert any(
        line == "Specials / OVAs: Put in Season 00"
        for line in captured_review["detail_lines"]
    )
    assert any(
        line.startswith("Selected titles: 1 [Title 1: Episode Block]")
        for line in captured_review["detail_lines"]
    )
    assert any(
        "Naming plan: Season 02 episode files" in line
        for line in captured_review["detail_lines"]
    )
    assert any(
        line == "Replace existing: Yes"
        for line in captured_review["detail_lines"]
    )
    assert any(
        "Cancelled at TV review step." in line
        for line in controller.gui.messages
    )
    assert not any(
        title == "Done" and message == "Session complete!"
        for title, message in info_calls
    )


def test_run_tv_disc_without_tv_setup_dialog_uses_generic_confirm(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    tv_root = tmp_path / "tv"
    temp_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["opt_confirm_before_rip"] = True
    engine.cfg["opt_smart_rip_mode"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["tv_folder"] = str(tv_root)

    disc_titles = [
        {
            "id": 0,
            "name": "Episode Block",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
            "chapters": 20,
            "audio_tracks": [1],
            "subtitle_tracks": [1],
        },
    ]
    prompts: list[str] = []

    def fake_ask_input(label, _prompt, default_value=""):
        if label == "Title":
            return "Fallback Show"
        if label == "Metadata ID":
            return ""
        if label == "Season":
            return "2"
        return default_value

    def fake_ask_yesno(prompt):
        prompts.append(prompt)
        if prompt.startswith("Use custom folders for this run?"):
            return False
        if prompt.startswith("Continue an existing show folder?"):
            return False
        if prompt.startswith("Rip "):
            return False
        if prompt == "Try again?":
            return False
        return False

    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_input = fake_ask_input
    controller.gui.ask_yesno = fake_ask_yesno
    controller.gui.ask_tv_setup = None

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "write_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "update_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)
    monkeypatch.setattr(
        controller,
        "_open_manual_disc_picker",
        lambda *_args, **_kwargs: ([0], 4_000_000_000),
    )
    monkeypatch.setattr(
        controller,
        "_show_manual_tv_output_plan",
        lambda *_args, **_kwargs: pytest.fail(
            "TV review output plan should stay gated to ask_tv_setup flows"
        ),
    )
    monkeypatch.setattr(
        engine,
        "run_job",
        lambda _job: pytest.fail("rip should not start after generic confirm cancel"),
    )

    controller.run_tv_disc()

    assert any(prompt.startswith("Rip 1 title(s)") for prompt in prompts)
    assert any("Rip cancelled by user." in line for line in controller.gui.messages)


def test_run_tv_disc_raw_metadata_id_is_persisted(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    tv_root = tmp_path / "tv"
    temp_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["tv_folder"] = str(tv_root)

    disc_titles = [
        {
            "id": 0,
            "name": "Episode Block",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
        },
    ]
    updates = []

    def ask_input(label, _prompt, default_value=""):
        if label == "Title":
            return "Chosen Show"
        if label == "Metadata ID":
            return "tvdb:79168"
        if label == "Season":
            return "2"
        return default_value

    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_input = ask_input
    controller.gui.ask_yesno = lambda _prompt: False
    controller.gui.ask_tv_setup = None

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(
        engine,
        "write_temp_metadata",
        lambda *_a, **_k: engine.abort(),
    )
    monkeypatch.setattr(
        engine,
        "update_temp_metadata",
        lambda path, **kwargs: updates.append((path, kwargs)),
    )
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)

    controller.run_tv_disc()

    assert updates
    assert updates[-1][1]["metadata_id"] == "tvdb:79168"


def test_select_and_move_tv_passes_replace_existing_to_move_files(
    tmp_path, monkeypatch
):
    controller, engine = _controller_with_engine()

    src_episode = tmp_path / "raw_episode.mkv"
    src_episode.write_text("new", encoding="utf-8")

    dest_folder = tmp_path / "TV" / "Chosen Show" / "Season 01"
    extras_folder = dest_folder / "Extras"
    dest_folder.mkdir(parents=True, exist_ok=True)
    extras_folder.mkdir(parents=True, exist_ok=True)
    existing_episode = dest_folder / "Chosen Show - S01E01 - Old Episode.mkv"
    existing_episode.write_text("old", encoding="utf-8")

    prompts: list[str] = []
    controller.gui.ask_yesno = lambda prompt: prompts.append(prompt) or True

    def ask_input(label, _prompt, default_value=""):
        if label == "Episode Numbers":
            return "1"
        if label == "Episode Names":
            return ""
        return default_value

    controller.gui.ask_input = ask_input

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        controller,
        "_verify_container_integrity",
        lambda *_a, **_k: True,
    )

    def fake_move_files(
        titles_list,
        main_indices,
        episode_numbers,
        real_names,
        extra_indices,
        is_tv,
        title,
        dest_folder,
        extras_folder,
        season,
        year,
        extra_counter,
        on_progress,
        on_log,
        bonus_indices=None,
        bonus_folder=None,
        replace_main_existing=False,
        edition="",
    ):
        captured["main_indices"] = main_indices
        captured["episode_numbers"] = episode_numbers
        captured["replace_main_existing"] = replace_main_existing
        return True, extra_counter, [str(existing_episode)]

    monkeypatch.setattr(engine, "move_files", fake_move_files)

    ok = controller._select_and_move(
        titles_list=[(str(src_episode), 1440.0, 1000.0)],
        is_tv=True,
        title="Chosen Show",
        dest_folder=str(dest_folder),
        extras_folder=str(extras_folder),
        season=1,
        year="0000",
        session_meta={"replace_existing": True},
    )

    assert ok is True
    assert captured["main_indices"] == [0]
    assert captured["episode_numbers"] == [1]
    assert captured["replace_main_existing"] is True
    assert any("replace the existing episode file(s)" in prompt for prompt in prompts)
    assert any("Existing files will be replaced." in line for line in controller.gui.messages)


def test_movie_run_custom_folder_overrides_continue_past_path_selection(tmp_path, monkeypatch):
    controller, engine = _controller_with_engine()

    default_temp = tmp_path / "default-temp"
    default_movies = tmp_path / "default-movies"
    custom_temp = tmp_path / "custom-temp"
    custom_movies = tmp_path / "custom-movies"
    for path in (default_temp, default_movies, custom_temp, custom_movies):
        path.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = True
    engine.cfg["temp_folder"] = str(default_temp)
    engine.cfg["movies_folder"] = str(default_movies)

    chosen_dirs = iter([str(custom_movies), str(custom_temp)])

    controller.gui.ask_yesno = lambda _prompt: True
    controller.gui.ask_directory = (
        lambda _title, _prompt, initialdir="": next(chosen_dirs)
    )
    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.show_error = lambda *_args, **_kwargs: None

    controller.gui.ask_movie_setup = lambda **_kw: MovieSessionSetup(
        title="Chosen Movie",
        year="2024",
        edition="",
        metadata_provider="TMDB",
        metadata_id="",
        replace_existing=False,
        keep_raw=False,
        extras_mode="ask",
    )

    writes = []
    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    engine.find_old_temp_folders = lambda *_a, **_k: []
    monkeypatch.setattr(
        engine,
        "write_temp_metadata",
        lambda path, title, disc_number, **kwargs: (
            writes.append((path, title, disc_number, kwargs)),
            engine.abort(),
        )[-1],
    )
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(
        controller,
        "scan_with_retry",
        lambda: [
            {
                "id": 0,
                "name": "Main Feature",
                "duration": "120:00",
                "size": "4.0 GB",
                "duration_seconds": 7200,
                "size_bytes": 4_000_000_000,
                "chapters": 20,
                "audio_tracks": [1],
                "subtitle_tracks": [1],
            },
        ],
    )

    controller.run_movie_disc()

    assert writes
    new_rip_path = os.path.normpath(writes[0][0])
    assert new_rip_path.startswith(os.path.normpath(str(custom_temp)))
    assert any("Custom folders set, continuing..." in m for m in controller.gui.messages)
    assert any("Flow: session initialized" in m for m in controller.gui.messages)


def test_run_dump_all_single_scans_size_before_disc_name_prompt(tmp_path, monkeypatch):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    temp_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = True

    events = []
    confirms = iter([False, False])

    controller.gui.ask_yesno = lambda _prompt: next(confirms)
    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_input = lambda *_args, **_kwargs: (
        events.append("prompt:Disc Name"),
        "Named Disc",
    )[1]

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(
        engine,
        "get_disc_size",
        lambda *_a, **_k: (events.append("disc_size"), 5_000_000_000)[1],
    )
    monkeypatch.setattr(
        engine,
        "check_disk_space",
        lambda *_a, **_k: ("ok", 0, 0),
    )
    monkeypatch.setattr(
        engine,
        "write_temp_metadata",
        lambda *_a, **_k: engine.abort(),
    )

    controller.run_dump_all()

    assert events.index("disc_size") < events.index("prompt:Disc Name")


def test_movie_run_does_not_open_temp_manager_when_enabled(tmp_path, monkeypatch):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = True
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)

    controller.gui.ask_yesno = lambda _prompt: False
    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.show_error = lambda *_args, **_kwargs: None

    called = {"temp_manager": False}

    def _unexpected_temp_manager(_temp_root):
        called["temp_manager"] = True

    monkeypatch.setattr(controller, "_offer_temp_manager", _unexpected_temp_manager)
    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: None)

    controller.run_movie_disc()

    assert called["temp_manager"] is False
    assert any("Flow: session initialized" in m for m in controller.gui.messages)


def test_movie_run_stops_if_abort_requested_during_path_setup(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)

    def _aborting_paths(_fields):
        engine.abort()
        return {
            "temp_folder": str(temp_root),
            "movies_folder": str(movies_root),
        }

    cleanup_called = {"value": False}

    monkeypatch.setattr(controller, "_prompt_run_path_overrides", _aborting_paths)
    monkeypatch.setattr(
        controller.diagnostics,
        "generate_session_summary",
        lambda: None,
    )
    monkeypatch.setattr(
        engine,
        "cleanup_partial_files",
        lambda *_a, **_k: cleanup_called.__setitem__("value", True),
    )

    controller.run_movie_disc()

    assert engine.abort_event.is_set()
    assert cleanup_called["value"] is False
    assert not any("Flow: session initialized" in m for m in controller.gui.messages)
    assert any(
        "abort requested during setup" in m.lower()
        for m in controller.gui.messages
    )


def test_run_organize_deletes_session_json_when_temp_folder_preserved(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    source = temp_root / "Disc_2026-04-13_12-59-46"
    movies_root = tmp_path / "movies"
    source.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)

    mkv = source / "title_t00.mkv"
    mkv.write_text("data", encoding="utf-8")
    (source / "_rip_meta.json").write_text("{}", encoding="utf-8")
    (source / "session.state.json").write_text("{}", encoding="utf-8")

    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)
    engine.cfg["opt_auto_delete_temp"] = False
    engine.cfg["opt_auto_delete_session_metadata"] = True

    inputs = iter([str(source), "m", "Chosen Movie", "", "2024"])
    controller.gui.ask_input = (
        lambda _label, _prompt, default_value="": next(inputs)
    )
    controller.gui.ask_yesno = lambda _prompt: False
    controller.gui.show_info = lambda *_args, **_kwargs: None

    monkeypatch.setattr(
        controller,
        "_prompt_run_path_overrides",
        lambda _fields: {},
    )
    monkeypatch.setattr(
        engine,
        "analyze_files",
        lambda *_args, **_kwargs: [(str(mkv), 7200.0, 4000.0)],
    )
    monkeypatch.setattr(
        controller,
        "_select_and_move",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(controller, "write_session_summary", lambda: None)
    monkeypatch.setattr(controller, "flush_log", lambda: None)

    controller.run_organize()

    assert source.exists()
    assert not (source / "_rip_meta.json").exists()
    assert not (source / "session.state.json").exists()
    assert mkv.exists()


def test_movie_run_manual_selection_preserves_main_movie_picker(tmp_path, monkeypatch):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_smart_rip_mode"] = False
    engine.cfg["opt_confirm_before_rip"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)

    controller.gui.ask_movie_setup = lambda **_kw: MovieSessionSetup(
        title="Chosen Movie",
        year="2024",
        edition="",
        metadata_provider="TMDB",
        metadata_id="",
        replace_existing=False,
        keep_raw=False,
        extras_mode="ask",
    )
    controller.gui.ask_yesno = lambda _prompt: False
    controller.gui.show_info = lambda *_args, **_kwargs: None
    disc_tree_args = {}

    def show_disc_tree(_disc_titles, _is_tv, _preview):
        disc_tree_args["preview"] = _preview
        return ["0", "1"]

    controller.gui.show_disc_tree = show_disc_tree

    disc_titles = [
        {"id": 0, "size_bytes": 4_000_000_000},
        {"id": 1, "size_bytes": 600_000_000},
    ]
    analyzed = [
        (str(temp_root / "manual_main.mkv"), 7200.0, 4000.0),
        (str(temp_root / "manual_extra.mkv"), 600.0, 600.0),
    ]

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "write_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "update_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)
    monkeypatch.setattr(
        engine,
        "run_job",
        lambda _job: SimpleNamespace(success=True, errors=[]),
    )
    monkeypatch.setattr(
        controller,
        "_normalize_rip_result",
        lambda *_a, **_k: (True, [item[0] for item in analyzed]),
    )
    monkeypatch.setattr(
        controller,
        "_stabilize_ripped_files",
        lambda *_a, **_k: (True, False),
    )
    monkeypatch.setattr(
        controller,
        "_verify_expected_sizes",
        lambda *_a, **_k: ("pass", "ok"),
    )
    monkeypatch.setattr(
        controller,
        "_verify_container_integrity",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(engine, "analyze_files", lambda *_a, **_k: analyzed)

    captured = {}

    def fake_select_and_move(
        _titles_list,
        _is_tv,
        _title,
        _dest_folder,
        _extras_folder,
        _season,
        _year,
        expected_size_by_title=None,
        session_rip_path=None,
        session_meta=None,
        selected_title_ids=None,
        **kwargs,
    ):
        captured["selected_title_ids"] = selected_title_ids
        captured["expected_size_by_title"] = expected_size_by_title
        captured["session_rip_path"] = session_rip_path
        captured["session_meta"] = session_meta
        return True

    monkeypatch.setattr(controller, "_select_and_move", fake_select_and_move)

    controller.run_movie_disc()

    assert captured["selected_title_ids"] is None
    assert captured["expected_size_by_title"] == {
        0: 4_000_000_000,
        1: 600_000_000,
    }
    assert captured["session_rip_path"] is not None
    assert callable(disc_tree_args["preview"])


def test_movie_run_scan_retry_keeps_same_disc_number(tmp_path, monkeypatch):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)

    show_info_calls: list[tuple[str, str]] = []
    controller.gui.show_info = (
        lambda title, message: show_info_calls.append((title, message))
    )
    controller.gui.show_error = lambda *_args, **_kwargs: None

    def _ask_yesno(prompt):
        if prompt == "Retry?":
            return True
        return False

    controller.gui.ask_yesno = _ask_yesno
    controller.gui.ask_movie_setup = lambda **_kw: MovieSessionSetup(
        title="Chosen Movie",
        year="2024",
        edition="",
        metadata_provider="TMDB",
        metadata_id="",
        replace_existing=False,
        keep_raw=False,
        extras_mode="ask",
    )

    disc_titles = [
        {
            "id": 0,
            "name": "Main Feature",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
        },
    ]
    scans = iter([None, disc_titles])

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: next(scans))
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(
        engine,
        "write_temp_metadata",
        lambda *_a, **_k: engine.abort(),
    )
    monkeypatch.setattr(engine, "update_temp_metadata", lambda *_a, **_k: None)

    controller.run_movie_disc()

    insert_messages = [
        message for title, message in show_info_calls if title == "Insert Disc"
    ]
    assert insert_messages == [
        "Insert disc 1 and click OK when ready.",
        "Insert disc 1 and click OK when ready.",
    ]


def test_manual_movie_selection_offers_main_file_replacement(
    tmp_path, monkeypatch
):
    controller, engine = _controller_with_engine(
        _engine_cfg(opt_confirm_before_move=False)
    )

    src_main = tmp_path / "selected_main.mkv"
    src_main.write_text("main", encoding="utf-8")

    dest_folder = tmp_path / "Movies" / "Chosen Movie (2024)"
    extras_folder = dest_folder / "Extras"
    dest_folder.mkdir(parents=True, exist_ok=True)
    extras_folder.mkdir(parents=True, exist_ok=True)
    existing_main = dest_folder / "Chosen Movie (2024).mkv"
    existing_main.write_text("old", encoding="utf-8")

    controller.gui.ask_yesno = lambda prompt: "already has a main movie file" in prompt

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        controller,
        "_verify_container_integrity",
        lambda *_a, **_k: True,
    )

    def fake_move_files(
        titles_list,
        main_indices,
        episode_numbers,
        real_names,
        extra_indices,
        is_tv,
        title,
        dest_folder,
        extras_folder,
        season,
        year,
        extra_counter,
        on_progress,
        on_log,
        bonus_indices=None,
        bonus_folder=None,
        replace_main_existing=False,
        edition="",
    ):
        captured["main_indices"] = main_indices
        captured["replace_main_existing"] = replace_main_existing
        return True, extra_counter, [str(existing_main)]

    monkeypatch.setattr(engine, "move_files", fake_move_files)

    ok = controller._select_and_move(
        titles_list=[(str(src_main), 7200.0, 4000.0)],
        is_tv=False,
        title="Chosen Movie",
        dest_folder=str(dest_folder),
        extras_folder=str(extras_folder),
        season=0,
        year="2024",
        selected_title_ids=None,
    )

    assert ok is True
    assert captured["main_indices"] == [0]
    assert captured["replace_main_existing"] is True


def test_manual_movie_selection_honors_preconfigured_replace_existing(
    tmp_path, monkeypatch
):
    controller, engine = _controller_with_engine(
        _engine_cfg(opt_confirm_before_move=False)
    )

    src_main = tmp_path / "selected_main.mkv"
    src_main.write_text("main", encoding="utf-8")

    dest_folder = tmp_path / "Movies" / "Chosen Movie (2024)"
    extras_folder = dest_folder / "Extras"
    dest_folder.mkdir(parents=True, exist_ok=True)
    extras_folder.mkdir(parents=True, exist_ok=True)
    existing_main = dest_folder / "Chosen Movie (2024).mkv"
    existing_main.write_text("old", encoding="utf-8")

    prompts: list[str] = []
    controller.gui.ask_yesno = lambda prompt: prompts.append(prompt) or True

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        controller,
        "_verify_container_integrity",
        lambda *_a, **_k: True,
    )

    def fake_move_files(
        titles_list,
        main_indices,
        episode_numbers,
        real_names,
        extra_indices,
        is_tv,
        title,
        dest_folder,
        extras_folder,
        season,
        year,
        extra_counter,
        on_progress,
        on_log,
        bonus_indices=None,
        bonus_folder=None,
        replace_main_existing=False,
        edition="",
    ):
        captured["main_indices"] = main_indices
        captured["replace_main_existing"] = replace_main_existing
        return True, extra_counter, [str(existing_main)]

    monkeypatch.setattr(engine, "move_files", fake_move_files)

    ok = controller._select_and_move(
        titles_list=[(str(src_main), 7200.0, 4000.0)],
        is_tv=False,
        title="Chosen Movie",
        dest_folder=str(dest_folder),
        extras_folder=str(extras_folder),
        season=0,
        year="2024",
        selected_title_ids=None,
        replace_existing=True,
    )

    assert ok is True
    assert captured["main_indices"] == [0]
    assert captured["replace_main_existing"] is True
    assert not any("already has a main movie file" in prompt for prompt in prompts)
    assert any(
        "Configured to replace the existing main movie file." in line
        for line in controller.gui.messages
    )


def test_manual_movie_selection_uses_edition_for_existing_main_detection(
    tmp_path, monkeypatch
):
    controller, engine = _controller_with_engine(
        _engine_cfg(opt_confirm_before_move=False)
    )

    src_main = tmp_path / "selected_main.mkv"
    src_main.write_text("main", encoding="utf-8")

    dest_folder = tmp_path / "Movies" / "Chosen Movie (2024) - Director's Cut"
    extras_folder = dest_folder / "Extras"
    dest_folder.mkdir(parents=True, exist_ok=True)
    extras_folder.mkdir(parents=True, exist_ok=True)
    existing_main = dest_folder / "Chosen Movie (2024) - Director's Cut.mkv"
    existing_main.write_text("old", encoding="utf-8")

    prompts: list[str] = []
    controller.gui.ask_yesno = lambda prompt: prompts.append(prompt) or False

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        controller,
        "_verify_container_integrity",
        lambda *_a, **_k: True,
    )

    def fake_move_files(
        titles_list,
        main_indices,
        episode_numbers,
        real_names,
        extra_indices,
        is_tv,
        title,
        dest_folder,
        extras_folder,
        season,
        year,
        extra_counter,
        on_progress,
        on_log,
        bonus_indices=None,
        bonus_folder=None,
        replace_main_existing=False,
        edition="",
    ):
        captured["main_indices"] = main_indices
        captured["replace_main_existing"] = replace_main_existing
        captured["edition"] = edition
        return True, extra_counter, [str(existing_main)]

    monkeypatch.setattr(engine, "move_files", fake_move_files)

    ok = controller._select_and_move(
        titles_list=[(str(src_main), 7200.0, 4000.0)],
        is_tv=False,
        title="Chosen Movie",
        dest_folder=str(dest_folder),
        extras_folder=str(extras_folder),
        season=0,
        year="2024",
        edition="Director's Cut",
        selected_title_ids=None,
    )

    assert ok is True
    assert captured["main_indices"] == [0]
    assert captured["replace_main_existing"] is False
    assert captured["edition"] == "Director's Cut"
    assert any(str(existing_main) in prompt for prompt in prompts)


def test_run_smart_rip_wizard_flow_completes_movie(tmp_path, monkeypatch):
    """Smart rip 5-step wizard: scan -> classify -> identity -> map -> preview -> rip."""
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    tv_root = tmp_path / "tv"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)
    engine.cfg["tv_folder"] = str(tv_root)

    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_yesno = lambda _prompt: True

    # Step 1: scan results -> "movie"
    controller.gui.show_scan_results_step = lambda _cl, _di=None: "movie"

    # Step 2: library identity -> MovieSessionSetup
    controller.gui.ask_movie_setup = lambda **_kw: MovieSessionSetup(
        title="Chosen Movie", year="2024", edition="",
        metadata_provider="TMDB", metadata_id="",
        replace_existing=False, keep_raw=False, extras_mode="ask",
    )

    # Step 3: content mapping -> select main only
    from shared.wizard_types import ContentSelection
    controller.gui.show_content_mapping_step = lambda _cl: ContentSelection(
        main_title_ids=[0], extra_title_ids=[], skip_title_ids=[1],
    )

    # Step 4: extras classification (skipped — no extras)
    # Step 5: output plan -> confirm
    controller.gui.show_output_plan_step = lambda *_a, **_k: True

    disc_titles = [
        {
            "id": 0, "name": "Main Feature",
            "duration": "120:00", "size": "4.0 GB",
            "duration_seconds": 7200, "size_bytes": 4_000_000_000,
        },
        {
            "id": 1, "name": "Bonus",
            "duration": "10:00", "size": "0.6 GB",
            "duration_seconds": 600, "size_bytes": 600_000_000,
        },
    ]
    analyzed = [
        (str(temp_root / "main.mkv"), 7200.0, 4000.0),
    ]

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "write_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "update_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)

    _main_ct = ClassifiedTitle(
        title=disc_titles[0], score=0.80, label="MAIN",
        confidence=0.95, reasons=["longest duration", "largest size"],
    )
    _extra_ct = ClassifiedTitle(
        title=disc_titles[1], score=0.20, label="EXTRA",
        confidence=0.95, reasons=["short duration (<20 min)"],
    )
    monkeypatch.setattr(
        "controller.controller.classify_and_pick_main",
        lambda *_a, **_k: (_main_ct, [_main_ct, _extra_ct]),
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

    move_calls = {}

    def fake_move_files(
        titles_list, main_indices, episode_numbers, real_names,
        extra_indices, is_tv, title, dest_folder, extras_folder,
        season, year, extra_counter, on_progress, on_log,
        bonus_indices=None, bonus_folder=None,
        replace_main_existing=False,
        edition="",
    ):
        move_calls["title"] = title
        move_calls["main_indices"] = main_indices
        move_calls["dest_folder"] = dest_folder
        move_calls["replace_main_existing"] = replace_main_existing
        return True, extra_counter, [str(tmp_path / "library" / "main.mkv")]

    monkeypatch.setattr(engine, "move_files", fake_move_files)

    controller.run_smart_rip()

    assert move_calls.get("title") == "Chosen Movie"
    assert "Chosen Movie (2024)" in move_calls.get("dest_folder", "")
    assert move_calls.get("replace_main_existing") is False
    assert any("movie" in m.lower() for m in controller.gui.messages
               if "media type" in m.lower())


def test_run_smart_rip_movie_setup_normalizes_numeric_metadata_id_with_provider(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    tv_root = tmp_path / "tv"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)
    engine.cfg["tv_folder"] = str(tv_root)

    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_yesno = lambda _prompt: True
    controller.gui.show_scan_results_step = lambda _cl, _di=None: "movie"
    controller.gui.ask_movie_setup = lambda **_kw: MovieSessionSetup(
        title="Chosen Movie",
        year="2024",
        edition="",
        metadata_provider="OpenDB",
        metadata_id="1396",
        replace_existing=False,
        keep_raw=False,
        extras_mode="ask",
    )

    from shared.wizard_types import ContentSelection

    controller.gui.show_content_mapping_step = lambda _cl: ContentSelection(
        main_title_ids=[0], extra_title_ids=[], skip_title_ids=[],
    )
    captured_output = {}

    def _show_output_plan_step(base_folder, main_label, _extras_map, **_kwargs):
        captured_output["base_folder"] = base_folder
        captured_output["main_label"] = main_label
        return False

    controller.gui.show_output_plan_step = _show_output_plan_step

    disc_titles = [
        {
            "id": 0, "name": "Main Feature",
            "duration": "120:00", "size": "4.0 GB",
            "duration_seconds": 7200, "size_bytes": 4_000_000_000,
        },
    ]

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)

    _main_ct = ClassifiedTitle(
        title=disc_titles[0], score=0.80, label="MAIN",
        confidence=0.95, reasons=["longest duration", "largest size"],
    )
    monkeypatch.setattr(
        "controller.controller.classify_and_pick_main",
        lambda *_a, **_k: (_main_ct, [_main_ct]),
    )
    monkeypatch.setattr(
        "controller.controller.format_classification_log",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(engine, "match_last_disc_memory", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "commit_current_disc_memory", lambda **_k: None)

    session_updates = []
    monkeypatch.setattr(
        engine,
        "update_last_disc_session_state",
        lambda data: session_updates.append(dict(data)) or {"session_info": dict(data)},
    )

    controller.run_smart_rip()

    expected_folder = str(movies_root / "Chosen Movie (2024) [opendbid-1396]")
    assert captured_output["base_folder"] == expected_folder
    assert captured_output["main_label"] == "Chosen Movie (2024) [opendbid-1396].mkv"

    identity_updates = [update for update in session_updates if update.get("title") == "Chosen Movie"]
    assert identity_updates
    assert identity_updates[-1]["metadata_provider"] == "OpenDB"
    assert identity_updates[-1]["metadata_id"] == "opendb:1396"


def test_run_smart_rip_movie_setup_uses_0000_when_year_blank(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    tv_root = tmp_path / "tv"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)
    engine.cfg["tv_folder"] = str(tv_root)

    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_yesno = lambda _prompt: True
    controller.gui.show_scan_results_step = lambda _cl, _di=None: "movie"
    controller.gui.ask_movie_setup = lambda **_kw: MovieSessionSetup(
        title="Chosen Movie",
        year="",
        edition="",
        metadata_provider="TMDB",
        metadata_id="",
        replace_existing=False,
        keep_raw=False,
        extras_mode="ask",
    )

    from shared.wizard_types import ContentSelection

    controller.gui.show_content_mapping_step = lambda _cl: ContentSelection(
        main_title_ids=[0], extra_title_ids=[], skip_title_ids=[],
    )
    captured_output = {}

    def _show_output_plan_step(base_folder, main_label, _extras_map, **_kwargs):
        captured_output["base_folder"] = base_folder
        captured_output["main_label"] = main_label
        return False

    controller.gui.show_output_plan_step = _show_output_plan_step

    disc_titles = [
        {
            "id": 0, "name": "Main Feature",
            "duration": "120:00", "size": "4.0 GB",
            "duration_seconds": 7200, "size_bytes": 4_000_000_000,
        },
    ]

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)

    _main_ct = ClassifiedTitle(
        title=disc_titles[0], score=0.80, label="MAIN",
        confidence=0.95, reasons=["longest duration", "largest size"],
    )
    monkeypatch.setattr(
        "controller.controller.classify_and_pick_main",
        lambda *_a, **_k: (_main_ct, [_main_ct]),
    )
    monkeypatch.setattr(
        "controller.controller.format_classification_log",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(engine, "match_last_disc_memory", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "commit_current_disc_memory", lambda **_k: None)

    session_updates = []
    monkeypatch.setattr(
        engine,
        "update_last_disc_session_state",
        lambda data: session_updates.append(dict(data)) or {"session_info": dict(data)},
    )

    controller.run_smart_rip()

    expected_folder = str(movies_root / "Chosen Movie (0000)")
    assert captured_output["base_folder"] == expected_folder
    assert captured_output["main_label"] == "Chosen Movie (0000).mkv"

    identity_updates = [
        update for update in session_updates if update.get("title") == "Chosen Movie"
    ]
    assert identity_updates
    assert identity_updates[-1]["year"] == "0000"
    assert any(
        "warning: no year" in message.lower()
        for message in controller.gui.messages
    )


def test_run_smart_rip_tv_setup_normalizes_numeric_metadata_id_with_provider(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    tv_root = tmp_path / "tv"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)
    engine.cfg["tv_folder"] = str(tv_root)

    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_yesno = lambda _prompt: True
    controller.gui.show_scan_results_step = lambda _cl, _di=None: "tv"
    controller.gui.ask_tv_setup = lambda **_kw: TVSessionSetup(
        title="Example Show",
        year="2004",
        season=2,
        starting_disc=1,
        episode_mapping="auto",
        metadata_provider="OpenDB",
        metadata_id="1396",
        multi_episode="auto",
        specials="ask",
        replace_existing=False,
        keep_raw=False,
    )

    from shared.wizard_types import ContentSelection

    controller.gui.show_content_mapping_step = lambda _cl: ContentSelection(
        main_title_ids=[0], extra_title_ids=[], skip_title_ids=[],
    )
    captured_output = {}

    def _show_output_plan_step(base_folder, main_label, _extras_map, **_kwargs):
        captured_output["base_folder"] = base_folder
        captured_output["main_label"] = main_label
        return False

    controller.gui.show_output_plan_step = _show_output_plan_step

    disc_titles = [
        {
            "id": 0, "name": "Episode Block",
            "duration": "120:00", "size": "4.0 GB",
            "duration_seconds": 7200, "size_bytes": 4_000_000_000,
        },
    ]

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)

    _main_ct = ClassifiedTitle(
        title=disc_titles[0], score=0.80, label="MAIN",
        confidence=0.95, reasons=["longest duration", "largest size"],
    )
    monkeypatch.setattr(
        "controller.controller.classify_and_pick_main",
        lambda *_a, **_k: (_main_ct, [_main_ct]),
    )
    monkeypatch.setattr(
        "controller.controller.format_classification_log",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(engine, "match_last_disc_memory", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "commit_current_disc_memory", lambda **_k: None)

    session_updates = []
    monkeypatch.setattr(
        engine,
        "update_last_disc_session_state",
        lambda data: session_updates.append(dict(data)) or {"session_info": dict(data)},
    )

    controller.run_smart_rip()

    expected_folder = str(tv_root / "Example Show [opendbid-1396]" / "Season 02")
    assert captured_output["base_folder"] == expected_folder
    assert captured_output["main_label"] == "S02Exx - Example Show.mkv"

    identity_updates = [update for update in session_updates if update.get("title") == "Example Show"]
    assert identity_updates
    assert identity_updates[-1]["metadata_provider"] == "OpenDB"
    assert identity_updates[-1]["metadata_id"] == "opendb:1396"


def test_run_smart_rip_splits_main_and_extras_phases(tmp_path, monkeypatch):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    tv_root = tmp_path / "tv"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["opt_confirm_before_move"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)
    engine.cfg["tv_folder"] = str(tv_root)

    info_messages = []
    controller.gui.show_info = lambda title, msg: info_messages.append((title, msg))
    controller.gui.ask_yesno = lambda _prompt: True
    controller.gui.show_scan_results_step = lambda _cl, _di=None: "movie"
    controller.gui.ask_movie_setup = lambda **_kw: MovieSessionSetup(
        title="Chosen Movie", year="2024", edition="",
        metadata_provider="TMDB", metadata_id="",
        replace_existing=False, keep_raw=False, extras_mode="ask",
    )

    from shared.wizard_types import ContentSelection, ExtrasAssignment

    controller.gui.show_content_mapping_step = lambda _cl: ContentSelection(
        main_title_ids=[0], extra_title_ids=[1], skip_title_ids=[],
    )
    controller.gui.show_extras_classification_step = lambda _titles: ExtrasAssignment(
        assignments={1: JELLYFIN_EXTRAS_CATEGORIES[0]}
    )
    controller.gui.show_output_plan_step = lambda *_a, **_k: True

    disc_titles = [
        {
            "id": 0, "name": "Main Feature",
            "duration": "120:00", "size": "4.0 GB",
            "duration_seconds": 7200, "size_bytes": 4_000_000_000,
        },
        {
            "id": 1, "name": "Bonus",
            "duration": "10:00", "size": "0.6 GB",
            "duration_seconds": 600, "size_bytes": 600_000_000,
        },
    ]

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)

    _main_ct = ClassifiedTitle(
        title=disc_titles[0], score=0.80, label="MAIN",
        confidence=0.95, reasons=["longest duration", "largest size"],
    )
    _extra_ct = ClassifiedTitle(
        title=disc_titles[1], score=0.20, label="EXTRA",
        confidence=0.95, reasons=["short duration (<20 min)"],
    )
    monkeypatch.setattr(
        "controller.controller.classify_and_pick_main",
        lambda *_a, **_k: (_main_ct, [_main_ct, _extra_ct]),
    )
    monkeypatch.setattr(
        "controller.controller.format_classification_log",
        lambda *_a, **_k: [],
    )

    rip_capture = {}
    run_sources = []
    normalize_queue = []
    phase_events = []

    def fake_run_job(job):
        rip_dir = Path(job.output)
        rip_dir.mkdir(parents=True, exist_ok=True)
        run_sources.append(job.source)
        phase_events.append(f"run:{job.source}")
        if job.source == "0":
            main_path = rip_dir / "title_t00_main.mkv"
            main_path.write_text("main")
            rip_capture["path"] = str(rip_dir)
            rip_capture["main"] = str(main_path)
            engine.last_title_file_map = {0: [str(main_path)]}
            normalize_queue.append((True, [str(main_path)]))
            return SimpleNamespace(success=True, errors=[])
        if job.source == "1":
            extra_path = rip_dir / "title_t01_bonus.mkv"
            extra_path.write_text("extra")
            rip_capture["extra"] = str(extra_path)
            engine.last_title_file_map = {1: [str(extra_path)]}
            normalize_queue.append((True, [str(extra_path)]))
            return SimpleNamespace(success=True, errors=[])
        raise AssertionError(f"unexpected smart-rip source {job.source}")

    monkeypatch.setattr(engine, "run_job", fake_run_job)
    monkeypatch.setattr(
        controller,
        "_normalize_rip_result",
        lambda *_a, **_k: normalize_queue.pop(0),
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
    monkeypatch.setattr(
        engine,
        "analyze_files",
        lambda files, *_a, **_k: [
            (
                files[0],
                7200.0 if "main" in os.path.basename(files[0]) else 600.0,
                4000.0 if "main" in os.path.basename(files[0]) else 600.0,
            )
        ],
    )

    cleanup_calls = []
    rmtree_calls = []
    monkeypatch.setattr(
        controller,
        "_cleanup_success_session_metadata",
        lambda *_a, **_k: cleanup_calls.append(True),
    )
    monkeypatch.setattr(
        controller_module.shutil,
        "rmtree",
        lambda path, ignore_errors=False: rmtree_calls.append((path, ignore_errors)),
    )

    def fake_move_files(
        titles_list, main_indices, episode_numbers, real_names,
        extra_indices, is_tv, title, dest_folder, extras_folder,
        season, year, extra_counter, on_progress, on_log,
        bonus_indices=None, bonus_folder=None,
        replace_main_existing=False,
        edition="",
    ):
        phase_events.append("move_main")
        return True, extra_counter, [str(Path(dest_folder) / f"{title} ({year}).mkv")]

    monkeypatch.setattr(engine, "move_files", fake_move_files)
    monkeypatch.setattr(
        controller,
        "_move_extras_to_categories",
        lambda *_a, **_k: phase_events.append("move_extras") or True,
    )

    controller.run_smart_rip()

    meta = engine.read_temp_metadata(rip_capture["path"])

    assert run_sources == ["0", "1"]
    assert phase_events == ["run:0", "move_main", "run:1", "move_extras"]
    assert meta is not None
    assert meta["status"] == "organized"
    assert meta["phase"] == "complete"
    assert meta["completed_titles"] == [0, 1]
    assert cleanup_calls == [True]
    assert rmtree_calls == [(rip_capture["path"], True)]
    assert info_messages[-1][0] == "Smart Rip Complete"


def test_run_smart_rip_preserves_main_when_extras_fail(tmp_path, monkeypatch):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    tv_root = tmp_path / "tv"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["opt_confirm_before_move"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)
    engine.cfg["tv_folder"] = str(tv_root)

    info_messages = []
    controller.gui.show_info = lambda title, msg: info_messages.append((title, msg))
    controller.gui.ask_yesno = lambda _prompt: True
    controller.gui.show_scan_results_step = lambda _cl, _di=None: "movie"
    controller.gui.ask_movie_setup = lambda **_kw: MovieSessionSetup(
        title="Chosen Movie", year="2024", edition="",
        metadata_provider="TMDB", metadata_id="",
        replace_existing=False, keep_raw=False, extras_mode="ask",
    )

    from shared.wizard_types import ContentSelection, ExtrasAssignment
    controller.gui.show_content_mapping_step = lambda _cl: ContentSelection(
        main_title_ids=[0], extra_title_ids=[1], skip_title_ids=[],
    )
    controller.gui.show_extras_classification_step = lambda _titles: ExtrasAssignment(
        assignments={1: JELLYFIN_EXTRAS_CATEGORIES[0]}
    )
    controller.gui.show_output_plan_step = lambda *_a, **_k: True

    disc_titles = [
        {
            "id": 0, "name": "Main Feature",
            "duration": "120:00", "size": "4.0 GB",
            "duration_seconds": 7200, "size_bytes": 4_000_000_000,
        },
        {
            "id": 1, "name": "Bonus",
            "duration": "10:00", "size": "0.6 GB",
            "duration_seconds": 600, "size_bytes": 600_000_000,
        },
    ]

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)

    _main_ct = ClassifiedTitle(
        title=disc_titles[0], score=0.80, label="MAIN",
        confidence=0.95, reasons=["longest duration", "largest size"],
    )
    _extra_ct = ClassifiedTitle(
        title=disc_titles[1], score=0.20, label="EXTRA",
        confidence=0.95, reasons=["short duration (<20 min)"],
    )
    monkeypatch.setattr(
        "controller.controller.classify_and_pick_main",
        lambda *_a, **_k: (_main_ct, [_main_ct, _extra_ct]),
    )
    monkeypatch.setattr(
        "controller.controller.format_classification_log",
        lambda *_a, **_k: [],
    )

    rip_capture = {}
    run_sources = []
    normalize_queue = []

    def fake_run_job(job):
        rip_dir = Path(job.output)
        rip_dir.mkdir(parents=True, exist_ok=True)
        run_sources.append(job.source)
        rip_capture["path"] = str(rip_dir)
        if job.source == "0":
            main_path = rip_dir / "title_t00_main.mkv"
            main_path.write_text("main")
            rip_capture["main"] = str(main_path)
            engine.last_title_file_map = {0: [str(main_path)]}
            normalize_queue.append((True, [str(main_path)]))
            return SimpleNamespace(success=True, errors=[])
        if job.source == "1":
            engine.last_title_file_map = {}
            normalize_queue.append((False, []))
            return SimpleNamespace(success=False, errors=[2])
        raise AssertionError(f"unexpected smart-rip source {job.source}")

    monkeypatch.setattr(engine, "run_job", fake_run_job)
    monkeypatch.setattr(
        controller,
        "_normalize_rip_result",
        lambda *_a, **_k: normalize_queue.pop(0),
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
    monkeypatch.setattr(
        engine,
        "analyze_files",
        lambda *_a, **_k: [(rip_capture["main"], 7200.0, 4000.0)],
    )

    cleanup_calls = []
    rmtree_calls = []
    move_calls = {}
    monkeypatch.setattr(
        controller,
        "_cleanup_success_session_metadata",
        lambda *_a, **_k: cleanup_calls.append(True),
    )
    monkeypatch.setattr(
        controller,
        "_move_extras_to_categories",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("partial smart rip should not move extras")
        ),
    )
    monkeypatch.setattr(
        controller_module.shutil,
        "rmtree",
        lambda path, ignore_errors=False: rmtree_calls.append((path, ignore_errors)),
    )

    def fake_move_files(
        titles_list, main_indices, episode_numbers, real_names,
        extra_indices, is_tv, title, dest_folder, extras_folder,
        season, year, extra_counter, on_progress, on_log,
        bonus_indices=None, bonus_folder=None,
        replace_main_existing=False,
        edition="",
    ):
        move_calls["main_indices"] = list(main_indices)
        move_calls["extra_indices"] = extra_indices
        move_calls["title"] = title
        Path(titles_list[main_indices[0]][0]).unlink(missing_ok=True)
        return True, extra_counter, [str(Path(dest_folder) / f"{title} ({year}).mkv")]

    monkeypatch.setattr(engine, "move_files", fake_move_files)

    controller.run_smart_rip()

    meta = engine.read_temp_metadata(rip_capture["path"])

    assert run_sources == ["0", "1"]
    assert meta is not None
    assert meta["status"] == "partial"
    assert meta["phase"] == "partial"
    assert meta["selected_titles"] == [0, 1]
    assert meta["completed_titles"] == [0]
    assert meta["failed_titles"] == [2]
    assert move_calls["title"] == "Chosen Movie"
    assert move_calls["main_indices"] == [0]
    assert move_calls["extra_indices"] == []
    assert cleanup_calls == []
    assert rmtree_calls == []
    assert controller.sm.state == SessionState.MOVED
    assert info_messages[-1][0] == "Smart Rip Partial"
    assert "Remaining titles need retry" in info_messages[-1][1]


def test_run_movie_disc_preserves_partial_session_for_completed_titles(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["opt_confirm_before_rip"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)

    another_disc_prompts = {"count": 0}

    def _ask_yesno(prompt):
        if prompt == "Another disc in this set?":
            another_disc_prompts["count"] += 1
            return False
        return False

    controller.gui.ask_yesno = _ask_yesno
    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.show_error = lambda *_args, **_kwargs: None
    controller.gui.ask_movie_setup = lambda **_kw: MovieSessionSetup(
        title="Chosen Movie",
        year="2024",
        edition="",
        metadata_provider="TMDB",
        metadata_id="",
        replace_existing=False,
        keep_raw=False,
        extras_mode="ask",
    )

    disc_titles = [
        {
            "id": 0,
            "name": "Main Feature",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
        },
        {
            "id": 1,
            "name": "Bonus",
            "duration": "10:00",
            "size": "0.6 GB",
            "duration_seconds": 600,
            "size_bytes": 600_000_000,
        },
    ]

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "write_session_summary", lambda: None)
    monkeypatch.setattr(controller, "flush_log", lambda: None)
    monkeypatch.setattr(
        controller,
        "_open_manual_disc_picker",
        lambda _titles, _is_tv: ([0, 1], 4_600_000_000),
    )
    monkeypatch.setattr(engine, "_quick_ffprobe_ok", lambda *_a, **_k: True)

    rip_capture: dict[str, str] = {}

    def fake_run_job(job):
        rip_dir = Path(job.output)
        rip_dir.mkdir(parents=True, exist_ok=True)
        main_path = rip_dir / "title_t00_main.mkv"
        main_path.write_text("main", encoding="utf-8")
        rip_capture["path"] = str(rip_dir)
        rip_capture["main"] = str(main_path)
        engine.last_title_file_map = {0: [str(main_path)]}
        return SimpleNamespace(success=False, errors=[1])

    monkeypatch.setattr(engine, "run_job", fake_run_job)
    monkeypatch.setattr(
        controller,
        "_normalize_rip_result",
        lambda *_a, **_k: (False, [rip_capture["main"]]),
    )
    monkeypatch.setattr(
        controller,
        "_stabilize_ripped_files",
        lambda *_a, **_k: (True, False),
    )
    monkeypatch.setattr(
        controller,
        "_verify_expected_sizes",
        lambda *_a, **_k: ("pass", "ok"),
    )
    monkeypatch.setattr(
        controller,
        "_verify_container_integrity",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(
        engine,
        "analyze_files",
        lambda *_a, **_k: [(rip_capture["main"], 7200.0, 4000.0)],
    )

    cleanup_calls = []
    rmtree_calls = []
    move_call: dict[str, object] = {}
    monkeypatch.setattr(
        controller,
        "_cleanup_success_session_metadata",
        lambda *_a, **_k: cleanup_calls.append(True),
    )
    monkeypatch.setattr(
        controller_module.shutil,
        "rmtree",
        lambda path, ignore_errors=False: rmtree_calls.append((path, ignore_errors)),
    )

    def fake_select_and_move(
        _titles_list,
        _is_tv,
        _title,
        _dest_folder,
        _extras_folder,
        _season,
        _year,
        expected_size_by_title=None,
        session_rip_path=None,
        session_meta=None,
        selected_title_ids=None,
        **_kwargs,
    ):
        move_call["selected_title_ids"] = selected_title_ids
        move_call["expected_size_by_title"] = expected_size_by_title
        move_call["session_rip_path"] = session_rip_path
        move_call["session_meta"] = session_meta
        return True

    monkeypatch.setattr(controller, "_select_and_move", fake_select_and_move)

    controller.run_movie_disc()

    meta = engine.read_temp_metadata(rip_capture["path"])

    assert meta is not None
    assert meta["status"] == "partial"
    assert meta["phase"] == "partial"
    assert meta["selected_titles"] == [0, 1]
    assert meta["completed_titles"] == [0]
    assert meta["failed_titles"] == [1]
    assert move_call["selected_title_ids"] is None
    assert move_call["expected_size_by_title"] == {0: 4_000_000_000}
    assert move_call["session_rip_path"] == rip_capture["path"]
    assert move_call["session_meta"] is None
    assert cleanup_calls == []
    assert rmtree_calls == []
    assert any(
        "preserving session as partial" in message.lower()
        for message in controller.gui.messages
    )
    assert any(
        "partial session preserved at" in message.lower()
        for message in controller.gui.messages
    )
    assert another_disc_prompts["count"] == 0


def test_movie_run_move_failure_does_not_prompt_for_next_disc(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["opt_confirm_before_rip"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)

    another_disc_prompts = {"count": 0}
    info_calls: list[tuple[str, str]] = []

    def _ask_yesno(prompt):
        if prompt == "Another disc in this set?":
            another_disc_prompts["count"] += 1
            return True
        return False

    controller.gui.ask_yesno = _ask_yesno
    controller.gui.show_info = (
        lambda title, message: info_calls.append((title, message))
    )
    controller.gui.show_error = lambda *_args, **_kwargs: None
    controller.gui.ask_movie_setup = lambda **_kw: MovieSessionSetup(
        title="Chosen Movie",
        year="2024",
        edition="",
        metadata_provider="TMDB",
        metadata_id="",
        replace_existing=False,
        keep_raw=False,
        extras_mode="ask",
    )

    disc_titles = [
        {
            "id": 0,
            "name": "Main Feature",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
        },
    ]

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "write_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "update_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)
    monkeypatch.setattr(
        controller,
        "_open_manual_disc_picker",
        lambda _titles, _is_tv: ([0], 4_000_000_000),
    )
    monkeypatch.setattr(
        engine,
        "run_job",
        lambda _job: SimpleNamespace(success=True, errors=[]),
    )
    monkeypatch.setattr(
        controller,
        "_normalize_rip_result",
        lambda *_a, **_k: (True, [str(temp_root / "main.mkv")]),
    )
    monkeypatch.setattr(
        controller,
        "_stabilize_ripped_files",
        lambda *_a, **_k: (True, False),
    )
    monkeypatch.setattr(
        controller,
        "_verify_expected_sizes",
        lambda *_a, **_k: ("pass", "ok"),
    )
    monkeypatch.setattr(
        controller,
        "_verify_container_integrity",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(
        engine,
        "analyze_files",
        lambda *_a, **_k: [(str(temp_root / "main.mkv"), 7200.0, 4000.0)],
    )
    monkeypatch.setattr(controller, "_select_and_move", lambda *_a, **_k: False)

    controller.run_movie_disc()

    assert another_disc_prompts["count"] == 0
    assert controller.session_report == ["Move failed; temp output remains preserved."]
    assert controller.sm.state == SessionState.INIT
    assert not any(
        title == "Done" and message == "Session complete!"
        for title, message in info_calls
    )


def test_run_smart_rip_movie_dialog_receives_identity_defaults_and_ai_prompt(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    tv_root = tmp_path / "tv"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["opt_confirm_before_rip"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)
    engine.cfg["tv_folder"] = str(tv_root)
    engine.last_disc_info = {"title": "KUNG_FU_PANDA_3"}

    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_yesno = lambda _prompt: True
    controller.gui.show_scan_results_step = lambda _cl, _di=None: "movie"

    captured_defaults = {}

    def _ask_movie_setup(**kwargs):
        captured_defaults.update(kwargs)
        return MovieSessionSetup(
            title=kwargs.get("default_title", "Chosen Movie"),
            year=kwargs.get("default_year", "2016"),
            edition="",
            metadata_provider=kwargs.get("default_metadata_provider", "TMDB"),
            metadata_id=kwargs.get("default_metadata_id", ""),
            replace_existing=False,
            keep_raw=False,
            extras_mode="ask",
        )

    controller.gui.ask_movie_setup = _ask_movie_setup

    from shared.wizard_types import ContentSelection

    controller.gui.show_content_mapping_step = lambda _cl: ContentSelection(
        main_title_ids=[0], extra_title_ids=[], skip_title_ids=[],
    )
    controller.gui.show_output_plan_step = lambda *_a, **_k: True

    disc_titles = [
        {
            "id": 0,
            "name": "Main Feature",
            "duration": "94:39",
            "size": "4.6 GB",
            "duration_seconds": 5679,
            "size_bytes": 4_600_000_000,
        },
    ]
    analyzed = [
        (str(temp_root / "main.mkv"), 5679.0, 4600.0),
    ]

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "write_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "update_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)
    monkeypatch.setattr(
        controller,
        "_request_identity_ai_suggestion",
        lambda *_a, **_k: IdentitySuggestion(
            title="Kung Fu Panda 3",
            year="2016",
            metadata_provider="TMDB",
            metadata_id="tmdb:950",
            source="ai:local",
        ),
    )

    _main_ct = ClassifiedTitle(
        title=disc_titles[0],
        score=0.95,
        label="MAIN",
        confidence=0.95,
        reasons=["longest duration"],
        recommended=True,
    )
    monkeypatch.setattr(
        "controller.controller.classify_and_pick_main",
        lambda *_a, **_k: (_main_ct, [_main_ct]),
    )
    monkeypatch.setattr(
        "controller.controller.format_classification_log",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        engine,
        "run_job",
        lambda _job: SimpleNamespace(success=True, errors=[]),
    )
    monkeypatch.setattr(
        controller,
        "_normalize_rip_result",
        lambda *_a, **_k: (True, [item[0] for item in analyzed]),
    )
    monkeypatch.setattr(
        controller,
        "_stabilize_ripped_files",
        lambda *_a, **_k: (True, False),
    )
    monkeypatch.setattr(
        controller,
        "_verify_expected_sizes",
        lambda *_a, **_k: ("pass", "ok"),
    )
    monkeypatch.setattr(
        controller,
        "_verify_container_integrity",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(engine, "analyze_files", lambda *_a, **_k: analyzed)
    engine.last_title_file_map = {}

    monkeypatch.setattr(
        engine,
        "move_files",
        lambda *_a, **_k: (True, 1, [str(tmp_path / "library" / "main.mkv")]),
    )

    controller.run_smart_rip()

    assert captured_defaults["default_title"] == "Kung Fu Panda 3"
    assert captured_defaults["default_year"] == "2016"
    assert captured_defaults["default_metadata_provider"] == "TMDB"
    assert captured_defaults["default_metadata_id"] == "tmdb:950"
    assert controller.gui.ai_choices
    assert "Kung Fu Panda 3" in controller.gui.ai_choices[-1]["text"]
    assert controller.gui.ai_choices[-1]["backend_tag"] == "LOCAL"


def test_run_smart_rip_movie_ai_accept_opens_dialog_with_suggested_fields(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    tv_root = tmp_path / "tv"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["opt_confirm_before_rip"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)
    engine.cfg["tv_folder"] = str(tv_root)
    engine.last_disc_info = {"title": "BEAUTY_AND_THE_BEAST"}

    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_yesno = lambda _prompt: True
    controller.gui.show_scan_results_step = lambda _cl, _di=None: "movie"
    controller.gui.ask_ai_identity_choice = lambda *_a, **_k: "accept"

    captured_defaults = {}

    def _ask_movie_setup(**kwargs):
        captured_defaults.update(kwargs)
        return MovieSessionSetup(
            title=kwargs.get("default_title", ""),
            year=kwargs.get("default_year", ""),
            edition="",
            metadata_provider=kwargs.get("default_metadata_provider", "TMDB"),
            metadata_id=kwargs.get("default_metadata_id", ""),
            replace_existing=False,
            keep_raw=False,
            extras_mode="ask",
        )

    controller.gui.ask_movie_setup = _ask_movie_setup

    from shared.wizard_types import ContentSelection

    controller.gui.show_content_mapping_step = lambda _cl: ContentSelection(
        main_title_ids=[0], extra_title_ids=[], skip_title_ids=[],
    )
    controller.gui.show_output_plan_step = lambda *_a, **_k: True

    disc_titles = [
        {
            "id": 0,
            "name": "Main Feature",
            "duration": "84:00",
            "size": "4.2 GB",
            "duration_seconds": 5040,
            "size_bytes": 4_200_000_000,
        },
    ]
    analyzed = [
        (str(temp_root / "main.mkv"), 5040.0, 4200.0),
    ]

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "write_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "update_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)
    monkeypatch.setattr(
        controller,
        "_request_identity_ai_suggestion",
        lambda *_a, **_k: IdentitySuggestion(
            title="Beauty and the Beast",
            year="1991",
            metadata_provider="TMDB",
            metadata_id="tmdb:10020",
            source="ai:local",
        ),
    )

    _main_ct = ClassifiedTitle(
        title=disc_titles[0],
        score=0.95,
        label="MAIN",
        confidence=0.95,
        reasons=["longest duration"],
        recommended=True,
    )
    monkeypatch.setattr(
        "controller.controller.classify_and_pick_main",
        lambda *_a, **_k: (_main_ct, [_main_ct]),
    )
    monkeypatch.setattr(
        "controller.controller.format_classification_log",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        engine,
        "run_job",
        lambda _job: SimpleNamespace(success=True, errors=[]),
    )
    monkeypatch.setattr(
        controller,
        "_normalize_rip_result",
        lambda *_a, **_k: (True, [item[0] for item in analyzed]),
    )
    monkeypatch.setattr(
        controller,
        "_stabilize_ripped_files",
        lambda *_a, **_k: (True, False),
    )
    monkeypatch.setattr(
        controller,
        "_verify_expected_sizes",
        lambda *_a, **_k: ("pass", "ok"),
    )
    monkeypatch.setattr(
        controller,
        "_verify_container_integrity",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(engine, "analyze_files", lambda *_a, **_k: analyzed)
    engine.last_title_file_map = {}

    monkeypatch.setattr(
        engine,
        "move_files",
        lambda *_a, **_k: (True, 1, [str(tmp_path / "library" / "main.mkv")]),
    )

    controller.run_smart_rip()

    assert captured_defaults["default_title"] == "Beauty and the Beast"
    assert captured_defaults["default_year"] == "1991"
    assert captured_defaults["default_metadata_provider"] == "TMDB"
    assert captured_defaults["default_metadata_id"] == "tmdb:10020"
    assert any(
        "opening identity step with suggested fields" in message.lower()
        for message in controller.gui.messages
    )


def test_smart_rip_stops_if_abort_requested_during_path_setup(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    tv_root = tmp_path / "tv"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)
    engine.cfg["tv_folder"] = str(tv_root)

    def _aborting_paths(_fields):
        engine.abort()
        return {
            "temp_folder": str(temp_root),
            "movies_folder": str(movies_root),
            "tv_folder": str(tv_root),
        }

    cleanup_called = {"value": False}

    monkeypatch.setattr(controller, "_prompt_run_path_overrides", _aborting_paths)
    monkeypatch.setattr(
        controller.diagnostics,
        "generate_session_summary",
        lambda: None,
    )
    monkeypatch.setattr(
        engine,
        "cleanup_partial_files",
        lambda *_a, **_k: cleanup_called.__setitem__("value", True),
    )

    controller.run_smart_rip()

    assert engine.abort_event.is_set()
    assert cleanup_called["value"] is False
    assert not any("Flow: session initialized" in m for m in controller.gui.messages)
    assert any(
        "abort requested during setup" in m.lower()
        for m in controller.gui.messages
    )


def test_run_smart_rip_manual_picker_flow_uses_manual_picker(tmp_path, monkeypatch):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    tv_root = tmp_path / "tv"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["opt_confirm_before_rip"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)
    engine.cfg["tv_folder"] = str(tv_root)

    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_yesno = lambda _prompt: False
    controller.gui.show_scan_results_step = lambda _cl, _di=None: "manual_movie"
    controller.gui.ask_movie_setup = lambda **_kw: MovieSessionSetup(
        title="Chosen Movie", year="2024", edition="",
        metadata_provider="TMDB", metadata_id="",
        replace_existing=False, keep_raw=False, extras_mode="ask",
    )
    controller.gui.show_content_mapping_step = (
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("manual picker flow should skip content mapping")
        )
    )
    controller.gui.show_output_plan_step = (
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("manual picker flow should skip output preview")
        )
    )

    disc_titles = [
        {
            "id": 0, "name": "Main Feature",
            "duration": "120:00", "size": "4.0 GB",
            "duration_seconds": 7200, "size_bytes": 4_000_000_000,
        },
        {
            "id": 1, "name": "Bonus",
            "duration": "10:00", "size": "0.6 GB",
            "duration_seconds": 600, "size_bytes": 600_000_000,
        },
    ]
    analyzed = [
        (str(temp_root / "main.mkv"), 7200.0, 4000.0),
    ]

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "write_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "update_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)
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

    picker_calls = {"count": 0}
    monkeypatch.setattr(
        controller,
        "_open_manual_disc_picker",
        lambda _titles, _is_tv: (
            picker_calls.__setitem__("count", picker_calls["count"] + 1)
            or ([0, 1], 4_600_000_000)
        ),
    )

    move_call = {}

    def fake_select_and_move(
        _titles_list,
        _is_tv,
        _title,
        _dest_folder,
        _extras_folder,
        _season,
        _year,
        expected_size_by_title=None,
        session_rip_path=None,
        session_meta=None,
        selected_title_ids=None,
        **_kwargs,
    ):
        move_call["selected_title_ids"] = selected_title_ids
        move_call["dest_folder"] = _dest_folder
        return True

    monkeypatch.setattr(controller, "_select_and_move", fake_select_and_move)

    controller.run_smart_rip()

    assert picker_calls["count"] == 1
    assert move_call["selected_title_ids"] is None
    assert "Chosen Movie (2024)" in move_call["dest_folder"]
    assert any(
        "manual title picker" in m.lower()
        for m in controller.gui.messages
    )


def test_run_smart_rip_standard_flow_uses_manual_picker(tmp_path, monkeypatch):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    tv_root = tmp_path / "tv"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["opt_confirm_before_rip"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)
    engine.cfg["tv_folder"] = str(tv_root)

    def _ask_yesno(prompt):
        if prompt.startswith("Standard mode uses the older manual title picker."):
            return False
        return False

    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_yesno = _ask_yesno
    controller.gui.show_scan_results_step = lambda _cl, _di=None: "standard"
    controller.gui.ask_movie_setup = lambda **_kw: MovieSessionSetup(
        title="Chosen Movie", year="2024", edition="",
        metadata_provider="TMDB", metadata_id="",
        replace_existing=False, keep_raw=False, extras_mode="ask",
    )
    controller.gui.show_content_mapping_step = (
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("standard flow should skip content mapping")
        )
    )
    controller.gui.show_output_plan_step = (
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("standard flow should skip output preview")
        )
    )

    disc_titles = [
        {
            "id": 0, "name": "Main Feature",
            "duration": "120:00", "size": "4.0 GB",
            "duration_seconds": 7200, "size_bytes": 4_000_000_000,
        },
        {
            "id": 1, "name": "Bonus",
            "duration": "10:00", "size": "0.6 GB",
            "duration_seconds": 600, "size_bytes": 600_000_000,
        },
    ]
    analyzed = [
        (str(temp_root / "main.mkv"), 7200.0, 4000.0),
    ]

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "write_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "update_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)
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

    picker_calls = {"count": 0}
    monkeypatch.setattr(
        controller,
        "_open_manual_disc_picker",
        lambda _titles, _is_tv: (
            picker_calls.__setitem__("count", picker_calls["count"] + 1)
            or ([0, 1], 4_600_000_000)
        ),
    )

    move_call = {}

    def fake_select_and_move(
        _titles_list,
        _is_tv,
        _title,
        _dest_folder,
        _extras_folder,
        _season,
        _year,
        expected_size_by_title=None,
        session_rip_path=None,
        session_meta=None,
        selected_title_ids=None,
        **_kwargs,
    ):
        move_call["selected_title_ids"] = selected_title_ids
        move_call["dest_folder"] = _dest_folder
        return True

    monkeypatch.setattr(controller, "_select_and_move", fake_select_and_move)

    controller.run_smart_rip()

    assert picker_calls["count"] == 1
    assert move_call["selected_title_ids"] is None
    assert "Chosen Movie (2024)" in move_call["dest_folder"]
    assert any(
        "disc flow selected: standard" in m.lower()
        for m in controller.gui.messages
    )


def test_run_smart_rip_manual_tv_picker_preserves_selected_title_ids_for_move(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    tv_root = tmp_path / "tv"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["opt_confirm_before_rip"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)
    engine.cfg["tv_folder"] = str(tv_root)

    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.show_scan_results_step = lambda _cl, _di=None: "manual_tv"
    controller.gui.ask_tv_setup = lambda **_kw: TVSessionSetup(
        title="Chosen Show",
        year="",
        season=1,
        starting_disc=1,
        episode_mapping="auto",
        metadata_provider="TMDB",
        metadata_id="",
        multi_episode="auto",
        specials="ask",
        replace_existing=False,
        keep_raw=False,
    )
    controller.gui.show_content_mapping_step = (
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("manual_tv flow should skip content mapping")
        )
    )
    controller.gui.show_output_plan_step = (
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("manual_tv flow should skip output preview")
        )
    )

    disc_titles = [
        {
            "id": 0,
            "name": "Episode One",
            "duration": "24:00",
            "size": "1.1 GB",
            "duration_seconds": 1440,
            "size_bytes": 1_100_000_000,
        },
        {
            "id": 1,
            "name": "Episode Two",
            "duration": "24:00",
            "size": "1.1 GB",
            "duration_seconds": 1440,
            "size_bytes": 1_100_000_000,
        },
    ]
    analyzed = [
        (str(temp_root / "ep1.mkv"), 1440.0, 1100.0),
        (str(temp_root / "ep2.mkv"), 1440.0, 1100.0),
    ]

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "write_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "update_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(
        controller,
        "_open_manual_disc_picker",
        lambda _titles, _is_tv: ([0, 1], 2_200_000_000),
    )
    monkeypatch.setattr(
        engine,
        "run_job",
        lambda _job: SimpleNamespace(success=True, errors=[]),
    )
    monkeypatch.setattr(
        controller,
        "_normalize_rip_result",
        lambda *_a, **_k: (True, [item[0] for item in analyzed]),
    )
    monkeypatch.setattr(
        controller,
        "_stabilize_ripped_files",
        lambda *_a, **_k: (True, False),
    )
    monkeypatch.setattr(
        controller,
        "_verify_expected_sizes",
        lambda *_a, **_k: ("pass", "ok"),
    )
    monkeypatch.setattr(
        controller,
        "_verify_container_integrity",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(engine, "analyze_files", lambda *_a, **_k: analyzed)

    move_call: dict[str, object] = {}

    def fake_select_and_move(
        _titles_list,
        _is_tv,
        _title,
        _dest_folder,
        _extras_folder,
        _season,
        _year,
        expected_size_by_title=None,
        session_rip_path=None,
        session_meta=None,
        selected_title_ids=None,
        **_kwargs,
    ):
        move_call["is_tv"] = _is_tv
        move_call["selected_title_ids"] = selected_title_ids
        move_call["dest_folder"] = _dest_folder
        return True

    monkeypatch.setattr(controller, "_select_and_move", fake_select_and_move)

    controller.run_smart_rip()

    assert move_call["is_tv"] is True
    assert move_call["selected_title_ids"] == [0, 1]
    assert "Chosen Show" in move_call["dest_folder"]
    assert any(
        "manual title picker" in m.lower()
        for m in controller.gui.messages
    )


def test_movie_run_smart_rip_no_recommendation_manual_picker_uses_selection(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_smart_rip_mode"] = True
    engine.cfg["opt_confirm_before_rip"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)

    show_info_calls: list[tuple[str, str]] = []
    controller.gui.show_info = (
        lambda title, message: show_info_calls.append((title, message))
    )
    controller.gui.show_error = lambda *_args, **_kwargs: None

    def _ask_yesno(prompt):
        if prompt == "Open manual title picker with Preview buttons?":
            return True
        return False

    controller.gui.ask_yesno = _ask_yesno
    controller.gui.ask_movie_setup = lambda **_kw: MovieSessionSetup(
        title="Chosen Movie",
        year="2024",
        edition="",
        metadata_provider="TMDB",
        metadata_id="",
        replace_existing=False,
        keep_raw=False,
        extras_mode="ask",
    )

    disc_titles = [
        {
            "id": 0,
            "name": "Main Feature",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
        },
        {
            "id": 1,
            "name": "Bonus",
            "duration": "10:00",
            "size": "0.6 GB",
            "duration_seconds": 600,
            "size_bytes": 600_000_000,
        },
    ]
    analyzed = [
        (str(temp_root / "main.mkv"), 7200.0, 4000.0),
    ]

    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "write_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "update_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "_get_shared_classified_titles", lambda _titles: [])
    monkeypatch.setattr(
        controller,
        "_get_recommended_classified_title",
        lambda _classified: None,
    )
    monkeypatch.setattr(
        controller,
        "_open_manual_disc_picker",
        lambda _titles, _is_tv: ([0], 4_000_000_000),
    )
    monkeypatch.setattr(
        engine,
        "run_job",
        lambda _job: SimpleNamespace(success=True, errors=[]),
    )
    monkeypatch.setattr(
        controller,
        "_normalize_rip_result",
        lambda *_a, **_k: (True, [item[0] for item in analyzed]),
    )
    monkeypatch.setattr(
        controller,
        "_stabilize_ripped_files",
        lambda *_a, **_k: (True, False),
    )
    monkeypatch.setattr(
        controller,
        "_verify_expected_sizes",
        lambda *_a, **_k: ("pass", "ok"),
    )
    monkeypatch.setattr(
        controller,
        "_verify_container_integrity",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(engine, "analyze_files", lambda *_a, **_k: analyzed)
    monkeypatch.setattr(controller, "write_session_summary", lambda: None)
    monkeypatch.setattr(controller, "flush_log", lambda: None)

    move_call: dict[str, object] = {}

    def fake_select_and_move(
        _titles_list,
        _is_tv,
        _title,
        _dest_folder,
        _extras_folder,
        _season,
        _year,
        expected_size_by_title=None,
        session_rip_path=None,
        session_meta=None,
        selected_title_ids=None,
        **_kwargs,
    ):
        move_call["selected_title_ids"] = selected_title_ids
        move_call["called"] = True
        return True

    monkeypatch.setattr(controller, "_select_and_move", fake_select_and_move)

    controller.run_movie_disc()

    insert_messages = [
        message for title, message in show_info_calls if title == "Insert Disc"
    ]
    assert insert_messages == ["Insert disc 1 and click OK when ready."]
    assert move_call["called"] is True
    assert move_call["selected_title_ids"] is None
    assert any(
        "switched to manual selection because no valid recommendation was available"
        in m.lower()
        for m in controller.gui.messages
    )


def test_run_smart_rip_cancel_at_scan_results_stops(tmp_path, monkeypatch):
    """Cancelling at Step 1 (scan results) should abort without ripping."""
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    temp_root.mkdir(parents=True, exist_ok=True)
    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(tmp_path / "movies")
    engine.cfg["tv_folder"] = str(tmp_path / "tv")

    controller.gui.show_info = lambda *_args, **_kwargs: None
    # Step 1: cancel
    controller.gui.show_scan_results_step = lambda _cl, _di=None: None

    disc_titles = [
        {"id": 0, "name": "Main", "duration": "120:00", "size": "4.0 GB",
         "duration_seconds": 7200, "size_bytes": 4_000_000_000},
    ]
    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)

    _ct = ClassifiedTitle(
        title=disc_titles[0], score=0.80, label="MAIN",
        confidence=0.95, reasons=["longest duration"],
    )
    monkeypatch.setattr(
        "controller.controller.classify_and_pick_main",
        lambda *_a, **_k: (_ct, [_ct]),
    )
    monkeypatch.setattr(
        "controller.controller.format_classification_log",
        lambda *_a, **_k: [],
    )

    rip_called = {"value": False}
    monkeypatch.setattr(engine, "run_job", lambda _j: (
        rip_called.update({"value": True}),
        SimpleNamespace(success=False, errors=[]),
    )[-1])

    controller.run_smart_rip()

    assert rip_called["value"] is False
    assert any("cancelled" in m.lower() for m in controller.gui.messages)


def test_run_smart_rip_cancel_at_output_plan_stops(tmp_path, monkeypatch):
    """Cancelling at Step 5 (output plan) should abort without ripping."""
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    temp_root.mkdir(parents=True, exist_ok=True)
    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(tmp_path / "movies")
    engine.cfg["tv_folder"] = str(tmp_path / "tv")

    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_yesno = lambda _prompt: True
    controller.gui.show_scan_results_step = lambda _cl, _di=None: "movie"
    controller.gui.ask_movie_setup = lambda **_kw: MovieSessionSetup(
        title="Test Movie", year="2024", edition="",
        metadata_provider="TMDB", metadata_id="",
        replace_existing=False, keep_raw=False, extras_mode="ask",
    )

    from shared.wizard_types import ContentSelection
    controller.gui.show_content_mapping_step = lambda _cl: ContentSelection(
        main_title_ids=[0], extra_title_ids=[], skip_title_ids=[],
    )
    # Step 5: cancel
    controller.gui.show_output_plan_step = lambda *_a, **_k: False

    disc_titles = [
        {"id": 0, "name": "Main", "duration": "120:00", "size": "4.0 GB",
         "duration_seconds": 7200, "size_bytes": 4_000_000_000},
    ]
    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)

    _ct = ClassifiedTitle(
        title=disc_titles[0], score=0.80, label="MAIN",
        confidence=0.95, reasons=["longest duration"],
    )
    monkeypatch.setattr(
        "controller.controller.classify_and_pick_main",
        lambda *_a, **_k: (_ct, [_ct]),
    )
    monkeypatch.setattr(
        "controller.controller.format_classification_log",
        lambda *_a, **_k: [],
    )

    rip_called = {"value": False}
    monkeypatch.setattr(engine, "run_job", lambda _j: (
        rip_called.update({"value": True}),
        SimpleNamespace(success=False, errors=[]),
    )[-1])

    controller.run_smart_rip()

    assert rip_called["value"] is False
    assert any("cancelled" in m.lower() for m in controller.gui.messages)


def test_run_smart_rip_same_disc_continue_reuses_saved_steps(tmp_path, monkeypatch):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    temp_root.mkdir(parents=True, exist_ok=True)
    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(tmp_path / "movies")
    engine.cfg["tv_folder"] = str(tmp_path / "tv")
    engine.last_disc_info = {"title": "FINDING_NEMO"}

    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_yesno = lambda _prompt: True

    prompt_summaries: list[str] = []
    controller.gui.show_same_disc_prompt_step = (
        lambda summary="": (
            prompt_summaries.append(summary),
            "continue",
        )[-1]
    )
    controller.gui.show_scan_results_step = (
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("scan results should be skipped on continue")
        )
    )
    controller.gui.ask_movie_setup = (
        lambda **_k: (_ for _ in ()).throw(
            AssertionError("movie setup should be skipped on continue")
        )
    )
    controller.gui.show_content_mapping_step = (
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("content mapping should be skipped on continue")
        )
    )
    controller.gui.show_output_plan_step = lambda *_a, **_k: False

    disc_titles = [
        {"id": 0, "name": "Main", "duration": "120:00", "size": "4.0 GB",
         "duration_seconds": 7200, "size_bytes": 4_000_000_000},
    ]
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)
    classified = ClassifiedTitle(
        title=disc_titles[0], score=0.80, label="MAIN",
        confidence=0.95, reasons=["longest duration"],
    )
    monkeypatch.setattr(
        controller,
        "_get_shared_classified_titles",
        lambda _titles: [classified],
    )
    monkeypatch.setattr(
        engine,
        "match_last_disc_memory",
        lambda *_a, **_k: {
            "match_type": "identity",
            "saved": {
                "disc_title": "Finding Nemo",
                "session_info": {
                    "media_type": "movie",
                    "title": "Finding Nemo",
                    "year": "2003",
                    "metadata_provider": "TMDB",
                    "metadata_id": "tmdb:12",
                    "content_mapping": {
                        "main_title_ids": [0],
                        "extra_title_ids": [],
                        "skip_title_ids": [],
                    },
                    "selected_title_ids": [0],
                },
            },
            "current": {},
        },
    )

    commit_flags: list[bool] = []
    monkeypatch.setattr(
        engine,
        "commit_current_disc_memory",
        lambda preserve_session_info=False: commit_flags.append(
            preserve_session_info
        ),
    )

    controller.run_smart_rip()

    assert commit_flags == [True]
    assert prompt_summaries and "Title: Finding Nemo" in prompt_summaries[0]
    assert any("continued from previous info" in m.lower() for m in controller.gui.messages)


def test_run_smart_rip_same_disc_continue_restores_tv_season_zero(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    tv_root = tmp_path / "tv"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)
    engine.cfg["tv_folder"] = str(tv_root)
    engine.last_disc_info = {"title": "MY_SHOW_SPECIALS"}

    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_yesno = lambda _prompt: True

    prompt_summaries: list[str] = []
    controller.gui.show_same_disc_prompt_step = (
        lambda summary="": (
            prompt_summaries.append(summary),
            "continue",
        )[-1]
    )
    controller.gui.show_scan_results_step = (
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("scan results should be skipped on continue")
        )
    )
    controller.gui.ask_tv_setup = (
        lambda **_k: (_ for _ in ()).throw(
            AssertionError("tv setup should be skipped on continue")
        )
    )
    controller.gui.show_content_mapping_step = (
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("content mapping should be skipped on continue")
        )
    )

    captured_output_plan_calls: list[str] = []
    controller.gui.show_output_plan_step = (
        lambda base_folder, _main_label, _extras_map, **_kwargs: (
            captured_output_plan_calls.append(base_folder),
            False,
        )[-1]
    )

    disc_titles = [
        {
            "id": 0,
            "name": "Episode",
            "duration": "24:00",
            "size": "1.1 GB",
            "duration_seconds": 1440,
            "size_bytes": 1_100_000_000,
        },
    ]
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)
    classified = ClassifiedTitle(
        title=disc_titles[0],
        score=0.80,
        label="MAIN",
        confidence=0.95,
        reasons=["single episode"],
    )
    monkeypatch.setattr(
        controller,
        "_get_shared_classified_titles",
        lambda _titles: [classified],
    )
    monkeypatch.setattr(
        engine,
        "match_last_disc_memory",
        lambda *_a, **_k: {
            "match_type": "identity",
            "saved": {
                "disc_title": "My Show Specials",
                "session_info": {
                    "media_type": "tv",
                    "title": "My Show",
                    "season": 0,
                    "metadata_provider": "TVDB",
                    "metadata_id": "tvdb:12345",
                    "content_mapping": {
                        "main_title_ids": [0],
                        "extra_title_ids": [],
                        "skip_title_ids": [],
                    },
                    "selected_title_ids": [0],
                },
            },
            "current": {},
        },
    )
    monkeypatch.setattr(engine, "commit_current_disc_memory", lambda **_k: None)

    controller.run_smart_rip()

    assert prompt_summaries and "Season 00" in prompt_summaries[0]
    assert captured_output_plan_calls
    assert captured_output_plan_calls[-1].endswith("Season 00")
    assert any(
        "tv: my show season 0 (continued from previous info)" in message.lower()
        for message in controller.gui.messages
    )


def test_run_smart_rip_same_disc_continue_restores_output_destination(tmp_path, monkeypatch):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    tv_root = tmp_path / "tv"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)
    engine.cfg["tv_folder"] = str(tv_root)
    engine.last_disc_info = {"title": "FINDING_NEMO"}

    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_yesno = lambda _prompt: True
    controller.gui.show_scan_results_step = (
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("scan results should be skipped on continue")
        )
    )
    controller.gui.ask_movie_setup = (
        lambda **_k: (_ for _ in ()).throw(
            AssertionError("movie setup should be skipped on continue")
        )
    )
    controller.gui.show_content_mapping_step = (
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("content mapping should be skipped on continue")
        )
    )

    restored_dest = str(tmp_path / "custom-dest" / "Finding Nemo")
    captured_output_plan_calls: list[tuple[str, str | None]] = []

    controller.gui.show_same_disc_prompt_step = lambda _summary="": "continue"
    controller.gui.show_output_plan_step = (
        lambda base_folder, _main_label, _extras_map, *, suggested_folder=None: (
            captured_output_plan_calls.append((base_folder, suggested_folder)),
            False,
        )[-1]
    )

    disc_titles = [
        {"id": 0, "name": "Main", "duration": "120:00", "size": "4.0 GB",
         "duration_seconds": 7200, "size_bytes": 4_000_000_000},
    ]
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)
    classified = ClassifiedTitle(
        title=disc_titles[0], score=0.80, label="MAIN",
        confidence=0.95, reasons=["longest duration"],
    )
    monkeypatch.setattr(controller, "_get_shared_classified_titles", lambda _titles: [classified])
    monkeypatch.setattr(
        engine,
        "match_last_disc_memory",
        lambda *_a, **_k: {
            "match_type": "identity",
            "saved": {
                "disc_title": "Finding Nemo",
                "session_info": {
                    "media_type": "movie",
                    "title": "Finding Nemo",
                    "year": "2003",
                    "metadata_provider": "TMDB",
                    "metadata_id": "tmdb:12",
                    "content_mapping": {
                        "main_title_ids": [0],
                        "extra_title_ids": [],
                        "skip_title_ids": [],
                    },
                    "selected_title_ids": [0],
                    "output_plan": {
                        "dest_folder": restored_dest,
                        "confirmed": False,
                    },
                },
            },
            "current": {},
        },
    )

    controller.run_smart_rip()

    assert captured_output_plan_calls
    assert captured_output_plan_calls[-1][0] == restored_dest
    assert captured_output_plan_calls[-1][1] is not None
    assert any(
        "output destination restored from previous info" in message.lower()
        for message in controller.gui.messages
    )


def test_run_smart_rip_same_disc_continue_restores_replace_existing_for_movie(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    temp_root.mkdir(parents=True, exist_ok=True)
    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["opt_confirm_before_move"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(tmp_path / "movies")
    engine.cfg["tv_folder"] = str(tmp_path / "tv")
    engine.last_disc_info = {"title": "FINDING_NEMO"}

    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_yesno = lambda _prompt: True
    controller.gui.show_same_disc_prompt_step = lambda _summary="": "continue"
    controller.gui.show_scan_results_step = (
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("scan results should be skipped on continue")
        )
    )
    controller.gui.ask_movie_setup = (
        lambda **_k: (_ for _ in ()).throw(
            AssertionError("movie setup should be skipped on continue")
        )
    )
    controller.gui.show_content_mapping_step = (
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("content mapping should be skipped on continue")
        )
    )
    controller.gui.show_output_plan_step = lambda *_a, **_k: True

    disc_titles = [
        {"id": 0, "name": "Main", "duration": "120:00", "size": "4.0 GB",
         "duration_seconds": 7200, "size_bytes": 4_000_000_000},
    ]
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)
    classified = ClassifiedTitle(
        title=disc_titles[0], score=0.80, label="MAIN",
        confidence=0.95, reasons=["longest duration"],
    )
    monkeypatch.setattr(
        controller,
        "_get_shared_classified_titles",
        lambda _titles: [classified],
    )
    monkeypatch.setattr(
        engine,
        "match_last_disc_memory",
        lambda *_a, **_k: {
            "match_type": "identity",
            "saved": {
                "disc_title": "Finding Nemo",
                "session_info": {
                    "media_type": "movie",
                    "title": "Finding Nemo",
                    "year": "2003",
                    "metadata_provider": "TMDB",
                    "metadata_id": "tmdb:12",
                    "replace_existing": True,
                    "content_mapping": {
                        "main_title_ids": [0],
                        "extra_title_ids": [],
                        "skip_title_ids": [],
                    },
                    "selected_title_ids": [0],
                },
            },
            "current": {},
        },
    )
    monkeypatch.setattr(engine, "commit_current_disc_memory", lambda **_k: None)
    monkeypatch.setattr(controller, "write_session_summary", lambda: None)
    monkeypatch.setattr(controller, "flush_log", lambda: None)
    monkeypatch.setattr(
        controller,
        "_cleanup_success_session_metadata",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        controller_module.shutil,
        "rmtree",
        lambda *_a, **_k: None,
    )

    rip_capture = {}

    def fake_run_job(job):
        rip_dir = Path(job.output)
        rip_dir.mkdir(parents=True, exist_ok=True)
        main_path = rip_dir / "title_t00_main.mkv"
        main_path.write_text("main", encoding="utf-8")
        rip_capture["main"] = str(main_path)
        engine.last_title_file_map = {0: [str(main_path)]}
        return SimpleNamespace(success=True, errors=[])

    monkeypatch.setattr(engine, "run_job", fake_run_job)
    monkeypatch.setattr(
        controller,
        "_normalize_rip_result",
        lambda *_a, **_k: (True, [rip_capture["main"]]),
    )
    monkeypatch.setattr(
        controller,
        "_stabilize_ripped_files",
        lambda *_a, **_k: (True, False),
    )
    monkeypatch.setattr(
        controller,
        "_verify_expected_sizes",
        lambda *_a, **_k: ("pass", "ok"),
    )
    monkeypatch.setattr(
        controller,
        "_verify_container_integrity",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(
        engine,
        "analyze_files",
        lambda *_a, **_k: [(rip_capture["main"], 7200.0, 4000.0)],
    )

    captured_move = {}

    def fake_select_and_move(
        _titles_list,
        _is_tv,
        _title,
        _dest_folder,
        _extras_folder,
        _season,
        _year,
        *,
        replace_existing=False,
        **_kwargs,
    ):
        captured_move["replace_existing"] = replace_existing
        return True

    monkeypatch.setattr(controller, "_select_and_move", fake_select_and_move)

    controller.run_smart_rip()

    assert captured_move["replace_existing"] is True
    assert any(
        "continued from previous info" in message.lower()
        for message in controller.gui.messages
    )


def test_run_smart_rip_same_disc_continue_uses_0000_for_blank_movie_year(
    tmp_path,
    monkeypatch,
):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root = tmp_path / "movies"
    movies_root.mkdir(parents=True, exist_ok=True)
    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)
    engine.cfg["tv_folder"] = str(tmp_path / "tv")
    engine.last_disc_info = {"title": "FINDING_NEMO"}

    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_yesno = lambda _prompt: True
    controller.gui.show_same_disc_prompt_step = lambda _summary="": "continue"
    controller.gui.show_scan_results_step = (
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("scan results should be skipped on continue")
        )
    )
    controller.gui.ask_movie_setup = (
        lambda **_k: (_ for _ in ()).throw(
            AssertionError("movie setup should be skipped on continue")
        )
    )
    controller.gui.show_content_mapping_step = (
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("content mapping should be skipped on continue")
        )
    )

    captured_output = {}

    def _show_output_plan_step(base_folder, main_label, _extras_map, **_kwargs):
        captured_output["base_folder"] = base_folder
        captured_output["main_label"] = main_label
        return False

    controller.gui.show_output_plan_step = _show_output_plan_step

    disc_titles = [
        {"id": 0, "name": "Main", "duration": "120:00", "size": "4.0 GB",
         "duration_seconds": 7200, "size_bytes": 4_000_000_000},
    ]
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)
    classified = ClassifiedTitle(
        title=disc_titles[0], score=0.80, label="MAIN",
        confidence=0.95, reasons=["longest duration"],
    )
    monkeypatch.setattr(
        controller,
        "_get_shared_classified_titles",
        lambda _titles: [classified],
    )
    monkeypatch.setattr(
        engine,
        "match_last_disc_memory",
        lambda *_a, **_k: {
            "match_type": "identity",
            "saved": {
                "disc_title": "Finding Nemo",
                "session_info": {
                    "media_type": "movie",
                    "title": "Finding Nemo",
                    "year": "",
                    "metadata_provider": "TMDB",
                    "metadata_id": "tmdb:12",
                    "content_mapping": {
                        "main_title_ids": [0],
                        "extra_title_ids": [],
                        "skip_title_ids": [],
                    },
                    "selected_title_ids": [0],
                },
            },
            "current": {},
        },
    )
    monkeypatch.setattr(engine, "commit_current_disc_memory", lambda **_k: None)

    session_updates = []
    monkeypatch.setattr(
        engine,
        "update_last_disc_session_state",
        lambda data: session_updates.append(dict(data)) or {"session_info": dict(data)},
    )

    controller.run_smart_rip()

    expected_folder = str(movies_root / "Finding Nemo (0000) [tmdbid-12]")
    assert captured_output["base_folder"] == expected_folder
    assert captured_output["main_label"] == "Finding Nemo (0000) [tmdbid-12].mkv"

    identity_updates = [
        update for update in session_updates if update.get("title") == "Finding Nemo"
    ]
    assert identity_updates
    assert identity_updates[-1]["year"] == "0000"
    assert any(
        "warning: no year" in message.lower()
        for message in controller.gui.messages
    )
    assert any(
        "continued from previous info" in message.lower()
        for message in controller.gui.messages
    )


def test_run_smart_rip_persists_path_and_output_plan_state(tmp_path, monkeypatch):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    tv_root = tmp_path / "tv"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)
    engine.cfg["tv_folder"] = str(tv_root)

    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_yesno = lambda _prompt: False
    controller.gui.show_scan_results_step = lambda *_a, **_k: "movie"
    controller.gui.ask_movie_setup = lambda **_kwargs: MovieSessionSetup(
        title="Finding Nemo",
        year="2003",
        edition="",
        metadata_provider="TMDB",
        metadata_id="tmdb:12",
        replace_existing=False,
        keep_raw=False,
        extras_mode="ask",
    )

    from shared.wizard_types import ContentSelection

    controller.gui.show_content_mapping_step = lambda _cl: ContentSelection(
        main_title_ids=[0],
        extra_title_ids=[],
        skip_title_ids=[],
    )
    controller.gui.show_output_plan_step = lambda *_a, **_k: False

    disc_titles = [
        {"id": 0, "name": "Main", "duration": "120:00", "size": "4.0 GB",
         "duration_seconds": 7200, "size_bytes": 4_000_000_000},
    ]
    classified = ClassifiedTitle(
        title=disc_titles[0], score=0.80, label="MAIN",
        confidence=0.95, reasons=["longest duration"],
    )

    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)
    monkeypatch.setattr(controller, "_get_shared_classified_titles", lambda _titles: [classified])
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(engine, "match_last_disc_memory", lambda *_a, **_k: None)

    commit_flags: list[bool] = []
    monkeypatch.setattr(
        engine,
        "commit_current_disc_memory",
        lambda preserve_session_info=False: commit_flags.append(preserve_session_info),
    )

    session_updates: list[dict] = []
    monkeypatch.setattr(
        engine,
        "update_last_disc_session_state",
        lambda data: session_updates.append(dict(data)) or {"session_info": dict(data)},
    )

    workflow_events: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        controller,
        "_record_workflow_event",
        lambda event_type, *, pipeline_step="", details=None: workflow_events.append(
            (event_type, pipeline_step, dict(details or {}))
        ),
    )

    controller.run_smart_rip()

    assert commit_flags == [False]
    assert any("run_path_overrides" in update for update in session_updates)
    assert any("session_paths" in update for update in session_updates)

    output_plan_updates = [
        update["output_plan"]
        for update in session_updates
        if "output_plan" in update
    ]
    assert output_plan_updates
    assert output_plan_updates[-1]["confirmed"] is False
    assert output_plan_updates[-1]["dest_folder"] == str(
        movies_root / "Finding Nemo (2003) [tmdbid-12]"
    )
    assert output_plan_updates[-1]["destination_edited"] is False
    assert output_plan_updates[-1]["action"] == "cancel"
    assert "Finding Nemo" in output_plan_updates[-1]["main_label"]
    assert output_plan_updates[-1]["main_label"].endswith(".mkv")

    assert any(event[0] == "path_setup_resolved" for event in workflow_events)
    output_events = [
        event for event in workflow_events if event[0] == "output_plan_decision"
    ]
    assert output_events
    assert output_events[-1][2]["dest_folder"] == str(
        movies_root / "Finding Nemo (2003) [tmdbid-12]"
    )
    assert output_events[-1][2]["destination_edited"] is False
    assert output_events[-1][2]["action"] == "cancel"
    assert output_events[-1][2]["confirmed"] is False


def test_run_smart_rip_same_disc_start_fresh_uses_normal_flow(tmp_path, monkeypatch):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    temp_root.mkdir(parents=True, exist_ok=True)
    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(tmp_path / "movies")
    engine.cfg["tv_folder"] = str(tmp_path / "tv")
    engine.last_disc_info = {"title": "FINDING_NEMO"}

    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_yesno = lambda _prompt: True
    controller.gui.show_same_disc_prompt_step = lambda _summary="": "fresh"

    scan_results_calls = {"count": 0}
    movie_setup_calls = {"count": 0}

    def _show_scan_results(_cl, _di=None):
        scan_results_calls["count"] += 1
        return "movie"

    def _ask_movie_setup(**_kwargs):
        movie_setup_calls["count"] += 1
        return MovieSessionSetup(
            title="Fresh Movie", year="2024", edition="",
            metadata_provider="TMDB", metadata_id="",
            replace_existing=False, keep_raw=False, extras_mode="ask",
        )

    controller.gui.show_scan_results_step = _show_scan_results
    controller.gui.ask_movie_setup = _ask_movie_setup

    from shared.wizard_types import ContentSelection

    controller.gui.show_content_mapping_step = lambda _cl: ContentSelection(
        main_title_ids=[0], extra_title_ids=[], skip_title_ids=[],
    )
    controller.gui.show_output_plan_step = lambda *_a, **_k: False

    disc_titles = [
        {"id": 0, "name": "Main", "duration": "120:00", "size": "4.0 GB",
         "duration_seconds": 7200, "size_bytes": 4_000_000_000},
    ]
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)
    classified = ClassifiedTitle(
        title=disc_titles[0], score=0.80, label="MAIN",
        confidence=0.95, reasons=["longest duration"],
    )
    monkeypatch.setattr(
        controller,
        "_get_shared_classified_titles",
        lambda _titles: [classified],
    )
    monkeypatch.setattr(
        engine,
        "match_last_disc_memory",
        lambda *_a, **_k: {
            "match_type": "identity",
            "saved": {
                "disc_title": "Finding Nemo",
                "session_info": {
                    "media_type": "movie",
                    "title": "Finding Nemo",
                    "year": "2003",
                },
            },
            "current": {},
        },
    )

    commit_flags: list[bool] = []
    monkeypatch.setattr(
        engine,
        "commit_current_disc_memory",
        lambda preserve_session_info=False: commit_flags.append(
            preserve_session_info
        ),
    )

    controller.run_smart_rip()

    assert commit_flags == [False]
    assert scan_results_calls["count"] == 1
    assert movie_setup_calls["count"] == 1


def test_run_smart_rip_same_disc_cancel_stops_without_commit(tmp_path, monkeypatch):
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    temp_root.mkdir(parents=True, exist_ok=True)
    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(tmp_path / "movies")
    engine.cfg["tv_folder"] = str(tmp_path / "tv")
    engine.last_disc_info = {"title": "FINDING_NEMO"}

    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_yesno = lambda _prompt: True
    controller.gui.show_same_disc_prompt_step = lambda _summary="": "cancel"
    controller.gui.show_scan_results_step = (
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("scan results should not run after cancel")
        )
    )

    disc_titles = [
        {"id": 0, "name": "Main", "duration": "120:00", "size": "4.0 GB",
         "duration_seconds": 7200, "size_bytes": 4_000_000_000},
    ]
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)
    classified = ClassifiedTitle(
        title=disc_titles[0], score=0.80, label="MAIN",
        confidence=0.95, reasons=["longest duration"],
    )
    monkeypatch.setattr(
        controller,
        "_get_shared_classified_titles",
        lambda _titles: [classified],
    )
    monkeypatch.setattr(
        engine,
        "match_last_disc_memory",
        lambda *_a, **_k: {
            "match_type": "identity",
            "saved": {"disc_title": "Finding Nemo", "session_info": {}},
            "current": {},
        },
    )

    commit_flags: list[bool] = []
    monkeypatch.setattr(
        engine,
        "commit_current_disc_memory",
        lambda preserve_session_info=False: commit_flags.append(
            preserve_session_info
        ),
    )

    controller.run_smart_rip()

    assert commit_flags == []
    assert any("cancelled" in m.lower() for m in controller.gui.messages)


def test_ensure_session_paths_raises_before_init():
    """_ensure_session_paths() must raise RuntimeError if called before init."""
    controller, _engine = _controller_with_engine()
    controller.session_paths = None

    import pytest
    with pytest.raises(RuntimeError, match="session_paths not initialized"):
        controller._ensure_session_paths()


def test_ensure_session_paths_passes_after_init(tmp_path):
    """_ensure_session_paths() must not raise once _init_session_paths ran."""
    controller, engine = _controller_with_engine()
    engine.cfg["temp_folder"] = str(tmp_path / "temp")
    engine.cfg["movies_folder"] = str(tmp_path / "movies")
    engine.cfg["tv_folder"] = str(tmp_path / "tv")

    controller._init_session_paths({})
    controller._ensure_session_paths()  # must not raise


def test_verify_container_integrity_uses_preanalyzed_data(monkeypatch, tmp_path):
    """_verify_container_integrity(analyzed=...) must not call analyze_files."""
    controller, engine = _controller_with_engine()

    f = tmp_path / "title_t00.mkv"
    f.write_text("fake")

    analyze_called = {"times": 0}

    def fake_analyze(files, log):
        analyze_called["times"] += 1
        return [(str(files[0]), 5400.0, 4200)]

    monkeypatch.setattr(engine, "analyze_files", fake_analyze)

    # Pass pre-analyzed data â€" analyze_files must NOT be called.
    preanalyzed = [(str(f), 5400.0, 4200)]
    result = controller._verify_container_integrity([str(f)], analyzed=preanalyzed)

    assert result is True
    assert analyze_called["times"] == 0


def test_verify_container_integrity_calls_analyze_when_no_preanalyzed(
    monkeypatch, tmp_path
):
    """Without analyzed=, _verify_container_integrity must call analyze_files."""
    controller, engine = _controller_with_engine()

    f = tmp_path / "title_t00.mkv"
    f.write_text("fake")

    analyze_called = {"times": 0}

    def fake_analyze(files, log):
        analyze_called["times"] += 1
        return [(str(files[0]), 5400.0, 4200)]

    monkeypatch.setattr(engine, "analyze_files", fake_analyze)

    result = controller._verify_container_integrity([str(f)])

    assert result is True
    assert analyze_called["times"] == 1


def test_verify_container_integrity_fails_on_zero_duration(tmp_path):
    """A file with duration <= 0 in pre-analyzed data must fail integrity."""
    controller, _engine = _controller_with_engine()

    f = tmp_path / "corrupt.mkv"
    f.write_text("bad")

    preanalyzed = [(str(f), 0.0, 100)]
    result = controller._verify_container_integrity([str(f)], analyzed=preanalyzed)

    assert result is False
    assert any("Container integrity check failed" in m for m in controller.gui.messages)


def test_verify_container_integrity_fails_on_count_mismatch(tmp_path):
    """If analyzed count < file count, integrity check must fail."""
    controller, _engine = _controller_with_engine()

    f1 = tmp_path / "a.mkv"
    f2 = tmp_path / "b.mkv"
    f1.write_text("a")
    f2.write_text("b")

    # Only one result for two files.
    preanalyzed = [(str(f1), 5400.0, 4200)]
    result = controller._verify_container_integrity(
        [str(f1), str(f2)], analyzed=preanalyzed
    )

    assert result is False
    assert any("incomplete" in m for m in controller.gui.messages)


def test_size_advisory_log_uses_arrow_format(tmp_path, monkeypatch):
    """Post-stabilization advisory must use 'expected X GB â†' threshold Y GB' format."""
    import time as time_module

    controller, engine = _controller_with_engine({
        **_engine_cfg(),
        "opt_file_stabilization": True,
        "opt_stabilize_timeout_seconds": 5,
        "opt_stabilize_required_polls": 1,
        "opt_min_rip_size_gb": 1,
        "opt_expected_size_ratio_pct": 70,
        "opt_hard_fail_ratio_pct": 40,
    })

    tiny = tmp_path / "title_t02.mkv"
    tiny.write_bytes(b"x" * (1 * 1024 * 1024))  # 1 MB

    monkeypatch.setattr(
        "controller.controller.time.sleep", lambda _s: None
    )
    monkeypatch.setattr(
        controller, "_stabilize_file",
        lambda _f, _timeout, _polls: (True, False)
    )

    # 500 MB expected â†' floor = 250 MB; actual is 1 MB so advisory fires.
    expected_size_by_title = {2: 500 * 1024 * 1024}
    controller._stabilize_ripped_files(
        [str(tiny)], expected_size_by_title=expected_size_by_title
    )

    advisory_lines = [
        m for m in controller.gui.messages
        if "below threshold" in m
    ]
    assert advisory_lines, "Expected an advisory log line"
    assert "→" in advisory_lines[0]
    assert "expected" in advisory_lines[0]
    assert "threshold" in advisory_lines[0]


# â"€â"€ Degraded rip classification â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def test_nonzero_exit_no_files_is_real_failure(tmp_path, monkeypatch):
    """rip_selected_titles: non-zero exit + no files = real failure."""
    controller, engine = _controller_with_engine(
        _engine_cfg(opt_auto_retry=False, opt_retry_attempts=1)
    )

    def fake_run(_cmd, _on_progress, _on_log):
        return False  # exit error, no files written

    monkeypatch.setattr(engine, "_run_rip_process", fake_run)

    success, failed = engine.rip_selected_titles(
        str(tmp_path), [0], on_progress=lambda _p: None, on_log=controller.log
    )

    assert success is False or 1 in failed
    assert engine.last_degraded_titles == []


def test_degraded_rip_added_to_session_report(tmp_path, monkeypatch):
    """_warn_degraded_rips() must append a warning to session_report."""
    controller, engine = _controller_with_engine(
        _engine_cfg(opt_auto_retry=False, opt_retry_attempts=1)
    )

    def fake_run(_cmd, _on_progress, _on_log):
        Path(tmp_path, "title_t00.mkv").write_text("data")
        return False

    monkeypatch.setattr(engine, "_run_rip_process", fake_run)

    engine.rip_selected_titles(
        str(tmp_path), [0], on_progress=lambda _p: None, on_log=controller.log
    )
    controller._warn_degraded_rips()

    assert any("degraded rip" in line for line in controller.session_report)


def test_session_summary_shows_warnings_on_degraded_rip(tmp_path, monkeypatch):
    """write_session_summary: COMPLETED state + session_report â†' 'Completed with warnings'."""
    controller, engine = _controller_with_engine()

    controller._reset_state_machine()
    # Manually drive state to COMPLETED with a warning in session_report.
    for state in (
        SessionState.SCANNED,
        SessionState.RIPPED,
        SessionState.STABILIZED,
        SessionState.VALIDATED,
        SessionState.MOVED,
        SessionState.COMPLETED,
    ):
        controller.sm.transition(state)

    controller.session_report.append("Title 1: MakeMKV read errors (degraded rip)")

    controller.write_session_summary()

    assert any(
        "Completed with warnings" in m for m in controller.gui.messages
    )
    assert not any(
        "All discs completed successfully" in m for m in controller.gui.messages
    )


def test_session_summary_clean_when_no_warnings():
    """write_session_summary: COMPLETED state + empty session_report â†' clean success message."""
    controller, _engine = _controller_with_engine()

    controller._reset_state_machine()
    for state in (
        SessionState.SCANNED,
        SessionState.RIPPED,
        SessionState.STABILIZED,
        SessionState.VALIDATED,
        SessionState.MOVED,
        SessionState.COMPLETED,
    ):
        controller.sm.transition(state)

    controller.write_session_summary()

    assert any(
        "All discs completed successfully" in m for m in controller.gui.messages
    )


# â"€â"€ Fallback logging â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def test_fallback_title_from_mode_logs_but_does_not_report(monkeypatch):
    """Auto-title fallback is informational and must not pollute session summary."""
    controller, _engine = _controller_with_engine()

    monkeypatch.setattr(
        "controller.controller.build_fallback_title",
        lambda *_a, **_kw: "Auto-Title-2026",
    )

    result = controller._fallback_title_from_mode()

    assert result == "Auto-Title-2026"
    assert any("Auto-Title-2026" in line for line in controller.gui.messages)
    assert controller.session_report == []


def test_build_identity_title_seed_prefers_disc_title_over_timestamp_mode():
    controller, engine = _controller_with_engine(
        _engine_cfg(opt_naming_mode="timestamp")
    )
    engine.last_disc_info = {"title": "Kung_Fu_Panda_3"}

    seed = controller._build_identity_title_seed(
        disc_titles=[
            {
                "id": 0,
                "name": "Title 2",
                "duration_seconds": 5679,
                "size_bytes": 4_600_000_000,
            }
        ],
        classified=[],
    )

    assert seed == "Kung Fu Panda 3"


def test_parse_identity_ai_response_accepts_json_code_fence():
    controller, _engine = _controller_with_engine()

    suggestion = controller._parse_identity_ai_response(
        """```json
{"title":"Kung Fu Panda 3 (2016)","year":"","season":"","metadata_provider":"TMDB","metadata_id":"950"}
```""",
        fallback_title="Fallback Title",
        is_tv=False,
        backend_tag="LOCAL",
    )

    assert suggestion is not None
    assert suggestion.title == "Kung Fu Panda 3"
    assert suggestion.year == "2016"
    assert suggestion.metadata_provider == "TMDB"
    assert suggestion.metadata_id == "tmdb:950"
    assert suggestion.source == "ai:local"


def test_run_smart_rip_abort_during_scan_stops_before_wizard(monkeypatch):
    """Aborting during disc scan should stop before any wizard steps."""
    controller, engine = _controller_with_engine()
    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["movies_folder"] = r"C:\Movies"
    engine.cfg["tv_folder"] = r"C:\TV"
    engine.cfg["temp_folder"] = r"C:\Temp"

    controller.gui.show_info = lambda *_args, **_kwargs: None

    def fake_scan():
        engine.abort()
        return None

    controller.scan_with_retry = fake_scan

    wizard_called = {"value": False}
    original_scan_results = controller.gui.show_scan_results_step

    def fail_scan_results(*_args, **_kwargs):
        wizard_called["value"] = True
        raise AssertionError("show_scan_results_step should not run after abort")

    controller.gui.show_scan_results_step = fail_scan_results

    controller.run_smart_rip()

    assert wizard_called["value"] is False


def test_run_smart_rip_no_titles_uses_async_terminal_error_when_available():
    controller, engine = _controller_with_engine()
    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["movies_folder"] = r"C:\Movies"
    engine.cfg["tv_folder"] = r"C:\TV"
    engine.cfg["temp_folder"] = r"C:\Temp"

    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.scan_with_retry = lambda: []

    sync_calls = []
    async_calls = []

    controller.gui.show_error = (
        lambda title, msg: sync_calls.append((title, msg))
    )
    controller.gui.show_error_async = (
        lambda title, msg: async_calls.append((title, msg))
    )

    controller.run_smart_rip()

    assert sync_calls == []
    assert async_calls == [
        (
            "No Titles Found",
            "Disc was readable, but no rip-able titles were found.\n\n"
            "This can happen with unsupported or empty media.",
        )
    ]
    assert controller.sm.state == SessionState.FAILED
    assert any(
        "no titles were found on this disc" in message.lower()
        for message in controller.gui.messages
    )


# â"€â"€ Duration sanity check (tiered + aggregation + clamping) â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def test_integrity_severe_duration_warns_only_no_size(tmp_path):
    """< 50% duration alone â†' severe warning but still returns True."""
    controller, _engine = _controller_with_engine()
    f = tmp_path / "title_t00.mkv"
    f.write_text("fake")

    # 600 s actual, 3600 s expected â†' 16.7% â€" severe tier (long title).
    preanalyzed = [(str(f), 600.0, 100)]
    result = controller._verify_container_integrity(
        [str(f)],
        analyzed=preanalyzed,
        expected_durations={str(f): 3600.0},
    )

    assert result is True  # warning only when size not confirmed
    assert any("Severe" in line or "severe" in line for line in controller.session_report)


def test_integrity_severe_duration_and_size_logs_error(tmp_path):
    """< 50% duration AND < 50% size (above 200MB floor) â†' TRUNCATION ERROR."""
    controller, _engine = _controller_with_engine()
    f = tmp_path / "title_t00.mkv"
    f.write_text("fake")

    # 600 s (16.7%), 400 MB actual vs 4000 MB expected (10%) â€" both severe.
    preanalyzed = [(str(f), 600.0, 400)]  # size_mb = 400 (above 200MB floor)
    result = controller._verify_container_integrity(
        [str(f)],
        analyzed=preanalyzed,
        expected_durations={str(f): 3600.0},
        expected_sizes={str(f): 4000 * 1024 * 1024},
    )

    assert result is True  # error in report, still not hard fail (no strict)
    assert any("TRUNCATION ERROR" in line for line in controller.session_report)


def test_integrity_small_expected_size_disables_escalation(tmp_path):
    """expected_size < 200 MB floor â†' size signal ignored, no escalation."""
    controller, _engine = _controller_with_engine()
    f = tmp_path / "title_t00.mkv"
    f.write_text("fake")

    # 600 s (16.7%) duration â€" would be severe. But expected_size only 50 MB
    # â€" below 200 MB floor so size signal must be ignored.
    preanalyzed = [(str(f), 600.0, 10)]  # actual 10 MB
    result = controller._verify_container_integrity(
        [str(f)],
        analyzed=preanalyzed,
        expected_durations={str(f): 3600.0},
        expected_sizes={str(f): 50 * 1024 * 1024},  # 50 MB â€" below floor
    )

    assert result is True
    assert not any("TRUNCATION ERROR" in line for line in controller.session_report)
    assert any("Severe" in line for line in controller.session_report)  # dur warning still fires


def test_integrity_strict_mode_fails_on_likely_truncation(tmp_path):
    """Strict mode: likely truncation (50-75% duration) â†' returns False."""
    controller, engine = _controller_with_engine()
    engine.cfg["opt_strict_mode"] = True
    f = tmp_path / "title_t00.mkv"
    f.write_text("fake")

    # 2000 s actual, 3600 s expected â†' 55.6% â€" likely truncation tier.
    preanalyzed = [(str(f), 2000.0, 100)]
    result = controller._verify_container_integrity(
        [str(f)],
        analyzed=preanalyzed,
        expected_durations={str(f): 3600.0},
    )

    assert result is False
    assert any("Strict mode" in line for line in controller.gui.messages)


def test_integrity_minor_mismatch_warns_but_passes(tmp_path):
    """75â€"90% duration â†' minor warning, still returns True."""
    controller, _engine = _controller_with_engine()
    f = tmp_path / "title_t00.mkv"
    f.write_text("fake")

    # 2880 s actual, 3600 s expected â†' 80% â€" minor tier.
    preanalyzed = [(str(f), 2880.0, 100)]
    result = controller._verify_container_integrity(
        [str(f)],
        analyzed=preanalyzed,
        expected_durations={str(f): 3600.0},
    )

    assert result is True
    assert any("Minor" in line or "minor" in line for line in controller.session_report)


def test_integrity_normal_variance_no_warning(tmp_path):
    """>= 90% duration â†' no warning at all."""
    controller, _engine = _controller_with_engine()
    f = tmp_path / "title_t00.mkv"
    f.write_text("fake")

    # 3300 s actual, 3600 s expected â†' 91.7% â€" normal variance.
    preanalyzed = [(str(f), 3300.0, 100)]
    result = controller._verify_container_integrity(
        [str(f)],
        analyzed=preanalyzed,
        expected_durations={str(f): 3600.0},
    )

    assert result is True
    assert controller.session_report == []


def test_integrity_multi_file_title_aggregates_before_comparing(tmp_path):
    """Two files from one title: aggregate duration before comparing â€" no false warning."""
    controller, _engine = _controller_with_engine()
    f1 = tmp_path / "part1.mkv"
    f2 = tmp_path / "part2.mkv"
    f1.write_text("p1")
    f2.write_text("p2")

    # Each file is 1800 s (30 min). Expected total 3600 s.
    # Per-file: 1800/3600 = 50% â†' would trigger "likely" warning.
    # Aggregated: 3600/3600 = 100% â†' no warning.
    preanalyzed = [(str(f1), 1800.0, 200), (str(f2), 1800.0, 200)]
    result = controller._verify_container_integrity(
        [str(f1), str(f2)],
        analyzed=preanalyzed,
        expected_durations={str(f1): 3600.0, str(f2): 0},  # expect only on group
        title_file_map={0: [str(f1), str(f2)]},
    )

    assert result is True
    assert controller.session_report == []


def test_integrity_multi_file_dedup_only_one_warning(tmp_path):
    """Two files from one title both below threshold â†' only ONE warning emitted."""
    controller, _engine = _controller_with_engine()
    f1 = tmp_path / "part1.mkv"
    f2 = tmp_path / "part2.mkv"
    f1.write_text("p1")
    f2.write_text("p2")

    # Total 600 s, expected 3600 s â†' 16.7% â€" severe, one warning for the group.
    preanalyzed = [(str(f1), 300.0, 100), (str(f2), 300.0, 100)]
    result = controller._verify_container_integrity(
        [str(f1), str(f2)],
        analyzed=preanalyzed,
        expected_durations={str(f1): 1800.0, str(f2): 1800.0},
        title_file_map={0: [str(f1), str(f2)]},
    )

    assert result is True
    # Only one warning entry for the title group, not two.
    warn_lines = [l for l in controller.session_report if "Severe" in l or "severe" in l]
    assert len(warn_lines) == 1



# ---------------------------------------------------------------------------
# Library-scanning helpers â€" append / gap-fill / existing show support
# ---------------------------------------------------------------------------

# --- get_next_episode gap-fill logic ---

def test_get_next_episode_empty_set_returns_1():
    """No existing episodes â†' first episode is 1."""
    c, _ = _controller_with_engine()
    assert c.get_next_episode(set()) == 1


def test_get_next_episode_appends_after_max():
    """Contiguous set â†' suggest next after the last."""
    c, _ = _controller_with_engine()
    assert c.get_next_episode({1, 2, 3}) == 4


def test_get_next_episode_fills_gap():
    """Missing episode 3 â†' suggest 3, not 6."""
    c, _ = _controller_with_engine()
    assert c.get_next_episode({1, 2, 4, 5}) == 3


def test_get_next_episode_fills_earliest_gap():
    """Multiple gaps â†' always return the lowest missing."""
    c, _ = _controller_with_engine()
    # 2 and 4 are both missing; 2 is earlier
    assert c.get_next_episode({1, 3, 5}) == 2


# --- _scan_episode_files (replaces _scan_highest_episode internally) ---

def test_scan_highest_episode_returns_zero_for_empty_folder(tmp_path):
    """No episode files â†' returns 0 (first disc starts at episode 1)."""
    controller, _ = _controller_with_engine()
    result = controller._scan_highest_episode(str(tmp_path), 1)
    assert result == 0


def test_scan_highest_episode_detects_existing_episodes(tmp_path):
    """Three S01Exx files present â†' returns highest episode number."""
    controller, _ = _controller_with_engine()
    (tmp_path / "Show - S01E01 - Pilot.mkv").write_text("")
    (tmp_path / "Show - S01E02 - Second.mkv").write_text("")
    (tmp_path / "Show - S01E08 - Eight.mkv").write_text("")
    result = controller._scan_highest_episode(str(tmp_path), 1)
    assert result == 8


def test_scan_highest_episode_ignores_other_seasons(tmp_path):
    """Files from a different season are not counted."""
    controller, _ = _controller_with_engine()
    (tmp_path / "Show - S01E05 - One.mkv").write_text("")
    (tmp_path / "Show - S02E10 - Two.mkv").write_text("")
    result = controller._scan_highest_episode(str(tmp_path), 1)
    assert result == 5


def test_scan_highest_episode_case_insensitive(tmp_path):
    """Filenames with mixed-case S/E markers are still detected."""
    controller, _ = _controller_with_engine()
    (tmp_path / "show - s01e03 - lower.mkv").write_text("")
    result = controller._scan_highest_episode(str(tmp_path), 1)
    assert result == 3


def test_scan_highest_episode_nonexistent_folder():
    """Non-existent folder returns 0 safely (no exception)."""
    controller, _ = _controller_with_engine()
    result = controller._scan_highest_episode("/path/that/does/not/exist", 1)
    assert result == 0


def test_scan_highest_episode_returns_zero_when_dest_none():
    """None dest_folder returns 0 (TV path not yet created)."""
    controller, _ = _controller_with_engine()
    result = controller._scan_highest_episode(None, 1)
    assert result == 0


def test_scan_episode_files_detects_1x01_format(tmp_path):
    """NxEE filename format (1x03) is recognised."""
    c, _ = _controller_with_engine()
    (tmp_path / "Show 1x01 Title.mkv").write_text("")
    (tmp_path / "Show 1x02 Title.mkv").write_text("")
    result = c._scan_episode_files(str(tmp_path), 1)
    assert result == {1, 2}


def test_scan_episode_files_detects_episode_n_format(tmp_path):
    """'Episode N' filename format is recognised."""
    c, _ = _controller_with_engine()
    (tmp_path / "Episode 4.mkv").write_text("")
    (tmp_path / "episode 7.mkv").write_text("")
    result = c._scan_episode_files(str(tmp_path), 1)
    assert 4 in result
    assert 7 in result


# --- _scan_library_folder ---

def test_scan_library_folder_detects_season_dirs(tmp_path):
    """Season subdirs with episode files are returned keyed by season number."""
    c, _ = _controller_with_engine()
    s1 = tmp_path / "Season 01"
    s1.mkdir()
    (s1 / "Show - S01E01.mkv").write_text("")
    (s1 / "Show - S01E02.mkv").write_text("")
    s2 = tmp_path / "Season 02"
    s2.mkdir()
    (s2 / "Show - S02E01.mkv").write_text("")

    result = c._scan_library_folder(str(tmp_path))
    assert 1 in result and result[1] == [1, 2]
    assert 2 in result and result[2] == [1]


def test_scan_library_folder_ignores_non_season_dirs(tmp_path):
    """Non-season directories are ignored, while Specials maps to season 0."""
    c, _ = _controller_with_engine()
    (tmp_path / "Extras").mkdir()
    (tmp_path / "Specials").mkdir()
    (tmp_path / "Specials" / "Episode 1.mkv").write_text("")
    s1 = tmp_path / "Season 01"
    s1.mkdir()
    (s1 / "Show - S01E01.mkv").write_text("")

    result = c._scan_library_folder(str(tmp_path))
    assert set(result.keys()) == {0, 1}
    assert result[0] == [1]


def test_scan_library_folder_empty_show_root(tmp_path):
    """Empty show root (no season folders) returns an empty dict."""
    c, _ = _controller_with_engine()
    result = c._scan_library_folder(str(tmp_path))
    assert result == {}


def test_scan_library_folder_nonexistent_path():
    """Non-existent path returns empty dict without raising."""
    c, _ = _controller_with_engine()
    result = c._scan_library_folder("/does/not/exist")
    assert result == {}


def test_scan_library_folder_returns_sorted_episodes(tmp_path):
    """Episode lists in the returned dict are sorted ascending."""
    c, _ = _controller_with_engine()
    s1 = tmp_path / "Season 01"
    s1.mkdir()
    for ep in (5, 1, 3, 2, 4):
        (s1 / f"Show - S01E{ep:02d}.mkv").write_text("")

    result = c._scan_library_folder(str(tmp_path))
    assert result[1] == [1, 2, 3, 4, 5]


# ---------------------------------------------------------------------------
# Multi-episode filenames (S01E01E02.mkv), Season 00, duplicate protection
# ---------------------------------------------------------------------------

# --- multi-episode filename detection ---

def test_multi_episode_file_contributes_both_episodes(tmp_path):
    """S01E01E02.mkv must register BOTH E01 and E02."""
    c, _ = _controller_with_engine()
    (tmp_path / "Show - S01E01E02 - Title.mkv").write_text("")
    result = c._scan_episode_files(str(tmp_path), 1)
    assert result == {1, 2}


def test_multi_episode_file_three_episodes(tmp_path):
    """S01E01E02E03.mkv registers three episodes."""
    c, _ = _controller_with_engine()
    (tmp_path / "Show - S01E01E02E03 - Title.mkv").write_text("")
    result = c._scan_episode_files(str(tmp_path), 1)
    assert result == {1, 2, 3}


def test_multi_episode_file_does_not_report_gap_for_covered_range(tmp_path):
    """When S01E01E02.mkv exists, get_next_episode should suggest E03, not E02."""
    c, _ = _controller_with_engine()
    (tmp_path / "Show - S01E01E02.mkv").write_text("")
    existing = c._scan_episode_files(str(tmp_path), 1)
    assert c.get_next_episode(existing) == 3


def test_multi_episode_ignores_wrong_season(tmp_path):
    """S02E01E02.mkv must not register episodes for season 1."""
    c, _ = _controller_with_engine()
    (tmp_path / "Show - S02E01E02 - Title.mkv").write_text("")
    result = c._scan_episode_files(str(tmp_path), 1)
    assert result == set()


# --- Season 00 / Specials ---

def test_scan_library_folder_detects_season_00(tmp_path):
    """'Season 00' directory is returned as season 0."""
    c, _ = _controller_with_engine()
    s0 = tmp_path / "Season 00"
    s0.mkdir()
    (s0 / "Show - S00E01 - Special.mkv").write_text("")
    result = c._scan_library_folder(str(tmp_path))
    assert 0 in result
    assert result[0] == [1]


def test_scan_library_folder_detects_specials_folder(tmp_path):
    """A folder named 'Specials' maps to season 0."""
    c, _ = _controller_with_engine()
    sp = tmp_path / "Specials"
    sp.mkdir()
    (sp / "Episode 1.mkv").write_text("")
    result = c._scan_library_folder(str(tmp_path))
    assert 0 in result


def test_scan_library_folder_season_00_logs_detection(tmp_path):
    """Detecting Season 00 emits a log line (not silently ignored)."""
    c, _ = _controller_with_engine()
    s0 = tmp_path / "Season 00"
    s0.mkdir()
    (s0 / "Show - S00E01.mkv").write_text("")
    c._scan_library_folder(str(tmp_path))
    assert any("Specials" in m or "Season 00" in m for m in c.session_log)


# --- _episodes_from_filename edge cases ---

def test_episodes_from_filename_single(tmp_path):
    """Standard S01E05 returns {5}."""
    c, _ = _controller_with_engine()
    assert c._episodes_from_filename("Show - S01E05 - Title.mkv", 1) == {5}


def test_episodes_from_filename_wrong_season_returns_empty(tmp_path):
    """S02E05 returns empty set when queried for season 1."""
    c, _ = _controller_with_engine()
    assert c._episodes_from_filename("Show - S02E05.mkv", 1) == set()


def test_build_ai_session_facts_returns_compact_workflow_snapshot():
    controller, engine = _controller_with_engine()
    controller.workflow_session_id = "wf-123"
    controller.session_paths = {
        "temp_folder": r"C:\temp",
        "movies_folder": r"C:\movies",
        "tv_folder": r"C:\tv",
    }
    controller.diagnostics.update_context(
        session_mode="smart_rip",
        pipeline_step="output_plan",
    )
    engine.current_disc_memory = {
        "disc_title": "Finding Nemo",
        "volume_id": "DISC001",
        "identity_hash": "identity-abc",
        "structure_hash": "structure-def",
        "title_count": 6,
        "total_duration_seconds": 9300,
        "total_size_bytes": 6_200_000_000,
        "session_info": {
            "media_type": "movie",
            "title": "Finding Nemo",
            "year": "2003",
            "edition": "Theatrical Cut",
            "metadata_provider": "TMDB",
            "metadata_id": "tmdb:12",
            "selected_title_ids": [1, 3, 4, 5, 6],
            "content_mapping": {
                "main_title_ids": [1],
                "extra_title_ids": [3, 4, 5, 6],
                "skip_title_ids": [2],
            },
            "extras_assignments": {
                "3": "Featurettes",
                "4": "Featurettes",
            },
            "output_plan": {
                "dest_folder": r"C:\movies\Finding Nemo (2003)",
                "suggested_dest_folder": r"C:\movies\Finding Nemo (2003)",
                "main_label": "Finding Nemo (2003).mkv",
                "destination_edited": False,
                "confirmed": True,
                "extras_preview": {
                    "Featurettes": ["Title 3.mkv", "Title 4.mkv"],
                },
            },
        },
    }
    engine.last_disc_info = {
        "title": "Finding Nemo",
        "volume_id": "DISC001",
        "lang_name": "English",
    }
    engine.last_drive_info = {
        "disc_type": "Blu-ray",
        "libre_drive": "enabled",
        "uhd_friendly": True,
    }
    issue_summary = MakeMKVIssueSummary(
        total_messages=3,
        scsi_error_count=1,
        hardware_timeout_count=1,
        sample_messages=["SCSI error - MEDIUM ERROR"],
    )
    issue_summary.affected_paths.update({"AACS/MKB_RO.inf": 1})
    engine.last_scan_issue_summary = issue_summary

    facts = controller.build_ai_session_facts()

    assert facts["workflow_session_id"] == "wf-123"
    assert facts["session_mode"] == "smart_rip"
    assert facts["pipeline_step"] == "output_plan"
    assert facts["paths"]["movies_folder"] == r"C:\movies"
    assert facts["disc"]["identity_hash"] == "identity-abc"
    assert "titles" not in facts["disc"]
    assert facts["session"]["title"] == "Finding Nemo"
    assert facts["session"]["content_mapping"]["extra_title_count"] == 4
    assert facts["session"]["output_plan"]["main_label"] == "Finding Nemo (2003).mkv"
    assert facts["scan_issue_summary"]["affected_paths"] == ["AACS/MKB_RO.inf"]


def test_dump_session_not_offered_for_movie_resume(tmp_path):
    """Dump-all sessions must not be offered as resumable for movie/TV runs.

    Regression guard for the bug where dump-all wrote
    `media_type=None`, which `resume_session_matches` treats as a wildcard
    and would incorrectly offer the dump folder when starting a movie or
    TV workflow. With `media_type="dump"` persisted by `write_temp_metadata`,
    a movie resume offer must skip the dump folder.
    """
    from controller.session_recovery import select_resumable_session

    engine = RipperEngine(_engine_cfg())

    dump_dir = tmp_path / "dump-session"
    dump_dir.mkdir()
    engine.write_temp_metadata(
        str(dump_dir), "Dump 01", 1, media_type="dump"
    )

    movie_dir = tmp_path / "movie-session"
    movie_dir.mkdir()
    engine.write_temp_metadata(
        str(movie_dir), "Some Movie", 1, media_type="movie"
    )

    resumable = engine.find_resumable_sessions(str(tmp_path))
    assert {name for _full, name, _meta, _count in resumable} == {
        "dump-session",
        "movie-session",
    }

    offered_prompts: list[str] = []

    def ask_yesno(prompt: str) -> bool:
        offered_prompts.append(prompt)
        return True

    result = select_resumable_session(
        resumable,
        media_type="movie",
        ask_yesno=ask_yesno,
        log_fn=lambda _msg: None,
    )

    assert result is not None
    assert result["name"] == "movie-session"
    assert result["meta"].get("media_type") == "movie"
    assert len(offered_prompts) == 1
    assert "Dump 01" not in offered_prompts[0]


def test_run_tv_disc_persists_year_into_temp_metadata(tmp_path, monkeypatch):
    """TV setup year must be persisted to _rip_meta.json so the second resume
    cycle can repopulate the dialog. Regression for the
    `year=year if not is_tv else None` strip-on-TV bug."""
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    tv_root = tmp_path / "tv"
    temp_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["tv_folder"] = str(tv_root)

    disc_titles = [
        {
            "id": 0,
            "name": "Episode Block",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
            "chapters": 20,
            "audio_tracks": [1],
            "subtitle_tracks": [1],
        },
    ]
    writes = []

    controller.gui.show_info = lambda *_args, **_kwargs: None
    controller.gui.ask_yesno = lambda _prompt: False
    controller.gui.ask_input = lambda *_args, **_kwargs: pytest.fail(
        "raw TV prompts should be skipped when ask_tv_setup is available"
    )
    controller.gui.ask_tv_setup = lambda **_kwargs: TVSessionSetup(
        title="Example Show",
        year="2004",
        season=2,
        starting_disc=1,
        episode_mapping="auto",
        metadata_provider="TMDB",
        metadata_id="tmdb:12345",
        multi_episode="auto",
        specials="ask",
        replace_existing=False,
        keep_raw=False,
    )

    monkeypatch.setattr(controller, "check_resume", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(
        engine,
        "write_temp_metadata",
        lambda path, title, disc_number, **kwargs: (
            writes.append((path, title, disc_number, kwargs)),
            engine.abort(),
        )[-1],
    )
    monkeypatch.setattr(engine, "update_temp_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)

    controller.run_tv_disc()

    assert writes, "write_temp_metadata should have been called for TV setup"
    captured_kwargs = writes[0][3]
    assert "year" in captured_kwargs, (
        "year must be passed to write_temp_metadata so it persists across resume"
    )
    assert captured_kwargs["year"] == "2004", (
        f"year should persist for TV sessions, got {captured_kwargs['year']!r}"
    )
    assert captured_kwargs["media_type"] == "tv"


def test_run_movie_disc_invokes_preserve_partial_session_on_partial_failure(
    tmp_path,
    monkeypatch,
):
    """Behavior guard: partial rip path must dispatch _preserve_partial_session.

    Reproduces the runtime gap fix — controller.run_movie_disc() must call the
    legacy_compat partial-session methods so that selected/completed/failed
    title metadata + dest_folder are preserved when some titles succeed and
    others fail.
    """
    controller, engine = _controller_with_engine()

    temp_root = tmp_path / "temp"
    movies_root = tmp_path / "movies"
    temp_root.mkdir(parents=True, exist_ok=True)
    movies_root.mkdir(parents=True, exist_ok=True)

    engine.cfg["opt_show_temp_manager"] = False
    engine.cfg["opt_scan_disc_size"] = False
    engine.cfg["opt_confirm_before_rip"] = False
    engine.cfg["temp_folder"] = str(temp_root)
    engine.cfg["movies_folder"] = str(movies_root)

    controller.gui.ask_yesno = lambda _prompt: False
    controller.gui.show_info = lambda *_a, **_kw: None
    controller.gui.show_error = lambda *_a, **_kw: None
    controller.gui.ask_movie_setup = lambda **_kw: MovieSessionSetup(
        title="Guarded Movie",
        year="2025",
        edition="",
        metadata_provider="TMDB",
        metadata_id="",
        replace_existing=False,
        keep_raw=False,
        extras_mode="ask",
    )

    disc_titles = [
        {
            "id": 0,
            "name": "Main Feature",
            "duration": "120:00",
            "size": "4.0 GB",
            "duration_seconds": 7200,
            "size_bytes": 4_000_000_000,
        },
        {
            "id": 1,
            "name": "Bonus",
            "duration": "10:00",
            "size": "0.6 GB",
            "duration_seconds": 600,
            "size_bytes": 600_000_000,
        },
    ]

    monkeypatch.setattr(controller, "check_resume", lambda *_a, **_kw: None)
    monkeypatch.setattr(engine, "cleanup_partial_files", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "scan_with_retry", lambda: disc_titles)
    monkeypatch.setattr(controller, "_log_drive_compatibility", lambda: True)
    monkeypatch.setattr(controller, "write_session_summary", lambda: None)
    monkeypatch.setattr(controller, "flush_log", lambda: None)
    monkeypatch.setattr(
        controller,
        "_open_manual_disc_picker",
        lambda _titles, _is_tv: ([0, 1], 4_600_000_000),
    )
    monkeypatch.setattr(engine, "_quick_ffprobe_ok", lambda *_a, **_k: True)

    rip_capture: dict[str, str] = {}

    def fake_run_job(job):
        rip_dir = Path(job.output)
        rip_dir.mkdir(parents=True, exist_ok=True)
        main_path = rip_dir / "title_t00_main.mkv"
        main_path.write_text("main", encoding="utf-8")
        rip_capture["path"] = str(rip_dir)
        rip_capture["main"] = str(main_path)
        engine.last_title_file_map = {0: [str(main_path)]}
        return SimpleNamespace(success=False, errors=[1])

    monkeypatch.setattr(engine, "run_job", fake_run_job)
    monkeypatch.setattr(
        controller,
        "_normalize_rip_result",
        lambda *_a, **_k: (False, [rip_capture["main"]]),
    )
    monkeypatch.setattr(
        controller,
        "_stabilize_ripped_files",
        lambda *_a, **_k: (True, False),
    )
    monkeypatch.setattr(
        controller,
        "_verify_expected_sizes",
        lambda *_a, **_k: ("pass", "ok"),
    )
    monkeypatch.setattr(
        controller,
        "_verify_container_integrity",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(
        engine,
        "analyze_files",
        lambda *_a, **_k: [(rip_capture["main"], 7200.0, 4000.0)],
    )
    monkeypatch.setattr(
        controller,
        "_cleanup_success_session_metadata",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        controller_module.shutil,
        "rmtree",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        controller,
        "_select_and_move",
        lambda *_a, **_kw: True,
    )

    # Spy on _preserve_partial_session to guard dispatch + kwargs.
    preserve_calls: list[dict] = []
    metadata_writes: list[dict] = []
    real_preserve = controller._preserve_partial_session

    def spy_preserve(rip_path, **kwargs):
        preserve_calls.append({"rip_path": rip_path, **kwargs})
        return real_preserve(rip_path, **kwargs)

    monkeypatch.setattr(controller, "_preserve_partial_session", spy_preserve)

    real_update = engine.update_temp_metadata

    def spy_update(rip_path, **kwargs):
        metadata_writes.append({"rip_path": rip_path, **kwargs})
        return real_update(rip_path, **kwargs)

    monkeypatch.setattr(engine, "update_temp_metadata", spy_update)

    controller.run_movie_disc()

    assert preserve_calls, (
        "_preserve_partial_session must be invoked on the partial-success path"
    )
    call = preserve_calls[-1]
    assert call["rip_path"] == rip_capture["path"]
    assert call["title"] == "Guarded Movie"
    assert call["year"] == "2025"
    assert call["media_type"] == "movie"
    assert call["selected_titles"] == [0, 1]
    assert call["completed_titles"] == [0]
    assert call["failed_titles"] == [1]
    assert "dest_folder" in call

    # The preserve call must hit update_temp_metadata with phase="partial"
    # and the four required fields (selected/completed/failed/dest_folder).
    partial_writes = [
        write for write in metadata_writes if write.get("phase") == "partial"
    ]
    assert partial_writes, (
        "expected at least one update_temp_metadata write with phase='partial'"
    )
    final_write = partial_writes[-1]
    assert final_write["status"] == "partial"
    assert final_write["selected_titles"] == [0, 1]
    assert final_write["completed_titles"] == [0]
    assert final_write["failed_titles"] == [1]
    assert "dest_folder" in final_write
