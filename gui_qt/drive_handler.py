"""Drive-scan handler — wires the shell's ``drive_refresh_clicked``
signal to the engine's drive-enumeration helper, then populates the
drive combo on the GUI thread.

Mirrors the tkinter ``_refresh_drives`` / ``_update_drive_menu`` /
``_on_drive_select`` flow at ``gui/main_window.py:3972`` — but split
into a small QObject so it's testable in isolation and not coupled
to the shell's import chain.

**Threading model:**

* The refresh runs in a daemon worker thread (drive enumeration
  shells out to ``makemkvcon``, which can block).
* The combo update marshals back to the GUI thread via
  ``submit_to_main`` from ``gui_qt.thread_safety``.
* Persisting ``opt_drive_index`` on selection happens on the GUI
  thread (the user clicked a combo item, so we're already there).

**Cfg keys touched:** ``opt_drive_index`` (int).  The handler reads
it on populate to restore the user's prior selection, and writes it
when the user picks a new drive.

**Out of scope:** if drive enumeration fails (no makemkvcon, no
drives detected, etc.), we log the error and fall back to a single
"(no drives detected)" placeholder rather than blocking startup.
The tkinter side does the same.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Callable, Sequence

from PySide6.QtCore import QObject

from gui_qt.formatters import format_drive_label
from gui_qt.thread_safety import submit_to_main

if TYPE_CHECKING:
    from gui_qt.main_window import MainWindow
    from utils.helpers import MakeMKVDriveInfo


def _coerce_drive_info(drive: Any) -> "MakeMKVDriveInfo":
    """Normalize a drive into ``MakeMKVDriveInfo``.

    Mirrors ``JellyRipperGUI._coerce_drive_info`` at
    ``gui/main_window.py:233``.  Lazy-imports
    ``utils.helpers.MakeMKVDriveInfo`` so this module stays
    importable without that dependency in the path.

    Accepts:

    * ``MakeMKVDriveInfo`` — passed through unchanged.
    * 2-tuple ``(idx, name)`` — wrapped into a default-state info.
    * Anything else — returns the default-drive sentinel.
    """
    from utils.helpers import MakeMKVDriveInfo

    if isinstance(drive, MakeMKVDriveInfo):
        return drive
    try:
        idx, name = drive
        idx = int(idx)
        return MakeMKVDriveInfo(
            index=idx,
            state_code=0,
            flags_code=999,
            disc_type_code=0,
            drive_name=str(name),
            disc_name="",
            device_path=f"disc:{idx}",
        )
    except Exception:
        return _default_drive_info()


def _default_drive_info() -> "MakeMKVDriveInfo":
    """The sentinel used when no drives are available.  Mirrors
    ``JellyRipperGUI._default_drive_info`` at ``gui/main_window.py:222``."""
    from utils.helpers import MakeMKVDriveInfo
    return MakeMKVDriveInfo(
        index=0,
        state_code=0,
        flags_code=999,
        disc_type_code=0,
        drive_name="(no drives detected)",
        disc_name="",
        device_path="disc:0",
    )


class DriveHandler(QObject):
    """Connects ``drive_refresh_clicked`` to a worker-thread scan and
    populates ``window.drive_combo`` on completion.

    Construction:

        handler = DriveHandler(window, cfg, scanner=get_available_drives)
        handler.connect_signals()
        handler.refresh_async()  # initial populate at startup

    The ``scanner`` callable defaults to lazy-importing
    ``utils.helpers.get_available_drives`` and resolving makemkvcon
    via ``config.resolve_makemkvcon`` — same path the tkinter version
    uses.  Tests inject a stub scanner to avoid touching the
    filesystem.
    """

    def __init__(
        self,
        window: "MainWindow",
        cfg: dict[str, Any] | None = None,
        scanner: Callable[[], Sequence[Any]] | None = None,
        save_cfg: Callable[[dict], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent or window)
        self._window = window
        self._cfg = cfg if cfg is not None else {}
        self._scanner = scanner or self._default_scanner
        self._save_cfg = save_cfg
        # Latest list of drives (after coercion).  Populated on
        # successful refresh; consulted by ``_on_drive_changed`` to
        # map combo selections back to drive indices.
        self.drive_options: list["MakeMKVDriveInfo"] = []
        self._connected = False

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def connect_signals(self) -> None:
        if self._connected:
            return
        self._window.drive_refresh_clicked.connect(self._on_refresh_click)
        # Connect the combo's currentIndexChanged signal to persist
        # the user's choice back to cfg.
        self._window.drive_combo.currentIndexChanged.connect(
            self._on_combo_changed,
        )
        self._connected = True

    def disconnect_signals(self) -> None:
        if not self._connected:
            return
        try:
            self._window.drive_refresh_clicked.disconnect(self._on_refresh_click)
            self._window.drive_combo.currentIndexChanged.disconnect(
                self._on_combo_changed,
            )
        except (RuntimeError, TypeError):
            pass
        self._connected = False

    # ------------------------------------------------------------------
    # Click → worker thread
    # ------------------------------------------------------------------

    def _on_refresh_click(self) -> None:
        self._window.append_log("Scanning for drives...")
        self.refresh_async()

    def refresh_async(self) -> threading.Thread:
        """Spawn a daemon thread to enumerate drives.  Returns the
        thread for tests that want to join."""
        thread = threading.Thread(
            target=self._scan_worker,
            daemon=True,
            name="drive-scan",
        )
        thread.start()
        return thread

    def _scan_worker(self) -> None:
        """Worker-thread entry point.  Calls the scanner, marshals
        the result back to the GUI thread for combo population."""
        try:
            drives = self._scanner()
        except Exception as e:  # noqa: BLE001 — scanner failures are surfaced
            submit_to_main(
                self._window._invoker,
                self._window.append_log,
                f"Drive scan failed: {e}",
            )
            submit_to_main(
                self._window._invoker,
                self.populate_combo,
                [],
            )
            return
        submit_to_main(
            self._window._invoker,
            self.populate_combo,
            drives,
        )

    # ------------------------------------------------------------------
    # Combo population (GUI thread)
    # ------------------------------------------------------------------

    def populate_combo(
        self,
        drives: Sequence[Any],
    ) -> None:
        """Replace the drive combo's items with formatted labels for
        ``drives``.  If empty, falls back to a single placeholder
        entry so the combo isn't visually empty.

        Restores the user's prior ``opt_drive_index`` selection when
        possible.
        """
        normalized = [_coerce_drive_info(d) for d in (drives or [])]
        if not normalized:
            normalized = [_default_drive_info()]

        self.drive_options = normalized
        include_glyph = bool(self._cfg.get("opt_drive_state_glyph", True))
        labels = [
            format_drive_label(d, include_state_glyph=include_glyph)
            for d in normalized
        ]

        combo = self._window.drive_combo
        # Block the index-changed signal during repopulation so we
        # don't trigger a spurious "user picked a drive" save.
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItems(labels)
            combo.setEnabled(True)

            # Restore prior selection if possible.
            target_idx = int(self._cfg.get("opt_drive_index", 0))
            for i, drive in enumerate(normalized):
                if drive.index == target_idx:
                    combo.setCurrentIndex(i)
                    break
            else:
                if labels:
                    combo.setCurrentIndex(0)
        finally:
            combo.blockSignals(False)

    def refresh_labels(self) -> None:
        """Re-render the combo's current labels using the current cfg.

        Called by the Appearance tab when ``opt_drive_state_glyph``
        toggles — we don't need a full re-scan of drives, just a
        re-format of the existing entries.  Preserves selection.
        """
        if not self.drive_options:
            return
        include_glyph = bool(self._cfg.get("opt_drive_state_glyph", True))
        labels = [
            format_drive_label(d, include_state_glyph=include_glyph)
            for d in self.drive_options
        ]
        combo = self._window.drive_combo
        prior_index = combo.currentIndex()
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItems(labels)
            if 0 <= prior_index < len(labels):
                combo.setCurrentIndex(prior_index)
        finally:
            combo.blockSignals(False)

    # ------------------------------------------------------------------
    # User picked a drive in the combo
    # ------------------------------------------------------------------

    def _on_combo_changed(self, index: int) -> None:
        """Persist the newly-selected drive index to cfg.

        Mirrors ``_on_drive_select`` at ``gui/main_window.py:4002``.
        """
        if index < 0 or index >= len(self.drive_options):
            return
        drive = self.drive_options[index]
        self._cfg["opt_drive_index"] = drive.index
        if self._save_cfg is not None:
            try:
                self._save_cfg(self._cfg)
            except Exception as e:  # noqa: BLE001
                self._window.append_log(f"Could not save drive selection: {e}")
        label = format_drive_label(
            drive,
            include_state_glyph=bool(self._cfg.get("opt_drive_state_glyph", True)),
        )
        self._window.append_log(f"Drive selected: {label}")

    # ------------------------------------------------------------------
    # Default scanner — lazy-imports the engine helpers
    # ------------------------------------------------------------------

    def _default_scanner(self) -> Sequence[Any]:
        """Default scanner used when no override is passed at
        construction.  Resolves ``makemkvcon`` via
        ``config.resolve_makemkvcon`` and calls
        ``utils.helpers.get_available_drives``.  Lazy imports keep
        this module's import chain decoupled from those modules
        until actual use."""
        import os
        from config import resolve_makemkvcon
        from utils.helpers import get_available_drives

        makemkv_path = os.path.normpath(
            self._cfg.get("makemkvcon_path", "")
        )
        # ``allow_path_lookup`` matches tkinter's
        # ``_allow_path_tool_resolution`` — enabled by default in cfg.
        allow = bool(
            self._cfg.get("opt_allow_path_tool_resolution", True),
        )
        makemkvcon = resolve_makemkvcon(
            makemkv_path, allow_path_lookup=allow,
        )
        return get_available_drives(makemkvcon.path)
