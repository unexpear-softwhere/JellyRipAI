"""64-bit MakeMKV preference — a configured 32-bit makemkvcon.exe is
upgraded to makemkvcon64.exe when it sits right beside it.

MakeMKV's 32-bit console prints a deprecation warning every rip; the
app should run the modern 64-bit build when it's installed.
"""

from __future__ import annotations

import os
import platform
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import _prefer_makemkvcon64

_WIN_ONLY = pytest.mark.skipif(
    platform.system() != "Windows", reason="path swap is Windows-only"
)


@_WIN_ONLY
def test_upgrades_32bit_when_64_sibling_exists(tmp_path):
    (tmp_path / "makemkvcon.exe").write_bytes(b"32")
    (tmp_path / "makemkvcon64.exe").write_bytes(b"64")
    got = _prefer_makemkvcon64(str(tmp_path / "makemkvcon.exe"))
    assert got == str(tmp_path / "makemkvcon64.exe")


@_WIN_ONLY
def test_leaves_32bit_when_no_64_sibling(tmp_path):
    (tmp_path / "makemkvcon.exe").write_bytes(b"32")
    p = str(tmp_path / "makemkvcon.exe")
    assert _prefer_makemkvcon64(p) == p  # 32-bit-only install → keep it


@_WIN_ONLY
def test_leaves_custom_tool_name(tmp_path):
    (tmp_path / "makemkvcon64.exe").write_bytes(b"64")
    p = str(tmp_path / "mymakemkv.exe")
    assert _prefer_makemkvcon64(p) == p  # not the stock 32-bit name


@_WIN_ONLY
def test_leaves_already_64bit(tmp_path):
    (tmp_path / "makemkvcon64.exe").write_bytes(b"64")
    p = str(tmp_path / "makemkvcon64.exe")
    assert _prefer_makemkvcon64(p) == p


def test_empty_and_none_are_safe():
    assert _prefer_makemkvcon64("") == ""
    assert _prefer_makemkvcon64(None) == ""
