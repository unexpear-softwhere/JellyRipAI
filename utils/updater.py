"""Update helpers for checking GitHub releases and downloading assets."""

import hashlib
import json
import os
import re
import subprocess
import sys as _sys
import time
import urllib.error
import urllib.request
from shared.windows_exec import get_powershell_executable

# Resolve PowerShell executable once at import time using the trusted
# Windows system path rather than search-order lookup.
_ps_exe = get_powershell_executable()

# Suppress black CMD flash on Windows.
_POPEN_FLAGS = {"creationflags": 0x08000000} if _sys.platform == "win32" else {}


def _extract_version_string(value):
    token = str(value or "").strip().lower()
    match = re.search(r"(\d+(?:\.\d+)+)", token)
    if match:
        return match.group(1)
    if token.startswith("v"):
        return token[1:]
    return token


def _normalize_version(value):
    token = _extract_version_string(value)
    parts = []
    for piece in token.split("."):
        try:
            parts.append(int(piece))
        except Exception:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_newer_version(current_version, latest_version):
    """Return True when latest_version is newer than current_version."""
    return _normalize_version(latest_version) > _normalize_version(current_version)


def fetch_latest_release(
    repo="unexpear-softwhere/JellyRipAI",
    timeout=8,
    *,
    include_prereleases=False,
    preferred_assets=None,
    tag_prefix="",
):
    """Fetch GitHub release metadata for the given repo."""
    if include_prereleases:
        api_url = f"https://api.github.com/repos/{repo}/releases?per_page=10"
    else:
        api_url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(
        api_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "JellyRip-Updater",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    expected_tag_prefix = str(tag_prefix or "").strip().lower()

    def _matches_expected_channel(item):
        if not isinstance(item, dict):
            return False
        if item.get("draft"):
            return False
        if not include_prereleases and item.get("prerelease"):
            return False
        if not expected_tag_prefix:
            return True
        tag_name = str(item.get("tag_name") or "").strip().lower()
        return tag_name.startswith(expected_tag_prefix)

    if isinstance(payload, list):
        candidates = [item for item in payload if _matches_expected_channel(item)]
        # Newest by parsed version, not list position — robust against
        # GitHub's created-date ordering when a release is re-published.
        release = max(
            candidates,
            key=lambda item: _normalize_version(item.get("tag_name")),
            default={},
        )
    else:
        release = payload if _matches_expected_channel(payload) else {}

    assets = release.get("assets") or []
    # Installer first, then the portable zip (the one-DIR replacement
    # for the old bare exe — there is no JellyRipAI.exe asset anymore).
    preferred = list(
        preferred_assets or ["JellyRipAIInstaller.exe", "JellyRipAI-portable.zip"]
    )
    chosen_asset = None
    for name in preferred:
        chosen_asset = next((a for a in assets if a.get("name") == name), None)
        if chosen_asset:
            break
    if chosen_asset is None and assets:
        chosen_asset = assets[0]

    tag = str(release.get("tag_name") or "").strip()
    normalized = _extract_version_string(tag)

    return {
        "tag": tag,
        "version": normalized,
        "html_url": release.get("html_url") or "",
        "asset_name": (chosen_asset or {}).get("name") or "",
        "asset_url": (chosen_asset or {}).get("browser_download_url") or "",
        "prerelease": bool(release.get("prerelease")),
    }


def download_asset(url, destination_path, progress_callback=None, timeout=15,
                   abort_event=None, max_total_seconds=1800,
                   stall_window_seconds=120, min_window_bytes=64 * 1024):
    """Download a release asset to destination_path.

    Writes to ``<destination>.partial`` and renames into place only
    after the byte count matches Content-Length.  http.client treats a
    connection dropped mid-body as a clean EOF, so without the length
    check a half-downloaded exe looked like success — and failures
    used to leave partial bytes at the final path.
    """
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "JellyRip-Updater"},
    )
    temp_path = str(destination_path) + ".partial"
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            written = 0
            start_time = time.time()
            window_start = start_time
            window_bytes = 0
            with open(temp_path, "wb") as out:
                while True:
                    if abort_event and abort_event.is_set():
                        raise InterruptedError("Download aborted by user")
                    now = time.time()
                    if max_total_seconds and now - start_time > max_total_seconds:
                        raise TimeoutError(
                            "Download exceeded maximum allowed duration"
                        )
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    out.write(chunk)
                    written += len(chunk)
                    window_bytes += len(chunk)
                    if stall_window_seconds and (now - window_start) >= stall_window_seconds:
                        if window_bytes < min_window_bytes:
                            raise TimeoutError(
                                "Download stalled (throughput below minimum threshold)"
                            )
                        window_start = now
                        window_bytes = 0
                    if progress_callback:
                        progress_callback(written, total)
        if total and written != total:
            raise OSError(
                f"Download truncated: received {written} of {total} bytes"
            )
        os.replace(temp_path, destination_path)
    finally:
        # On any failure the partial never reaches the destination.
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass


def sha256_file(path):
    """Return lowercase SHA256 hex for the given file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(1024 * 1024)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def get_authenticode_signature(path):
    """Get Authenticode signature details via PowerShell on Windows."""
    # The path travels via an environment variable.  With -Command,
    # trailing argv tokens are appended to the command text rather than
    # bound to param() — the old "-p <path>" form left $p empty, so
    # every signature query failed (the verify gate could never pass).
    # An env var is also injection-proof: the value is never parsed as
    # PowerShell, and -LiteralPath disables wildcard expansion.
    ps = (
        "$p = $env:JELLYRIP_VERIFY_PATH; "
        "$sig = Get-AuthenticodeSignature -LiteralPath $p; "
        "$out = [PSCustomObject]@{"
        "Status = [string]$sig.Status; "
        "StatusMessage = [string]$sig.StatusMessage; "
        "Thumbprint = [string]($sig.SignerCertificate.Thumbprint); "
        "Subject = [string]($sig.SignerCertificate.Subject)"
        "}; "
        "$out | ConvertTo-Json -Compress"
    )
    env = dict(os.environ)
    env["JELLYRIP_VERIFY_PATH"] = str(path)
    proc = subprocess.run(
        [_ps_exe, "-NoProfile", "-Command", ps],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        shell=False,
        env=env,
        **_POPEN_FLAGS
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "signature query failed")
    data = json.loads(proc.stdout.strip() or "{}")
    return {
        "status": str(data.get("Status") or "").strip(),
        "status_message": str(data.get("StatusMessage") or "").strip(),
        "thumbprint": str(data.get("Thumbprint") or "").strip().upper(),
        "subject": str(data.get("Subject") or "").strip(),
    }


def verify_downloaded_update(
    path,
    *,
    require_signature=True,
    required_thumbprint="",
):
    """Validate downloaded update package before launch."""
    if not require_signature:
        return True, "Signature verification is disabled by configuration."

    pin = (required_thumbprint or "").strip().upper().replace(" ", "")
    if not pin:
        return (
            False,
            "Signature verification requires a pinned signer thumbprint, "
            "but none is configured.",
        )

    try:
        sig = get_authenticode_signature(path)
    except Exception as e:
        return False, f"Could not verify Authenticode signature: {e}"

    if sig.get("status") != "Valid":
        msg = sig.get("status_message") or "Unsigned or invalid signature"
        return False, f"Signature validation failed: {msg}"

    got = (sig.get("thumbprint") or "").strip().upper().replace(" ", "")
    if pin and got != pin:
        return (
            False,
            "Signer certificate thumbprint mismatch. "
            f"Expected {pin}, got {got or '<none>'}."
        )

    return True, (
        "Authenticode signature is valid"
        + (f" (subject: {sig.get('subject')})" if sig.get("subject") else "")
    )


__all__ = [
    "download_asset",
    "fetch_latest_release",
    "get_authenticode_signature",
    "is_newer_version",
    "sha256_file",
    "verify_downloaded_update",
]
