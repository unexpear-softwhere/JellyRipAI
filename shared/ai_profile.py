from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


def _normalize_choice(
    value: object,
    *,
    allowed: set[str],
    default: str,
) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in allowed:
        return normalized
    return default


def _normalize_text(value: object, *, max_length: int = 600) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text[:max_length]


@dataclass(frozen=True)
class AIProfile:
    experience_level: str = "intermediate"
    verbosity: str = "balanced"
    response_style: str = "direct"
    guidance_level: str = "balanced"
    provider_preference: str = "app_default"
    privacy_preference: str = "standard"
    custom_instructions: str = ""

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object] | None) -> "AIProfile":
        data = raw if isinstance(raw, Mapping) else {}
        return cls(
            experience_level=_normalize_choice(
                data.get("experience_level"),
                allowed={"new", "intermediate", "advanced"},
                default=cls.experience_level,
            ),
            verbosity=_normalize_choice(
                data.get("verbosity"),
                allowed={"concise", "balanced", "detailed"},
                default=cls.verbosity,
            ),
            response_style=_normalize_choice(
                data.get("response_style"),
                allowed={"direct", "explanatory"},
                default=cls.response_style,
            ),
            guidance_level=_normalize_choice(
                data.get("guidance_level"),
                allowed={"minimal", "balanced", "proactive"},
                default=cls.guidance_level,
            ),
            provider_preference=_normalize_choice(
                data.get("provider_preference"),
                allowed={"app_default", "prefer_cloud", "prefer_local"},
                default=cls.provider_preference,
            ),
            privacy_preference=_normalize_choice(
                data.get("privacy_preference"),
                allowed={"standard", "minimize_sensitive_detail"},
                default=cls.privacy_preference,
            ),
            custom_instructions=_normalize_text(
                data.get("custom_instructions"),
            ),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "experience_level": self.experience_level,
            "verbosity": self.verbosity,
            "response_style": self.response_style,
            "guidance_level": self.guidance_level,
            "provider_preference": self.provider_preference,
            "privacy_preference": self.privacy_preference,
            "custom_instructions": self.custom_instructions,
        }


DEFAULT_AI_PROFILE: dict[str, str] = AIProfile().to_dict()

AI_PROFILE_FIELDS: tuple[tuple[str, str], ...] = (
    ("experience_level", "User experience level:"),
    ("verbosity", "Answer length:"),
    ("response_style", "Explanation style:"),
    ("guidance_level", "How proactive the assistant should be:"),
    ("provider_preference", "Preferred backend lane:"),
    ("privacy_preference", "Sensitive-detail handling:"),
)

AI_PROFILE_CHOICE_LABELS: dict[str, dict[str, str]] = {
    "experience_level": {
        "New / guided": "new",
        "Intermediate": "intermediate",
        "Advanced": "advanced",
    },
    "verbosity": {
        "Concise": "concise",
        "Balanced": "balanced",
        "Detailed": "detailed",
    },
    "response_style": {
        "Direct": "direct",
        "Explanatory": "explanatory",
    },
    "guidance_level": {
        "Minimal": "minimal",
        "Balanced": "balanced",
        "Proactive": "proactive",
    },
    "provider_preference": {
        "App default": "app_default",
        "Prefer cloud": "prefer_cloud",
        "Prefer local": "prefer_local",
    },
    "privacy_preference": {
        "Standard": "standard",
        "Minimize sensitive detail": "minimize_sensitive_detail",
    },
}

AI_PROFILE_VALUE_LABELS: dict[str, dict[str, str]] = {
    key: {value: label for label, value in labels.items()}
    for key, labels in AI_PROFILE_CHOICE_LABELS.items()
}


def load_ai_profile(cfg: Mapping[str, object] | None) -> AIProfile:
    if not isinstance(cfg, Mapping):
        return AIProfile()
    raw_profile = cfg.get("opt_ai_profile", {})
    if not isinstance(raw_profile, Mapping):
        return AIProfile()
    return AIProfile.from_mapping(raw_profile)
