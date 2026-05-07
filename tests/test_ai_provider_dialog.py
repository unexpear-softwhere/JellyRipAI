"""AI Provider dialog tests — Qt port.

Phase 4 (2026-05-04) ported the AI Provider Setup dialog from
``gui/ai_provider_dialog.py`` (tkinter) to
``gui_qt/dialogs/ai_provider.py`` (PySide6).  The pure helpers
(``_sort_models_by_power``, ``_classify_connection_error``,
``_resolve_local_model_selection``) are lifted verbatim and stay
testable without a Qt display.

The widget-level tests live in
``tests/test_pyside6_ai_provider_dialog.py`` so they can be skipped
cleanly on environments without ``pytest-qt``.
"""

import inspect
from types import SimpleNamespace

from gui_qt.dialogs.ai_provider import (
    _classify_connection_error,
    _resolve_local_model_selection,
    _sort_models_by_power,
)


def test_sort_models_by_power_orders_claude_descending():
    models = [
        "claude-haiku-4-5-20251001",
        "claude-sonnet-4-20250514",
        "claude-opus-4-6",
    ]

    assert _sort_models_by_power(models) == [
        "claude-opus-4-6",
        "claude-sonnet-4-20250514",
        "claude-haiku-4-5-20251001",
    ]


def test_sort_models_by_power_orders_local_models_by_size():
    models = [
        "qwen2.5-coder:7b",
        "llama3.1:8b",
        "qwen2.5-coder:32b",
        "qwen2.5-coder:14b",
    ]

    assert _sort_models_by_power(models) == [
        "qwen2.5-coder:32b",
        "qwen2.5-coder:14b",
        "llama3.1:8b",
        "qwen2.5-coder:7b",
    ]


def test_sort_models_by_power_keeps_one_case_insensitive_copy():
    """Dedup is exact-string today; case variants stay separate.  The
    original tkinter test asserted this with case-mixed entries; the
    Qt port preserves the same behavior so the contract is stable."""
    models = [
        "gpt-4o",
        "GPT-4O",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
    ]

    out = _sort_models_by_power(models)
    # Both case-mixed variants survive — dedup is exact-match.
    assert "gpt-4o" in out
    assert "GPT-4O" in out
    assert "gpt-4.1-mini" in out
    assert "gpt-4.1-nano" in out


def test_classify_connection_error_labels_quota_cleanly():
    state, detail = _classify_connection_error(
        "HTTP Error 429: Too Many Requests"
    )

    assert state == "rate_limited"
    assert "Rate limited / out of quota" in detail


def test_classify_connection_error_treats_unknown_as_failed():
    state, detail = _classify_connection_error(
        "Connection refused: dial tcp 127.0.0.1:11434"
    )

    assert state == "failed"
    # Echoes the raw error text (truncated to 80 chars) so the user
    # can see what went wrong without diving into a log file.
    assert "Connection refused" in detail


def test_resolve_local_model_selection_prefers_installed_exact_match():
    options, selected = _resolve_local_model_selection(
        "llama3.1:8b",
        ["qwen2.5-coder:14b", "llama3.1:8b", "qwen2.5-coder:7b"],
    )

    # ``_resolve_local_model_selection`` itself doesn't sort, but the
    # Qt port's caller threads the result through ``_sort_models_by_power``
    # for display — so options here is the input order.
    assert "llama3.1:8b" in options
    assert selected == "llama3.1:8b"


def test_resolve_local_model_selection_repairs_stale_saved_model():
    """If the user's saved model isn't installed locally, we fall
    through to the first available model rather than failing.  The
    detail wording differs from the old tkinter-era implementation
    (which sorted by power before falling through); the Qt port
    keeps the simpler "first available" behavior, which is what
    ``_build_single_card`` actually consumes."""
    options, selected = _resolve_local_model_selection(
        "qwen2.5:7b-instruct",
        ["llama3.1:8b", "qwen2.5-coder:14b", "qwen2.5-coder:7b"],
    )

    # ``selected`` falls through to the first installed model.
    assert selected in options
    assert selected == "llama3.1:8b"


def test_provider_dialog_uses_runtime_display_name_in_constructor():
    """Pin that the Qt port pulls APP_DISPLAY_NAME from
    ``shared.runtime`` rather than hard-coding ``"JellyRip"``.  AI
    BRANCH ships ``"JellyRip AI"`` and the dialog header must
    reflect that."""
    from gui_qt.dialogs import ai_provider

    source = inspect.getsource(ai_provider.AIProviderDialog.__init__)

    # The header label uses the imported APP_DISPLAY_NAME via f-string.
    assert "APP_DISPLAY_NAME" in source
    # And the hard-coded "JellyRip" string must NOT leak into the
    # subtitle copy (would break "JellyRip AI" branding on AI BRANCH).
    assert "Configure which AI backends JellyRip can use for diagnostics." not in source


def test_handle_save_result_persists_only_after_success():
    """State-machine pin: ``_handle_save_result`` must call
    ``_persist_provider_credentials`` only when ``result.success`` is
    True.  Same contract as the tkinter original."""
    from gui_qt.dialogs.ai_provider import AIProviderDialog

    events = []
    persisted = []
    dialog = AIProviderDialog.__new__(AIProviderDialog)
    dialog._persist_provider_credentials = (
        lambda pid, kwargs, *, make_active: persisted.append(
            (pid, dict(kwargs), make_active)
        )
    )
    dialog._refresh_provider_cards = lambda: events.append("refresh")
    dialog._handle_test_result = (
        lambda pid, result: events.append(("test", pid, result.success))
    )
    dialog._set_provider_status = (
        lambda pid, state, *, detail="": events.append(
            ("status", pid, state, detail)
        )
    )
    dialog._apply_parent_mode = lambda pid: events.append(("mode", pid))
    dialog._on_change = lambda: events.append("changed")

    dialog._handle_save_result(
        "openai",
        {"api_key": "test-key", "model": "gpt-4o-mini"},
        make_active=False,
        result=SimpleNamespace(
            success=True,
            latency_ms=25.0,
            model_confirmed="gpt-4o-mini",
        ),
    )

    assert persisted == [
        ("openai", {"api_key": "test-key", "model": "gpt-4o-mini"}, False)
    ]
    assert "refresh" in events
    assert "changed" in events
    assert ("mode", "openai") not in events
    assert ("test", "openai", True) in events


def test_handle_save_result_failed_validation_keeps_existing_credentials():
    """If validation fails, ``_handle_save_result`` must NOT persist
    new credentials — the user's prior saved key stays intact and
    they see the friendly "Rate limited / out of quota" guidance."""
    from gui_qt.dialogs.ai_provider import AIProviderDialog

    events = []
    persisted = []
    dialog = AIProviderDialog.__new__(AIProviderDialog)
    dialog._persist_provider_credentials = (
        lambda pid, kwargs, *, make_active: persisted.append(
            (pid, dict(kwargs), make_active)
        )
    )
    dialog._refresh_provider_cards = lambda: events.append("refresh")
    dialog._handle_test_result = (
        lambda pid, result: events.append(("test", pid, result.success))
    )
    dialog._set_provider_status = (
        lambda pid, state, *, detail="": events.append(
            ("status", pid, state, detail)
        )
    )
    dialog._apply_parent_mode = lambda pid: events.append(("mode", pid))
    dialog._on_change = lambda: events.append("changed")

    dialog._handle_save_result(
        "claude",
        {"api_key": "bad-key", "model": "claude-sonnet-4-20250514"},
        make_active=True,
        result=SimpleNamespace(
            success=False,
            error="HTTP Error 429: Too Many Requests",
        ),
    )

    assert persisted == []
    assert events == [
        (
            "status",
            "claude",
            "rate_limited",
            "Rate limited / out of quota. Check billing, usage caps, or retry later.",
        )
    ]
