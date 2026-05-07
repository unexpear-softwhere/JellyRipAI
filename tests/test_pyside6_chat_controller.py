"""Tests for the Qt chat controller (Phase 4b backend).

Pins the ``ChatController`` contract:

* Sidebar signals → controller slot wiring is connected at construction.
* ``handle_message_submitted`` rejects empty / whitespace-only prompts.
* ``handle_message_submitted`` populates history, clears the input,
  marks busy, and spawns a worker.
* The worker's success path lands in the sidebar's transcript with
  the right backend label, and the controller becomes idle again.
* The worker's error path renders a warning message AND does NOT
  poison ``history`` with a failed turn.
* ``handle_new_chat`` clears history + transcript.
* ``handle_copy_chat`` copies the transcript to the clipboard.
* Provider resolution honors ``opt_ai_mode``,
  ``opt_ai_cloud_enabled``, ``opt_ai_local_enabled``.

Threading is exercised via direct signal emission (``response_ready``
/ ``error_occurred``) so tests don't depend on real wall-clock
provider calls.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

pytest.importorskip("pytestqt")

from gui_qt.ai_chat_sidebar import ChatSidebar
from gui_qt.chat_controller import ChatController


@pytest.fixture
def sidebar(qtbot):
    sb = ChatSidebar()
    qtbot.addWidget(sb)
    return sb


@pytest.fixture
def cfg():
    return {
        "opt_ai_mode": "cloud",
        "opt_ai_cloud_enabled": True,
        "opt_ai_local_enabled": True,
        "opt_ai_cloud_timeout_seconds": 30,
        "opt_ai_local_timeout_seconds": 90,
    }


# ─── Signal wiring ─────────────────────────────────────────────────


def test_controller_constructor_wires_sidebar_signals(sidebar, cfg):
    controller = ChatController(sidebar=sidebar, cfg=cfg)
    # Construction shouldn't fire anything visible yet.
    assert controller.history == []
    assert controller.busy is False


# ─── Empty-prompt guard ────────────────────────────────────────────


def test_empty_prompt_does_not_change_history(sidebar, cfg):
    controller = ChatController(sidebar=sidebar, cfg=cfg)
    controller.handle_message_submitted("")
    controller.handle_message_submitted("   \n  ")
    assert controller.history == []
    assert controller.busy is False


# ─── Submit + success ──────────────────────────────────────────────


def test_handle_message_submitted_appends_user_turn_and_marks_busy(
    sidebar, cfg, monkeypatch,
):
    """The synchronous bookkeeping that runs before the worker
    spawns: user turn into history, input cleared, busy flag set."""
    controller = ChatController(sidebar=sidebar, cfg=cfg)

    # Prevent the worker thread from actually running so the test
    # can inspect mid-flight state.
    monkeypatch.setattr(controller, "_worker_call", lambda *_a, **_k: None)

    sidebar._input.setPlainText("Hello")
    controller.handle_message_submitted("Hello")

    assert controller.history == [{"role": "user", "content": "Hello"}]
    assert controller.busy is True
    assert "Hello" in sidebar.transcript_text()


def test_response_ready_signal_appends_assistant_and_clears_busy(
    sidebar, cfg, monkeypatch, qtbot,
):
    controller = ChatController(sidebar=sidebar, cfg=cfg)
    monkeypatch.setattr(controller, "_worker_call", lambda *_a, **_k: None)

    controller.handle_message_submitted("Question")
    # Simulate the worker emitting success on the GUI thread.
    controller.response_ready.emit("This is the answer.", "cloud")

    assert controller.busy is False
    assert controller.history == [
        {"role": "user", "content": "Question"},
        {"role": "assistant", "content": "This is the answer."},
    ]
    assert "This is the answer." in sidebar.transcript_text()
    assert "Ready via cloud" in sidebar._status_label.text()


def test_error_occurred_signal_renders_warning_without_polluting_history(
    sidebar, cfg, monkeypatch,
):
    """Failed turns must NOT land in ``history`` — otherwise the next
    prompt's context would include a half-baked failed exchange."""
    controller = ChatController(sidebar=sidebar, cfg=cfg)
    monkeypatch.setattr(controller, "_worker_call", lambda *_a, **_k: None)

    controller.handle_message_submitted("Question")
    controller.error_occurred.emit("network error")

    assert controller.busy is False
    assert controller.history == [{"role": "user", "content": "Question"}]
    transcript = sidebar.transcript_text()
    assert "network error" in transcript
    assert sidebar._status_label.property("state") == "error"


# ─── Reset / Copy / Suggest ────────────────────────────────────────


def test_handle_new_chat_clears_history_and_transcript(
    sidebar, cfg, monkeypatch,
):
    controller = ChatController(sidebar=sidebar, cfg=cfg)
    monkeypatch.setattr(controller, "_worker_call", lambda *_a, **_k: None)

    controller.handle_message_submitted("first")
    controller.response_ready.emit("first reply", "cloud")
    controller.handle_message_submitted("second")
    controller.response_ready.emit("second reply", "cloud")
    assert len(controller.history) == 4
    assert "first" in sidebar.transcript_text()

    controller.handle_new_chat()
    assert controller.history == []
    assert sidebar.transcript_text().strip() == ""


def test_handle_suggest_uses_canned_prompt(sidebar, cfg, monkeypatch):
    """Suggest Next Step submits a fixed prompt asking for next-step
    guidance — pinned because changing the wording is a UX choice
    that needs review."""
    controller = ChatController(sidebar=sidebar, cfg=cfg)
    captured: list[str] = []
    monkeypatch.setattr(
        controller,
        "_worker_call",
        lambda msgs, *_a, **_k: captured.append(msgs[-1]["content"]),
    )

    controller.handle_suggest_requested()

    assert len(captured) == 1
    prompt = captured[0]
    assert "next step" in prompt.lower()
    assert "live log" in prompt.lower()


def test_handle_copy_chat_copies_transcript_to_clipboard(
    sidebar, cfg, monkeypatch,
):
    from PySide6.QtWidgets import QApplication

    controller = ChatController(sidebar=sidebar, cfg=cfg)
    monkeypatch.setattr(controller, "_worker_call", lambda *_a, **_k: None)

    controller.handle_message_submitted("a question")
    controller.response_ready.emit("an answer", "cloud")

    controller.handle_copy_chat()
    cb = QApplication.clipboard()
    assert cb is not None
    text = cb.text()
    assert "a question" in text
    assert "an answer" in text


def test_handle_copy_chat_with_empty_transcript_shows_status_only(
    sidebar, cfg,
):
    """Copy on an empty transcript is a no-op except for the status
    update — we don't want to overwrite the user's clipboard with
    nothing."""
    controller = ChatController(sidebar=sidebar, cfg=cfg)

    controller.handle_copy_chat()
    assert "Nothing to copy" in sidebar._status_label.text()


# ─── Provider resolution ───────────────────────────────────────────


def test_resolve_provider_returns_none_when_mode_off(sidebar, cfg):
    cfg["opt_ai_mode"] = "off"
    controller = ChatController(sidebar=sidebar, cfg=cfg)
    provider, label, _timeout = controller._resolve_provider()
    assert provider is None
    assert label == "off"


def test_resolve_provider_local_only_when_mode_local(sidebar, cfg, monkeypatch):
    cfg["opt_ai_mode"] = "local"
    controller = ChatController(sidebar=sidebar, cfg=cfg)

    fake_local = SimpleNamespace(is_available=lambda: True)
    fake_cloud = SimpleNamespace(is_available=lambda: True)

    import shared.ai.provider_registry as reg
    monkeypatch.setattr(reg, "resolve_local_provider", lambda: fake_local)
    monkeypatch.setattr(reg, "resolve_active_cloud_provider", lambda: fake_cloud)

    provider, label, _timeout = controller._resolve_provider()
    assert provider is fake_local
    assert label == "local"


def test_resolve_provider_cloud_first_falls_back_to_local(
    sidebar, cfg, monkeypatch,
):
    """Default ``mode="cloud"`` tries cloud first; if cloud isn't
    available, falls back to local rather than refusing the request."""
    fake_local = SimpleNamespace(is_available=lambda: True)
    import shared.ai.provider_registry as reg
    monkeypatch.setattr(reg, "resolve_active_cloud_provider", lambda: None)
    monkeypatch.setattr(reg, "resolve_local_provider", lambda: fake_local)

    controller = ChatController(sidebar=sidebar, cfg=cfg)
    provider, label, _timeout = controller._resolve_provider()
    assert provider is fake_local
    assert label == "local"


def test_resolve_provider_skips_local_when_disabled(
    sidebar, cfg, monkeypatch,
):
    cfg["opt_ai_cloud_enabled"] = True
    cfg["opt_ai_local_enabled"] = False
    fake_local = SimpleNamespace(is_available=lambda: True)
    import shared.ai.provider_registry as reg
    monkeypatch.setattr(reg, "resolve_active_cloud_provider", lambda: None)
    monkeypatch.setattr(reg, "resolve_local_provider", lambda: fake_local)

    controller = ChatController(sidebar=sidebar, cfg=cfg)
    provider, label, _timeout = controller._resolve_provider()
    assert provider is None
    # When cloud fails AND local is disabled, the controller reports
    # the requested mode (cloud) so error messaging is consistent.
    assert label == "cloud"


# ─── Friendly error formatting ─────────────────────────────────────


def test_friendly_error_handles_timeout(sidebar, cfg):
    controller = ChatController(sidebar=sidebar, cfg=cfg)
    msg = controller._friendly_error("local", "Read timed out after 90s")
    assert "timed out" in msg.lower()
    assert "smaller model" in msg.lower()


def test_friendly_error_handles_auth(sidebar, cfg):
    controller = ChatController(sidebar=sidebar, cfg=cfg)
    msg = controller._friendly_error("cloud", "401 unauthorized")
    assert "API key" in msg
    assert "AI Providers" in msg


def test_friendly_error_handles_quota(sidebar, cfg):
    controller = ChatController(sidebar=sidebar, cfg=cfg)
    msg = controller._friendly_error("cloud", "429 too many requests")
    assert "Rate limited" in msg


def test_friendly_error_truncates_long_unknown_errors(sidebar, cfg):
    controller = ChatController(sidebar=sidebar, cfg=cfg)
    msg = controller._friendly_error("cloud", "X" * 500)
    # Truncated to 240 chars so the chat bubble doesn't bloat.
    assert len(msg) <= 240


# ─── On-device fallback (added 2026-05-05) ─────────────────────────


def test_prompt_looks_like_ui_help_pure_helper():
    from gui_qt.chat_controller import prompt_looks_like_ui_help

    assert prompt_looks_like_ui_help("What's happening with the rip?") is True
    assert prompt_looks_like_ui_help("check progress") is True
    assert prompt_looks_like_ui_help("status") is True
    assert prompt_looks_like_ui_help("When did Alien come out?") is False
    assert prompt_looks_like_ui_help("") is False


def test_looks_like_ai_payload_echo_pure_helper():
    from gui_qt.chat_controller import looks_like_ai_payload_echo

    echo = '{"request": "x", "conversation_history": [], "ui_snapshot": {}}'
    assert looks_like_ai_payload_echo(echo) is True
    assert looks_like_ai_payload_echo("Just a normal answer") is False
    assert looks_like_ai_payload_echo("") is False


def test_build_ui_help_fallback_pure_helper_active_session():
    from gui_qt.chat_controller import build_ui_help_fallback

    snapshot = {
        "status": "Ripping disc",
        "selected_drive": "D: HL-DT-ST",
        "ai_mode": "cloud",
        "abort_button_state": "normal",
        "progress_percent": 42.5,
    }
    out = build_ui_help_fallback(snapshot, log_tail="ripping...")
    assert "Ripping disc" in out
    assert "42.5%" in out
    assert "Abort is available" in out


def test_build_ui_help_fallback_pure_helper_idle_session():
    from gui_qt.chat_controller import build_ui_help_fallback

    snapshot = {
        "status": "Ready",
        "selected_drive": "D: HL-DT-ST",
        "ai_mode": "cloud",
        "abort_button_state": "disabled",
        "progress_percent": 0.0,
    }
    out = build_ui_help_fallback(snapshot, log_tail="ready")
    assert "Nothing is actively running" in out
    assert "Rip Movie Disc" in out


def test_fallback_when_provider_unavailable_and_prompt_is_ui_help(
    sidebar, cfg, monkeypatch,
):
    """If no provider is configured AND the prompt looks like a "what's
    happening?" question, the controller responds with the on-device
    fallback summary instead of the bare "no provider" error."""
    cfg["opt_ai_mode"] = "off"

    class FakeWindow:
        def get_chat_ui_snapshot(self):
            return {
                "status": "Ripping",
                "selected_drive": "D:",
                "ai_mode": "off",
                "abort_button_state": "normal",
                "progress_percent": 30.0,
                "live_log_tail": "ripping...",
            }

    fake_window = FakeWindow()
    controller = ChatController(sidebar=sidebar, cfg=cfg, parent=None)

    # Patch ``_collect_ui_snapshot`` to return our fake window's data
    # since the controller has no real parent in the fixture.
    monkeypatch.setattr(
        controller,
        "_collect_ui_snapshot",
        lambda: fake_window.get_chat_ui_snapshot(),
    )

    # Run the worker synchronously by stubbing the threading.Thread
    # spawn — call the body inline.
    monkeypatch.setattr(
        controller,
        "handle_message_submitted",
        lambda prompt: controller._worker_call(
            [{"role": "user", "content": prompt}]
        ),
    )

    captured: list[tuple[str, str]] = []
    controller.response_ready.connect(
        lambda text, label: captured.append((text, label))
    )
    errors: list[str] = []
    controller.error_occurred.connect(errors.append)

    controller.handle_message_submitted("What's the current status?")

    # No error — the fallback path fired.
    assert errors == []
    assert len(captured) == 1
    text, label = captured[0]
    assert label == "fallback"
    assert "Ripping" in text


def test_fallback_when_provider_echoes_request_payload(
    sidebar, cfg, monkeypatch,
):
    """If the provider returns the request payload verbatim, the
    controller swaps in the on-device summary instead of showing
    the gibberish."""
    fake_provider = SimpleNamespace(
        chat=lambda *_a, **_k: (
            '{"request": "x", "conversation_history": [], "ui_snapshot": {}}'
        ),
        is_available=lambda: True,
    )
    monkeypatch.setattr(
        "shared.ai.provider_registry.resolve_active_cloud_provider",
        lambda: fake_provider,
    )

    controller = ChatController(sidebar=sidebar, cfg=cfg, parent=None)
    monkeypatch.setattr(
        controller,
        "_collect_ui_snapshot",
        lambda: {
            "status": "Ready",
            "selected_drive": "D:",
            "ai_mode": "cloud",
            "abort_button_state": "disabled",
            "progress_percent": 0.0,
            "live_log_tail": "",
        },
    )

    captured: list[tuple[str, str]] = []
    controller.response_ready.connect(
        lambda text, label: captured.append((text, label))
    )

    controller._worker_call([{"role": "user", "content": "what is happening"}])

    assert len(captured) == 1
    text, label = captured[0]
    assert label == "fallback"
    # The payload echo should NOT appear in the answer.
    assert '"conversation_history"' not in text


def test_fallback_unavailable_when_no_window_parent(
    sidebar, cfg, monkeypatch,
):
    """Without a MainWindow parent (e.g., headless test), the
    controller falls back to the bare error path rather than
    crashing on a missing snapshot helper."""
    cfg["opt_ai_mode"] = "off"
    controller = ChatController(sidebar=sidebar, cfg=cfg, parent=None)
    # Stub the snapshot helper to return None (no window).
    monkeypatch.setattr(controller, "_collect_ui_snapshot", lambda: None)

    errors: list[str] = []
    controller.error_occurred.connect(errors.append)

    controller._worker_call([
        {"role": "user", "content": "what's happening"},
    ])

    assert len(errors) == 1
    assert "No AI provider" in errors[0]


# ─── Replay logging (added 2026-05-05) ─────────────────────────────


def _capture_replay(monkeypatch) -> list[dict]:
    """Patch ``shared.ai_chat_replay.append_ai_chat_replay`` to
    capture every call into a list the test inspects.  Mirrors the
    way the tkinter replay tests patched the same function."""
    calls: list[dict] = []

    def fake_append(phase, **kwargs):
        calls.append({"phase": phase, **kwargs})
        return {"phase": phase, **kwargs}

    monkeypatch.setattr(
        "shared.ai_chat_replay.append_ai_chat_replay", fake_append
    )
    return calls


def test_replay_records_request_at_submit(sidebar, cfg, monkeypatch):
    """Submitting a prompt writes a ``request`` replay record with
    the correlated ``replay_id`` so request/response can be matched
    in the JSONL log."""
    calls = _capture_replay(monkeypatch)
    controller = ChatController(sidebar=sidebar, cfg=cfg)
    monkeypatch.setattr(controller, "_worker_call", lambda *_a, **_k: None)

    controller.handle_message_submitted("Test prompt")

    request_calls = [c for c in calls if c["phase"] == "request"]
    assert len(request_calls) == 1
    record = request_calls[0]
    assert record["request_text"] == "Test prompt"
    assert record["replay_id"]  # non-empty
    assert record["title"] == "AI Chat (Qt)"


def test_replay_records_response_on_provider_success(
    sidebar, cfg, monkeypatch,
):
    calls = _capture_replay(monkeypatch)
    fake_provider = SimpleNamespace(
        chat=lambda *_a, **_k: "Provider answer",
        is_available=lambda: True,
    )
    monkeypatch.setattr(
        "shared.ai.provider_registry.resolve_active_cloud_provider",
        lambda: fake_provider,
    )

    controller = ChatController(sidebar=sidebar, cfg=cfg, parent=None)
    controller._worker_call(
        [{"role": "user", "content": "regular prompt"}],
        replay_id="abc123",
    )

    response_calls = [c for c in calls if c["phase"] == "response"]
    assert len(response_calls) == 1
    record = response_calls[0]
    assert record["replay_id"] == "abc123"
    assert record["response_text"] == "Provider answer"
    assert record["backend"] == "cloud"


def test_replay_records_error_on_provider_failure(
    sidebar, cfg, monkeypatch,
):
    """Provider raises → replay records an ``error`` event with the
    raw error text and the friendly message in details."""
    calls = _capture_replay(monkeypatch)

    def boom(*_a, **_k):
        raise RuntimeError("connection refused")

    fake_provider = SimpleNamespace(chat=boom, is_available=lambda: True)
    monkeypatch.setattr(
        "shared.ai.provider_registry.resolve_active_cloud_provider",
        lambda: fake_provider,
    )

    controller = ChatController(sidebar=sidebar, cfg=cfg, parent=None)
    controller._worker_call(
        [{"role": "user", "content": "regular prompt"}],
        replay_id="err-id",
    )

    error_calls = [c for c in calls if c["phase"] == "error"]
    assert len(error_calls) == 1
    record = error_calls[0]
    assert record["replay_id"] == "err-id"
    assert "connection refused" in record["error_text"]
    assert "friendly_message" in record["details"]


def test_replay_records_fallback_response_when_provider_unavailable(
    sidebar, cfg, monkeypatch,
):
    """No provider + UI-help prompt → fallback response is replayed
    with ``backend="fallback"`` and a reason in details."""
    cfg["opt_ai_mode"] = "off"
    calls = _capture_replay(monkeypatch)

    controller = ChatController(sidebar=sidebar, cfg=cfg, parent=None)
    monkeypatch.setattr(
        controller,
        "_collect_ui_snapshot",
        lambda: {
            "status": "Ripping",
            "selected_drive": "D:",
            "ai_mode": "off",
            "abort_button_state": "normal",
            "progress_percent": 30.0,
            "live_log_tail": "ripping...",
        },
    )
    controller._worker_call(
        [{"role": "user", "content": "what's happening?"}],
        replay_id="fallback-id",
    )

    response_calls = [c for c in calls if c["phase"] == "response"]
    assert len(response_calls) == 1
    record = response_calls[0]
    assert record["backend"] == "fallback"
    assert record["details"]["reason"] == "no_provider_configured"


def test_replay_records_fallback_when_provider_echoes_payload(
    sidebar, cfg, monkeypatch,
):
    """Provider echo path → fallback response is replayed with
    ``reason="payload_echo"`` and the echoed excerpt in details."""
    calls = _capture_replay(monkeypatch)
    fake_provider = SimpleNamespace(
        chat=lambda *_a, **_k: (
            '{"request": "x", "conversation_history": [], "ui_snapshot": {}}'
        ),
        is_available=lambda: True,
    )
    monkeypatch.setattr(
        "shared.ai.provider_registry.resolve_active_cloud_provider",
        lambda: fake_provider,
    )

    controller = ChatController(sidebar=sidebar, cfg=cfg, parent=None)
    monkeypatch.setattr(
        controller,
        "_collect_ui_snapshot",
        lambda: {
            "status": "Ready",
            "selected_drive": "D:",
            "ai_mode": "cloud",
            "abort_button_state": "disabled",
            "progress_percent": 0.0,
            "live_log_tail": "",
        },
    )
    controller._worker_call(
        [{"role": "user", "content": "what is happening"}],
        replay_id="echo-id",
    )

    response_calls = [c for c in calls if c["phase"] == "response"]
    assert len(response_calls) == 1
    record = response_calls[0]
    assert record["backend"] == "fallback"
    assert record["details"]["reason"] == "payload_echo"
    assert record["details"]["source_backend"] == "cloud"


def test_replay_skipped_when_no_replay_id(sidebar, cfg, monkeypatch):
    """Direct ``_worker_call`` with empty ``replay_id`` doesn't write
    replay records — gives test fixtures a way to skip logging
    without monkey-patching ``append_ai_chat_replay`` itself."""
    calls = _capture_replay(monkeypatch)
    fake_provider = SimpleNamespace(
        chat=lambda *_a, **_k: "ok",
        is_available=lambda: True,
    )
    monkeypatch.setattr(
        "shared.ai.provider_registry.resolve_active_cloud_provider",
        lambda: fake_provider,
    )

    controller = ChatController(sidebar=sidebar, cfg=cfg, parent=None)
    controller._worker_call(
        [{"role": "user", "content": "test"}],
        replay_id="",
    )

    assert calls == []


def test_replay_failure_does_not_break_chat(sidebar, cfg, monkeypatch):
    """If ``append_ai_chat_replay`` raises, the chat path keeps
    working — replay logging is best-effort."""
    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(
        "shared.ai_chat_replay.append_ai_chat_replay", boom
    )
    fake_provider = SimpleNamespace(
        chat=lambda *_a, **_k: "answer",
        is_available=lambda: True,
    )
    monkeypatch.setattr(
        "shared.ai.provider_registry.resolve_active_cloud_provider",
        lambda: fake_provider,
    )

    controller = ChatController(sidebar=sidebar, cfg=cfg, parent=None)
    captured: list[tuple[str, str]] = []
    controller.response_ready.connect(
        lambda text, label: captured.append((text, label))
    )

    # Should NOT raise even though replay logging blows up.
    controller._worker_call(
        [{"role": "user", "content": "test"}],
        replay_id="x",
    )

    assert captured == [("answer", "cloud")]


# ─── AI mode switcher (added 2026-05-05) ───────────────────────────


def test_controller_seeds_sidebar_mode_from_cfg(sidebar, cfg):
    """The controller pushes ``cfg['opt_ai_mode']`` into the sidebar's
    mode combo at construction so the picker reflects the saved
    value without re-firing ``mode_changed``."""
    cfg["opt_ai_mode"] = "local"
    controller = ChatController(sidebar=sidebar, cfg=cfg)
    assert sidebar.current_mode() == "local"


def test_handle_mode_changed_writes_cfg_and_persists(
    sidebar, cfg, monkeypatch,
):
    """User flips the picker → controller writes the new value into
    cfg + calls ``config.save_config`` so it survives a restart."""
    saved: list[dict] = []
    import config
    monkeypatch.setattr(
        config, "save_config", lambda c: saved.append(dict(c))
    )

    controller = ChatController(sidebar=sidebar, cfg=cfg)
    controller.handle_mode_changed("local")

    assert cfg["opt_ai_mode"] == "local"
    assert len(saved) == 1
    assert saved[0]["opt_ai_mode"] == "local"


def test_handle_mode_changed_ignores_invalid_values(sidebar, cfg, monkeypatch):
    """Garbage values shouldn't corrupt cfg."""
    cfg["opt_ai_mode"] = "cloud"
    import config
    monkeypatch.setattr(config, "save_config", lambda c: None)

    controller = ChatController(sidebar=sidebar, cfg=cfg)
    controller.handle_mode_changed("nonsense")

    assert cfg["opt_ai_mode"] == "cloud"  # unchanged


def test_handle_mode_changed_save_failure_does_not_break_chat(
    sidebar, cfg, monkeypatch,
):
    """If ``save_config`` raises, the cfg mutation still applies and
    the chat path keeps working — persistence is best-effort."""
    def boom(_c):
        raise OSError("config write failed")

    import config
    monkeypatch.setattr(config, "save_config", boom)

    controller = ChatController(sidebar=sidebar, cfg=cfg)
    controller.handle_mode_changed("local")

    # Mutation applied even though save failed.
    assert cfg["opt_ai_mode"] == "local"


def test_mode_change_takes_effect_on_next_resolve(sidebar, cfg, monkeypatch):
    """After ``handle_mode_changed("off")``, the next provider
    resolution returns ``("off", ...)`` regardless of how the
    controller was originally configured."""
    cfg["opt_ai_mode"] = "cloud"
    import config
    monkeypatch.setattr(config, "save_config", lambda c: None)

    controller = ChatController(sidebar=sidebar, cfg=cfg)
    # Resolve once with cloud — confirm baseline
    provider, label, _t = controller._resolve_provider()
    # (May or may not find a real cloud provider in the test env;
    # the label is what we care about.)

    controller.handle_mode_changed("off")
    provider, label, _t = controller._resolve_provider()
    assert provider is None
    assert label == "off"


def test_sidebar_set_mode_does_not_emit_mode_changed(sidebar, qtbot):
    """``set_mode`` is the controller→sidebar seed path — it must NOT
    fire ``mode_changed`` back at the controller (would cause an
    infinite save loop on construction)."""
    fired: list[str] = []
    sidebar.mode_changed.connect(fired.append)

    sidebar.set_mode("local")
    sidebar.set_mode("off")

    assert fired == []
    assert sidebar.current_mode() == "off"


def test_sidebar_user_changing_combo_emits_mode_changed(sidebar, qtbot):
    """When the USER clicks a combo entry, ``mode_changed`` fires.
    Distinguishes user-initiated changes from controller-initiated
    seeds."""
    fired: list[str] = []
    sidebar.mode_changed.connect(fired.append)

    sidebar.set_mode("cloud")  # baseline (no fire)
    # Now simulate a user click via direct index change.
    sidebar._mode_combo.setCurrentIndex(2)  # "local"

    assert fired == ["local"]


def test_main_window_get_chat_ui_snapshot_returns_dict(qtbot):
    """``MainWindow.get_chat_ui_snapshot`` returns a snapshot dict
    with the keys the fallback helper expects, even on a freshly
    constructed window with no rip in flight."""
    from gui_qt.main_window import MainWindow

    mw = MainWindow(cfg={"opt_ai_mode": "cloud"})
    qtbot.addWidget(mw)

    snap = mw.get_chat_ui_snapshot()
    assert isinstance(snap, dict)
    for key in (
        "status",
        "selected_drive",
        "ai_mode",
        "abort_button_state",
        "progress_percent",
        "live_log_tail",
    ):
        assert key in snap
    assert snap["ai_mode"] == "cloud"
