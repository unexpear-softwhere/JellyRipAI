from __future__ import annotations

from typing import Any

from controller.session_recovery import (
    build_resume_prompt,
    map_title_ids_to_analyzed_indices,
    mark_session_failed,
    restore_selected_titles,
    select_resumable_session,
)


class _FakeFailureEngine:
    def __init__(self) -> None:
        self.metadata_updates: list[tuple[str, dict[str, Any]]] = []
        self.wipes: list[str] = []

    def update_temp_metadata(
        self,
        rip_path: str,
        status: str | None = None,
        **updates: Any,
    ) -> None:
        payload = dict(updates)
        if status is not None:
            payload["status"] = status
        self.metadata_updates.append((rip_path, payload))

    def wipe_session_outputs(self, rip_path: str, on_log) -> None:
        self.wipes.append(rip_path)
        on_log(f"wiped {rip_path}")


def test_mark_session_failed_updates_metadata_and_wipes_once():
    engine = _FakeFailureEngine()
    messages: list[str] = []
    wiped: set[str] = set()

    mark_session_failed(
        engine,
        "rip-a",
        wiped_session_paths=wiped,
        log_fn=messages.append,
        metadata={"title": "Movie", "media_type": "movie"},
    )
    mark_session_failed(
        engine,
        "rip-a",
        wiped_session_paths=wiped,
        log_fn=messages.append,
        metadata={"title": "Movie", "media_type": "movie"},
    )

    assert engine.metadata_updates == [
        (
            "rip-a",
            {
                "status": "failed",
                "phase": "failed",
                "title": "Movie",
                "media_type": "movie",
            },
        ),
        (
            "rip-a",
            {
                "status": "failed",
                "phase": "failed",
                "title": "Movie",
                "media_type": "movie",
            },
        ),
    ]
    assert engine.wipes == ["rip-a"]
    assert wiped == {"rip-a"}
    assert messages.count("Session failed - wiping outputs.") == 2


def test_select_resumable_session_filters_media_type_and_returns_choice():
    prompts: list[str] = []
    messages: list[str] = []
    sessions = [
        (
            "C:/temp/tv-session",
            "tv-session",
            {"title": "Show", "media_type": "tv", "phase": "ripping"},
            2,
        ),
        (
            "C:/temp/movie-session",
            "movie-session",
            {
                "title": "Movie",
                "media_type": "movie",
                "timestamp": "2026-04-10 12:00:00",
                "phase": "analyzing",
            },
            1,
        ),
    ]

    def ask_yesno(prompt: str) -> bool:
        prompts.append(prompt)
        return True

    result = select_resumable_session(
        sessions,
        media_type="movie",
        ask_yesno=ask_yesno,
        log_fn=messages.append,
    )

    assert result == {
        "path": "C:/temp/movie-session",
        "name": "movie-session",
        "meta": sessions[1][2],
    }
    assert len(prompts) == 1
    assert "Title: Movie" in prompts[0]
    assert "Phase: analyzing" in prompts[0]
    assert messages == ["Resuming session: movie-session"]


def test_build_resume_prompt_uses_status_when_phase_missing():
    prompt = build_resume_prompt(
        "session-name",
        {"title": "Movie", "status": "failed"},
        3,
    )

    assert "Title: Movie" in prompt
    assert "Started: session-name" in prompt
    assert "Phase: failed" in prompt
    assert "Files so far: 3" in prompt


def test_restore_selected_titles_filters_to_current_disc_titles():
    restored = restore_selected_titles(
        [{"id": 2}, {"id": 4}],
        {"selected_titles": ["2", 3, 4]},
    )

    assert restored == [2, 4]


def test_restore_selected_titles_skips_completed_titles():
    restored = restore_selected_titles(
        [{"id": 2}, {"id": 3}, {"id": 4}],
        {
            "selected_titles": ["2", 3, 4],
            "completed_titles": ["3"],
        },
    )

    assert restored == [2, 4]


def test_map_title_ids_to_analyzed_indices_prefers_tracked_map(tmp_path):
    first = tmp_path / "first.mkv"
    second = tmp_path / "second.mkv"
    first.write_text("first")
    second.write_text("second")
    titles_list = [
        (str(first), 0.0, 0.0),
        (str(second), 0.0, 0.0),
    ]

    result = map_title_ids_to_analyzed_indices(
        titles_list,
        [7],
        title_file_map={7: [str(second)]},
        title_id_from_filename=lambda _path: None,
    )

    assert result == [1]
