"""Base provider adapter interface for AI diagnostics backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class ProviderInfo:
    """Static metadata about a provider shown in the connection dialog."""
    id: str               # "openai" | "claude" | "gemini" | "local"
    display_name: str     # "OpenAI" | "Claude (Anthropic)" | ...
    category: str         # "cloud" | "local"
    requires_api_key: bool
    default_model: str
    available_models: list[str]
    help_url: str         # where user gets an API key


@dataclass
class ConnectionResult:
    """Result of a test_connection() call."""
    success: bool
    latency_ms: float = 0.0
    error: str = ""
    model_confirmed: str = ""


class BaseProvider(ABC):
    """Adapter interface for a single AI provider.

    Each provider knows how to:
    - describe itself (info)
    - accept credentials (configure)
    - test the connection
    - send a diagnostic prompt and return the response
    """

    @abstractmethod
    def info(self) -> ProviderInfo:
        """Return static metadata about this provider."""

    @abstractmethod
    def configure(self, api_key: str = "", model: str = "", **kwargs: Any) -> None:
        """Apply credentials and model selection. Called before any API use."""

    @abstractmethod
    def test_connection(self, timeout: float = 10.0) -> ConnectionResult:
        """Quick connectivity check. Must not raise."""

    @abstractmethod
    def chat(self, messages: list[dict[str, str]],
             max_tokens: int = 800, timeout: float = 30.0) -> str:
        """Send a conversational message history and return the next reply text.

        Must raise on failure so the caller can fall back.
        """

    @abstractmethod
    def diagnose(self, payload_json: str, system_prompt: str,
                 max_tokens: int = 800, timeout: float = 30.0) -> str:
        """Send a diagnostic payload and return the model response text.

        Must raise on failure so the caller can fall back.
        """

    @abstractmethod
    def summarize(self, payload_json: str, system_prompt: str,
                  max_tokens: int = 1200, timeout: float = 30.0) -> str:
        """Send a session summary payload and return the model response text.

        Must raise on failure so the caller can fall back.
        """

    @abstractmethod
    def is_available(self) -> bool:
        """Quick local check: are credentials present and likely valid?"""
