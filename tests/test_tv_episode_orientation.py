"""v1.0.26 reworks: TV episode-orientation + multi-disc season prompt.

Pins the two behavior changes that stop treating a TV disc as a movie:

* The classifier, given ``is_tv=True``, labels every valid full-length
  title an EPISODE and pre-recommends them all (instead of picking one
  "MAIN" and rejecting the equal-length episodes as duplicates).  Short
  titles still fall out as extras.  The movie path is unchanged.
* The multi-disc continuation can advance to a new season in place, so a
  box set flows Season 1 -> Season 2 without starting a fresh session.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from controller.controller import RipperController
from utils.classifier import classify_titles


def _title(tid: int, duration_s: int, size_gb: float = 0.7, chapters: int = 5) -> dict:
    return {
        "id": tid,
        "name": f"Title {tid + 1}",
        "duration_seconds": duration_s,
        "size_bytes": int(size_gb * 1024 ** 3),
        "chapters": chapters,
        "audio_tracks": [],
        "subtitle_tracks": [],
    }


class TestClassifierIsTv:
    """``is_tv=True``: a disc is N episodes, not one main feature."""

    def test_full_length_titles_are_all_episodes_and_recommended(self):
        # Three ~22 min episodes of near-equal length — the movie
        # classifier would call one MAIN and reject the rest DUPLICATE.
        titles = [_title(0, 1320), _title(1, 1330), _title(2, 1325)]
        result = classify_titles(titles, is_tv=True)
        labels = {ct.title_id: ct.label for ct in result}
        assert labels == {0: "EPISODE", 1: "EPISODE", 2: "EPISODE"}
        # Every episode is pre-checked; none rejected as a duplicate.
        assert all(ct.recommended for ct in result)
        assert not any(ct.label == "DUPLICATE" for ct in result)

    def test_short_titles_are_extras_not_episodes(self):
        titles = [_title(0, 1320), _title(1, 400, size_gb=0.2, chapters=2)]
        result = classify_titles(titles, is_tv=True)
        by_id = {ct.title_id: ct for ct in result}
        assert by_id[0].label == "EPISODE" and by_id[0].recommended
        assert by_id[1].label == "EXTRA" and not by_id[1].recommended

    def test_movie_path_unchanged(self):
        # Default (is_tv omitted) still picks a single MAIN.
        titles = [_title(0, 1320), _title(1, 1330)]
        labels = [ct.label for ct in classify_titles(titles)]
        assert "MAIN" in labels
        assert "EPISODE" not in labels


class TestPromptNextDiscSeason:
    """The multi-disc continuation can advance to a new season in-flow."""

    @staticmethod
    def _fake(*, same_season: bool, typed: str | None = None):
        gui = SimpleNamespace(
            ask_yesno=lambda _prompt: same_season,
            ask_input=lambda *a, **k: typed,
        )
        return SimpleNamespace(gui=gui, log=lambda *a, **k: None)

    def test_same_season_keeps_number(self):
        fake = self._fake(same_season=True)
        assert RipperController._prompt_next_disc_season(fake, 2) == 2

    def test_new_season_updates_number(self):
        fake = self._fake(same_season=False, typed="3")
        assert RipperController._prompt_next_disc_season(fake, 2) == 3

    def test_invalid_entry_keeps_current(self):
        fake = self._fake(same_season=False, typed="oops")
        assert RipperController._prompt_next_disc_season(fake, 2) == 2
