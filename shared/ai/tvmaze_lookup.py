"""TVmaze lookup for the AI assistant — the free, TV-focused source.

A TV-show-specialized backup alongside TMDB (``shared.ai.tmdb_lookup``)
and OMDb (``shared.ai.omdb_lookup``).  TVmaze's whole catalog is TV
series, so it catches shows a movie-centric query can miss, and its
``externals`` block often carries the **IMDb id** for free.

Why it's the *primary* free backup: TVmaze needs **no API key and no
signup** (unlike TheTVDB, whose v4 API is paid).  So it runs even for a
user who has configured nothing at all — every JellyRip AI install gets
TV identification out of the box.

We use TVmaze for *text facts only* (title, year, IMDb id) — never its
artwork — so the image-licensing clause in its terms doesn't apply.
JellyRip is not affiliated with TVmaze.

Stdlib only.  Fail-safe: an empty query or any network/parse error
returns an empty list plus a short status string.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass

# Singular ``/search/shows`` (not ``/singlesearch``) so we get ranked
# candidates and can take the top hit, matching the other providers.
_TVMAZE_SEARCH_URL = "https://api.tvmaze.com/search/shows"


@dataclass
class TVmazeResult:
    media_type: str  # always "tv" — TVmaze is TV-only
    tvmaze_id: int
    title: str
    year: str
    imdb_id: str  # from show.externals.imdb; "" when TVmaze has none


def _coerce_year(premiered: str) -> str:
    """``premiered`` is ``YYYY-MM-DD`` (or null); keep the leading year."""
    s = str(premiered or "").strip()
    return s[:4] if len(s) >= 4 and s[:4].isdigit() else ""


def search_tvmaze(
    query: str,
    *,
    max_results: int = 3,
    timeout: float = 10.0,
) -> tuple[list[TVmazeResult], str]:
    """Search TVmaze for ``query`` (keyless).

    Returns ``(results, status)``: ``status`` is ``""`` on success, else
    a short reason — ``"empty query"``, ``"no results"``, or
    ``"TVmaze unavailable (...)"``.  There is deliberately no "no key"
    status: TVmaze never needs one.
    """
    q = str(query or "").strip()
    if not q:
        return [], "empty query"

    params = urllib.parse.urlencode({"q": q})
    req = urllib.request.Request(
        f"{_TVMAZE_SEARCH_URL}?{params}", method="GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001 — degrade gracefully
        return [], f"TVmaze unavailable ({exc.__class__.__name__})"

    results: list[TVmazeResult] = []
    for item in data if isinstance(data, list) else []:
        show = item.get("show") if isinstance(item, dict) else None
        if not isinstance(show, dict):
            continue
        title = show.get("name") or ""
        tvmaze_id = show.get("id")
        if not title or not isinstance(tvmaze_id, int):
            continue
        externals = show.get("externals") or {}
        imdb_id = ""
        if isinstance(externals, dict):
            imdb_id = str(externals.get("imdb") or "").strip()
        results.append(
            TVmazeResult(
                media_type="tv",
                tvmaze_id=tvmaze_id,
                title=str(title),
                year=_coerce_year(show.get("premiered", "")),
                imdb_id=imdb_id,
            )
        )
        if len(results) >= max_results:
            break

    if not results:
        return [], "no results"
    return results, ""


def format_for_context(query: str, results: list[TVmazeResult]) -> str:
    """Render TVmaze results as a compact text block for injection."""
    lines = [f'TVMAZE_RESULTS for "{query}":']
    for r in results:
        head = f"- TV: {r.title}"
        if r.year:
            head += f" ({r.year})"
        head += f" [tvmaze {r.tvmaze_id}]"
        if r.imdb_id:
            head += f" [imdb {r.imdb_id}]"
        lines.append(head)
    lines.append(
        "From TVmaze (free TV database; JellyRip is not affiliated with "
        "TVmaze).  TVmaze covers TV series only.  The 'tt...' values are "
        "IMDb IDs, not TMDB IDs."
    )
    return "\n".join(lines)
