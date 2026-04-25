"""Import smoke tests to guard module boundary regressions."""
import ast
import inspect
import json
import os
import sys
import threading
import textwrap
import unittest.mock

import pytest


class _FakeTkBase:
    pass


def test_imports():
    import config  # noqa: F401
    import engine.ripper_engine  # noqa: F401
    import controller.controller  # noqa: F401


def test_gui_import():
    """GUI import must not require a live display.

    main_window.py imports tkinter at module level; patch Tk so this test
    passes on headless CI without a display server.
    """
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        import gui.main_window  # noqa: F401


def test_shared_python_sources_do_not_start_with_utf8_bom():
    for path in ("gui/main_window.py", "tests/test_parsing.py"):
        with open(path, "rb") as handle:
            data = handle.read(3)

        assert data != b"\xef\xbb\xbf", path


def test_gui_import_exposes_make_rip_folder_name():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        import gui.main_window as main_window

    assert callable(main_window.make_rip_folder_name)


def test_compute_initial_window_bounds_matches_screen_aware_defaults():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import compute_initial_window_bounds

    assert compute_initial_window_bounds(1920, 1080) == (1260, 900, 1040, 760)
    assert compute_initial_window_bounds(1100, 800) == (1024, 760, 1024, 760)


def test_get_bottom_safe_margin_px_uses_main_default_and_allows_override():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import get_bottom_safe_margin_px

    assert get_bottom_safe_margin_px({}) == 72
    assert get_bottom_safe_margin_px({"opt_bottom_safe_margin_px": 32}) == 32


def test_run_on_main_executes_directly_on_main_thread():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI

    gui = object.__new__(JellyRipperGUI)

    result = gui._run_on_main(lambda: "ok")

    assert result == "ok"


def test_ask_duplicate_resolution_uses_modal_fallback_on_main_thread():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI

    gui = object.__new__(JellyRipperGUI)
    gui._ask_duplicate_resolution_modal = unittest.mock.Mock(return_value="retry")

    result = gui.ask_duplicate_resolution("dup?")

    assert result == "retry"
    gui._ask_duplicate_resolution_modal.assert_called_once()


def test_ask_input_uses_modal_popup_on_main_thread():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI

    gui = object.__new__(JellyRipperGUI)
    gui._input_lock = threading.Lock()
    gui._ask_input_modal = unittest.mock.Mock(return_value="Movie Name")
    gui.append_log = unittest.mock.Mock()

    result = gui.ask_input("Title", "Exact title:", default_value="default")

    assert result == "Movie Name"
    gui._ask_input_modal.assert_called_once_with(
        "Title",
        "Exact title:",
        default_value="default",
    )
    gui.append_log.assert_called_once()


def test_ask_yesno_uses_modal_popup_on_main_thread():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI

    gui = object.__new__(JellyRipperGUI)
    gui._ask_yesno_modal = unittest.mock.Mock(return_value=True)

    result = gui.ask_yesno("Proceed?")

    assert result is True
    gui._ask_yesno_modal.assert_called_once_with("Proceed?")


def test_ask_dump_setup_dispatches_builder_on_main_thread(monkeypatch):
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        import gui.session_setup_dialog as session_setup_dialog
        from gui.main_window import JellyRipperGUI

    sentinel = object()
    monkeypatch.setattr(
        session_setup_dialog,
        "build_dump_setup_dialog",
        lambda *_args, **_kwargs: sentinel,
    )

    gui = object.__new__(JellyRipperGUI)
    gui.after = lambda _delay, callback: callback()
    gui.engine = unittest.mock.Mock()
    gui.engine.abort_event = threading.Event()

    result = gui.ask_dump_setup(
        default_multi_disc=True,
        default_disc_name="Disc 1",
        default_disc_count="2",
        default_custom_disc_names="Disc 1, Disc 2",
        default_batch_title="Batch",
    )

    assert result is sentinel


def test_ask_space_override_uses_modal_fallback_on_main_thread():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI

    gui = object.__new__(JellyRipperGUI)
    gui._ask_space_override_modal = unittest.mock.Mock(return_value=True)

    result = gui.ask_space_override(10.0, 5.0)

    assert result is True
    gui._ask_space_override_modal.assert_called_once_with(10.0, 5.0)


def test_resolve_ffprobe_tool_uses_cfg_and_path_toggle(monkeypatch):
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        import gui.main_window as main_window
        from gui.main_window import JellyRipperGUI

    seen = {}
    resolved = object()

    def _fake_resolve(path, *, allow_path_lookup=False):
        seen["path"] = path
        seen["allow_path_lookup"] = allow_path_lookup
        return resolved

    gui = object.__new__(JellyRipperGUI)
    gui.cfg = {
        "ffprobe_path": r"C:\ffmpeg\bin",
        "opt_allow_path_tool_resolution": True,
    }

    monkeypatch.setattr(main_window, "resolve_ffprobe", _fake_resolve)
    monkeypatch.setattr(main_window.sys, "platform", "win32")

    assert gui._resolve_ffprobe_tool() is resolved
    assert seen["path"] == os.path.normpath(r"C:\ffmpeg\bin")
    assert seen["allow_path_lookup"] is True


def test_confirm_input_preserves_empty_string():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI

    class _Var:
        def get(self):
            return "   "

    gui = object.__new__(JellyRipperGUI)
    gui._input_active = True
    gui.input_var = _Var()
    gui._input_event = threading.Event()
    gui._input_result = object()

    gui._confirm_input()

    assert gui._input_result == ""
    assert gui._input_event.is_set()


def test_on_close_destroys_window_without_force_exit(monkeypatch):
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        import gui.main_window as main_window
        from gui.main_window import JellyRipperGUI

    gui = object.__new__(JellyRipperGUI)
    gui.engine = unittest.mock.Mock()
    gui.rip_thread = None
    gui.destroy = unittest.mock.Mock()

    monkeypatch.setattr(
        main_window.messagebox,
        "askokcancel",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        main_window.os,
        "_exit",
        unittest.mock.Mock(side_effect=AssertionError("os._exit should not run")),
    )

    gui.on_close()

    gui.engine.abort.assert_called_once_with()
    gui.destroy.assert_called_once_with()
    main_window.os._exit.assert_not_called()


def test_open_settings_safe_clears_settings_state_on_failure():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI

    gui = object.__new__(JellyRipperGUI)
    gui._settings_window = object()
    gui._settings_notebook = object()
    gui._settings_tabs = {"ai": object()}
    gui.controller = unittest.mock.Mock()
    gui.show_error = unittest.mock.Mock()

    def _boom(*, selected_tab=None):
        raise RuntimeError(f"broken: {selected_tab}")

    gui.open_settings = _boom

    gui._open_settings_safe(selected_tab="ai")

    assert gui._settings_window is None
    assert gui._settings_notebook is None
    assert gui._settings_tabs == {}
    gui.show_error.assert_called_once()
    gui.controller.log.assert_called_once()


def test_open_settings_uses_runtime_display_name_in_window_title():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI

    source = textwrap.dedent(inspect.getsource(JellyRipperGUI.open_settings))
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "win"
            and node.func.attr == "title"
        ):
            continue

        assert len(node.args) == 1
        title_arg = node.args[0]
        assert isinstance(title_arg, ast.JoinedStr)
        assert any(
            isinstance(value, ast.FormattedValue)
            and isinstance(value.value, ast.Name)
            and value.value.id == "APP_DISPLAY_NAME"
            for value in title_arg.values
        )
        assert any(
            isinstance(value, ast.Constant)
            and value.value == " Settings"
            for value in title_arg.values
        )
        return

    raise AssertionError("win.title(...) call not found in open_settings")


def test_apply_startup_context_uses_runtime_display_name_in_recovery_message():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI

    source = inspect.getsource(JellyRipperGUI._apply_startup_context)

    assert "APP_DISPLAY_NAME" in source
    assert "opened with safe defaults so you can fix this in Settings." in source
    assert "JellyRip opened with safe defaults" not in source


def test_ai_onboarding_prompt_uses_runtime_display_name():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI

    source = inspect.getsource(JellyRipperGUI._ensure_ai_profile_onboarded)

    assert "APP_DISPLAY_NAME" in source
    assert "Set up the JellyRip assistant profile before your first run?" not in source


def test_shared_workflow_copy_uses_runtime_name_constants():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI

    output_plan_source = inspect.getsource(JellyRipperGUI.show_output_plan_step)
    ffmpeg_warning_source = inspect.getsource(
        JellyRipperGUI._ffmpeg_version_ok
    )
    browse_path_source = inspect.getsource(JellyRipperGUI._browse_settings_path)

    assert "APP_DISPLAY_NAME" in output_plan_source
    assert "This is exactly what JellyRip will create." not in output_plan_source

    assert "APP_DISPLAY_NAME" in ffmpeg_warning_source
    assert "run JellyRip transcodes" not in ffmpeg_warning_source
    assert "by JellyRip are not available" not in ffmpeg_warning_source

    assert "APP_EXE_BASENAME" in browse_path_source
    assert '"jellyrip.log"' not in browse_path_source


def test_open_transcode_queue_builder_uses_shared_profile_summary():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI

    source = inspect.getsource(JellyRipperGUI._open_transcode_queue_builder)

    assert "summarize_profile(" in source
    assert "describe_profile(" not in source


def test_notify_complete_uses_runtime_aumid():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI

    source = inspect.getsource(JellyRipperGUI._notify_complete)

    assert "APP_AUMID" in source
    assert "CreateToastNotifier($args[2])" in source
    assert "JellyRip.App.1" not in source


def test_disable_buttons_keeps_transcode_prep_available():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI

    class _FakeButton:
        def __init__(self):
            self.state = None

        def config(self, **kwargs):
            self.state = kwargs.get("state")

    gui = object.__new__(JellyRipperGUI)
    gui.mode_buttons = {
        "t": _FakeButton(),
        "m": _FakeButton(),
        "d": _FakeButton(),
        "i": _FakeButton(),
        "scan": _FakeButton(),
    }
    gui.settings_btn = _FakeButton()
    gui.update_btn = _FakeButton()
    gui.abort_btn = _FakeButton()

    gui.disable_buttons()

    assert gui.mode_buttons["t"].state == "disabled"
    assert gui.mode_buttons["m"].state == "disabled"
    assert gui.mode_buttons["d"].state == "disabled"
    assert gui.mode_buttons["i"].state == "disabled"
    assert gui.mode_buttons["scan"].state == "normal"
    assert gui.settings_btn.state == "disabled"
    assert gui.update_btn.state == "disabled"
    assert gui.abort_btn.state == "normal"


def test_enable_buttons_restores_idle_abort_disabled():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI

    class _FakeButton:
        def __init__(self):
            self.state = None
            self.text = None

        def config(self, **kwargs):
            if "state" in kwargs:
                self.state = kwargs["state"]
            if "text" in kwargs:
                self.text = kwargs["text"]

    gui = object.__new__(JellyRipperGUI)
    gui._task_active = True
    gui.mode_buttons = {
        "t": _FakeButton(),
        "m": _FakeButton(),
        "d": _FakeButton(),
        "i": _FakeButton(),
        "scan": _FakeButton(),
    }
    gui.settings_btn = _FakeButton()
    gui.update_btn = _FakeButton()
    gui.abort_btn = _FakeButton()

    gui.enable_buttons()

    assert gui._task_active is False
    assert all(button.state == "normal" for button in gui.mode_buttons.values())
    assert gui.settings_btn.state == "normal"
    assert gui.update_btn.state == "normal"
    assert gui.abort_btn.text == "ABORT SESSION"
    assert gui.abort_btn.state == "disabled"


def test_build_interface_v2_creates_abort_button_disabled():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI

    source = textwrap.dedent(
        inspect.getsource(JellyRipperGUI._build_interface_v2)
    )
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
            and target.attr == "abort_btn"
            for target in node.targets
        ):
            continue

        assert isinstance(node.value, ast.Call)
        state_kw = next(
            (kw.value for kw in node.value.keywords if kw.arg == "state"),
            None,
        )
        assert isinstance(state_kw, ast.Constant)
        assert state_kw.value == "disabled"
        return

    raise AssertionError(
        "abort_btn constructor not found in _build_interface_v2"
    )


def test_build_interface_v2_uses_equal_width_toolbar_grid():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI

    source = textwrap.dedent(
        inspect.getsource(JellyRipperGUI._build_interface_v2)
    )
    tree = ast.parse(source)

    found_pack = False
    found_uniform = False
    found_grid_buttons = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "toolbar_actions"
                and node.func.attr == "pack"
            ):
                keywords = {kw.arg: kw.value for kw in node.keywords}
                assert isinstance(keywords.get("side"), ast.Constant)
                assert keywords["side"].value == "right"
                assert isinstance(keywords.get("fill"), ast.Constant)
                assert keywords["fill"].value == "x"
                assert isinstance(keywords.get("expand"), ast.Constant)
                assert keywords["expand"].value is True
                found_pack = True
            elif (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "toolbar_actions"
                and node.func.attr == "grid_columnconfigure"
            ):
                uniform_kw = next(
                    (kw.value for kw in node.keywords if kw.arg == "uniform"),
                    None,
                )
                assert isinstance(uniform_kw, ast.Constant)
                assert uniform_kw.value == "toolbar"
                found_uniform = True
            elif (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Attribute)
                and isinstance(node.func.value.value, ast.Name)
                and node.func.value.value.id == "self"
                and node.func.attr == "grid"
            ):
                found_grid_buttons.add(node.func.value.attr)

    assert found_pack
    assert found_uniform
    for button_name in [
        "ai_chat_toggle_btn",
        "settings_btn",
        "update_btn",
        "log_btn",
        "copy_log_btn",
        "browse_btn",
    ]:
        assert button_name in found_grid_buttons


def test_build_interface_v2_keeps_main_shared_layout_metrics():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI

    source = textwrap.dedent(
        inspect.getsource(JellyRipperGUI._build_interface_v2)
    )

    assert 'disabledforeground=colors["text"]' in source
    assert (
        'self.content_frame.pack(fill="both", expand=True, padx=36, pady=(10, 0))'
        in source
    )
    assert 'self.status_brand_inner.pack(expand=True, pady=0)' in source
    assert 'font=("Segoe UI", 24, "bold")' in source
    assert 'font=("Segoe UI", 22, "italic")' in source
    assert (
        '_input_bar_pack_kwargs = {"fill": "x", "padx": 36, "pady": (10, 0)}'
        in source
    )
    assert (
        'self._session_controls_frame.pack(fill="x", padx=36, pady=(10, 0))'
        in source
    )
    assert 'font=("Segoe UI", 18, "bold")' in source
    assert '#ffd0d6' not in source


def test_init_keeps_main_log_panel_min_width_default():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI

    source = inspect.getsource(JellyRipperGUI.__init__)

    assert 'self._log_panel_min_width = 520' in source
    assert 'self._log_panel_min_width = 600' not in source


def test_show_input_bar_uses_main_fallback_pack_metrics():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI

    source = inspect.getsource(JellyRipperGUI._show_input_bar)

    assert '{"fill": "x", "padx": 36, "pady": (10, 0)}' in source
    assert '{"fill": "x", "padx": 20, "pady": 4}' not in source


def test_pick_movie_mode_cancel_stops_before_scan(monkeypatch):
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        import gui.main_window as main_window
        from gui.main_window import JellyRipperGUI

    captured = {}

    def smart_rip():
        return None

    def manual_rip():
        return None

    def fake_askyesnocancel(title, message, parent=None):
        captured["title"] = title
        captured["message"] = message
        captured["parent"] = parent
        return None

    gui = object.__new__(JellyRipperGUI)
    gui.controller = unittest.mock.Mock()
    gui.controller.run_smart_rip = smart_rip
    gui.controller.run_movie_disc = manual_rip

    monkeypatch.setattr(main_window.messagebox, "askyesnocancel", fake_askyesnocancel)

    result = gui._pick_movie_mode()

    assert result is None
    assert captured["title"] == "Movie Mode"
    assert "Cancel = stop" in captured["message"]
    assert captured["parent"] is gui
    gui.controller.log.assert_called_once_with(
        "Movie mode prompt cancelled before scan."
    )


def test_start_task_notifications_use_runtime_display_name():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI

    source = textwrap.dedent(inspect.getsource(JellyRipperGUI.start_task))
    tree = ast.parse(source)
    notify_title_args = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "_notify_complete"
        ):
            continue
        if node.args:
            notify_title_args.append(node.args[0])

    assert notify_title_args, "_notify_complete calls not found in start_task"

    for title_arg in notify_title_args:
        if isinstance(title_arg, ast.Name):
            assert title_arg.id == "APP_DISPLAY_NAME"
            continue
        assert isinstance(title_arg, ast.JoinedStr)
        assert any(
            isinstance(value, ast.FormattedValue)
            and isinstance(value.value, ast.Name)
            and value.value.id == "APP_DISPLAY_NAME"
            for value in title_arg.values
        )
        assert not any(
            isinstance(value, ast.Constant)
            and isinstance(value.value, str)
            and ("JellyRip" in value.value or "Raw Jelly Ripper" in value.value)
            for value in title_arg.values
        )


def test_pick_movie_mode_yes_uses_smart_rip(monkeypatch):
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        import gui.main_window as main_window
        from gui.main_window import JellyRipperGUI

    def smart_rip():
        return None

    def manual_rip():
        return None

    gui = object.__new__(JellyRipperGUI)
    gui.controller = unittest.mock.Mock()
    gui.controller.run_smart_rip = smart_rip
    gui.controller.run_movie_disc = manual_rip

    monkeypatch.setattr(
        main_window.messagebox,
        "askyesnocancel",
        lambda *args, **kwargs: True,
    )

    result = gui._pick_movie_mode()

    assert result is smart_rip


def test_pick_movie_mode_no_uses_manual_rip(monkeypatch):
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        import gui.main_window as main_window
        from gui.main_window import JellyRipperGUI

    def smart_rip():
        return None

    def manual_rip():
        return None

    gui = object.__new__(JellyRipperGUI)
    gui.controller = unittest.mock.Mock()
    gui.controller.run_smart_rip = smart_rip
    gui.controller.run_movie_disc = manual_rip

    monkeypatch.setattr(
        main_window.messagebox,
        "askyesnocancel",
        lambda *args, **kwargs: False,
    )

    result = gui._pick_movie_mode()

    assert result is manual_rip


def test_confirm_profile_hdr_metadata_save_respects_user_choice(monkeypatch):
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        import gui.main_window as main_window
        from gui.main_window import JellyRipperGUI
    from transcode.profiles import normalize_profile_data

    profile_data = normalize_profile_data({})
    profile_data["video"]["extra_video_params"] = "hdr-opt=1:colorprim=bt2020"

    gui = object.__new__(JellyRipperGUI)
    prompt = unittest.mock.Mock(return_value=False)
    monkeypatch.setattr(main_window, "ask_yes_no", prompt)

    result = gui._confirm_profile_hdr_metadata_save(profile_data, parent=None)

    assert result is False
    prompt.assert_called_once()


def test_open_custom_transcode_editor_uses_shared_hdr_confirmation_helper():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI

    source = textwrap.dedent(
        inspect.getsource(JellyRipperGUI._open_custom_transcode_editor)
    )
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and node.func.attr == "_confirm_profile_hdr_metadata_save"
        ):
            continue

        assert len(node.args) == 2
        assert isinstance(node.args[0], ast.Name)
        assert node.args[0].id == "profile_data"
        assert isinstance(node.args[1], ast.Name)
        assert node.args[1].id == "name_dlg"
        return

    raise AssertionError(
        "_open_custom_transcode_editor no longer uses "
        "_confirm_profile_hdr_metadata_save"
    )


def test_shared_workflow_strings_do_not_contain_mojibake():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI

    method_names = [
        "__init__",
        "_ask_folder_scan_options",
        "_show_folder_scan_results",
        "_open_custom_transcode_editor",
        "show_disc_tree",
        "ask_movie_setup",
        "ask_tv_setup",
        "show_content_mapping_step",
        "show_extras_classification_step",
        "start_task",
        "ask_space_override",
        "_ask_space_override_modal",
        "copy_log_to_clipboard",
        "open_settings",
    ]
    source = "\n".join(
        inspect.getsource(getattr(JellyRipperGUI, name))
        for name in method_names
    )

    expected_strings = [
        "LAYER 3 \u2014 GUI",
        "message_queue \u2192 process_queue()",
        "MKV Scanner \u2014 Sort Options",
        "MKV Scanner Results \u2014 ",
        "Source: {analysis.get('name', '')}   \u00b7   {' | '.join(info_parts)}",
        "CRF (0\u201351, lower = better):",
        "B-frames (0\u201316):",
        "Reference frames (1\u201316):",
        'Parse "2 (stereo)" \u2192 2',
        "Burn subtitles in (hard sub \u2014 baked permanently into the picture)",
        "Skip if source is already the target codec (avoids HEVC \u2192 HEVC re-encode)",
        "\U0001f4ac Subtitle: {lang}",
        "Safe to call from a worker thread \u2014 dispatches the Toplevel to the",
        "Step 3: Content mapping \u2014 select titles to rip.",
        "Step 4: Extras classification \u2014 assign Jellyfin categories.",
        "Log is empty \u2014 nothing to copy.",
        "\u26a0  NOT ENOUGH DISK SPACE",
        "(optional \u2014 download the CLI from handbrake.fr if needed).",
        "don't start the rip \u2014 just bail out cleanly.",
    ]
    bad_fragments = [
        "\u00e2\u20ac\u201d",
        "\u00e2\u2020\u2019",
        "\u00c2\u00b7",
        "0\u00e2\u20ac\u201c51",
        "0\u00e2\u20ac\u201c16",
        "1\u00e2\u20ac\u201c16",
        "\u00e2\u0161\u00a0",
        "\u00f0\u0178\u2019\u00ac",
        "\u00e2\u20ac\u009d\u20ac",
    ]

    for text in expected_strings:
        assert text in source
    for fragment in bad_fragments:
        assert fragment not in source

def test_confirm_discard_dirty_expert_changes_respects_user_choice(monkeypatch):
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        import gui.main_window as main_window
        from gui.main_window import JellyRipperGUI
    from transcode.profiles import normalize_profile_data

    class _Var:
        def __init__(self, value):
            self._value = value

        def get(self):
            return self._value

        def set(self, value):
            self._value = value

    profile_data = normalize_profile_data({})
    expert_vars = {"video": {"crf": _Var("27")}}

    gui = object.__new__(JellyRipperGUI)
    prompt = unittest.mock.Mock(return_value=False)
    monkeypatch.setattr(main_window, "ask_yes_no", prompt)

    result = gui._confirm_discard_dirty_expert_changes(
        profile_data,
        expert_vars,
        "Discard unsaved Expert profile edits and close Settings?",
        parent=None,
    )

    assert result is False
    prompt.assert_called_once()


def test_save_expert_profile_data_updates_loader(tmp_path):
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI
    from transcode.profiles import ProfileLoader, normalize_profile_data

    loader = ProfileLoader(str(tmp_path / "profiles.json"))
    gui = object.__new__(JellyRipperGUI)
    gui._get_transcode_profile_loader = unittest.mock.Mock(return_value=loader)

    profile_data = normalize_profile_data({})
    profile_data["video"]["crf"] = 19

    saved_name = gui._save_expert_profile_data(
        "Balanced (Recommended)",
        profile_data,
    )

    reloaded = ProfileLoader(str(tmp_path / "profiles.json"))

    assert saved_name == "Balanced (Recommended)"
    assert reloaded.get_profile(saved_name).to_dict()["video"]["crf"] == 19


def test_persist_settings_and_profile_saves_profile_before_config(tmp_path, monkeypatch):
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        import gui.main_window as main_window
        from gui.main_window import JellyRipperGUI
    from transcode.profiles import ProfileLoader

    gui = object.__new__(JellyRipperGUI)
    calls = []
    gui._get_transcode_profile_loader = unittest.mock.Mock(
        return_value=ProfileLoader(str(tmp_path / "profiles.json"))
    )

    gui._save_expert_profile_data = unittest.mock.Mock(
        side_effect=lambda name, data: calls.append(("profile", name, data)) or name
    )
    monkeypatch.setattr(
        main_window,
        "save_config",
        lambda cfg: calls.append(("config", dict(cfg))),
    )

    saved_name = gui._persist_settings_and_profile(
        {"temp_folder": "C:/Temp"},
        expert_profile_name="Balanced (Recommended)",
        expert_profile_data={"video": {"crf": 20}},
    )

    assert saved_name == "Balanced (Recommended)"
    assert calls[0][0] == "profile"
    assert calls[1][0] == "config"


def test_persist_settings_and_profile_rolls_back_profile_on_config_failure(
    tmp_path,
    monkeypatch,
):
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        import gui.main_window as main_window
        from gui.main_window import JellyRipperGUI
    from transcode.profiles import ProfileLoader, normalize_profile_data

    loader = ProfileLoader(str(tmp_path / "profiles.json"))
    gui = object.__new__(JellyRipperGUI)
    gui.controller = unittest.mock.Mock()
    gui._get_transcode_profile_loader = unittest.mock.Mock(return_value=loader)

    updated_profile = normalize_profile_data({})
    updated_profile["video"]["crf"] = 19

    monkeypatch.setattr(
        main_window,
        "save_config",
        unittest.mock.Mock(side_effect=RuntimeError("config failed")),
    )

    with pytest.raises(RuntimeError, match="config failed"):
        gui._persist_settings_and_profile(
            {"temp_folder": "C:/Temp"},
            expert_profile_name="Balanced (Recommended)",
            expert_profile_data=updated_profile,
        )

    reloaded = ProfileLoader(str(tmp_path / "profiles.json"))
    assert reloaded.get_profile("Balanced (Recommended)").to_dict()["video"]["crf"] == 22


def test_create_expert_profile_adds_named_profile(tmp_path):
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI
    from transcode.profiles import ProfileLoader

    loader = ProfileLoader(str(tmp_path / "profiles.json"))
    gui = object.__new__(JellyRipperGUI)
    gui._get_transcode_profile_loader = unittest.mock.Mock(return_value=loader)

    created_name = gui._create_expert_profile("Cinema")

    reloaded = ProfileLoader(str(tmp_path / "profiles.json"))

    assert created_name == "Cinema"
    assert "Cinema" in reloaded.profiles


def test_duplicate_expert_profile_copies_profile_data(tmp_path):
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI
    from transcode.profiles import ProfileLoader, normalize_profile_data

    loader = ProfileLoader(str(tmp_path / "profiles.json"))
    source_profile = normalize_profile_data({})
    source_profile["video"]["crf"] = 17
    loader.add_profile("Cinema", source_profile)

    gui = object.__new__(JellyRipperGUI)
    gui._get_transcode_profile_loader = unittest.mock.Mock(return_value=loader)

    duplicated_name = gui._duplicate_expert_profile("Cinema", "Cinema Copy")

    reloaded = ProfileLoader(str(tmp_path / "profiles.json"))

    assert duplicated_name == "Cinema Copy"
    assert reloaded.get_profile("Cinema Copy").to_dict()["video"]["crf"] == 17


def test_delete_expert_profile_removes_profile_and_returns_next_name(tmp_path):
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI
    from transcode.profiles import ProfileLoader, normalize_profile_data

    loader = ProfileLoader(str(tmp_path / "profiles.json"))
    loader.add_profile("Cinema", normalize_profile_data({}))
    loader.set_default("Cinema")

    gui = object.__new__(JellyRipperGUI)
    gui._get_transcode_profile_loader = unittest.mock.Mock(return_value=loader)

    next_name = gui._delete_expert_profile("Cinema")

    reloaded = ProfileLoader(str(tmp_path / "profiles.json"))

    assert next_name == "Balanced (Recommended)"
    assert "Cinema" not in reloaded.profiles
    assert reloaded.default == "Balanced (Recommended)"


def test_set_default_expert_profile_updates_loader(tmp_path):
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI
    from transcode.profiles import ProfileLoader, normalize_profile_data

    loader = ProfileLoader(str(tmp_path / "profiles.json"))
    cinema_profile = normalize_profile_data({})
    loader.add_profile("Cinema", cinema_profile)

    gui = object.__new__(JellyRipperGUI)
    gui._get_transcode_profile_loader = unittest.mock.Mock(return_value=loader)

    default_name = gui._set_default_expert_profile("Cinema")

    reloaded = ProfileLoader(str(tmp_path / "profiles.json"))

    assert default_name == "Cinema"
    assert reloaded.default == "Cinema"


def test_show_output_plan_step_passes_review_customization(monkeypatch):
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI
        import gui.setup_wizard as setup_wizard

    captured = {}
    returned = True

    def fake_show_output_plan(
        parent,
        base_folder,
        main_label,
        extras_map,
        detail_lines=None,
        header_text="",
        subtitle_text="",
        confirm_text="",
        *,
        suggested_base_folder=None,
    ):
        captured["parent"] = parent
        captured["base_folder"] = base_folder
        captured["main_label"] = main_label
        captured["extras_map"] = extras_map
        captured["detail_lines"] = detail_lines
        captured["header_text"] = header_text
        captured["subtitle_text"] = subtitle_text
        captured["confirm_text"] = confirm_text
        captured["suggested_base_folder"] = suggested_base_folder
        return returned

    monkeypatch.setattr(setup_wizard, "show_output_plan", fake_show_output_plan)

    gui = object.__new__(JellyRipperGUI)
    gui.after = lambda _delay, callback: callback()
    gui.ask_directory = unittest.mock.Mock(return_value=r"C:\picked")
    gui.engine = unittest.mock.Mock()
    gui.engine.abort_event = threading.Event()

    result = gui.show_output_plan_step(
        r"C:\base",
        "Movie.mkv",
        {"Extras": ["clip.mkv"]},
        detail_lines=["Show: Example"],
        header_text="Review Output Plan",
        subtitle_text="Check the destination before ripping.",
        confirm_text="Go",
        suggested_folder=r"C:\suggested",
    )

    assert result is returned
    assert captured["parent"] is gui
    assert captured["base_folder"] == r"C:\base"
    assert captured["main_label"] == "Movie.mkv"
    assert captured["extras_map"] == {"Extras": ["clip.mkv"]}
    assert captured["detail_lines"] == ["Show: Example"]
    assert captured["header_text"] == "Review Output Plan"
    assert captured["subtitle_text"] == "Check the destination before ripping."
    assert captured["confirm_text"] == "Go"
    assert captured["suggested_base_folder"] == r"C:\suggested"


def test_build_content_selection_keeps_extras_only_selection_as_extras():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        import gui.setup_wizard as setup_wizard

    from utils.classifier import ClassifiedTitle

    classified = [
        ClassifiedTitle(
            {
                "id": 0,
                "name": "Main Feature",
                "duration_seconds": 7200,
                "size_bytes": 4_000_000_000,
            },
            score=1.0,
            label="MAIN",
            confidence=0.95,
            recommended=True,
        ),
        ClassifiedTitle(
            {
                "id": 1,
                "name": "Bonus Feature",
                "duration_seconds": 900,
                "size_bytes": 500_000_000,
            },
            score=0.3,
            label="EXTRA",
            confidence=0.45,
        ),
    ]

    selection = setup_wizard._build_content_selection(classified, {1})

    assert selection.main_title_ids == []
    assert selection.extra_title_ids == [1]
    assert selection.skip_title_ids == [0]


def test_debug_ui_event_logs_human_readable_line_when_enabled():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI

    gui = object.__new__(JellyRipperGUI)
    gui.cfg = {"opt_debug_state": True, "opt_debug_state_json": False}
    gui.controller = unittest.mock.Mock()
    gui._ai_sidebar_visible = True
    gui._ai_chat_busy = False
    gui._task_active = False
    gui.focus_get = lambda: None

    gui._debug_ui_event("mode_button_invoke", mode="m", source="tile")

    message = gui.controller.log.call_args[0][0]
    assert message.startswith("DEBUG UI mode_button_invoke:")
    assert "mode='m'" in message
    assert "source='tile'" in message


def test_debug_ui_event_logs_json_when_enabled():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI

    gui = object.__new__(JellyRipperGUI)
    gui.cfg = {"opt_debug_state": True, "opt_debug_state_json": True}
    gui.controller = unittest.mock.Mock()
    gui._ai_sidebar_visible = False
    gui._ai_chat_busy = True
    gui._task_active = False
    gui.focus_get = lambda: None

    gui._debug_ui_event("ai_chat_send_invoke", source="button", prompt_len=12)

    message = gui.controller.log.call_args[0][0]
    assert message.startswith("DEBUG UI ")
    payload = json.loads(message[len("DEBUG UI "):])
    assert payload["event"] == "ai_chat_send_invoke"
    assert payload["source"] == "button"
    assert payload["prompt_len"] == 12
    assert payload["ai_chat_busy"] is True


def test_start_task_from_ui_logs_and_calls_start_task():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI

    gui = object.__new__(JellyRipperGUI)
    gui.cfg = {"opt_debug_state": True, "opt_debug_state_json": False}
    gui.controller = unittest.mock.Mock()
    gui._ai_sidebar_visible = False
    gui._ai_chat_busy = False
    gui._task_active = False
    gui.focus_get = lambda: None
    gui.start_task = unittest.mock.Mock()

    gui._start_task_from_ui("m", source="tile")

    gui.start_task.assert_called_once_with("m")
    gui.controller.log.assert_called_once()


def test_start_task_logs_resolved_tool_paths_before_launch():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        import gui.main_window as main_window
        from gui.main_window import JellyRipperGUI

    class _FakeThread:
        def __init__(self, *, target=None, daemon=None):
            self.target = target
            self.daemon = daemon
            self.started = False

        def start(self):
            self.started = True

        def is_alive(self):
            return self.started

    class _FakeEngine:
        def __init__(self):
            self.cfg = {}
            self.abort_event = threading.Event()
            self._makemkvcon_source = "known location"
            self._resolved_makemkvcon = r"C:\Program Files\MakeMKV\makemkvcon64.exe"
            self._ffprobe_source = "configured executable"
            self._resolved_ffprobe = r"C:\ffmpeg\bin\ffprobe.exe"

        def validate_tools(self):
            return True, ""

        def reset_abort(self):
            return None

    gui = object.__new__(JellyRipperGUI)
    gui.cfg = {
        "opt_debug_state": False,
        "opt_debug_state_json": False,
        "opt_safe_mode": False,
        "opt_first_run_done": True,
        "temp_folder": r"C:\temp",
    }
    gui.engine = _FakeEngine()
    gui.controller = unittest.mock.Mock()
    gui.controller.run_tv_disc = unittest.mock.Mock()
    gui.controller.run_smart_rip = unittest.mock.Mock()
    gui.controller.run_movie_disc = unittest.mock.Mock()
    gui.controller.run_dump_all = unittest.mock.Mock()
    gui.controller.run_organize = unittest.mock.Mock()
    gui.rip_thread = None
    gui.disable_buttons = unittest.mock.Mock()
    gui.set_progress = unittest.mock.Mock()
    gui._debug_ui_event = lambda *_a, **_k: None

    with unittest.mock.patch.object(main_window.threading, "Thread", new=_FakeThread):
        gui.start_task("sr")

    logged_messages = [call.args[0] for call in gui.controller.log.call_args_list]
    assert (
        r"MakeMKV resolved via known location: C:\Program Files\MakeMKV\makemkvcon64.exe"
        in logged_messages
    )
    assert (
        r"ffprobe resolved via configured executable: C:\ffmpeg\bin\ffprobe.exe"
        in logged_messages
    )


def test_request_abort_sets_status_and_calls_engine_abort():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI

    class _FakeButton:
        def __init__(self):
            self.text = None
            self.state = None

        def config(self, **kwargs):
            if "text" in kwargs:
                self.text = kwargs["text"]
            if "state" in kwargs:
                self.state = kwargs["state"]

    class _FakeAbortEvent:
        def is_set(self):
            return False

    class _FakeEngine:
        def __init__(self):
            self.abort_calls = 0
            self.abort_event = _FakeAbortEvent()

        def abort(self):
            self.abort_calls += 1

    gui = object.__new__(JellyRipperGUI)
    gui.controller = unittest.mock.Mock()
    gui.engine = _FakeEngine()
    gui.abort_btn = _FakeButton()
    gui.set_status = unittest.mock.Mock()
    gui._task_active = True
    gui._input_active = False
    gui._abort_ui_recovery_job = None
    gui.grab_current = lambda: None

    gui.request_abort()

    assert gui.engine.abort_calls == 1
    gui.controller.log.assert_any_call("ABORT REQUESTED BY USER")
    gui.set_status.assert_called_once_with("Aborting...")
    assert gui.abort_btn.text == "ABORTING..."
    assert gui.abort_btn.state == "disabled"
