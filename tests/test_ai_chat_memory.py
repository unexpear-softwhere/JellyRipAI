import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.ai_chat_memory import AIChatMemory


def test_ai_chat_memory_compacts_old_turns_into_summary_and_trace():
    memory = AIChatMemory(max_recent_turns=2, max_summary_chars=400, max_trace_events=4)

    assert memory.remember_turn("assistant", "Welcome back.") is None
    assert memory.remember_turn("user", "Old question.") is None

    event = memory.remember_turn("assistant", "New answer.")
    payload = memory.build_context_payload()

    assert event is not None
    assert payload["recent_turns"] == [
        {"role": "user", "content": "Old question."},
        {"role": "assistant", "content": "New answer."},
    ]
    assert "Assistant: Welcome back." in payload["rolling_summary"]
    assert payload["compaction_trace"][0]["reason"] == "recent_turn_limit"
    assert payload["compaction_trace"][0]["compacted_turns"][0]["content"] == "Welcome back."


def test_ai_chat_memory_pins_session_facts_separately():
    memory = AIChatMemory()
    memory.remember_turn("user", "Check progress")
    memory.pin_session_facts({"pipeline_step": "output_plan", "session_mode": "smart_rip"})

    payload = memory.build_context_payload(max_recent_turns=4)

    assert payload["recent_turns"] == [{"role": "user", "content": "Check progress"}]
    assert payload["pinned_session_facts"]["pipeline_step"] == "output_plan"
    assert payload["rolling_summary"] == ""
