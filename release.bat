@echo off
REM ============================================================
REM  JellyRip AI release pipeline — enforces correct order:
REM    git-check -> tests -> build -> verify -> push -> publish
REM
REM  Usage:  release.bat 1.0.17
REM ============================================================
setlocal enabledelayedexpansion

set VERSION=%~1
if "%VERSION%"=="" (
    echo Usage: release.bat ^<version^>
    echo Example: release.bat 1.0.17
    exit /b 1
)
set "RELEASE_BRANCH=ai"
set "RELEASE_TAG=ai-v%VERSION%"

set "PYTHON_EXE=.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
set ISCC_EXE=C:\Program Files (x86)\Inno Setup 6\ISCC.exe
if not exist "%ISCC_EXE%" set ISCC_EXE=C:\Program Files\Inno Setup 6\ISCC.exe
if not exist "%ISCC_EXE%" set ISCC_EXE=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe

echo.
echo ========================================
echo  JellyRip AI Release Pipeline v%VERSION%
echo ========================================
echo.

REM ---- Step 1: Verify git state ----
echo [1/8] Verifying git state...
for /f %%I in ('git rev-parse --abbrev-ref HEAD 2^>nul') do set CURRENT_BRANCH=%%I
if errorlevel 1 (
    echo ABORT: Could not determine current git branch.
    exit /b 1
)
if /I not "%CURRENT_BRANCH%"=="%RELEASE_BRANCH%" (
    echo ABORT: Releases must be created from branch %RELEASE_BRANCH%. Current branch: %CURRENT_BRANCH%
    exit /b 1
)
for /f "delims=" %%I in ('git status --porcelain --untracked-files=normal') do (
    echo ABORT: Working tree is not clean. Commit, stash, or ignore pending files before releasing.
    echo        First pending entry: %%I
    exit /b 1
)
echo       Git branch and working tree look clean.
echo.

REM ---- Step 2: Run tests ----
echo [2/8] Running tests...
%PYTHON_EXE% -m pytest tests/ -q --tb=short
if errorlevel 1 (
    echo.
    echo ABORT: Tests failed. Fix before releasing.
    exit /b 1
)
echo       Tests passed.
echo.

REM ---- Step 3: Check version consistency ----
echo [3/8] Checking version consistency...

findstr /C:"__version__ = \"%VERSION%\"" shared\runtime.py >nul 2>&1
if errorlevel 1 (
    echo ABORT: shared\runtime.py does not contain version %VERSION%
    echo        Update __version__ before running this script.
    exit /b 1
)

findstr /C:"version = \"%VERSION%\"" pyproject.toml >nul 2>&1
if errorlevel 1 (
    echo ABORT: pyproject.toml does not contain version %VERSION%
    exit /b 1
)
findstr /C:"GPL-3.0-only" pyproject.toml >nul 2>&1
if errorlevel 1 (
    echo ABORT: pyproject.toml does not declare the GPLv3 project license.
    exit /b 1
)

findstr /C:"#define MyAppVersion \"%VERSION%\"" installer\JellyRip.iss >nul 2>&1
if errorlevel 1 (
    echo ABORT: installer\JellyRip.iss does not contain version %VERSION%
    exit /b 1
)

findstr /C:"[%VERSION%]" CHANGELOG.md >nul 2>&1
if errorlevel 1 (
    echo ABORT: CHANGELOG.md has no entry for %VERSION%
    exit /b 1
)

findstr /C:"v%VERSION%" release_notes.txt >nul 2>&1
if errorlevel 1 (
    echo ABORT: release_notes.txt does not mention v%VERSION%
    exit /b 1
)
if not exist LICENSE (
    echo ABORT: LICENSE is missing.
    exit /b 1
)
if not exist THIRD_PARTY_NOTICES.md (
    echo ABORT: THIRD_PARTY_NOTICES.md is missing.
    exit /b 1
)
echo       All files show v%VERSION%.
echo.

REM ---- Step 4: Build exe ----
echo [4/8] Building JellyRipAI.exe...
if exist dist rmdir /s /q dist >nul 2>&1
if exist build rmdir /s /q build >nul 2>&1
%PYTHON_EXE% -m PyInstaller JellyRip.spec >nul 2>&1
if errorlevel 1 (
    echo ABORT: PyInstaller build failed.
    exit /b 1
)
if not exist dist\JellyRipAI.exe (
    echo ABORT: dist\JellyRipAI.exe not found after build.
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File tools\stage_ffmpeg_bundle.ps1 >nul 2>&1
if errorlevel 1 (
    echo ABORT: FFmpeg bundle staging failed.
    exit /b 1
)
for %%F in (dist\ffmpeg.exe dist\ffprobe.exe dist\ffplay.exe) do (
    if not exist %%F (
        echo ABORT: %%F is missing; JellyRip AI releases intentionally bundle FFmpeg.
        exit /b 1
    )
)
for %%F in (dist\FFmpeg-LICENSE.txt dist\FFmpeg-README.txt) do (
    if not exist %%F (
        echo ABORT: %%F is missing; FFmpeg notices must ship with bundled FFmpeg.
        exit /b 1
    )
)
echo       dist\JellyRipAI.exe built.
echo.

REM ---- Step 5: Build installer ----
echo [5/8] Building JellyRipAIInstaller.exe...
if not exist "%ISCC_EXE%" (
    echo ABORT: Inno Setup compiler not found.
    exit /b 1
)
"%ISCC_EXE%" installer\JellyRip.iss >nul 2>&1
if errorlevel 1 (
    echo ABORT: Installer build failed.
    exit /b 1
)
if not exist dist\JellyRipAIInstaller.exe (
    echo ABORT: dist\JellyRipAIInstaller.exe not found after build.
    exit /b 1
)
echo       dist\JellyRipAIInstaller.exe built.
echo.

REM ---- Step 6: Verify build outputs ----
echo [6/8] Verifying build outputs...
for %%F in (dist\JellyRipAI.exe) do (
    if %%~zF LSS 1000000 (
        echo ABORT: JellyRipAI.exe is suspiciously small (%%~zF bytes).
        exit /b 1
    )
)
for %%F in (dist\JellyRipAIInstaller.exe) do (
    if %%~zF LSS 1000000 (
        echo ABORT: JellyRipAIInstaller.exe is suspiciously small (%%~zF bytes).
        exit /b 1
    )
)
echo       Both executables verified.
echo.

REM ---- Step 7: Push code ----
echo [7/8] Pushing to GitHub...
git push origin "%RELEASE_BRANCH%"
if errorlevel 1 (
    echo ABORT: git push failed.
    exit /b 1
)
echo       Code pushed.
echo.

REM ---- Step 8: Create release with assets ----
echo [8/8] Publishing release %RELEASE_TAG% with assets...
gh release create %RELEASE_TAG% dist\JellyRipAI.exe dist\JellyRipAIInstaller.exe LICENSE THIRD_PARTY_NOTICES.md dist\FFmpeg-LICENSE.txt dist\FFmpeg-README.txt --title "JellyRip AI v%VERSION% (UNSTABLE)" --notes-file release_notes.txt --prerelease
if errorlevel 1 (
    echo ABORT: gh release create failed.
    exit /b 1
)
echo.
echo ========================================
echo  Release %RELEASE_TAG% published!
echo ========================================
echo.
echo  Assets:
echo    - JellyRipAI.exe
echo    - JellyRipAIInstaller.exe
echo    - LICENSE
echo    - THIRD_PARTY_NOTICES.md
echo    - FFmpeg-LICENSE.txt
echo    - FFmpeg-README.txt
echo.
echo  Verify: https://github.com/unexpear/JellyRip/releases/tag/%RELEASE_TAG%
echo.
