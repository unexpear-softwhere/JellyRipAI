import unittest.mock
from types import SimpleNamespace

from gui.main_window import (
    JellyRipperGUI,
    _build_ui_help_fallback,
    _friendly_ai_chat_error,
    _looks_like_ai_payload_echo,
    _prompt_looks_like_ui_help,
)
from shared.ai_chat_memory import AIChatMemory


def test_friendly_ai_chat_error_explains_local_timeout():
    message = _friendly_ai_chat_error("LOCAL: timed out")

    assert "local model timed out" in message.lower()
    assert "smaller pulled model" in message.lower()
    assert "settings" in message.lower()


def test_prompt_looks_like_ui_help_detects_next_step_requests():
    assert _prompt_looks_like_ui_help(
        "Suggest what to do next from the current UI and live log."
    ) is True
    assert _prompt_looks_like_ui_help("check progress") is True
    assert _prompt_looks_like_ui_help("When did Alien come out?") is False


def test_looks_like_ai_payload_echo_detects_raw_sidebar_payload():
    assert _looks_like_ai_payload_echo(
        '{"request":"check progress","conversation_history":[],"ui_snapshot":{"status":"Ready"}}'
    ) is True
    assert _looks_like_ai_payload_echo("Alien was released in 1979.") is False


def test_build_ui_help_fallback_guides_idle_ready_state():
    snapshot = {
        "status": "Ready",
        "progress_percent": 0.0,
        "selected_drive": "Loading drives...",
        "ai_mode": "local",
        "abort_button_state": "normal",
    }
    log_tail = "Jellyfin Raw Ripper v1.0.16 started\nChoose a mode to begin"

    message = _build_ui_help_fallback(snapshot, log_tail, "LOCAL: timed out")

    assert "drive list still looks busy" in message.lower()
    assert "nothing is actively running right now" in message.lower()
    assert "local assistant model is taking too long" in message.lower()
    assert "settings" in message.lower()


def test_build_ui_help_fallback_guides_active_session_without_progress():
    snapshot = {
        "status": "Ripping all titles...",
        "progress_percent": 0.0,
        "selected_drive": "Loading drives...",
        "ai_mode": "local",
        "abort_button_state": "normal",
    }
    log_tail = "[06:03:03] Flow: ripping titles"

    message = _build_ui_help_fallback(snapshot, log_tail)

    assert "current status is ripping all titles" in message.lower()
    assert "0.0%" in message.lower()
    assert "field can lag behind the real rip state" in message.lower()
    assert "abort is available" in message.lower()


def test_resolve_ai_text_providers_respects_configured_local_timeout(monkeypatch):
    class _Provider:
        def is_available(self):
            return True

    from shared.ai import provider_registry

    monkeypatch.setattr(
        provider_registry,
        "resolve_active_cloud_provider",
        lambda: None,
    )
    monkeypatch.setattr(
        provider_registry,
        "resolve_local_provider",
        lambda: _Provider(),
    )

    gui = object.__new__(JellyRipperGUI)
    gui.cfg = {
        "opt_ai_mode": "local",
        "opt_ai_cloud_enabled": True,
        "opt_ai_local_enabled": True,
        "opt_ai_local_timeout_seconds": 12,
    }

    providers = gui._resolve_ai_text_providers()

    assert providers and providers[0][0] == "LOCAL"
    assert providers[0][2] == 12.0


def test_ai_chat_typing_text_uses_status_when_available():
    gui = object.__new__(JellyRipperGUI)

    assert gui._ai_chat_typing_text_for_status("Thinking with local...") == "Thinking with local"
    assert gui._ai_chat_typing_text_for_status("Ready") == "Thinking"


def test_set_ai_chat_busy_toggles_typing_indicator():
    class _Button:
        def __init__(self):
            self.state = None

        def configure(self, **kwargs):
            self.state = kwargs.get("state")

    class _Var:
        def __init__(self):
            self.value = ""

        def set(self, value):
            self.value = value

    gui = object.__new__(JellyRipperGUI)
    gui.ai_chat_send_btn = _Button()
    gui.ai_chat_suggest_btn = _Button()
    gui.ai_chat_status_var = _Var()
    gui._show_ai_chat_typing_indicator = unittest.mock.Mock()
    gui._hide_ai_chat_typing_indicator = unittest.mock.Mock()

    gui._set_ai_chat_busy(True, "Thinking with local...")

    assert gui._ai_chat_busy is True
    assert gui.ai_chat_send_btn.state == "disabled"
    assert gui.ai_chat_suggest_btn.state == "disabled"
    assert gui.ai_chat_status_var.value == "Thinking with local..."
    gui._show_ai_chat_typing_indicator.assert_called_once_with("Thinking with local...")

    gui._set_ai_chat_busy(False, "Ready")

    assert gui._ai_chat_busy is False
    assert gui.ai_chat_send_btn.state == "normal"
    assert gui.ai_chat_suggest_btn.state == "normal"
    assert gui.ai_chat_status_var.value == "Ready"
    gui._hide_ai_chat_typing_indicator.assert_called_once_with()


def test_format_ai_chat_transcript_includes_roles():
    gui = object.__new__(JellyRipperGUI)
    gui._ai_chat_history = [
        {"role": "user", "content": "When did Alien come out?"},
        {"role": "assistant", "content": "Alien was released in 1979."},
    ]

    transcript = gui._format_ai_chat_transcript()

    assert "You\nWhen did Alien come out?" in transcript
    assert "Assistant\nAlien was released in 1979." in transcript


def test_push_ai_chat_message_appends_history_and_updates_status():
    class _Var:
        def __init__(self):
            self.value = ""

        def set(self, value):
            self.value = value

    gui = object.__new__(JellyRipperGUI)
    gui._ai_chat_history = []
    gui._ai_chat_busy = False
    gui.ai_chat_status_var = _Var()
    gui._run_on_main = lambda fn: fn()
    gui._append_ai_chat_message = unittest.mock.Mock()
    gui._show_ai_sidebar = unittest.mock.Mock()

    gui.push_ai_chat_message(
        "assistant",
        "Suggested title: Kung Fu Panda 3",
        backend_tag="LOCAL",
        open_sidebar=False,
    )

    assert gui._ai_chat_history == [
        {"role": "assistant", "content": "Suggested title: Kung Fu Panda 3"}
    ]
    gui._append_ai_chat_message.assert_called_once_with(
        "assistant",
        "Suggested title: Kung Fu Panda 3",
        backend_tag="LOCAL",
    )
    assert gui.ai_chat_status_var.value == "New message via local"
    gui._show_ai_sidebar.assert_not_called()


def test_build_ai_sidebar_chat_messages_uses_role_history_and_snapshot():
    gui = object.__new__(JellyRipperGUI)
    gui.cfg = {
        "opt_ai_profile": {
            "experience_level": "advanced",
            "verbosity": "concise",
            "response_style": "direct",
            "guidance_level": "minimal",
            "provider_preference": "app_default",
            "privacy_preference": "standard",
            "custom_instructions": "Prefer concrete answers.",
        }
    }
    gui._ai_chat_history = [
        {"role": "assistant", "content": "Welcome back."},
        {"role": "assistant", "content": "Ready when you are."},
        {"role": "user", "content": "Old short text"},
    ]
    gui.controller = unittest.mock.Mock()
    gui.controller.build_ai_session_facts.return_value = {
        "session_mode": "smart_rip",
        "pipeline_step": "output_plan",
        "session": {"title": "Finding Nemo", "media_type": "movie"},
    }
    gui._get_ai_sidebar_snapshot = lambda **_kwargs: {
        "status": "Scanning disc...",
        "progress_percent": 0.0,
        "selected_drive": "disc:0",
        "ai_mode": "cloud",
        "abort_button_state": "normal",
        "live_log_tail": "[10:54:56] Scanning disc",
    }

    messages = gui._build_ai_sidebar_chat_messages(
        "Use the full request text here.",
        max_history=8,
        max_log_lines=10,
        max_log_chars=500,
    )

    assert messages[0]["role"] == "system"
    assert "conversational assistant inside the jellyrip desktop app" in messages[0][
        "content"
    ].lower()
    assert messages[1]["role"] == "system"
    assert '"experience_level": "advanced"' in messages[1]["content"]
    assert messages[2]["role"] == "system"
    assert '"pipeline_step": "output_plan"' in messages[2]["content"]
    assert messages[3]["role"] == "system"
    assert '"status": "Scanning disc..."' in messages[3]["content"]
    assert messages[4:] == [
        {"role": "assistant", "content": "Welcome back.\n\nReady when you are."},
        {"role": "user", "content": "Use the full request text here."},
    ]


def test_build_ai_sidebar_chat_messages_system_prompt_allows_general_metadata_knowledge():
    gui = object.__new__(JellyRipperGUI)

    messages = gui._build_ai_sidebar_chat_messages_from_payload(
        {
            "request": "its OVER THE HEDGE what year and metadata do i put in for this",
            "ai_profile": {"verbosity": "concise"},
            "conversation_history": [
                {"role": "user", "content": "what year and metadata do i put in for this"},
                {"role": "user", "content": "its OVER THE HEDGE"},
            ],
            "pinned_session_facts": {"pipeline_step": "identity_prompt"},
            "ui_snapshot": {"status": "Ready"},
        }
    )

    assert "release year" in messages[0]["content"]
    assert "tmdb" in messages[0]["content"].lower()
    assert "metadata to enter" in messages[0]["content"].lower()
    assert "not as a limit on general movie or tv knowledge" in messages[3]["content"].lower()


def test_build_ai_sidebar_chat_messages_system_prompt_keeps_greetings_conversational():
    gui = object.__new__(JellyRipperGUI)

    messages = gui._build_ai_sidebar_chat_messages_from_payload(
        {
            "request": "hi",
            "ai_profile": {"verbosity": "balanced"},
            "conversation_history": [],
            "pinned_session_facts": {"pipeline_step": "idle"},
            "ui_snapshot": {"status": "Ready", "selected_drive": "Loading drives..."},
        }
    )

    assert "greeting" in messages[0]["content"].lower()
    assert "respond naturally" in messages[0]["content"].lower()


def test_build_ai_sidebar_context_payload_includes_profile_facts_and_snapshot():
    gui = object.__new__(JellyRipperGUI)
    gui.cfg = {
        "opt_ai_profile": {
            "experience_level": "advanced",
            "verbosity": "concise",
            "response_style": "direct",
            "guidance_level": "minimal",
            "provider_preference": "app_default",
            "privacy_preference": "standard",
            "custom_instructions": "Prefer concrete answers.",
        }
    }
    gui._ai_chat_history = [{"role": "assistant", "content": "Welcome back."}]
    gui.controller = unittest.mock.Mock()
    gui.controller.build_ai_session_facts.return_value = {
        "session_mode": "smart_rip",
        "pipeline_step": "output_plan",
    }
    gui._get_ai_sidebar_snapshot = lambda **_kwargs: {
        "status": "Scanning disc...",
        "progress_percent": 0.0,
        "selected_drive": "disc:0",
        "ai_mode": "cloud",
        "abort_button_state": "normal",
        "live_log_tail": "[10:54:56] Scanning disc",
    }

    payload = gui._build_ai_sidebar_context_payload("Use the full request text here.")

    assert payload["request"] == "Use the full request text here."
    assert payload["conversation_history"] == [
        {"role": "assistant", "content": "Welcome back."}
    ]
    assert payload["conversation_summary"] == ""
    assert payload["ai_profile"]["experience_level"] == "advanced"
    assert payload["pinned_session_facts"]["pipeline_step"] == "output_plan"
    assert payload["session_facts"]["pipeline_step"] == "output_plan"
    assert payload["ui_snapshot"]["selected_drive"] == "disc:0"


def test_build_ai_sidebar_context_payload_uses_compact_memory_summary():
    gui = object.__new__(JellyRipperGUI)
    gui.cfg = {}
    gui._ai_chat_history = []
    gui._ai_chat_memory = AIChatMemory(max_recent_turns=2, max_summary_chars=400)
    gui._ai_chat_memory.remember_turn("assistant", "Welcome back.")
    gui._ai_chat_memory.remember_turn("user", "Old question.")
    gui._ai_chat_memory.remember_turn("assistant", "Old answer.")
    gui.controller = unittest.mock.Mock()
    gui.controller.build_ai_session_facts.return_value = {
        "pipeline_step": "output_plan",
    }
    gui._get_ai_sidebar_snapshot = lambda **_kwargs: {
        "status": "Ready",
        "progress_percent": 0.0,
        "selected_drive": "disc:0",
        "ai_mode": "cloud",
        "abort_button_state": "normal",
        "live_log_tail": "Ready",
    }

    payload = gui._build_ai_sidebar_context_payload("Newest question", max_history=2)

    assert payload["conversation_history"] == [
        {"role": "user", "content": "Old question."},
        {"role": "assistant", "content": "Old answer."},
    ]
    assert "Assistant: Welcome back." in payload["conversation_summary"]
    assert payload["pinned_session_facts"]["pipeline_step"] == "output_plan"
    assert payload["compaction_trace"][0]["compacted_turns"][0]["content"] == "Welcome back."


def test_build_ai_sidebar_chat_messages_includes_summary_when_present():
    gui = object.__new__(JellyRipperGUI)

    messages = gui._build_ai_sidebar_chat_messages_from_payload(
        {
            "request": "Newest question",
            "ai_profile": {"verbosity": "concise"},
            "conversation_history": [
                {"role": "assistant", "content": "Old answer."},
                {"role": "user", "content": "Newest question"},
            ],
            "conversation_summary": "Assistant: Welcome back.",
            "pinned_session_facts": {"pipeline_step": "output_plan"},
            "ui_snapshot": {"status": "Ready"},
        }
    )

    assert any(
        message["role"] == "system"
        and "Rolling conversation memory" in message["content"]
        and "Welcome back." in message["content"]
        for message in messages
    )


def test_push_ai_chat_message_can_open_sidebar():
    gui = object.__new__(JellyRipperGUI)
    gui._ai_chat_history = []
    gui._ai_chat_busy = False
    gui._run_on_main = lambda fn: fn()
    gui._append_ai_chat_message = unittest.mock.Mock()
    gui._show_ai_sidebar = unittest.mock.Mock()

    gui.push_ai_chat_message(
        "assistant",
        "Open the sidebar for this message",
        open_sidebar=True,
    )

    gui._show_ai_sidebar.assert_called_once_with()
    gui._append_ai_chat_message.assert_called_once_with(
        "assistant",
        "Open the sidebar for this message",
        backend_tag="",
    )


def test_start_ai_chat_request_uses_snapshot_response_for_ui_help():
    gui = object.__new__(JellyRipperGUI)
    gui._ai_chat_busy = False
    gui._ai_chat_history = []
    gui._ai_sidebar_visible = False
    gui._ensure_ai_profile_onboarded = unittest.mock.Mock(return_value=True)
    gui._append_ai_chat_message = unittest.mock.Mock()
    gui._set_ai_chat_busy = unittest.mock.Mock()
    gui._request_ai_chat_async = unittest.mock.Mock(
        side_effect=AssertionError("AI request should be skipped for UI help")
    )
    replay_calls = []
    gui._record_ai_chat_replay = lambda phase, **kwargs: replay_calls.append(
        {"phase": phase, **kwargs}
    )
    gui._get_ai_sidebar_snapshot = lambda **_kwargs: {
        "status": "Ripping all titles...",
        "progress_percent": 0.0,
        "selected_drive": "Loading drives...",
        "ai_mode": "local",
        "abort_button_state": "normal",
        "live_log_tail": "[06:03:03] Flow: ripping titles",
    }

    gui._start_ai_chat_request("check progress")

    assert gui._ai_chat_history[0] == {"role": "user", "content": "check progress"}
    assert gui._ai_chat_history[1]["role"] == "assistant"
    assert "ripping all titles" in gui._ai_chat_history[1]["content"].lower()
    gui._append_ai_chat_message.assert_any_call("user", "check progress")
    gui._append_ai_chat_message.assert_any_call(
        "assistant",
        gui._ai_chat_history[1]["content"],
        backend_tag="app",
    )
    gui._set_ai_chat_busy.assert_any_call(False, "Ready via app")
    assert [call["phase"] for call in replay_calls] == ["request", "response"]
    assert replay_calls[0]["details"]["mode"] == "app_ui_help"
    assert replay_calls[1]["backend"] == "app"


def test_start_ai_chat_request_requires_ai_onboarding_before_side_effects():
    gui = object.__new__(JellyRipperGUI)
    gui._ai_chat_busy = False
    gui._ai_chat_history = []
    gui._ai_sidebar_visible = False
    gui._ensure_ai_profile_onboarded = unittest.mock.Mock(return_value=False)
    gui._append_ai_chat_message = unittest.mock.Mock()
    gui._set_ai_chat_busy = unittest.mock.Mock()
    gui._remember_ai_chat_turn = unittest.mock.Mock()

    gui._start_ai_chat_request("When did Alien come out?")

    gui._ensure_ai_profile_onboarded.assert_called_once_with()
    assert gui._ai_chat_history == []
    gui._remember_ai_chat_turn.assert_not_called()
    gui._append_ai_chat_message.assert_not_called()
    gui._set_ai_chat_busy.assert_not_called()


def test_start_ai_chat_request_records_replay_for_provider_success():
    gui = object.__new__(JellyRipperGUI)
    gui._ai_chat_busy = False
    gui._ai_chat_history = []
    gui._ai_sidebar_visible = False
    gui._ensure_ai_profile_onboarded = unittest.mock.Mock(return_value=True)
    gui.cfg = {}
    gui.controller = unittest.mock.Mock()
    gui.controller.build_ai_session_facts.return_value = {
        "pipeline_step": "output_plan"
    }
    gui._append_ai_chat_message = unittest.mock.Mock()
    gui._set_ai_chat_busy = unittest.mock.Mock()
    replay_calls = []
    gui._record_ai_chat_replay = lambda phase, **kwargs: replay_calls.append(
        {"phase": phase, **kwargs}
    )
    gui._get_ai_sidebar_snapshot = lambda **_kwargs: {
        "status": "Ready",
        "progress_percent": 0.0,
        "selected_drive": "Loading drives...",
        "ai_mode": "local",
        "abort_button_state": "normal",
        "live_log_tail": "Choose a mode to begin",
    }

    def _fake_async(**kwargs):
        kwargs["on_success"]("Alien was released in 1979.", "CLOUD")

    gui._request_ai_chat_async = unittest.mock.Mock(side_effect=_fake_async)

    gui._start_ai_chat_request("When did Alien come out?")

    assert gui._ai_chat_history[0] == {
        "role": "user",
        "content": "When did Alien come out?",
    }
    assert gui._ai_chat_history[1]["role"] == "assistant"
    assert gui._ai_chat_history[1]["content"] == "Alien was released in 1979."
    gui._append_ai_chat_message.assert_any_call(
        "assistant",
        gui._ai_chat_history[1]["content"],
        backend_tag="CLOUD",
    )
    assert [call["phase"] for call in replay_calls] == ["request", "response"]
    assert replay_calls[0]["details"]["payload"]["request"] == "When did Alien come out?"
    assert replay_calls[0]["details"]["session_facts"]["pipeline_step"] == "output_plan"
    assert replay_calls[0]["details"]["pinned_session_facts"]["pipeline_step"] == "output_plan"
    assert replay_calls[0]["details"]["messages_by_provider"]["LOCAL"][-1]["content"] == (
        "When did Alien come out?"
    )
    assert replay_calls[1]["backend"] == "CLOUD"
    assert replay_calls[1]["response_text"] == "Alien was released in 1979."


def test_submit_ai_chat_preserves_input_when_ai_onboarding_blocks():
    class _Text:
        def __init__(self, value):
            self.value = value
            self.delete_calls = []
            self.focused = False

        def get(self, _start, _end):
            return self.value

        def delete(self, start, end):
            self.delete_calls.append((start, end))
            self.value = ""

        def focus_set(self):
            self.focused = True

    gui = object.__new__(JellyRipperGUI)
    gui.ai_chat_input = _Text("When did Alien come out?")
    gui._ensure_ai_profile_onboarded = unittest.mock.Mock(return_value=False)
    gui._start_ai_chat_request = unittest.mock.Mock()

    gui._submit_ai_chat()

    gui._ensure_ai_profile_onboarded.assert_called_once_with()
    assert gui.ai_chat_input.value == "When did Alien come out?"
    assert gui.ai_chat_input.delete_calls == []
    gui._start_ai_chat_request.assert_not_called()


def test_handle_ai_chat_return_submits_and_breaks_without_shift():
    gui = object.__new__(JellyRipperGUI)
    gui._submit_ai_chat = unittest.mock.Mock()

    result = gui._handle_ai_chat_return(SimpleNamespace(state=0))

    assert result == "break"
    gui._submit_ai_chat.assert_called_once_with()


def test_handle_ai_chat_return_allows_shift_enter_newline():
    gui = object.__new__(JellyRipperGUI)
    gui._submit_ai_chat = unittest.mock.Mock()

    result = gui._handle_ai_chat_return(SimpleNamespace(state=0x1))

    assert result is None
    gui._submit_ai_chat.assert_not_called()


def test_submit_ai_chat_routes_only_to_ai_request():
    class _Text:
        def __init__(self, value):
            self.value = value
            self.delete_calls = []
            self.focused = False

        def get(self, _start, _end):
            return self.value

        def delete(self, start, end):
            self.delete_calls.append((start, end))
            self.value = ""

        def focus_set(self):
            self.focused = True

    gui = object.__new__(JellyRipperGUI)
    gui.ai_chat_input = _Text("Suggest what to do next.")
    gui._ensure_ai_profile_onboarded = unittest.mock.Mock(return_value=True)
    gui._start_ai_chat_request = unittest.mock.Mock()
    gui.start_task = unittest.mock.Mock(
        side_effect=AssertionError("chat submit should not invoke mode start")
    )

    gui._submit_ai_chat()

    gui._ensure_ai_profile_onboarded.assert_called_once_with()
    assert gui.ai_chat_input.delete_calls == [("1.0", "end")]
    gui._start_ai_chat_request.assert_called_once_with(
        "Suggest what to do next."
    )
    gui.start_task.assert_not_called()


def test_submit_ai_chat_from_ui_logs_and_delegates():
    class _Text:
        def __init__(self, value):
            self.value = value

        def get(self, _start, _end):
            return self.value

    gui = object.__new__(JellyRipperGUI)
    gui.cfg = {"opt_debug_state": True, "opt_debug_state_json": False}
    gui.controller = unittest.mock.Mock()
    gui._ai_sidebar_visible = True
    gui._ai_chat_busy = False
    gui._task_active = False
    gui.focus_get = lambda: None
    gui.ai_chat_input = _Text("check progress")
    gui._submit_ai_chat = unittest.mock.Mock()

    gui._submit_ai_chat_from_ui("button")

    gui._submit_ai_chat.assert_called_once_with()
    gui.controller.log.assert_called_once()
    assert "ai_chat_send_invoke" in gui.controller.log.call_args[0][0]


def test_start_ai_chat_request_records_compaction_trace_in_replay():
    gui = object.__new__(JellyRipperGUI)
    gui._ai_chat_busy = False
    gui._ai_chat_history = [
        {"role": "assistant", "content": "Welcome back."},
        {"role": "user", "content": "Old question."},
    ]
    gui._ensure_ai_profile_onboarded = unittest.mock.Mock(return_value=True)
    gui._ai_chat_memory = AIChatMemory(max_recent_turns=2, max_summary_chars=400)
    gui._ai_chat_memory.remember_turn("assistant", "Welcome back.")
    gui._ai_chat_memory.remember_turn("user", "Old question.")
    gui._ai_sidebar_visible = False
    gui.cfg = {}
    gui.controller = unittest.mock.Mock()
    gui.controller.build_ai_session_facts.return_value = {
        "pipeline_step": "output_plan"
    }
    gui._append_ai_chat_message = unittest.mock.Mock()
    gui._set_ai_chat_busy = unittest.mock.Mock()
    replay_calls = []
    gui._record_ai_chat_replay = lambda phase, **kwargs: replay_calls.append(
        {"phase": phase, **kwargs}
    )
    gui._get_ai_sidebar_snapshot = lambda **_kwargs: {
        "status": "Ready",
        "progress_percent": 0.0,
        "selected_drive": "disc:0",
        "ai_mode": "local",
        "abort_button_state": "normal",
        "live_log_tail": "Ready",
    }

    def _fake_async(**kwargs):
        kwargs["on_success"]("Here is the answer.", "LOCAL")

    gui._request_ai_chat_async = unittest.mock.Mock(side_effect=_fake_async)

    gui._start_ai_chat_request("Newest question")

    assert replay_calls[0]["phase"] == "request"
    assert replay_calls[0]["details"]["compaction_trace"]
    assert replay_calls[0]["details"]["compaction_trace"][0]["compacted_turns"][0]["content"] == (
        "Welcome back."
    )
    assert replay_calls[0]["details"]["conversation_summary"]


def test_start_ai_chat_request_recovers_from_payload_echo():
    gui = object.__new__(JellyRipperGUI)
    gui._ai_chat_busy = False
    gui._ai_chat_history = []
    gui._ai_sidebar_visible = False
    gui._ensure_ai_profile_onboarded = unittest.mock.Mock(return_value=True)
    gui.cfg = {}
    gui.controller = unittest.mock.Mock()
    gui.controller.build_ai_session_facts.return_value = {}
    gui._append_ai_chat_message = unittest.mock.Mock()
    gui._set_ai_chat_busy = unittest.mock.Mock()
    replay_calls = []
    gui._record_ai_chat_replay = lambda phase, **kwargs: replay_calls.append(
        {"phase": phase, **kwargs}
    )
    gui._get_ai_sidebar_snapshot = lambda **_kwargs: {
        "status": "Ready",
        "progress_percent": 0.0,
        "selected_drive": "Loading drives...",
        "ai_mode": "local",
        "abort_button_state": "normal",
        "live_log_tail": "Choose a mode to begin",
    }

    def _fake_async(**kwargs):
        kwargs["on_success"](
            '{"request":"check progress","conversation_history":[],"ui_snapshot":{"status":"Ready"}}',
            "LOCAL",
        )

    gui._request_ai_chat_async = unittest.mock.Mock(side_effect=_fake_async)

    gui._start_ai_chat_request("When did Alien come out?")

    assert gui._ai_chat_history[0] == {
        "role": "user",
        "content": "When did Alien come out?",
    }
    assert gui._ai_chat_history[1]["role"] == "assistant"
    assert "nothing is actively running right now" in gui._ai_chat_history[1][
        "content"
    ].lower()
    gui._append_ai_chat_message.assert_any_call(
        "assistant",
        gui._ai_chat_history[1]["content"],
        backend_tag="app",
    )
    assert [call["phase"] for call in replay_calls] == ["request", "response"]
    assert replay_calls[1]["backend"] == "app"
    assert replay_calls[1]["details"]["fallback_reason"] == "payload_echo"
    assert replay_calls[1]["details"]["source_backend"] == "LOCAL"


def test_record_ai_chat_replay_logs_path_once(monkeypatch):
    gui = object.__new__(JellyRipperGUI)
    gui.controller = unittest.mock.Mock()

    records = []
    monkeypatch.setattr(
        "gui.main_window.append_ai_chat_replay",
        lambda phase, **kwargs: records.append({"phase": phase, **kwargs}) or records[-1],
    )
    monkeypatch.setattr(
        "gui.main_window.ai_chat_replay_path",
        lambda: r"C:\temp\ai_chat_replay.jsonl",
    )

    first = gui._record_ai_chat_replay(
        "request",
        replay_id="replay-1",
        details={"payload": {"request": "hello"}},
    )
    second = gui._record_ai_chat_replay(
        "response",
        replay_id="replay-1",
        backend="CLOUD",
        response_text="hi",
    )

    assert first is not None
    assert second is not None
    assert gui._last_ai_chat_replay_id == "replay-1"
    assert gui._last_ai_chat_replay_path == r"C:\temp\ai_chat_replay.jsonl"
    gui.controller.log.assert_called_once_with(
        r"[AI] Sidebar chat replay: C:\temp\ai_chat_replay.jsonl"
    )


def test_request_ai_chat_async_uses_provider_chat(monkeypatch):
    class _Provider:
        def __init__(self):
            self.chat_calls = []

        def chat(self, messages, max_tokens=0, timeout=0):
            self.chat_calls.append(
                {
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "timeout": timeout,
                }
            )
            return "Alien was released in 1979."

    provider = _Provider()
    gui = object.__new__(JellyRipperGUI)
    gui.controller = type("C", (), {"log": lambda self, message: None})()
    gui.after = lambda _delay, fn: fn()
    gui._resolve_ai_text_providers = lambda: [("CLOUD", provider, 15.0)]

    result = {}
    gui._request_ai_chat_async(
        title="AI Assistant",
        messages=[
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "When did Alien come out?"},
        ],
        max_tokens=321,
        on_success=lambda response, backend: result.update(
            {"response": response, "backend": backend}
        ),
        on_error=lambda message: result.update({"error": message}),
    )

    assert "error" not in result
    assert result == {"response": "Alien was released in 1979.", "backend": "CLOUD"}
    assert provider.chat_calls == [
        {
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "When did Alien come out?"},
            ],
            "max_tokens": 321,
            "timeout": 15.0,
        }
    ]


def test_copy_ai_chat_text_uses_clipboard_methods():
    class _Var:
        def __init__(self):
            self.value = ""

        def set(self, value):
            self.value = value

    gui = object.__new__(JellyRipperGUI)
    gui.ai_chat_status_var = _Var()
    gui.clipboard_clear = unittest.mock.Mock()
    gui.clipboard_append = unittest.mock.Mock()
    gui.update_idletasks = unittest.mock.Mock()

    gui._copy_ai_chat_text("Copied message")

    gui.clipboard_clear.assert_called_once_with()
    gui.clipboard_append.assert_called_once_with("Copied message")
    gui.update_idletasks.assert_called_once_with()
    assert gui.ai_chat_status_var.value == "Copied"


def test_format_ai_chat_replay_bundle_text_includes_debug_sections():
    gui = object.__new__(JellyRipperGUI)

    text = gui._format_ai_chat_replay_bundle_text(
        {
            "replay_id": "replay-1",
            "title": "AI Assistant",
            "status": "response",
            "backend": "CLOUD",
            "first_timestamp": "2026-04-17T10:00:00-04:00",
            "last_timestamp": "2026-04-17T10:00:03-04:00",
            "phase_sequence": ["request", "response"],
            "line_count": 2,
            "request_text": "What happened?",
            "display_text": "What happened?",
            "final_answer_text": "Here is the answer.",
            "final_error_text": "",
            "ai_profile": {"verbosity": "concise"},
            "session_facts": {"pipeline_step": "output_plan"},
            "payload": {"request": "What happened?"},
            "payload_by_provider": {"LOCAL": {"request": "What happened?"}},
            "messages": [{"role": "user", "content": "What happened?"}],
            "messages_by_provider": {
                "LOCAL": [{"role": "user", "content": "What happened?"}]
            },
            "final_record": {"phase": "response"},
        }
    )

    assert "Summary" in text
    assert "Replay ID: replay-1" in text
    assert "AI Profile" in text
    assert '"verbosity": "concise"' in text
    assert "Session Facts" in text
    assert '"pipeline_step": "output_plan"' in text
    assert "Payload" in text
    assert "Messages By Provider" in text
    assert "Final Answer" in text
    assert "Here is the answer." in text


def test_refresh_ai_chat_replay_inspector_populates_bundle_list_and_selects_newest(monkeypatch):
    class _Tree:
        def __init__(self):
            self.rows = []
            self.selected = []
            self.focused = None
            self.seen = None

        def selection(self):
            return tuple(self.selected)

        def delete(self, *items):
            if not items:
                self.rows = []
                return
            doomed = set(items)
            self.rows = [row for row in self.rows if row["iid"] not in doomed]

        def get_children(self, _item=""):
            return tuple(row["iid"] for row in self.rows)

        def insert(self, _parent, _index, iid=None, values=()):
            self.rows.append({"iid": iid, "values": values})

        def selection_set(self, iid):
            self.selected = [iid]

        def focus(self, iid):
            self.focused = iid

        def see(self, iid):
            self.seen = iid

    class _Detail:
        def __init__(self):
            self.text = ""
            self.state = "disabled"

        def configure(self, **kwargs):
            if "state" in kwargs:
                self.state = kwargs["state"]

        def delete(self, _start, _end):
            self.text = ""

        def insert(self, _start, text):
            self.text = text

        def see(self, _index):
            pass

    class _Var:
        def __init__(self):
            self.value = ""

        def set(self, value):
            self.value = value

    monkeypatch.setattr(
        "gui.main_window.list_ai_chat_replay_bundles",
        lambda limit=60: [
            {
                "replay_id": "newer",
                "last_timestamp": "2026-04-17T10:00:03-04:00",
                "status": "response",
                "backend": "cloud",
                "display_text": "Newest request text",
                "request_text": "Newest request text",
                "final_answer_text": "Newest answer",
                "phase_sequence": ["request", "response"],
                "line_count": 2,
                "final_record": {"phase": "response"},
            },
            {
                "replay_id": "older",
                "last_timestamp": "2026-04-17T09:59:03-04:00",
                "status": "error",
                "backend": "local",
                "display_text": "Older request text",
                "request_text": "Older request text",
                "final_error_text": "boom",
                "phase_sequence": ["request", "error"],
                "line_count": 2,
                "final_record": {"phase": "error"},
            },
        ],
    )
    monkeypatch.setattr(
        "gui.main_window.ai_chat_replay_path",
        lambda: r"C:\temp\ai_chat_replay.jsonl",
    )

    gui = object.__new__(JellyRipperGUI)
    gui._ai_chat_replay_tree = _Tree()
    gui._ai_chat_replay_detail = _Detail()
    gui._ai_chat_replay_status_var = _Var()

    gui._refresh_ai_chat_replay_inspector()

    assert [row["iid"] for row in gui._ai_chat_replay_tree.rows] == ["newer", "older"]
    assert gui._ai_chat_replay_tree.selected == ["newer"]
    assert gui._ai_chat_replay_tree.focused == "newer"
    assert gui._ai_chat_replay_tree.seen == "newer"
    assert gui._ai_chat_replay_index["older"]["backend"] == "local"
    assert "Replay ID: newer" in gui._ai_chat_replay_detail.text
    assert "Showing replay newer" in gui._ai_chat_replay_status_var.value


def test_update_ai_chat_replay_detail_uses_selected_bundle():
    class _Tree:
        def selection(self):
            return ("older",)

    class _Detail:
        def __init__(self):
            self.text = ""

        def configure(self, **kwargs):
            pass

        def delete(self, _start, _end):
            self.text = ""

        def insert(self, _start, text):
            self.text = text

        def see(self, _index):
            pass

    class _Var:
        def __init__(self):
            self.value = ""

        def set(self, value):
            self.value = value

    gui = object.__new__(JellyRipperGUI)
    gui._ai_chat_replay_tree = _Tree()
    gui._ai_chat_replay_index = {
        "older": {
            "replay_id": "older",
            "backend": "LOCAL",
            "status": "response",
            "final_answer_text": "Older answer",
            "phase_sequence": ["request", "response"],
            "line_count": 2,
            "final_record": {"phase": "response"},
        }
    }
    gui._ai_chat_replay_detail = _Detail()
    gui._ai_chat_replay_status_var = _Var()

    gui._update_ai_chat_replay_detail()

    assert "Replay ID: older" in gui._ai_chat_replay_detail.text
    assert "Older answer" in gui._ai_chat_replay_detail.text
    assert "Showing replay older" in gui._ai_chat_replay_status_var.value


def test_copy_selected_ai_chat_replay_uses_selected_bundle():
    class _Tree:
        def selection(self):
            return ("older",)

    class _Var:
        def __init__(self):
            self.value = ""

        def set(self, value):
            self.value = value

    copied = {}
    gui = object.__new__(JellyRipperGUI)
    gui._ai_chat_replay_tree = _Tree()
    gui._ai_chat_replay_index = {
        "older": {
            "replay_id": "older",
            "status": "response",
            "backend": "LOCAL",
        }
    }
    gui._ai_chat_replay_status_var = _Var()
    gui._copy_text_to_clipboard = lambda text: copied.setdefault("text", text) or True

    gui._copy_selected_ai_chat_replay()

    assert '"replay_id": "older"' in copied["text"]
    assert gui._ai_chat_replay_status_var.value == "Copied replay older."


def test_export_selected_ai_chat_replay_writes_json_bundle(tmp_path):
    class _Tree:
        def selection(self):
            return ("older",)

    class _Var:
        def __init__(self):
            self.value = ""

        def set(self, value):
            self.value = value

    export_path = tmp_path / "ai_chat_replay_older.json"

    gui = object.__new__(JellyRipperGUI)
    gui._ai_chat_replay_tree = _Tree()
    gui._ai_chat_replay_index = {
        "older": {
            "replay_id": "older",
            "status": "response",
            "backend": "LOCAL",
            "final_answer_text": "Older answer",
        }
    }
    gui._ai_chat_replay_status_var = _Var()
    gui.ask_save_file = lambda *args, **kwargs: str(export_path)
    gui.controller = type("C", (), {"log": unittest.mock.Mock()})()

    gui._export_selected_ai_chat_replay()

    assert export_path.exists()
    exported = export_path.read_text(encoding="utf-8")
    assert '"replay_id": "older"' in exported
    assert '"final_answer_text": "Older answer"' in exported
    assert gui._ai_chat_replay_status_var.value == f"Exported replay to {export_path}"
    gui.controller.log.assert_called_once_with(
        f"[AI] Exported sidebar chat replay bundle: {export_path}"
    )


def test_clamp_ai_sidebar_width_preserves_visible_main_ui():
    gui = object.__new__(JellyRipperGUI)
    gui._ai_sidebar_min_width = 340
    gui._ai_sidebar_edge_margin = 18
    gui._ai_sidebar_main_min_visible_width = 240
    gui._ai_sidebar_width = 360
    gui.winfo_width = lambda: 1000

    assert gui._clamp_ai_sidebar_width(200) == 340
    assert gui._clamp_ai_sidebar_width(900) == 724


def test_get_ai_sidebar_overlay_bounds_anchors_to_action_buttons():
    class _Anchor:
        def winfo_ismapped(self):
            return True

        def winfo_y(self):
            return 210

    gui = object.__new__(JellyRipperGUI)
    gui._ai_sidebar_min_width = 340
    gui._ai_sidebar_edge_margin = 18
    gui._ai_sidebar_main_min_visible_width = 240
    gui._ai_sidebar_width = 500
    gui._ai_sidebar_overlay_anchor = _Anchor()
    gui.winfo_width = lambda: 1400
    gui.winfo_height = lambda: 900

    bounds = gui._get_ai_sidebar_overlay_bounds()

    assert bounds == {"x": 882, "y": 210, "width": 500, "height": 672}


def test_fit_ai_chat_text_widget_preserves_disabled_state_and_sizes_widget():
    class _Widget:
        def __init__(self):
            self.state = "disabled"
            self.width = None
            self.height = None

        def cget(self, key):
            if key == "state":
                return self.state
            raise KeyError(key)

        def configure(self, **kwargs):
            if "state" in kwargs:
                self.state = kwargs["state"]
            if "width" in kwargs:
                self.width = kwargs["width"]
            if "height" in kwargs:
                self.height = kwargs["height"]

        def count(self, _start, _end, *_what):
            return (4,)

        def get(self, _start, _end):
            return "alpha\nbeta\ngamma\ndelta"

    gui = object.__new__(JellyRipperGUI)
    gui._ai_sidebar_width = 360

    widget = _Widget()
    gui._fit_ai_chat_text_widget(widget, width=420)

    assert widget.state == "disabled"
    assert widget.width == gui._get_ai_chat_text_width_chars(420)
    assert widget.height == 4


def test_estimate_ai_chat_text_lines_wraps_long_unmapped_content():
    gui = object.__new__(JellyRipperGUI)

    assert gui._estimate_ai_chat_text_lines("", 30) == 1
    assert gui._estimate_ai_chat_text_lines("short line", 30) == 1
    assert gui._estimate_ai_chat_text_lines("x" * 80, 20) == 4


def test_fit_ai_chat_text_widget_uses_estimate_when_widget_not_mapped():
    class _Widget:
        def __init__(self):
            self.state = "disabled"
            self.width = None
            self.height = None

        def cget(self, key):
            if key == "state":
                return self.state
            raise KeyError(key)

        def configure(self, **kwargs):
            if "state" in kwargs:
                self.state = kwargs["state"]
            if "width" in kwargs:
                self.width = kwargs["width"]
            if "height" in kwargs:
                self.height = kwargs["height"]

        def get(self, _start, _end):
            return "x" * 100

        def winfo_ismapped(self):
            return False

        def count(self, *_args):
            raise AssertionError("displaylines count should not run while unmapped")

    gui = object.__new__(JellyRipperGUI)
    gui._ai_sidebar_width = 360

    widget = _Widget()
    gui._fit_ai_chat_text_widget(widget, width=300)

    assert widget.state == "disabled"
    assert widget.width == gui._get_ai_chat_text_width_chars(300)
    assert widget.height >= 2


def test_install_ai_chat_mousewheel_binding_only_binds_once():
    gui = object.__new__(JellyRipperGUI)
    gui._ai_chat_mousewheel_binding_installed = False
    calls = []

    def _bind_all(sequence, callback, add=""):
        calls.append((sequence, callback, add))

    gui.bind_all = _bind_all

    gui._install_ai_chat_mousewheel_binding()
    gui._install_ai_chat_mousewheel_binding()

    assert len(calls) == 1
    assert calls[0][0] == "<MouseWheel>"
    assert calls[0][2] == "+"
    assert gui._ai_chat_mousewheel_binding_installed is True


def test_handle_ai_chat_mousewheel_scrolls_canvas_for_nested_transcript_widget():
    class _Widget:
        def __init__(self, master=None):
            self.master = master

    class _Canvas(_Widget):
        def __init__(self, master=None):
            super().__init__(master)
            self.scroll_calls = []

        def yview_scroll(self, amount, units):
            self.scroll_calls.append((amount, units))

    gui = object.__new__(JellyRipperGUI)
    gui._ai_sidebar_visible = True
    gui._ai_chat_transcript_frame = None
    gui.ai_chat_canvas = _Canvas()
    nested_widget = _Widget(master=_Widget(master=gui.ai_chat_canvas))
    event = type("Event", (), {"widget": nested_widget, "delta": -240})()

    handled = gui._handle_ai_chat_mousewheel(event)

    assert handled == "break"
    assert gui.ai_chat_canvas.scroll_calls == [(2, "units")]


def test_handle_ai_chat_mousewheel_ignores_events_outside_transcript():
    class _Widget:
        def __init__(self, master=None):
            self.master = master

    class _Canvas(_Widget):
        def __init__(self, master=None):
            super().__init__(master)
            self.scroll_calls = []

        def yview_scroll(self, amount, units):
            self.scroll_calls.append((amount, units))

    gui = object.__new__(JellyRipperGUI)
    gui._ai_sidebar_visible = True
    gui._ai_chat_transcript_frame = None
    gui.ai_chat_canvas = _Canvas()
    event = type("Event", (), {"widget": _Widget(), "delta": -120})()

    handled = gui._handle_ai_chat_mousewheel(event)

    assert handled is None
    assert gui.ai_chat_canvas.scroll_calls == []
