"""Parsing regression tests focused on malformed real-world inputs."""

import os
import sys

import pytest

# Import from project root when tests are run from this folder.
sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

from utils.media import select_largest_file  # pyright: ignore[reportMissingImports]
from utils.parsing import (  # pyright: ignore[reportMissingImports]
    parse_cli_args,
    parse_duration_to_seconds,
    parse_ordered_titles,
    parse_size_to_bytes,
    safe_int,
)


class TestSafeIntMalformedInputs:
    """Safe integer parsing under MakeMKV-style malformed values."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("3.7 GB", 3),
            ("  12  ", 12),
            ("-4", -4),
            ("chapter=08", 8),
            ("1/12", 0),
            ("abc", 0),
            ("", 0),
            (None, 0),
        ],
    )
    def test_safe_int_handles_malformed_values(self, raw, expected):
        assert safe_int(raw) == expected


class TestDurationParsing:
    """Duration parsing behavior for valid and broken metadata."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("01:23:45", 1 * 3600 + 23 * 60 + 45),
            ("1:02", 62),
            ("00:45:12.500", 2712),
            ("1:23:45.678", 5025),
            ("", 0),
            ("N/A", 0),
            ("12", 0),
            ("1:2:3:4", 0),
            ("bad:10", 0),
            (None, 0),
        ],
    )
    def test_duration_parsing_edge_cases(self, raw, expected):
        assert parse_duration_to_seconds(raw) == expected


class TestSizeParsing:
    """Size parsing behavior for locale and noisy strings."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("3.7 GB", int(3.7 * 1000**3)),
            ("3,7 GB", int(3.7 * 1000**3)),
            ("3.7 GiB", int(3.7 * 1024**3)),
            ("Size: 5,000 MB", 5_000 * 1000**2),
            ("1024", 1024),
            ("", 0),
            ("unknown", 0),
            (None, 0),
        ],
    )
    def test_size_parsing_variants(self, raw, expected):
        assert parse_size_to_bytes(raw) == expected

    def test_size_parsing_ambiguous_single_comma_treated_as_decimal(self):
        assert parse_size_to_bytes("1,23 GB") == int(1.23 * 1000**3)


class TestMediaSelection:
    def test_select_largest_file_skips_deleted_entries(self, tmp_path):
        small = tmp_path / "small.mkv"
        gone = tmp_path / "gone.mkv"
        big = tmp_path / "big.mkv"
        small.write_bytes(b"1" * 10)
        gone.write_bytes(b"1" * 100)
        big.write_bytes(b"1" * 1000)

        gone.unlink()

        selected = select_largest_file([str(small), str(gone), str(big)])
        assert selected == str(big)


class TestOrderedTitleParsing:
    """Ordered title parsing for multi-disc naming flows."""

    def test_comma_and_whitespace_titles(self):
        assert parse_ordered_titles("toony,  herb , jeckel") == [
            "toony", "herb", "jeckel"
        ]

    def test_spaced_hyphen_delimiter(self):
        assert parse_ordered_titles("toony - herb - jeckel") == [
            "toony", "herb", "jeckel"
        ]

    def test_extra_spaces_around_hyphen_delimiter(self):
        # Double spaces around the dash must still split correctly.
        assert parse_ordered_titles("pitch perfect 2  -  pitch perfect") == [
            "pitch perfect 2", "pitch perfect"
        ]

    def test_internal_whitespace_is_collapsed(self):
        # Multiple internal spaces are collapsed to one in each part.
        assert parse_ordered_titles("pitch  perfect  2 - pitch  perfect") == [
            "pitch perfect 2", "pitch perfect"
        ]

    def test_tab_around_hyphen_delimiter(self):
        assert parse_ordered_titles("Title A\t-\tTitle B") == [
            "Title A", "Title B"
        ]

    def test_quoted_csv_like_titles(self):
        assert parse_ordered_titles('"Episode One", "Episode Two"') == [
            "Episode One", "Episode Two"
        ]

    def test_preserves_embedded_hyphen_without_spacing(self):
        assert parse_ordered_titles("Spider-Man") == ["Spider-Man"]

    @pytest.mark.parametrize("raw", ["", "   ", None])
    def test_empty_inputs(self, raw):
        assert parse_ordered_titles(raw) == []


class TestCliArgsParsing:
    """CLI arg parser behavior including unsupported profile tokens."""

    def test_strips_profile_selection_tokens_and_logs_warning(self):
        logs = []
        tokens = parse_cli_args(
            '--cache=1024 +sel:all,-sel:eng --directio=true',
            on_log=logs.append,
            label="rip args",
        )
        assert "+sel:all,-sel:eng" not in tokens
        assert "--cache=1024" in tokens
        assert "--directio=true" in tokens
        assert any("removed unsupported MakeMKV profile token" in msg for msg in logs)

    def test_fallback_split_when_shell_parse_fails(self, monkeypatch):
        logs = []

        def raise_value_error(_s, posix=True):
            raise ValueError("bad quoting")

        monkeypatch.setattr("utils.parsing.shlex.split", raise_value_error)
        tokens = parse_cli_args('--cache=1024 --foo="bar"', on_log=logs.append)

        assert len(tokens) >= 1
        assert any("falling back to simple split" in msg for msg in logs)
