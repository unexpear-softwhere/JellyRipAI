"""
AI Diagnostics Manager for JellyRip.

Central diagnostic bus that captures events at the lowest layers (subprocess,
engine, controller) and fans out to:
  - GUI log panel (human-readable)
  - System log (%LOCALAPPDATA%/JellyRipAI/logs/system.log — always writes,
    survives GUI failure)
  - session.log (normal runtime log)
  - session.ai.log (AI diagnoses and suggested fixes)
  - session.state.json (structured machine-readable context snapshots)
  - In-memory ring buffer (last N events, survives GUI choke)

AI is only called on meaningful failure events, not every log line.
Mode is "suggest fixes" — never auto-patches code, never blocks pipeline.

Backend architecture:
  Layer 1: DiagnosticsManager (event collection, safety, routing)
  Layer 2: shared/ai/providers/ (provider adapters via provider_registry)
  Layer 3: Backend selector (cloud first, local fallback, offline graceful)

Non-blocking guarantee: all AI calls run in daemon threads. AI failure
(including out-of-tokens) is treated as optional, never fatal.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import traceback
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

_logger = logging.getLogger("ai_diagnostics")


# ---------------------------------------------------------------------------
# Diagnostic event model
# ---------------------------------------------------------------------------

@dataclass
class DiagnosticEvent:
    """Single captured event in the diagnostic pipeline."""
    timestamp: str
    level: str                          # "info", "warning", "error", "critical"
    category: str                       # "subprocess", "scan", "rip", "move", ...
    summary: str                        # One-line human description
    details: dict[str, Any] = field(default_factory=dict)
    ai_diagnosis: Optional[str] = None  # Filled after AI call

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Subprocess capture record
# ---------------------------------------------------------------------------

@dataclass
class ProcessCapture:
    """Raw subprocess output captured before any parsing."""
    command: list[str]
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    start_time: str = ""
    end_time: str = ""
    duration_seconds: float = 0.0
    timeout_reason: str = ""
    working_directory: str = ""
    stall_detected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# AI trigger conditions
# ---------------------------------------------------------------------------

AI_TRIGGER_CATEGORIES = frozenset({
    "uncaught_exception",
    "subprocess_nonzero_exit",
    "stall_timeout",
    "output_validation_failure",
    "move_verify_failure",
    "scan_anomaly",
    "repeated_retry",
    "low_confidence",
    "file_stabilization_failure",
    "disc_read_error",
    "permission_error",
    "network_share_failure",
    "rip_no_output_files",
})

# Categories that warrant cloud-level reasoning (serious failures).
# Everything else in AI_TRIGGER_CATEGORIES can use local.
_CLOUD_PREFERRED_CATEGORIES = frozenset({
    "uncaught_exception",
    "subprocess_nonzero_exit",
    "rip_no_output_files",
    "repeated_retry",
    "disc_read_error",
})


# ---------------------------------------------------------------------------
# System prompts for AI
# ---------------------------------------------------------------------------

_DIAGNOSIS_SYSTEM_PROMPT = (
    "You are an expert diagnostic assistant embedded in JellyRip, a Blu-ray/DVD disc ripper application. "
    "JellyRip uses MakeMKV (makemkvcon) for disc ripping, ffprobe for file analysis, and ffmpeg/HandBrake for transcoding. "
    "You are analyzing a failure event that occurred during a rip session.\n\n"
    "Respond in this exact format:\n"
    "WHAT FAILED: (one line)\n"
    "LIKELY CAUSE: (one line)\n"
    "CONFIDENCE: (high/medium/low)\n"
    "INSPECT: (file paths and line numbers to check)\n"
    "SUGGESTED FIX: (specific code change or config change)\n"
    "USER MESSAGE: (plain-English message suitable for the GUI log)\n"
    "RETRY SAFE: (yes/no and why)\n"
)

_SUMMARY_SYSTEM_PROMPT = (
    "You are reviewing a completed JellyRip disc ripping session. "
    "Summarize what happened, what went wrong (if anything), and "
    "recommend specific code or config fixes for next time. "
    "Be concise and actionable."
)


# ---------------------------------------------------------------------------
# Token exhaustion / quota error detection
# ---------------------------------------------------------------------------

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


def _is_quota_error(error: Exception) -> bool:
    """Detect token exhaustion, rate limits, and quota errors."""
    msg = str(error).lower()
    return any(pattern in msg for pattern in _QUOTA_ERROR_PATTERNS)


# ---------------------------------------------------------------------------
# Session context snapshot (sent with every AI request)
# ---------------------------------------------------------------------------

def _build_session_context(manager: DiagnosticsManager) -> dict[str, Any]:
    """Gather session context for AI request payload."""
    ctx: dict[str, Any] = {}
    try:
        ctx["app_version"] = manager._app_version
        ctx["disc_title"] = manager._disc_title
        ctx["session_mode"] = manager._session_mode
        ctx["pipeline_step"] = manager._pipeline_step
        ctx["retry_count"] = manager._retry_count
        ctx["drive_index"] = manager._config.get("opt_drive_index", 0) if manager._config else None
        ctx["temp_folder"] = manager._config.get("temp_folder", "") if manager._config else None
        ctx["movies_folder"] = manager._config.get("movies_folder", "") if manager._config else None
        ctx["tv_folder"] = manager._config.get("tv_folder", "") if manager._config else None

        # Recent log tail (last 30 lines from ring buffer)
        recent = list(manager._ring_buffer)[-30:]
        ctx["recent_log_tail"] = [e.summary for e in recent]

        # Active config flags relevant to diagnostics
        if manager._config:
            diag_keys = [
                "opt_safe_mode", "opt_stall_detection", "opt_stall_timeout_seconds",
                "opt_auto_retry", "opt_retry_attempts", "opt_file_stabilization",
                "opt_stabilize_timeout_seconds", "opt_expected_size_ratio_pct",
                "opt_atomic_move", "opt_fsync",
            ]
            ctx["active_config"] = {k: manager._config.get(k) for k in diag_keys}
    except Exception as e:
        ctx["context_error"] = str(e)
    return ctx


# ---------------------------------------------------------------------------
# Main diagnostics manager
# ---------------------------------------------------------------------------

class DiagnosticsManager:
    """Central AI diagnostic bus for a JellyRip session.

    This is the ONLY place in the app that decides:
    - which backend to call (cloud vs local)
    - when to fall back
    - when to disable AI entirely

    AI is non-blocking and advisory only. It never controls the pipeline,
    modifies files, or overrides user choices. It only explains failures,
    suggests fixes, and annotates logs.

    No other module should make backend routing decisions.
    """

    def __init__(
        self,
        config: Optional[dict[str, Any]] = None,
        gui_log_fn: Optional[Callable[[str], None]] = None,
        session_dir: Optional[str] = None,
        ring_buffer_size: int = 500,
    ):
        self._config = config or {}
        self._gui_log_fn = gui_log_fn
        self._session_dir = session_dir
        self._lock = threading.Lock()

        # Ring buffer
        self._ring_buffer: deque[DiagnosticEvent] = deque(maxlen=ring_buffer_size)

        # Session context (updated by controller as session progresses)
        self._app_version = ""
        self._disc_title = ""
        self._session_mode = ""
        self._pipeline_step = ""
        self._retry_count = 0

        # File handles (opened lazily)
        self._ai_log_path: Optional[str] = None
        self._state_json_path: Optional[str] = None
        self._session_log_path: Optional[str] = None

        # System logger (persistent, survives GUI failure)
        self._system_logger: Optional[Any] = None
        try:
            from shared.ai.diagnostics import get_system_logger, init_system_logger
            self._system_logger = get_system_logger()
            if self._system_logger is None:
                self._system_logger = init_system_logger(gui_log_fn=gui_log_fn)
        except Exception:
            pass

        # Log credential storage mode on init
        try:
            from shared.ai.credential_store import get_storage_label
            self._sys_log("info", f"[AI] Credential storage: {get_storage_label()}")
        except Exception:
            pass

        # AI state
        self._ai_enabled = bool(self._config.get("opt_ai_diagnostics_enabled", True))
        self._ai_suggest_mode = str(self._config.get("opt_ai_diagnostics_mode", "suggest"))
        self._ai_log_to_gui = bool(self._config.get("opt_ai_log_to_gui", True))
        self._ai_log_to_file = bool(self._config.get("opt_ai_log_to_file", True))
        self._ai_call_count = 0
        self._ai_max_calls_per_session = int(
            self._config.get("opt_ai_max_calls_per_session", 20)
        )
        self._ai_consecutive_failures = 0
        self._ai_max_consecutive_failures = int(
            self._config.get("opt_ai_disable_after_failures", 3)
        )
        self._ai_disabled_reason: str = ""
        self._ai_quota_disabled_until: float = 0.0  # timestamp for temp disable

        # Backend routing mode: "off" | "cloud" | "local"
        self._ai_mode = str(self._config.get("opt_ai_mode", "cloud"))
        if self._ai_mode not in ("off", "cloud", "local"):
            self._ai_mode = "cloud"
        if self._ai_mode == "off":
            self._ai_enabled = False

        # Backend config
        self._cloud_timeout = float(
            self._config.get("opt_ai_cloud_timeout_seconds", 30)
        )
        self._local_timeout = float(
            self._config.get("opt_ai_local_timeout_seconds", 20)
        )

        # Provider availability tracking
        self._cloud_available = True
        self._local_available = True

        # Runtime status for UI
        self._cloud_last_latency_ms: Optional[float] = None
        self._local_last_latency_ms: Optional[float] = None
        self._last_failure_reason: str = ""

        # Process captures (for raw subprocess output)
        self._process_captures: list[ProcessCapture] = []

        if session_dir:
            self._init_log_files(session_dir)

    def _init_log_files(self, session_dir: str) -> None:
        """Create log file paths (files created on first write)."""
        os.makedirs(session_dir, exist_ok=True)
        self._ai_log_path = os.path.join(session_dir, "session.ai.log")
        self._state_json_path = os.path.join(session_dir, "session.state.json")
        self._session_log_path = os.path.join(session_dir, "session.log")

    def set_session_dir(self, session_dir: str) -> None:
        """Update session directory (e.g., after temp folder is resolved)."""
        self._session_dir = session_dir
        self._init_log_files(session_dir)

    def update_context(self, **kwargs: Any) -> None:
        """Update session context fields (disc_title, pipeline_step, etc.)."""
        for key, value in kwargs.items():
            if hasattr(self, f"_{key}"):
                setattr(self, f"_{key}", value)

    def set_config(self, config: dict[str, Any]) -> None:
        """Update config reference (call after config reload)."""
        self._config = config
        self._ai_enabled = bool(config.get("opt_ai_diagnostics_enabled", True))
        self._ai_suggest_mode = str(config.get("opt_ai_diagnostics_mode", "suggest"))
        self._ai_log_to_gui = bool(config.get("opt_ai_log_to_gui", True))
        self._ai_log_to_file = bool(config.get("opt_ai_log_to_file", True))
        new_mode = str(config.get("opt_ai_mode", self._ai_mode))
        if new_mode in ("off", "cloud", "local"):
            self.set_mode(new_mode)

    def set_mode(self, mode: str) -> None:
        """Set AI backend mode at runtime. Called by UI toggle.

        Modes:
          "off"   — no AI calls, diagnostics still log normally
          "cloud" — cloud backend, local fallback on failure
          "local" — local model only, fully offline
        """
        if mode not in ("off", "cloud", "local"):
            return
        self._ai_mode = mode
        if mode == "off":
            self._ai_enabled = False
        else:
            # Re-enable if previously off (but not if circuit-breaker disabled)
            if not self._ai_disabled_reason:
                self._ai_enabled = True
        self._write_ai_log(f"[AI] Mode set to: {mode}")
        self._sys_log("event", f"AI mode changed to: {mode}")

    def get_status(self) -> dict[str, Any]:
        """Return current AI status for UI display."""
        if self._ai_mode == "off":
            state = "off"
        elif self._ai_disabled_reason:
            state = "disabled"
        elif self._ai_consecutive_failures > 0:
            state = "degraded"
        else:
            state = "active"

        return {
            "mode": self._ai_mode,
            "state": state,
            "cloud_available": self._cloud_available,
            "local_available": self._local_available,
            "calls_made": self._ai_call_count,
            "calls_max": self._ai_max_calls_per_session,
            "consecutive_failures": self._ai_consecutive_failures,
            "disabled_reason": self._ai_disabled_reason,
            "cloud_latency_ms": self._cloud_last_latency_ms,
            "local_latency_ms": self._local_last_latency_ms,
            "last_failure": self._last_failure_reason,
        }

    def test_backends(self) -> dict[str, str]:
        """Quick health check of both backends. For "Test AI" button."""
        results: dict[str, str] = {}

        # Test via provider registry
        try:
            from shared.ai.provider_registry import (
                resolve_active_cloud_provider,
                resolve_local_provider,
            )

            cloud = resolve_active_cloud_provider()
            if cloud:
                try:
                    result = cloud.test_connection(timeout=15.0)
                    if result.success:
                        self._cloud_last_latency_ms = result.latency_ms
                        results["cloud"] = f"OK ({result.latency_ms:.0f}ms)"
                    else:
                        results["cloud"] = f"Failed: {result.error}"
                except Exception as e:
                    results["cloud"] = f"Failed: {e}"
            else:
                results["cloud"] = "Not configured"

            local = resolve_local_provider()
            if local:
                try:
                    result = local.test_connection(timeout=15.0)
                    if result.success:
                        self._local_last_latency_ms = result.latency_ms
                        results["local"] = f"OK ({result.latency_ms:.0f}ms)"
                    else:
                        results["local"] = f"Failed: {result.error}"
                except Exception as e:
                    results["local"] = f"Failed: {e}"
            else:
                results["local"] = "Not installed"
        except Exception as e:
            results["cloud"] = f"Error: {e}"
            results["local"] = f"Error: {e}"

        return results

    # ------------------------------------------------------------------
    # Provider resolution (via shared/ai/provider_registry)
    # ------------------------------------------------------------------

    def _resolve_provider(self, prefer: str) -> Any:
        """Resolve a provider via the registry. Returns None if unavailable.

        Args:
            prefer: "cloud" or "local"
        """
        try:
            from shared.ai.provider_registry import (
                resolve_active_cloud_provider,
                resolve_local_provider,
            )
            if prefer == "cloud":
                provider = resolve_active_cloud_provider()
                if provider and provider.is_available():
                    return provider
            elif prefer == "local":
                provider = resolve_local_provider()
                if provider and provider.is_available():
                    return provider
        except Exception as e:
            _logger.debug("Provider resolution failed for %s: %s", prefer, e)
        return None

    def _select_providers(self, category: str) -> list[tuple[str, Any]]:
        """Return ordered list of (tag, provider) to try for this event.

        Smart fallback chain:
          "off"   → empty (no AI)
          "cloud" → [cloud, local] for severe; [local, cloud] for medium
          "local" → [local] only

        CLOUD fails → LOCAL. LOCAL fails → no AI. Never loops.
        """
        if self._ai_mode == "off":
            return []

        # Check if temporarily disabled due to quota
        if self._ai_quota_disabled_until > 0:
            if time.time() < self._ai_quota_disabled_until:
                return []
            else:
                # Cooldown expired, re-enable
                self._ai_quota_disabled_until = 0.0

        cloud = self._resolve_provider("cloud") if self._cloud_available else None
        local = self._resolve_provider("local") if self._local_available else None

        if self._ai_mode == "local":
            return [("LOCAL", local)] if local else []

        # Mode is "cloud" — use severity routing with local fallback
        if category in _CLOUD_PREFERRED_CATEGORIES or category == "_session_summary":
            chain: list[tuple[str, Any]] = []
            if cloud:
                chain.append(("CLOUD", cloud))
            if local:
                chain.append(("LOCAL", local))
            return chain
        else:
            # Medium-severity: prefer local to save cloud budget
            chain = []
            if local:
                chain.append(("LOCAL", local))
            if cloud:
                chain.append(("CLOUD", cloud))
            return chain

    # ------------------------------------------------------------------
    # Event recording
    # ------------------------------------------------------------------

    def record(
        self,
        level: str,
        category: str,
        summary: str,
        details: Optional[dict[str, Any]] = None,
        process_capture: Optional[ProcessCapture] = None,
    ) -> DiagnosticEvent:
        """Record a diagnostic event. Triggers AI if category warrants it.

        This method must never raise — it is called from critical pipeline code.
        Ring buffer and file writes use best-effort error suppression.
        """
        event = DiagnosticEvent(
            timestamp=datetime.now().isoformat(),
            level=level,
            category=category,
            summary=summary,
            details=details or {},
        )

        try:
            with self._lock:
                self._ring_buffer.append(event)
                if process_capture:
                    self._process_captures.append(process_capture)
        except Exception:
            pass

        # Write to session log file (best-effort)
        try:
            self._write_session_log(event)
        except Exception:
            pass

        # Write to system log (persistent, survives GUI failure)
        self._sys_log(level, f"[{category}] {summary}")

        # Trigger AI on meaningful failure events (best-effort, background)
        try:
            if (
                self._ai_enabled
                and category in AI_TRIGGER_CATEGORIES
                and level in ("error", "critical", "warning")
            ):
                self._trigger_ai_diagnosis(event, process_capture)
        except Exception:
            pass

        return event

    def record_exception(
        self,
        exc: BaseException,
        context: str = "",
        category: str = "uncaught_exception",
    ) -> DiagnosticEvent:
        """Record an exception with full traceback."""
        tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
        return self.record(
            level="critical",
            category=category,
            summary=f"{context}: {type(exc).__name__}: {exc}" if context else f"{type(exc).__name__}: {exc}",
            details={
                "exception_type": type(exc).__name__,
                "exception_message": str(exc),
                "traceback": "".join(tb),
            },
        )

    def record_process(
        self,
        capture: ProcessCapture,
        success: bool,
        category: str = "subprocess_nonzero_exit",
    ) -> Optional[DiagnosticEvent]:
        """Record a completed subprocess. Only creates event on failure."""
        with self._lock:
            self._process_captures.append(capture)

        if success:
            return None

        cmd_str = " ".join(capture.command[:3]) + ("..." if len(capture.command) > 3 else "")
        return self.record(
            level="error",
            category=category,
            summary=f"Process failed (exit {capture.exit_code}): {cmd_str}",
            details=capture.to_dict(),
            process_capture=capture,
        )

    # ------------------------------------------------------------------
    # AI diagnosis (non-blocking, runs in daemon thread)
    # ------------------------------------------------------------------

    def _trigger_ai_diagnosis(
        self,
        event: DiagnosticEvent,
        process_capture: Optional[ProcessCapture] = None,
    ) -> None:
        """Call AI backend in a background thread to diagnose the event.

        NEVER blocks the main thread or pipeline. AI failure is logged
        and silently absorbed.
        """
        if self._ai_disabled_reason:
            return

        if self._ai_call_count >= self._ai_max_calls_per_session:
            self._force_disable_ai(
                f"Max {self._ai_max_calls_per_session} AI calls reached for this session"
            )
            return

        if self._ai_consecutive_failures >= self._ai_max_consecutive_failures:
            self._force_disable_ai(
                f"{self._ai_consecutive_failures} consecutive AI failures — "
                "disabling for remainder of session"
            )
            return

        # Run in background to avoid blocking the rip pipeline
        thread = threading.Thread(
            target=self._do_ai_diagnosis,
            args=(event, process_capture),
            daemon=True,
        )
        thread.start()

    def _force_disable_ai(self, reason: str) -> None:
        """Permanently disable AI for this session and notify user."""
        if self._ai_disabled_reason:
            return  # Already disabled
        self._ai_disabled_reason = reason
        self._ai_enabled = False
        self._write_ai_log(f"[AI:OFFLINE] {reason}")
        self._sys_log("warning", f"[AI:DISABLED] {reason}")
        if self._gui_log_fn:
            try:
                self._gui_log_fn(
                    f"[AI] Diagnostics unavailable: {reason}. "
                    "Rip/transcode continues normally."
                )
            except Exception:
                pass

    def _disable_ai_temporarily(self, reason: str, cooldown_seconds: float = 300.0) -> None:
        """Temporarily disable AI due to quota/rate-limit. Re-enables after cooldown.

        The app continues normally. AI is advisory only.
        """
        self._ai_quota_disabled_until = time.time() + cooldown_seconds
        msg = f"{reason} (AI paused for {int(cooldown_seconds)}s)"
        self._write_ai_log(f"[AI:QUOTA] {msg}")
        self._sys_log("warning", f"[AI:QUOTA] {msg}")
        if self._gui_log_fn:
            try:
                self._gui_log_fn(f"[AI] {msg}. App continues normally.")
            except Exception:
                pass

    def _build_ai_payload(
        self,
        event: DiagnosticEvent,
        process_capture: Optional[ProcessCapture] = None,
        max_output_lines: int = 100,
        max_log_tail: int = 30,
    ) -> str:
        """Build JSON payload for AI, with size controls."""
        try:
            session_context = _build_session_context(self)
            if "recent_log_tail" in session_context:
                session_context["recent_log_tail"] = session_context["recent_log_tail"][-max_log_tail:]
        except Exception:
            session_context = {"context_error": "failed to build context"}

        payload: dict[str, Any] = {
            "event": event.to_dict(),
            "session_context": session_context,
        }
        if process_capture:
            pc = process_capture.to_dict()
            for key in ("stdout", "stderr"):
                lines = pc.get(key, "").splitlines()
                if len(lines) > max_output_lines:
                    pc[key] = "\n".join(["... (truncated)", *lines[-max_output_lines:]])
            payload["process_output"] = pc

        result = json.dumps(payload, indent=2, default=str)

        # Hard cap: if payload exceeds ~50KB, aggressively truncate
        if len(result) > 50_000:
            return self._build_ai_payload(
                event, process_capture,
                max_output_lines=30,
                max_log_tail=10,
            ) if max_output_lines > 30 else result[:50_000]

        return result

    def _build_local_payload(
        self,
        event: DiagnosticEvent,
        process_capture: Optional[ProcessCapture] = None,
    ) -> str:
        """Build a smaller payload suitable for local models."""
        return self._build_ai_payload(
            event, process_capture,
            max_output_lines=30,
            max_log_tail=10,
        )

    def _do_ai_diagnosis(
        self,
        event: DiagnosticEvent,
        process_capture: Optional[ProcessCapture] = None,
    ) -> None:
        """Try providers in priority order. Runs in background thread.

        Smart fallback: CLOUD fails → LOCAL. LOCAL fails → no AI. Never loops.
        """
        providers = self._select_providers(event.category)
        if not providers:
            self._write_ai_log(
                f"[AI:OFFLINE] No backends available for: {event.summary}"
            )
            return

        diagnosis: Optional[str] = None
        used_backend: Optional[str] = None

        for tag, provider in providers:
            if provider is None:
                continue
            try:
                # Use smaller payload for local models
                if tag == "LOCAL":
                    payload = self._build_local_payload(event, process_capture)
                    timeout = self._local_timeout
                else:
                    payload = self._build_ai_payload(event, process_capture)
                    timeout = self._cloud_timeout

                start = time.time()
                diagnosis = provider.diagnose(
                    payload, _DIAGNOSIS_SYSTEM_PROMPT,
                    max_tokens=800, timeout=timeout,
                )
                latency_ms = (time.time() - start) * 1000

                used_backend = tag
                self._ai_consecutive_failures = 0
                self._last_failure_reason = ""
                if tag == "CLOUD":
                    self._cloud_last_latency_ms = latency_ms
                else:
                    self._local_last_latency_ms = latency_ms
                break
            except Exception as e:
                self._last_failure_reason = f"{tag}: {e}"
                _logger.warning("AI backend %s failed: %s", tag, e)
                self._write_ai_log(f"[AI:{tag}] Diagnosis failed: {e}")

                # Token exhaustion handling: detect and disable temporarily
                if _is_quota_error(e):
                    self._disable_ai_temporarily(
                        f"{tag} quota/rate-limit exceeded",
                        cooldown_seconds=300.0,
                    )
                    return  # Don't try fallback for quota errors on cloud
                continue

        if diagnosis is None:
            self._ai_consecutive_failures += 1
            self._write_ai_log(
                f"[AI:OFFLINE] All backends failed for: {event.summary} "
                f"(failure {self._ai_consecutive_failures}/{self._ai_max_consecutive_failures})"
            )
            self._sys_log("warning",
                f"[AI] All backends failed ({self._ai_consecutive_failures}/{self._ai_max_consecutive_failures}): {event.summary}"
            )
            return

        self._ai_call_count += 1
        event.ai_diagnosis = diagnosis

        tag_label = f"AI:{used_backend}"

        # Write to AI log file
        self._write_ai_log(
            f"\n{'='*72}\n"
            f"[{tag_label}] {event.timestamp} | {event.category}\n"
            f"Trigger: {event.summary}\n"
            f"{'='*72}\n"
            f"{diagnosis}\n"
        )

        # Write full analysis to system log
        self._sys_log_ai(diagnosis, tag_label)

        # Write to GUI if enabled
        if self._ai_log_to_gui and self._gui_log_fn:
            user_msg = ""
            for line in diagnosis.splitlines():
                if line.strip().upper().startswith("USER MESSAGE:"):
                    user_msg = line.split(":", 1)[1].strip()
                    break
            try:
                if user_msg:
                    self._gui_log_fn(f"[{tag_label}] {user_msg}")
                else:
                    self._gui_log_fn(f"[{tag_label}] Diagnosis available in session.ai.log")
            except Exception:
                pass

        # Update state JSON
        try:
            self._write_state_json()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # File I/O
    # ------------------------------------------------------------------

    def _write_session_log(self, event: DiagnosticEvent) -> None:
        """Append event to session.log."""
        if not self._session_log_path:
            return
        try:
            line = f"[{event.timestamp}] [{event.level.upper()}] [{event.category}] {event.summary}\n"
            with open(self._session_log_path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

    def _write_ai_log(self, text: str) -> None:
        """Append to session.ai.log."""
        if not self._ai_log_path:
            return
        try:
            with open(self._ai_log_path, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception:
            pass

    def _write_state_json(self) -> None:
        """Dump current state snapshot to session.state.json."""
        if not self._state_json_path:
            return
        try:
            state = {
                "timestamp": datetime.now().isoformat(),
                "session_context": _build_session_context(self),
                "ai_calls_made": self._ai_call_count,
                "ai_backend_status": {
                    "cloud_available": self._cloud_available,
                    "local_available": self._local_available,
                    "disabled_reason": self._ai_disabled_reason,
                    "consecutive_failures": self._ai_consecutive_failures,
                },
                "ring_buffer_size": len(self._ring_buffer),
                "recent_events": [e.to_dict() for e in list(self._ring_buffer)[-20:]],
                "process_captures_count": len(self._process_captures),
            }
            tmp = self._state_json_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, default=str)
            os.replace(tmp, self._state_json_path)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # System logger integration (persistent, survives GUI failure)
    # ------------------------------------------------------------------

    def _sys_log(self, level: str, message: str) -> None:
        """Write to the persistent system log."""
        if not self._system_logger:
            return
        try:
            self._system_logger.log_event(message, level=level)
        except Exception:
            pass

    def _sys_log_ai(self, analysis: str, backend: str) -> None:
        """Write full AI analysis to the persistent system log."""
        if not self._system_logger:
            return
        try:
            self._system_logger.log_ai(analysis, backend=backend)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Ring buffer dump (for crash recovery)
    # ------------------------------------------------------------------

    def dump_ring_buffer(self, path: Optional[str] = None) -> str:
        """Dump ring buffer to a crash file. Returns the path written."""
        if path is None:
            if self._session_dir:
                path = os.path.join(self._session_dir, "crash_buffer.json")
            else:
                from shared.runtime import get_config_dir
                path = os.path.join(get_config_dir(), "last_crash_buffer.json")

        with self._lock:
            events = [e.to_dict() for e in self._ring_buffer]
            captures = [c.to_dict() for c in self._process_captures[-10:]]

        data = {
            "dumped_at": datetime.now().isoformat(),
            "events": events,
            "recent_process_captures": captures,
        }
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            _logger.warning("Failed to dump ring buffer: %s", e)
        return path

    # ------------------------------------------------------------------
    # Session summary (called at end of session)
    # ------------------------------------------------------------------

    def generate_session_summary(self) -> Optional[str]:
        """Ask AI for a full session summary. Called once at session end."""
        if not self._ai_enabled or self._ai_disabled_reason:
            return None

        providers = self._select_providers("_session_summary")
        if not providers:
            return None

        with self._lock:
            all_events = [e.to_dict() for e in self._ring_buffer]

        if not all_events:
            return None

        try:
            errors = [e for e in all_events if e["level"] in ("error", "critical")]
            warnings = [e for e in all_events if e["level"] == "warning"]

            payload = {
                "total_events": len(all_events),
                "errors": errors[:20],
                "warnings": warnings[:20],
            }
            try:
                payload["session_context"] = _build_session_context(self)
            except Exception:
                payload["session_context"] = {"context_error": "failed to build"}

            payload_json = json.dumps(payload, indent=2, default=str)
            if len(payload_json) > 50_000:
                payload["errors"] = errors[:5]
                payload["warnings"] = warnings[:5]
                payload_json = json.dumps(payload, indent=2, default=str)

            # Smaller payload for local
            local_payload_json: Optional[str] = None
            if len(payload_json) > 20_000:
                local_payload = {
                    "total_events": len(all_events),
                    "errors": errors[:5],
                    "warnings": warnings[:5],
                }
                try:
                    local_payload["session_context"] = _build_session_context(self)
                except Exception:
                    local_payload["session_context"] = {"context_error": "failed to build"}
                local_payload_json = json.dumps(local_payload, indent=2, default=str)

            summary: Optional[str] = None
            used_backend: Optional[str] = None

            for tag, provider in providers:
                if provider is None:
                    continue
                try:
                    if tag == "LOCAL" and local_payload_json:
                        pj = local_payload_json
                    else:
                        pj = payload_json
                    timeout = self._local_timeout if tag == "LOCAL" else self._cloud_timeout
                    summary = provider.summarize(
                        pj, _SUMMARY_SYSTEM_PROMPT,
                        max_tokens=1200, timeout=timeout,
                    )
                    used_backend = tag
                    break
                except Exception as e:
                    _logger.warning("Session summary via %s failed: %s", tag, e)
                    self._write_ai_log(f"[AI:{tag}] Session summary failed: {e}")
                    if _is_quota_error(e):
                        self._disable_ai_temporarily(f"{tag} quota exceeded")
                        return None
                    continue

            if summary:
                tag_label = f"AI:{used_backend}"
                self._write_ai_log(
                    f"\n{'='*72}\n"
                    f"[{tag_label}] SESSION SUMMARY\n"
                    f"{'='*72}\n"
                    f"{summary}\n"
                )
                self._sys_log_ai(summary, tag_label)
            else:
                self._write_ai_log("[AI:OFFLINE] Session summary: all backends failed")

            return summary
        except Exception as e:
            _logger.warning("Session summary failed: %s", e)
            self._write_ai_log(f"[AI:OFFLINE] Session summary error: {e}")
            return None


# ---------------------------------------------------------------------------
# Module-level singleton (initialized by engine/controller on startup)
# ---------------------------------------------------------------------------

_instance: Optional[DiagnosticsManager] = None
_instance_lock = threading.Lock()


def init_diagnostics(
    config: Optional[dict[str, Any]] = None,
    gui_log_fn: Optional[Callable[[str], None]] = None,
    session_dir: Optional[str] = None,
) -> DiagnosticsManager:
    """Initialize or reinitialize the global diagnostics manager."""
    global _instance
    with _instance_lock:
        _instance = DiagnosticsManager(
            config=config,
            gui_log_fn=gui_log_fn,
            session_dir=session_dir,
        )
    return _instance


def get_diagnostics() -> Optional[DiagnosticsManager]:
    """Get the global diagnostics manager (may be None if not initialized)."""
    return _instance


def diag_record(
    level: str,
    category: str,
    summary: str,
    details: Optional[dict[str, Any]] = None,
    process_capture: Optional[ProcessCapture] = None,
) -> Optional[DiagnosticEvent]:
    """Convenience: record an event on the global manager.

    Guaranteed never to raise — safe to call from any pipeline code.
    """
    try:
        mgr = _instance
        if mgr is None:
            return None
        return mgr.record(level, category, summary, details, process_capture)
    except Exception:
        return None


def diag_exception(
    exc: BaseException,
    context: str = "",
    category: str = "uncaught_exception",
) -> Optional[DiagnosticEvent]:
    """Convenience: record an exception on the global manager.

    Guaranteed never to raise — safe to call from any pipeline code.
    """
    try:
        mgr = _instance
        if mgr is None:
            return None
        return mgr.record_exception(exc, context, category)
    except Exception:
        return None


def diag_process(
    capture: ProcessCapture,
    success: bool,
    category: str = "subprocess_nonzero_exit",
) -> Optional[DiagnosticEvent]:
    """Convenience: record a process result on the global manager.

    Guaranteed never to raise — safe to call from any pipeline code.
    """
    try:
        mgr = _instance
        if mgr is None:
            return None
        return mgr.record_process(capture, success, category)
    except Exception:
        return None
