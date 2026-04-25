from types import SimpleNamespace
import unittest.mock

import config

import gui.main_window as main_window
from gui.main_window import JellyRipperGUI
from shared.ai_profile import AIProfile, load_ai_profile


def test_load_ai_profile_uses_defaults_for_missing_or_invalid_values():
    profile = load_ai_profile(
        {
            "opt_ai_profile": {
                "experience_level": "expert",
                "verbosity": "detailed",
                "response_style": "chatty",
                "guidance_level": "proactive",
                "provider_preference": "prefer_local",
                "privacy_preference": "locked_down",
                "custom_instructions": "  Keep answers concrete.  ",
            }
        }
    )

    assert profile == AIProfile(
        experience_level="intermediate",
        verbosity="detailed",
        response_style="direct",
        guidance_level="proactive",
        provider_preference="prefer_local",
        privacy_preference="standard",
        custom_instructions="Keep answers concrete.",
    )


def test_merge_config_copies_ai_profile_mapping():
    merged = config._merge_config(
        {
            "opt_ai_profile": {
                "verbosity": "detailed",
            }
        }
    )

    merged["opt_ai_profile"]["verbosity"] = "concise"

    assert config.DEFAULTS["opt_ai_profile"]["verbosity"] == "balanced"


def test_set_ai_profile_updates_cfg_and_engine():
    gui = object.__new__(JellyRipperGUI)
    gui.cfg = {}
    gui.engine = SimpleNamespace(cfg={})

    profile = gui._set_ai_profile(
        {
            "verbosity": "detailed",
            "response_style": "explanatory",
        },
        onboarded=True,
    )

    assert profile["verbosity"] == "detailed"
    assert gui.cfg["opt_ai_profile"]["response_style"] == "explanatory"
    assert gui.cfg["opt_ai_profile_onboarded"] is True
    assert gui.engine.cfg["opt_ai_profile"]["verbosity"] == "detailed"
    assert gui.engine.cfg["opt_ai_profile_onboarded"] is True


def test_ensure_ai_profile_onboarded_can_accept_defaults(monkeypatch):
    gui = object.__new__(JellyRipperGUI)
    gui.cfg = {"opt_ai_profile_onboarded": False}
    gui.engine = SimpleNamespace(cfg={})
    gui.controller = SimpleNamespace(log=unittest.mock.Mock())
    gui._run_on_main = lambda fn: fn()
    gui._persist_config = unittest.mock.Mock()

    monkeypatch.setattr(
        main_window.messagebox,
        "askyesnocancel",
        lambda *args, **kwargs: False,
    )

    result = gui._ensure_ai_profile_onboarded()

    assert result is True
    assert gui.cfg["opt_ai_profile_onboarded"] is True
    assert gui._persist_config.call_count == 1


def test_ensure_ai_profile_onboarded_can_route_to_settings(monkeypatch):
    gui = object.__new__(JellyRipperGUI)
    gui.cfg = {"opt_ai_profile_onboarded": False}
    gui.engine = SimpleNamespace(cfg={})
    gui.controller = SimpleNamespace(log=unittest.mock.Mock())
    gui._run_on_main = lambda fn: fn()
    gui.open_settings = unittest.mock.Mock()

    monkeypatch.setattr(
        main_window.messagebox,
        "askyesnocancel",
        lambda *args, **kwargs: True,
    )

    result = gui._ensure_ai_profile_onboarded()

    assert result is False
    gui.open_settings.assert_called_once_with(selected_tab="ai")
    assert gui.cfg.get("opt_ai_profile_onboarded", False) is False


def test_start_task_does_not_gate_rip_flow_on_ai_profile_onboarding(monkeypatch):
    gui = object.__new__(JellyRipperGUI)
    gui.cfg = {
        "opt_first_run_done": True,
        "opt_safe_mode": False,
        "temp_folder": r"C:\temp",
    }
    gui.engine = SimpleNamespace(
        validate_tools=lambda: (True, ""),
        _ffprobe_source="",
        cfg={},
        reset_abort=unittest.mock.Mock(),
    )
    gui.controller = SimpleNamespace(
        log=unittest.mock.Mock(),
        run_tv_disc=lambda: None,
        run_dump_all=lambda: None,
        run_organize=lambda: None,
        run_smart_rip=lambda: None,
        session_log=[],
        session_report=[],
        start_time=None,
        global_extra_counter=0,
    )
    gui.rip_thread = None
    gui._ensure_ai_profile_onboarded = unittest.mock.Mock(
        side_effect=AssertionError("rip flow should not consult AI onboarding")
    )
    gui.disable_buttons = unittest.mock.Mock()
    gui.set_progress = unittest.mock.Mock()

    started = {}

    class _Thread:
        def __init__(self, *, target, daemon):
            started["target"] = target
            started["daemon"] = daemon

        def start(self):
            started["started"] = True

    monkeypatch.setattr(main_window.threading, "Thread", _Thread)

    gui.start_task("i")

    gui.engine.reset_abort.assert_called_once_with()
    gui.disable_buttons.assert_called_once_with()
    gui.set_progress.assert_called_once_with(0)
    assert started == {"target": started["target"], "daemon": True, "started": True}
