"""Tests for controller.naming module."""

import pytest
from controller.naming import (
    build_movie_folder_name,
    build_movie_main_filename,
    build_tv_folder_name,
    build_fallback_title,
    build_naming_preview_text,
    normalize_metadata_id,
    normalize_naming_mode,
    parse_metadata_id,
)


# ── parse_metadata_id ────────────────────────────────────────────────

class TestParseMetadataId:
    """Verify flexible user input → Jellyfin tag conversion."""

    @pytest.mark.parametrize("raw, expected", [
        # Jellyfin format passthrough
        ("tmdbid-12345", "[tmdbid-12345]"),
        ("opendbid-12345", "[opendbid-12345]"),
        ("imdbid-tt1234567", "[imdbid-tt1234567]"),
        ("tvdbid-79168", "[tvdbid-79168]"),
        # Shorthand with colon
        ("tmdb:12345", "[tmdbid-12345]"),
        ("opendb:12345", "[opendbid-12345]"),
        ("imdb:tt1234567", "[imdbid-tt1234567]"),
        ("tvdb:79168", "[tvdbid-79168]"),
        # Shorthand with dash
        ("tmdb-12345", "[tmdbid-12345]"),
        ("opendb-12345", "[opendbid-12345]"),
        ("imdb-tt1234567", "[imdbid-tt1234567]"),
        ("tvdb-79168", "[tvdbid-79168]"),
        # Bare IMDb ID
        ("tt1234567", "[imdbid-tt1234567]"),
        ("tt0133093", "[imdbid-tt0133093]"),
        # Bare integer → assume TMDB
        ("12345", "[tmdbid-12345]"),
        ("603", "[tmdbid-603]"),
        # Case insensitive
        ("TMDB:999", "[tmdbid-999]"),
        ("IMDBID-tt0000001", "[imdbid-tt0000001]"),
        # Already bracketed
        ("[tmdbid-12345]", "[tmdbid-12345]"),
        # Empty / invalid
        ("", ""),
        (None, ""),
        ("  ", ""),
        ("foobar", ""),
        ("abc:xyz", ""),
    ])
    def test_parse_metadata_id(self, raw, expected):
        assert parse_metadata_id(raw) == expected

    def test_parse_metadata_id_uses_selected_provider_for_bare_integer(self):
        assert parse_metadata_id("12345", provider="opendb") == "[opendbid-12345]"


class TestNormalizeMetadataId:

    @pytest.mark.parametrize(("raw", "provider", "expected"), [
        ("12345", None, "tmdb:12345"),
        ("12345", "opendb", "opendb:12345"),
        ("TMDB:603", "opendb", "tmdb:603"),
        ("opendbid-88", None, "opendb:88"),
        ("tt0133093", "tmdb", "imdb:tt0133093"),
        ("", "tmdb", ""),
    ])
    def test_normalize_metadata_id(self, raw, provider, expected):
        assert normalize_metadata_id(raw, provider) == expected


# ── build_movie_folder_name ──────────────────────────────────────────

class TestBuildMovieFolderName:

    def test_basic(self):
        assert build_movie_folder_name("The Matrix", "1999") == "The Matrix (1999)"

    def test_with_tmdb(self):
        assert build_movie_folder_name("The Matrix", "1999", "tmdb:603") == \
            "The Matrix (1999) [tmdbid-603]"

    def test_with_imdb(self):
        assert build_movie_folder_name("Inception", "2010", "tt1375666") == \
            "Inception (2010) [imdbid-tt1375666]"

    def test_empty_metadata(self):
        assert build_movie_folder_name("Test", "2024", "") == "Test (2024)"

    def test_invalid_metadata_ignored(self):
        assert build_movie_folder_name("Test", "2024", "garbage") == "Test (2024)"

    def test_edition_is_cleaned_for_folder_name(self):
        assert build_movie_folder_name(
            "Chosen Movie",
            "2024",
            "",
            'Director: Cut / IMAX?',
        ) == "Chosen Movie (2024) - Director Cut  IMAX"


def test_build_movie_main_filename_cleans_edition():
    assert build_movie_main_filename(
        "Chosen Movie",
        "2024",
        'Director: Cut / IMAX?',
    ) == "Chosen Movie (2024) - Director Cut  IMAX.mkv"


# ── build_tv_folder_name ─────────────────────────────────────────────

class TestBuildTvFolderName:

    def test_basic(self):
        assert build_tv_folder_name("Breaking Bad") == "Breaking Bad"

    def test_with_tvdb(self):
        assert build_tv_folder_name("Breaking Bad", "tvdb:81189") == \
            "Breaking Bad [tvdbid-81189]"

    def test_with_tmdb(self):
        assert build_tv_folder_name("Breaking Bad", "tmdb:1396") == \
            "Breaking Bad [tmdbid-1396]"

    def test_empty_metadata(self):
        assert build_tv_folder_name("Test", "") == "Test"


# ── build_fallback_title with disc_name ──────────────────────────────

class TestBuildFallbackTitleDiscName:
    """Verify CINFO disc name is preferred over TINFO when available."""

    @staticmethod
    def _clean(s):
        return s.replace(" ", "_")

    @staticmethod
    def _temp():
        return "TEMP_TITLE"

    @staticmethod
    def _choose(titles, require_valid=False):
        if titles:
            return titles[0], 1.0
        return None, 0

    def test_disc_name_preferred_over_tinfo(self):
        cfg = {"opt_naming_mode": "disc-title"}
        titles = [{"name": "Title_00", "id": 0}]
        result = build_fallback_title(
            cfg, self._temp, self._clean, self._choose,
            disc_titles=titles, disc_name="MY_MOVIE_DISC"
        )
        assert result == "MY_MOVIE_DISC"

    def test_generic_disc_name_falls_through_to_tinfo(self):
        cfg = {"opt_naming_mode": "disc-title"}
        titles = [{"name": "Real Movie Name", "id": 0}]
        result = build_fallback_title(
            cfg, self._temp, self._clean, self._choose,
            disc_titles=titles, disc_name="Title 01"
        )
        # "Title 01" starts with "title " so it's considered generic
        assert "Real" in result or "Movie" in result

    def test_no_disc_name_uses_tinfo(self):
        cfg = {"opt_naming_mode": "disc-title"}
        titles = [{"name": "Good Title", "id": 0}]
        result = build_fallback_title(
            cfg, self._temp, self._clean, self._choose,
            disc_titles=titles, disc_name=None
        )
        assert result == "Good_Title"

    def test_timestamp_mode_ignores_disc_name(self):
        cfg = {"opt_naming_mode": "timestamp"}
        result = build_fallback_title(
            cfg, self._temp, self._clean, self._choose,
            disc_name="MY_DISC"
        )
        assert result == "TEMP_TITLE"


def test_build_naming_preview_disc_title_not_duplicated():
    preview = build_naming_preview_text("disc-title", "MyMovie", "2026-04-04_12-00-00")
    assert preview == "Example: MyMovie"


def test_build_naming_preview_disc_title_timestamp_and_timestamp_modes():
    with_suffix = build_naming_preview_text(
        "disc-title+timestamp", "MyMovie", "2026-04-04_12-00-00"
    )
    timestamp_only = build_naming_preview_text(
        "timestamp", "MyMovie", "2026-04-04_12-00-00"
    )
    assert with_suffix == "Example: MyMovie [MyMovie_2026-04-04_12-00-00]"
    assert timestamp_only == "Example: MyMovie [2026-04-04_12-00-00]"
