"""In-app update check — Qt-native implementation (2026-06-10).

Replaces the Phase 3h "feature deferred" stub.  Flow:

1. The Updates utility chip calls :func:`check_for_updates` on the
   GUI thread.  It logs immediate feedback, then spawns a daemon
   thread so the GitHub round-trip never blocks the UI.
2. The worker queries GitHub Releases via
   :func:`utils.updater.fetch_latest_release` with prereleases
   included — every JellyRip AI release to date is published as a
   GitHub *prerelease* (the project is pre-alpha), so the
   stable-only ``/releases/latest`` endpoint would never find
   anything.  Only ``ai-v*`` tags count: the MAIN fork's ``v*``
   channel must never update this app.
3. Results surface exclusively through the window's thread-safe
   methods (``set_status`` / ``append_log`` / ``show_info`` /
   ``show_error`` / ``ask_yesno``), which marshal onto the GUI
   thread internally.  This module therefore imports no Qt, never
   shells out, and tests can drive it with a plain fake window.

When an update exists the user is offered the release page in the
browser rather than an automatic download: releases are unsigned
(no Authenticode thumbprint is pinned in config), so the safer flow
is the documented one — download from GitHub and let SmartScreen /
Defender screen the file.

``test_release_consistency.py`` pins ``REPO_SLUG`` / ``TAG_PREFIX``
/ ``PREFERRED_ASSETS`` against ``release.bat`` so this check and
the publish pipeline can't drift apart.
"""

from __future__ import annotations

import threading
import webbrowser

from shared.runtime import APP_DISPLAY_NAME, __version__
from utils.updater import fetch_latest_release, is_newer_version

# Where release.bat publishes builds for THIS fork.
REPO_SLUG = "unexpear-softwhere/JellyRipAI"
RELEASES_URL = f"https://github.com/{REPO_SLUG}/releases"
# AI releases are tagged ai-v*; MAIN uses bare v*, so the two
# release channels can never cross-update even if a remote is ever
# shared.
TAG_PREFIX = "ai-v"
# Which asset to surface in the log: installer first, then the
# portable zip (the one-DIR replacement for the old bare exe).
PREFERRED_ASSETS = ("JellyRipAIInstaller.exe", "JellyRipAI-portable.zip")

# One check at a time — double-clicking the chip must not stack
# worker threads.  Released by the worker's ``finally``.
_check_lock = threading.Lock()


def check_for_updates(window) -> None:
    """Check GitHub Releases for a newer build and tell the user.

    Args:
        window: Any object exposing the thread-safe UI methods
            ``set_status``, ``append_log``, ``show_info``,
            ``show_error``, and ``ask_yesno``.  The Qt ``MainWindow``
            qualifies; tests may pass a fake.
    """
    if not _check_lock.acquire(blocking=False):
        window.append_log("Updates: a check is already in progress.")
        return
    try:
        window.set_status("Checking for updates...")
        window.append_log(
            f"Updates: checking {RELEASES_URL} "
            f"(current version v{__version__})..."
        )
        worker = threading.Thread(
            target=_check_worker,
            args=(window,),
            name="update-check",
            daemon=True,
        )
        worker.start()
    except Exception:
        _check_lock.release()
        raise


def _check_worker(window) -> None:
    try:
        _run_check(window)
    finally:
        _check_lock.release()


def _run_check(window) -> None:
    """Synchronous body of the check — runs on the worker thread."""
    try:
        release = fetch_latest_release(
            repo=REPO_SLUG,
            include_prereleases=True,
            tag_prefix=TAG_PREFIX,
            preferred_assets=PREFERRED_ASSETS,
        )
    except Exception as exc:
        window.set_status("Update check failed.")
        window.append_log(f"Updates: check failed - {exc}")
        window.show_error(
            "Update Check Failed",
            "Could not reach GitHub to check for updates.\n\n"
            f"Details: {exc}\n\n"
            f"You can check manually at:\n{RELEASES_URL}",
        )
        return

    latest = str(release.get("version") or "").strip()
    tag = str(release.get("tag") or "").strip()
    if not latest:
        window.set_status("Update check failed.")
        window.append_log(
            f"Updates: no published releases found at {RELEASES_URL}."
        )
        window.show_error(
            "Update Check Failed",
            f"GitHub returned no published releases for {REPO_SLUG}.\n\n"
            f"You can check manually at:\n{RELEASES_URL}",
        )
        return

    if is_newer_version(__version__, latest):
        window.set_status(f"Update available: {tag}")
        window.append_log(
            f"Updates: {tag} is available - you are on v{__version__}."
        )
        if release.get("asset_name"):
            window.append_log(
                f"Updates: download {release['asset_name']} from the "
                "release page."
            )
        url = release.get("html_url") or RELEASES_URL
        if window.ask_yesno(
            f"{APP_DISPLAY_NAME} {tag} is available - you are on "
            f"v{__version__}.\n\n"
            "Open the release page in your browser to download it?"
        ):
            webbrowser.open(url)
            window.append_log(f"Updates: opened {url}")
        else:
            window.append_log(f"Updates: release page: {url}")
    else:
        window.set_status(f"Up to date (v{__version__}).")
        window.append_log(
            f"Updates: v{__version__} is the latest published "
            f"release ({tag})."
        )
        window.show_info(
            "Up to Date",
            f"{APP_DISPLAY_NAME} v{__version__} is the latest "
            "published release.",
        )
