"""Shared dataclasses for session-setup result objects.

Originally defined in ``gui/session_setup_dialog.py`` (the tkinter
implementation).  Phase 3h (tkinter retirement, 2026-05-04) lifted
them to this neutral home so the controller, workflow launchers, and
both GUI layers can import them without pulling in any GUI-toolkit
dependency.

Imported by:

* ``gui_qt/dialogs/session_setup.py`` — Qt-native session setup
* ``gui_qt/workflow_launchers.py``
* ``controller/*`` — wherever a worker thread receives the
  user's session-setup answers
* ``tests/test_*`` — behavior-first tests

Nothing in here imports tkinter, PySide6, or any GUI module.  Pure
Python + standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MovieSessionSetup:
    """User answers from the movie session-setup dialog."""
    title: str
    year: str
    edition: str          # "" | "Theatrical Cut" | "Director's Cut" | etc.
    metadata_provider: str  # "TMDB" | "OpenDB"
    metadata_id: str      # raw ID value entered by user
    replace_existing: bool
    keep_raw: bool
    extras_mode: str      # "ask" | "keep" | "skip"


@dataclass
class TVSessionSetup:
    """User answers from the TV session-setup dialog."""
    title: str
    year: str
    season: int
    starting_disc: int
    episode_mapping: str  # "auto" | "manual"
    metadata_provider: str  # "TMDB" | "OpenDB"
    metadata_id: str
    multi_episode: str    # "auto" | "split" | "merge"
    specials: str         # "ask" | "season0" | "skip"
    replace_existing: bool
    keep_raw: bool


@dataclass
class DumpSessionSetup:
    """User answers from the dump-mode session-setup dialog."""
    multi_disc: bool
    disc_name: str
    disc_count: int
    custom_disc_names: str
    batch_title: str


__all__ = [
    "MovieSessionSetup",
    "TVSessionSetup",
    "DumpSessionSetup",
]
