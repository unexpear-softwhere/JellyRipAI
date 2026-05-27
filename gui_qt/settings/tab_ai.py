"""AI settings tab — exposes the previously-hidden opt_ai_* knobs.

Prior to 2026-05-08 (audit #18), about 19 ``opt_ai_*`` keys lived
in ``DEFAULTS`` but had no UI surface anywhere.  Users could only
flip diagnostics, change timeouts, or cap per-session call counts
by hand-editing ``%APPDATA%\\JellyRipAI\\config.json``.

This tab exposes the user-facing knobs.  Internal state keys
(``opt_ai_active_cloud_provider``, ``opt_ai_profile``,
``opt_ai_profile_onboarded``, ``opt_ai_sidebar_open``,
``opt_ai_sidebar_width``, ``opt_ai_mode``) are deliberately NOT
exposed here — those are managed by the AI Providers dialog and
the chat sidebar's own controls.

Mirrors the OK/Cancel + snapshot lifecycle of the other tabs:
edits live on the widgets until the user clicks OK; Cancel resets
the widgets to the snapshot taken at construction.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

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


_LOCAL_PROVIDER_OPTIONS: tuple[tuple[str, str], ...] = (
    ("ollama", "Ollama (HTTP)"),
)


class AITab(QWidget):
    """AI settings — diagnostics toggles, backend enables, timeouts, caps."""

    def __init__(
        self,
        cfg: dict[str, Any],
        save_cfg: Callable[[Mapping[str, Any]], None] | None = None,
        parent: "QWidget | None" = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("settingsTabAI")

        self._cfg = cfg
        self._save_cfg = save_cfg

        self._snapshot: dict[str, Any] = {}
        self._checkboxes: dict[str, QCheckBox] = {}
        self._combos: dict[str, QComboBox] = {}
        self._spinboxes: dict[str, QSpinBox] = {}
        self._lineedits: dict[str, QLineEdit] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        intro = QLabel(
            "Control how JellyRip's AI assistant features behave.  These "
            "settings take effect on the next AI request — no restart "
            "needed.  API keys and active-provider selection are managed "
            "separately via the ✦ AI Providers utility chip."
        )
        intro.setObjectName("settingsAIIntro")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        # ── Diagnostics ─────────────────────────────────────────────
        outer.addWidget(self._section_label("Diagnostics"))
        diag_host = QFrame()
        diag_host.setObjectName("settingsAIDiagnosticsHost")
        diag_form = QFormLayout(diag_host)
        diag_form.setContentsMargins(0, 0, 0, 0)
        diag_form.setSpacing(8)
        self._add_checkbox(
            diag_form, "opt_ai_diagnostics_enabled",
            "Send rip failures to the AI for plain-English explanations",
            default=True,
        )
        self._add_checkbox(
            diag_form, "opt_ai_log_to_gui",
            "Show AI diagnostics in the live log pane",
            default=True,
        )
        self._add_checkbox(
            diag_form, "opt_ai_log_to_file",
            "Write AI diagnostics to the session AI log on disk",
            default=True,
        )
        self._add_checkbox(
            diag_form, "opt_ai_capture_raw_process_output",
            "Include raw MakeMKV / ffprobe stdout+stderr in AI payloads",
            default=True,
        )
        self._add_checkbox(
            diag_form, "opt_ai_emit_state_json",
            "Include workflow state JSON in AI payloads",
            default=True,
        )
        outer.addWidget(diag_host)

        # ── Backends ────────────────────────────────────────────────
        outer.addWidget(self._section_label("Backends"))
        backend_host = QFrame()
        backend_form = QFormLayout(backend_host)
        backend_form.setContentsMargins(0, 0, 0, 0)
        backend_form.setSpacing(8)
        self._add_checkbox(
            backend_form, "opt_ai_cloud_enabled",
            "Allow the cloud provider (Claude / OpenAI / Gemini) when configured",
            default=True,
        )
        self._add_checkbox(
            backend_form, "opt_ai_local_enabled",
            "Allow the local provider (Ollama) when running",
            default=True,
        )
        self._add_combo(
            backend_form, "opt_ai_local_provider",
            "Local provider",
            _LOCAL_PROVIDER_OPTIONS,
            default="ollama",
        )
        self._add_lineedit(
            backend_form, "opt_ai_local_model",
            "Local model name",
            default="qwen2.5:14b-instruct",
        )
        outer.addWidget(backend_host)

        # ── Limits ──────────────────────────────────────────────────
        outer.addWidget(self._section_label("Limits"))
        limit_host = QFrame()
        limit_form = QFormLayout(limit_host)
        limit_form.setContentsMargins(0, 0, 0, 0)
        limit_form.setSpacing(8)
        self._add_spinbox(
            limit_form, "opt_ai_cloud_timeout_seconds",
            "Cloud-provider timeout (seconds)",
            default=30, minimum=5, maximum=300,
        )
        self._add_spinbox(
            limit_form, "opt_ai_local_timeout_seconds",
            "Local-provider timeout (seconds)",
            default=90, minimum=5, maximum=600,
        )
        self._add_spinbox(
            limit_form, "opt_ai_max_calls_per_session",
            "Max AI calls per session",
            default=20, minimum=1, maximum=200,
        )
        self._add_spinbox(
            limit_form, "opt_ai_disable_after_failures",
            "Disable provider after N consecutive failures",
            default=3, minimum=1, maximum=20,
        )
        outer.addWidget(limit_host)

        outer.addStretch(1)

    # ── Section + widget builders ──────────────────────────────────

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("settingsAISection")
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
        """Commit widget values into cfg and persist to disk."""
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
        """Restore every widget to the construction-time snapshot.

        cfg is never touched on cancel; this only repaints the
        widgets so reopening Settings shows the saved state, not
        whatever the user previewed.
        """
        for key, cb in self._checkboxes.items():
            cb.setChecked(bool(self._snapshot.get(key, cb.isChecked())))
        for key, combo in self._combos.items():
            target = self._snapshot.get(key)
            for idx in range(combo.count()):
                if combo.itemData(idx) == target:
                    combo.setCurrentIndex(idx)
                    break
        for key, spin in self._spinboxes.items():
            spin.setValue(int(self._snapshot.get(key, spin.value())))
        for key, edit in self._lineedits.items():
            edit.setText(str(self._snapshot.get(key, edit.text())))
