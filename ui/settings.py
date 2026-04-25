"""Settings-focused UI helpers."""

from __future__ import annotations

from typing import Any

from transcode.profiles import TranscodeProfile, describe_profile


def summarize_profile(profile: TranscodeProfile | dict[str, Any] | None) -> str:
    """Return the human summary shown under transcode profile pickers."""
    if profile is None:
        return "Profile details unavailable."
    return describe_profile(profile)
