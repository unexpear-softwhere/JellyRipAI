"""Naming helpers for workflow-level title and preview naming behavior."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Literal, TypeAlias

from utils.helpers import clean_name

NamingMode = Literal["disc-title", "disc-title+timestamp", "timestamp"]
ConfigLike: TypeAlias = Mapping[str, object]
TitleLike: TypeAlias = Mapping[str, object]
MakeTempTitleFn: TypeAlias = Callable[[], str]
CleanNameFn: TypeAlias = Callable[[object], str]
ChooseBestTitleFn: TypeAlias = Callable[[Sequence[TitleLike], bool], tuple[TitleLike | None, float]]

_METADATA_PROVIDER_ALIASES = {
    "tmdb": "tmdb",
    "themoviedb": "tmdb",
    "opendb": "opendb",
    "open_db": "opendb",
    "open-db": "opendb",
    "imdb": "imdb",
    "tvdb": "tvdb",
}


def normalize_naming_mode(mode_value: object) -> NamingMode:
    """Normalize configured naming mode including backward-compatible aliases."""
    mode = str(mode_value or "timestamp").strip().lower()
    if mode in {"auto-title", "disc-title"}:
        return "disc-title"
    if mode in {"auto-title+timestamp", "disc-title+timestamp"}:
        return "disc-title+timestamp"
    return "timestamp"


def normalize_metadata_provider(raw: str | None, default: str = "tmdb") -> str:
    """Normalize a metadata provider label into its canonical key."""
    token = str(raw or "").strip().lower()
    if not token:
        return default
    return _METADATA_PROVIDER_ALIASES.get(token, default)


def normalize_metadata_id(raw: str | None, provider: str | None = None) -> str:
    """Normalize user-entered metadata into a canonical ``provider:value`` form."""
    if not raw:
        return ""
    token = raw.strip().strip("[]")
    if not token:
        return ""

    match = re.match(r"^(tmdbid|opendbid|imdbid|tvdbid)-(\S+)$", token, re.IGNORECASE)
    if match:
        normalized_provider = normalize_metadata_provider(
            match.group(1).lower().removesuffix("id")
        )
        return f"{normalized_provider}:{match.group(2)}"

    match = re.match(r"^(tmdb|opendb|imdb|tvdb)[:\-](\S+)$", token, re.IGNORECASE)
    if match:
        normalized_provider = normalize_metadata_provider(match.group(1))
        return f"{normalized_provider}:{match.group(2)}"

    match = re.match(r"^(tt\d+)$", token, re.IGNORECASE)
    if match:
        return f"imdb:{match.group(1)}"

    match = re.match(r"^(\d+)$", token)
    if match:
        normalized_provider = normalize_metadata_provider(provider)
        return f"{normalized_provider}:{match.group(1)}"

    return ""


def parse_metadata_id(raw: str | None, provider: str | None = None) -> str:
    """Parse a user-entered metadata provider ID into a Jellyfin tag."""
    canonical = normalize_metadata_id(raw, provider)
    if not canonical or ":" not in canonical:
        return ""

    normalized_provider, value = canonical.split(":", 1)
    return f"[{normalized_provider}id-{value}]"


def build_movie_base_name(
    title_clean: str,
    year: str | int,
    edition: str = "",
) -> str:
    base = f"{title_clean} ({year})"
    cleaned_edition = clean_name(edition).strip() if edition else ""
    if cleaned_edition:
        base = f"{base} - {cleaned_edition}"
    return base


def build_movie_main_filename(
    title_clean: str,
    year: str | int,
    edition: str = "",
) -> str:
    return f"{build_movie_base_name(title_clean, year, edition)}.mkv"


def build_movie_folder_name(
    title_clean: str,
    year: str | int,
    metadata_id: str = "",
    edition: str = "",
) -> str:
    base = build_movie_base_name(title_clean, year, edition)
    tag  = parse_metadata_id(metadata_id)
    return f"{base} {tag}" if tag else base


def build_tv_folder_name(title_clean: str, metadata_id: str = "") -> str:
    tag = parse_metadata_id(metadata_id)
    return f"{title_clean} {tag}" if tag else title_clean


def resolve_naming_mode(cfg: ConfigLike) -> NamingMode:
    """Resolve naming mode from config with fallback for legacy key names."""
    mode_value = cfg.get("opt_naming_mode", cfg.get("opt_fallback_title_mode", "timestamp"))
    return normalize_naming_mode(mode_value)


def build_fallback_title(
    cfg: ConfigLike,
    make_temp_title_fn: MakeTempTitleFn,
    clean_name_fn: CleanNameFn,
    choose_best_title_fn: ChooseBestTitleFn,
    disc_titles: Sequence[TitleLike] | None = None,
    disc_name: str | None = None,
) -> str:
    """Build fallback title string based on the active naming mode."""
    mode = resolve_naming_mode(cfg)
    timestamp_title = make_temp_title_fn()

    if mode == "timestamp":
        return timestamp_title

    if disc_name:
        raw = clean_name_fn(disc_name.strip())
        low = raw.lower() if raw else ""
        is_generic = low.startswith("title ") or low.startswith("title_") or low.startswith("disc") or not low
        if not is_generic:
            if mode == "disc-title+timestamp":
                ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                return clean_name_fn(f"{raw}_{ts}")
            return raw

    best: TitleLike | None = None
    if disc_titles:
        best, _ = choose_best_title_fn(disc_titles, True)
        if not best:
            best, _ = choose_best_title_fn(disc_titles, False)

    if not best:
        return timestamp_title

    raw_name = str(best.get("name", "")).strip()
    raw = clean_name_fn(raw_name)
    if not raw or raw.lower().startswith("title "):
        _id = best.get('id', 0)
        raw = f"Disc_Title_{(int(_id) if isinstance(_id, (int, float)) else 0) + 1}"

    if mode == "disc-title+timestamp":
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        return clean_name_fn(f"{raw}_{ts}")

    return raw


def build_naming_preview_text(mode_label_value: object, sample_title: str, sample_suffix: str) -> str:
    """Build a human-readable naming preview example for settings UI."""
    mode = normalize_naming_mode(mode_label_value)
    if mode == "disc-title":
        return f"Example: {sample_title}"
    if mode == "disc-title+timestamp":
        return f"Example: {sample_title} [{sample_title}_{sample_suffix}]"
    return f"Example: {sample_title} [{sample_suffix}]"
