"""Import smoke tests to guard module boundary regressions."""
import threading
import unittest.mock


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


def test_gui_import_exposes_make_rip_folder_name():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        import gui.main_window as main_window

    assert callable(main_window.make_rip_folder_name)


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


def test_ask_space_override_uses_modal_fallback_on_main_thread():
    with unittest.mock.patch("tkinter.Tk", new=_FakeTkBase):
        from gui.main_window import JellyRipperGUI

    gui = object.__new__(JellyRipperGUI)
    gui._ask_space_override_modal = unittest.mock.Mock(return_value=True)

    result = gui.ask_space_override(10.0, 5.0)

    assert result is True
    gui._ask_space_override_modal.assert_called_once_with(10.0, 5.0)


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


def test_request_abort_releases_active_prompt_and_sets_abort():
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

    class _FakeEngine:
        def __init__(self):
            self.abort_event = threading.Event()
            self.current_process = None
            self.abort_calls = 0

        def abort(self):
            self.abort_calls += 1
            self.abort_event.set()

    class _FakeGrabWindow:
        def __init__(self):
            self.released = False
            self.destroyed = False

        def grab_release(self):
            self.released = True

        def destroy(self):
            self.destroyed = True

    gui = object.__new__(JellyRipperGUI)
    gui._task_active = True
    gui._input_active = True
    gui._input_event = threading.Event()
    gui._input_result = "pending"
    gui._hide_input_bar = unittest.mock.Mock()
    gui.grab_current = lambda: grab_window
    gui.controller = unittest.mock.Mock()
    gui.engine = _FakeEngine()
    gui.abort_btn = _FakeButton()
    gui.set_status = unittest.mock.Mock()

    grab_window = _FakeGrabWindow()

    gui.request_abort()

    assert gui.engine.abort_calls == 1
    assert gui.engine.abort_event.is_set()
    gui.set_status.assert_called_once_with("Aborting...")
    assert gui.abort_btn.text == "ABORTING..."
    assert gui.abort_btn.state == "disabled"
    gui._hide_input_bar.assert_called_once_with()
    assert gui._input_event.is_set()
    assert grab_window.released is True
    assert grab_window.destroyed is True


def test_request_abort_allows_live_worker_even_if_task_flag_is_false():
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

    class _FakeThread:
        def is_alive(self):
            return True

    class _FakeEngine:
        def __init__(self):
            self.abort_event = threading.Event()
            self.current_process = None
            self.abort_calls = 0

        def abort(self):
            self.abort_calls += 1
            self.abort_event.set()

    gui = object.__new__(JellyRipperGUI)
    gui._task_active = False
    gui._input_active = False
    gui._input_event = threading.Event()
    gui._input_result = None
    gui._hide_input_bar = unittest.mock.Mock()
    gui.rip_thread = _FakeThread()
    gui.grab_current = lambda: None
    gui.controller = unittest.mock.Mock()
    gui.engine = _FakeEngine()
    gui.abort_btn = _FakeButton()
    gui.set_status = unittest.mock.Mock()

    gui.request_abort()

    assert gui.engine.abort_calls == 1
    assert gui.engine.abort_event.is_set()
    gui.controller.log.assert_called_once_with("ABORT REQUESTED BY USER")


def test_request_abort_recovers_ui_when_no_live_work_remains():
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

    class _FakeEngine:
        def __init__(self):
            self.abort_event = threading.Event()
            self.current_process = None
            self.abort_calls = 0

        def abort(self):
            self.abort_calls += 1
            self.abort_event.set()

    gui = object.__new__(JellyRipperGUI)
    gui._task_active = True
    gui._input_active = False
    gui._input_event = threading.Event()
    gui._input_result = None
    gui._hide_input_bar = unittest.mock.Mock()
    gui.rip_thread = None
    gui.grab_current = lambda: None
    gui.controller = unittest.mock.Mock()
    gui.engine = _FakeEngine()
    gui.abort_btn = _FakeButton()
    gui.enable_buttons = unittest.mock.Mock()
    gui.set_status = unittest.mock.Mock()
    gui.after = lambda _delay, fn: fn()

    gui.request_abort()

    assert gui.engine.abort_calls == 1
    assert gui.engine.abort_event.is_set()
    gui.enable_buttons.assert_called_once_with()
    assert gui.set_status.call_args_list[0].args == ("Aborting...",)
    assert gui.set_status.call_args_list[-1].args == ("Ready",)
