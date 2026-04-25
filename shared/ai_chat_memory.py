from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Mapping


_ALLOWED_ROLES = {"user", "assistant"}
_MAX_TURN_CHARS = 280


def _truncate_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _normalize_role(value: object) -> str:
    role = str(value or "").strip().lower()
    return role if role in _ALLOWED_ROLES else ""


def _copy_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


@dataclass
class AIChatMemory:
    max_recent_turns: int = 8
    max_summary_chars: int = 1400
    max_trace_events: int = 10
    pinned_session_facts: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.max_recent_turns = max(2, int(self.max_recent_turns or 0))
        self.max_summary_chars = max(200, int(self.max_summary_chars or 0))
        self.max_trace_events = max(1, int(self.max_trace_events or 0))
        self._recent_turns: deque[dict[str, str]] = deque()
        self._summary_lines: deque[str] = deque()
        self._compaction_trace: deque[dict[str, Any]] = deque(maxlen=self.max_trace_events)
        self._compaction_event_id = 0

    def reset(self) -> None:
        self._recent_turns.clear()
        self._summary_lines.clear()
        self._compaction_trace.clear()
        self.pinned_session_facts = {}
        self._compaction_event_id = 0

    def pin_session_facts(self, facts: Mapping[str, Any] | None) -> None:
        self.pinned_session_facts = _copy_mapping(facts)

    def remember_turn(self, role: object, content: object) -> dict[str, Any] | None:
        normalized_role = _normalize_role(role)
        normalized_content = _truncate_text(content, _MAX_TURN_CHARS)
        if not normalized_role or not normalized_content:
            return None

        compacted_turns: list[dict[str, str]] = []
        while len(self._recent_turns) >= self.max_recent_turns:
            old_turn = dict(self._recent_turns.popleft())
            compacted_turns.append(old_turn)
            self._summary_lines.append(
                f"{old_turn.get('role', 'assistant').title()}: {old_turn.get('content', '')}"
            )
            self._trim_summary_lines()

        self._recent_turns.append(
            {
                "role": normalized_role,
                "content": normalized_content,
            }
        )

        if not compacted_turns:
            return None

        self._compaction_event_id += 1
        event = {
            "event_id": self._compaction_event_id,
            "reason": "recent_turn_limit",
            "compacted_turns": compacted_turns,
            "summary_char_count": len(self.summary_text),
            "summary_line_count": len(self._summary_lines),
            "recent_turn_count": len(self._recent_turns),
        }
        self._compaction_trace.append(event)
        return dict(event)

    @property
    def summary_text(self) -> str:
        return "\n".join(self._summary_lines).strip()

    def build_context_payload(
        self,
        *,
        max_recent_turns: int | None = None,
    ) -> dict[str, Any]:
        recent_turns = [dict(item) for item in self._recent_turns]
        if isinstance(max_recent_turns, int) and max_recent_turns > 0:
            recent_turns = recent_turns[-max_recent_turns:]
        return {
            "recent_turns": recent_turns,
            "rolling_summary": self.summary_text,
            "pinned_session_facts": dict(self.pinned_session_facts),
            "compaction_trace": [
                {
                    "event_id": int(event.get("event_id", 0) or 0),
                    "reason": str(event.get("reason", "") or ""),
                    "compacted_turns": [
                        dict(turn)
                        for turn in list(event.get("compacted_turns", []))
                        if isinstance(turn, dict)
                    ],
                    "summary_char_count": int(event.get("summary_char_count", 0) or 0),
                    "summary_line_count": int(event.get("summary_line_count", 0) or 0),
                    "recent_turn_count": int(event.get("recent_turn_count", 0) or 0),
                }
                for event in list(self._compaction_trace)
                if isinstance(event, Mapping)
            ],
        }

    def _trim_summary_lines(self) -> None:
        while self._summary_lines:
            summary = self.summary_text
            if len(summary) <= self.max_summary_chars:
                return
            if len(self._summary_lines) == 1:
                self._summary_lines[0] = _truncate_text(
                    self._summary_lines[0],
                    self.max_summary_chars,
                )
                return
            self._summary_lines.popleft()
