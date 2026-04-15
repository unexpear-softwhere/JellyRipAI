from gui.main_window import JellyRipperGUI
from gui.theme import build_app_theme


class _FakeVar:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _FakeWidget:
    def __init__(self):
        self.values = {}

    def configure(self, **kwargs):
        self.values.update(kwargs)


def test_main_status_style_maps_idle_active_warn_and_error_states():
    gui = object.__new__(JellyRipperGUI)
    gui._theme = build_app_theme()
    theme = gui._theme

    assert gui._main_status_style_for_message("Ready") == (
        theme["pill_idle_bg"],
        theme["pill_idle_border"],
        theme["ready_text"],
    )
    assert gui._main_status_style_for_message("Ripping... (this may take 20-60 min)") == (
        theme["pill_active_bg"],
        theme["pill_active_border"],
        theme["pill_active_border"],
    )
    assert gui._main_status_style_for_message("Settings need attention") == (
        theme["pill_warn_bg"],
        theme["pill_warn_border"],
        theme["pill_warn_border"],
    )
    assert gui._main_status_style_for_message("Rip Failed") == (
        theme["pill_error_bg"],
        theme["pill_error_border"],
        theme["pill_error_border"],
    )


def test_set_status_updates_main_indicator_text_and_style():
    gui = object.__new__(JellyRipperGUI)
    gui._theme = build_app_theme()
    gui.after = lambda _delay, fn: fn()
    gui.status_var = _FakeVar("Ready")
    gui.status_indicator = _FakeWidget()
    gui.status_value_label = _FakeWidget()

    gui.set_status("Ripping... (this may take 20-60 min)")

    assert gui.status_var.get() == "Ripping... (this may take 20-60 min)"
    assert gui.status_indicator.values["bg"] == gui._theme["pill_active_bg"]
    assert gui.status_indicator.values["highlightbackground"] == gui._theme["pill_active_border"]
    assert gui.status_value_label.values["bg"] == gui._theme["pill_active_bg"]
    assert gui.status_value_label.values["fg"] == gui._theme["pill_active_border"]

    gui.set_status("Ready")

    assert gui.status_var.get() == "Ready"
    assert gui.status_indicator.values["bg"] == gui._theme["pill_idle_bg"]
    assert gui.status_indicator.values["highlightbackground"] == gui._theme["pill_idle_border"]
    assert gui.status_value_label.values["bg"] == gui._theme["pill_idle_bg"]
    assert gui.status_value_label.values["fg"] == gui._theme["ready_text"]
