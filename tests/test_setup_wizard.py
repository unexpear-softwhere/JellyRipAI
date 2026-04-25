import inspect

import gui.setup_wizard as setup_wizard
from gui.setup_wizard import _build_content_selection
from utils.classifier import ClassifiedTitle


def _title(tid: int, duration_seconds: int, size_gb: float, name: str) -> dict:
    return {
        "id": tid,
        "name": name,
        "duration_seconds": duration_seconds,
        "size_bytes": int(size_gb * 1024 ** 3),
        "chapters": 1,
        "audio_tracks": [],
        "subtitle_tracks": [],
    }


def test_build_content_selection_preserves_classifier_main_when_checked():
    classified = [
        ClassifiedTitle(
            title=_title(0, 5400, 4.5, "Title 1"),
            score=1.0,
            label="MAIN",
            confidence=0.9,
            recommended=True,
        ),
        ClassifiedTitle(
            title=_title(1, 900, 0.8, "Title 2"),
            score=0.4,
            label="EXTRA",
            confidence=0.8,
        ),
    ]

    result = _build_content_selection(classified, {0, 1})

    assert result.main_title_ids == [0]
    assert result.extra_title_ids == [1]
    assert result.skip_title_ids == []


def test_build_content_selection_keeps_checked_non_main_titles_as_extras():
    classified = [
        ClassifiedTitle(
            title=_title(0, 5400, 4.5, "Title 1"),
            score=1.0,
            label="MAIN",
            confidence=0.9,
            recommended=True,
        ),
        ClassifiedTitle(
            title=_title(1, 3600, 3.7, "Title 2"),
            score=0.7,
            label="UNKNOWN",
            confidence=0.85,
        ),
        ClassifiedTitle(
            title=_title(2, 600, 0.3, "Title 3"),
            score=0.2,
            label="EXTRA",
            confidence=0.95,
        ),
    ]

    result = _build_content_selection(classified, checked_title_ids={1, 2})

    assert result.main_title_ids == []
    assert result.extra_title_ids == [1, 2]
    assert result.skip_title_ids == [0]


def test_show_output_plan_keeps_main_display_customization_params():
    params = inspect.signature(setup_wizard.show_output_plan).parameters

    assert "detail_lines" in params
    assert "header_text" in params
    assert "subtitle_text" in params
    assert "confirm_text" in params
    assert params["suggested_base_folder"].kind is inspect.Parameter.KEYWORD_ONLY


def test_setup_wizard_uses_runtime_display_name_in_user_facing_copy():
    scan_results_source = inspect.getsource(setup_wizard.show_scan_results)
    output_plan_source = inspect.getsource(setup_wizard.show_output_plan)

    assert "APP_DISPLAY_NAME" in scan_results_source
    assert "JellyRip has scanned and classified the disc titles." not in scan_results_source

    assert "APP_DISPLAY_NAME" in output_plan_source
    assert "This is exactly what JellyRip will create." not in output_plan_source
