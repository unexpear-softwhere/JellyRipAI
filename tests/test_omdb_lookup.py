"""Tests for the OMDb lookup (shared.ai.omdb_lookup).

Network is mocked so these are hermetic + offline.  Mirrors the TMDB
lookup's contract: parse search results, skip non-movie/TV types,
coerce year ranges, attribute the source, and fail safe (no key / bad
response / network error all return [] + a short status).
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shared.ai.omdb_lookup as omdb
from shared.ai.omdb_lookup import OMDbResult, format_for_context, search_omdb


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
        omdb.urllib.request, "urlopen",
        lambda req, timeout=10.0: _FakeResp(payload),
    )


def test_parses_and_filters(monkeypatch):
    _mock({
        "Response": "True",
        "Search": [
            {"Title": "Shark Tale", "Year": "2004",
             "imdbID": "tt0307453", "Type": "movie"},
            {"Title": "A Series", "Year": "2008-2013",
             "imdbID": "tt1111111", "Type": "series"},
            {"Title": "A Game", "Year": "2020",
             "imdbID": "tt2222222", "Type": "game"},
        ],
    }, monkeypatch)
    results, status = search_omdb("shark tale", "key")
    assert status == ""
    # The game is skipped; movie + series kept (series -> tv).
    assert [r.title for r in results] == ["Shark Tale", "A Series"]
    assert results[0].media_type == "movie"
    assert results[0].imdb_id == "tt0307453"
    assert results[1].media_type == "tv"
    assert results[1].year == "2008"  # range collapses to leading year


def test_no_key():
    assert search_omdb("shrek", "") == ([], "no OMDb key")


def test_empty_query():
    assert search_omdb("", "key") == ([], "empty query")


def test_response_false(monkeypatch):
    _mock({"Response": "False", "Error": "Movie not found!"}, monkeypatch)
    results, status = search_omdb("zzz", "key")
    assert results == []
    assert "not found" in status.lower()


def test_network_error(monkeypatch):
    def boom(req, timeout=10.0):
        raise OSError("offline")
    monkeypatch.setattr(omdb.urllib.request, "urlopen", boom)
    results, status = search_omdb("shrek", "key")
    assert results == []
    assert "OMDb unavailable" in status


def test_format_for_context_has_attribution():
    out = format_for_context(
        "shark tale", [OMDbResult("movie", "tt0307453", "Shark Tale", "2004")]
    )
    assert "OMDB_RESULTS" in out
    assert "Shark Tale" in out and "tt0307453" in out
    assert "CC BY-NC" in out  # required attribution present
