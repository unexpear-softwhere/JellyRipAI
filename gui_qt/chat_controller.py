"""Chat controller — Qt-native backend for the AI chat sidebar.

Phase 4b backend (2026-05-05).  Wires the Qt ``ChatSidebar`` to the
provider abstraction in ``shared/ai/`` so clicking Send actually
talks to the configured provider.

This is the **MVP** backend — a thin layer that:

* Resolves the active cloud or local provider per
  ``opt_ai_mode`` (``"cloud"`` / ``"local"`` / ``"off"``).
* Sends user prompts to the provider on a worker thread.
* Renders responses + errors back into the sidebar.
* Maintains a minimal in-memory message history so multi-turn
  conversations work.
* Records workflow events via ``shared.workflow_history`` so the
  existing diagnostics retain visibility.

Deferred from the tkinter version (intentional, future work):

* ``_prompt_looks_like_ui_help`` heuristics + ``_build_ui_help_fallback``
  on-device fallback when the prompt looks like a UI question.
* Full replay logging via ``shared.ai_chat_replay`` — the tkinter
  version logs request/response/error JSON bundles for offline
  debugging; we log just the workflow event for now.
* ``AIChatMemory`` integration with summarization + pinned facts.
* The onboarding gate (``_ensure_ai_profile_onboarded``) that
  walks first-time users through provider selection.

Cross-thread marshaling uses Qt signals — never ``threading.Event``,
``after()``, or polling.  The provider call runs in a daemon thread;
results land on the GUI thread via ``response_ready`` /
``error_occurred``.
"""

from __future__ import annotations

import json
import threading
import uuid
from typing import TYPE_CHECKING, Any, Callable, Mapping

from PySide6.QtCore import QObject, Signal, Slot

from shared.runtime import APP_DISPLAY_NAME

if TYPE_CHECKING:
    from gui_qt.ai_chat_sidebar import ChatSidebar


_DEFAULT_TIMEOUT_CLOUD = 30.0
_DEFAULT_TIMEOUT_LOCAL = 90.0
_DEFAULT_MAX_TOKENS = 700


# ─── Pure helpers (lifted into the Qt path 2026-05-05) ───────────────
#
# These match the tkinter equivalents in ``gui/main_window.py`` —
# ``_AI_CHAT_QUOTA_ERROR_PATTERNS``, ``_friendly_ai_chat_error``,
# ``_prompt_looks_like_ui_help``, ``_looks_like_ai_payload_echo``,
# ``_build_ui_help_fallback``.  Per the "chat code lives in pyside6"
# guidance, the Qt side carries its own copy rather than importing
# back into tkinter.  The duplication ends when ``gui/`` retires.


_QUOTA_ERROR_PATTERNS: tuple[str, ...] = (
    "quota",
    "rate_limit",
    "rate limit",
    "too many requests",
    "429",
    "insufficient_quota",
    "billing",
    "resource_exhausted",
)


def prompt_looks_like_ui_help(prompt: str) -> bool:
    """Heuristic: does the prompt look like a "what's happening on
    screen?" question?  Used to gate the on-device fallback path
    when a provider is unavailable or echoes the request payload."""
    lowered = str(prompt or "").lower()
    cues = (
        "current ui",
        "live log",
        "current rip",
        "what to do next",
        "next step",
        "status",
        "progress",
        "check progress",
        "how far",
        "what's happening",
        "what is happening",
        "drive",
        "session",
        "rip",
    )
    return any(cue in lowered for cue in cues)


def looks_like_ai_payload_echo(message: str) -> bool:
    """Detect when a provider regurgitated the request payload as
    its answer.  Some smaller local models do this when given a
    structured JSON prompt — they "respond" with the input."""
    text = str(message or "").strip()
    if not text:
        return False
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        keys = {str(key) for key in parsed.keys()}
        if {"request", "conversation_history", "ui_snapshot"}.issubset(keys):
            return True
    lowered = text.lower()
    return (
        '"request"' in lowered
        and '"conversation_history"' in lowered
        and '"ui_snapshot"' in lowered
    )


def build_ui_help_fallback(
    snapshot: Mapping[str, object],
    log_tail: str,
    error_message: str = "",
) -> str:
    """Build an on-device "what's happening?" summary from the live
    UI state.  Used when AI is unavailable, errors out, or echoes
    the request payload — gives the user something useful instead
    of a bare error.

    The shape of ``snapshot`` matches what
    ``MainWindow.get_chat_ui_snapshot()`` returns: ``status``,
    ``selected_drive``, ``ai_mode``, ``abort_button_state``,
    ``progress_percent``."""
    status = str(snapshot.get("status", "") or "").strip()
    drive = str(snapshot.get("selected_drive", "") or "").strip()
    ai_mode = str(snapshot.get("ai_mode", "") or "").strip().lower()
    abort_state = str(snapshot.get("abort_button_state", "") or "").strip().lower()
    progress = 0.0
    try:
        progress = float(snapshot.get("progress_percent", 0.0) or 0.0)
    except Exception:
        progress = 0.0

    log_lower = str(log_tail or "").lower()
    error_lower = str(error_message or "").lower()
    suggestions: list[str] = []
    status_lower = status.lower()

    active_session = (
        progress > 0
        or any(token in status_lower for token in ("rip", "scan", "move", "prep", "abort"))
    )

    if status:
        if progress > 0:
            suggestions.append(
                f"Current status is {status} ({progress:.1f}% reported)."
            )
        elif active_session:
            suggestions.append(
                f"Current status is {status}. Reported progress is still "
                f"{progress:.1f}%, which usually means the current step has "
                f"started but has not emitted progress yet."
            )

    if "loading drives" in drive.lower():
        if active_session:
            suggestions.append(
                "The drive picker still says Loading drives..., but during "
                "an active session that field can lag behind the real rip "
                "state."
            )
        else:
            suggestions.append(
                "The drive list still looks busy. Refresh it or wait for "
                "the drive picker to finish populating before starting."
            )

    if active_session:
        suggestions.append(
            "Let the current step continue unless the live log stops "
            "changing. Abort stays available while the session is active."
        )
        if abort_state == "normal":
            suggestions.append("Abort is available if the job is genuinely stuck.")
    elif "choose a mode to begin" in log_lower or status_lower == "ready":
        suggestions.append(
            "Nothing is actively running right now. Once the drive is "
            "ready, choose the rip mode that matches the disc."
        )
        suggestions.append(
            "Use Rip Movie Disc for a film, Rip TV Show Disc for episodes, "
            "Dump All Titles for manual review, or Organize Existing MKVs "
            "for files already on disk."
        )

    if "no active session to abort" in log_lower:
        suggestions.append(
            "There is no active rip session yet, so abort will not do anything."
        )

    if ai_mode == "local" and ("timed out" in error_lower or "timeout" in error_lower):
        suggestions.append(
            "The local assistant model is taking too long to answer. A "
            "smaller pulled Ollama model or a longer Local AI timeout in "
            "Settings will make the chat panel more reliable."
        )

    if not suggestions:
        return (
            "I could not get a model response, but the app still looks "
            "healthy enough to keep using. Check the latest live log lines, "
            "confirm the selected drive, and retry the request once the "
            "current state is stable."
        )

    return "\n".join(f"- {item}" for item in suggestions[:4])


class ChatController(QObject):
    """Wires a ``ChatSidebar`` to ``shared.ai`` providers.

    Construction binds the sidebar's signals to handler slots and
    keeps the cfg dict reference for runtime mode/timeout lookups.
    The controller owns the in-memory message history; ``New Chat``
    clears it.

    Typical wiring (done by ``app.py`` after MainWindow construction)::

        sidebar = main_window.ensure_chat_sidebar()
        controller = ChatController(sidebar=sidebar, cfg=cfg, parent=main_window)
        # sidebar shows / hides via the toolbar chip; controller stays alive.
    """

    # Internal cross-thread signals — emitted from the worker, slotted
    # on the GUI thread because Qt signal/slot is automatically
    # marshaled.
    response_ready = Signal(str, str)   # text, backend_label
    error_occurred = Signal(str)        # human-readable message
    disc_identified = Signal(str, str)  # (chat_md, log_line) — chat_md may
    #                                     be "" to post to the Live Log only

    def __init__(
        self,
        sidebar: "ChatSidebar",
        cfg: Mapping[str, Any],
        parent: QObject | None = None,
        facts_provider: "Callable[[], Mapping[str, Any]] | None" = None,
    ) -> None:
        super().__init__(parent)
        self._sidebar = sidebar
        self._cfg = cfg
        self._busy = False
        # Optional callable returning live disc/session facts (the
        # controller's build_ai_session_facts).  When set, each chat
        # request gets a fresh system message describing the disc the
        # user is currently looking at, so "what's the main title?" /
        # "help me identify this" answer with real data instead of
        # guessing.  Called fresh per request so it always reflects
        # the latest scan.
        self._facts_provider = facts_provider
        # Multi-turn message history.  Each element is a dict
        # {"role": "user" | "assistant", "content": str}.
        self._history: list[dict[str, str]] = []
        # Last disc auto-identified via TMDB, so a drive refresh doesn't
        # re-identify the same inserted disc on every populate.
        self._last_identified_disc: str = ""

        # Sidebar -> controller hooks.
        sidebar.message_submitted.connect(self.handle_message_submitted)
        sidebar.suggest_requested.connect(self.handle_suggest_requested)
        sidebar.new_chat_requested.connect(self.handle_new_chat)
        sidebar.copy_chat_requested.connect(self.handle_copy_chat)
        sidebar.model_selected.connect(self.handle_model_selected)
        sidebar.web_search_toggled.connect(self.handle_web_search_toggled)

        # Seed the sidebar's model picker from the active provider +
        # current cfg WITHOUT firing model_selected back at us (the cfg
        # already reflects this; no save needed at construction).
        self.refresh_model_picker()
        sidebar.set_web_search(bool(cfg.get("opt_ai_web_search", False)))

        # Worker thread -> GUI thread hooks.
        self.response_ready.connect(self._on_response_ready)
        self.error_occurred.connect(self._on_error_occurred)
        self.disc_identified.connect(self._on_disc_identified)

    # ── Public properties (test hooks) ─────────────────────────────

    @property
    def history(self) -> list[dict[str, str]]:
        """Read-only view of the current conversation."""
        return list(self._history)

    @property
    def busy(self) -> bool:
        return self._busy

    # ── Sidebar handlers ───────────────────────────────────────────

    @Slot(str)
    def handle_message_submitted(self, prompt: str) -> None:
        """User pressed Enter or clicked Send."""
        text = (prompt or "").strip()
        if not text:
            return
        if self._busy:
            self._sidebar.set_status(
                "Wait for the current reply to finish.",
                state="busy",
            )
            return

        self._history.append({"role": "user", "content": text})
        self._sidebar.append_user_message(text)
        self._sidebar.clear_input()
        self._sidebar.set_busy(True)
        self._busy = True

        # Issue a turn-scoped replay ID so request / response / error
        # records correlate cleanly in the JSONL log.
        replay_id = uuid.uuid4().hex
        self._record_replay(
            "request",
            replay_id=replay_id,
            request_text=text,
            display_text=text,
            details={"history_len": len(self._history)},
        )

        # Snapshot the history before kicking off the worker — a
        # second click during a slow request shouldn't change what
        # the worker sends.
        messages = list(self._history)
        threading.Thread(
            target=self._worker_call,
            args=(messages, replay_id),
            daemon=True,
            name="chat-controller-worker",
        ).start()

    @Slot()
    def handle_suggest_requested(self) -> None:
        """User clicked "Suggest Next Step" — sends a canned prompt
        asking the assistant what to do next.  Mirrors the tkinter
        ``_request_ai_sidebar_suggestion`` shape."""
        self.handle_message_submitted(
            "Look at the current state and live log.  What's the most "
            "useful next step or check?  Call out anything that looks "
            "healthy, anything risky, and what I should do next."
        )

    @Slot(str)
    def handle_web_search_toggled(self, enabled: bool) -> None:
        """User flipped the 🌐 Web toggle.  Writes ``opt_ai_web_search``
        into the live cfg and best-effort persists it so the choice
        survives a restart.  Applies on the next message —
        ``_with_web_context`` re-reads the live cfg each turn."""
        value = bool(enabled)
        try:
            self._cfg["opt_ai_web_search"] = value  # type: ignore[index]
        except Exception:
            pass
        try:
            from config import save_config
            save_config(self._cfg)
        except Exception as exc:
            import logging
            logging.warning(
                "Chat controller: failed to persist web-search toggle: %s",
                exc,
            )
        try:
            self._sidebar.set_status(
                "Web lookup on" if value else "Web lookup off", state="ready",
            )
        except Exception:
            pass

    def handle_model_selected(self, choice: str) -> None:
        """User picked an entry in the chat model dropdown.

        ``choice`` is ``""`` for the Off entry (disable the assistant),
        otherwise a model name belonging to the *active* provider.
        Picking a model writes that model onto the active provider,
        makes it active, and flips ``opt_ai_mode`` to the provider's
        category — so the next turn routes through ``_resolve_provider``
        unchanged.  Choosing *which* provider (a cloud one, or local)
        lives in the AI Providers dialog's "Set as Active" buttons.
        """
        value = str(choice or "").strip()
        if not value:
            # Off — disable AI.  Leave the active provider untouched so
            # picking a model again restores the previous backend.
            self._set_ai_mode_cfg("off")
            try:
                self._sidebar.set_status("AI: Off", state="ready")
            except Exception:
                pass
            return

        pid, category, _models, _current, _provider = self._picker_target()
        try:
            from shared.ai.credential_store import (
                set_active_provider_id,
                set_provider_credentials,
            )
            set_provider_credentials(pid, model=value)
            set_active_provider_id(pid)
        except Exception as exc:
            import logging
            logging.warning(
                "Chat controller: failed to persist model selection: %s", exc,
            )
        self._set_ai_mode_cfg("local" if category == "local" else "cloud")
        try:
            self._sidebar.set_status(f"Model: {value}", state="ready")
        except Exception:
            pass

    def _set_ai_mode_cfg(self, mode: str) -> None:
        """Write ``opt_ai_mode`` into the live cfg + best-effort persist.

        Shared by the Off path and the model-selection path.  Invalid
        values are ignored.  ``_resolve_provider`` and the diagnostics
        manager both read ``opt_ai_mode``, so keeping it in sync is what
        makes the dropdown actually switch the backend.  Persist
        failures aren't fatal (the in-memory cfg already reflects the
        choice) but are logged so a "my AI keeps reverting" report has
        a trail.
        """
        normalized = str(mode or "").strip().lower()
        if normalized not in ("off", "cloud", "local"):
            return
        try:
            self._cfg["opt_ai_mode"] = normalized  # type: ignore[index]
        except Exception:
            pass
        try:
            from config import save_config
            save_config(self._cfg)
        except Exception as exc:
            import logging
            logging.warning(
                "Chat controller: failed to persist AI mode change: %s", exc,
            )

    def _picker_target(self) -> "tuple[str, str, list[str], str]":
        """Resolve which provider the model dropdown reflects.

        Returns ``(provider_id, category, models, current_model, provider)``.  The
        target is the active provider (``get_active_provider_id``),
        defaulting to ``local`` when none is set.  For local we only hit
        the (blocking) HTTP model list after a fast TCP probe says
        Ollama is up, so this stays cheap when it isn't.
        """
        try:
            from shared.ai.credential_store import (
                get_active_provider_id,
                get_provider_credentials,
            )
            from shared.ai.provider_registry import get_provider
        except Exception:
            return ("local", "local", [], "", None)

        try:
            pid = get_active_provider_id() or "local"
        except Exception:
            pid = "local"
        provider = get_provider(pid)
        if provider is None:
            pid = "local"
            provider = get_provider("local")
        if provider is None:
            return ("local", "local", [], "", None)

        try:
            creds = get_provider_credentials(pid)
        except Exception:
            creds = {}
        if creds:
            try:
                provider.configure(**creds)
            except Exception:
                pass

        category = "local" if pid == "local" else "cloud"
        current = str(creds.get("model", "") or "")
        models: list[str] = []
        if category == "local":
            try:
                if provider.is_available():
                    models = list(provider._get_available_models())
            except Exception:
                models = []
            if not current and models:
                current = models[0]
        else:
            try:
                info = provider.info()
                models = list(info.available_models or [])
                if not current:
                    current = str(
                        info.default_model or (models[0] if models else "")
                    )
            except Exception:
                models = []
        return (pid, category, models, current, provider)

    def refresh_model_picker(self) -> None:
        """Repopulate the chat model dropdown from the active provider.

        Called at construction and whenever the AI Providers dialog
        reports a change.  Shows ``Off`` + the active provider's models;
        embedding/non-chat models are already filtered out by the local
        provider.  Models that won't work right now (Ollama *cloud*
        models when you aren't signed in) are still listed but flagged
        disabled + red so they can't be picked until usable.  The
        selection is ``Off`` when ``opt_ai_mode == "off"``, else the
        configured model.
        """
        try:
            _pid, category, models, current, provider = self._picker_target()
        except Exception:
            return
        mode = str(self._cfg.get("opt_ai_mode", "cloud") or "cloud").lower()
        disabled = self._unusable_models(category, models, provider)
        options: list[tuple[str, str, bool]] = [("", "Off", True)]
        seen: set[str] = set()
        for name in models:
            if name and name not in seen:
                seen.add(name)
                if name in disabled:
                    options.append((name, f"{name}  (needs Ollama sign-in)", False))
                else:
                    options.append((name, name, True))
        current_value = "" if mode == "off" else current
        if current_value and current_value not in seen:
            options.append((current_value, current_value, True))
        try:
            self._sidebar.set_model_options(options, current_value)
        except Exception:
            pass

    @staticmethod
    def _unusable_models(
        category: str, models: "list[str]", provider: Any,
    ) -> "set[str]":
        """Model names that can't be used right now.

        Today: Ollama *cloud* models when the user isn't signed in (they
        403).  Detected via the local provider's one-time cached sign-in
        probe, so a signed-in user keeps their cloud models and we never
        hide a model on a transient error.
        """
        if category != "local" or provider is None:
            return set()
        try:
            from shared.ai.providers.local_provider import _is_cloud_model
        except Exception:
            return set()
        cloud = {m for m in models if _is_cloud_model(m)}
        if not cloud:
            return set()
        try:
            if provider.cloud_models_usable(list(models)):
                return set()
        except Exception:
            return set()
        return cloud

    @Slot()
    def handle_new_chat(self) -> None:
        """User clicked "New Chat" — wipes history + transcript."""
        self._history.clear()
        self._sidebar.clear_transcript()
        self._sidebar.set_status("Ready", state="ready")

    @Slot()
    def handle_copy_chat(self) -> None:
        """User clicked "Copy Chat" — copies transcript to clipboard."""
        from PySide6.QtWidgets import QApplication

        text = self._sidebar.transcript_text()
        if not text.strip():
            self._sidebar.set_status("Nothing to copy yet.", state="ready")
            return
        cb = QApplication.clipboard()
        if cb is None:
            self._sidebar.set_status(
                "Clipboard unavailable.", state="error",
            )
            return
        cb.setText(text)
        self._sidebar.set_status("Copied to clipboard.", state="ready")

    # ── Disc/session context injection ─────────────────────────────

    @staticmethod
    def _reconcile_inserted_disc(facts: dict[str, Any]) -> dict[str, Any]:
        """Drop stale scanned-disc facts when a *different* disc is now
        inserted.

        ``build_ai_session_facts`` reports the last *scanned* disc
        (``last_classification`` etc.), which only updates on a scan — not
        when the user swaps discs.  ``_session_facts`` also surfaces the
        live drive-bar text (the inserted disc's volume label).  If the
        scanned disc's label is absent from the drive-bar text, a disc was
        swapped without re-scanning, so the disc/title facts belong to the
        *previous* disc.  Drop them and add a note, so the assistant
        describes the inserted disc honestly instead of naming the old one
        (the bug a tester hit swapping discs between consecutive rips).
        """
        if not isinstance(facts, dict):
            return facts
        disc = facts.get("disc")
        scanned = ""
        if isinstance(disc, dict):
            scanned = str(
                disc.get("disc_title") or disc.get("volume_id") or ""
            ).strip()
        drive_bar = str(facts.get("drive_bar") or "").strip()
        # Need both signals; only act on a clear disagreement.
        if not scanned or not drive_bar:
            return facts
        if scanned.upper() in drive_bar.upper():
            return facts  # same disc — the scan is current
        stale_keys = {"disc", "titles", "drive", "scan_issue_summary", "session"}
        cleaned = {
            key: value for key, value in facts.items() if key not in stale_keys
        }
        cleaned["disc_context_note"] = (
            f"A disc is inserted (drive shows: {drive_bar}) but it has NOT "
            "been scanned yet, so its titles are unknown. The "
            f'previously-scanned disc ("{scanned}") is no longer in the '
            "drive, so its titles are omitted here. Do NOT describe the "
            "previous disc as if it is the inserted one — tell the user to "
            "scan the current disc to see its contents."
        )
        return cleaned

    def _with_disc_facts(
        self, messages: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Prepend a system message with live disc/session facts.

        Calls ``self._facts_provider`` fresh so the context always
        reflects the most recent scan.  Returns ``messages`` unchanged
        when no provider is wired, it returns nothing, or it raises —
        the chat must never break because facts couldn't be gathered.
        All four providers accept a leading ``system`` message
        (Claude/Gemini fold it into their system field, OpenAI and
        Ollama pass it through natively).
        """
        if self._facts_provider is None:
            return messages
        try:
            facts = self._facts_provider() or {}
        except Exception:
            return messages
        if not facts:
            return messages
        facts = self._reconcile_inserted_disc(facts)
        try:
            facts_json = json.dumps(facts, ensure_ascii=False, default=str)
        except Exception:
            return messages
        # Cap the size so a huge facts blob can't blow the context
        # budget.  Disc + per-title facts (audio/subtitle tracks for
        # every title) are bigger than the old disc-only facts, so the
        # cap is generous but still bounded.
        if len(facts_json) > 12000:
            facts_json = facts_json[:12000] + "…(truncated)"
        system = {
            "role": "system",
            "content": (
                "Live context about the disc and session the user is "
                "currently looking at in JellyRip.  Use it to answer "
                "questions about the disc, its titles, identification, and "
                "the best next step.  If a field is empty or missing, say "
                "so plainly rather than guessing.  Never state a TMDB id "
                "from memory - it's only known from a TMDB lookup (an IMDb "
                "id like 'tt5117670' is NOT a TMDB id).\n\n"
                f"SESSION_FACTS = {facts_json}"
            ),
        }
        return [system, *messages]

    def _with_web_context(
        self,
        messages: list[dict[str, str]],
        *,
        provider: Any = None,
        timeout: float = 20.0,
    ) -> list[dict[str, str]]:
        """Run a TMDB (and optionally web) lookup and prepend the results.

        TMDB is **automatic**: whenever a TMDB key is configured it runs
        on every chat turn, independent of the 🌐 Web toggle — so a user
        who added their key gets exact titles / years / IDs without
        flipping anything on.  The keyless DuckDuckGo web search stays
        behind the Web toggle and is only the backup when TMDB doesn't
        answer.

        Query choice: with the Web toggle ON the model formulates a
        precise query (reused for TMDB).  With it OFF (TMDB-only / auto)
        we use the user's own words directly — no model call — so a slow
        or timing-out local model can never block the TMDB lookup.

        Runs on the chat worker thread (blocking network calls don't
        freeze the UI).  Fully fail-safe: nothing configured, no message,
        no results, or any exception all return ``messages`` unchanged.
        """
        try:
            web_on = bool(self._cfg.get("opt_ai_web_search", False))
            tmdb_key = str(self._cfg.get("opt_tmdb_api_key", "") or "").strip()
            omdb_key = str(self._cfg.get("opt_omdb_api_key", "") or "").strip()
            tvdb_key = str(self._cfg.get("opt_tvdb_api_key", "") or "").strip()
            tvdb_pin = str(self._cfg.get("opt_tvdb_pin", "") or "").strip()
        except Exception:
            return messages
        # Look things up only when the user opted in — a key set, or the
        # Web toggle on.  TVmaze is keyless but must NOT fire on every
        # idle chat turn, so it rides along only once we've decided to
        # look up (it never flips the gate on by itself).
        if not web_on and not tmdb_key and not omdb_key and not tvdb_key:
            return messages

        user_text = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_text = str(msg.get("content") or "").strip()
                break
        if not user_text:
            return messages

        # Precise model-formulated query only when web search is on (it
        # needs precision, and the model also decides whether a search is
        # warranted at all).  TMDB-auto just uses the user's own words.
        formulated = ""
        if web_on and provider is not None:
            try:
                formulated = self._formulate_search_query(
                    provider, user_text, timeout,
                ) or ""
            except Exception:
                formulated = ""

        from shared.ai.web_search import (
            search_web,
            format_for_context as _web_fmt,
        )
        from shared.ai.tmdb_lookup import (
            search_tmdb,
            format_for_context as _tmdb_fmt,
        )

        blocks: list[str] = []
        # TMDB first — the authoritative source for titles/IDs/years.
        # Use the model's query if it made one, else the user's message.
        tmdb_answered = False
        tmdb_query = formulated or user_text
        if tmdb_key and tmdb_query:
            try:
                tmdb_results, _status = search_tmdb(tmdb_query, tmdb_key)
                if tmdb_results:
                    blocks.append(_tmdb_fmt(tmdb_query, tmdb_results))
                    tmdb_answered = True
            except Exception:
                pass
        # OMDb — optional second source (the user opted in with a key).
        # Runs alongside TMDB, so with both keys set the model sees both
        # result sets (OMDb's value-add is the IMDb id).  Counts as
        # "answered" so the keyless web backup is skipped when it returns
        # something.
        if omdb_key and tmdb_query:
            try:
                from shared.ai.omdb_lookup import (
                    search_omdb,
                    format_for_context as _omdb_fmt,
                )
                omdb_results, _ = search_omdb(tmdb_query, omdb_key)
                if omdb_results:
                    blocks.append(_omdb_fmt(tmdb_query, omdb_results))
                    tmdb_answered = True
            except Exception:
                pass
        # TheTVDB — optional paid TV source (key + PIN).  TV-curated, so
        # it helps most on series; counts as "answered" like the others.
        if tvdb_key and tmdb_query:
            try:
                from shared.ai.tvdb_lookup import (
                    search_tvdb,
                    format_for_context as _tvdb_fmt,
                )
                tvdb_results, _ = search_tvdb(tmdb_query, tvdb_key, tvdb_pin)
                if tvdb_results:
                    blocks.append(_tvdb_fmt(tmdb_query, tvdb_results))
                    tmdb_answered = True
            except Exception:
                pass
        # TVmaze — free, keyless TV source.  Rides along on any lookup
        # turn (we're past the opt-in gate above), catching TV shows the
        # movie-centric sources miss and often supplying an IMDb id.
        if tmdb_query:
            try:
                from shared.ai.tvmaze_lookup import (
                    search_tvmaze,
                    format_for_context as _tvmaze_fmt,
                )
                tvmaze_results, _ = search_tvmaze(tmdb_query)
                if tvmaze_results:
                    blocks.append(_tvmaze_fmt(tmdb_query, tvmaze_results))
                    tmdb_answered = True
            except Exception:
                pass
        # Keyless web search: the BACKUP, and only when the Web toggle is
        # on.  Uses the model-formulated query so conversational messages
        # aren't searched verbatim.
        if web_on and not tmdb_answered and formulated:
            try:
                web_results, _status = search_web(formulated)
                if web_results:
                    blocks.append(_web_fmt(formulated, web_results))
            except Exception:
                pass

        if not blocks:
            return messages
        searched_for = tmdb_query if tmdb_answered else (formulated or tmdb_query)
        system = {
            "role": "system",
            "content": (
                f'I looked up "{searched_for}" for the user '
                "(TMDB / OMDb / TheTVDB / TVmaze / web).  "
                "Live results are below - use them to answer; if they don't "
                "cover it, say so rather than guessing.  You DO have these "
                "results, so never claim you can't access an API.\n"
                "TMDB IDs: only state a TMDB id if it appears in a "
                "TMDB_RESULTS block above.  An IMDb id (starts with 'tt', "
                "e.g. tt5117670) is NOT a TMDB id - never present one as a "
                "TMDB id.  If there is no TMDB_RESULTS block then there is "
                "no TMDB match - do NOT guess an id.\n\n"
                + "\n\n".join(blocks)
            ),
        }
        return [system, *messages]

    # Short labels the model may prefix its query with — stripped so the
    # actual query survives.  Kept tight so a real title with a colon
    # (e.g. "Peter Rabbit: The Runaway") isn't mangled.
    _QUERY_LABELS = frozenset(
        {"query", "search query", "search", "web search",
         "here is the query", "here's the query", "answer"}
    )

    def _formulate_search_query(
        self, provider: Any, user_text: str, timeout: float,
    ) -> str:
        """Turn the user's request + disc context into ONE concise search
        query via a short model call.  Returns ``""`` when the model says
        no search is needed (``NONE``) or anything fails — the caller then
        skips searching.  This is what makes web lookup useful: it
        searches the movie/show title, not the user's literal words."""
        facts_json = ""
        if self._facts_provider is not None:
            try:
                facts = self._facts_provider() or {}
                # Same disc-swap reconciliation as the disc-facts path, so
                # the query is grounded in the disc CURRENTLY in the drive,
                # not a held/stale one — otherwise search drifts back to a
                # previous disc across back-to-back rips.
                facts = self._reconcile_inserted_disc(facts)
                if isinstance(facts, dict):
                    # The optical drive's make/model (e.g. "BD-RE BUFFALO")
                    # is hardware, never the search subject — drop it so a
                    # vague query can't latch onto it instead of the movie.
                    facts = {k: v for k, v in facts.items() if k != "drive"}
                if facts:
                    facts_json = json.dumps(
                        facts, ensure_ascii=False, default=str,
                    )[:1500]
            except Exception:
                facts_json = ""
        prompt = [
            {
                "role": "system",
                "content": (
                    "You write a web search query about the MOVIE/SHOW on "
                    "the disc currently in the drive.  Anchor the query on "
                    "the disc's title (its disc_title / volume label, e.g. "
                    "'Spirit') - that is the movie's name.  NEVER use the "
                    "optical drive's make/model (e.g. 'BD-RE BUFFALO Optical "
                    "Drive') - that is hardware, not the movie.  Output ONE "
                    "short query (about 3-8 words), ONLY the query - no "
                    "quotes, no label, no explanation.  Requests to identify "
                    "the disc or get its year / title / id / metadata - "
                    "INCLUDING 'use the api', 'check the api', 'look it up' - "
                    "are lookups: output the disc's title as the query.  "
                    "Output exactly NONE only when the disc context names no "
                    "movie/show, or the message clearly isn't about "
                    "identifying the disc (e.g. 'forget that', 'thanks', "
                    "'look at the log').  NEVER invent a topic that isn't in "
                    "the disc context - no streaming services, no titles "
                    "from earlier in the chat."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Disc context: {facts_json or '(none)'}\n\n"
                    f"User request: {user_text}\n\nSearch query:"
                ),
            },
        ]
        try:
            raw = provider.chat(prompt, max_tokens=40, timeout=timeout)
        except Exception:
            return ""
        line = next(
            (ln.strip() for ln in str(raw or "").splitlines() if ln.strip()),
            "",
        )
        # Strip a leading "Query:"-style label if the model added one.
        if ":" in line:
            head, _, tail = line.partition(":")
            if head.strip().lower() in self._QUERY_LABELS and tail.strip():
                line = tail.strip()
        line = line.strip().strip('"').strip("'").strip()
        if not line or line.upper() == "NONE":
            return ""
        return line[:120]

    # ── Worker thread ──────────────────────────────────────────────

    def _worker_call(
        self,
        messages: list[dict[str, str]],
        replay_id: str = "",
    ) -> None:
        """Run the provider call.  All exceptions surface via
        ``error_occurred``; success surfaces via ``response_ready``.

        ``replay_id`` correlates this turn's request / response /
        error records in the replay JSONL log.  Empty string means
        the caller didn't bother (test fixtures); replay logging is
        skipped in that case.

        On-device fallback paths:

        * **Provider unavailable + UI-help prompt** — instead of the
          bare "no provider configured" error, generate a
          ``build_ui_help_fallback`` summary so the user gets useful
          state even without AI.
        * **Provider succeeds but echoes the request payload** —
          some smaller models do this.  Detect via
          ``looks_like_ai_payload_echo`` and replace with the
          on-device summary.
        * **Provider errors on a UI-help prompt** — same fallback
          so the user still gets something actionable.
        """
        prompt = messages[-1]["content"] if messages else ""
        is_ui_help = prompt_looks_like_ui_help(prompt)

        try:
            provider, label, timeout = self._resolve_provider()
            if provider is None:
                if is_ui_help:
                    fallback = self._build_fallback_answer()
                    if fallback:
                        self.response_ready.emit(fallback, "fallback")
                        self._record_replay(
                            "response",
                            replay_id=replay_id,
                            backend="fallback",
                            request_text=prompt,
                            response_text=fallback,
                            details={"reason": "no_provider_configured"},
                        )
                        return
                err_msg = (
                    "No AI provider is configured.  Open AI Providers from "
                    "the toolbar to set one up."
                )
                self.error_occurred.emit(err_msg)
                self._record_replay(
                    "error",
                    replay_id=replay_id,
                    backend=label,
                    request_text=prompt,
                    error_text=err_msg,
                )
                return

            try:
                reply = provider.chat(
                    self._with_web_context(
                        self._with_disc_facts(messages),
                        provider=provider,
                        timeout=timeout,
                    ),
                    max_tokens=_DEFAULT_MAX_TOKENS,
                    timeout=timeout,
                )
            except Exception as exc:  # noqa: BLE001 — provider contract raises
                if is_ui_help:
                    fallback = self._build_fallback_answer(error_message=str(exc))
                    if fallback:
                        self.response_ready.emit(fallback, "fallback")
                        self._record_replay(
                            "response",
                            replay_id=replay_id,
                            backend="fallback",
                            request_text=prompt,
                            response_text=fallback,
                            details={
                                "reason": "provider_error_with_ui_help_prompt",
                                "source_error": str(exc)[:240],
                            },
                        )
                        return
                friendly = self._friendly_error(label, str(exc))
                self.error_occurred.emit(friendly)
                self._record_replay(
                    "error",
                    replay_id=replay_id,
                    backend=label,
                    request_text=prompt,
                    error_text=str(exc),
                    details={"friendly_message": friendly},
                )
                return

            text = (reply or "").strip() or "(no response)"

            # Echo guard: a model that returns the request payload
            # verbatim isn't actually answering.  Swap to the
            # on-device summary so the user sees something useful.
            if looks_like_ai_payload_echo(text):
                fallback = self._build_fallback_answer()
                if fallback:
                    self.response_ready.emit(fallback, "fallback")
                    self._log_workflow_event(
                        "ai_chat_response",
                        backend="fallback",
                        prompt_len=len(prompt),
                        response_len=len(fallback),
                        reason="payload_echo",
                    )
                    self._record_replay(
                        "response",
                        replay_id=replay_id,
                        backend="fallback",
                        request_text=prompt,
                        response_text=fallback,
                        details={
                            "reason": "payload_echo",
                            "source_backend": label,
                            "echoed_response_excerpt": text[:240],
                        },
                    )
                    return

            self.response_ready.emit(text, label)
            self._log_workflow_event(
                "ai_chat_response",
                backend=label,
                prompt_len=len(prompt),
                response_len=len(text),
            )
            self._record_replay(
                "response",
                replay_id=replay_id,
                backend=label,
                request_text=prompt,
                response_text=text,
            )

        except Exception as exc:  # noqa: BLE001 — top-level worker safety net
            crash_msg = (
                f"Chat worker crashed: {exc}.  This is likely a bug — please report it."
            )
            self.error_occurred.emit(crash_msg)
            self._record_replay(
                "error",
                replay_id=replay_id,
                request_text=prompt,
                error_text=str(exc),
                details={"reason": "worker_crash"},
            )

    def _build_fallback_answer(self, *, error_message: str = "") -> str | None:
        """Build the on-device summary using the live MainWindow
        snapshot.  Returns ``None`` if no snapshot helper is reachable
        (e.g., the controller's parent isn't a MainWindow — happens
        in unit tests that construct controllers without a real
        window).
        """
        snapshot = self._collect_ui_snapshot()
        if snapshot is None:
            return None
        log_tail = str(snapshot.get("live_log_tail", "") or "")
        return build_ui_help_fallback(snapshot, log_tail, error_message)

    def _collect_ui_snapshot(self) -> Mapping[str, object] | None:
        """Reach up to the MainWindow for a UI snapshot.  Tolerant of
        controllers that don't have a window parent (test fixtures)."""
        parent = self.parent()
        getter = getattr(parent, "get_chat_ui_snapshot", None)
        if getter is None:
            return None
        try:
            return getter()
        except Exception:
            return None

    def _log_workflow_event(self, kind: str, **fields: Any) -> None:
        """Best-effort workflow-history log.  Failures swallowed —
        the chat path must not break because logging did."""
        try:
            from shared.workflow_history import append_workflow_event
            append_workflow_event(kind, dict(fields))
        except Exception:
            pass

    def _record_replay(
        self,
        phase: str,
        *,
        replay_id: str = "",
        backend: str = "",
        request_text: str = "",
        display_text: str = "",
        response_text: str = "",
        error_text: str = "",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        """Best-effort write into ``ai_chat_replay.jsonl``.  Skips
        silently if no ``replay_id`` was issued (test fixtures
        constructing controllers without a real turn) or if the
        write fails — the chat path must not break because debug
        logging did.

        Three phases land here:

        * ``request`` — emitted at submit time, captures the prompt
          and the history depth.
        * ``response`` — emitted on success (or fallback success),
          captures the answer and the backend label.
        * ``error`` — emitted on failure, captures the raw provider
          error + the friendly message shown to the user.

        Phase contract matches the tkinter side
        (``_record_ai_chat_replay`` in ``gui/main_window.py``) so
        existing replay-bundle viewers still parse our records.
        """
        if not replay_id:
            return
        try:
            from shared.ai_chat_replay import append_ai_chat_replay
            append_ai_chat_replay(
                phase,
                replay_id=replay_id,
                title="AI Chat (Qt)",
                backend=backend,
                request_text=request_text,
                display_text=display_text or request_text,
                response_text=response_text,
                error_text=error_text,
                details=dict(details or {}),
            )
        except Exception:
            pass

    def _resolve_provider(self) -> tuple[Any, str, float]:
        """Pick the active provider per ``opt_ai_mode``.

        Returns ``(provider_or_none, label, timeout)``.  The label is
        ``"cloud"`` / ``"local"`` / ``"off"`` — used for status text
        and workflow logging.
        """
        mode = str(self._cfg.get("opt_ai_mode", "cloud") or "cloud").lower()
        if mode == "off":
            return (None, "off", _DEFAULT_TIMEOUT_CLOUD)

        try:
            from shared.ai.provider_registry import (
                resolve_active_cloud_provider,
                resolve_local_provider,
            )
        except Exception:
            return (None, mode, _DEFAULT_TIMEOUT_CLOUD)

        cloud_timeout = float(
            self._cfg.get("opt_ai_cloud_timeout_seconds", _DEFAULT_TIMEOUT_CLOUD)
        )
        local_timeout = max(
            float(self._cfg.get("opt_ai_local_timeout_seconds", _DEFAULT_TIMEOUT_LOCAL)),
            5.0,
        )

        if mode == "local":
            local = self._safe_resolve(resolve_local_provider)
            return (local, "local", local_timeout) if local else (None, "local", local_timeout)

        # mode == "cloud" (default).  Try cloud first, fall back to local.
        if bool(self._cfg.get("opt_ai_cloud_enabled", True)):
            cloud = self._safe_resolve(resolve_active_cloud_provider)
            if cloud is not None:
                return (cloud, "cloud", cloud_timeout)

        if bool(self._cfg.get("opt_ai_local_enabled", True)):
            local = self._safe_resolve(resolve_local_provider)
            if local is not None:
                return (local, "local", local_timeout)

        return (None, mode, cloud_timeout)

    @staticmethod
    def _safe_resolve(fn) -> Any:
        try:
            provider = fn()
        except Exception:
            return None
        if provider is None:
            return None
        try:
            if not provider.is_available():
                return None
        except Exception:
            return None
        return provider

    @staticmethod
    def _friendly_error(backend_label: str, raw: str) -> str:
        """Build a short user-facing error.  Uses the same friendly
        wording cues as the tkinter ``_friendly_ai_chat_error``
        helper without copying the exact message — those messages
        live in ``gui/main_window.py:271`` and would drift if
        forked."""
        lowered = raw.lower()
        if "timeout" in lowered or "timed out" in lowered:
            if backend_label == "local":
                return (
                    "Local model timed out.  Try a smaller model from "
                    "AI Providers, or reduce the prompt size."
                )
            return (
                "Cloud provider timed out.  Check your network or try "
                "again — the request may have been queued."
            )
        if "401" in raw or "unauthor" in lowered:
            return (
                "API key was rejected.  Open AI Providers from the "
                "toolbar and re-enter the key."
            )
        if "429" in raw or "rate" in lowered or "quota" in lowered:
            return (
                "Rate limited / out of quota.  Check billing or wait "
                "a moment, then retry."
            )
        # Truncate to a sensible length so the chat bubble doesn't
        # bloat with a huge stack trace.
        return raw[:240] if raw else "Provider returned an unspecified error."

    # ── GUI-thread response/error slots ────────────────────────────

    @Slot(str, str)
    def _on_response_ready(self, text: str, backend_label: str) -> None:
        self._history.append({"role": "assistant", "content": text})
        self._sidebar.append_assistant_message(text)
        self._sidebar.set_busy(False)
        self._sidebar.set_status(f"Ready via {backend_label}", state="ready")
        self._busy = False
        self._sidebar.focus_input()

    @Slot(str)
    def _on_error_occurred(self, message: str) -> None:
        # Show the error inline so the user has something to read.
        # Don't add it to ``_history`` — failed turns shouldn't
        # poison the next prompt's context.
        self._sidebar.append_assistant_message(f"⚠ {message}")
        self._sidebar.set_busy(False)
        self._sidebar.set_status("Error", state="error")
        self._busy = False
        self._sidebar.focus_input()

    # ── Auto disc identification (TMDB on drive reload) ────────────

    def reset_disc_identify(self) -> None:
        """Forget the last auto-identified disc so the next identify
        fires even for the same disc — used by an explicit drive reload
        (the Reload button by the drive bar)."""
        self._last_identified_disc = ""

    def _log_disc(self, msg: str) -> None:
        """Diagnostic line to the main window log pane (GUI thread only)."""
        try:
            parent = self.parent()
            if parent is not None and hasattr(parent, "append_log"):
                parent.append_log(f"[disc-id] {msg}")
        except Exception:
            pass

    def identify_disc_async(self, disc_name: str) -> None:
        """Auto-identify a freshly-loaded disc and post the result to the
        chat.  Called by the drive handler when the drive reloads.

        Queries up to four sources: TMDB and OMDb (each needs the user's
        free key), TheTVDB (optional, paid key + PIN), and **TVmaze**,
        which is free and keyless — so identification runs even when the
        user has configured nothing at all.  Deduped so the same disc
        isn't re-identified on every refresh.  The network calls run on a
        worker thread and the post lands on the GUI thread via
        ``disc_identified`` — never touching the user-turn busy flag or
        history.
        """
        name = str(disc_name or "").strip()
        if not name:
            self._log_disc("skip: empty disc name")
            return
        if name == self._last_identified_disc:
            self._log_disc(f"skip: already identified {name!r}")
            return
        tmdb_key = str(self._cfg.get("opt_tmdb_api_key", "") or "").strip()
        omdb_key = str(self._cfg.get("opt_omdb_api_key", "") or "").strip()
        tvdb_key = str(self._cfg.get("opt_tvdb_api_key", "") or "").strip()
        tvdb_pin = str(self._cfg.get("opt_tvdb_pin", "") or "").strip()
        # TVmaze is keyless, so there's always at least one source — no
        # "no key" early-out anymore.
        self._last_identified_disc = name
        providers = " + ".join(
            p for p, on in (
                ("TMDB", tmdb_key),
                ("OMDb", omdb_key),
                ("TheTVDB", tvdb_key),
                ("TVmaze", True),  # always
            ) if on
        )
        self._log_disc(f"looking up {name!r} via {providers}...")
        threading.Thread(
            target=self._identify_disc_worker,
            args=(name, tmdb_key, omdb_key, tvdb_key, tvdb_pin),
            daemon=True,
            name="disc-identify",
        ).start()

    @staticmethod
    def _results_agree(tmdb_r: Any, omdb_r: Any) -> bool:
        """True when TMDB's and OMDb's top hits look like the SAME
        title: same punctuation/case-insensitive name, and same year
        when both services report one."""
        import re

        def norm(s: str) -> str:
            return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()

        if norm(tmdb_r.title) != norm(omdb_r.title):
            return False
        if tmdb_r.year and omdb_r.year and tmdb_r.year != omdb_r.year:
            return False
        return True

    @staticmethod
    def _clean_disc_label(disc_name: str) -> str:
        """Make a volume label more TMDB-searchable: ``_ - . +`` become
        spaces and runs of whitespace collapse.  So
        ``SPONGEBOB_SPONGE_OUT_OF_WATER`` -> ``SPONGEBOB SPONGE OUT OF
        WATER``.  Cryptic labels (e.g. ``L3M0NNW1``) just won't match,
        which is fine — auto-identify is best-effort."""
        s = str(disc_name or "")
        for ch in ("_", "-", ".", "+"):
            s = s.replace(ch, " ")
        return " ".join(s.split()).strip()

    @staticmethod
    def _native_id_line(source: str, r: Any) -> str:
        """The provider-native ID line for the identification card.
        OMDb's native id is the IMDb id, handled separately below."""
        if source == "TMDB":
            return f"- TMDB ID: {r.media_type}/{r.tmdb_id}"
        if source == "TheTVDB":
            return f"- TheTVDB ID: {r.tvdb_id}"
        if source == "TVmaze":
            return f"- TVmaze ID: {r.tvmaze_id}"
        return ""

    def _identify_disc_worker(
        self,
        disc_name: str,
        tmdb_key: str,
        omdb_key: str = "",
        tvdb_key: str = "",
        tvdb_pin: str = "",
    ) -> None:
        query = self._clean_disc_label(disc_name) or disc_name

        # Query each available source.  Per-provider failures are
        # swallowed so one bad/absent source never blocks the others.
        # TVmaze is keyless and always runs; the others need their key.
        def _top(import_call):
            try:
                res, _ = import_call()
                return res[0] if res else None
            except Exception:
                return None

        tmdb_r = omdb_r = tvdb_r = tvmaze_r = None
        if tmdb_key:
            def _tmdb():
                from shared.ai.tmdb_lookup import search_tmdb
                return search_tmdb(query, tmdb_key)
            tmdb_r = _top(_tmdb)
        if omdb_key:
            def _omdb():
                from shared.ai.omdb_lookup import search_omdb
                return search_omdb(query, omdb_key)
            omdb_r = _top(_omdb)
        if tvdb_key:
            def _tvdb():
                from shared.ai.tvdb_lookup import search_tvdb
                return search_tvdb(query, tvdb_key, tvdb_pin)
            tvdb_r = _top(_tvdb)

        def _tvmaze():
            from shared.ai.tvmaze_lookup import search_tvmaze
            return search_tvmaze(query)
        tvmaze_r = _top(_tvmaze)

        # Canonical priority: TMDB (richest, movies+TV) → TheTVDB
        # (paid, curated TV) → TVmaze (free TV) → OMDb (IMDb only).
        ordered = [
            ("TMDB", tmdb_r),
            ("TheTVDB", tvdb_r),
            ("TVmaze", tvmaze_r),
            ("OMDb", omdb_r),
        ]
        present = [(name, r) for name, r in ordered if r is not None]

        if not present:
            # Miss/error -> Live Log only (chat_md="") so an unmatchable
            # disc doesn't spam the transcript on every reload.
            self.disc_identified.emit(
                "",
                f'Disc "{disc_name}" — no match (searched "{query}"). '
                f"Type the title in chat to look it up.",
            )
            return

        canon_name, canon = present[0]
        others = present[1:]
        # Sources whose top hit independently agrees with the canonical
        # one (same normalized title + year) — shown as a cross-check so
        # the user can trust a multi-source match over a lone fuzzy hit.
        agreeing = [n for n, r in others if self._results_agree(canon, r)]

        # IMDb id: the canonical result's own if it carries one (TVmaze,
        # TheTVDB, and OMDb do; TMDB does not), else borrow it from an
        # AGREEING source — never from a disagreeing one, or the card
        # would pair this title with a different film's IMDb id.
        imdb_id = str(getattr(canon, "imdb_id", "") or "")
        imdb_src = canon_name if imdb_id else ""
        if not imdb_id:
            for n, r in others:
                rid = str(getattr(r, "imdb_id", "") or "")
                if rid and self._results_agree(canon, r):
                    imdb_id, imdb_src = rid, n
                    break

        kind = "Movie" if getattr(canon, "media_type", "tv") == "movie" else "TV"
        year = f" ({canon.year})" if canon.year else ""
        lines = [
            f'**Auto-identified the inserted disc** ("{disc_name}"):',
            f"- Title: {canon.title}{year}",
            f"- Type: {kind}",
        ]
        native = self._native_id_line(canon_name, canon)
        if native:
            lines.append(native)
        if imdb_id:
            via = (
                f" (via {imdb_src})"
                if imdb_src and imdb_src != canon_name
                else ""
            )
            lines.append(f"- IMDb ID: {imdb_id}{via}")
        chat_md = "\n".join(lines)

        srcs = canon_name + (
            " + " + " + ".join(agreeing) if agreeing else ""
        )
        log_line = f"Disc identified via {srcs}: {canon.title}{year}"
        self.disc_identified.emit(chat_md, log_line)

    @Slot(str, str)
    def _on_disc_identified(self, chat_md: str, log_line: str) -> None:
        """Deliver an auto-identification result on the GUI thread.

        ``log_line`` always goes to the main-window Live Log — visible
        even when the chat sidebar is hidden (which it is by default), so
        the user actually sees that identification happened.  ``chat_md``
        is the richer transcript note, posted only on a positive match
        ("" for misses/errors) so the chat isn't spammed.  Never touches
        the user-turn busy flag or history.
        """
        if log_line:
            try:
                parent = self.parent()
                if parent is not None and hasattr(parent, "append_log"):
                    parent.append_log(log_line)
            except Exception:
                pass
        if chat_md:
            try:
                self._sidebar.append_assistant_message(chat_md)
            except Exception:
                pass

    # ── Welcome message (called by app.py once on construction) ───

    def show_welcome(self) -> None:
        """Render a friendly first-time message into the transcript.

        Idempotent — only runs when the transcript is empty so
        re-opening the sidebar doesn't spam duplicates.
        """
        if self._sidebar.transcript_text().strip():
            return
        self._sidebar.append_assistant_message(
            f"Hi — I'm the {APP_DISPLAY_NAME} assistant.  "
            "Ask me about a disc you're ripping, an ambiguous title, "
            "or what a config option does.  Press **Suggest Next Step** "
            "for a take on the current session, or **New Chat** to "
            "start over."
        )
