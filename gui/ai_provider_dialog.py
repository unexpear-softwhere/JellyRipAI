"""AI Provider Connection dialog for JellyRip.

Toplevel popup that lets the user:
- See all available providers and their connection status
- Enter/edit API keys
- Select models
- Test connections
- Choose the active cloud provider

Opened from Settings or the AI mode bar.  Never touches engine/ or
transcode/ — all provider logic lives in shared/ai/.
"""

from __future__ import annotations

import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox
from typing import Any, Callable
import re

from gui.theme import dialog_palette
from shared.runtime import APP_DISPLAY_NAME

# Style constants matching the shared app theme
_COLORS = dialog_palette()
_BG = _COLORS["surface_deep"]
_BG2 = _COLORS["surface"]
_BG3 = _COLORS["border"]
_FG = _COLORS["text"]
_FG_DIM = _COLORS["muted"]
_ACCENT = _COLORS["accent"]
_GREEN = _COLORS["primary_button_bg"]
_GREEN_FG = _COLORS["success_fg"]
_RED = _COLORS["danger_fg"]
_YELLOW = _COLORS["warning_fg"]
_CANCEL_BG = _COLORS["secondary_button_bg"]

# ── Pricing table (display-only, per 1M tokens) ──────────────────────
# Kept here in the dialog layer — never imported by engine/runtime code.
# These are point-in-time snapshots; treat as cached display data.
_PRICING_LAST_UPDATED = "2026-04"
# fmt: off
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    # model_id:                         (input $/1M,  output $/1M)
    # Claude
    "claude-sonnet-4-20250514":         (3.00,   15.00),
    "claude-haiku-4-5-20251001":        (0.80,    4.00),
    "claude-opus-4-6":                  (15.00,  75.00),
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


def _format_model_cost(model_id: str) -> str | None:
    """Return a short cost string for a model, or None if unknown/free."""
    pricing = _MODEL_PRICING.get(model_id)
    if pricing is None:
        return None
    input_per_m, output_per_m = pricing
    # Estimated cost per diagnostic call
    est = (_TYPICAL_INPUT_TOKENS * input_per_m + _TYPICAL_OUTPUT_TOKENS * output_per_m) / 1_000_000
    if est < 0.001:
        est_str = "<$0.001"
    else:
        est_str = f"~${est:.4f}"
    return (
        f"${input_per_m:g} / ${output_per_m:g} per 1M tok  \u2022  {est_str}/call"
        f"  (as of {_PRICING_LAST_UPDATED})"
    )


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
        version_tokens: list[str] = []
        for token in parts[2:]:
            if not token.isdigit():
                continue
            if len(token) > 2:
                break
            version_tokens.append(token)
            if len(version_tokens) == 2:
                break
        if not version_tokens:
            return 0.0
        return _version_value(".".join(version_tokens))

    # Provider / family tiering first. These scores are intentionally spaced
    # far apart so "opus" will beat "sonnet" regardless of release date text.
    if lowered.startswith("claude"):
        tier = 0.0
        if "opus" in lowered:
            tier = 93000
        elif "sonnet" in lowered:
            tier = 92000
        elif "haiku" in lowered:
            tier = 91000
        return tier + _claude_version_value(lowered)

    if lowered.startswith("gpt-"):
        score = 0.0
        if lowered.startswith("gpt-5"):
            score += 9200
        elif "gpt-4.1" in lowered:
            score += 8300
        elif "gpt-4o" in lowered:
            score += 8100
        elif "gpt-4" in lowered:
            score += 7800
        elif "gpt-3.5" in lowered:
            score += 5000
        if "mini" in lowered:
            score -= 1200
        if "nano" in lowered:
            score -= 1800
        return 80000 + score

    if lowered.startswith("gemini"):
        score = 0.0
        if "ultra" in lowered:
            score += 8600
        elif "pro" in lowered:
            score += 7800
        elif "flash" in lowered:
            score += 6800
        if "lite" in lowered:
            score -= 1000
        if "nano" in lowered:
            score -= 1600
        version_match = re.search(r"gemini-([0-9]+(?:\.[0-9]+)?)", lowered)
        return 70000 + score + _version_value(version_match.group(1) if version_match else lowered)

    # Local / open-weight models: parameter count should dominate.
    family_bonus = 0.0
    if lowered.startswith("qwen"):
        family_bonus = 340
    elif lowered.startswith("llama"):
        family_bonus = 320
    elif lowered.startswith("mistral"):
        family_bonus = 300
    elif lowered.startswith("gemma"):
        family_bonus = 280

    return 10000 + (largest_size * 1000) + family_bonus + _version_value(lowered)


def _sort_models_by_power(models: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for model in models:
        name = str(model or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(name)
    return sorted(unique, key=lambda name: (-_model_power_score(name), name.lower()))


def _resolve_local_model_selection(
    current_model: str,
    available_models: list[str],
) -> tuple[list[str], str]:
    options = _sort_models_by_power(list(available_models))
    if not options:
        return ([], "")
    exact = {name.lower(): name for name in options}
    selected = exact.get(str(current_model or "").strip().lower(), options[0])
    return (options, selected)


def _classify_connection_error(error_text: str) -> tuple[str, str]:
    """Map raw provider test errors to clearer dialog states and guidance."""
    raw = str(error_text or "").strip()
    lowered = raw.lower()
    if any(pattern in lowered for pattern in _QUOTA_ERROR_PATTERNS):
        return (
            "rate_limited",
            "Rate limited / out of quota. Check billing, usage caps, or retry later.",
        )
    return ("failed", raw[:80])


class AIProviderDialog:
    """Modal dialog for managing AI provider connections."""

    def __init__(
        self,
        parent: tk.Tk | tk.Toplevel,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self._parent = parent
        self._on_change = on_change
        self._win: tk.Toplevel | None = None
        self._provider_frames: dict[str, dict[str, Any]] = {}
        self._scroll_canvas: tk.Canvas | None = None
        self._scroll_window_id: int | None = None

    def show(self) -> None:
        if self._win is not None and self._win.winfo_exists():
            self._win.lift()
            self._win.focus_force()
            return

        win = tk.Toplevel(self._parent)
        self._win = win
        win.title("AI Provider Setup")
        win.configure(bg=_BG)
        win.geometry("780x700")
        win.minsize(720, 560)
        win.resizable(True, True)
        try:
            win.grab_set()
        except tk.TclError:
            pass
        win.lift()
        win.focus_force()
        win.transient(self._parent)

        # Header
        tk.Label(
            win, text="AI Provider Connections",
            bg=_BG, fg=_ACCENT,
            font=("Segoe UI", 14, "bold"),
        ).pack(fill="x", padx=16, pady=(14, 2))
        tk.Label(
            win,
            text=f"Configure which AI backends {APP_DISPLAY_NAME} can use for diagnostics.",
            bg=_BG, fg=_FG_DIM,
            font=("Segoe UI", 10),
        ).pack(fill="x", padx=16, pady=(0, 10))

        # Scrollable area for provider cards
        body = tk.Frame(win, bg=_BG)
        body.pack(fill="both", expand=True)

        canvas = tk.Canvas(body, bg=_BG, highlightthickness=0)
        self._scroll_canvas = canvas
        scrollbar = tk.Scrollbar(body, orient="vertical", command=canvas.yview)
        self._scroll_frame = tk.Frame(canvas, bg=_BG)
        self._scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        self._scroll_window_id = canvas.create_window(
            (0, 0),
            window=self._scroll_frame,
            anchor="nw",
        )
        canvas.bind("<Configure>", self._on_scroll_canvas_configure)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(16, 8), pady=4)
        scrollbar.pack(side="right", fill="y", padx=(0, 12), pady=4)

        self._build_provider_cards()
        self._sync_scroll_canvas_width()

        # Bottom buttons
        btn_row = tk.Frame(win, bg=_BG)
        btn_row.pack(fill="x", padx=16, pady=(8, 12))
        tk.Button(
            btn_row, text="Close",
            bg=_CANCEL_BG, fg=_FG,
            font=("Segoe UI", 10), relief="flat",
            command=self._close,
        ).pack(side="right", padx=4)

        win.protocol("WM_DELETE_WINDOW", self._close)

    def _sync_scroll_canvas_width(self, width: int | None = None) -> None:
        canvas = self.__dict__.get("_scroll_canvas")
        window_id = self.__dict__.get("_scroll_window_id")
        if canvas is None or window_id is None:
            return
        if width is None:
            try:
                width = int(canvas.winfo_width())
            except Exception:
                return
        inner_width = max(1, int(width))
        try:
            canvas.itemconfigure(window_id, width=inner_width)
        except Exception:
            pass
        try:
            canvas.configure(scrollregion=canvas.bbox("all"))
        except Exception:
            pass

    def _on_scroll_canvas_configure(self, event) -> None:
        self._sync_scroll_canvas_width(int(getattr(event, "width", 1)))

    def _close(self) -> None:
        if self._win:
            try:
                self._win.destroy()
            except Exception:
                pass
            self._win = None

    def _build_provider_cards(self) -> None:
        from shared.ai.provider_registry import get_connection_summary, list_providers

        providers = list_providers()
        summary = get_connection_summary()

        for info in providers:
            pid = info.id
            status = summary.get(pid, {})
            self._build_single_card(info, status)

    def _refresh_provider_cards(self) -> None:
        if not hasattr(self, "_scroll_frame"):
            return
        for child in self._scroll_frame.winfo_children():
            try:
                child.destroy()
            except Exception:
                pass
        self._provider_frames.clear()
        if hasattr(self, "_setup_hints"):
            self._setup_hints.clear()
        self._build_provider_cards()
        self._sync_scroll_canvas_width()

    def _apply_parent_mode(self, pid: str | None) -> None:
        mode = "off"
        if pid:
            mode = "local" if pid == "local" else "cloud"

        setter = getattr(self._parent, "_set_ai_mode", None)
        if callable(setter):
            try:
                setter(mode)
                return
            except Exception:
                pass

        cfg = getattr(self._parent, "cfg", None)
        if isinstance(cfg, dict):
            cfg["opt_ai_mode"] = mode
            try:
                from config import save_config
                save_config(cfg)
            except Exception:
                pass

        try:
            from shared.ai_diagnostics import get_diagnostics
            mgr = get_diagnostics()
            if mgr:
                mgr.set_mode(mode)
        except Exception:
            pass

    def _build_single_card(self, info: Any, status: dict[str, Any]) -> None:
        from shared.ai.credential_store import get_provider_credentials

        pid = info.id
        creds = get_provider_credentials(pid)
        frame = tk.Frame(self._scroll_frame, bg=_BG2, bd=1, relief="solid")
        frame.pack(fill="x", padx=4, pady=6, ipady=6)

        # Title row
        title_row = tk.Frame(frame, bg=_BG2)
        title_row.pack(fill="x", padx=12, pady=(8, 2))

        tk.Label(
            title_row, text=info.display_name,
            bg=_BG2, fg=_FG,
            font=("Segoe UI", 11, "bold"),
        ).pack(side="left")

        category_color = _ACCENT if info.category == "cloud" else _YELLOW
        tk.Label(
            title_row,
            text=info.category.upper(),
            bg=_BG2, fg=category_color,
            font=("Segoe UI", 9),
        ).pack(side="left", padx=(8, 0))

        # Status indicator (updated via _set_provider_status after build)
        is_active = status.get("is_active", False)
        has_creds = status.get("has_credentials", False)
        if is_active and has_creds:
            init_state = "active"
        elif has_creds:
            init_state = "connected"
        else:
            init_state = "not_connected"
        init_text, init_color = self._STATUS_MAP[init_state]

        status_label = tk.Label(
            title_row, text=init_text,
            bg=_BG2, fg=init_color,
            font=("Segoe UI", 9),
        )
        status_label.pack(side="right")

        # API key row (for cloud providers)
        widgets: dict[str, Any] = {"status_label": status_label, "frame": frame}

        if info.requires_api_key:
            key_row = tk.Frame(frame, bg=_BG2)
            key_row.pack(fill="x", padx=12, pady=2)
            tk.Label(
                key_row, text="API Key:",
                bg=_BG2, fg=_FG_DIM,
                font=("Segoe UI", 10), width=8, anchor="w",
            ).pack(side="left")
            key_var = tk.StringVar(value=creds.get("api_key", ""))
            key_entry = tk.Entry(
                key_row, textvariable=key_var,
                bg=_BG, fg=_FG,
                font=("Consolas", 10),
                insertbackground="white",
                relief="flat", bd=3, width=42, show="*",
            )
            key_entry.pack(side="left", padx=4)
            # Toggle show/hide
            show_var = tk.BooleanVar(value=False)

            def _toggle_show(entry: tk.Entry = key_entry, sv: tk.BooleanVar = show_var) -> None:
                sv.set(not sv.get())
                entry.configure(show="" if sv.get() else "*")

            tk.Button(
                key_row, text="Show",
                bg=_BG3, fg=_FG_DIM,
                font=("Segoe UI", 8), relief="flat",
                command=_toggle_show,
            ).pack(side="left", padx=2)
            widgets["key_var"] = key_var

            # Security indicator (shows when a key is saved)
            if has_creds:
                from shared.ai.credential_store import is_encrypted_storage
                security_row = tk.Frame(frame, bg=_BG2)
                security_row.pack(fill="x", padx=12, pady=(0, 2))
                if is_encrypted_storage():
                    sec_text = "\U0001f512 Stored securely (Windows encrypted)"
                    sec_color = _GREEN_FG
                else:
                    sec_text = "\U0001f513 Stored locally (plaintext)"
                    sec_color = _YELLOW
                security_label = tk.Label(
                    security_row, text=sec_text,
                    bg=_BG2, fg=sec_color,
                    font=("Segoe UI", 9),
                )
                security_label.pack(side="left", padx=(64, 0))
                widgets["security_label"] = security_label
        else:
            # Local provider: base URL
            url_row = tk.Frame(frame, bg=_BG2)
            url_row.pack(fill="x", padx=12, pady=2)
            tk.Label(
                url_row, text="URL:",
                bg=_BG2, fg=_FG_DIM,
                font=("Segoe UI", 10), width=8, anchor="w",
            ).pack(side="left")
            url_var = tk.StringVar(
                value=creds.get("base_url", "http://localhost:11434")
            )
            tk.Entry(
                url_row, textvariable=url_var,
                bg=_BG, fg=_FG,
                font=("Consolas", 10),
                insertbackground="white",
                relief="flat", bd=3, width=42,
            ).pack(side="left", padx=4)
            widgets["url_var"] = url_var

        # Model selector
        model_row = tk.Frame(frame, bg=_BG2)
        model_row.pack(fill="x", padx=12, pady=2)
        tk.Label(
            model_row, text="Model:",
            bg=_BG2, fg=_FG_DIM,
            font=("Segoe UI", 10), width=8, anchor="w",
        ).pack(side="left")
        if info.category == "local":
            current_model = str(creds.get("model", "") or "").strip()
            model_options, current_model = _resolve_local_model_selection(
                current_model,
                list(info.available_models),
            )
            display_options = list(model_options) or ["No installed local models"]
        else:
            current_model = creds.get("model", info.default_model) or info.default_model
            model_options = _sort_models_by_power([current_model, *info.available_models])
            if not model_options:
                model_options = [info.default_model]
            display_options = list(model_options)
        model_var = tk.StringVar(value=current_model)
        if info.category == "local" and not current_model and display_options:
            model_var.set(display_options[0])
        model_menu = tk.OptionMenu(model_row, model_var, *display_options)
        model_menu.configure(
            bg=_BG, fg=_FG, font=("Segoe UI", 10),
            highlightthickness=0, relief="flat",
            activebackground=_BG3, activeforeground=_FG,
        )
        if info.category == "local" and not info.available_models:
            model_menu.configure(state="disabled")
        model_menu["menu"].configure(
            bg=_BG2, fg=_FG, font=("Segoe UI", 10),
            activebackground=_ACCENT, activeforeground="white",
        )
        model_menu.pack(side="left", padx=4)
        widgets["model_var"] = model_var

        if info.category == "local":
            local_note = (
                "Only pulled Ollama models are listed."
                if info.available_models
                else "No pulled Ollama models were detected on this PC at this URL yet."
            )
            tk.Label(
                frame, text=local_note,
                bg=_BG2, fg=_FG_DIM,
                font=("Segoe UI", 8),
            ).pack(fill="x", padx=76, pady=(0, 2))

        # Pricing label (cloud providers only)
        if info.category == "cloud":
            cost_var = tk.StringVar(value="")
            cost_label = tk.Label(
                model_row, textvariable=cost_var,
                bg=_BG2, fg=_FG_DIM,
                font=("Segoe UI", 8),
            )
            cost_label.pack(side="left", padx=(6, 0))
            widgets["cost_var"] = cost_var

            def _on_model_change(*_args: Any, cv: tk.StringVar = cost_var,
                                 mv: tk.StringVar = model_var) -> None:
                text = _format_model_cost(mv.get()) or ""
                cv.set(text)

            model_var.trace_add("write", _on_model_change)
            # Set initial value
            _on_model_change()

        # Action buttons
        btn_row = tk.Frame(frame, bg=_BG2)
        btn_row.pack(fill="x", padx=12, pady=(6, 4))

        # Detail line shown below status dot (latency, error excerpt, etc.)
        detail_var = tk.StringVar(value="")
        detail_label = tk.Label(
            btn_row, textvariable=detail_var,
            bg=_BG2, fg=_FG_DIM,
            font=("Segoe UI", 8),
        )
        detail_label.pack(side="right", padx=4)
        widgets["detail_var"] = detail_var
        widgets["detail_label"] = detail_label

        tk.Button(
            btn_row, text="Test",
            bg=_BG3, fg=_FG,
            font=("Segoe UI", 9), relief="flat",
            command=lambda p=pid: self._test_provider(p),
        ).pack(side="left", padx=(0, 4))

        tk.Button(
            btn_row, text="Save",
            bg=_GREEN, fg="white",
            font=("Segoe UI", 9, "bold"), relief="flat",
            command=lambda p=pid: self._save_provider(p),
        ).pack(side="left", padx=(0, 4))

        if info.category == "cloud":
            set_active_btn = tk.Button(
                btn_row, text="Set as Active",
                bg=_ACCENT if not status.get("is_active") else _BG3,
                fg="white" if not status.get("is_active") else _GREEN_FG,
                font=("Segoe UI", 9, "bold"), relief="flat",
                command=lambda p=pid: self._set_active(p),
            )
            set_active_btn.pack(side="left", padx=(0, 4))
            widgets["set_active_btn"] = set_active_btn

        # Disconnect button (only shown when credentials exist)
        if has_creds:
            tk.Button(
                btn_row, text="Disconnect",
                bg=_CANCEL_BG, fg=_RED,
                font=("Segoe UI", 9), relief="flat",
                command=lambda p=pid: self._disconnect_provider(p),
            ).pack(side="left", padx=(0, 4))

        # Help link — opens provider page and nudges the user back to paste + validate
        if info.help_url:
            link_text = "Get API key \u2192" if info.requires_api_key else "Setup guide \u2192"
            help_label = tk.Label(
                btn_row,
                text=link_text,
                bg=_BG2, fg=_ACCENT,
                font=("Segoe UI", 9, "underline"),
                cursor="hand2",
            )
            help_label.pack(side="right", padx=4)
            help_label.bind(
                "<Button-1>",
                lambda _e, p=pid, url=info.help_url: self._open_setup_guide(p, url),
            )

        self._provider_frames[pid] = widgets

    def _open_setup_guide(self, pid: str, url: str) -> None:
        """Open the provider's key-management page and show an inline prompt."""
        webbrowser.open(url)
        widgets = self._provider_frames.get(pid, {})

        # Focus the API key entry so the user can paste immediately
        key_entry = self._find_key_entry(pid)
        if key_entry:
            key_entry.focus_set()
            # Un-mask the field briefly so the user sees what they paste
            key_entry.configure(show="")

        # Show a transient inline hint below the action row
        frame = widgets.get("frame")
        if not frame:
            return

        # Avoid stacking duplicate hints
        existing = getattr(self, "_setup_hints", {})
        if pid in existing:
            try:
                existing[pid].destroy()
            except Exception:
                pass

        hint = tk.Frame(frame, bg=_BG3)
        hint.pack(fill="x", padx=12, pady=(2, 6))

        has_key = "key_var" in widgets
        hint_text = (
            "\U0001f310  Browser opened \u2014 copy your key, paste it above, then hit"
            if has_key
            else "\U0001f310  Browser opened \u2014 once installed, hit"
        )
        tk.Label(
            hint, text=hint_text,
            bg=_BG3, fg=_FG,
            font=("Segoe UI", 9),
        ).pack(side="left", padx=(8, 0), pady=4)

        validate_btn = tk.Button(
            hint, text="Save & Test",
            bg=_GREEN, fg="white",
            font=("Segoe UI", 9, "bold"), relief="flat",
            command=lambda p=pid: self._save_and_test(p),
        )
        validate_btn.pack(side="left", padx=(6, 4), pady=4)

        dismiss = tk.Label(
            hint, text="\u2715",
            bg=_BG3, fg=_FG_DIM,
            font=("Segoe UI", 10),
            cursor="hand2",
        )
        dismiss.pack(side="right", padx=(0, 6), pady=4)
        dismiss.bind("<Button-1>", lambda _e: hint.destroy())

        if not hasattr(self, "_setup_hints"):
            self._setup_hints: dict[str, tk.Frame] = {}
        self._setup_hints[pid] = hint

    def _find_key_entry(self, pid: str) -> tk.Entry | None:
        """Locate the API-key Entry widget inside a provider card."""
        widgets = self._provider_frames.get(pid, {})
        frame = widgets.get("frame")
        if not frame:
            return None
        for child in frame.winfo_children():
            for sub in child.winfo_children():
                if isinstance(sub, tk.Entry):
                    try:
                        if sub.cget("show") in ("*", ""):
                            return sub
                    except Exception:
                        pass
        return None

    def _save_and_test(self, pid: str) -> None:
        """Convenience: save credentials (which auto-validates) and clean up the hint."""
        self._save_provider(pid)
        # Re-mask the key entry after save
        key_entry = self._find_key_entry(pid)
        if key_entry:
            key_entry.configure(show="*")
        # Remove the setup hint
        hints = getattr(self, "_setup_hints", {})
        hint = hints.pop(pid, None)
        if hint:
            try:
                hint.destroy()
            except Exception:
                pass

    def _collect_provider_kwargs(self, pid: str) -> dict[str, str]:
        """Collect current field values for a provider."""
        widgets = self._provider_frames.get(pid, {})
        kwargs: dict[str, str] = {}
        if "key_var" in widgets:
            kwargs["api_key"] = widgets["key_var"].get().strip()
        if "url_var" in widgets:
            kwargs["base_url"] = widgets["url_var"].get().strip()
        if "model_var" in widgets:
            kwargs["model"] = widgets["model_var"].get().strip()
        return kwargs

    def _save_provider(self, pid: str) -> None:
        """Save one exclusive provider, then auto-validate the connection."""
        from shared.ai.credential_store import connect_single_provider
        from shared.ai.provider_registry import get_provider

        kwargs = self._collect_provider_kwargs(pid)
        provider = get_provider(pid)
        should_activate = False
        if provider:
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
                    self._apply_parent_mode(None)
                    return
                kwargs["model"] = selected_model
                widgets = self._provider_frames.get(pid, {})
                model_var = widgets.get("model_var")
                if model_var is not None:
                    try:
                        model_var.set(selected_model)
                    except Exception:
                        pass
            if info.requires_api_key:
                should_activate = bool(kwargs.get("api_key"))
            else:
                should_activate = any(bool(value) for value in kwargs.values())
        try:
            connect_single_provider(pid, **kwargs)
            self._refresh_provider_cards()
            self._apply_parent_mode(pid if should_activate else None)
            if self._on_change:
                self._on_change()
        except Exception as e:
            self._set_provider_status(pid, "failed", detail=f"Save error: {e}")
            return

        # Auto-validate: kick off a lightweight test immediately
        self._test_provider(pid)

    def _test_provider(self, pid: str) -> None:
        """Test connection in a background thread."""
        from shared.ai.provider_registry import get_provider

        kwargs = self._collect_provider_kwargs(pid)
        provider = get_provider(pid)
        if not provider:
            self._set_provider_status(pid, "failed", detail="Unknown provider")
            return

        # Apply current (unsaved) values for the test
        provider.configure(**kwargs)
        self._set_provider_status(pid, "validating")

        def _run() -> None:
            result = provider.test_connection(timeout=15.0)
            if self._win and self._win.winfo_exists():
                self._win.after(0, lambda: self._handle_test_result(pid, result))

        threading.Thread(target=_run, daemon=True).start()

    def _handle_test_result(self, pid: str, result: Any) -> None:
        from shared.ai.credential_store import get_active_provider_id

        if result.success:
            is_active = get_active_provider_id() == pid
            state = "active" if is_active else "connected"
            self._set_provider_status(
                pid, state,
                detail=f"{result.latency_ms:.0f}ms \u2022 {result.model_confirmed}",
            )
        else:
            state, detail = _classify_connection_error(result.error)
            self._set_provider_status(
                pid, state,
                detail=detail,
            )

    def _set_active(self, pid: str) -> None:
        """Make this provider the only connected backend."""
        self._save_provider(pid)

    def _disconnect_provider(self, pid: str) -> None:
        """Remove saved credentials for a provider after confirmation."""
        from shared.ai.credential_store import (
            get_active_provider_id,
            remove_provider_credentials,
            set_active_provider_id,
        )
        from shared.ai.provider_registry import get_provider

        provider = get_provider(pid)
        name = provider.info().display_name if provider else pid

        if not messagebox.askyesno(
            "Disconnect Provider",
            f"Remove the saved API key for {name}?\n\n"
            "You can re-enter it at any time.",
            parent=self._win,
        ):
            return

        remove_provider_credentials(pid)

        # If this was the active provider, clear active selection
        if get_active_provider_id() == pid:
            set_active_provider_id("")

        # Clear the key field in the UI
        widgets = self._provider_frames.get(pid, {})
        key_var = widgets.get("key_var")
        if key_var:
            key_var.set("")

        self._set_provider_status(pid, "not_connected")

        # Remove the security indicator if present
        sec_label = widgets.get("security_label")
        if sec_label:
            try:
                sec_label.master.destroy()
            except Exception:
                pass
            widgets.pop("security_label", None)

        self._refresh_provider_cards()
        self._apply_parent_mode(None)

        if self._on_change:
            self._on_change()

    # ── Unified status state machine ────────────────────────────────────
    # Every provider card shows exactly one state at all times.
    #   active        — green dot, "Active"       (chosen cloud provider)
    #   connected     — green dot, "Connected"     (credentials saved + validated)
    #   validating    — yellow dot, "Validating…"  (test in progress)
    #   rate_limited  — yellow dot, "Rate limited / quota" (429 / quota exhausted)
    #   failed        — red dot, "Failed"          (test or save error)
    #   not_connected — dim dot, "Not connected"   (no credentials)

    _STATUS_MAP: dict[str, tuple[str, str]] = {
        "active":        ("\u25cf Active",        _GREEN_FG),
        "connected":     ("\u25cf Connected",     _GREEN_FG),
        "validating":    ("\u25cf Validating\u2026", _YELLOW),
        "rate_limited":  ("\u25cf Rate limited / quota", _YELLOW),
        "failed":        ("\u25cf Failed",        _RED),
        "not_connected": ("\u25cf Not connected", _FG_DIM),
    }

    def _set_provider_status(
        self, pid: str, state: str, *, detail: str = "",
    ) -> None:
        """Set the single canonical status for a provider card."""
        if not (self._win and self._win.winfo_exists()):
            return
        widgets = self._provider_frames.get(pid, {})
        label = widgets.get("status_label")
        text, color = self._STATUS_MAP.get(state, self._STATUS_MAP["not_connected"])
        if label:
            label.configure(text=text, fg=color)
        # Detail line (latency, error excerpt, or blank)
        detail_var = widgets.get("detail_var")
        detail_label = widgets.get("detail_label")
        if detail_var:
            detail_var.set(detail)
        if detail_label:
            detail_color = _FG_DIM
            if state == "failed":
                detail_color = _RED
            elif state == "rate_limited":
                detail_color = _YELLOW
            detail_label.configure(fg=detail_color)


def open_ai_provider_dialog(
    parent: tk.Tk | tk.Toplevel,
    on_change: Callable[[], None] | None = None,
) -> None:
    """Convenience entry point to open the AI provider dialog."""
    dialog = AIProviderDialog(parent, on_change=on_change)
    dialog.show()
