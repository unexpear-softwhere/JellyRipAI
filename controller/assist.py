"""Assist-layer helpers kept separate from the deterministic workflow."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from controller.naming import normalize_metadata_id, parse_metadata_id
from utils.classifier import ClassifiedTitle, get_recommended_title
from utils.helpers import clean_name
from utils.parsing import safe_int

_IDENTITY_SUGGESTION_SYSTEM_PROMPT = (
    "You generate default library identity suggestions for a disc-ripping app.\n"
    "Return JSON only with keys: title, year, season, metadata_provider, metadata_id.\n"
    "Rules:\n"
    "- Prefer exact disc title or obvious franchise title from the scan context.\n"
    "- For movies, include release year only when reasonably clear.\n"
    "- For TV, include series title; season only when strongly implied.\n"
    "- metadata_provider must be TMDB or OpenDB.\n"
    "- metadata_id must be empty unless you are highly confident.\n"
    "- Never add commentary, markdown, or extra keys."
)


@dataclass
class IdentitySuggestion:
    title: str = ""
    year: str = ""
    season: str = "1"
    metadata_provider: str = "TMDB"
    metadata_id: str = ""
    source: str = "fallback"


class IdentityAssist:
    """Optional assist-layer for identity suggestions and assistant prompts."""

    def __init__(self, controller: Any) -> None:
        self._controller = controller

    @staticmethod
    def normalize_identity_title(value: object) -> str:
        text = str(value or "").strip().strip("\"'")
        if not text:
            return ""
        text = re.sub(r"_+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return clean_name(text)

    @staticmethod
    def looks_generic_identity_title(value: object) -> bool:
        text = str(value or "").strip().lower()
        if not text:
            return True
        if text.startswith(("title ", "title_", "disc", "track ")):
            return True
        generic = {
            "main feature",
            "main movie",
            "feature",
            "feature presentation",
            "movie",
            "untitled",
            "bonus",
            "bonus feature",
            "extra",
            "extras",
        }
        return text in generic

    @staticmethod
    def extract_identity_year(*values: object) -> str:
        max_year = datetime.now().year + 1
        for value in values:
            text = str(value or "")
            for match in re.finditer(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", text):
                year = int(match.group(1))
                if 1900 <= year <= max_year:
                    return str(year)
        return ""

    @staticmethod
    def normalize_identity_year(value: object) -> str:
        year = IdentityAssist.extract_identity_year(value)
        return year if re.fullmatch(r"\d{4}", year) else ""

    @staticmethod
    def normalize_identity_metadata_provider(value: object) -> str:
        token = str(value or "").strip().lower()
        if token in {"opendb", "open db", "open-db"}:
            return "OpenDB"
        return "TMDB"

    @staticmethod
    def extract_identity_json(raw_text: str) -> dict[str, Any] | None:
        text = str(raw_text or "").strip()
        if not text:
            return None

        if text.startswith("```"):
            text = re.sub(
                r"^```(?:json)?\s*|\s*```$",
                "",
                text,
                flags=re.IGNORECASE | re.DOTALL,
            ).strip()

        candidates = [text]
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            candidates.append(text[start:end + 1])

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except Exception:
                continue
            if isinstance(parsed, Mapping):
                return dict(parsed)
        return None

    def build_identity_title_seed(
        self,
        disc_titles: Sequence[dict[str, Any]],
        classified: Sequence[ClassifiedTitle],
    ) -> str:
        controller = self._controller
        disc_info = getattr(controller.engine, "last_disc_info", {}) or {}
        candidates: list[str] = []

        for raw_value in (
            disc_info.get("title", ""),
            disc_info.get("volume_id", ""),
        ):
            normalized = self.normalize_identity_title(raw_value)
            if normalized:
                candidates.append(normalized)

        recommended = get_recommended_title(classified)
        if recommended is not None:
            normalized = self.normalize_identity_title(recommended.display_name)
            if normalized:
                candidates.append(normalized)

        for title in disc_titles:
            normalized = self.normalize_identity_title(title.get("name", ""))
            if normalized:
                candidates.append(normalized)

        for candidate in candidates:
            if not self.looks_generic_identity_title(candidate):
                return candidate

        return controller._fallback_title_from_mode(list(disc_titles))

    def resolve_identity_ai_providers(self) -> list[tuple[str, Any, float]]:
        controller = self._controller
        mode = str(controller.engine.cfg.get("opt_ai_mode", "cloud")).strip().lower()
        if mode == "off":
            return []
        try:
            from shared.ai.provider_registry import (
                resolve_active_cloud_provider,
                resolve_local_provider,
            )
        except Exception:
            return []

        cloud = None
        local = None

        try:
            if bool(controller.engine.cfg.get("opt_ai_cloud_enabled", True)):
                cloud = resolve_active_cloud_provider()
                if cloud and not cloud.is_available():
                    cloud = None
        except Exception:
            cloud = None

        try:
            if bool(controller.engine.cfg.get("opt_ai_local_enabled", True)):
                local = resolve_local_provider()
                if local and not local.is_available():
                    local = None
        except Exception:
            local = None

        cloud_timeout = min(
            max(float(controller.engine.cfg.get("opt_ai_cloud_timeout_seconds", 30)), 5.0),
            12.0,
        )
        local_timeout = min(
            max(float(controller.engine.cfg.get("opt_ai_local_timeout_seconds", 90)), 5.0),
            20.0,
        )

        if mode == "local":
            return [("LOCAL", local, local_timeout)] if local else []

        providers: list[tuple[str, Any, float]] = []
        if cloud:
            providers.append(("CLOUD", cloud, cloud_timeout))
        if local:
            providers.append(("LOCAL", local, local_timeout))
        return providers

    def build_identity_ai_payload(
        self,
        disc_titles: Sequence[dict[str, Any]],
        classified: Sequence[ClassifiedTitle],
        *,
        is_tv: bool,
        fallback_title: str,
    ) -> str:
        disc_info = getattr(self._controller.engine, "last_disc_info", {}) or {}
        recommended = get_recommended_title(classified)
        payload = {
            "media_type": "tv" if is_tv else "movie",
            "disc_info": {
                "title": str(disc_info.get("title", "") or ""),
                "volume_id": str(disc_info.get("volume_id", "") or ""),
                "lang_name": str(disc_info.get("lang_name", "") or ""),
                "title_count": safe_int(disc_info.get("title_count", 0)),
            },
            "fallback_title": fallback_title,
            "recommended_title": (
                recommended.display_name if recommended is not None else ""
            ),
            "titles": [
                {
                    "id": safe_int(title.get("id", -1)),
                    "name": str(title.get("name", "") or ""),
                    "duration_seconds": safe_int(title.get("duration_seconds", 0)),
                    "size_bytes": safe_int(title.get("size_bytes", 0)),
                    "chapters": safe_int(title.get("chapters", 0)),
                    "audio_tracks": len(title.get("audio_tracks", []) or []),
                    "subtitle_tracks": len(title.get("subtitle_tracks", []) or []),
                }
                for title in list(disc_titles)[:8]
            ],
            "classified_titles": [
                {
                    "title_id": item.title_id,
                    "display_name": item.display_name,
                    "label": item.label,
                    "confidence": round(float(item.confidence), 3),
                    "why": item.why_text,
                    "recommended": bool(item.recommended),
                }
                for item in list(classified)[:8]
            ],
        }
        return json.dumps(payload, ensure_ascii=True)

    def parse_identity_ai_response(
        self,
        raw_text: str,
        *,
        fallback_title: str,
        is_tv: bool,
        backend_tag: str,
    ) -> IdentitySuggestion | None:
        parsed = self.extract_identity_json(raw_text)
        if not parsed:
            return None

        title = self.normalize_identity_title(parsed.get("title", ""))
        year = self.normalize_identity_year(parsed.get("year", ""))
        season = str(parsed.get("season", "") or "").strip()
        metadata_provider = self.normalize_identity_metadata_provider(
            parsed.get("metadata_provider", "TMDB")
        )
        metadata_id = normalize_metadata_id(
            str(parsed.get("metadata_id", "") or "").strip(),
            provider=metadata_provider,
        )

        if title and not year:
            extracted_year = self.extract_identity_year(title)
            if extracted_year:
                title = re.sub(
                    rf"[\s._-]*\(?{re.escape(extracted_year)}\)?$",
                    "",
                    title,
                ).strip()
                year = extracted_year

        if not title:
            title = fallback_title
        if not season.isdigit() or int(season) <= 0:
            season = "1"

        if not title and not year and not metadata_id:
            return None

        return IdentitySuggestion(
            title=title,
            year=("" if is_tv else year),
            season=season,
            metadata_provider=metadata_provider,
            metadata_id=metadata_id,
            source=f"ai:{backend_tag.lower()}",
        )

    def request_identity_ai_suggestion(
        self,
        disc_titles: Sequence[dict[str, Any]],
        classified: Sequence[ClassifiedTitle],
        *,
        is_tv: bool,
        fallback_title: str,
    ) -> IdentitySuggestion | None:
        providers = self.resolve_identity_ai_providers()
        if not providers:
            return None

        payload = self.build_identity_ai_payload(
            disc_titles,
            classified,
            is_tv=is_tv,
            fallback_title=fallback_title,
        )
        for backend_tag, provider, timeout in providers:
            try:
                response = provider.diagnose(
                    payload,
                    _IDENTITY_SUGGESTION_SYSTEM_PROMPT,
                    max_tokens=260,
                    timeout=float(timeout),
                )
            except Exception:
                continue
            suggestion = self.parse_identity_ai_response(
                response,
                fallback_title=fallback_title,
                is_tv=is_tv,
                backend_tag=backend_tag,
            )
            if suggestion is not None:
                return suggestion
        return None

    @staticmethod
    def format_identity_suggestion_chat_message(
        suggestion: IdentitySuggestion,
        *,
        is_tv: bool,
    ) -> str:
        title = suggestion.title or "this disc"
        metadata_hint = parse_metadata_id(suggestion.metadata_id)

        if is_tv:
            parts = [f"I think this disc is probably {title}."]
            if suggestion.season and suggestion.season != "1":
                parts.append(f"Likely season: {int(suggestion.season)}.")
        else:
            lead = f"I think this looks like {title}"
            if suggestion.year:
                lead += f" ({suggestion.year})"
            parts = [f"{lead}."]

        if metadata_hint:
            parts.append(f"Metadata hint: {metadata_hint}.")
        else:
            parts.append(
                "I do not have a confident metadata ID yet, so title/year lookup is safer."
            )
        parts.append("Check it before you continue.")
        return " ".join(parts)

    def publish_identity_suggestion_to_chat(
        self,
        suggestion: IdentitySuggestion,
        *,
        is_tv: bool,
    ) -> None:
        push_message = getattr(self._controller.gui, "push_ai_chat_message", None)
        if not callable(push_message):
            return
        backend_tag = ""
        if suggestion.source.startswith("ai:"):
            backend_tag = suggestion.source.split(":", 1)[1].upper()
        try:
            push_message(
                "assistant",
                self.format_identity_suggestion_chat_message(
                    suggestion,
                    is_tv=is_tv,
                ),
                backend_tag=backend_tag,
                open_sidebar=True,
            )
        except Exception:
            return

    def ask_identity_suggestion_choice(
        self,
        suggestion: IdentitySuggestion,
        *,
        is_tv: bool,
    ) -> str:
        ask_choice = getattr(self._controller.gui, "ask_ai_identity_choice", None)
        if not callable(ask_choice):
            return "edit"
        backend_tag = ""
        if suggestion.source.startswith("ai:"):
            backend_tag = suggestion.source.split(":", 1)[1].upper()
        try:
            return str(
                ask_choice(
                    self.format_identity_suggestion_chat_message(
                        suggestion,
                        is_tv=is_tv,
                    ),
                    backend_tag=backend_tag,
                )
            ).strip().lower() or "edit"
        except Exception:
            return "edit"

    def build_identity_defaults(
        self,
        disc_titles: Sequence[dict[str, Any]],
        classified: Sequence[ClassifiedTitle],
        *,
        is_tv: bool,
    ) -> IdentitySuggestion:
        controller = self._controller
        title_seed = controller._build_identity_title_seed(disc_titles, classified)
        disc_info = getattr(controller.engine, "last_disc_info", {}) or {}
        defaults = IdentitySuggestion(
            title=title_seed,
            year=(
                ""
                if is_tv
                else controller._extract_identity_year(
                    disc_info.get("title", ""),
                    disc_info.get("volume_id", ""),
                    title_seed,
                )
            ),
            season="1",
            metadata_provider="TMDB",
            metadata_id="",
            source="scan",
        )

        ai_suggestion = controller._request_identity_ai_suggestion(
            disc_titles,
            classified,
            is_tv=is_tv,
            fallback_title=title_seed,
        )
        if ai_suggestion is None:
            return defaults

        return IdentitySuggestion(
            title=ai_suggestion.title or defaults.title,
            year=ai_suggestion.year or defaults.year,
            season=ai_suggestion.season or defaults.season,
            metadata_provider=(
                ai_suggestion.metadata_provider or defaults.metadata_provider
            ),
            metadata_id=ai_suggestion.metadata_id or defaults.metadata_id,
            source=ai_suggestion.source,
        )
