@echo off
REM Build JellyRipAI.exe from source using PyInstaller

set "PYTHON_EXE=.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
set "ARTIFACT_DIR=dist\ai"
set "BUILD_DIR=build\ai"

echo Building JellyRipAI.exe...
echo.

REM Check if PyInstaller is installed
%PYTHON_EXE% -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo Error: PyInstaller not installed
    echo Run: pip install pyinstaller
    pause
    exit /b 1
)

REM Clean previous AI build outputs
if exist "%ARTIFACT_DIR%" rmdir /s /q "%ARTIFACT_DIR%" >nul 2>&1
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%" >nul 2>&1
if not exist "%ARTIFACT_DIR%" mkdir "%ARTIFACT_DIR%"
type nul > "%ARTIFACT_DIR%\.gitkeep"

REM Build the exe
%PYTHON_EXE% -m PyInstaller --distpath "%ARTIFACT_DIR%" --workpath "%BUILD_DIR%" JellyRip.spec

if errorlevel 1 (
    echo Build failed!
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File tools\stage_ffmpeg_bundle.ps1 -DistDir "%ARTIFACT_DIR%"
if errorlevel 1 (
    echo FFmpeg bundle staging failed!
    pause
    exit /b 1
)

echo.
echo Build complete! Output:
echo   %ARTIFACT_DIR%\JellyRipAI.exe
echo   %ARTIFACT_DIR%\ffmpeg.exe
echo   %ARTIFACT_DIR%\ffprobe.exe
echo   %ARTIFACT_DIR%\FFmpeg-LICENSE.txt
echo   %ARTIFACT_DIR%\FFmpeg-README.txt
pause
