from gui.ai_provider_dialog import (
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
    models = [
        "gpt-4o",
        "GPT-4O",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
    ]

    assert _sort_models_by_power(models) == [
        "gpt-4o",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
    ]


def test_classify_connection_error_labels_quota_cleanly():
    state, detail = _classify_connection_error("HTTP Error 429: Too Many Requests")

    assert state == "rate_limited"
    assert "Rate limited / out of quota" in detail


def test_resolve_local_model_selection_prefers_installed_exact_match():
    options, selected = _resolve_local_model_selection(
        "llama3.1:8b",
        ["qwen2.5-coder:14b", "llama3.1:8b", "qwen2.5-coder:7b"],
    )

    assert options == [
        "qwen2.5-coder:14b",
        "llama3.1:8b",
        "qwen2.5-coder:7b",
    ]
    assert selected == "llama3.1:8b"


def test_resolve_local_model_selection_repairs_stale_saved_model():
    options, selected = _resolve_local_model_selection(
        "qwen2.5:7b-instruct",
        ["llama3.1:8b", "qwen2.5-coder:14b", "qwen2.5-coder:7b"],
    )

    assert options == [
        "qwen2.5-coder:14b",
        "llama3.1:8b",
        "qwen2.5-coder:7b",
    ]
    assert selected == "qwen2.5-coder:14b"


def test_sync_scroll_canvas_width_updates_canvas_window_and_scrollregion():
    class _Canvas:
        def __init__(self):
            self.itemconfigure_calls = []
            self.configure_calls = []

        def itemconfigure(self, window_id, **kwargs):
            self.itemconfigure_calls.append((window_id, kwargs))

        def configure(self, **kwargs):
            self.configure_calls.append(kwargs)

        def bbox(self, _tag):
            return (0, 0, 640, 900)

    from gui.ai_provider_dialog import AIProviderDialog

    dialog = object.__new__(AIProviderDialog)
    dialog._scroll_canvas = _Canvas()
    dialog._scroll_window_id = 77

    dialog._sync_scroll_canvas_width(612)

    assert dialog._scroll_canvas.itemconfigure_calls == [(77, {"width": 612})]
    assert dialog._scroll_canvas.configure_calls == [
        {"scrollregion": (0, 0, 640, 900)}
    ]
