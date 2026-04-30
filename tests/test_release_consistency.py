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
    assert "git switch --track origin/ai" in readme
    assert "JellyRipAI.exe" in readme
    assert "JellyRipAIInstaller.exe" in readme
    assert "dist/ai/JellyRipAI.exe" in readme
    assert "dist\\ai\\FFmpeg-LICENSE.txt" in readme
    assert "%USERPROFILE%\\Desktop\\ffmpeg" in readme


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
    assert "tools\\stage_ffmpeg_bundle.ps1" in release_script
    assert 'set "ARTIFACT_DIR=dist\\ai"' in release_script
    assert 'set "BUILD_DIR=build\\ai"' in release_script
    assert 'type nul > "%ARTIFACT_DIR%\\.gitkeep"' in release_script
    assert 'LICENSE THIRD_PARTY_NOTICES.md "%ARTIFACT_DIR%\\FFmpeg-LICENSE.txt" "%ARTIFACT_DIR%\\FFmpeg-README.txt"' in release_script
    assert '"%ARTIFACT_DIR%\\ffmpeg.exe" "%ARTIFACT_DIR%\\ffprobe.exe" "%ARTIFACT_DIR%\\ffplay.exe"' in release_script
    assert 'set "RELEASE_BRANCH=ai"' in release_script
    assert 'set "RELEASE_TAG=ai-v%VERSION%"' in release_script
    assert "%ARTIFACT_DIR%\\JellyRipAI.exe" in release_script
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
    assert 'Source: "{#MyAppBuildOutputDir}\\ffmpeg.exe"' in installer
    assert 'Source: "{#MyAppBuildOutputDir}\\ffprobe.exe"' in installer
    assert 'Source: "{#MyAppBuildOutputDir}\\ffplay.exe"' in installer
    assert 'Source: "{#MyAppBuildOutputDir}\\JellyRipAI.exe"' in installer
    assert 'Source: "..\\LICENSE"' in installer
    assert 'Source: "..\\THIRD_PARTY_NOTICES.md"' in installer
    assert 'Source: "{#MyAppBuildOutputDir}\\FFmpeg-LICENSE.txt"' in installer
    assert 'Source: "{#MyAppBuildOutputDir}\\FFmpeg-README.txt"' in installer


def test_spec_bundles_ffmpeg_intentionally():
    spec = _read("JellyRip.spec")
    bundle_stager = _read("tools/stage_ffmpeg_bundle.ps1")

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
    assert "ffplay.exe" in spec.lower()
    assert 'PREFERRED_FFMPEG_ROOT = Path.home() / "Desktop" / "ffmpeg"' in spec
    assert '$preferredDesktopRoot = Join-Path $HOME "Desktop\\ffmpeg"' in bundle_stager
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
