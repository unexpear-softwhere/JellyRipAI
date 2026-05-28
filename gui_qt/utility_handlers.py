"""Utility chip handlers — wires the shell's
``utility_button_clicked(objectName)`` signal to actions.

The four chips emit:

* ``utilSettings``  — defer to Phase 3d (settings dialog)
* ``utilUpdates``   — kick off the existing ``check_for_updates`` flow
* ``utilCopyLog``   — copy log pane contents to the system clipboard
* ``utilBrowse``    — open a folder picker, log the chosen path

Mirrors the tkinter equivalents:

* Settings: ``JellyRipperGUI._open_settings_safe``
* Updates: ``gui.update_ui.check_for_updates(gui)``
* Copy Log: ``gui/main_window.py:4015`` (the ``copy_log`` callback)
* Browse Folder: ``gui/main_window.py:1364`` (``_browse_folder_in_explorer``)

The handler module is a thin layer — most actions delegate to existing
toolkit-agnostic helpers (e.g., the updates flow only calls into
``gui.update_ui`` which uses ``gui.gui.show_info`` / ``show_error``;
those now reach the Qt path).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication, QFileDialog

if TYPE_CHECKING:
    from gui_qt.main_window import MainWindow


class UtilityHandler(QObject):
    """Connects the main window's ``utility_button_clicked`` signal
    to handler callbacks.  Each chip's behavior is encapsulated in a
    method named after the chip's objectName so tests can monkeypatch
    individual handlers without setting up the full shell.
    """

    def __init__(
        self,
        window: "MainWindow",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent or window)
        self._window = window
        self._connected = False

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def connect_signals(self) -> None:
        if self._connected:
            return
        self._window.utility_button_clicked.connect(self._dispatch)
        self._connected = True

    def disconnect_signals(self) -> None:
        if not self._connected:
            return
        try:
            self._window.utility_button_clicked.disconnect(self._dispatch)
        except (RuntimeError, TypeError):
            pass
        self._connected = False

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, object_name: str) -> None:
        handler = getattr(self, "handle_" + object_name, None)
        if handler is None:
            self._window.append_log(
                f"Utility chip {object_name!r} has no handler yet."
            )
            return
        try:
            handler()
        except Exception as e:  # noqa: BLE001 — handlers can fail; log + continue
            # Surface the failure in three places so the user can't
            # miss it: the live log pane (was the only spot), the
            # status bar (one-line user-facing notice), and the
            # session log via stdlib logging (with stack trace for
            # later diagnosis).  Was a silent log-only swallow that
            # the user could easily miss in a busy log pane.
            import logging
            self._window.append_log(
                f"Utility chip {object_name!r} failed: {e}"
            )
            try:
                self._window.set_status(
                    f"⚠ {object_name!r} failed — see log pane."
                )
            except Exception:
                # MainWindow might not expose set_status in all
                # configurations; the log_pane write above is the
                # baseline guarantee.
                pass
            logging.exception(
                "Utility chip %r raised: %s", object_name, e,
            )

    # ------------------------------------------------------------------
    # Per-chip handlers — each named to match the objectName so
    # ``_dispatch`` can resolve via getattr.
    # ------------------------------------------------------------------

    def handle_utilSettings(self) -> None:  # noqa: N802 — matches objectName
        """Open the JellyRip settings dialog.

        Currently the dialog only ships the Themes tab — the
        everyday/advanced/expert tabs are pending and tracked by
        ``docs/handoffs/phase-3d-port-settings-tabs.md``.

        Lazy import of ``gui_qt.settings`` keeps utility_handlers'
        own import chain free of the settings package weight."""
        from gui_qt.settings import show_settings
        try:
            from config import save_config
        except Exception:
            save_config = None  # noqa: N806

        cfg = self._window._cfg if hasattr(self._window, "_cfg") else {}
        accepted = show_settings(
            self._window,
            cfg=cfg,
            save_cfg=save_config,
            window=self._window,
        )
        if accepted:
            self._window.append_log("Settings saved.")
        else:
            self._window.append_log("Settings closed without saving.")

    def handle_utilAIProvider(self) -> None:  # noqa: N802 — matches objectName
        """Open the AI Provider Setup dialog.

        AI BRANCH only — ported in Phase 4 from
        ``gui/ai_provider_dialog.py`` (tkinter) to
        ``gui_qt/dialogs/ai_provider.py`` (Qt).  Lazy-imported so MAIN
        (which doesn't ship the AI surface) wouldn't hit a missing
        module if the chip ever leaks into MAIN's toolbar.

        ``on_change`` rebuilds nothing today — the dialog handles its
        own internal refresh.  Hook it up here when the main window
        needs to react (e.g., to refresh an AI-mode indicator)."""
        try:
            from gui_qt.dialogs.ai_provider import open_ai_provider_dialog
        except Exception as e:  # noqa: BLE001
            self._window.append_log(
                f"AI Provider dialog unavailable: {e}"
            )
            return

        def _on_change() -> None:
            # Today: just append a log line so the user sees the
            # provider state changed.  Future: emit a signal the
            # AI mode indicator can listen on.
            self._window.append_log("AI provider configuration updated.")

        open_ai_provider_dialog(self._window, on_change=_on_change)

    def handle_utilAIChat(self) -> None:  # noqa: N802 — matches objectName
        """Toggle the AI chat sidebar dock.

        AI BRANCH only — Phase 4b.  The dock is lazily constructed
        on first call via ``MainWindow.toggle_chat_sidebar()`` so
        the widget cost (QTextBrowser, etc.) only lands when the
        user opens it.

        Backend wiring (controller-driven send/receive) is a
        follow-up; this handler currently just makes the sidebar
        visible.  Until the backend lands, typing into the input
        and pressing Send will fire ``message_submitted`` to
        nobody — harmless, but visibly inert.  See
        ``gui_qt/ai_chat_sidebar.py`` for the signal/slot contract
        a future controller hook can attach to.
        """
        try:
            new_state = self._window.toggle_chat_sidebar()
        except Exception as e:  # noqa: BLE001
            self._window.append_log(
                f"AI chat sidebar unavailable: {e}"
            )
            return
        if new_state:
            self._window.append_log("AI chat sidebar opened.")
        else:
            self._window.append_log("AI chat sidebar closed.")

    def handle_utilUpdates(self) -> None:  # noqa: N802
        """Trigger the in-app update check.

        Phase 3h (2026-05-04) — the prior implementation in
        ``gui/update_ui.py`` was tkinter-specific (``gui.after()``,
        ``gui.destroy()``, etc.) and was not ported one-to-one
        during the tkinter retirement.  ``tools.update_check`` now
        holds a deferred-feature stub that logs guidance pointing
        users at the GitHub Releases page; a Qt-native rewrite is
        scheduled as polish-tier follow-up work.
        """
        from tools.update_check import check_for_updates
        check_for_updates(self._window)

    def handle_utilCopyLog(self) -> None:  # noqa: N802
        """Copy the log pane's full text to the system clipboard.

        Mirrors tkinter's copy-log button (``gui/main_window.py:4015``):
        if the log is empty, log a notice; otherwise copy and confirm.
        """
        text = self._window.log_pane.get_text()
        if not text.strip():
            self._window.append_log("Log is empty — nothing to copy.")
            return

        clipboard = QApplication.clipboard()
        if clipboard is None:
            self._window.append_log(
                "No system clipboard available — could not copy log."
            )
            return
        clipboard.setText(text)
        self._window.append_log("Log copied to clipboard.")

    def handle_utilBrowse(self) -> None:  # noqa: N802
        """Open a folder picker and log the selected path.

        Mirrors tkinter's Browse Folder chip
        (``gui/main_window.py:1364``).  The user can pick any folder;
        we just log it — the controller doesn't currently consume
        this state.  Cancelling the picker → no-op.
        """
        folder = QFileDialog.getExistingDirectory(
            self._window,
            "Choose a folder to open",
        )
        if not folder:
            return  # user cancelled
        self._window.append_log(f"Browsed to folder: {folder}")
