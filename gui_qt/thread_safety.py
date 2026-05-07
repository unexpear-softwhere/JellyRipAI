"""Thread-safety helper for cross-thread GUI calls.

The tkinter ``JellyRipperGUI`` had a ``_run_on_main`` method that
marshaled a callable from a worker thread onto the Tk main loop and
blocked the worker until the callable finished, returning its result.
This module is the Qt equivalent.

**Why this is needed:** Qt widgets are only safe to touch from the
GUI thread.  The controller calls back into the GUI from worker
threads (``set_status`` / ``set_progress`` / ``append_log`` / etc.).
Without marshaling, those calls either crash Qt or silently misbehave.

**Pattern used:**

* An ``Invoker`` ``QObject`` lives on the GUI thread.
* It has a single signal connected to its ``_dispatch`` slot via
  ``Qt.QueuedConnection``.  The connection type makes the slot run
  on the *receiver's* thread (the GUI thread), not the emitter's.
* ``run_on_main(invoker, fn, *args, **kwargs)`` either:
    1. Calls ``fn`` directly if already on the GUI thread.
    2. Submits the callable via the signal, blocks until done with
       a ``threading.Event``, and returns the result.

This mirrors tkinter's same-thread fast-path + cross-thread blocking
pattern at ``gui/main_window.py:5167``.

Tests can construct an ``Invoker`` on the test thread and verify
both code paths.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, TypeVar

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot

T = TypeVar("T")


class Invoker(QObject):
    """Marshals callables onto the thread that owns this object.

    Instantiate on the GUI thread (e.g., during ``MainWindow.__init__``).
    Call ``submit(fn)`` from any thread to run ``fn`` on the GUI thread.
    """

    # Carries an arbitrary callable.  ``object`` because Qt's
    # signal/slot system doesn't have a "callable" type slot.
    _trigger = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # QueuedConnection ensures the slot runs on this object's
        # thread, even when the signal is emitted from a different
        # thread.
        self._trigger.connect(self._dispatch, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _dispatch(self, callable_obj: Callable[[], None]) -> None:
        """Slot — runs the callable on the invoker's thread."""
        callable_obj()

    def submit(self, fn: Callable[[], None]) -> None:
        """Schedule ``fn`` to run on this invoker's thread.

        Async — does not wait for completion.  Use ``run_on_main``
        for the blocking variant that captures a return value.
        """
        self._trigger.emit(fn)


def run_on_main(
    invoker: Invoker,
    fn: Callable[..., T],
    *args: Any,
    **kwargs: Any,
) -> T:
    """Run ``fn(*args, **kwargs)`` on the invoker's thread.

    If the caller is already on the invoker's thread, calls ``fn``
    directly (fast path; no marshaling overhead).

    Otherwise:

    1. Wraps ``fn`` in a closure that captures the result and signals
       a ``threading.Event`` when done.
    2. Submits the wrapper via the invoker's signal.
    3. Blocks the calling thread until the event fires.
    4. Returns the captured result (or re-raises if ``fn`` raised).

    This is the contract callers rely on: a synchronous, return-value-
    preserving cross-thread call.

    Mirrors tkinter's ``_run_on_main`` pattern at
    ``gui/main_window.py:5167``.
    """
    if QThread.currentThread() is invoker.thread():
        return fn(*args, **kwargs)

    result_holder: list[Any] = [None]
    error_holder: list[BaseException | None] = [None]
    done = threading.Event()

    def wrapper() -> None:
        try:
            result_holder[0] = fn(*args, **kwargs)
        except BaseException as e:  # noqa: BLE001 — we re-raise below
            error_holder[0] = e
        finally:
            done.set()

    invoker.submit(wrapper)
    done.wait()

    if error_holder[0] is not None:
        raise error_holder[0]
    return result_holder[0]


def submit_to_main(invoker: Invoker, fn: Callable[..., None], *args: Any, **kwargs: Any) -> None:
    """Async variant of ``run_on_main`` — submits and returns immediately.

    Use for fire-and-forget UI updates from worker threads (status,
    progress, log lines) where blocking the worker on every UI call
    would slow down the rip.

    The callable still runs on the invoker's thread, but the caller
    doesn't wait.  Errors raised by ``fn`` are silently swallowed by
    Qt's signal dispatch — same trade-off as Qt's normal queued
    connections.  If error visibility matters, use ``run_on_main``.
    """
    if QThread.currentThread() is invoker.thread():
        fn(*args, **kwargs)
        return

    if args or kwargs:
        invoker.submit(lambda: fn(*args, **kwargs))
    else:
        invoker.submit(fn)
