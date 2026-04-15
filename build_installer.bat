@echo off
REM Build JellyRipAI.exe and package JellyRipAIInstaller.exe with Inno Setup

set "PYTHON_EXE=.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
set ISCC_EXE=C:\Program Files (x86)\Inno Setup 6\ISCC.exe
if not exist "%ISCC_EXE%" set ISCC_EXE=C:\Program Files\Inno Setup 6\ISCC.exe
if not exist "%ISCC_EXE%" set ISCC_EXE=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe

if exist dist rmdir /s /q dist >nul 2>&1
if exist build rmdir /s /q build >nul 2>&1

echo Building JellyRipAI.exe...
%PYTHON_EXE% -m PyInstaller JellyRip.spec
if errorlevel 1 (
    echo EXE build failed.
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File tools\stage_ffmpeg_bundle.ps1
if errorlevel 1 (
    echo FFmpeg bundle staging failed.
    exit /b 1
)

if not exist "%ISCC_EXE%" (
    echo Inno Setup compiler not found.
    echo Install Inno Setup 6, then run this script again.
    exit /b 1
)

echo Building JellyRipAIInstaller.exe...
"%ISCC_EXE%" installer\JellyRip.iss
if errorlevel 1 (
    echo Installer build failed.
    exit /b 1
)

echo.
echo Build complete:
echo   dist\JellyRipAI.exe
echo   dist\JellyRipAIInstaller.exe
echo   dist\ffmpeg.exe
echo   dist\ffprobe.exe
echo   dist\ffplay.exe
echo   dist\FFmpeg-LICENSE.txt
echo   dist\FFmpeg-README.txt
