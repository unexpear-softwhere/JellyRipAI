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

When a newer build exists, the check offers to download the installer
asset and run it.  Trust model: the asset URL comes from the GitHub
Releases API for this fork's pinned ``REPO_SLUG`` over HTTPS, and
``utils.updater.download_asset`` is truncation-safe (it renames the
file into place only when the byte count matches Content-Length).
Authenticode verification is enforced *iff* a signer thumbprint is
pinned in ``PINNED_SIGNER_THUMBPRINT`` — today's prerelease builds are
unsigned, so the signature step is skipped and noted; pin a thumbprint
once builds are signed and the check turns on with no other change.
Any download / verify / launch failure (and any non-installer asset,
e.g. the portable zip) falls back to opening the release page in the
browser.

``test_release_consistency.py`` pins ``REPO_SLUG`` / ``TAG_PREFIX``
/ ``PREFERRED_ASSETS`` against ``release.bat`` so this check and
the publish pipeline can't drift apart.
"""

from __future__ import annotations

import os
import sys as _sys
import tempfile
import threading
import webbrowser

from shared.runtime import APP_DISPLAY_NAME, __version__
from utils.updater import (
    download_asset,
    fetch_latest_release,
    is_newer_version,
    verify_downloaded_update,
)

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
# Authenticode signer thumbprint to require on a downloaded installer.
# Empty = unsigned builds (today): the signature check is skipped and
# the HTTPS GitHub release URL + truncation-safe download are the trust
# anchors.  Once builds are code-signed, paste the signer's thumbprint
# here and verification is enforced automatically.
PINNED_SIGNER_THUMBPRINT = ""

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
        page_url = release.get("html_url") or RELEASES_URL
        asset_url = str(release.get("asset_url") or "").strip()
        asset_name = str(release.get("asset_name") or "").strip()

        # Only auto-run an executable installer.  A non-installer asset
        # (e.g. the portable zip) keeps the manual browser flow so we
        # never launch an archive.
        if asset_url and asset_name.lower().endswith(".exe"):
            if window.ask_yesno(
                f"{APP_DISPLAY_NAME} {tag} is available - you are on "
                f"v{__version__}.\n\n"
                f"Download and run the installer now?\n({asset_name})"
            ):
                _download_and_run_installer(
                    window, asset_url, asset_name, tag, page_url
                )
            else:
                window.append_log(f"Updates: release page: {page_url}")
            return

        if window.ask_yesno(
            f"{APP_DISPLAY_NAME} {tag} is available - you are on "
            f"v{__version__}.\n\n"
            "Open the release page in your browser to download it?"
        ):
            webbrowser.open(page_url)
            window.append_log(f"Updates: opened {page_url}")
        else:
            window.append_log(f"Updates: release page: {page_url}")
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


def _download_and_run_installer(window, asset_url, asset_name, tag, page_url):
    """Download the installer to a temp dir, verify it, and launch it.

    Runs on the update-check worker thread, so the download never
    blocks the UI; progress and outcome surface through the window's
    thread-safe methods.  Any failure at any step falls back to opening
    the release page so the user can always finish the update by hand.
    """
    dest = os.path.join(tempfile.gettempdir(), asset_name)
    window.set_status(f"Downloading {asset_name}...")
    window.append_log(f"Updates: downloading {asset_name}...")

    last_pct = [-1]

    def _progress(written, total):
        if not total:
            return
        pct = min(written * 100 // total, 100)
        if pct >= last_pct[0] + 10:
            last_pct[0] = pct - (pct % 10)
            window.set_status(f"Downloading {asset_name}... {pct}%")

    try:
        download_asset(asset_url, dest, progress_callback=_progress)
    except Exception as exc:
        window.set_status("Update download failed.")
        window.append_log(f"Updates: download failed - {exc}")
        window.show_error(
            "Download Failed",
            f"Could not download {asset_name}:\n\n{exc}\n\n"
            "Opening the release page so you can download it manually.",
        )
        webbrowser.open(page_url)
        return

    require_sig = bool(PINNED_SIGNER_THUMBPRINT.strip())
    ok, detail = verify_downloaded_update(
        dest,
        require_signature=require_sig,
        required_thumbprint=PINNED_SIGNER_THUMBPRINT,
    )
    if not ok:
        window.set_status("Update verification failed.")
        window.append_log(f"Updates: verification failed - {detail}")
        window.show_error(
            "Update Verification Failed",
            f"The downloaded installer could not be verified:\n\n{detail}\n\n"
            "Opening the release page instead.",
        )
        webbrowser.open(page_url)
        return
    if require_sig:
        window.append_log(f"Updates: {detail}")
    else:
        window.append_log(
            "Updates: signature check skipped (build is not code-signed)."
        )

    window.set_status("Launching installer...")
    window.append_log(f"Updates: launching {asset_name}...")
    try:
        _launch_installer(dest)
    except Exception as exc:
        window.append_log(f"Updates: could not launch installer - {exc}")
        window.show_error(
            "Couldn't Launch Installer",
            f"The installer was downloaded to:\n{dest}\n\n"
            f"but couldn't be started automatically ({exc}).\n\n"
            "Run it yourself to finish updating.",
        )
        return
    window.append_log(
        f"Updates: installer started from {dest}; it will close "
        f"{APP_DISPLAY_NAME} to finish."
    )
    window.show_info(
        "Installing Update",
        f"The {APP_DISPLAY_NAME} {tag} installer is starting.\n\n"
        f"It will close {APP_DISPLAY_NAME} to apply the update, then "
        "offer to relaunch.",
    )


def _launch_installer(path):
    """Launch the downloaded installer.  Split out for testability and
    so the platform branch is in one place.  Uses ShellExecute via
    ``os.startfile`` on Windows (user-initiated, controlled temp path —
    no shell-command parsing); a plain ``Popen`` elsewhere."""
    if _sys.platform == "win32":
        os.startfile(path)  # noqa: S606 — controlled path, no shell string
    else:
        import subprocess
        subprocess.Popen([path])
