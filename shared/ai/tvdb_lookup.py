"""TheTVDB (v4) lookup for the AI assistant — the paid TV source.

An optional TV-specialized source alongside TMDB, OMDb, and the free
TVmaze.  TheTVDB has deep, well-curated TV metadata, but — unlike the
others — its v4 API is **not free**: each user needs either a
negotiated commercial license or a paid user-subscription API key
($12/yr) plus a subscriber **PIN**.  So this source is off unless the
user has pasted both a key and (for user-supported keys) a PIN into
Settings -> AI.  Without a key it returns ``"no TVDB key"`` and the
caller simply falls back to the free providers.

Auth is two-step: POST the key (+ optional PIN) to ``/v4/login`` for a
bearer token, then GET ``/v4/search`` with that token.  We surface
text facts only (title, year, IMDb id) — never artwork.  JellyRip is
not affiliated with TheTVDB.

Stdlib only.  Fail-safe: a missing key, auth failure, empty query, or
any network/parse error returns an empty list plus a short status.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass

_TVDB_LOGIN_URL = "https://api4.thetvdb.com/v4/login"
_TVDB_SEARCH_URL = "https://api4.thetvdb.com/v4/search"


@dataclass
class TVDBResult:
    media_type: str  # "tv" | "movie" (TheTVDB carries both)
    tvdb_id: str
    title: str
    year: str
    imdb_id: str  # from remote_ids (sourceName IMDB); "" when absent


def _login(api_key: str, pin: str, timeout: float) -> tuple[str, str]:
    """Exchange the key (+ optional PIN) for a bearer token.

    Returns ``(token, status)``; ``status`` is ``""`` on success.
    """
    payload: dict[str, str] = {"apikey": api_key}
    if pin:
        payload["pin"] = pin
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        _TVDB_LOGIN_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001 — degrade gracefully
        return "", f"TVDB auth failed ({exc.__class__.__name__})"
    token = str((data.get("data") or {}).get("token") or "").strip()
    if not token:
        return "", "TVDB auth failed (no token)"
    return token, ""


def _imdb_from_remote_ids(remote_ids: object) -> str:
    """Pull the IMDb id (``tt...``) out of TheTVDB's remote_ids list."""
    if not isinstance(remote_ids, list):
        return ""
    for entry in remote_ids:
        if not isinstance(entry, dict):
            continue
        rid = str(entry.get("id") or "").strip()
        source = str(entry.get("sourceName") or "").strip().lower()
        if source == "imdb" or rid.startswith("tt"):
            return rid
    return ""


def search_tvdb(
    query: str,
    api_key: str,
    pin: str = "",
    *,
    max_results: int = 3,
    timeout: float = 10.0,
) -> tuple[list[TVDBResult], str]:
    """Search TheTVDB v4 for ``query``.

    Returns ``(results, status)``: ``status`` is ``""`` on success, else
    a short reason — ``"no TVDB key"`` (caller falls back to the free
    providers), ``"empty query"``, ``"no results"``, ``"TVDB auth
    failed (...)"``, or ``"TVDB unavailable (...)"``.
    """
    q = str(query or "").strip()
    key = str(api_key or "").strip()
    if not key:
        return [], "no TVDB key"
    if not q:
        return [], "empty query"

    token, auth_status = _login(key, str(pin or "").strip(), timeout)
    if not token:
        return [], auth_status

    params = urllib.parse.urlencode({"query": q, "limit": max_results})
    req = urllib.request.Request(
        f"{_TVDB_SEARCH_URL}?{params}",
        method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001 — degrade gracefully
        return [], f"TVDB unavailable ({exc.__class__.__name__})"

    results: list[TVDBResult] = []
    for item in data.get("data", []) if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type", "")).lower()
        if kind == "series":
            media = "tv"
        elif kind == "movie":
            media = "movie"
        else:
            continue  # skip people, companies, seasons, etc.
        title = item.get("name") or item.get("translations", {}).get("eng") or ""
        tvdb_id = item.get("tvdb_id") or item.get("id") or ""
        if not title or not tvdb_id:
            continue
        year = str(item.get("year") or "").strip()
        year = year[:4] if len(year) >= 4 and year[:4].isdigit() else ""
        results.append(
            TVDBResult(
                media_type=media,
                tvdb_id=str(tvdb_id),
                title=str(title),
                year=year,
                imdb_id=_imdb_from_remote_ids(item.get("remote_ids")),
            )
        )
        if len(results) >= max_results:
            break

    if not results:
        return [], "no results"
    return results, ""


def format_for_context(query: str, results: list[TVDBResult]) -> str:
    """Render TheTVDB results as a compact text block for injection."""
    lines = [f'TVDB_RESULTS for "{query}":']
    for r in results:
        label = "Movie" if r.media_type == "movie" else "TV"
        head = f"- {label}: {r.title}"
        if r.year:
            head += f" ({r.year})"
        head += f" [thetvdb {r.tvdb_id}]"
        if r.imdb_id:
            head += f" [imdb {r.imdb_id}]"
        lines.append(head)
    lines.append(
        "From TheTVDB (used under the user's own API key; JellyRip is not "
        "affiliated with TheTVDB).  The 'tt...' values are IMDb IDs, not "
        "TMDB IDs."
    )
    return "\n".join(lines)
