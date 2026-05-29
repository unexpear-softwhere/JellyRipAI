"""Run workflow dialogs without freezing the docked AI chat.

``QDialog.exec()`` (and the static ``QMessageBox`` / ``QInputDialog``
helpers) force *modality*.  A modal dialog blocks every widget in its
window — and because the AI chat is now a **docked panel inside the
main window**, modality freezes the chat too.  That defeats the
headline AI-BRANCH feature: "the assistant is always available, even
while the identity step is open."

``exec_modeless()`` shows the dialog **non-modally** and spins a local
``QEventLoop`` until it closes.  The worker thread that launched the
dialog (blocked inside ``run_on_main``) stays blocked until we have a
result — so the rip workflow still waits for the user's answer — but
the GUI thread keeps dispatching events to *every* widget, so the
chat dock stays live.

**Why a nested event loop instead of just ``show()``?**  The dialog
functions are called on the GUI thread from inside ``run_on_main``'s
dispatch.  If we returned immediately after ``show()``, ``run_on_main``
would unblock the worker before the user answered.  The nested loop
keeps the GUI-thread call blocked (so the worker stays blocked) while
still pumping events — exactly what ``exec()`` does, minus the
modality.

**Workflow desync** (the user clicking "Rip Movie Disc" again while
the identity dialog is open) is prevented separately: if the dialog's
top-level window exposes ``begin_workflow_dialog`` /
``end_workflow_dialog`` (``MainWindow`` does), those are called around
the loop to soft-lock the workflow buttons for the dialog's lifetime.
The lock runs on the GUI thread (here, inside the loop) — never on the
worker thread — so it's safe to touch widgets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEventLoop, Qt

if TYPE_CHECKING:
    from PySide6.QtWidgets import QDialog


def _guard_host(dialog: "QDialog") -> object | None:
    """Return the dialog's top-level window iff it exposes the
    workflow-dialog guard API, else ``None``.

    Resilient by design: a missing parent, a parentless dialog, or a
    host without the guard methods all yield ``None`` and the dialog
    simply runs without the soft-lock.
    """
    try:
        parent = dialog.parentWidget()
        host = parent.window() if parent is not None else None
    except Exception:
        return None
    if host is not None and hasattr(host, "begin_workflow_dialog") and hasattr(
        host, "end_workflow_dialog"
    ):
        return host
    return None


def exec_modeless(dialog: "QDialog") -> int:
    """Show ``dialog`` non-modally and block until it closes.

    Returns the dialog's result code — ``QDialog.Accepted`` /
    ``QDialog.Rejected`` for ordinary dialogs.  (Custom workflow
    dialogs ignore this and read their own ``result_value`` /
    ``choice`` / ``proceed`` attribute after the call, exactly as they
    did with ``exec()``.)
    """
    dialog.setModal(False)
    dialog.setWindowModality(Qt.WindowModality.NonModal)

    host = _guard_host(dialog)
    if host is not None:
        host.begin_workflow_dialog()  # type: ignore[attr-defined]

    loop = QEventLoop()
    dialog.finished.connect(loop.quit)
    try:
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        loop.exec()
    finally:
        if host is not None:
            host.end_workflow_dialog()  # type: ignore[attr-defined]
    return dialog.result()
