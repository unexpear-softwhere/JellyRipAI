"""Update workflow helpers extracted from the main GUI window module."""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import webbrowser
from shared.runtime import (
    APP_DISPLAY_NAME,
    APP_RELEASE_REPO,
    APP_RELEASE_TAG_PREFIX,
    APP_UPDATER_ASSET_PREFERENCE,
    APP_VARIANT,
    __version__,
)
from utils.updater import (
    download_asset,
    fetch_latest_release,
    is_newer_version,
    sha256_file,
    verify_downloaded_update,
)

def launch_downloaded_update(gui, downloaded_path):
    """Launch downloaded update package and close app for file replacement."""
    try:
        update_dir = os.path.dirname(os.path.normpath(downloaded_path))
        is_installer = os.path.basename(downloaded_path).lower().endswith("installer.exe")
        gui.controller.log(
            "A UAC permission prompt may appear.\n"
            f"{APP_DISPLAY_NAME} will now close so files can be replaced."
        )
        gui.engine.abort()
        gui.after(500, gui.destroy)
        if is_installer:
            try:
                if sys.platform == "win32":
                    subprocess.Popen(
                        [
                            downloaded_path,
                            "/SP-",
                            "/SUPPRESSMSGBOXES",
                        ],
                        creationflags=0x08000000,
                    )
                else:
                    subprocess.Popen(
                        [
                            downloaded_path,
                            "/SP-",
                            "/SUPPRESSMSGBOXES",
                        ]
                    )
            except Exception as e:
                gui.controller.log(
                    f"Silent installer launch failed ({e}); falling back to standard launch."
                )
                os.startfile(downloaded_path)
        else:
            os.startfile(downloaded_path)
        # Best-effort cleanup after launch. Run detached so cleanup can
        expected_prefix = f"JellyRip{APP_VARIANT.upper()}Update_"
        if update_dir and os.path.basename(update_dir).startswith(expected_prefix):
            safe_dir = update_dir.replace("'", "''")
            cleanup_cmd = (
                f"for($i=0;$i -lt 120;$i++){{"
                f"try{{Remove-Item -LiteralPath '{safe_dir}' -Recurse -Force -ErrorAction Stop;break}}"
                f"catch{{Start-Sleep -Seconds 2}}"
                f"}}"
            )
            try:
                if sys.platform == "win32":
                    subprocess.Popen(
                        [
                            "powershell",
                            "-NoProfile",
                            "-WindowStyle",
                            "Hidden",
                            "-Command",
                            cleanup_cmd,
                        ],
                        creationflags=0x08000000,
                    )
                else:
                    subprocess.Popen(
                        [
                            "powershell",
                            "-NoProfile",
                            "-WindowStyle",
                            "Hidden",
                            "-Command",
                            cleanup_cmd,
                        ]
                    )
        # Use a unique per-download temp directory to prevent TOCTOU
        # attacks via the predictable JellyRipUpdate/ fixed path.
            except Exception:
                pass
    except Exception as e:
        gui.controller.log(f"Could not launch update package: {e}")
        gui.show_error(
            "Update Downloaded",
            "Downloaded update package but could not launch it.\n\n"
            f"Run this file manually:\n{downloaded_path}"
        )


def check_for_updates(gui):
    """Check GitHub releases for a newer version and offer download."""
    gui.set_status("Checking for updates...")
    gui.controller.log("Checking GitHub Releases for updates...")
    if hasattr(gui, "update_btn"):
        gui.update_btn.config(state="disabled")

    def _finish_ready():
        gui.set_status("Ready")
        if hasattr(gui, "update_btn"):
            gui.update_btn.config(state="normal")

    def worker():
        try:
            release = fetch_latest_release(
                APP_RELEASE_REPO,
                include_prereleases=True,
                preferred_assets=APP_UPDATER_ASSET_PREFERENCE,
                tag_prefix=APP_RELEASE_TAG_PREFIX,
            )
        except urllib.error.URLError as e:
            gui.controller.log(f"Update check failed: {e}")
            gui.after(
                0,
                lambda: gui.show_error(
                    "Update Check Failed",
                    "Could not reach GitHub Releases right now."
                )
            )
            gui.after(0, _finish_ready)
            return
        except Exception as e:
            gui.controller.log(f"Update check failed: {e}")
            gui.after(
                0,
                lambda: gui.show_error(
                    "Update Check Failed",
                    f"Unexpected error while checking updates:\n{e}"
                )
            )
            gui.after(0, _finish_ready)
            return

        latest = release.get("version") or ""
        if not latest:
            gui.controller.log("Latest release has no usable version tag.")
            gui.after(
                0,
                lambda: gui.show_error(
                    "Update Check Failed",
                    "Latest release metadata did not include a version."
                )
            )
            gui.after(0, _finish_ready)
            return

        if not is_newer_version(__version__, latest):
            gui.controller.log(
                f"Already up to date (current: {__version__}, latest: {latest})."
            )
            gui.after(
                0,
                lambda: gui.show_info(
                    "No Update Available",
                    f"You are already on v{__version__}."
                )
            )
            gui.after(0, _finish_ready)
            return

        gui.controller.log(
            f"Update available: v{latest} (current: v{__version__})"
        )
        wants_update = gui.ask_yesno(
            f"Update available: v{latest} (current: v{__version__}).\n"
            "Download and install now?"
        )
        if not wants_update:
            gui.controller.log("Update deferred by user.")
            gui.after(0, _finish_ready)
            return

        asset_url = release.get("asset_url") or ""
        asset_name = release.get("asset_name") or APP_UPDATER_ASSET_PREFERENCE[-1]
        page_url = release.get("html_url") or ""

        if not asset_url:
            gui.controller.log("No downloadable asset found in latest release.")
            if page_url:
                webbrowser.open(page_url)
            gui.after(0, _finish_ready)
            return

        update_dir = tempfile.mkdtemp(
            prefix=f"JellyRip{APP_VARIANT.upper()}Update_"
        )
        destination = os.path.join(update_dir, asset_name)

        gui.set_status("Downloading update...")
        gui.controller.log(f"Downloading update asset: {asset_name}")

        last_logged_mb = {"mb": -1}

        def on_progress(written, total):
            mb = written // (1024 * 1024)
            if mb == last_logged_mb["mb"]:
                return
            last_logged_mb["mb"] = mb
            if total > 0:
                pct = int((written / total) * 100)
                gui.controller.log(
                    f"Update download: {pct}% ({mb} MB)"
                )
            else:
                gui.controller.log(f"Update download: {mb} MB")

        try:
            download_asset(
                asset_url, destination, on_progress,
                abort_event=gui.engine.abort_event,
            )
        except Exception as e:
            gui.controller.log(f"Update download failed: {e}")
            shutil.rmtree(update_dir, ignore_errors=True)
            gui.after(
                0,
                lambda: gui.show_error(
                    "Update Download Failed",
                    f"Could not download update package:\n{e}"
                )
            )
            gui.after(0, _finish_ready)
            return

        try:
            digest = sha256_file(destination)
            gui.controller.log(f"Update SHA256: {digest}")
        except Exception as e:
            gui.controller.log(f"Could not compute update SHA256: {e}")

        require_sig = bool(
            gui.cfg.get("opt_update_require_signature", True)
        )
        pinned_thumbprint = str(
            gui.cfg.get("opt_update_signer_thumbprint", "")
        )
        if require_sig and not pinned_thumbprint.strip():
            gui.controller.log(
                "Update blocked: signature pinning is enabled but "
                "opt_update_signer_thumbprint is empty."
            )
            shutil.rmtree(update_dir, ignore_errors=True)
            gui.after(
                0,
                lambda: gui.show_error(
                    "Update Blocked",
                    "Signature verification is enabled but no signer "
                    "thumbprint is configured.\n\n"
                    "To enable updates, open Settings → Advanced, and set "
                    "the 'Update Signer Thumbprint' field to your release certificate thumbprint.\n\n"
                    "See the documentation: https://github.com/unexpear/JellyRip/wiki/Update-Signing for details.",
                    "Set opt_update_signer_thumbprint in Settings to "
                    "your release certificate thumbprint before using "
                    "auto-update.",
                ),
            )
            gui.after(0, _finish_ready)
            return
        ok, verify_msg = verify_downloaded_update(
            destination,
            require_signature=require_sig,
            required_thumbprint=pinned_thumbprint,
        )
        gui.controller.log(verify_msg)
        if not ok:
            shutil.rmtree(update_dir, ignore_errors=True)
            gui.after(
                0,
                lambda: gui.show_error(
                    "Update Blocked",
                    "Downloaded update failed signature verification.\n\n"
                    "The package will not be launched automatically."
                )
            )
            gui.after(0, _finish_ready)
            return

        gui.controller.log(f"Update downloaded to: {destination}")
        gui.after(0, _finish_ready)
        gui.after(0, lambda: launch_downloaded_update(gui, destination))

    threading.Thread(target=worker, daemon=True).start()
