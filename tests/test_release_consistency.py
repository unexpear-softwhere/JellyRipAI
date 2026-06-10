from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _current_version() -> str:
    runtime_text = _read("shared/runtime.py")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', runtime_text)
    assert match is not None
    return match.group(1)


def test_release_metadata_matches_current_version():
    version = _current_version()
    installer_text = _read("installer/JellyRip.iss")
    changelog_text = _read("CHANGELOG.md")
    readme = _read("README.md")

    assert f'version = "{version}"' in _read("pyproject.toml")
    assert f'#define MyAppVersion "{version}"' in installer_text
    assert "VersionInfoVersion={#MyAppVersion}" in installer_text
    assert "VersionInfoProductVersion={#MyAppVersion}" in installer_text
    assert f"- Current unstable line: `ai-v{version}`" in readme
    assert f"(recommended, currently `ai-v{version}` unstable AI pre-release)" in readme
    assert "Settings are stored at `%APPDATA%\\JellyRipAI\\config.json` on Windows." in readme
    assert f"v{version}" in _read("TESTERS.md")
    assert f"ai-v{version}" in _read("release_notes.txt")
    assert f"ai-v{version}" in _read("release_notes.md")
    assert re.search(rf"^## \[{re.escape(version)}\] - ", changelog_text, re.MULTILINE)


def test_readme_points_to_spec_build_and_release_notes_txt():
    readme = _read("README.md")
    version = _current_version()

    assert "build.bat" in readme
    assert "pyinstaller JellyRip.spec" in readme
    assert "release_notes.txt" in readme
    assert f"release.bat {version}" in readme
    # Two-repo fork model (post-2026-05-08).  The AI fork lives on its
    # own repo (unexpear-softwhere/JellyRipAI), so README's clone
    # instructions point at that URL — not at MAIN with a branch
    # switch.  The legacy `git switch --track origin/ai` line is gone
    # along with the legacy unexpear/JellyRipAI-old repo.
    assert "unexpear-softwhere/JellyRipAI" in readme
    assert "JellyRipAI.exe" in readme
    assert "JellyRipAIInstaller.exe" in readme
    # One-DIR bundle (2026-06-09): the build output is an app folder
    # (exe + _internal\), the portable download is a zip of it, and
    # the FFmpeg notices ship inside _internal\licenses\ffmpeg\ —
    # there are no staged FFmpeg-LICENSE.txt copies anymore.
    assert "dist/ai/JellyRipAI/JellyRipAI.exe" in readme
    assert "JellyRipAI-portable.zip" in readme
    assert "_internal\\licenses\\ffmpeg\\" in readme
    assert "FFmpeg-LICENSE.txt" not in readme
    assert "ffplay" not in readme
    assert "%USERPROFILE%\\Desktop\\ffmpeg" in readme


def test_update_check_contract_matches_release_pipeline():
    """The in-app update check and release.bat must agree on where
    releases live: same repo, same tag prefix (ai-v*, never MAIN's
    v*), same artifact names, prereleases included (every release
    publishes as a GitHub prerelease while the project is
    pre-alpha).  If the publish target or artifact set ever
    changes, the updater must change in the same commit — this is
    what keeps the Check Updates chip always pointing at the
    latest published build."""
    src = _read("tools/update_check.py")
    release_script = _read("release.bat")

    assert 'REPO_SLUG = "unexpear-softwhere/JellyRipAI"' in src
    assert 'TAG_PREFIX = "ai-v"' in src
    assert "include_prereleases=True" in src
    assert '"JellyRipAIInstaller.exe"' in src
    assert '"JellyRipAI-portable.zip"' in src
    assert "feature pending Qt port" not in src

    assert 'set "RELEASE_TAG=ai-v%VERSION%"' in release_script
    assert "--prerelease" in release_script


def test_ai_branch_declares_claude_runtime_dependency():
    readme = _read("README.md")
    requirements = _read("requirements.txt")

    assert "pip install -r requirements.txt" in readme
    assert re.search(r"^anthropic\s*$", requirements, re.MULTILINE)


def test_release_script_checks_git_state_and_release_notes():
    release_script = _read("release.bat")
    version = _current_version()

    assert "git status --porcelain" in release_script
    assert "git rev-parse --abbrev-ref HEAD" in release_script
    assert 'findstr /C:"v%VERSION%" release_notes.txt' in release_script
    # One-DIR bundle (2026-06-09): the staging step is retired — FFmpeg
    # ships inside the app folder's _internal\, and the portable
    # artifact is a zip of the folder instead of a bare exe.
    assert "stage_ffmpeg_bundle.ps1" not in release_script
    assert 'set "ARTIFACT_DIR=dist\\ai"' in release_script
    assert 'set "BUILD_DIR=build\\ai"' in release_script
    assert 'type nul > "%ARTIFACT_DIR%\\.gitkeep"' in release_script
    assert "%ARTIFACT_DIR%\\JellyRipAI\\JellyRipAI.exe" in release_script
    assert "_internal\\ffmpeg.exe" in release_script
    assert "_internal\\ffprobe.exe" in release_script
    assert "_internal\\licenses\\ffmpeg\\LICENSE" in release_script
    assert "JellyRipAI-portable.zip" in release_script
    assert "Compress-Archive" in release_script
    assert 'LICENSE THIRD_PARTY_NOTICES.md --title' in release_script
    # ffplay was dropped 2026-06-09 (unused; ~130 MB per artifact).
    assert "ffplay" not in release_script.lower()
    assert 'set "RELEASE_BRANCH=main"' in release_script
    assert 'set "RELEASE_TAG=ai-v%VERSION%"' in release_script
    assert "%ARTIFACT_DIR%\\JellyRipAIInstaller.exe" in release_script
    assert "JellyRip AI releases intentionally bundle FFmpeg" in release_script
    assert f"REM  Usage:  release.bat {version}" in release_script
    assert f"echo Example: release.bat {version}" in release_script


def test_release_metadata_tracks_license_notices():
    readme = _read("README.md")
    pyproject = _read("pyproject.toml")
    installer = _read("installer/JellyRip.iss")
    notices = _read("THIRD_PARTY_NOTICES.md")

    assert 'license = { text = "GPL-3.0-only" }' in pyproject
    assert "GNU General Public License v3 (GPLv3)" in pyproject
    assert "THIRD_PARTY_NOTICES.md" in readme
    assert "2026-04-01-git-eedf8f0165-full_build-www.gyan.dev" in notices
    assert "https://github.com/FFmpeg/FFmpeg/commit/eedf8f0165" in notices
    assert 'AppName={#MyAppName}' in installer
    assert 'DefaultDirName={localappdata}\\Programs\\JellyRip AI' in installer
    assert 'OutputBaseFilename=JellyRipAIInstaller' in installer
    assert '#define MyAppBuildOutputDir "..\\dist\\ai"' in installer
    # One-DIR bundle: the installer packages the whole app folder; the
    # old per-file FFmpeg sources are gone, and stale staged copies
    # from pre-onedir installs are deleted on upgrade (the app prefers
    # an exe-dir ffmpeg over the bundled one).
    assert 'Source: "{#MyAppBuildOutputDir}\\JellyRipAI\\*"' in installer
    assert "recursesubdirs" in installer
    assert "[InstallDelete]" in installer
    assert 'Name: "{app}\\ffmpeg.exe"' in installer
    assert '_internal\\ffprobe.exe' in installer
    assert 'Source: "..\\LICENSE"' in installer
    assert 'Source: "..\\THIRD_PARTY_NOTICES.md"' in installer


def test_spec_bundles_ffmpeg_intentionally():
    spec = _read("JellyRip.spec")

    # One-DIR bundle: EXE excludes binaries; COLLECT assembles the
    # app folder.  No runtime extraction (the onefile-only
    # runtime_tmpdir knob must stay gone).
    assert "exclude_binaries=True" in spec
    assert "COLLECT(" in spec
    assert "runtime_tmpdir" not in spec

    assert "TCL_LIBRARY" in spec
    assert "TK_LIBRARY" in spec
    assert 'StringStruct("ProductVersion", version)' in spec
    assert 'StringStruct("FileVersion", version)' in spec
    assert "FFMPEG_FILENAMES" in spec
    assert "FFMPEG_NOTICE_FILENAMES" in spec
    assert "binaries=FFMPEG_BINARIES" in spec
    assert "*FFMPEG_NOTICE_DATAS" in spec
    assert "THIRD_PARTY_NOTICES.md" in spec
    assert '"anthropic"' in spec
    assert 'APP_EXE_BASENAME = "JellyRipAI"' in spec
    assert 'name=APP_EXE_BASENAME' in spec
    assert 'StringStruct("OriginalFilename", APP_EXE_NAME)' in spec
    assert "ffmpeg.exe" in spec.lower()
    assert "ffprobe.exe" in spec.lower()
    # ffplay is referenced only in the drop-note comment, never bundled.
    assert '"ffplay.exe"' not in spec
    assert 'PREFERRED_FFMPEG_ROOT = Path.home() / "Desktop" / "ffmpeg"' in spec
    assert "C:/Users/" not in spec
    assert "Desktop/ffmpeg" not in spec


def test_release_binaries_are_not_tracked():
    git_exe = shutil.which("git")
    if not git_exe:
        return

    for path in ("JellyRip.exe", "JellyRipAI.exe"):
        result = subprocess.run(
            [git_exe, "ls-files", "--error-unmatch", path],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

        assert result.returncode != 0, f"{path} should not be tracked in git"
