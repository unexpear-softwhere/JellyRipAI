"""Behavior tests for the Qt-native update check (tools/update_check.py).

No Qt and no network: the window is a plain fake recording calls
(the real MainWindow methods these map to are thread-safe), and
``fetch_latest_release`` is monkeypatched.  ``_run_check`` is the
synchronous worker body, so most tests drive it directly; one smoke
test exercises the full threaded ``check_for_updates`` entry.

This file is intentionally identical in MAIN and the AI fork — it
reads the channel contract (repo slug, tag prefix, preferred
assets) from the module under test, so each fork verifies its own
values.  The absolute values are pinned per-fork in
``test_release_consistency.py``.
"""

from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools.update_check as uc
from shared.runtime import __version__


class FakeWindow:
    """Records every UI call; scripts the ask_yesno answer."""

    def __init__(self, yes: bool = False) -> None:
        self.statuses: list[str] = []
        self.logs: list[str] = []
        self.infos: list[tuple[str, str]] = []
        self.errors: list[tuple[str, str]] = []
        self.prompts: list[str] = []
        self._yes = yes
        self.done = threading.Event()

    def set_status(self, msg) -> None:
        self.statuses.append(str(msg))

    def append_log(self, msg) -> None:
        self.logs.append(str(msg))

    def show_info(self, title, msg) -> None:
        self.infos.append((str(title), str(msg)))
        self.done.set()

    def show_error(self, title, msg) -> None:
        self.errors.append((str(title), str(msg)))
        self.done.set()

    def ask_yesno(self, prompt) -> bool:
        self.prompts.append(str(prompt))
        self.done.set()
        return self._yes


def _release(version: str, **overrides):
    tag = f"{uc.TAG_PREFIX}{version}"
    data = {
        "tag": tag,
        "version": version,
        "html_url": f"https://github.com/{uc.REPO_SLUG}/releases/tag/{tag}",
        "asset_name": uc.PREFERRED_ASSETS[0],
        "asset_url": "https://example.invalid/asset",
        "prerelease": True,
    }
    data.update(overrides)
    return data


def test_up_to_date_shows_info(monkeypatch):
    monkeypatch.setattr(
        uc, "fetch_latest_release", lambda **_kw: _release(__version__)
    )
    win = FakeWindow()
    uc._run_check(win)
    assert win.infos and "latest" in win.infos[0][1]
    assert not win.errors and not win.prompts
    assert any("up to date" in s.lower() for s in win.statuses)


def test_newer_installer_downloads_verifies_and_launches_on_yes(monkeypatch):
    rel = _release("999.0.0")  # PREFERRED_ASSETS[0] is the .exe installer
    monkeypatch.setattr(uc, "fetch_latest_release", lambda **_kw: rel)
    dl: dict = {}
    verified: list[str] = []
    launched: list[str] = []
    opened: list[str] = []
    monkeypatch.setattr(
        uc, "download_asset",
        lambda url, dest, **kw: dl.update(url=url, dest=dest),
    )
    monkeypatch.setattr(
        uc, "verify_downloaded_update",
        lambda path, **kw: (verified.append(path), (True, "ok"))[1],
    )
    monkeypatch.setattr(uc, "_launch_installer", lambda p: launched.append(p))
    monkeypatch.setattr(uc.webbrowser, "open", lambda url: opened.append(url))
    win = FakeWindow(yes=True)
    uc._run_check(win)
    # Downloaded the installer asset, verified the downloaded file, then
    # launched it — and never bounced to the browser.
    assert dl["url"] == rel["asset_url"]
    assert dl["dest"].endswith(uc.PREFERRED_ASSETS[0])
    assert verified == [dl["dest"]]
    assert launched == [dl["dest"]]
    assert opened == []
    assert win.prompts and rel["tag"] in win.prompts[0]
    assert any(rel["tag"] in s for s in win.statuses)


def test_declining_install_downloads_nothing(monkeypatch):
    rel = _release("999.0.0")
    monkeypatch.setattr(uc, "fetch_latest_release", lambda **_kw: rel)
    acted: list[str] = []
    monkeypatch.setattr(uc, "download_asset", lambda *a, **kw: acted.append("dl"))
    monkeypatch.setattr(uc, "_launch_installer", lambda p: acted.append("launch"))
    monkeypatch.setattr(uc.webbrowser, "open", lambda url: acted.append("open"))
    win = FakeWindow(yes=False)
    uc._run_check(win)
    assert acted == []  # declined: nothing downloaded, launched, or opened
    assert any(rel["html_url"] in line for line in win.logs)


def test_download_failure_falls_back_to_browser(monkeypatch):
    rel = _release("999.0.0")
    monkeypatch.setattr(uc, "fetch_latest_release", lambda **_kw: rel)

    def _boom(url, dest, **kw):
        raise OSError("connection reset")

    monkeypatch.setattr(uc, "download_asset", _boom)
    launched: list[str] = []
    opened: list[str] = []
    monkeypatch.setattr(uc, "_launch_installer", lambda p: launched.append(p))
    monkeypatch.setattr(uc.webbrowser, "open", lambda url: opened.append(url))
    win = FakeWindow(yes=True)
    uc._run_check(win)
    assert launched == []
    assert opened == [rel["html_url"]]  # falls back to the release page
    assert win.errors and win.errors[0][0] == "Download Failed"


def test_non_installer_asset_uses_browser(monkeypatch):
    # When the top asset isn't an .exe (e.g. the portable zip), never
    # auto-launch it — fall back to the manual browser download.
    rel = _release("999.0.0", asset_name=uc.PREFERRED_ASSETS[1])
    monkeypatch.setattr(uc, "fetch_latest_release", lambda **_kw: rel)
    acted: list[str] = []
    monkeypatch.setattr(uc, "download_asset", lambda *a, **kw: acted.append("dl"))
    monkeypatch.setattr(uc, "_launch_installer", lambda p: acted.append("launch"))
    opened: list[str] = []
    monkeypatch.setattr(uc.webbrowser, "open", lambda url: opened.append(url))
    win = FakeWindow(yes=True)
    uc._run_check(win)
    assert acted == []  # no download, no launch
    assert opened == [rel["html_url"]]


def test_network_failure_reports_error_with_manual_url(monkeypatch):
    def _boom(**_kw):
        raise OSError("name resolution failed")

    monkeypatch.setattr(uc, "fetch_latest_release", _boom)
    win = FakeWindow()
    uc._run_check(win)
    assert win.errors
    title, msg = win.errors[0]
    assert title == "Update Check Failed"
    assert uc.RELEASES_URL in msg
    assert not win.prompts


def test_empty_release_payload_reports_error(monkeypatch):
    monkeypatch.setattr(
        uc,
        "fetch_latest_release",
        lambda **_kw: {
            "tag": "",
            "version": "",
            "html_url": "",
            "asset_name": "",
            "asset_url": "",
            "prerelease": False,
        },
    )
    win = FakeWindow()
    uc._run_check(win)
    assert win.errors and uc.RELEASES_URL in win.errors[0][1]


def test_fetch_called_with_pinned_channel_contract(monkeypatch):
    """The worker must query exactly the channel the module pins:
    this fork's repo, this fork's tag prefix, prereleases included
    (every release publishes as a prerelease), this fork's assets."""
    seen: dict = {}

    def _fake(**kwargs):
        seen.update(kwargs)
        return _release(__version__)

    monkeypatch.setattr(uc, "fetch_latest_release", _fake)
    uc._run_check(FakeWindow())
    assert seen["repo"] == uc.REPO_SLUG
    assert seen["include_prereleases"] is True
    assert seen["tag_prefix"] == uc.TAG_PREFIX
    assert tuple(seen["preferred_assets"]) == tuple(uc.PREFERRED_ASSETS)


def test_second_click_while_running_logs_and_returns():
    win = FakeWindow()
    assert uc._check_lock.acquire(blocking=False)
    try:
        uc.check_for_updates(win)
        assert any("already in progress" in line for line in win.logs)
        assert win.statuses == []  # no new check started
    finally:
        uc._check_lock.release()


def test_threaded_entry_completes_and_releases_lock(monkeypatch):
    monkeypatch.setattr(
        uc, "fetch_latest_release", lambda **_kw: _release(__version__)
    )
    win = FakeWindow()
    uc.check_for_updates(win)
    assert win.done.wait(5), "worker never reported a result"
    # The worker's finally releases the lock (may race the event by a
    # few ms) — a follow-up check must be able to start.
    pause = threading.Event()
    for _ in range(100):
        if uc._check_lock.acquire(blocking=False):
            uc._check_lock.release()
            break
        pause.wait(0.05)
    else:
        raise AssertionError("check lock was never released")
