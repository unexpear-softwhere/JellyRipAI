import pathlib

from gui.session_setup_dialog import _BG2, _choice_label, _prepare_identity_dialog_window


class _FakeParent:
    def __init__(self):
        self.top = object()

    def winfo_toplevel(self):
        return self.top


class _FakeWindow:
    def __init__(self):
        self.calls: list[tuple] = []

    def configure(self, **kwargs):
        self.calls.append(("configure", kwargs))

    def resizable(self, width, height):
        self.calls.append(("resizable", width, height))

    def transient(self, parent):
        self.calls.append(("transient", parent))

    def lift(self, parent):
        self.calls.append(("lift", parent))

    def grab_set(self):
        self.calls.append(("grab_set",))

    def focus_force(self):
        self.calls.append(("focus_force",))


class _TransientLiftFailWindow(_FakeWindow):
    def transient(self, parent):
        self.calls.append(("transient", parent))
        raise RuntimeError("transient failed")

    def lift(self, parent):
        self.calls.append(("lift", parent))
        raise RuntimeError("lift failed")


def test_prepare_identity_dialog_window_sets_modal_transient_dialog():
    parent = _FakeParent()
    win = _FakeWindow()

    _prepare_identity_dialog_window(win, parent)

    assert ("configure", {"bg": _BG2}) in win.calls
    assert ("resizable", False, False) in win.calls
    assert ("transient", parent.top) in win.calls
    assert ("lift", parent.top) in win.calls
    assert ("grab_set",) in win.calls
    assert ("focus_force",) in win.calls


def test_prepare_identity_dialog_window_still_grabs_if_transient_setup_fails():
    parent = _FakeParent()
    win = _TransientLiftFailWindow()

    _prepare_identity_dialog_window(win, parent)

    assert ("grab_set",) in win.calls
    assert ("focus_force",) in win.calls


def test_choice_label_matches_values_case_insensitively():
    labels = ["Ask per disc", "Put in Season 00", "Skip specials"]
    values = ["ASK", "Season0", "skip"]

    assert _choice_label("season0", labels, values) == "Put in Season 00"
    assert _choice_label(" ASK ", labels, values) == "Ask per disc"


def test_session_setup_dialog_source_keeps_main_text_labels():
    source = pathlib.Path("gui/session_setup_dialog.py").read_text(encoding="utf-8")

    assert "ask_tv_setup() \\u2014 never instantiated directly from worker threads." in source
    assert '"Custom\\u2026"' in source
    assert "ask_tv_setup() - never instantiated directly from worker threads." not in source
    assert '"Custom..."' not in source
