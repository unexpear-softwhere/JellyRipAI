"""Keyless web search for the AI assistant (DuckDuckGo HTML endpoint).

The assistant has no internet by itself — the local Ollama model
especially.  This module runs a real web search **without any API key
or account** by querying DuckDuckGo's HTML results page, then returns
the top hits as structured data that the chat controller injects into
the model's context (the same pattern as the disc-scan facts).  That
way even the local model can answer "look this up" questions.

No third-party dependencies — stdlib ``urllib`` plus a tolerant HTML
scan.  Fail-safe by design: any network or parse error returns an
empty list and a short status string; the caller decides how to
surface it.  DuckDuckGo's HTML can change, so the parser is
deliberately forgiving and the whole thing degrades to "no results"
rather than raising.
"""

from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass

_DDG_HTML_URL = "https://html.duckduckgo.com/html/"
# A real browser-ish UA — the lite/html endpoints reject empty agents.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

_MAX_SNIPPET_CHARS = 300


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


# Result anchor: <a ... class="result__a" href="...">TITLE</a>
_RESULT_A_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
# Snippet: <a ... class="result__snippet" ...>SNIPPET</a>
_SNIPPET_RE = re.compile(
    r'<a[^>]+class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """Drop tags + unescape entities + collapse whitespace."""
    cleaned = html.unescape(_TAG_RE.sub("", text or ""))
    return " ".join(cleaned.split()).strip()


def _decode_ddg_href(href: str) -> str:
    """DuckDuckGo wraps results as ``//duckduckgo.com/l/?uddg=<encoded>``;
    pull the real destination out of the ``uddg`` query param."""
    if not href:
        return ""
    if "uddg=" in href:
        try:
            full = href if href.startswith("http") else "https:" + href
            params = urllib.parse.parse_qs(urllib.parse.urlparse(full).query)
            if params.get("uddg"):
                return params["uddg"][0]
        except Exception:
            pass
    if href.startswith("//"):
        return "https:" + href
    return href


def search_web(
    query: str,
    *,
    max_results: int = 5,
    timeout: float = 10.0,
) -> tuple[list[SearchResult], str]:
    """Run a keyless DuckDuckGo search.

    Returns ``(results, status)``:

    * ``results`` — up to ``max_results`` :class:`SearchResult` (may be
      empty).
    * ``status`` — ``""`` on success, otherwise a short human-readable
      reason (``"no results"``, ``"web search unavailable (...)"``,
      ``"empty query"``) suitable for showing in the chat.
    """
    q = str(query or "").strip()
    if not q:
        return [], "empty query"

    payload = urllib.parse.urlencode({"q": q}).encode("utf-8")
    req = urllib.request.Request(
        _DDG_HTML_URL,
        data=payload,
        headers={
            "User-Agent": _USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 — degrade gracefully
        return [], f"web search unavailable ({exc.__class__.__name__})"

    title_matches = list(_RESULT_A_RE.finditer(body))
    snippet_matches = list(_SNIPPET_RE.finditer(body))

    results: list[SearchResult] = []
    for i, match in enumerate(title_matches):
        if len(results) >= max_results:
            break
        title = _strip_html(match.group("title"))
        url = _decode_ddg_href(match.group("href"))
        snippet = (
            _strip_html(snippet_matches[i].group("snippet"))
            if i < len(snippet_matches)
            else ""
        )
        if len(snippet) > _MAX_SNIPPET_CHARS:
            snippet = snippet[:_MAX_SNIPPET_CHARS].rstrip() + "…"
        if title and url:
            results.append(SearchResult(title=title, url=url, snippet=snippet))

    if not results:
        return [], "no results"
    return results, ""


def format_for_context(query: str, results: list[SearchResult]) -> str:
    """Render results as a compact text block for injection into the
    model's context."""
    lines = [f'WEB_SEARCH_RESULTS for "{query}":']
    for i, r in enumerate(results, 1):
        line = f"[{i}] {r.title} - {r.url}"
        if r.snippet:
            line += f"\n    {r.snippet}"
        lines.append(line)
    lines.append(
        "Answer using these results when relevant and cite sources by URL. "
        "If they don't cover the question, say so rather than guessing."
    )
    return "\n".join(lines)
