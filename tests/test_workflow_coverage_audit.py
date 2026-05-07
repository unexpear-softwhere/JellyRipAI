"""Behavior-first test coverage audit (per workflow).

Closes the cross-cutting criterion *"Behavior-first test coverage"*
in [docs/workflow-stabilization-criteria.md](../docs/workflow-stabilization-criteria.md):

    At least one ``tests/test_behavior_guards.py`` test (or
    analogous) exists that drives the workflow happy-path and pins
    the state-machine trajectory.  The 825+ test baseline already
    covers most of these; a quick audit pass confirms no workflow
    is unprotected.

This file IS that audit pass.  Each workflow entry point is paired
with the test file that drives its happy path, and a smoke test
asserts the test file exists with at least one matching test
function.  If a future refactor renames a workflow or its test file,
the audit fails loudly so the criterion's invariant — "no workflow
is unprotected" — stays true.

The audit runs in <50ms (pure file-system + grep over the tests
directory).  No GUI, no subprocess, no controller wiring.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))


# Workflow → expected test file(s) and at least one happy-path
# function name pattern.  Update this table as test coverage evolves.
_WORKFLOW_COVERAGE = [
    # (workflow_label, [(filename, function_name_substring), ...])
    (
        "run_smart_rip",
        [
            ("test_pipeline_state_trajectory.py",
             "happy_path_walks_full_state_trajectory"),
            ("test_behavior_guards.py",
             "test_run_smart_rip_wizard_flow_completes_movie"),
        ],
    ),
    (
        "run_movie_disc",
        [
            ("test_behavior_guards.py",
             "test_movie_run_manual_selection_preserves_main_movie_picker"),
        ],
    ),
    (
        "run_tv_disc",
        [
            # MAIN happy-path test name
            ("test_behavior_guards.py",
             "test_run_tv_disc_review_step_uses_output_plan_and_stops_before_rip"),
            # AI BRANCH happy-path test names — branches diverged in
            # 2026-04-30 refactors; the audit accepts either branch's
            # name so the file is portable.
            ("test_behavior_guards.py",
             "test_tv_run_with_no_resume_keeps_clean_defaults"),
            ("test_behavior_guards.py",
             "test_run_tv_disc_tv_setup_applies_starting_disc_and_skips_raw_prompts"),
        ],
    ),
    (
        "run_dump_all",
        [
            ("test_behavior_guards.py",
             "test_run_dump_all_reports_file_and_title_group_counts"),
        ],
    ),
    (
        "run_organize",
        [
            ("test_organize_workflow.py",
             "test_organize_movie_happy_path_creates_folder_and_deletes_temp"),
            ("test_organize_workflow.py",
             "test_organize_tv_happy_path_creates_season_and_extras_folders"),
        ],
    ),
]


def _read_test_file(filename: str) -> str:
    """Read a test file from tests/.  Caches via lru_cache-ish dict."""
    path = os.path.join(_TESTS_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


@pytest.mark.parametrize(
    "workflow_label,coverage_entries",
    _WORKFLOW_COVERAGE,
    ids=[entry[0] for entry in _WORKFLOW_COVERAGE],
)
def test_workflow_has_at_least_one_happy_path_test(
    workflow_label, coverage_entries,
):
    """Every workflow entry point must have at least one happy-path
    test.  If a workflow is renamed, removed, or its happy-path test
    deleted, this audit fails loudly so the criterion invariant
    stays honest."""
    found_any = False
    missing: list[str] = []

    for filename, function_name in coverage_entries:
        path = os.path.join(_TESTS_DIR, filename)
        if not os.path.exists(path):
            missing.append(f"{filename} (file missing)")
            continue

        content = _read_test_file(filename)
        if re.search(rf"\bdef\s+{re.escape(function_name)}\b", content):
            found_any = True
            break
        missing.append(f"{filename}::{function_name} (function missing)")

    assert found_any, (
        f"Workflow '{workflow_label}' has no happy-path test.  "
        f"Audit expected at least one of: {missing}.  Either "
        f"restore the test or update _WORKFLOW_COVERAGE in this "
        f"file to point at the new test."
    )


def test_workflow_coverage_table_covers_all_five_documented_workflows():
    """The audit table must cover every workflow listed in
    ``docs/workflow-stabilization-criteria.md``.  Pins this file
    against accidental drift if a sixth workflow is added without
    updating the audit."""
    documented = {
        "run_smart_rip",
        "run_movie_disc",
        "run_tv_disc",
        "run_dump_all",
        "run_organize",
    }
    audited = {label for label, _ in _WORKFLOW_COVERAGE}

    assert documented == audited, (
        f"Workflow audit drift: documented={documented} "
        f"audited={audited}.  Either add the new workflow's test "
        f"to _WORKFLOW_COVERAGE or remove the obsolete workflow."
    )


def test_workflow_coverage_table_entries_have_at_least_one_test():
    """Defensive: every workflow entry must list at least one
    coverage candidate.  Catches a refactor that empties a list
    rather than removing the row."""
    for label, entries in _WORKFLOW_COVERAGE:
        assert entries, (
            f"Workflow '{label}' has an empty coverage list.  "
            f"Either add candidates or remove the row entirely."
        )


def test_organize_workflow_has_dedicated_test_file():
    """Pre-2026-05-03 the organize workflow had only ONE test in
    test_behavior_guards.py (covering session-metadata cleanup
    only).  This test pins that the dedicated
    ``tests/test_organize_workflow.py`` file landed and contains
    multiple tests — closes the gap from
    ``workflow-stabilization-criteria.md`` §4."""
    path = os.path.join(_TESTS_DIR, "test_organize_workflow.py")
    assert os.path.exists(path), (
        "test_organize_workflow.py is required to close the §4 "
        "test-coverage gap"
    )

    content = _read_test_file("test_organize_workflow.py")
    test_count = len(re.findall(r"^def test_\w+", content, re.MULTILINE))
    assert test_count >= 5, (
        f"test_organize_workflow.py must have at least 5 tests per "
        f"the §4 criterion; got {test_count}"
    )
