"""Title classification layer built on top of scoring.

Wraps the existing score_title logic to produce one shared ranked result
object for Smart Rip, manual selection, logs, and the setup wizard.
"""

from __future__ import annotations

from collections.abc import Sequence

from .parsing import safe_int
from .scoring import Title, _numeric_field, _sequence_length, score_title


class ClassifiedTitle:
    """A disc title with its score translated into a shared decision model."""

    __slots__ = (
        "title",
        "score",
        "label",
        "confidence",
        "reasons",
        "valid",
        "rejection_reason",
        "rank",
        "recommended",
    )

    def __init__(
        self,
        title: Title,
        score: float,
        label: str,
        confidence: float,
        reasons: list[str] | None = None,
        valid: bool = True,
        rejection_reason: str = "",
        rank: int = 0,
        recommended: bool = False,
    ) -> None:
        self.title = title
        self.score = score
        self.label = label
        self.confidence = confidence
        self.reasons = list(reasons or [])
        self.valid = valid
        self.rejection_reason = rejection_reason
        self.rank = rank
        self.recommended = recommended

    @property
    def title_id(self) -> int:
        return int(self.title.get("id", -1))

    @property
    def display_name(self) -> str:
        return str(self.title.get("name", f"Title {self.title_id + 1}"))

    @property
    def status_text(self) -> str:
        if self.label == "EPISODE":
            return "Episode"
        if self.recommended:
            return "Recommended"
        if not self.valid:
            return f"Rejected: {self.rejection_reason or 'invalid title'}"
        if self.label == "DUPLICATE":
            return "Rejected: duplicate pattern"
        if self.label == "EXTRA":
            return "Valid extra"
        return "Secondary candidate"

    @property
    def why_text(self) -> str:
        if not self.valid and self.rejection_reason:
            return self.rejection_reason
        if self.reasons:
            return " + ".join(self.reasons)
        return "no strong signals"

    def summary(self) -> str:
        """One-line summary: 'Title 008 - MAIN (92%)'."""
        pct = int(self.confidence * 100)
        return f"{self.display_name} - {self.label} ({pct}%)"

    def detail(self) -> str:
        """Multi-line detail for logs."""
        pct = int(self.confidence * 100)
        return (
            f"{self.display_name} - {self.label} ({pct}%)\n"
            f"  Status: {self.status_text}\n"
            f"  Why: {self.why_text}"
        )

    def __repr__(self) -> str:
        return (
            f"ClassifiedTitle({self.display_name!r}, label={self.label!r}, "
            f"confidence={self.confidence:.2f})"
        )


def _build_reasons(
    title: Title,
    all_titles: Sequence[Title],
    label: str,
    best_title: Title | None,
) -> list[str]:
    """Build human-readable reasons for a title's classification."""
    reasons: list[str] = []

    duration = _numeric_field(title, "duration_seconds")
    size = _numeric_field(title, "size_bytes")
    chapters = safe_int(title.get("chapters"))

    max_duration = max((_numeric_field(t, "duration_seconds") for t in all_titles), default=0.0)
    max_size = max((_numeric_field(t, "size_bytes") for t in all_titles), default=0.0)
    max_chapters = max((safe_int(t.get("chapters")) for t in all_titles), default=0)
    max_audio = max((_sequence_length(t, "audio_tracks") for t in all_titles), default=0)

    if label == "MAIN":
        if max_duration > 0 and duration == max_duration:
            reasons.append("longest duration")
        elif max_duration > 0 and duration >= max_duration * 0.95:
            reasons.append("near-longest duration")
        if max_size > 0 and size == max_size:
            reasons.append("largest size")
        elif max_size > 0 and size >= max_size * 0.95:
            reasons.append("near-largest size")
        if max_chapters > 0 and chapters == max_chapters:
            reasons.append("most chapters")
        if max_audio > 0 and _sequence_length(title, "audio_tracks") == max_audio:
            reasons.append("most audio tracks")
        if not reasons:
            reasons.append("highest combined score")

    elif label == "DUPLICATE":
        if best_title is not None:
            reasons.append("same duration and size as main title")

    elif label == "EXTRA":
        if duration < 1200:
            reasons.append("short duration (<20 min)")
        elif duration < 2400:
            reasons.append("short duration (<40 min)")
        else:
            reasons.append("low score relative to main")

    elif label == "UNKNOWN":
        reasons.append("ambiguous signals")

    elif label == "EPISODE":
        reasons.append("full-length title")

    return reasons


def _compute_confidence(best_score: float, second_score: float, label: str) -> float:
    """Derive confidence from the gap between top candidates."""
    if label == "EPISODE":
        return 0.95

    if label == "EXTRA":
        return 0.95

    if label == "DUPLICATE":
        return 0.89

    gap = best_score - second_score

    if gap > 0.3:
        return 0.95
    if gap > 0.15:
        return 0.85
    if gap > 0.05:
        return 0.70
    return 0.60


def _is_duplicate(
    title: Title,
    best_title: Title,
    duration_tolerance: float = 60.0,
    size_ratio: float = 0.05,
) -> bool:
    """Check if a title is a likely duplicate of the best title."""
    dur_a = _numeric_field(title, "duration_seconds")
    dur_b = _numeric_field(best_title, "duration_seconds")
    size_a = _numeric_field(title, "size_bytes")
    size_b = _numeric_field(best_title, "size_bytes")

    if dur_b <= 0 or size_b <= 0:
        return False

    duration_close = abs(dur_a - dur_b) < duration_tolerance
    size_close = abs(size_a - size_b) / size_b < size_ratio if size_b > 0 else False

    return duration_close and size_close


def _validate_title(title: Title) -> tuple[bool, str]:
    duration = _numeric_field(title, "duration_seconds")
    size = _numeric_field(title, "size_bytes")

    missing: list[str] = []
    if duration <= 0:
        missing.append("duration")
    if size <= 0:
        missing.append("size")

    if not missing:
        return True, ""
    if len(missing) == 2:
        return False, "missing duration and size"
    return False, f"missing {missing[0]}"


def _title_key(title: Title) -> tuple[int, int, int]:
    return (
        safe_int(title.get("id", -1)),
        int(_numeric_field(title, "duration_seconds")),
        int(_numeric_field(title, "size_bytes")),
    )


def classify_titles(
    disc_titles: Sequence[Title], is_tv: bool = False,
) -> list[ClassifiedTitle]:
    """Classify all titles into MAIN / DUPLICATE / EXTRA / UNKNOWN.

    The returned list is sorted by score. The `recommended` flag marks the
    highest-ranked valid title, while `rank` always reflects score order.

    On a TV disc (``is_tv=True``) there is no single "main feature" — a
    disc is N episodes.  So every valid full-length title is labelled
    EPISODE and pre-recommended (the picker checks them all), and only
    the short titles fall out as EXTRA.  The movie path is unchanged.
    """
    if not disc_titles:
        return []

    scored = [(title, score_title(title, disc_titles)) for title in disc_titles]
    scored.sort(key=lambda item: item[1], reverse=True)

    valid_scored = [item for item in scored if _validate_title(item[0])[0]]
    reference_title, best_score = valid_scored[0] if valid_scored else scored[0]
    second_score = (
        valid_scored[1][1]
        if len(valid_scored) > 1
        else (scored[1][1] if len(scored) > 1 else 0.0)
    )

    results: list[ClassifiedTitle] = []
    for rank, (title, score) in enumerate(scored, start=1):
        valid, rejection_reason = _validate_title(title)

        if is_tv:
            # TV: a disc is N episodes, not one main feature.  Every
            # valid full-length title is an EPISODE (all pre-checked);
            # only short titles fall out as extras.
            if valid and _numeric_field(title, "duration_seconds") >= 1200:
                label = "EPISODE"
            else:
                label = "EXTRA"
        elif title is reference_title:
            label = "MAIN"
        elif _is_duplicate(title, reference_title):
            label = "DUPLICATE"
        elif _numeric_field(title, "duration_seconds") < 1200:
            label = "EXTRA"
        else:
            label = "UNKNOWN"

        if is_tv:
            recommended = valid and label == "EPISODE"
        else:
            recommended = valid and title is reference_title

        results.append(
            ClassifiedTitle(
                title=title,
                score=score,
                label=label,
                confidence=_compute_confidence(best_score, second_score, label),
                reasons=_build_reasons(title, disc_titles, label, reference_title),
                valid=valid,
                rejection_reason=rejection_reason,
                rank=rank,
                recommended=recommended,
            )
        )

    return results


def get_recommended_title(
    classified: Sequence[ClassifiedTitle],
) -> ClassifiedTitle | None:
    for ct in classified:
        if ct.recommended:
            return ct
    return None


def classify_and_pick_main(
    disc_titles: Sequence[Title], is_tv: bool = False,
) -> tuple[ClassifiedTitle | None, list[ClassifiedTitle]]:
    """Convenience wrapper returning (recommended, all_classified)."""
    classified = classify_titles(disc_titles, is_tv=is_tv)
    if not classified:
        return None, []
    return get_recommended_title(classified), classified


def classification_matches_titles(
    classified: Sequence[ClassifiedTitle],
    disc_titles: Sequence[Title],
) -> bool:
    if len(classified) != len(disc_titles):
        return False
    classified_keys = sorted(_title_key(ct.title) for ct in classified)
    title_keys = sorted(_title_key(title) for title in disc_titles)
    return classified_keys == title_keys


def format_classification_log(classified: list[ClassifiedTitle]) -> list[str]:
    """Format classification results for the session log."""
    lines: list[str] = []
    for ct in classified:
        pct = int(ct.confidence * 100)
        dur = int(_numeric_field(ct.title, "duration_seconds"))
        size_gb = _numeric_field(ct.title, "size_bytes") / (1024 ** 3)
        chapters = safe_int(ct.title.get("chapters"))
        lines.append(
            f"[SCAN] #{ct.rank} {ct.label}: {ct.display_name} ({pct}%) "
            f"| {ct.status_text} | {ct.why_text} "
            f"[{dur}s {size_gb:.1f}GB ch{chapters}]"
        )
    return lines


__all__ = [
    "ClassifiedTitle",
    "classification_matches_titles",
    "classify_and_pick_main",
    "classify_titles",
    "format_classification_log",
    "get_recommended_title",
]
