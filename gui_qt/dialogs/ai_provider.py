"""AI Provider Connection dialog — Qt (PySide6) port.

Replaces ``gui/ai_provider_dialog.py`` (tkinter) for AI BRANCH's
Phase 4 Qt migration.  Same public contract:

    open_ai_provider_dialog(parent, on_change=None)

Modal QDialog that lets the user manage cloud + local AI provider
connections — set API keys, pick models, test connections, choose the
active cloud provider, disconnect.  All provider-side logic still
lives in ``shared.ai`` (the registry, credential store, provider
adapters); this module is the UI shell only.

Theming: the dialog and its child widgets carry objectNames so the
existing QSS files (``gui_qt/qss/*.qss``) style it consistently with
the rest of the app.

Pure helpers (``_format_model_cost``, ``_model_power_score``,
``_sort_models_by_power``, ``_resolve_local_model_selection``,
``_classify_connection_error``) are lifted verbatim from the tkinter
original — they're Qt-free and the existing tests in
``tests/test_ai_provider_dialog.py`` import them.
"""

from __future__ import annotations

import re
import threading
import webbrowser
from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from shared.runtime import APP_DISPLAY_NAME


# ─── Pricing table (display-only, per 1M tokens) ──────────────────────
# Lifted verbatim from gui/ai_provider_dialog.py.  Treat as cached
# display data; never imported by engine/runtime code.
_PRICING_LAST_UPDATED = "2026-05"
# fmt: off
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    # model_id:                         (input $/1M,  output $/1M)
    # Claude — IDs realigned 2026-05-08 against the live lineup.
    "claude-opus-4-7":                  (15.00,  75.00),
    "claude-sonnet-4-6":                ( 3.00,  15.00),
    "claude-haiku-4-5-20251001":        ( 0.80,   4.00),
    # OpenAI
    "gpt-4o":                           (2.50,   10.00),
    "gpt-4o-mini":                      (0.15,    0.60),
    "gpt-4.1-mini":                     (0.40,    1.60),
    "gpt-4.1-nano":                     (0.10,    0.40),
    # Gemini
    "gemini-2.5-flash":                 (0.15,    0.60),
    "gemini-2.0-flash":                 (0.10,    0.40),
    "gemini-2.0-flash-lite":            (0.075,   0.30),
}
# fmt: on

# Typical diagnostic call: ~1.5K input tokens, ~400 output tokens.
_TYPICAL_INPUT_TOKENS = 1500
_TYPICAL_OUTPUT_TOKENS = 400

_MODEL_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)b\b")
_QUOTA_ERROR_PATTERNS = (
    "quota",
    "rate_limit",
    "rate limit",
    "too many requests",
    "429",
    "token",
    "insufficient_quota",
    "billing",
    "exceeded",
    "resource_exhausted",
    "overloaded",
)


# ─── Pure helpers (Qt-free, testable without a display) ──────────────


def _format_model_cost(model_id: str) -> str | None:
    """Return a short cost string for a model, or None if unknown/free."""
    pricing = _MODEL_PRICING.get(model_id)
    if pricing is None:
        return None
    input_per_m, output_per_m = pricing
    est = (_TYPICAL_INPUT_TOKENS * input_per_m + _TYPICAL_OUTPUT_TOKENS * output_per_m) / 1_000_000
    if est < 0.001:
        est_str = "<$0.001"
    else:
        est_str = f"~${est:.4f}"
    return (
        f"${input_per_m:g} / ${output_per_m:g} per 1M tok  •  {est_str}/call"
        f"  (as of {_PRICING_LAST_UPDATED})"
    )


def _model_power_score(model_id: str) -> float:
    """Heuristic score so stronger models appear first in dropdowns."""
    lowered = str(model_id or "").strip().lower()
    if not lowered:
        return float("-inf")

    size_matches = [float(match) for match in _MODEL_SIZE_RE.findall(lowered)]
    largest_size = max(size_matches) if size_matches else 0.0

    def _version_value(text: str) -> float:
        tokens = re.findall(r"\d+(?:\.\d+)?", text)
        if not tokens:
            return 0.0
        return sum(float(token) / (10 ** idx) for idx, token in enumerate(tokens[:2]))

    def _claude_version_value(text: str) -> float:
        parts = text.split("-")
        if len(parts) < 3:
            return 0.0
        version_token = parts[2].lstrip("v")
        try:
            return float(version_token)
        except (TypeError, ValueError):
            return 0.0

    family_score: float
    version_score: float

    if lowered.startswith("claude"):
        if "opus" in lowered:
            family_score = 30.0
        elif "sonnet" in lowered:
            family_score = 20.0
        elif "haiku" in lowered:
            family_score = 10.0
        else:
            family_score = 5.0
        version_score = _claude_version_value(lowered)
    elif "gpt" in lowered:
        family_score = 20.0 if "gpt-4" in lowered else 5.0
        version_score = _version_value(lowered)
        if "mini" in lowered or "nano" in lowered:
            version_score -= 0.2
    elif "gemini" in lowered:
        family_score = 18.0
        version_score = _version_value(lowered)
        if "lite" in lowered:
            version_score -= 0.3
    else:
        family_score = 0.0
        version_score = _version_value(lowered)

    return family_score + version_score + (largest_size * 0.5)


def _sort_models_by_power(models: list[str]) -> list[str]:
    """Sort models stronger-first, deduping while preserving order."""
    seen: set[str] = set()
    deduped: list[str] = []
    for model in models:
        clean = str(model or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        deduped.append(clean)
    return sorted(deduped, key=_model_power_score, reverse=True)


def _resolve_local_model_selection(
    current: str,
    available: list[str],
) -> tuple[list[str], str]:
    """Pick a sane local-model default when ``current`` isn't installed."""
    cleaned_available = [str(m).strip() for m in available if str(m).strip()]
    cleaned_current = str(current or "").strip()
    if cleaned_current and cleaned_current in cleaned_available:
        return cleaned_available, cleaned_current
    if cleaned_available:
        return cleaned_available, cleaned_available[0]
    return [], cleaned_current


def _classify_connection_error(error_text: str) -> tuple[str, str]:
    """Map raw provider test errors to clearer dialog states and guidance.

    Lifted verbatim from ``gui/ai_provider_dialog.py`` — same return
    shape so the existing tkinter-era tests work against the Qt
    module unchanged.
    """
    raw = str(error_text or "").strip()
    lowered = raw.lower()
    if any(pattern in lowered for pattern in _QUOTA_ERROR_PATTERNS):
        return (
            "rate_limited",
            "Rate limited / out of quota. Check billing, usage caps, or retry later.",
        )
    return ("failed", raw[:80])


# ─── Qt UI shell ─────────────────────────────────────────────────────


_STATUS_MAP: dict[str, tuple[str, str]] = {
    # state              -> (text, qss-state)
    "active":        ("● Active",                 "active"),
    "connected":     ("● Connected",              "connected"),
    "validating":    ("● Validating…",       "validating"),
    "rate_limited":  ("● Rate limited / quota",   "rate-limited"),
    "failed":        ("● Failed",                 "failed"),
    "not_connected": ("● Not connected",          "not-connected"),
}


class AIProviderDialog(QDialog):
    """Modal Qt dialog for managing AI provider connections.

    Same surface as the tkinter original — call ``exec()`` (or just
    use ``open_ai_provider_dialog``) to show it.  Construction is
    cheap; all provider data is loaded from ``shared.ai`` lazily inside
    the build pass.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_change = on_change
        self._provider_widgets: dict[str, dict[str, Any]] = {}
        self._setup_hints: dict[str, QFrame] = {}

        self.setObjectName("aiProviderDialog")
        self.setWindowTitle("AI Provider Setup")
        # Window-modal so the standalone AI chat window stays usable
        # while configuring providers.
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(780, 700)
        self.setMinimumSize(720, 560)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(8)

        # Header
        header = QLabel("AI Provider Connections")
        header.setObjectName("aiProviderHeader")
        header_font = QFont()
        header_font.setPointSize(14)
        header_font.setBold(True)
        header.setFont(header_font)
        outer.addWidget(header)

        subtitle = QLabel(
            f"Configure which AI backends {APP_DISPLAY_NAME} can use for diagnostics."
        )
        subtitle.setObjectName("aiProviderSubtitle")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        # Scrollable area for provider cards
        self._scroll_area = QScrollArea()
        self._scroll_area.setObjectName("aiProviderScroll")
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        self._cards_host = QWidget()
        self._cards_host.setObjectName("aiProviderCards")
        self._cards_layout = QVBoxLayout(self._cards_host)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(8)
        self._cards_layout.addStretch(1)

        self._scroll_area.setWidget(self._cards_host)
        outer.addWidget(self._scroll_area, stretch=1)

        # Bottom button row
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.setObjectName("cancelButton")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        outer.addLayout(btn_row)

        self._build_provider_cards()

    # ── Card construction ──────────────────────────────────────────

    def _build_provider_cards(self) -> None:
        from shared.ai.provider_registry import (
            get_connection_summary,
            list_providers,
        )

        providers = list_providers()
        summary = get_connection_summary()

        # Insert before the trailing stretch so cards stack at the top.
        insert_idx = self._cards_layout.count() - 1
        for info in providers:
            pid = info.id
            status = summary.get(pid, {})
            card = self._build_single_card(info, status)
            self._cards_layout.insertWidget(insert_idx, card)
            insert_idx += 1

    def _refresh_provider_cards(self) -> None:
        # Strip everything except the trailing stretch.
        while self._cards_layout.count() > 1:
            item = self._cards_layout.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.deleteLater()
        self._provider_widgets.clear()
        self._setup_hints.clear()
        self._build_provider_cards()

    def _build_single_card(self, info: Any, status: dict[str, Any]) -> QFrame:
        from shared.ai.credential_store import (
            get_provider_credentials,
            is_encrypted_storage,
        )

        pid = info.id
        creds = get_provider_credentials(pid)

        card = QFrame()
        card.setObjectName("aiProviderCard")
        card.setFrameShape(QFrame.Shape.StyledPanel)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 8, 12, 8)
        card_layout.setSpacing(4)

        widgets: dict[str, Any] = {"card": card}

        # ── Title row ──────────────────────────────────────────────
        title_row = QHBoxLayout()
        title_label = QLabel(info.display_name)
        title_label.setObjectName("aiProviderCardTitle")
        title_font = QFont()
        title_font.setPointSize(11)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_row.addWidget(title_label)

        category_label = QLabel(info.category.upper())
        category_label.setObjectName("aiProviderCardCategory")
        category_label.setProperty(
            "category", "cloud" if info.category == "cloud" else "local",
        )
        title_row.addWidget(category_label)
        title_row.addStretch(1)

        # Status indicator (right-aligned).
        is_active = bool(status.get("is_active"))
        has_creds = bool(status.get("has_credentials"))
        if is_active and has_creds:
            init_state = "active"
        elif has_creds:
            init_state = "connected"
        else:
            init_state = "not_connected"
        init_text, init_qss_state = _STATUS_MAP[init_state]

        status_label = QLabel(init_text)
        status_label.setObjectName("aiProviderCardStatus")
        status_label.setProperty("state", init_qss_state)
        title_row.addWidget(status_label)
        widgets["status_label"] = status_label

        card_layout.addLayout(title_row)

        # ── Credential row (API key OR base URL) ──────────────────
        cred_row = QHBoxLayout()
        if info.requires_api_key:
            cred_row.addWidget(self._field_label("API Key:"))
            key_edit = QLineEdit(creds.get("api_key", ""))
            key_edit.setObjectName("aiProviderApiKey")
            key_edit.setEchoMode(QLineEdit.EchoMode.Password)
            cred_row.addWidget(key_edit, stretch=1)

            show_btn = QPushButton("Show")
            show_btn.setObjectName("aiProviderShowKeyButton")
            show_btn.setCheckable(True)
            show_btn.toggled.connect(
                lambda checked, e=key_edit: e.setEchoMode(
                    QLineEdit.EchoMode.Normal if checked
                    else QLineEdit.EchoMode.Password
                )
            )
            cred_row.addWidget(show_btn)
            widgets["key_edit"] = key_edit
            widgets["show_btn"] = show_btn

            card_layout.addLayout(cred_row)

            # Security indicator (only when a key is saved).
            if has_creds:
                sec_text = (
                    "\U0001f512 Stored securely (Windows encrypted)"
                    if is_encrypted_storage()
                    else "\U0001f513 Stored locally (plaintext)"
                )
                sec_label = QLabel(sec_text)
                sec_label.setObjectName("aiProviderSecurityIndicator")
                sec_label.setProperty(
                    "secure",
                    "encrypted" if is_encrypted_storage() else "plaintext",
                )
                card_layout.addWidget(sec_label)
                widgets["security_label"] = sec_label
        else:
            cred_row.addWidget(self._field_label("URL:"))
            url_edit = QLineEdit(
                creds.get("base_url", "http://localhost:11434")
            )
            url_edit.setObjectName("aiProviderBaseUrl")
            cred_row.addWidget(url_edit, stretch=1)
            widgets["url_edit"] = url_edit
            card_layout.addLayout(cred_row)

        # ── Model selector row ────────────────────────────────────
        model_row = QHBoxLayout()
        model_row.addWidget(self._field_label("Model:"))

        if info.category == "local":
            current_model = str(creds.get("model", "") or "").strip()
            model_options, current_model = _resolve_local_model_selection(
                current_model,
                list(info.available_models),
            )
            display_options = list(model_options) or ["No installed local models"]
        else:
            current_model = creds.get("model", info.default_model) or info.default_model
            model_options = _sort_models_by_power(
                [current_model, *info.available_models]
            )
            if not model_options:
                model_options = [info.default_model]
            display_options = list(model_options)

        model_combo = QComboBox()
        model_combo.setObjectName("aiProviderModelCombo")
        model_combo.addItems(display_options)
        if current_model in display_options:
            model_combo.setCurrentText(current_model)
        if info.category == "local" and not info.available_models:
            model_combo.setEnabled(False)
        model_row.addWidget(model_combo)
        widgets["model_combo"] = model_combo

        # Pricing label (cloud providers only).
        if info.category == "cloud":
            cost_label = QLabel("")
            cost_label.setObjectName("aiProviderCostLabel")
            model_row.addWidget(cost_label)
            widgets["cost_label"] = cost_label

            def _refresh_cost(text: str = "", *, _cl=cost_label, _mc=model_combo) -> None:
                _cl.setText(_format_model_cost(_mc.currentText()) or "")

            model_combo.currentTextChanged.connect(_refresh_cost)
            _refresh_cost()  # initial value

        model_row.addStretch(1)
        card_layout.addLayout(model_row)

        if info.category == "local":
            local_note = (
                "Only pulled Ollama models are listed."
                if info.available_models
                else "No pulled Ollama models were detected on this PC at this URL yet."
            )
            note_label = QLabel(local_note)
            note_label.setObjectName("aiProviderLocalNote")
            card_layout.addWidget(note_label)

        # ── Action button row ─────────────────────────────────────
        btn_row = QHBoxLayout()
        test_btn = QPushButton("Test")
        test_btn.setObjectName("aiProviderTestButton")
        test_btn.clicked.connect(lambda _checked=False, p=pid: self._test_provider(p))
        btn_row.addWidget(test_btn)

        save_btn = QPushButton("Save")
        save_btn.setObjectName("aiProviderSaveButton")
        save_btn.clicked.connect(lambda _checked=False, p=pid: self._save_provider(p))
        btn_row.addWidget(save_btn)

        # "Set as Active" on every provider (cloud + local).  Local
        # used to be selected via the chat sidebar's old AI-mode combo;
        # with that combo replaced by a model dropdown, the only place
        # to choose local-vs-cloud is here, so the Local card needs this
        # button too.
        set_active_btn = QPushButton("Set as Active")
        set_active_btn.setObjectName("aiProviderSetActiveButton")
        set_active_btn.setProperty("active", bool(is_active))
        set_active_btn.clicked.connect(
            lambda _checked=False, p=pid: self._set_active(p)
        )
        btn_row.addWidget(set_active_btn)
        widgets["set_active_btn"] = set_active_btn

        if has_creds:
            disconnect_btn = QPushButton("Disconnect")
            disconnect_btn.setObjectName("aiProviderDisconnectButton")
            disconnect_btn.clicked.connect(
                lambda _checked=False, p=pid: self._disconnect_provider(p)
            )
            btn_row.addWidget(disconnect_btn)

        btn_row.addStretch(1)

        # Detail line (latency / error excerpt) — right-aligned.
        detail_label = QLabel("")
        detail_label.setObjectName("aiProviderDetailLabel")
        btn_row.addWidget(detail_label)
        widgets["detail_label"] = detail_label

        # Help link — opens provider page in default browser.
        if getattr(info, "help_url", ""):
            link_text = (
                "Get API key →" if info.requires_api_key else "Setup guide →"
            )
            help_btn = QPushButton(link_text)
            help_btn.setObjectName("aiProviderHelpLink")
            help_btn.setFlat(True)
            help_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            help_btn.clicked.connect(
                lambda _checked=False, p=pid, url=info.help_url: self._open_setup_guide(p, url)
            )
            btn_row.addWidget(help_btn)

        card_layout.addLayout(btn_row)

        self._provider_widgets[pid] = widgets
        return card

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("aiProviderFieldLabel")
        label.setFixedWidth(56)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return label

    # ── Help link / setup hint ─────────────────────────────────────

    def _open_setup_guide(self, pid: str, url: str) -> None:
        """Open the provider's key-management page and show an inline hint."""
        webbrowser.open(url)

        widgets = self._provider_widgets.get(pid, {})
        key_edit = widgets.get("key_edit")
        if key_edit is not None:
            key_edit.setFocus()
            # Briefly un-mask so the user sees what they paste.
            key_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            show_btn = widgets.get("show_btn")
            if show_btn is not None:
                show_btn.setChecked(True)

        card = widgets.get("card")
        if card is None:
            return

        existing_hint = self._setup_hints.get(pid)
        if existing_hint is not None:
            existing_hint.deleteLater()

        hint = QFrame(card)
        hint.setObjectName("aiProviderSetupHint")
        hint_layout = QHBoxLayout(hint)
        hint_layout.setContentsMargins(8, 6, 8, 6)
        hint_layout.setSpacing(6)

        has_key = "key_edit" in widgets
        hint_text = (
            "\U0001f310  Browser opened — copy your key, paste it above, then hit"
            if has_key
            else "\U0001f310  Browser opened — once installed, hit"
        )
        hint_layout.addWidget(QLabel(hint_text))

        validate_btn = QPushButton("Save & Test")
        validate_btn.setObjectName("aiProviderSaveAndTestButton")
        validate_btn.clicked.connect(
            lambda _checked=False, p=pid: self._save_and_test(p)
        )
        hint_layout.addWidget(validate_btn)

        hint_layout.addStretch(1)

        dismiss_btn = QPushButton("✕")
        dismiss_btn.setObjectName("aiProviderHintDismissButton")
        dismiss_btn.setFlat(True)
        dismiss_btn.setFixedWidth(24)
        dismiss_btn.clicked.connect(hint.deleteLater)
        hint_layout.addWidget(dismiss_btn)

        # Append the hint at the bottom of the card.
        card_layout = card.layout()
        if isinstance(card_layout, QVBoxLayout):
            card_layout.addWidget(hint)
        self._setup_hints[pid] = hint

    def _save_and_test(self, pid: str) -> None:
        """Save (which validates) and dismiss the setup hint."""
        self._save_provider(pid)

        widgets = self._provider_widgets.get(pid, {})
        key_edit = widgets.get("key_edit")
        show_btn = widgets.get("show_btn")
        if key_edit is not None:
            key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        if show_btn is not None:
            show_btn.setChecked(False)

        hint = self._setup_hints.pop(pid, None)
        if hint is not None:
            hint.deleteLater()

    # ── Provider state machine ─────────────────────────────────────

    def _collect_provider_kwargs(self, pid: str) -> dict[str, str]:
        widgets = self._provider_widgets.get(pid, {})
        kwargs: dict[str, str] = {}
        key_edit = widgets.get("key_edit")
        if key_edit is not None:
            kwargs["api_key"] = key_edit.text().strip()
        url_edit = widgets.get("url_edit")
        if url_edit is not None:
            kwargs["base_url"] = url_edit.text().strip()
        model_combo = widgets.get("model_combo")
        if model_combo is not None:
            kwargs["model"] = model_combo.currentText().strip()
        return kwargs

    def _prepare_provider_request(
        self, pid: str,
    ) -> tuple[Any, dict[str, str], Any] | None:
        from shared.ai.provider_registry import get_provider

        kwargs = self._collect_provider_kwargs(pid)
        provider = get_provider(pid)
        if not provider:
            self._set_provider_status(pid, "failed", detail="Unknown provider")
            return None

        provider.configure(**kwargs)
        info = provider.info()
        if info.category == "local":
            installed_models, selected_model = _resolve_local_model_selection(
                kwargs.get("model", ""),
                list(info.available_models),
            )
            if not installed_models:
                self._set_provider_status(
                    pid,
                    "failed",
                    detail="No pulled Ollama models found on this PC at this URL.",
                )
                return None
            kwargs["model"] = selected_model
            widgets = self._provider_widgets.get(pid, {})
            model_combo = widgets.get("model_combo")
            if model_combo is not None:
                model_combo.setCurrentText(selected_model)
        return (provider, kwargs, info)

    def _run_provider_check(
        self,
        pid: str,
        provider: Any,
        on_result: Callable[[Any], None],
    ) -> None:
        """Run a provider validation check on a worker thread.

        Marshals the result back to the GUI thread via QTimer so the
        callbacks can safely touch widgets.
        """
        self._set_provider_status(pid, "validating")

        def _run() -> None:
            result = provider.test_connection(timeout=15.0)
            # QTimer.singleShot is the Qt-idiomatic equivalent of
            # tkinter's ``win.after(0, ...)`` — schedules the call on
            # the GUI event loop.
            QTimer.singleShot(0, lambda r=result: on_result(r))

        threading.Thread(target=_run, daemon=True).start()

    def _persist_provider_credentials(
        self,
        pid: str,
        kwargs: dict[str, str],
        *,
        make_active: bool,
    ) -> None:
        from shared.ai.credential_store import (
            set_active_provider_id,
            set_provider_credentials,
        )

        set_provider_credentials(pid, **kwargs)
        if make_active:
            # Cloud and local alike: the active provider id is the
            # single source of truth the chat model picker reads.
            set_active_provider_id(pid)

    def _save_provider(self, pid: str, *, make_active: bool = False) -> None:
        prepared = self._prepare_provider_request(pid)
        if prepared is None:
            return
        provider, kwargs, _info = prepared

        self._run_provider_check(
            pid,
            provider,
            lambda result: self._handle_save_result(
                pid,
                kwargs,
                make_active=make_active,
                result=result,
            ),
        )

    def _handle_save_result(
        self,
        pid: str,
        kwargs: dict[str, str],
        *,
        make_active: bool,
        result: Any,
    ) -> None:
        if not result.success:
            state, detail = _classify_connection_error(result.error)
            self._set_provider_status(pid, state, detail=detail)
            return

        try:
            self._persist_provider_credentials(
                pid, kwargs, make_active=make_active,
            )
            self._refresh_provider_cards()
            if self._on_change:
                self._on_change()
        except Exception as e:
            self._set_provider_status(pid, "failed", detail=f"Save error: {e}")
            return

        if make_active:
            # Writes opt_ai_mode = "local"/"cloud" so _resolve_provider
            # and the diagnostics manager follow the chosen backend.
            self._apply_parent_mode(pid)
        self._handle_test_result(pid, result)

    def _test_provider(self, pid: str) -> None:
        prepared = self._prepare_provider_request(pid)
        if prepared is None:
            return
        provider, _kwargs, _info = prepared

        self._run_provider_check(
            pid,
            provider,
            lambda result: self._handle_test_result(pid, result),
        )

    def _handle_test_result(self, pid: str, result: Any) -> None:
        from shared.ai.credential_store import get_active_provider_id

        if result.success:
            is_active = get_active_provider_id() == pid
            state = "active" if is_active else "connected"
            self._set_provider_status(
                pid,
                state,
                detail=f"{result.latency_ms:.0f}ms • {result.model_confirmed}",
            )
        else:
            state, detail = _classify_connection_error(result.error)
            self._set_provider_status(pid, state, detail=detail)

    def _set_active(self, pid: str) -> None:
        self._save_provider(pid, make_active=True)

    def _disconnect_provider(self, pid: str) -> None:
        from shared.ai.credential_store import (
            get_active_provider_id,
            remove_provider_credentials,
            set_active_provider_id,
        )
        from shared.ai.provider_registry import get_provider

        provider = get_provider(pid)
        name = provider.info().display_name if provider else pid

        confirmed = QMessageBox.question(
            self,
            "Disconnect Provider",
            f"Remove the saved API key for {name}?\n\n"
            "You can re-enter it at any time.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return

        remove_provider_credentials(pid)

        if get_active_provider_id() == pid:
            set_active_provider_id("")

        # Clear the key field in the UI before refresh removes it.
        widgets = self._provider_widgets.get(pid, {})
        key_edit = widgets.get("key_edit")
        if key_edit is not None:
            key_edit.setText("")

        self._set_provider_status(pid, "not_connected")
        self._refresh_provider_cards()

        if self._on_change:
            self._on_change()

    def _apply_parent_mode(self, pid: str | None) -> None:
        """Mirror the tkinter ``_set_ai_mode`` parent hook.

        The Qt MainWindow doesn't currently expose ``_set_ai_mode``;
        we fall back to writing ``opt_ai_mode`` directly into the
        controller's cfg if a parent cfg is reachable, then notify
        the AI diagnostics manager.
        """
        mode = "off"
        if pid:
            mode = "local" if pid == "local" else "cloud"

        parent = self.parent()
        # 1. Direct setter, if the parent provides one.
        setter = getattr(parent, "_set_ai_mode", None)
        if callable(setter):
            try:
                setter(mode)
                return
            except Exception:
                pass

        # 2. Fall back: write into parent._cfg if it's a dict.
        # MainWindow exposes the live cfg as the private-by-convention
        # ``_cfg`` attribute (gui_qt/main_window.py:151).  The dialog
        # was previously looking for ``parent.cfg``, which silently
        # returned None and skipped the persist step — clicking "Set
        # as Active" updated the runtime diagnostics manager but never
        # actually saved opt_ai_mode.
        cfg = getattr(parent, "_cfg", None)
        if isinstance(cfg, dict):
            cfg["opt_ai_mode"] = mode
            try:
                from config import save_config
                save_config(cfg)
            except Exception:
                pass

        # 3. Update the live diagnostics manager regardless.
        try:
            from shared.ai_diagnostics import get_diagnostics
            mgr = get_diagnostics()
            if mgr:
                mgr.set_mode(mode)
        except Exception:
            pass

    # ── Status setter ──────────────────────────────────────────────

    def _set_provider_status(
        self, pid: str, state: str, *, detail: str = "",
    ) -> None:
        widgets = self._provider_widgets.get(pid, {})
        label = widgets.get("status_label")
        text, qss_state = _STATUS_MAP.get(state, _STATUS_MAP["not_connected"])
        if label is not None:
            label.setText(text)
            label.setProperty("state", qss_state)
            # Force the QSS to re-evaluate the property selector.
            style = label.style()
            if style is not None:
                style.unpolish(label)
                style.polish(label)

        detail_label = widgets.get("detail_label")
        if detail_label is not None:
            detail_label.setText(detail)
            detail_label.setProperty("state", qss_state)
            style = detail_label.style()
            if style is not None:
                style.unpolish(detail_label)
                style.polish(detail_label)


# ─── Public entry point ───────────────────────────────────────────────


def open_ai_provider_dialog(
    parent: QWidget | None = None,
    on_change: Callable[[], None] | None = None,
) -> int:
    """Convenience entry point — opens the Qt AI provider dialog modally.

    Returns the dialog exec code (``QDialog.Accepted`` / ``Rejected``)
    so callers can branch if they care; the original tkinter version
    returned None, but Qt dialogs naturally return an int.
    """
    dialog = AIProviderDialog(parent, on_change=on_change)
    return dialog.exec()
