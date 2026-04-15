import config
from gui.theme import APP_THEME, build_app_theme, normalize_theme_color, sanitize_theme_overrides


def test_normalize_theme_color_accepts_hash_and_bare_hex():
    assert normalize_theme_color("#ABCDEF") == "#abcdef"
    assert normalize_theme_color("123456") == "#123456"
    assert normalize_theme_color(" #00FF99 ") == "#00ff99"
    assert normalize_theme_color("#abcd") is None
    assert normalize_theme_color("nope") is None


def test_build_app_theme_applies_only_valid_known_overrides():
    theme = build_app_theme(
        {
            "window_bg": "#112233",
            "title": "445566",
            "unknown_key": "#ffffff",
            "text": "bad-value",
        }
    )

    assert theme["window_bg"] == "#112233"
    assert theme["title"] == "#445566"
    assert theme["text"] == APP_THEME["text"]
    assert "unknown_key" not in theme


def test_sanitize_theme_overrides_ignores_invalid_entries():
    cleaned = sanitize_theme_overrides(
        {
            "window_bg": "#001122",
            "title": 123,
            "bad": "#334455",
        }
    )

    assert cleaned == {"window_bg": "#001122"}


def test_merge_config_copies_theme_overrides_mapping():
    merged = config._merge_config({"opt_theme_overrides": {"window_bg": "#123456"}})

    merged["opt_theme_overrides"]["title"] = "#abcdef"

    assert config.DEFAULTS["opt_theme_overrides"] == {}
