"""Title-id extraction from MakeMKV output filenames.

MakeMKV names rip outputs ``<DiscLabel>_tNN.mkv`` (optionally with
``_partM`` splits); the literal ``title_tNN`` form only occurs when
the disc has no usable volume label.  The old pattern
``r"title_t(\\d+)"`` therefore matched ONLY label-less discs: for
every labeled disc the title-file map came back empty, silently
skipping per-file integrity expectations and partial-resume credit.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_behavior_guards import _controller_with_engine


def test_labeled_disc_filenames_yield_title_ids():
    controller, _engine = _controller_with_engine()
    f = controller._title_id_from_filename
    assert f(r"C:\temp\SHREK_t00.mkv") == 0
    assert f(r"C:\temp\THE_SECRET_LIFE_OF_PETS_2_t12.mkv") == 12
    assert f("title_t03.mkv") == 3          # label-less form still works
    assert f("SHREK_t01_part2.mkv") == 1    # split parts
    assert f("SHREK_T02.MKV") == 2          # case-insensitive


def test_non_title_files_yield_none():
    controller, _engine = _controller_with_engine()
    f = controller._title_id_from_filename
    assert f("notes.txt") is None
    assert f("metadata.json") is None
    assert f("SHREK.mkv") is None           # no _tNN suffix
