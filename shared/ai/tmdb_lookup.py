"""TMDB (The Movie Database) lookup for the AI assistant.

Gives the assistant *accurate, structured* movie/show facts — exact
title, year, TMDB id, overview — rather than relying on the model's
memory or a generic web snippet.  Used alongside the keyless web search
(``shared.ai.web_search``): web search is the general fallback, TMDB is
the authoritative source for titles/IDs when the user has supplied a
free TMDB API key (v3).

Stdlib only.  Fail-safe: a missing key or any network/parse error
returns an empty list plus a short status string; the caller falls back
to web search.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass

_TMDB_SEARCH_URL = "https://api.themoviedb.org/3/search/multi"
_MAX_OVERVIEW_CHARS = 300


@dataclass
class TMDBResult:
    media_type: str  # "movie" | "tv"
    tmdb_id: int
    title: str
    year: str
    overview: str


def _coerce_year(date_str: str) -> str:
    s = str(date_str or "").strip()
    return s[:4] if len(s) >= 4 and s[:4].isdigit() else ""


def search_tmdb(
    query: str,
    api_key: str,
    *,
    max_results: int = 3,
    timeout: float = 10.0,
) -> tuple[list[TMDBResult], str]:
    """Search TMDB (movies + TV) for ``query``.

    Returns ``(results, status)``: ``status`` is ``""`` on success, else
    a short reason — ``"no TMDB key"`` (so the caller can fall back to
    web search), ``"empty query"``, ``"no results"``, or
    ``"TMDB unavailable (...)"``.
    """
    q = str(query or "").strip()
    key = str(api_key or "").strip()
    if not key:
        return [], "no TMDB key"
    if not q:
        return [], "empty query"

    params = urllib.parse.urlencode(
        {"api_key": key, "query": q, "include_adult": "false"}
    )
    req = urllib.request.Request(f"{_TMDB_SEARCH_URL}?{params}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001 — degrade gracefully
        return [], f"TMDB unavailable ({exc.__class__.__name__})"

    results: list[TMDBResult] = []
    for item in data.get("results", []):
        media = item.get("media_type", "")
        if media not in ("movie", "tv"):
            continue
        title = item.get("title") or item.get("name") or ""
        tmdb_id = item.get("id")
        if not title or not isinstance(tmdb_id, int):
            continue
        overview = str(item.get("overview") or "").strip()
        if len(overview) > _MAX_OVERVIEW_CHARS:
            overview = overview[:_MAX_OVERVIEW_CHARS].rstrip() + "..."
        results.append(
            TMDBResult(
                media_type=media,
                tmdb_id=tmdb_id,
                title=str(title),
                year=_coerce_year(
                    item.get("release_date") or item.get("first_air_date") or ""
                ),
                overview=overview,
            )
        )
        if len(results) >= max_results:
            break

    if not results:
        return [], "no results"
    return results, ""


def format_for_context(query: str, results: list[TMDBResult]) -> str:
    """Render TMDB results as a compact text block for injection."""
    lines = [f'TMDB_RESULTS for "{query}":']
    for r in results:
        label = "Movie" if r.media_type == "movie" else "TV"
        head = f"- {label}: {r.title}"
        if r.year:
            head += f" ({r.year})"
        head += f" [tmdb {r.media_type}/{r.tmdb_id}]"
        lines.append(head)
        if r.overview:
            lines.append(f"    {r.overview}")
    lines.append(
        "These titles/IDs are authoritative (from TMDB) - prefer them for "
        "identification and library naming."
    )
    return "\n".join(lines)
