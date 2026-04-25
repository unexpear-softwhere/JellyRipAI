"""Trusted Windows system executable helpers."""

from __future__ import annotations

import ctypes
import os
import sys


def _get_windows_directory(api_name: str) -> str:
    if sys.platform != "win32":
        return ""

    kernel32 = getattr(ctypes, "windll", None)
    if kernel32 is None:
        return ""

    func = getattr(kernel32.kernel32, api_name, None)
    if func is None:
        return ""

    size = 260
    while size <= 32768:
        buffer = ctypes.create_unicode_buffer(size)
        length = int(func(buffer, size))
        if 0 < length < size:
            return os.path.normpath(buffer.value)
        if length == 0:
            return ""
        size = length + 1
    return ""


def get_windows_system_directory() -> str:
    if sys.platform != "win32":
        return ""

    path = _get_windows_directory("GetSystemDirectoryW")
    if path:
        return path
    return os.path.normpath(r"C:\Windows\System32")


def get_windows_root_directory() -> str:
    if sys.platform != "win32":
        return ""

    path = _get_windows_directory("GetSystemWindowsDirectoryW")
    if path:
        return path

    fallback = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if fallback:
        return os.path.normpath(fallback)
    return os.path.normpath(r"C:\Windows")


def get_powershell_executable() -> str:
    if sys.platform != "win32":
        return "powershell"

    system_dir = get_windows_system_directory()
    candidate = os.path.join(
        system_dir,
        "WindowsPowerShell",
        "v1.0",
        "powershell.exe",
    )
    if os.path.isfile(candidate):
        return os.path.normpath(candidate)
    return os.path.normpath(
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    )


def get_explorer_executable() -> str:
    if sys.platform != "win32":
        return "explorer"

    windows_dir = get_windows_root_directory()
    candidate = os.path.join(windows_dir, "explorer.exe")
    if os.path.isfile(candidate):
        return os.path.normpath(candidate)
    return os.path.normpath(r"C:\Windows\explorer.exe")


__all__ = [
    "get_explorer_executable",
    "get_powershell_executable",
    "get_windows_root_directory",
    "get_windows_system_directory",
]
