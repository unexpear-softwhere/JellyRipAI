"""Persistent system-level diagnostic logger for JellyRip.

Dual-layer logging:
  Layer A: GUI log (existing) — human-readable, live updates
  Layer B: System log (this module) — always writes, survives GUI failure

System log location:
  Windows: %LOCALAPPDATA%/JellyRipAI/logs/system.log
  macOS:   ~/Library/Logs/JellyRipAI/logs/system.log
  Linux:   ~/.local/share/JellyRipAI/logs/system.log

AI writes here even if the UI doesn't show it. The system log is
append-only and rotated by size (max 5 MB, 3 backups).
"""

from __future__ import annotations

import logging
import os
import platform
from logging.handlers import RotatingFileHandler
from typing import Any, Optional

from shared.runtime import APP_CONFIG_DIR_NAME


def _system_log_dir() -> str:
    """Return the persistent log directory (created on first call)."""
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local"))
    elif system == "Darwin":
        base = os.path.expanduser("~/Library/Logs")
    else:
        base = os.environ.get(
            "XDG_DATA_HOME", os.path.expanduser("~/.local/share")
        )
    log_dir = os.path.join(base, APP_CONFIG_DIR_NAME, "logs")
    os.makedirs(log_dir, exist_ok=True)
    return log_dir


class DiagnosticLogger:
    """Persistent system-level logger that survives GUI failure.

    Outputs to:
      - %LOCALAPPDATA%/JellyRipAI/logs/system.log (always)
      - GUI panel callback (optional mirror, best-effort)

    All methods are safe to call from any thread and guaranteed
    never to raise.
    """

    _MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
    _BACKUP_COUNT = 3

    def __init__(
        self,
        gui_log_fn: Optional[Any] = None,
        log_dir: Optional[str] = None,
    ) -> None:
        self._gui_log_fn = gui_log_fn
        self._log_dir = log_dir or _system_log_dir()

        # Set up the rotating file logger
        self._logger = logging.getLogger("jellyrip.system")
        self._logger.setLevel(logging.DEBUG)
        # Avoid duplicate handlers if re-initialized
        self._logger.handlers.clear()

        try:
            log_path = os.path.join(self._log_dir, "system.log")
            handler = RotatingFileHandler(
                log_path,
                maxBytes=self._MAX_BYTES,
                backupCount=self._BACKUP_COUNT,
                encoding="utf-8",
            )
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
            self._logger.addHandler(handler)
        except Exception:
            # If we can't write the log file, continue silently.
            # The app must never crash because of logging.
            pass

    @property
    def log_path(self) -> str:
        return os.path.join(self._log_dir, "system.log")

    def log_event(self, event: str, level: str = "info") -> None:
        """Log a diagnostic event to system log and optionally GUI."""
        try:
            log_fn = getattr(self._logger, level.lower(), self._logger.info)
            log_fn(event)
        except Exception:
            pass
        self._mirror_to_gui(event)

    def log_error(self, error: str, exc: Optional[BaseException] = None) -> None:
        """Log an error with optional exception details."""
        try:
            if exc:
                self._logger.error("%s: %s", error, exc, exc_info=True)
            else:
                self._logger.error(error)
        except Exception:
            pass
        self._mirror_to_gui(f"[ERROR] {error}")

    def log_ai(self, analysis: str, backend: str = "AI") -> None:
        """Log an AI diagnosis/analysis result."""
        try:
            self._logger.info("[%s] %s", backend, analysis)
        except Exception:
            pass
        # For GUI, show only the short user-facing message
        user_msg = _extract_user_message(analysis)
        if user_msg:
            self._mirror_to_gui(f"[{backend}] {user_msg}")
        else:
            self._mirror_to_gui(f"[{backend}] Diagnosis logged to system.log")

    def log_ai_failure(self, error: Exception, backend: str = "AI") -> None:
        """Log an AI call failure. App continues normally."""
        try:
            self._logger.warning("[%s:FAIL] %s: %s", backend, type(error).__name__, error)
        except Exception:
            pass

    def log_ai_disabled(self, reason: str) -> None:
        """Log that AI has been temporarily disabled."""
        try:
            self._logger.warning("[AI:DISABLED] %s", reason)
        except Exception:
            pass
        self._mirror_to_gui(
            f"[AI] Diagnostics unavailable: {reason}. App continues normally."
        )

    def _mirror_to_gui(self, message: str) -> None:
        """Best-effort mirror to GUI log callback."""
        if not self._gui_log_fn:
            return
        try:
            self._gui_log_fn(message)
        except Exception:
            pass


def _extract_user_message(analysis: str) -> str:
    """Extract the USER MESSAGE line from a structured AI response."""
    for line in analysis.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("USER MESSAGE:"):
            return stripped.split(":", 1)[1].strip()
    return ""


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_instance: Optional[DiagnosticLogger] = None


def init_system_logger(
    gui_log_fn: Optional[Any] = None,
    log_dir: Optional[str] = None,
) -> DiagnosticLogger:
    """Initialize or reinitialize the global system logger."""
    global _instance
    _instance = DiagnosticLogger(gui_log_fn=gui_log_fn, log_dir=log_dir)
    return _instance


def get_system_logger() -> Optional[DiagnosticLogger]:
    """Get the global system logger (may be None before init)."""
    return _instance
