"""Tests for the TheTVDB v4 lookup (shared.ai.tvdb_lookup).

Network is mocked so these are hermetic + offline.  TheTVDB is the
optional PAID source: a missing key short-circuits with "no TVDB key"
(no login attempted).  When keyed, auth is two-step — login for a
bearer token, then search — so the mock routes by URL.  Verifies the
PIN is sent, the IMDb id is pulled from ``remote_ids``, series/movie
types map correctly, and every failure degrades safely.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shared.ai.tvdb_lookup as tvdb
from shared.ai.tvdb_lookup import TVDBResult, format_for_context, search_tvdb


class _FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _route(monkeypatch, *, login_payload, search_payload, sent=None):
    """Mock urlopen, dispatching by URL to the login or search payload.
    Records the decoded login body into ``sent`` when provided."""
    def fake_urlopen(req, timeout=10.0):
        url = req.full_url
        if "login" in url:
            if sent is not None and getattr(req, "data", None):
                sent.append(json.loads(req.data.decode("utf-8")))
            return _FakeResp(login_payload)
        return _FakeResp(search_payload)
    monkeypatch.setattr(tvdb.urllib.request, "urlopen", fake_urlopen)


def test_no_key_short_circuits():
    # No key → no login attempt, no network.
    assert search_tvdb("3rd rock", "") == ([], "no TVDB key")


def test_empty_query():
    assert search_tvdb("", "key") == ([], "empty query")


def test_login_sends_key_and_pin_and_parses_results(monkeypatch):
    sent: list = []
    _route(
        monkeypatch,
        login_payload={"status": "success", "data": {"token": "TOK"}},
        search_payload={"status": "success", "data": [
            {"name": "3rd Rock from the Sun", "type": "series",
             "year": "1996", "tvdb_id": "71663",
             "remote_ids": [
                 {"id": "tt0115082", "sourceName": "IMDB"},
                 {"id": "12345", "sourceName": "TheMovieDB"},
             ]},
        ]},
        sent=sent,
    )
    results, status = search_tvdb("3rd rock", "mykey", "mypin")
    assert status == ""
    # PIN + key were sent in the login body.
    assert sent and sent[0] == {"apikey": "mykey", "pin": "mypin"}
    r = results[0]
    assert r.media_type == "tv"
    assert r.tvdb_id == "71663"
    assert r.title == "3rd Rock from the Sun"
    assert r.year == "1996"
    assert r.imdb_id == "tt0115082"


def test_pin_omitted_when_blank(monkeypatch):
    sent: list = []
    _route(
        monkeypatch,
        login_payload={"data": {"token": "TOK"}},
        search_payload={"data": []},
        sent=sent,
    )
    search_tvdb("x", "mykey")  # no pin
    assert sent and sent[0] == {"apikey": "mykey"}  # no "pin" key


def test_movie_type_maps_and_people_skipped(monkeypatch):
    _route(
        monkeypatch,
        login_payload={"data": {"token": "TOK"}},
        search_payload={"data": [
            {"name": "A Person", "type": "person", "tvdb_id": "1"},
            {"name": "Some Movie", "type": "movie", "year": "2009",
             "tvdb_id": "999", "remote_ids": []},
        ]},
    )
    results, status = search_tvdb("x", "key")
    assert status == ""
    assert [r.title for r in results] == ["Some Movie"]
    assert results[0].media_type == "movie"
    assert results[0].imdb_id == ""  # no IMDb remote id present


def test_auth_failure_no_token(monkeypatch):
    _route(
        monkeypatch,
        login_payload={"status": "failure", "data": {}},
        search_payload={"data": []},
    )
    results, status = search_tvdb("x", "badkey")
    assert results == []
    assert "auth failed" in status.lower()


def test_login_network_error(monkeypatch):
    def boom(req, timeout=10.0):
        raise OSError("offline")
    monkeypatch.setattr(tvdb.urllib.request, "urlopen", boom)
    results, status = search_tvdb("x", "key")
    assert results == []
    assert "auth failed" in status.lower()


def test_format_for_context_labels_and_imdb():
    out = format_for_context(
        "3rd rock",
        [TVDBResult("tv", "71663", "3rd Rock from the Sun", "1996", "tt0115082")],
    )
    assert "TVDB_RESULTS" in out
    assert "thetvdb 71663" in out
    assert "tt0115082" in out
