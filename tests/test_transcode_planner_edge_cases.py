"""Edge-case tests for transcode/planner.py:build_transcode_plan.

The existing tests in `tests/test_transcode_queue_builder.py` cover
the happy path (preserved relative paths, basename fallback for
outside-root input) plus dedup of exact-match duplicate inputs.
This file covers the long tail flagged in TASKS.md Active —
Windows drive-letter paths, UNC paths, `..` segments, large input
sets for dedup correctness, mixed case-sensitivity on Windows,
mixed separators, empty inputs, non-MKV extensions.

These tests are behavior-first — they pin the planner's contract
without depending on tkinter or the GUI. Per migration plan
decision #5, behavior-first tests survive the PySide6 migration.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from transcode.planner import build_transcode_plan


# --------------------------------------------------------------------------
# Empty / degenerate input
# --------------------------------------------------------------------------


def test_empty_selected_paths_returns_empty_plan(tmp_path):
    plans = build_transcode_plan(
        str(tmp_path / "source"),
        [],
        str(tmp_path / "output"),
    )
    assert plans == []


# --------------------------------------------------------------------------
# Deduplication — exact, case, and large-scale
# --------------------------------------------------------------------------


def test_dedup_collapses_repeated_input_to_single_plan(tmp_path):
    """Same path repeated 3x → single plan entry. Existing test covers
    one duplicate; this pins the behavior at higher repeat counts."""
    source_root = tmp_path / "source"
    f = source_root / "movie.mkv"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("x", encoding="utf-8")

    plans = build_transcode_plan(
        str(source_root),
        [str(f), str(f), str(f), str(f), str(f)],
        str(tmp_path / "out"),
    )

    assert len(plans) == 1
    assert plans[0]["relative_path"] == "movie.mkv"


@pytest.mark.skipif(sys.platform != "win32", reason="Case-insensitive dedup is Windows-only")
def test_dedup_case_insensitive_on_windows(tmp_path):
    """`os.path.normcase` lowercases on Windows, so two paths that
    differ only in case must collapse to a single plan. This is the
    case-collision bug the planner's `dedupe_key = os.path.normcase(...)`
    line was added to prevent."""
    source_root = tmp_path / "source"
    f = source_root / "Movie.mkv"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("x", encoding="utf-8")

    upper = str(f).replace("Movie.mkv", "MOVIE.MKV")
    mixed = str(f).replace("Movie.mkv", "mOvIe.MkV")

    plans = build_transcode_plan(
        str(source_root),
        [str(f), upper, mixed],
        str(tmp_path / "out"),
    )

    assert len(plans) == 1, (
        f"Case-only duplicates should dedup on Windows. Got "
        f"{len(plans)} plans: {[p['input_path'] for p in plans]}"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="Case-sensitive dedup is non-Windows")
def test_dedup_case_sensitive_on_non_windows(tmp_path):
    """Non-Windows filesystems are case-sensitive; the planner's
    `os.path.normcase` is a no-op there. Different-case paths
    are different files and must NOT dedupe."""
    source_root = tmp_path / "source"
    source_root.mkdir(parents=True, exist_ok=True)
    f1 = source_root / "Movie.mkv"
    f2 = source_root / "movie.mkv"
    f1.write_text("x", encoding="utf-8")
    f2.write_text("y", encoding="utf-8")

    plans = build_transcode_plan(
        str(source_root),
        [str(f1), str(f2)],
        str(tmp_path / "out"),
    )

    assert len(plans) == 2


def test_dedup_with_large_input_set_handles_100_plus_files(tmp_path):
    """Performance + correctness sanity: 150 inputs with various
    duplicates should dedup without quadratic blowup. The planner's
    dedup uses a `set`, so this is O(n) — but pin the contract so
    a future refactor doesn't regress to a list."""
    source_root = tmp_path / "source"
    source_root.mkdir(parents=True, exist_ok=True)

    # 100 unique files
    unique_files = []
    for i in range(100):
        f = source_root / f"file_{i:03d}.mkv"
        f.write_text("x", encoding="utf-8")
        unique_files.append(str(f))

    # 50 duplicates picked from the 100, deterministically
    duplicates = unique_files[::2]  # every other file
    selected = unique_files + duplicates  # 150 total, 50 dupes
    assert len(selected) == 150

    plans = build_transcode_plan(
        str(source_root),
        selected,
        str(tmp_path / "out"),
    )

    assert len(plans) == 100  # dedup must collapse to the unique 100
    # Order preserved: the first occurrence wins, so plans should
    # reflect the input order of unique items.
    assert [p["input_path"] for p in plans] == [
        os.path.normpath(p) for p in unique_files
    ]


# --------------------------------------------------------------------------
# `..` segments / paths outside scan root
# --------------------------------------------------------------------------


def test_dotdot_segments_in_relative_path_trigger_basename_fallback(tmp_path):
    """The planner has an explicit `if relative_path.startswith(".."):`
    fallback. Pin this — input is a SIBLING of source_root, so the
    natural relative path would start with ".." (e.g., "../sibling/file.mkv").
    The planner should fall back to basename rather than emit a
    relative path that escapes the output tree."""
    source_root = tmp_path / "source"
    source_root.mkdir(parents=True, exist_ok=True)
    sibling = tmp_path / "sibling" / "movie.mkv"
    sibling.parent.mkdir(parents=True, exist_ok=True)
    sibling.write_text("x", encoding="utf-8")

    plans = build_transcode_plan(
        str(source_root),
        [str(sibling)],
        str(tmp_path / "out"),
    )

    assert len(plans) == 1
    plan = plans[0]
    # Relative path must NOT start with ".." — that's the bug being prevented
    assert not plan["relative_path"].startswith("..")
    # Falls back to basename
    assert plan["relative_path"] == "movie.mkv"
    assert plan["output_relative_path"] == "movie.mkv"


def test_deeply_nested_dotdot_input_falls_back_to_basename(tmp_path):
    """Even an input that's many levels above source_root should
    fall back to basename, not produce a chain of `..` segments
    that escape the output tree."""
    source_root = tmp_path / "source" / "level1" / "level2" / "level3"
    source_root.mkdir(parents=True, exist_ok=True)
    far_away = tmp_path / "elsewhere" / "movie.mkv"
    far_away.parent.mkdir(parents=True, exist_ok=True)
    far_away.write_text("x", encoding="utf-8")

    plans = build_transcode_plan(
        str(source_root),
        [str(far_away)],
        str(tmp_path / "out"),
    )

    assert len(plans) == 1
    assert plans[0]["relative_path"] == "movie.mkv"


# --------------------------------------------------------------------------
# Cross-drive input on Windows (simulated via monkeypatch)
# --------------------------------------------------------------------------


def test_cross_drive_input_falls_back_to_basename(tmp_path, monkeypatch):
    """`os.path.relpath` raises ValueError when the input and root
    are on different drives (Windows-only condition, but the
    planner's try/except catches it on any platform via the same
    code path). Simulate by making `os.path.relpath` raise — then
    the planner should fall back to basename."""
    source_root = tmp_path / "source"
    source_root.mkdir(parents=True, exist_ok=True)
    f = source_root / "movie.mkv"
    f.write_text("x", encoding="utf-8")

    # Make os.path.relpath raise ValueError to simulate the cross-drive case
    real_relpath = os.path.relpath
    def fake_relpath(path, start):
        # Raise for any call to relpath — simulates the cross-drive failure
        raise ValueError("path is on mount '<other>', start on mount '<this>'")
    monkeypatch.setattr("transcode.planner.os.path.relpath", fake_relpath)

    plans = build_transcode_plan(
        str(source_root),
        [str(f)],
        str(tmp_path / "out"),
    )

    assert len(plans) == 1
    # Falls back to basename when relpath fails
    assert plans[0]["relative_path"] == "movie.mkv"


# --------------------------------------------------------------------------
# UNC paths (Windows synthetic — `os.path.normpath` accepts the strings
# regardless of whether the share actually exists)
# --------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="UNC path semantics are Windows-specific")
def test_unc_paths_inside_same_share_get_relative_paths():
    """When both the source root and the input are on the same UNC
    share, the relative path should be computed normally — the
    planner shouldn't trip on the UNC prefix."""
    source_root = r"\\server\share\source"
    input_path = r"\\server\share\source\movies\file.mkv"
    output_root = r"\\server\share\output"

    plans = build_transcode_plan(source_root, [input_path], output_root)

    assert len(plans) == 1
    plan = plans[0]
    # Relative path should be computed correctly across UNC paths
    assert plan["relative_path"] == os.path.normpath(r"movies\file.mkv")
    assert plan["output_relative_path"] == os.path.normpath(r"movies\file.mkv")


@pytest.mark.skipif(sys.platform != "win32", reason="UNC path semantics are Windows-specific")
def test_unc_input_with_local_root_falls_back_to_basename():
    """UNC input + local-drive root is the cross-drive scenario in
    UNC form. Should fall back to basename via the relpath /
    .. fallback path, not produce a strange relative path."""
    source_root = r"C:\Users\me\Movies"
    input_path = r"\\server\share\file.mkv"
    output_root = r"C:\Users\me\Output"

    plans = build_transcode_plan(source_root, [input_path], output_root)

    assert len(plans) == 1
    # On Windows, relpath between UNC and local drive raises ValueError;
    # the planner catches it and falls back to basename. Even if relpath
    # somehow succeeds, the result starts with `..` which also triggers
    # the basename fallback.
    assert plans[0]["relative_path"] == "file.mkv"


# --------------------------------------------------------------------------
# Mixed separators / extensions
# --------------------------------------------------------------------------


def test_mixed_path_separators_get_normalized(tmp_path):
    """If the input path uses a mix of `/` and `\\`, the planner's
    `os.path.normpath` should normalize them. The output paths
    should use the platform's native separator throughout."""
    source_root = tmp_path / "source"
    nested = source_root / "Season 01" / "ep1.mkv"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text("x", encoding="utf-8")

    # Build a path string with mixed separators by hand
    mixed_input = f"{source_root}/Season 01\\ep1.mkv"

    plans = build_transcode_plan(
        str(source_root),
        [mixed_input],
        str(tmp_path / "out"),
    )

    assert len(plans) == 1
    # The relative_path should be a normalized form (no mixed separators)
    relative = plans[0]["relative_path"]
    # On Windows, normalized form uses `\`; on POSIX, `/`. Either way,
    # it should match what os.path.normpath produces.
    assert relative == os.path.normpath("Season 01/ep1.mkv")


def test_non_mkv_extension_replaced_with_mkv(tmp_path):
    """Output_relative_path always ends in .mkv regardless of the
    input extension — the planner enforces this so transcode output
    is uniform."""
    source_root = tmp_path / "source"
    source_root.mkdir(parents=True, exist_ok=True)
    mp4_input = source_root / "video.mp4"
    mp4_input.write_text("x", encoding="utf-8")
    avi_input = source_root / "video.avi"
    avi_input.write_text("x", encoding="utf-8")

    plans = build_transcode_plan(
        str(source_root),
        [str(mp4_input), str(avi_input)],
        str(tmp_path / "out"),
    )

    assert len(plans) == 2
    assert all(plan["output_relative_path"].endswith(".mkv") for plan in plans)
    assert plans[0]["output_relative_path"] == os.path.normpath("video.mkv")
    assert plans[1]["output_relative_path"] == os.path.normpath("video.mkv")


def test_no_extension_input_still_gets_mkv_extension(tmp_path):
    """Edge case: input file has no extension at all. Output should
    still get `.mkv`."""
    source_root = tmp_path / "source"
    source_root.mkdir(parents=True, exist_ok=True)
    no_ext = source_root / "extensionless_file"
    no_ext.write_text("x", encoding="utf-8")

    plans = build_transcode_plan(
        str(source_root),
        [str(no_ext)],
        str(tmp_path / "out"),
    )

    assert len(plans) == 1
    assert plans[0]["output_relative_path"].endswith(".mkv")


# --------------------------------------------------------------------------
# Output paths are always under output_root
# --------------------------------------------------------------------------


def test_all_output_paths_are_under_output_root(tmp_path):
    """A core safety property: regardless of input path shape
    (deep nesting, outside-root, dotdot, etc.), every plan's
    `output_path` must be under the output_root. This prevents
    transcode output from accidentally writing to arbitrary
    filesystem locations."""
    source_root = tmp_path / "source"
    output_root = tmp_path / "output"
    source_root.mkdir(parents=True, exist_ok=True)

    # Mix of: nested-inside, outside-root (dotdot fallback), repeated
    inside = source_root / "deeply" / "nested" / "movie.mkv"
    inside.parent.mkdir(parents=True, exist_ok=True)
    inside.write_text("x", encoding="utf-8")

    sibling = tmp_path / "sibling" / "outside.mkv"
    sibling.parent.mkdir(parents=True, exist_ok=True)
    sibling.write_text("x", encoding="utf-8")

    plans = build_transcode_plan(
        str(source_root),
        [str(inside), str(sibling), str(inside)],  # note duplicate
        str(output_root),
    )

    assert len(plans) == 2
    output_root_normalized = os.path.normpath(str(output_root))
    for plan in plans:
        # Output path must start with the output root prefix
        assert plan["output_path"].startswith(output_root_normalized + os.sep), (
            f"output_path {plan['output_path']!r} escapes output_root "
            f"{output_root_normalized!r}"
        )


# --------------------------------------------------------------------------
# Order preservation
# --------------------------------------------------------------------------


def test_plan_order_matches_first_occurrence_in_input_list(tmp_path):
    """Dedup keeps the first occurrence and discards subsequent
    duplicates — pin the order so a future refactor that uses an
    unordered set without preserving insertion order is caught."""
    source_root = tmp_path / "source"
    source_root.mkdir(parents=True, exist_ok=True)
    a = source_root / "a.mkv"
    b = source_root / "b.mkv"
    c = source_root / "c.mkv"
    for f in (a, b, c):
        f.write_text("x", encoding="utf-8")

    # Order: c, a, b, a (duplicate), c (duplicate)
    plans = build_transcode_plan(
        str(source_root),
        [str(c), str(a), str(b), str(a), str(c)],
        str(tmp_path / "out"),
    )

    # Should be 3 plans in order: c, a, b
    assert [p["relative_path"] for p in plans] == ["c.mkv", "a.mkv", "b.mkv"]
