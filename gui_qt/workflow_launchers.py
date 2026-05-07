"""Workflow launcher — wires the shell's button signals to controller
methods and runs them in worker threads.

The shell (``gui_qt/main_window.py``) emits
``workflow_button_clicked(objectName)`` on click.  This module maps
that objectName to the appropriate controller method and starts a
worker thread, mirroring tkinter's ``start_task`` pattern at
``gui/main_window.py:7635``.

**Mapping (objectName → controller method):**

* ``modeGoTv``       → ``controller.run_tv_disc``
* ``modeGoMovie``    → ``controller.run_movie_disc``
* ``modeInfoDump``   → ``controller.run_dump_all``
* ``modeAltOrganize`` → ``controller.run_organize``
* ``modeWarnPrep``   → not yet wired — the tkinter "Prep MKVs"
  workflow uses an inline transcode-queue helper, not a top-level
  ``run_*`` controller method.  Wiring it requires porting
  ``_run_transcode_queue`` first; deferred to 3c-iii.
* ``stopSession``    → sets the engine's abort_event

**Worker lifecycle (mirrors tkinter):**

1. Reject the click if a workflow is already running.
2. Reset the abort event on the engine.
3. Reset session state on the controller.
4. Disable the workflow buttons / enable Stop.
5. Start a daemon thread running the controller method.
6. On finish (success, error, or abort): re-enable buttons,
   reset progress, set status to "Ready".

Worker threads call back into the shell via the thread-safety
wrapper (``set_status`` / ``set_progress`` / ``append_log`` /
``show_error`` are all thread-safe — see ``gui_qt/thread_safety.py``).
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Optional

from PySide6.QtCore import QObject

if TYPE_CHECKING:
    from gui_qt.main_window import MainWindow


# Mapping from button objectName to controller attribute name.  The
# launcher resolves the actual callable at click time so the
# controller can be swapped out for testing.
_BUTTON_TO_CONTROLLER_METHOD: dict[str, str] = {
    "modeGoTv":        "run_tv_disc",
    "modeGoMovie":     "run_movie_disc",
    "modeInfoDump":    "run_dump_all",
    "modeAltOrganize": "run_organize",
    # modeWarnPrep — the Prep MKVs workflow runs through the
    # ``_handle_prep_workflow`` method below rather than a top-level
    # ``run_*`` controller method; the tkinter equivalent is
    # ``_open_folder_scanner`` → ``_open_transcode_queue_builder`` →
    # ``_run_transcode_queue`` (a 3-window flow).  This module ports
    # the entry point as an MVP (folder pick → MKV scan → summary);
    # the full queue UI is deferred to
    # ``docs/handoffs/phase-3c-iii-prep-workflow.md``.
}


def find_mkv_files(folder: str) -> list[str]:
    """Walk ``folder`` recursively and return absolute paths to all
    ``.mkv`` files.  Pure function — no Qt dependency.

    Used by the Prep MVP to give the user immediate feedback about
    what's in their folder.  The tkinter version uses
    ``tools.folder_scanner.scan_folder`` which also probes each
    file with ffprobe; the MVP skips that for speed and simplicity.
    """
    import os
    out: list[str] = []
    if not folder:
        return out
    for dirpath, _dirnames, filenames in os.walk(folder):
        for name in filenames:
            if name.lower().endswith(".mkv"):
                out.append(os.path.join(dirpath, name))
    return sorted(out)


class WorkflowLauncher(QObject):
    """Connects the main window's workflow signals to controller
    methods and manages the worker-thread lifecycle.

    Construction:

        launcher = WorkflowLauncher(window, controller, engine)
        launcher.connect_signals()

    The launcher does NOT own the window or controller — it just
    holds references and connects signals.  Disconnecting (e.g., for
    tests) is via ``disconnect_signals``.
    """

    def __init__(
        self,
        window: "MainWindow",
        controller: Any,
        engine: Any,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._controller = controller
        self._engine = engine
        self._rip_thread: Optional[threading.Thread] = None
        # Track whether signals are connected so disconnect is safe.
        self._connected = False

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def connect_signals(self) -> None:
        """Connect the shell's signals to this launcher.  Idempotent."""
        if self._connected:
            return
        self._window.workflow_button_clicked.connect(self._on_workflow_click)
        self._connected = True

    def disconnect_signals(self) -> None:
        """Reverse of ``connect_signals``.  Idempotent."""
        if not self._connected:
            return
        try:
            self._window.workflow_button_clicked.disconnect(self._on_workflow_click)
        except (RuntimeError, TypeError):
            # Already disconnected or signal interface changed
            pass
        self._connected = False

    # ------------------------------------------------------------------
    # Click dispatch
    # ------------------------------------------------------------------

    def _on_workflow_click(self, object_name: str) -> None:
        """Slot — called when the shell emits
        ``workflow_button_clicked``.  Resolves the controller method
        (or the Prep MVP handler) and starts a worker thread."""
        # Stop session is a special case — set abort, don't spawn.
        if object_name == "stopSession":
            self._handle_stop_session()
            return

        # Tool-path pre-flight.  Surfaces a friendly "MakeMKV /
        # ffprobe not found, please check Settings" dialog instead
        # of the cryptic ``[Errno 2] No such file or directory: ''``
        # the user used to see when the configured binaries went
        # missing or moved.  Guards every workflow that touches
        # makemkvcon or ffprobe (everything except Prep MVP, which
        # just walks folders).  See
        # tests/test_failure_modes_section_8.py for the orphan-call
        # gap this closes.
        if object_name != "modeWarnPrep":
            if not self._validate_tools_or_show_error():
                return

        # Prep MKVs has no top-level ``run_*`` method on the
        # controller — it runs through ``_handle_prep_workflow``
        # instead.  See module docstring + the brief at
        # ``docs/handoffs/phase-3c-iii-prep-workflow.md``.
        if object_name == "modeWarnPrep":
            self.start_task(self._run_prep_mvp, button_name=object_name)
            return

        method_name = _BUTTON_TO_CONTROLLER_METHOD.get(object_name)
        if method_name is None:
            # Genuinely unmapped button.
            self._window.append_log(
                f"Workflow {object_name!r} is not yet wired."
            )
            return

        target = getattr(self._controller, method_name, None)
        if target is None or not callable(target):
            self._window.append_log(
                f"Controller has no method {method_name!r}; "
                f"workflow {object_name!r} cannot launch."
            )
            return

        self.start_task(target, button_name=object_name)

    def _validate_tools_or_show_error(self) -> bool:
        """Run the engine's tool-path pre-flight.  On failure, log
        + show an error dialog and return ``False`` so the caller
        can refuse to launch the workflow.  Returns ``True`` when
        either the validation passes OR the engine doesn't expose
        ``validate_tools`` (legacy / test stubs)."""
        engine = self._engine
        if engine is None or not hasattr(engine, "validate_tools"):
            return True
        try:
            ok, reason = engine.validate_tools()
        except Exception as exc:  # noqa: BLE001 — defensive
            # A crash inside validate_tools shouldn't block the user.
            self._window.append_log(
                f"Tool-path pre-flight crashed: {exc}.  Proceeding."
            )
            return True
        if ok:
            return True
        self._window.append_log(f"Tool-path pre-flight failed: {reason}")
        self._window.show_error(
            "Required Tool Not Found",
            reason or "MakeMKV or ffprobe could not be located.",
        )
        return False

    # ------------------------------------------------------------------
    # Prep MKVs MVP
    # ------------------------------------------------------------------

    def _run_prep_mvp(self) -> None:
        """Minimum-viable Prep workflow.

        Runs on a worker thread (via ``start_task``).  Calls
        thread-safe shell methods to:

        1. Open a folder picker (``ask_directory``).
        2. Walk for ``.mkv`` files via ``find_mkv_files``.
        3. Show a summary via ``show_info``.
        4. Log the count + an explicit pointer to the brief for the
           full transcode-queue UI port.

        The folder-scanner / queue-builder / progress-display
        subwindows are deferred to a follow-up — see
        ``docs/handoffs/phase-3c-iii-prep-workflow.md``.
        """
        folder = self._window.ask_directory(
            "Prep MKVs",
            "Choose a folder containing MKVs to prep",
        )
        if not folder:
            self._window.append_log("Prep cancelled — no folder selected.")
            return

        self._window.append_log(f"Scanning {folder} for MKVs...")
        try:
            mkvs = find_mkv_files(folder)
        except Exception as e:  # noqa: BLE001
            self._window.show_error(
                "Prep MKVs",
                f"Could not scan {folder}:\n\n{e}",
            )
            return

        count = len(mkvs)
        if count == 0:
            self._window.show_info(
                "Prep MKVs",
                f"No .mkv files found in:\n{folder}",
            )
            self._window.append_log("Prep: no MKVs found.")
            return

        # Log every file found so the user gets verbose feedback in
        # the log pane (parallels what the tkinter scanner does).
        for path in mkvs:
            self._window.append_log(f"  • {path}")

        self._window.show_info(
            "Prep MKVs",
            f"Found {count} MKV file(s) in:\n{folder}\n\n"
            f"The transcode queue UI is pending — see\n"
            f"docs/handoffs/phase-3c-iii-prep-workflow.md "
            f"for the full port plan.",
        )
        self._window.append_log(
            f"Prep: scanned {count} MKVs.  Transcode queue UI pending "
            "(see docs/handoffs/phase-3c-iii-prep-workflow.md)."
        )

    def _handle_stop_session(self) -> None:
        """Set the engine's abort event and update status."""
        if self._engine is None or not hasattr(self._engine, "abort_event"):
            self._window.append_log("Stop pressed; no engine to abort.")
            return
        self._engine.abort_event.set()
        self._window.set_status("Aborting...")
        self._window.append_log("Stop requested by user.")

    # ------------------------------------------------------------------
    # Thread lifecycle
    # ------------------------------------------------------------------

    def is_busy(self) -> bool:
        """True if a workflow thread is currently running.  Pinned
        as a public test hook + used for the busy check in
        ``start_task``."""
        return self._rip_thread is not None and self._rip_thread.is_alive()

    def start_task(
        self,
        target: Callable[[], Any],
        *,
        button_name: str = "",
    ) -> bool:
        """Launch ``target`` in a daemon worker thread, mirroring
        tkinter's ``start_task`` at ``gui/main_window.py:7635``.

        Returns ``True`` if the task was started, ``False`` if a
        prior task is still running (busy check).

        Pre-flight (on the calling thread):

        1. Reject if busy (caller can show the user a message).
        2. Reset the engine's abort event so the new task starts
           clean.
        3. Reset session state on the controller.

        Worker thread:

        1. Run ``target()``.
        2. Catch any exception and route to the log + error dialog.
        3. Always: reset progress, set status "Ready".
        """
        if self.is_busy():
            self._window.show_info(
                "Busy",
                "Wait for the current operation to finish.",
            )
            return False

        # Reset abort flag.
        if self._engine is not None and hasattr(self._engine, "reset_abort"):
            self._engine.reset_abort()

        # Reset session state on the controller (matches tkinter
        # lines 7710-7713).
        if self._controller is not None:
            try:
                self._controller.session_log = []
                self._controller.session_report = []
                self._controller.start_time = datetime.now()
                self._controller.global_extra_counter = 1
            except AttributeError:
                # Controller doesn't have these (e.g., a stub during tests)
                pass

        self._window.set_progress(0)

        thread = threading.Thread(
            target=self._task_wrapper,
            args=(target, button_name),
            daemon=True,
            name=f"workflow:{button_name or 'unknown'}",
        )
        self._rip_thread = thread
        thread.start()
        return True

    def _task_wrapper(self, target: Callable[[], Any], button_name: str) -> None:
        """Worker-thread entry point.  Runs ``target`` and routes
        any exception to the log + error dialog.  Always restores
        the GUI to a Ready state at exit.

        Exception handling deliberately broad — the tkinter equivalent
        catches everything and records crash details (line 7748).  We
        log the error and surface it to the user; deeper diagnostics
        (ring-buffer dump etc.) land when 3c-iii ports the
        diagnostics integration.
        """
        try:
            target()
        except BaseException as e:  # noqa: BLE001 — top-level catch by design
            try:
                self._window.append_log(f"Unhandled error in {button_name}: {e}")
            except Exception:  # noqa: BLE001 — don't double-fault
                pass
            try:
                self._window.show_error(
                    "Workflow Error",
                    f"The workflow {button_name!r} hit an unhandled error:\n\n{e}",
                )
            except Exception:  # noqa: BLE001
                pass
        finally:
            try:
                self._window.stop_indeterminate()
                self._window.set_status("Ready")
            except Exception:  # noqa: BLE001
                pass
