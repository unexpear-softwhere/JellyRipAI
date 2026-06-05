"""The scan-issue summary must not surface MakeMKV's unfilled template
placeholders (e.g. "%2") as the "affected location".

A tester's AI session review read "most frequent location being '%2'
(5x)" because MakeMKV emitted ``occurred while reading '%2'`` and the
parser counted the literal token.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.makemkv_log import analyze_makemkv_messages


def test_template_placeholder_not_reported_as_location():
    summary = analyze_makemkv_messages([
        "Error 'Scsi error - HARDWARE ERROR:TIMEOUT ON LOGICAL UNIT' "
        "occurred while reading '%2' at offset '6735872'",
    ])
    # The error itself is still counted.
    assert summary.scsi_error_count == 1
    # But the placeholder isn't recorded as an affected path...
    assert "%2" not in summary.affected_paths
    assert not summary.affected_paths
    # ...and never appears in the human-readable summary.
    lines = summary.build_summary_lines(phase="scan", exit_code=0)
    assert all("%2" not in line for line in lines)


def test_real_location_still_recorded():
    summary = analyze_makemkv_messages([
        "Error 'Scsi error - NOT READY:LOGICAL UNIT IS IN PROCESS OF "
        "BECOMING READY' occurred while reading '/VIDEO_TS/VTS_05_1.VOB' "
        "at offset '0'",
    ])
    assert summary.affected_paths["/VIDEO_TS/VTS_05_1.VOB"] == 1
