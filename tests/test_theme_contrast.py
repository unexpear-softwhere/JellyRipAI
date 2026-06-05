"""RETIRED at the user's request (2026-06-03).

This file pinned WCAG AA (4.5:1) contrast for theme text/menu token
pairs — an experiment we tried and decided not to keep.  Emptied to a
zero-test stub (the repo's convention for retiring a test; mirrors
``tests/test_button_contrast.py``) rather than hard-deleting the file.

Note: the ``selectionFg`` token stays in ``TOKEN_KEYS`` because the QSS
renderer consumes it (``selection-color``); only the contrast
assertions are removed.  Delete this file from disk if you want it gone
entirely.
"""
