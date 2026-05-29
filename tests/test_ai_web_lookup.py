"""Tests for the assistant's web search + TMDB lookup modules.

These power the chat's 🌐 Web toggle (2026-05-29): keyless DuckDuckGo
search (``shared.ai.web_search``) and optional TMDB lookup
(``shared.ai.tmdb_lookup``).  Both are pure stdlib HTTP, so every test
mocks ``urllib.request.urlopen`` — no real network calls, deterministic,
and isolated (no dependency on internet / a TMDB key being present).
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.ai import tmdb_lookup, web_search


class _FakeResp:
    """Minimal stand-in for the urlopen context manager."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


# ─── web_search (DuckDuckGo) ───────────────────────────────────────

_DDG_HTML = """
<div class="result results_links">
  <a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FOver_the_Hedge">Over the Hedge - <b>Wikipedia</b></a>
  <a class="result__snippet" href="x">Over the Hedge is a 2006 American animated <b>comedy</b> film.</a>
</div>
<div class="result results_links">
  <a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.imdb.com%2Ftitle%2Ftt0327084%2F">Over the Hedge (2006) - IMDb</a>
  <a class="result__snippet" href="x">Directed by Tim Johnson.</a>
</div>
"""


def test_search_web_parses_and_decodes(monkeypatch):
    monkeypatch.setattr(
        web_search.urllib.request, "urlopen",
        lambda req, timeout=0: _FakeResp(_DDG_HTML.encode("utf-8")),
    )
    results, status = web_search.search_web("over the hedge")

    assert status == ""
    assert len(results) == 2
    # uddg redirect decoded to the real destination.
    assert results[0].url == "https://en.wikipedia.org/wiki/Over_the_Hedge"
    assert results[0].title == "Over the Hedge - Wikipedia"  # <b> stripped
    assert "2006 American animated comedy film" in results[0].snippet
    assert results[1].url == "https://www.imdb.com/title/tt0327084/"


def test_search_web_respects_max_results(monkeypatch):
    monkeypatch.setattr(
        web_search.urllib.request, "urlopen",
        lambda req, timeout=0: _FakeResp(_DDG_HTML.encode("utf-8")),
    )
    results, status = web_search.search_web("x", max_results=1)
    assert status == ""
    assert len(results) == 1


def test_search_web_empty_query_short_circuits(monkeypatch):
    called = {"n": 0}

    def _boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("should not hit the network")

    monkeypatch.setattr(web_search.urllib.request, "urlopen", _boom)
    results, status = web_search.search_web("   ")
    assert results == []
    assert status == "empty query"
    assert called["n"] == 0


def test_search_web_network_error_is_safe(monkeypatch):
    def _raise(*_a, **_k):
        raise OSError("no route to host")

    monkeypatch.setattr(web_search.urllib.request, "urlopen", _raise)
    results, status = web_search.search_web("x")
    assert results == []
    assert status.startswith("web search unavailable")


def test_search_web_no_results(monkeypatch):
    monkeypatch.setattr(
        web_search.urllib.request, "urlopen",
        lambda req, timeout=0: _FakeResp(b"<html>nothing here</html>"),
    )
    results, status = web_search.search_web("x")
    assert results == []
    assert status == "no results"


def test_web_format_for_context_includes_urls():
    results = [
        web_search.SearchResult("Title A", "https://a.example", "snippet a"),
        web_search.SearchResult("Title B", "https://b.example", ""),
    ]
    block = web_search.format_for_context("q", results)
    assert "https://a.example" in block
    assert "Title B" in block
    assert "WEB_SEARCH_RESULTS" in block


# ─── tmdb_lookup ───────────────────────────────────────────────────

_TMDB_JSON = json.dumps(
    {
        "results": [
            {
                "media_type": "movie",
                "id": 9760,
                "title": "Over the Hedge",
                "release_date": "2006-05-19",
                "overview": "A scheming raccoon...",
            },
            {
                "media_type": "tv",
                "id": 1234,
                "name": "Some Show",
                "first_air_date": "2010-09-01",
                "overview": "A show.",
            },
            {  # person → filtered out
                "media_type": "person",
                "id": 5,
                "name": "Some Actor",
            },
        ]
    }
).encode("utf-8")


def test_search_tmdb_parses_movies_and_tv(monkeypatch):
    monkeypatch.setattr(
        tmdb_lookup.urllib.request, "urlopen",
        lambda req, timeout=0: _FakeResp(_TMDB_JSON),
    )
    results, status = tmdb_lookup.search_tmdb("over the hedge", "FAKEKEY")

    assert status == ""
    assert len(results) == 2  # person filtered out
    assert results[0].title == "Over the Hedge"
    assert results[0].year == "2006"
    assert results[0].tmdb_id == 9760
    assert results[0].media_type == "movie"
    assert results[1].media_type == "tv"
    assert results[1].title == "Some Show"
    assert all(r.media_type in ("movie", "tv") for r in results)


def test_search_tmdb_without_key_short_circuits(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("should not hit the network without a key")

    monkeypatch.setattr(tmdb_lookup.urllib.request, "urlopen", _boom)
    results, status = tmdb_lookup.search_tmdb("x", "")
    assert results == []
    assert status == "no TMDB key"


def test_search_tmdb_network_error_is_safe(monkeypatch):
    def _raise(*_a, **_k):
        raise OSError("timeout")

    monkeypatch.setattr(tmdb_lookup.urllib.request, "urlopen", _raise)
    results, status = tmdb_lookup.search_tmdb("x", "KEY")
    assert results == []
    assert status.startswith("TMDB unavailable")


def test_tmdb_format_for_context_has_ids():
    results = [
        tmdb_lookup.TMDBResult("movie", 9760, "Over the Hedge", "2006", "ov"),
    ]
    block = tmdb_lookup.format_for_context("q", results)
    assert "tmdb movie/9760" in block
    assert "Over the Hedge (2006)" in block
    assert "TMDB_RESULTS" in block
