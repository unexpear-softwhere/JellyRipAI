"""Tests for the TVmaze lookup (shared.ai.tvmaze_lookup).

Network is mocked so these are hermetic + offline.  TVmaze is the free,
KEYLESS TV source: it parses the ``/search/shows`` envelope (each item
wraps a ``show``), keeps the IMDb id from ``externals``, coerces the
``premiered`` date to a year, and fails safe.  Unlike the keyed
providers there is no "no key" path.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shared.ai.tvmaze_lookup as tvmaze
from shared.ai.tvmaze_lookup import (
    TVmazeResult,
    format_for_context,
    search_tvmaze,
)


class _FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _mock(payload, monkeypatch):
    monkeypatch.setattr(
        tvmaze.urllib.request, "urlopen",
        lambda req, timeout=10.0: _FakeResp(payload),
    )


def test_parses_show_envelope_and_imdb(monkeypatch):
    _mock([
        {"score": 0.9, "show": {
            "id": 1, "name": "3rd Rock from the Sun",
            "premiered": "1996-01-09",
            "externals": {"imdb": "tt0115082", "thetvdb": 71663},
        }},
        {"score": 0.5, "show": {
            "id": 2, "name": "Other Show", "premiered": None,
            "externals": {"imdb": None},
        }},
    ], monkeypatch)
    results, status = search_tvmaze("3rd rock")
    assert status == ""
    assert results[0].media_type == "tv"
    assert results[0].tvmaze_id == 1
    assert results[0].title == "3rd Rock from the Sun"
    assert results[0].year == "1996"
    assert results[0].imdb_id == "tt0115082"
    # Second show has no premiered/imdb — those degrade to "".
    assert results[1].year == ""
    assert results[1].imdb_id == ""


def test_keyless_no_no_key_status():
    # Empty query is the only "skip" reason — never "no key".
    assert search_tvmaze("") == ([], "empty query")


def test_no_results(monkeypatch):
    _mock([], monkeypatch)
    assert search_tvmaze("zzzzz") == ([], "no results")


def test_network_error(monkeypatch):
    def boom(req, timeout=10.0):
        raise OSError("offline")
    monkeypatch.setattr(tvmaze.urllib.request, "urlopen", boom)
    results, status = search_tvmaze("3rd rock")
    assert results == []
    assert "TVmaze unavailable" in status


def test_max_results_capped(monkeypatch):
    _mock([
        {"show": {"id": i, "name": f"S{i}", "premiered": "2000-01-01",
                  "externals": {}}}
        for i in range(10)
    ], monkeypatch)
    results, _ = search_tvmaze("s", max_results=2)
    assert len(results) == 2


def test_format_for_context_marks_tv_and_imdb():
    out = format_for_context(
        "3rd rock",
        [TVmazeResult("tv", 1, "3rd Rock from the Sun", "1996", "tt0115082")],
    )
    assert "TVMAZE_RESULTS" in out
    assert "3rd Rock from the Sun" in out
    assert "tt0115082" in out
    assert "TV series only" in out  # makes clear it's not movies
