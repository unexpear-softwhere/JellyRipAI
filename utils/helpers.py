"""Helper utilities implementation."""

import csv
import os
import platform
import re
import subprocess
import sys as _sys
from dataclasses import dataclass
from datetime import datetime
from os import PathLike

_WINDOWS_RESERVED = re.compile(
    r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)',
    re.IGNORECASE,
)


def clean_name(name: object) -> str:
    cleaned = re.sub(r'[\x00-\x1f<>:"/\\|?*]', '', str(name))
    cleaned = cleaned.strip().rstrip(". ")
    if not cleaned:
        return "Title_Unknown"
    # Append underscore to Windows reserved device names.
    stem, _, ext = cleaned.partition(".")
    if _WINDOWS_RESERVED.match(stem):
        cleaned = stem + "_" + ("." + ext if ext else "")
    return cleaned


def make_rip_folder_name() -> str:
    return datetime.now().strftime("Disc_%Y-%m-%d_%H-%M-%S")


def make_temp_title() -> str:
    return f"TEMP_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"


def is_network_path(path: str | PathLike[str] | None) -> bool:
    """Best-effort check for UNC or mapped/network drive paths.

    On Windows: checks for UNC paths (\\\\server\\share) and DRIVE_REMOTE via GetDriveType.
    On Linux/macOS: checks /proc/mounts or mount output for network filesystem types (nfs, cifs, smb).
    Note: /mnt/ on WSL contains local mounts, not network paths; this function checks actual mount types.
    """
    try:
        if not path:
            return False
        p: str = os.path.normpath(os.fspath(path))
        if p.startswith("\\\\"):
            return True
        if platform.system() == "Windows":
            drive, _ = os.path.splitdrive(p)
            if drive:
                root = drive + "\\"
                try:
                    import ctypes
                    drive_type = ctypes.windll.kernel32.GetDriveTypeW(root)
                    # DRIVE_REMOTE = 4
                    return int(drive_type) == 4
                except Exception:
                    return False
        else:
            # Non-Windows: check /proc/mounts or mount output for network filesystem types.
            # This properly handles WSL where /mnt/* are local mounts, not network paths.
            try:
                # Try /proc/mounts first (Linux)
                if os.path.exists("/proc/mounts"):
                    with open("/proc/mounts", "r") as f:
                        for line in f:
                            parts = line.split()
                            if len(parts) >= 3:
                                mount_point = parts[1]
                                fs_type = parts[2]
                                # Check if path starts with this mount point and fs_type is network
                                if p.startswith(mount_point) and fs_type in ("nfs", "nfs4", "cifs", "smb", "smbfs"):
                                    return True
            except Exception:
                pass
            # Fallback: if /proc/mounts is unavailable, try 'mount' command
            try:
                result = subprocess.run(
                    ["mount"], capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=2,
                )
                for line in result.stdout.split("\n"):
                    if any(fs in line.lower() for fs in ("nfs", "cifs", "smb")):
                        # Try to extract mount point and see if path is under it
                        parts = line.split()
                        for i, part in enumerate(parts):
                            if part == "on" and i + 1 < len(parts):
                                mount_point = parts[i + 1]
                                if p.startswith(mount_point):
                                    return True
            except Exception:
                pass
        return False
    except Exception:
        return False


@dataclass(frozen=True)
class MakeMKVDrive:
    index: int
    visible: int
    enabled: int
    flags: int
    drive_name: str
    disc_name: str
    device_path: str = ""

    @property
    def usability_state(self) -> str:
        return f"visible={self.visible}, enabled={self.enabled}, flags={self.flags}"

    @property
    def is_accessible(self) -> bool:
        return self.visible > 0 and self.enabled > 0


def make_default_drive(index: int = 0) -> MakeMKVDrive:
    return MakeMKVDrive(
        index=index,
        visible=1,
        enabled=1,
        flags=0,
        drive_name="Default Drive",
        disc_name="",
        device_path=f"disc:{index}",
    )


def parse_makemkv_drive_row(line: str) -> MakeMKVDrive | None:
    """Parse a MakeMKV automation-mode DRV row.

    Officially documented fields are:
    ``DRV:index,visible,enabled,flags,drive name,disc name``.
    Some Windows builds also emit a trailing device path / drive letter.
    """
    if not isinstance(line, str) or not line.startswith("DRV:"):
        return None
    try:
        fields = next(csv.reader([line[4:]], escapechar="\\"))
    except Exception:
        return None
    if len(fields) < 6:
        return None
    try:
        index = int(fields[0])
        visible = int(fields[1])
        enabled = int(fields[2])
        flags = int(fields[3])
    except (TypeError, ValueError):
        return None
    device_path = fields[6].strip() if len(fields) >= 7 else ""
    return MakeMKVDrive(
        index=index,
        visible=visible,
        enabled=enabled,
        flags=flags,
        drive_name=fields[4].strip(),
        disc_name=fields[5].strip(),
        device_path=device_path,
    )


def format_makemkv_drive_label(drive: MakeMKVDrive) -> str:
    name = drive.drive_name or f"Drive {drive.index}"
    label = f"Drive {drive.index}: {name}"
    if drive.device_path:
        label += f" [{drive.device_path}]"
    if drive.disc_name:
        label += f" | Disc: {drive.disc_name}"
    return label


def get_available_drives(
    makemkvcon_path: str,
    *,
    allow_fallback: bool = True,
) -> list[MakeMKVDrive]:
    """Query MakeMKV for available optical drives via disc:9999."""
    drives: list[MakeMKVDrive] = []
    try:
        if _sys.platform == "win32":
            proc = subprocess.Popen(
                [makemkvcon_path, "-r", "--cache=1", "info", "disc:9999"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                # makemkvcon emits UTF-8; the text=True default decodes
                # with the locale code page and mojibakes non-ASCII
                # disc/drive names (or raises mid-read).
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=0x08000000,
            )
        else:
            proc = subprocess.Popen(
                [makemkvcon_path, "-r", "--cache=1", "info", "disc:9999"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                # makemkvcon emits UTF-8; the text=True default decodes
                # with the locale code page and mojibakes non-ASCII
                # disc/drive names (or raises mid-read).
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        try:
            if proc.stdout is None:
                return [make_default_drive()] if allow_fallback else []
            for line in iter(proc.stdout.readline, ""):
                line = line.strip()
                drive = parse_makemkv_drive_row(line)
                if drive is not None:
                    drives.append(drive)
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
    except Exception:
        pass
    if not drives and allow_fallback:
        drives = [make_default_drive()]
    return drives

__all__ = [
    "MakeMKVDrive",
    "clean_name",
    "format_makemkv_drive_label",
    "get_available_drives",
    "is_network_path",
    "make_default_drive",
    "make_rip_folder_name",
    "make_temp_title",
    "parse_makemkv_drive_row",
]
