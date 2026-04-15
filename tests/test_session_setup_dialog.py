from gui.session_setup_dialog import _prepare_identity_dialog_window


def test_prepare_identity_dialog_window_does_not_grab_parent():
    class _FakeWindow:
        def __init__(self):
            self.transient_parent = None
            self.lift_parent = None
            self.focused = False
            self.configured = {}
            self.resizable_args = None

        def configure(self, **kwargs):
            self.configured.update(kwargs)

        def resizable(self, width, height):
            self.resizable_args = (width, height)

        def transient(self, parent):
            self.transient_parent = parent

        def lift(self, parent):
            self.lift_parent = parent

        def focus_force(self):
            self.focused = True

    parent = object()
    win = _FakeWindow()

    _prepare_identity_dialog_window(win, parent)

    assert win.configured["bg"] == "#161b22"
    assert win.resizable_args == (False, False)
    assert win.transient_parent is parent
    assert win.lift_parent is parent
    assert win.focused is True

