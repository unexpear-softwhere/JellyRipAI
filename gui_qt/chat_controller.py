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
from typing import TYPE_CHECKING, Any, Mapping

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

    def __init__(
        self,
        sidebar: "ChatSidebar",
        cfg: Mapping[str, Any],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._sidebar = sidebar
        self._cfg = cfg
        self._busy = False
        # Multi-turn message history.  Each element is a dict
        # {"role": "user" | "assistant", "content": str}.
        self._history: list[dict[str, str]] = []

        # Sidebar -> controller hooks.
        sidebar.message_submitted.connect(self.handle_message_submitted)
        sidebar.suggest_requested.connect(self.handle_suggest_requested)
        sidebar.new_chat_requested.connect(self.handle_new_chat)
        sidebar.copy_chat_requested.connect(self.handle_copy_chat)
        sidebar.mode_changed.connect(self.handle_mode_changed)

        # Seed the sidebar's mode picker from the current cfg value
        # WITHOUT firing the changed signal back at us — the cfg
        # already has this value, no save needed at construction.
        sidebar.set_mode(str(cfg.get("opt_ai_mode", "cloud") or "cloud"))

        # Worker thread -> GUI thread hooks.
        self.response_ready.connect(self._on_response_ready)
        self.error_occurred.connect(self._on_error_occurred)

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
    def handle_mode_changed(self, mode: str) -> None:
        """User flipped the AI mode picker.  Writes the new value into
        ``cfg["opt_ai_mode"]`` and (best-effort) persists via
        ``save_config`` so the choice survives restarts.

        Three modes match the cfg key:

        * ``"off"``   — no AI calls; on-device fallback still runs
                        for "what's happening?" prompts.
        * ``"cloud"`` — try active cloud provider, fall back to local
                        if cloud isn't configured.
        * ``"local"`` — local provider only.

        The change applies immediately — the next ``handle_message_submitted``
        call routes via ``_resolve_provider`` which reads the live
        ``cfg`` dict.  No restart needed.
        """
        normalized = str(mode or "").strip().lower()
        if normalized not in ("off", "cloud", "local"):
            return  # ignore garbage
        # Mutate the live cfg dict so _resolve_provider sees the new
        # value on the next turn.  ChatController._cfg is a Mapping in
        # type signature but in practice the runtime cfg is a dict.
        try:
            self._cfg["opt_ai_mode"] = normalized  # type: ignore[index]
        except Exception:
            # If the cfg is genuinely immutable, silently degrade —
            # the user's runtime choice still applies for the next
            # provider lookup because resolve_provider re-reads cfg
            # each time.
            pass
        # Best-effort persistence so the choice survives a restart.
        # Failures here aren't fatal (the in-memory cfg already
        # reflects the user's choice), but they were previously
        # invisible — a disk-full or locked-config.json save error
        # would mean the mode reset on next launch with no
        # explanation.  Log so a "my mode keeps reverting" report
        # has a trail.
        try:
            from config import save_config
            save_config(self._cfg)
        except Exception as exc:
            import logging
            logging.warning(
                "Chat controller: failed to persist AI mode change: %s", exc,
            )
        # Surface the change in the status line so the user sees it
        # took effect.
        labels = {"off": "Off", "cloud": "Cloud", "local": "Local"}
        self._sidebar.set_status(
            f"AI mode: {labels.get(normalized, normalized)}",
            state="ready",
        )

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
                    messages,
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
