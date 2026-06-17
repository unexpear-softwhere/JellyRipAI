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

from gui_qt.tmdb_logo import tmdb_logo_pixmap


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
        # NOTE: the local *model* is chosen in the AI Providers dialog —
        # a dropdown of installed Ollama models, which is the single
        # source of truth (the provider credential's "model").  A second
        # free-text field here only diverged from that dropdown and broke
        # it, so it is intentionally not offered in Settings.
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

        # ── Web lookup ──────────────────────────────────────────────
        outer.addWidget(self._section_label("Web lookup"))
        web_hint = QLabel(
            "The chat's Web toggle searches DuckDuckGo with no key needed. "
            "Add a free TMDB API key (themoviedb.org - Settings - API, the "
            "v3 \"API Key\") to also pull exact movie/show titles and IDs.\n"
            "This product uses TMDB and the TMDB APIs but is not endorsed, "
            "certified, or otherwise approved by TMDB."
        )
        web_hint.setObjectName("settingsAIWebHint")
        web_hint.setWordWrap(True)
        outer.addWidget(web_hint)
        # TMDB attribution logo — required by TMDB's API terms.  Shown
        # smaller than JellyRip's own branding, with the non-endorsement
        # notice in the hint above + a tooltip.
        _tmdb_logo = QLabel()
        _tmdb_logo.setObjectName("settingsTmdbLogo")
        _tmdb_pixmap = tmdb_logo_pixmap(18)
        if not _tmdb_pixmap.isNull():
            _tmdb_logo.setPixmap(_tmdb_pixmap)
            _tmdb_logo.setToolTip(
                "This product uses TMDB and the TMDB APIs but is not "
                "endorsed, certified, or otherwise approved by TMDB."
            )
            outer.addWidget(_tmdb_logo)
        web_host = QFrame()
        web_host.setObjectName("settingsAIWebHost")
        web_form = QFormLayout(web_host)
        web_form.setContentsMargins(0, 0, 0, 0)
        web_form.setSpacing(8)
        self._add_lineedit(
            web_form, "opt_tmdb_api_key", "TMDB API key (optional)", default="",
        )
        self._add_lineedit(
            web_form, "opt_omdb_api_key", "OMDb API key (optional)", default="",
        )
        self._add_lineedit(
            web_form, "opt_tvdb_api_key",
            "TheTVDB API key (optional, paid)", default="",
        )
        self._add_lineedit(
            web_form, "opt_tvdb_pin",
            "TheTVDB subscriber PIN", default="",
        )
        outer.addWidget(web_host)

        # OMDb (omdbapi.com) is an optional second source — a free key
        # (1,000 lookups/day) adds IMDb IDs.  Its data is CC BY-NC, so we
        # show a credit line (no logo is required, unlike TMDB).  When both
        # keys are set the assistant queries both.
        omdb_hint = QLabel(
            "OMDb (omdbapi.com) is an optional second lookup — a free key "
            "adds IMDb IDs.  When both a TMDB and an OMDb key are set, the "
            "assistant uses both.\n"
            "Movie data provided by OMDb, licensed CC BY-NC 4.0.  JellyRip "
            "is not affiliated with OMDb or IMDb."
        )
        omdb_hint.setObjectName("settingsAIOmdbHint")
        omdb_hint.setWordWrap(True)
        outer.addWidget(omdb_hint)

        # TV-specialized sources.  TVmaze is free + keyless (always on);
        # TheTVDB is the optional paid one (key + PIN above).
        tv_hint = QLabel(
            "TV shows: TVmaze (free, no key — always on) is used as a "
            "TV-specialized backup, so series identify even with no keys "
            "set.  TheTVDB is an optional extra TV source, but its API is "
            "paid: it needs a key plus, for user-supported keys, a "
            "subscriber PIN (thetvdb.com — $11.99/yr).  Leave both blank "
            "to skip TheTVDB and rely on the free sources.\n"
            "JellyRip is not affiliated with TVmaze or TheTVDB."
        )
        tv_hint.setObjectName("settingsAITvHint")
        tv_hint.setWordWrap(True)
        outer.addWidget(tv_hint)

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
            except Exception as exc:
                # Persist failure shouldn't crash the dialog (the in-
                # memory cfg already reflects the user's choices), but
                # the user deserves to know it didn't save.  Log to
                # session log; failure-mode usually disk-full or
                # locked config.json.
                import logging
                logging.warning(
                    "Settings (AI tab): failed to persist cfg: %s", exc,
                )

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
