"""Settings-focused UI helpers."""

from __future__ import annotations

from typing import Any

from transcode.profile_summary import profile_summary_readable
from transcode.profiles import TranscodeProfile, describe_profile


def summarize_profile(
    profile: TranscodeProfile | dict[str, Any] | None,
    *,
    plain_english: bool = False,
) -> str:
    """Return the human summary shown under transcode profile pickers.

    By default uses the terse technical summary from
    `transcode.profiles.describe_profile()` (e.g., "Video: H.265 CRF 22").

    When ``plain_english=True``, dispatches to
    `transcode.profile_summary.profile_summary_readable` instead, which
    produces a friendlier description for non-technical users (e.g.,
    "Convert video to H.265 (smaller files, good quality)..."). The
    plain-English summary expects a nested dict shape; if ``profile`` is
    a `TranscodeProfile` instance, it's converted via ``to_dict()``
    first. If the resulting shape is not what the plain-English helper
    expects, the function silently falls back to the terse summary
    rather than raising — Settings UI rendering must never crash on an
    exotic profile.

    Wired by ``opt_plain_english_profile_summary`` in DEFAULTS (False).
    """
    if profile is None:
        return "Profile details unavailable."
    if plain_english:
        try:
            data = profile.to_dict() if hasattr(profile, "to_dict") else profile
            if isinstance(data, dict):
                return profile_summary_readable(data)
        except (KeyError, TypeError, AttributeError):
            # Plain-English helper expects a specific shape; fall through
            # to the terse summary if the input doesn't match.
            pass
    return describe_profile(profile)
