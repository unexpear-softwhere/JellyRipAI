import unittest.mock

from gui.main_window import (
    JellyRipperGUI,
    _build_ui_help_fallback,
    _friendly_ai_chat_error,
    _looks_like_ai_payload_echo,
    _prompt_looks_like_ui_help,
)


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
    gui._append_ai_chat_message = unittest.mock.Mock()
    gui._set_ai_chat_busy = unittest.mock.Mock()
    gui._build_ai_sidebar_payload = unittest.mock.Mock(
        side_effect=AssertionError("payload build should be skipped for UI help")
    )
    gui._request_ai_response_async = unittest.mock.Mock(
        side_effect=AssertionError("AI request should be skipped for UI help")
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


def test_start_ai_chat_request_recovers_from_payload_echo():
    gui = object.__new__(JellyRipperGUI)
    gui._ai_chat_busy = False
    gui._ai_chat_history = []
    gui._ai_sidebar_visible = False
    gui._append_ai_chat_message = unittest.mock.Mock()
    gui._set_ai_chat_busy = unittest.mock.Mock()
    gui._build_ai_sidebar_payload = unittest.mock.Mock(return_value='{"request":"foo"}')
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

    gui._request_ai_response_async = unittest.mock.Mock(side_effect=_fake_async)

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
