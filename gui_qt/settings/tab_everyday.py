"""Everyday settings tab — common rip + library behavior toggles.

Exposes the cfg keys most users adjust frequently:

* **Logs**
    - ``opt_save_logs``                       — write rip log to disk
* **Confirmations**
    - ``opt_confirm_before_rip``              — prompt before starting a rip
    - ``opt_confirm_before_move``             — prompt before moving to library
* **Temp folder**
    - ``opt_show_temp_manager``               — open the temp manager when needed
    - ``opt_auto_delete_temp``                — wipe temp/ after a successful rip
    - ``opt_auto_delete_session_metadata``    — wipe session metadata after a rip
    - ``opt_clean_partials_startup``          — clean stale `.partial` files at launch
* **Library / naming**
    - ``opt_warn_out_of_order_episodes``      — warn if TV episode order looks off
    - ``opt_naming_mode``                     — disc-name vs timestamp folder naming
    - ``opt_extras_folder_mode``              — single vs per-category extras folders
    - ``opt_bonus_folder_name``               — top-level bonus-content folder name
* **Workflow**
    - ``opt_smart_rip_mode``                  — auto-pick titles based on classifier
    - ``opt_smart_min_minutes``               — duration floor for smart-rip auto-pick
    - ``opt_session_failure_report``          — generate end-of-run failure report
    - ``opt_allow_path_tool_resolution``      — let tool resolver consult PATH

Same OK/Cancel + snapshot lifecycle as ``tab_paths.py`` and
``tab_appearance.py``: edits live on the widgets until OK; Cancel
resets them to the snapshot.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


_NAMING_MODE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("timestamp", "Timestamped folder"),
    ("disc",      "Disc name"),
)

_EXTRAS_MODE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("single",   "Single Extras folder"),
    ("category", "Per-category folders"),
)


class EverydayTab(QWidget):
    """Form layout with grouped sections for common settings."""

    def __init__(
        self,
        cfg: dict[str, Any],
        save_cfg: Callable[[Mapping[str, Any]], None] | None = None,
        parent: "QWidget | None" = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("settingsTabEveryday")

        self._cfg = cfg
        self._save_cfg = save_cfg

        # Snapshot every key we own so cancel() can revert the
        # widget state.  cfg is never touched until apply().
        self._snapshot: dict[str, Any] = {}
        # Map of cfg-key → widget so apply()/cancel() walk uniformly.
        self._checkboxes: dict[str, QCheckBox] = {}
        self._combos: dict[str, QComboBox] = {}
        self._spinboxes: dict[str, QSpinBox] = {}
        self._lineedits: dict[str, QLineEdit] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        intro = QLabel(
            "Common rip and library behavior.  Each setting takes effect "
            "the next time the relevant workflow runs — no restart needed."
        )
        intro.setObjectName("settingsEverydayIntro")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        form_host = QFrame()
        form_host.setObjectName("settingsEverydayFormHost")
        form = QFormLayout(form_host)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)

        # ── Logs ────────────────────────────────────────────────────
        outer.addWidget(self._section_label("Logs"))
        self._add_checkbox(
            form, "opt_save_logs", "Save rip log file",
            default=True,
        )
        outer.addWidget(form_host, stretch=0)

        # New form for next section to give it visual separation.
        form2_host = QFrame()
        form2 = QFormLayout(form2_host)
        form2.setContentsMargins(0, 0, 0, 0)
        form2.setSpacing(8)
        outer.addWidget(self._section_label("Confirmations"))
        self._add_checkbox(
            form2, "opt_confirm_before_rip",
            "Confirm before starting a rip", default=True,
        )
        self._add_checkbox(
            form2, "opt_confirm_before_move",
            "Confirm before moving files into the library", default=True,
        )
        outer.addWidget(form2_host)

        # ── Temp folder ────────────────────────────────────────────
        form3_host = QFrame()
        form3 = QFormLayout(form3_host)
        form3.setContentsMargins(0, 0, 0, 0)
        form3.setSpacing(8)
        outer.addWidget(self._section_label("Temp folder"))
        self._add_checkbox(
            form3, "opt_show_temp_manager",
            "Show the temp folder manager when temp accumulates",
            default=True,
        )
        self._add_checkbox(
            form3, "opt_auto_delete_temp",
            "Auto-delete temp folder after a successful rip",
            default=True,
        )
        self._add_checkbox(
            form3, "opt_auto_delete_session_metadata",
            "Auto-delete session metadata after a successful rip",
            default=True,
        )
        self._add_checkbox(
            form3, "opt_clean_partials_startup",
            "Clean stale .partial files at launch",
            default=True,
        )
        outer.addWidget(form3_host)

        # ── Library / naming ───────────────────────────────────────
        form4_host = QFrame()
        form4 = QFormLayout(form4_host)
        form4.setContentsMargins(0, 0, 0, 0)
        form4.setSpacing(8)
        outer.addWidget(self._section_label("Library / naming"))
        self._add_checkbox(
            form4, "opt_warn_out_of_order_episodes",
            "Warn if TV episodes look out of order",
            default=True,
        )
        self._add_combo(
            form4, "opt_naming_mode", "Folder naming:",
            _NAMING_MODE_OPTIONS, default="timestamp",
        )
        self._add_combo(
            form4, "opt_extras_folder_mode", "Extras layout:",
            _EXTRAS_MODE_OPTIONS, default="single",
        )
        self._add_lineedit(
            form4, "opt_bonus_folder_name", "Bonus folder name:",
            default="featurettes",
        )
        outer.addWidget(form4_host)

        # ── Workflow ──────────────────────────────────────────────
        form5_host = QFrame()
        form5 = QFormLayout(form5_host)
        form5.setContentsMargins(0, 0, 0, 0)
        form5.setSpacing(8)
        outer.addWidget(self._section_label("Workflow"))
        self._add_checkbox(
            form5, "opt_smart_rip_mode",
            "Smart rip mode (auto-pick titles based on classifier)",
            default=False,
        )
        self._add_spinbox(
            form5, "opt_smart_min_minutes",
            "Smart-rip duration floor (minutes):",
            default=20, minimum=0, maximum=600,
        )
        self._add_checkbox(
            form5, "opt_session_failure_report",
            "Generate end-of-run failure report",
            default=True,
        )
        self._add_checkbox(
            form5, "opt_allow_path_tool_resolution",
            "Allow PATH-based tool lookup (advanced, less predictable)",
            default=False,
        )
        outer.addWidget(form5_host)

        outer.addStretch(1)

    # ── Section + widget builders ──────────────────────────────────

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("settingsEverydaySection")
        f = label.font()
        f.setBold(True)
        f.setPointSize(f.pointSize() + 1)
        label.setFont(f)
        return label

    def _add_checkbox(
        self, form: QFormLayout, key: str, label: str, *, default: bool,
    ) -> None:
        current = bool(self._cfg.get(key, default))
        self._snapshot[key] = current
        cb = QCheckBox()
        cb.setObjectName(f"settingsCheck_{key}")
        cb.setChecked(current)
        host = QHBoxLayout()
        host.setContentsMargins(0, 0, 0, 0)
        host.setSpacing(6)
        host.addWidget(cb)
        host.addWidget(QLabel(label))
        host.addStretch(1)
        wrap = QWidget()
        wrap.setLayout(host)
        form.addRow("", wrap)
        self._checkboxes[key] = cb

    def _add_combo(
        self,
        form: QFormLayout,
        key: str,
        label: str,
        options: tuple[tuple[str, str], ...],
        *,
        default: str,
    ) -> None:
        current = str(self._cfg.get(key, default) or default)
        self._snapshot[key] = current
        combo = QComboBox()
        combo.setObjectName(f"settingsCombo_{key}")
        for value, display in options:
            combo.addItem(display, userData=value)
        # Pre-select current value.
        for idx, (value, _display) in enumerate(options):
            if value == current:
                combo.setCurrentIndex(idx)
                break
        form.addRow(label, combo)
        self._combos[key] = combo

    def _add_spinbox(
        self,
        form: QFormLayout,
        key: str,
        label: str,
        *,
        default: int,
        minimum: int,
        maximum: int,
    ) -> None:
        try:
            current = int(self._cfg.get(key, default))
        except (TypeError, ValueError):
            current = default
        current = max(minimum, min(maximum, current))
        self._snapshot[key] = current
        spin = QSpinBox()
        spin.setObjectName(f"settingsSpin_{key}")
        spin.setRange(minimum, maximum)
        spin.setValue(current)
        form.addRow(label, spin)
        self._spinboxes[key] = spin

    def _add_lineedit(
        self, form: QFormLayout, key: str, label: str, *, default: str,
    ) -> None:
        current = str(self._cfg.get(key, default) or default)
        self._snapshot[key] = current
        edit = QLineEdit(current)
        edit.setObjectName(f"settingsEdit_{key}")
        form.addRow(label, edit)
        self._lineedits[key] = edit

    # ── Dialog hooks ───────────────────────────────────────────────

    def apply(self) -> None:
        """Write every widget's current value into cfg + persist."""
        for key, cb in self._checkboxes.items():
            self._cfg[key] = bool(cb.isChecked())
        for key, combo in self._combos.items():
            data = combo.currentData()
            self._cfg[key] = str(data) if data is not None else ""
        for key, spin in self._spinboxes.items():
            self._cfg[key] = int(spin.value())
        for key, edit in self._lineedits.items():
            self._cfg[key] = edit.text().strip()
        if self._save_cfg is not None:
            try:
                self._save_cfg(self._cfg)
            except Exception:
                pass

    def cancel(self) -> None:
        """Reset every widget to the snapshot taken at construction.
        cfg is never touched on cancel; this only repaints the
        widgets so re-opening Settings shows the saved state, not
        the user's abandoned edits."""
        for key, cb in self._checkboxes.items():
            cb.setChecked(bool(self._snapshot.get(key, False)))
        for key, combo in self._combos.items():
            target = self._snapshot.get(key, "")
            for idx in range(combo.count()):
                if combo.itemData(idx) == target:
                    combo.setCurrentIndex(idx)
                    break
        for key, spin in self._spinboxes.items():
            try:
                spin.setValue(int(self._snapshot.get(key, spin.minimum())))
            except Exception:
                pass
        for key, edit in self._lineedits.items():
            edit.setText(str(self._snapshot.get(key, "")))
