@echo off
REM Build JellyRipAI.exe from source using PyInstaller

set "PYTHON_EXE=.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

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

REM Clean previous builds
if exist dist rmdir /s /q dist >nul 2>&1
if exist build rmdir /s /q build >nul 2>&1

REM Build the exe
%PYTHON_EXE% -m PyInstaller JellyRip.spec

if errorlevel 1 (
    echo Build failed!
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File tools\stage_ffmpeg_bundle.ps1
if errorlevel 1 (
    echo FFmpeg bundle staging failed!
    pause
    exit /b 1
)

echo.
echo Build complete! Output:
echo   dist\JellyRipAI.exe
echo   dist\ffmpeg.exe
echo   dist\ffprobe.exe
echo   dist\ffplay.exe
echo   dist\FFmpeg-LICENSE.txt
echo   dist\FFmpeg-README.txt
pause
