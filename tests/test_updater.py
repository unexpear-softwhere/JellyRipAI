"""Tests for update verification and download safeguards."""

from pathlib import Path

import pytest

from utils import updater


class _FakeResponse:
    def __init__(self, chunks, total="0"):
        self._chunks = list(chunks)
        self.headers = {"Content-Length": total}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, _size):
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


def test_verify_downloaded_update_valid_signature(monkeypatch):
    monkeypatch.setattr(
        updater,
        "get_authenticode_signature",
        lambda _path: {
            "status": "Valid",
            "status_message": "",
            "thumbprint": "ABCDEF",
            "subject": "CN=Signer",
        },
    )

    ok, msg = updater.verify_downloaded_update(
        "dummy.exe",
        require_signature=True,
        required_thumbprint="ABCDEF",
    )

    assert ok is True
    assert (
        "valid" in msg.lower()
        or "validated" in msg.lower()
        or "passed" in msg.lower()
        or "verified" in msg.lower()
    )


def test_download_asset_detects_stall(monkeypatch, tmp_path):
    chunks = [b"a", b"b", b"c", b""]
    monkeypatch.setattr(
        updater.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _FakeResponse(chunks, total="3"),
    )

    tick = {"t": 0.0}

    def fake_time():
        # Advance quickly so each chunk lands in a new timeout window.
        tick["t"] += 61.0
        return tick["t"]

    monkeypatch.setattr(updater.time, "time", fake_time)

    out_file = Path(tmp_path) / "update.bin"

    with pytest.raises(TimeoutError):
        updater.download_asset(
            "https://example.invalid/update.bin",
            str(out_file),
            timeout=1,
            max_total_seconds=0,
            stall_window_seconds=60,
            min_window_bytes=1024,
        )


def test_fetch_latest_release_can_include_prereleases(monkeypatch):
    releases = [
        {
            "tag_name": "ai-v1.0.16",
            "html_url": "https://example.invalid/ai-v1.0.16",
            "prerelease": True,
            "draft": False,
            "assets": [
                {
                    "name": "JellyRipAIInstaller.exe",
                    "browser_download_url": "https://example.invalid/JellyRipAIInstaller.exe",
                }
            ],
        },
        {
            "tag_name": "v1.0.11",
            "html_url": "https://example.invalid/v1.0.11",
            "prerelease": False,
            "draft": False,
            "assets": [
                {
                    "name": "JellyRip.exe",
                    "browser_download_url": "https://example.invalid/JellyRip.exe",
                }
            ],
        },
    ]

    class _JsonResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return updater.json.dumps(releases).encode("utf-8")

    monkeypatch.setattr(
        updater.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _JsonResponse(),
    )

    release = updater.fetch_latest_release(
        "unexpear/JellyRip",
        include_prereleases=True,
        preferred_assets=("JellyRipAIInstaller.exe", "JellyRipAI.exe"),
        tag_prefix="ai-v",
    )

    assert release["tag"] == "ai-v1.0.16"
    assert release["version"] == "1.0.16"
    assert release["asset_name"] == "JellyRipAIInstaller.exe"
    assert release["prerelease"] is True


def test_fetch_latest_release_skips_prereleases_by_default(monkeypatch):
    releases = [
        {
            "tag_name": "v1.0.16",
            "html_url": "https://example.invalid/v1.0.16",
            "prerelease": True,
            "draft": False,
            "assets": [],
        },
        {
            "tag_name": "v1.0.11",
            "html_url": "https://example.invalid/v1.0.11",
            "prerelease": False,
            "draft": False,
            "assets": [
                {
                    "name": "JellyRip.exe",
                    "browser_download_url": "https://example.invalid/JellyRip.exe",
                }
            ],
        },
    ]

    class _JsonResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return updater.json.dumps(releases).encode("utf-8")

    monkeypatch.setattr(
        updater.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _JsonResponse(),
    )

    release = updater.fetch_latest_release("unexpear/JellyRip")

    assert release["tag"] == "v1.0.11"
    assert release["version"] == "1.0.11"
    assert release["asset_name"] == "JellyRip.exe"
    assert release["prerelease"] is False


def test_fetch_latest_release_filters_channel_and_extracts_version(monkeypatch):
    releases = [
        {
            "tag_name": "v1.0.20",
            "html_url": "https://example.invalid/v1.0.20",
            "prerelease": True,
            "draft": False,
            "assets": [
                {
                    "name": "JellyRipInstaller.exe",
                    "browser_download_url": "https://example.invalid/JellyRipInstaller.exe",
                }
            ],
        },
        {
            "tag_name": "ai-v1.0.18",
            "html_url": "https://example.invalid/ai-v1.0.18",
            "prerelease": True,
            "draft": False,
            "assets": [
                {
                    "name": "JellyRipAI.exe",
                    "browser_download_url": "https://example.invalid/JellyRipAI.exe",
                }
            ],
        },
    ]

    class _JsonResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return updater.json.dumps(releases).encode("utf-8")

    monkeypatch.setattr(
        updater.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _JsonResponse(),
    )

    release = updater.fetch_latest_release(
        "unexpear/JellyRip",
        include_prereleases=True,
        preferred_assets=("JellyRipAIInstaller.exe", "JellyRipAI.exe"),
        tag_prefix="ai-v",
    )

    assert release["tag"] == "ai-v1.0.18"
    assert release["version"] == "1.0.18"
    assert release["asset_name"] == "JellyRipAI.exe"


def test_download_asset_rejects_truncated_body(monkeypatch, tmp_path):
    """http.client treats a connection dropped mid-body as a clean
    EOF, so the byte count must be checked against Content-Length —
    and nothing may remain at the destination (or .partial) after a
    failed download."""
    chunks = [b"abc", b""]  # 3 of a promised 10 bytes
    monkeypatch.setattr(
        updater.urllib.request,
        "urlopen",
        lambda *_a, **_k: _FakeResponse(chunks, total="10"),
    )
    out_file = Path(tmp_path) / "update.bin"

    with pytest.raises(OSError):
        updater.download_asset(
            "https://example.invalid/update.bin",
            str(out_file),
            stall_window_seconds=0,
        )

    assert not out_file.exists(), \
        "a truncated download must never land at the destination"
    assert not Path(str(out_file) + ".partial").exists(), \
        "the partial is cleaned up on failure"


def test_download_asset_complete_body_lands_at_destination(
    monkeypatch, tmp_path,
):
    chunks = [b"abc", b"de", b""]
    monkeypatch.setattr(
        updater.urllib.request,
        "urlopen",
        lambda *_a, **_k: _FakeResponse(chunks, total="5"),
    )
    out_file = Path(tmp_path) / "update.bin"

    updater.download_asset(
        "https://example.invalid/update.bin",
        str(out_file),
        stall_window_seconds=0,
    )

    assert out_file.read_bytes() == b"abcde"
    assert not Path(str(out_file) + ".partial").exists()


def test_authenticode_query_binds_path_for_real(tmp_path):
    """End-to-end against real PowerShell.  With ``-Command``,
    trailing argv tokens are NOT bound to ``param()`` — the old
    ``-p <path>`` form left ``$p`` empty, so every signature query
    raised and the verify gate could never pass.  The env-var form
    must bind: an unsigned file reports NotSigned (a real, parsed
    answer — not an error)."""
    import sys

    if sys.platform != "win32":
        pytest.skip("Authenticode is Windows-only")

    target = Path(tmp_path) / "unsigned.exe"
    target.write_bytes(b"MZ not really an exe")

    sig = updater.get_authenticode_signature(str(target))

    # The old broken invocation RAISED here (exit 1: $p never bound,
    # the trailing -p arg hit ParameterBindingValidationException).
    # With the path bound, PowerShell parses the file and returns a
    # real status — "NotSigned" for unsigned PEs, "UnknownError" for
    # a non-PE like this stub.  Either proves the round-trip works.
    assert sig["status"] in ("NotSigned", "UnknownError")
    assert sig["status"] != "Valid"
