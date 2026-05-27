"""Update-check entrypoint for the JellyRip AI UI.

Phase 3h (2026-05-04) — the prior implementation lived in
``gui/update_ui.py`` and depended heavily on tkinter-specific GUI
APIs (``gui.after()`` for thread-safe callbacks, ``gui.destroy()``
for window teardown). When the tkinter UI was retired the function
was not ported one-to-one because the Qt shell uses different
threading primitives (``gui_qt.thread_safety.submit_to_main``) and
a different teardown idiom (``QMainWindow.close()``).

Rather than do a half-finished port, this module exposes a stub
``check_for_updates(window)`` that logs a clear "feature deferred"
message. The Updates utility chip in ``gui_qt/utility_handlers.py``
calls this stub. A Qt-native rewrite is scheduled as a polish-tier
follow-up — see ``docs/handoffs/phase-3h-tkinter-retirement.md``
Step 3 notes (on MAIN).

The previous implementation is preserved in git history at
``gui/update_ui.py`` (commit before tkinter retirement) for
reference when the Qt port lands.

This file is the AI-fork mirror of MAIN's ``tools/update_check.py``.
It existed on MAIN since the Phase 3h retirement but never landed
on AI BRANCH; clicking the Updates utility chip on AI raised
``ModuleNotFoundError`` until this stub was added.
"""

from __future__ import annotations


def check_for_updates(window) -> None:
    """Stub for the in-app update check.

    Logs a deferred-feature notice instead of querying GitHub
    Releases. Users can manually visit the GitHub Releases page in
    the meantime.

    Args:
        window: Any object exposing ``set_status`` and
            ``append_log`` methods. The Qt ``MainWindow`` qualifies;
            tests may pass a fake.
    """
    window.set_status("Updates: feature pending Qt port")
    window.append_log(
        "Updates check is deferred while the Qt port stabilizes. "
        "For now, please check the GitHub Releases page manually:"
    )
    window.append_log(
        "  https://github.com/unexpear-softwhere/JellyRipAI/releases"
    )
