"""Modal dialogs for the PySide6 GUI (sub-phase 3c-ii).

Each dialog mirrors a method that the controller used to call on the
tkinter ``JellyRipperGUI``.  The shell's ``MainWindow`` delegates to
these functions; the tkinter side stays untouched until Phase 3h.

Public API (importable from ``gui_qt.dialogs``):

* ``show_info(parent, title, message)`` — informational message
* ``show_error(parent, title, message)`` — error message
* ``ask_yesno(parent, prompt, *, title="Confirm")`` — bool
* ``ask_input(parent, label, prompt, default="")`` — str | None
* ``ask_space_override(parent, required_gb, free_gb)`` — bool
* ``ask_duplicate_resolution(parent, prompt, *, retry_text, ...)``
  — Literal["retry", "bypass", "stop"]

**Thread safety:** these dialogs assume they're called from the GUI
thread.  The tkinter equivalents marshal cross-thread calls via
``self.after(0, fn)``; the Qt port will need a parallel wrapper —
deferred to a follow-up 3c-ii pass.  Until then, any worker-thread
caller must marshal via the shell's signals or
``QMetaObject.invokeMethod``.
"""

from gui_qt.dialogs.ask import ask_input, ask_yesno
from gui_qt.dialogs.disc_tree import show_disc_tree
from gui_qt.dialogs.duplicate_resolution import (
    DuplicateResolutionChoice,
    ask_duplicate_resolution,
)
from gui_qt.dialogs.info import show_error, show_info
from gui_qt.dialogs.list_picker import show_extras_picker, show_file_list
from gui_qt.dialogs.temp_manager import show_temp_manager
from gui_qt.dialogs.session_setup import (
    MovieSessionSetup,
    TVSessionSetup,
    ask_movie_setup,
    ask_tv_setup,
)
from gui_qt.dialogs.space_override import ask_space_override

__all__ = [
    "DuplicateResolutionChoice",
    "MovieSessionSetup",
    "TVSessionSetup",
    "ask_duplicate_resolution",
    "ask_input",
    "ask_movie_setup",
    "ask_space_override",
    "ask_tv_setup",
    "ask_yesno",
    "show_disc_tree",
    "show_error",
    "show_extras_picker",
    "show_file_list",
    "show_info",
    "show_temp_manager",
]
