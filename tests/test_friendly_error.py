"""Tests for ui.dialogs.friendly_error.

Closes Finding #8 in
`docs/ux-copy-and-accessibility-plan.md` — WCAG 3.3.3 (Error
Suggestion). The helper maps caught exceptions to user-facing
recovery text so error dialogs tell users both *what failed* and
*what to try next*, instead of raw-dumping exception strings.

Tests pin:
- The base message is preserved unchanged.
- Without an exception, only the base message is returned.
- Each known exception type maps to a recognizable recovery hint.
- Unknown exception types fall through to a generic "session log"
  hint rather than crashing.
- The raw exception text is NEVER included in the returned message —
  raw detail belongs in logs, not dialog bodies.
- Multiline base messages (with path info) work correctly.
"""

from __future__ import annotations

from ui.dialogs import friendly_error


# --------------------------------------------------------------------------
# No-exception path — helper degrades gracefully
# --------------------------------------------------------------------------


def test_no_exception_returns_base_message_unchanged():
    result = friendly_error("Could not save the file.")
    assert result == "Could not save the file."


def test_explicit_none_exception_same_as_omitted():
    a = friendly_error("Could not save the file.", None)
    b = friendly_error("Could not save the file.")
    assert a == b


def test_base_message_trailing_whitespace_stripped():
    result = friendly_error("Could not save.\n   ")
    # The helper rstrips the base message before composition so the
    # blank-line separator before the recovery text is consistent.
    assert "   " not in result.split("\n")[0]


# --------------------------------------------------------------------------
# Known exception types — each gets type-appropriate recovery text
# --------------------------------------------------------------------------


def test_permission_error_includes_open_in_another_program_hint():
    result = friendly_error("Could not save.", PermissionError("EACCES"))
    assert "Permission denied" in result
    assert "another program" in result.lower() or "write access" in result.lower()


def test_file_not_found_error_suggests_checking_location():
    result = friendly_error("Could not load.", FileNotFoundError("missing"))
    assert "Path not found" in result
    assert "Check the location" in result or "create it manually" in result


def test_is_a_directory_error_suggests_different_name():
    result = friendly_error("Could not write file.", IsADirectoryError("dir"))
    assert "folder" in result.lower()


def test_not_a_directory_error_suggests_different_location():
    result = friendly_error("Could not write to folder.", NotADirectoryError("file"))
    assert "folder" in result.lower() or "location" in result.lower()


def test_os_error_no_space_left_on_device_suggests_freeing_space():
    err = OSError(28, "No space left on device")
    result = friendly_error("Could not save.", err)
    assert "disk space" in result.lower()
    assert "free up" in result.lower() or "free space" in result.lower()


def test_os_error_directory_not_empty_suggests_emptying_or_different_path():
    err = OSError(39, "Directory not empty")
    result = friendly_error("Could not remove folder.", err)
    assert "isn't empty" in result.lower() or "not empty" in result.lower()


def test_os_error_unknown_errno_falls_back_to_generic_session_log_hint():
    err = OSError(99999, "Something exotic")
    result = friendly_error("Operation failed.", err)
    assert "session log" in result.lower()


def test_timeout_error_mentions_network():
    result = friendly_error("Update check failed.", TimeoutError("timed out"))
    assert "network" in result.lower() or "connection" in result.lower()


def test_connection_error_mentions_network():
    result = friendly_error("Update check failed.", ConnectionError("refused"))
    assert "network" in result.lower() or "connection" in result.lower()


def test_value_error_mentions_session_log_for_details():
    result = friendly_error("Bad input.", ValueError("not a number"))
    assert "session log" in result.lower() or "details" in result.lower()


def test_unknown_exception_falls_back_to_generic_session_log_hint():
    class _Custom(Exception):
        pass
    result = friendly_error("Operation failed.", _Custom("strange"))
    assert "unexpected" in result.lower() or "session log" in result.lower()


# --------------------------------------------------------------------------
# Raw exception text must NOT leak into the dialog body
# --------------------------------------------------------------------------


def test_raw_exception_message_not_in_returned_text():
    """The whole point of the helper. Catch a future regression where
    someone adds the exception's str() to the recovery text — that
    would re-introduce the bug Finding #8 was about."""
    sentinel = "RAW_EXCEPTION_DETAIL_THAT_SHOULD_NOT_LEAK"
    err = PermissionError(sentinel)
    result = friendly_error("Could not save.", err)
    assert sentinel not in result, (
        f"Raw exception message leaked into dialog body. "
        f"friendly_error must NOT include str(exception) in its "
        f"output — that's exactly the Finding #8 bug. Raw detail "
        f"belongs in the session log, not the dialog."
    )


def test_raw_os_error_text_not_in_returned_text():
    """Same check, OSError variant — its str() includes both errno
    and message and would be especially noisy in a dialog body."""
    sentinel = "RAW_OS_ERROR_DETAIL_SENTINEL"
    err = OSError(28, sentinel)
    result = friendly_error("Could not save.", err)
    assert sentinel not in result


# --------------------------------------------------------------------------
# Multiline base messages (path / context info)
# --------------------------------------------------------------------------


def test_multiline_base_message_with_path_preserves_path():
    base = 'Could not open path:\nC:/Users/me/Downloads/file.mkv'
    result = friendly_error(base, PermissionError("denied"))
    assert "C:/Users/me/Downloads/file.mkv" in result
    assert "Permission denied" in result


def test_recovery_text_separated_from_base_by_blank_line():
    """Visual separation matters — the dialog body should be readable
    as two parts: 'what failed' and 'what to try'. Blank line
    between them is the convention."""
    result = friendly_error("Could not save.", PermissionError("denied"))
    lines = result.split("\n")
    # Find the line with "Permission denied" — the line right before
    # it should be empty (blank-line separator).
    perm_idx = next(i for i, line in enumerate(lines) if "Permission denied" in line)
    assert perm_idx > 0
    assert lines[perm_idx - 1] == "", (
        f"Recovery text should be separated from base message by a "
        f"blank line. Got lines around the recovery hint:\n"
        f"  [{perm_idx - 1}] {lines[perm_idx - 1]!r}\n"
        f"  [{perm_idx}]   {lines[perm_idx]!r}"
    )
