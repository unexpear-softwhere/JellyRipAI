"""OMDb (Open Movie Database) lookup for the AI assistant.

A second, optional movie/show data source alongside TMDB
(``shared.ai.tmdb_lookup``).  OMDb returns IMDb-flavored data — most
usefully the **IMDb ID** — for a title, using the user's own free OMDb
API key (omdbapi.com, 1,000 lookups/day on the free tier).

When the user has BOTH a TMDB and an OMDb key configured, the assistant
queries both and passes both result sets (TMDB stays the richer,
canonical source; OMDb adds the IMDb reference).  With only one key it
uses that one.

OMDb's data is licensed CC BY-NC 4.0 — attribution required,
non-commercial.  JellyRip is free/open-source and never bundles OMDb
data (every call uses the user's own key at runtime); the Settings ->
AI panel carries the OMDb credit line for attribution.

Stdlib only.  Fail-safe: a missing key or any network/parse error
returns an empty list plus a short status string.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass

_OMDB_URL = "https://www.omdbapi.com/"


@dataclass
class OMDbResult:
    media_type: str  # "movie" | "tv"
    imdb_id: str     # e.g. "tt0317705"
    title: str
    year: str


def _coerce_year(date_str: str) -> str:
    """OMDb years may be a single year or a range ("2008-2013"); keep
    the leading 4-digit year."""
    s = str(date_str or "").strip()
    return s[:4] if len(s) >= 4 and s[:4].isdigit() else ""


def search_omdb(
    query: str,
    api_key: str,
    *,
    max_results: int = 3,
    timeout: float = 10.0,
) -> tuple[list[OMDbResult], str]:
    """Search OMDb for ``query``.

    Returns ``(results, status)``: ``status`` is ``""`` on success, else
    a short reason — ``"no OMDb key"`` (so the caller can skip it),
    ``"empty query"``, ``"no results"``, or ``"OMDb unavailable (...)"``.
    """
    q = str(query or "").strip()
    key = str(api_key or "").strip()
    if not key:
        return [], "no OMDb key"
    if not q:
        return [], "empty query"

    params = urllib.parse.urlencode({"apikey": key, "s": q})
    req = urllib.request.Request(f"{_OMDB_URL}?{params}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001 — degrade gracefully
        return [], f"OMDb unavailable ({exc.__class__.__name__})"

    if str(data.get("Response", "")).lower() != "true":
        return [], str(data.get("Error") or "no results")

    results: list[OMDbResult] = []
    for item in data.get("Search", []):
        kind = str(item.get("Type", "")).lower()
        if kind == "movie":
            media = "movie"
        elif kind in ("series", "episode"):
            media = "tv"
        else:
            continue  # skip games and anything non-movie/TV
        title = item.get("Title") or ""
        imdb_id = item.get("imdbID") or ""
        if not title or not imdb_id:
            continue
        results.append(
            OMDbResult(
                media_type=media,
                imdb_id=str(imdb_id),
                title=str(title),
                year=_coerce_year(item.get("Year", "")),
            )
        )
        if len(results) >= max_results:
            break

    if not results:
        return [], "no results"
    return results, ""


def format_for_context(query: str, results: list[OMDbResult]) -> str:
    """Render OMDb results as a compact text block for injection."""
    lines = [f'OMDB_RESULTS for "{query}":']
    for r in results:
        label = "Movie" if r.media_type == "movie" else "TV"
        head = f"- {label}: {r.title}"
        if r.year:
            head += f" ({r.year})"
        head += f" [imdb {r.imdb_id}]"
        lines.append(head)
    lines.append(
        "From OMDb (data licensed CC BY-NC; JellyRip is not affiliated "
        "with OMDb or IMDb).  The 'tt...' values are IMDb IDs."
    )
    return "\n".join(lines)
