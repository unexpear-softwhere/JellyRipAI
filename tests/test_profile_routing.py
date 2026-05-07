"""Profile routing tests — multi-instance / multi-drive support.

Pins the contract added 2026-05-05:

* ``JELLYRIP_PROFILE`` env var controls which config dir + log file
  + AUMID + window title an instance uses.
* Empty / missing profile preserves legacy single-instance behavior
  (existing installs see no change).
* Profile names get sanitized to filesystem-safe characters.
* ``main._bootstrap_profile_from_argv`` strips ``--profile NAME``
  out of ``sys.argv`` and writes the env var.

These tests run synchronously against ``shared.runtime``'s pure
helpers — no Qt, no pytest-qt required.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import importlib

import pytest


@pytest.fixture
def runtime(monkeypatch):
    """Reload ``shared.runtime`` with a controlled env so the
    module-level CONFIG_FILE recomputes per-test."""
    import shared.runtime as rt
    monkeypatch.delenv("JELLYRIP_PROFILE", raising=False)
    importlib.reload(rt)
    return rt


def test_no_profile_preserves_legacy_paths(runtime, monkeypatch):
    """Without ``JELLYRIP_PROFILE``, behavior is unchanged.  This
    is the backwards-compatibility guard — existing installs must
    keep using the same config dir and log file."""
    monkeypatch.delenv("JELLYRIP_PROFILE", raising=False)
    importlib.reload(runtime)

    assert runtime.get_active_profile() == ""
    config_dir = runtime.get_config_dir(create=False)
    # Should NOT contain "/profiles/" segment.
    assert os.sep + "profiles" + os.sep not in config_dir
    # Default log file: no profile suffix.
    assert runtime.get_profile_log_file_default().endswith("rip_log.txt")


def test_profile_routes_into_profiles_subdir(runtime, monkeypatch):
    """With a profile set, config + log + AUMID all reflect it."""
    monkeypatch.setenv("JELLYRIP_PROFILE", "drive-a")
    importlib.reload(runtime)

    assert runtime.get_active_profile() == "drive-a"
    config_dir = runtime.get_config_dir(create=False)
    assert config_dir.endswith(os.path.join("profiles", "drive-a"))
    assert runtime.get_profile_log_file_default().endswith("rip_log_drive-a.txt")
    aumid = runtime.get_profile_aumid()
    assert aumid.endswith(".drive-a")
    title = runtime.get_profile_window_title()
    assert "drive-a" in title


def test_profile_name_sanitized_for_filesystem(runtime, monkeypatch):
    """Profile names with slashes/colons/spaces get sanitized so
    they can't escape the profiles directory or break Windows
    paths."""
    # Slashes, colons, spaces, periods all replaced with underscores.
    monkeypatch.setenv("JELLYRIP_PROFILE", "../evil/path")
    importlib.reload(runtime)

    profile = runtime.get_active_profile()
    assert ".." not in profile
    assert "/" not in profile
    assert "\\" not in profile

    monkeypatch.setenv("JELLYRIP_PROFILE", "drive a:1")
    importlib.reload(runtime)
    profile = runtime.get_active_profile()
    assert ":" not in profile
    assert " " not in profile


def test_empty_profile_after_sanitization_treated_as_default(
    runtime, monkeypatch,
):
    """If a profile name is all garbage characters that get stripped
    to empty, treat it as no profile (don't create a 'profiles/'
    subdir with empty name)."""
    monkeypatch.setenv("JELLYRIP_PROFILE", "...///")
    importlib.reload(runtime)

    assert runtime.get_active_profile() == ""
    config_dir = runtime.get_config_dir(create=False)
    assert os.sep + "profiles" + os.sep not in config_dir


def test_profile_names_with_alphanumeric_dash_underscore_pass_through(
    runtime, monkeypatch,
):
    """Valid characters survive sanitization unchanged."""
    monkeypatch.setenv("JELLYRIP_PROFILE", "drive-a_1")
    importlib.reload(runtime)
    assert runtime.get_active_profile() == "drive-a_1"


def test_aumid_varies_per_profile(runtime, monkeypatch):
    """Two profiles produce different AUMIDs so Windows treats them
    as separate apps on the taskbar."""
    monkeypatch.setenv("JELLYRIP_PROFILE", "drive-a")
    importlib.reload(runtime)
    aumid_a = runtime.get_profile_aumid()

    monkeypatch.setenv("JELLYRIP_PROFILE", "drive-b")
    importlib.reload(runtime)
    aumid_b = runtime.get_profile_aumid()

    assert aumid_a != aumid_b


# ─── --profile CLI flag in main._bootstrap_profile_from_argv ───────


def test_bootstrap_profile_from_argv_picks_up_separated_form(monkeypatch):
    """``--profile NAME`` (separated form) is parsed and stripped
    from sys.argv."""
    import main
    monkeypatch.delenv("JELLYRIP_PROFILE", raising=False)
    monkeypatch.setattr(sys, "argv", ["JellyRipAI.exe", "--profile", "drive-a"])

    main._bootstrap_profile_from_argv()

    assert os.environ.get("JELLYRIP_PROFILE") == "drive-a"
    assert sys.argv == ["JellyRipAI.exe"]


def test_bootstrap_profile_from_argv_picks_up_equals_form(monkeypatch):
    """``--profile=NAME`` (equals form) also works."""
    import main
    monkeypatch.delenv("JELLYRIP_PROFILE", raising=False)
    monkeypatch.setattr(sys, "argv", ["JellyRipAI.exe", "--profile=drive-b"])

    main._bootstrap_profile_from_argv()

    assert os.environ.get("JELLYRIP_PROFILE") == "drive-b"
    assert sys.argv == ["JellyRipAI.exe"]


def test_bootstrap_profile_from_argv_no_op_without_flag(monkeypatch):
    """No ``--profile`` flag → env var stays unset, sys.argv
    untouched."""
    import main
    monkeypatch.delenv("JELLYRIP_PROFILE", raising=False)
    monkeypatch.setattr(sys, "argv", ["JellyRipAI.exe"])

    main._bootstrap_profile_from_argv()

    assert "JELLYRIP_PROFILE" not in os.environ
    assert sys.argv == ["JellyRipAI.exe"]


def test_bootstrap_profile_from_argv_dangling_flag_ignored(monkeypatch):
    """``--profile`` without a NAME argument doesn't crash; it just
    consumes the lone flag and leaves the env var unchanged."""
    import main
    monkeypatch.delenv("JELLYRIP_PROFILE", raising=False)
    monkeypatch.setattr(sys, "argv", ["JellyRipAI.exe", "--profile"])

    # Should not raise.
    main._bootstrap_profile_from_argv()
    assert "JELLYRIP_PROFILE" not in os.environ


def test_bootstrap_profile_preserves_other_argv_args(monkeypatch):
    """Other CLI args alongside ``--profile`` survive the strip."""
    import main
    monkeypatch.delenv("JELLYRIP_PROFILE", raising=False)
    monkeypatch.setattr(
        sys, "argv",
        ["JellyRipAI.exe", "--profile", "drive-a", "--verbose", "extra"],
    )

    main._bootstrap_profile_from_argv()

    assert os.environ.get("JELLYRIP_PROFILE") == "drive-a"
    assert sys.argv == ["JellyRipAI.exe", "--verbose", "extra"]
