"""Helpers for condensing and classifying noisy MakeMKV log output."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

_READING_PATH_RE = re.compile(r"occurred while reading '([^']+)'", re.IGNORECASE)


def _is_scsi_error(message: str) -> bool:
    return "scsi error -" in message.lower()


def _is_profile_error(message: str) -> bool:
    return message.lower().startswith("profile parsing error:")


def _is_cell_warning(message: str) -> bool:
    return message.lower().startswith("can't locate a cell")


def _is_short_title_skip(message: str) -> bool:
    lower = message.lower()
    return (
        lower.startswith("title #")
        and "minimum title length" in lower
        and "was therefore skipped" in lower
    )


def should_compact_makemkv_message(message: str) -> bool:
    """Return True for messages that tend to flood the UI log."""
    return (
        _is_scsi_error(message)
        or _is_profile_error(message)
        or _is_cell_warning(message)
        or _is_short_title_skip(message)
    )


@dataclass
class MakeMKVIssueSummary:
    """Structured summary of a MakeMKV scan or rip run."""

    total_messages: int = 0
    scsi_error_count: int = 0
    hardware_timeout_count: int = 0
    not_ready_count: int = 0
    profile_error_count: int = 0
    cell_warning_count: int = 0
    short_title_skip_count: int = 0
    success_marker_count: int = 0
    message_counts: Counter[str] = field(default_factory=Counter)
    affected_paths: Counter[str] = field(default_factory=Counter)
    sample_messages: list[str] = field(default_factory=list)

    def record(self, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return

        self.total_messages += 1
        self.message_counts[text] += 1

        lowered = text.lower()
        is_significant = False

        if _is_scsi_error(text):
            self.scsi_error_count += 1
            is_significant = True
            if "timeout on logical unit" in lowered:
                self.hardware_timeout_count += 1
            if "logical unit is in process of becoming ready" in lowered:
                self.not_ready_count += 1
            match = _READING_PATH_RE.search(text)
            if match:
                self.affected_paths[match.group(1)] += 1
        elif _is_profile_error(text):
            self.profile_error_count += 1
            is_significant = True
        elif _is_cell_warning(text):
            self.cell_warning_count += 1
        elif _is_short_title_skip(text):
            self.short_title_skip_count += 1
        elif lowered == "operation successfully completed":
            self.success_marker_count += 1

        if is_significant and text not in self.sample_messages:
            self.sample_messages.append(text)

    def merge(self, other: "MakeMKVIssueSummary | None") -> None:
        if other is None:
            return
        self.total_messages += other.total_messages
        self.scsi_error_count += other.scsi_error_count
        self.hardware_timeout_count += other.hardware_timeout_count
        self.not_ready_count += other.not_ready_count
        self.profile_error_count += other.profile_error_count
        self.cell_warning_count += other.cell_warning_count
        self.short_title_skip_count += other.short_title_skip_count
        self.success_marker_count += other.success_marker_count
        self.message_counts.update(other.message_counts)
        self.affected_paths.update(other.affected_paths)
        for sample in other.sample_messages:
            if sample not in self.sample_messages:
                self.sample_messages.append(sample)

    @property
    def completed_successfully(self) -> bool:
        return self.success_marker_count > 0

    @property
    def significant_issue_count(self) -> int:
        return self.scsi_error_count + self.profile_error_count

    @property
    def has_actionable_issues(self) -> bool:
        return self.significant_issue_count > 0

    @property
    def has_disc_read_errors(self) -> bool:
        return self.scsi_error_count > 0

    @property
    def completed_with_errors(self) -> bool:
        return self.completed_successfully and self.has_actionable_issues

    @property
    def max_repeat_count(self) -> int:
        if not self.message_counts:
            return 0
        return max(int(count) for count in self.message_counts.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_messages": self.total_messages,
            "scsi_error_count": self.scsi_error_count,
            "hardware_timeout_count": self.hardware_timeout_count,
            "not_ready_count": self.not_ready_count,
            "profile_error_count": self.profile_error_count,
            "cell_warning_count": self.cell_warning_count,
            "short_title_skip_count": self.short_title_skip_count,
            "success_marker_count": self.success_marker_count,
            "completed_successfully": self.completed_successfully,
            "completed_with_errors": self.completed_with_errors,
            "max_repeat_count": self.max_repeat_count,
            "affected_paths": dict(self.affected_paths),
            "sample_messages": list(self.sample_messages),
        }

    def build_summary_lines(
        self,
        *,
        phase: str,
        exit_code: int | None = None,
    ) -> list[str]:
        lines: list[str] = []
        phase_label = phase.strip() or "run"
        completed_with_errors = self.completed_with_errors or (
            exit_code == 0 and self.has_actionable_issues
        )

        if self.has_disc_read_errors:
            path_summary = ""
            if self.affected_paths:
                path, count = self.affected_paths.most_common(1)[0]
                path_summary = (
                    f"; most frequent location: {path} ({count}x)"
                )
            detail_bits: list[str] = []
            if self.hardware_timeout_count:
                detail_bits.append(
                    f"hardware timeouts={self.hardware_timeout_count}"
                )
            if self.not_ready_count:
                detail_bits.append(
                    f"not-ready loops={self.not_ready_count}"
                )
            detail_suffix = (
                f" ({', '.join(detail_bits)})" if detail_bits else ""
            )
            lines.append(
                f"Warning: MakeMKV logged {self.scsi_error_count} disc-read error(s) "
                f"during {phase_label}{detail_suffix}{path_summary}."
            )

        if self.profile_error_count:
            lines.append(
                f"Warning: MakeMKV rejected {self.profile_error_count} profile token(s) "
                f"during {phase_label}; check custom MakeMKV args/profile settings."
            )

        if completed_with_errors:
            result_label = (
                f"exit code {exit_code}" if exit_code is not None else "a success status"
            )
            lines.append(
                f"Warning: MakeMKV reported {result_label}, but {phase_label} completed "
                "with logged errors."
            )

        return lines


def analyze_makemkv_messages(messages: list[str]) -> MakeMKVIssueSummary:
    """Return a structured summary for a list of plain MakeMKV messages."""
    summary = MakeMKVIssueSummary()
    for message in messages:
        summary.record(message)
    return summary


class MakeMKVMessageCoalescer:
    """Collapse repeated high-volume MakeMKV messages into one follow-up line."""

    def __init__(self) -> None:
        self._pending_message: str | None = None
        self._repeat_count = 0

    def feed(self, message: str) -> list[str]:
        text = str(message or "").strip()
        if not text:
            return []

        emitted: list[str] = []
        if self._pending_message is not None and text != self._pending_message:
            emitted.extend(self.flush())

        if not should_compact_makemkv_message(text):
            emitted.append(text)
            return emitted

        if text == self._pending_message:
            self._repeat_count += 1
            return emitted

        self._pending_message = text
        self._repeat_count = 0
        emitted.append(text)
        return emitted

    def flush(self) -> list[str]:
        if self._pending_message is None:
            return []

        emitted: list[str] = []
        if self._repeat_count > 0:
            emitted.append(
                "Previous MakeMKV message repeated "
                f"{self._repeat_count} more time(s)."
            )
        self._pending_message = None
        self._repeat_count = 0
        return emitted
