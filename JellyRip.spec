# -*- mode: python ; coding: utf-8 -*-

import os
import re
import sys
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VSVersionInfo,
    VarFileInfo,
    VarStruct,
)


# PyInstaller executes the spec without defining __file__, so resolve
# project-relative paths from the working directory used to launch it.
PROJECT_ROOT = Path.cwd()
APP_DISPLAY_NAME = "JellyRip AI"
APP_EXE_BASENAME = "JellyRipAI"
APP_EXE_NAME = f"{APP_EXE_BASENAME}.exe"
FFMPEG_ENV_VARS = ("JELLYRIP_FFMPEG_DIR", "FFMPEG_DIR")
FFMPEG_FILENAMES = ("ffmpeg.exe", "ffprobe.exe", "ffplay.exe")
FFMPEG_NOTICE_FILENAMES = ("LICENSE", "README.txt")
PREFERRED_FFMPEG_ROOT = Path.home() / "Desktop" / "ffmpeg"


def _configure_tcl_tk_environment() -> None:
    base_prefix = Path(getattr(sys, "base_prefix", "") or "")
    if not base_prefix:
        return

    tcl_root = base_prefix / "tcl"
    tcl_library = tcl_root / "tcl8.6"
    tk_library = tcl_root / "tk8.6"

    if not os.environ.get("TCL_LIBRARY") and tcl_library.is_dir():
        os.environ["TCL_LIBRARY"] = str(tcl_library)
    if not os.environ.get("TK_LIBRARY") and tk_library.is_dir():
        os.environ["TK_LIBRARY"] = str(tk_library)


_configure_tcl_tk_environment()


def _collect_tree(root_dir: Path, dest_root: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    if not root_dir.is_dir():
        return entries

    for path in root_dir.rglob("*"):
        if not path.is_file():
            continue
        relative_parent = path.parent.relative_to(root_dir)
        destination = str(Path(dest_root) / relative_parent).replace("\\", "/")
        entries.append((str(path), destination))
    return entries


def _read_app_version() -> str:
    runtime_path = PROJECT_ROOT / "shared" / "runtime.py"
    runtime_text = runtime_path.read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', runtime_text)
    if not match:
        raise SystemExit("Could not read __version__ from shared/runtime.py")
    return match.group(1)


def _version_quad(version: str) -> tuple[int, int, int, int]:
    parts: list[int] = []
    for piece in version.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])


def _build_version_info(version: str) -> VSVersionInfo:
    version_quad = _version_quad(version)
    return VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=version_quad,
            prodvers=version_quad,
            mask=0x3F,
            flags=0x0,
            OS=0x40004,
            fileType=0x1,
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo(
                [
                    StringTable(
                        "040904B0",
                        [
                            StringStruct("CompanyName", "unexpear"),
                            StringStruct("FileDescription", APP_DISPLAY_NAME),
                            StringStruct("FileVersion", version),
                            StringStruct("InternalName", APP_EXE_BASENAME),
                            StringStruct("OriginalFilename", APP_EXE_NAME),
                            StringStruct("ProductName", APP_DISPLAY_NAME),
                            StringStruct("ProductVersion", version),
                        ],
                    )
                ]
            ),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )


def _add_search_root(roots: list[Path], root: Path) -> None:
    roots.append(root)
    if root.name.lower() == "bin":
        roots.append(root.parent)


def _search_roots() -> list[Path]:
    roots: list[Path] = []
    _add_search_root(roots, PREFERRED_FFMPEG_ROOT)
    for env_name in FFMPEG_ENV_VARS:
        raw = os.environ.get(env_name, "").strip()
        if raw:
            _add_search_root(roots, Path(raw).expanduser())

    for root in (
        PROJECT_ROOT / "ffmpeg",
        PROJECT_ROOT / "ffmpeg" / "bin",
        PROJECT_ROOT.parent / "ffmpeg",
        PROJECT_ROOT.parent / "ffmpeg" / "bin",
    ):
        _add_search_root(roots, root)
    return roots


def _find_bundle_file(filename: str) -> str:
    seen: set[str] = set()
    for root in _search_roots():
        try:
            normalized_root = root.resolve()
        except OSError:
            normalized_root = root

        root_key = os.path.normcase(str(normalized_root))
        if root_key in seen or not normalized_root.exists():
            continue
        seen.add(root_key)

        direct_candidates = [
            normalized_root / filename,
            normalized_root / "bin" / filename,
        ]
        for candidate in direct_candidates:
            if candidate.is_file():
                return str(candidate)

        for candidate in normalized_root.rglob(filename):
            if candidate.is_file():
                return str(candidate)

    search_hint = (
        f"Could not find {filename} from the FFmpeg build required by JellyRip.spec.\n"
        "Place the Gyan FFmpeg full build under %USERPROFILE%\\Desktop\\ffmpeg, "
        "set JELLYRIP_FFMPEG_DIR (or FFMPEG_DIR), or place the extracted build "
        "under .\\ffmpeg\\ or ..\\ffmpeg\\."
    )
    raise SystemExit(search_hint)


APP_VERSION = _read_app_version()
APP_VERSION_INFO = _build_version_info(APP_VERSION)
PYTHON_BASE = Path(getattr(sys, "base_prefix", "") or "")
PYTHON_DLLS = PYTHON_BASE / "DLLs"
PYTHON_TCL_ROOT = PYTHON_BASE / "tcl"
TCL_BUILD_DIR = PYTHON_TCL_ROOT / "tcl8.6"
TK_BUILD_DIR = PYTHON_TCL_ROOT / "tk8.6"
TK_DATAS = _collect_tree(TCL_BUILD_DIR, "_tcl_data")
TK_DATAS += _collect_tree(TK_BUILD_DIR, "_tk_data")
TK_BINARIES = []
for dll_name in ("_tkinter.pyd", "tcl86t.dll", "tk86t.dll"):
    dll_path = PYTHON_DLLS / dll_name
    if dll_path.is_file():
        TK_BINARIES.append((str(dll_path), "."))
FFMPEG_BINARIES = [(_find_bundle_file(name), ".") for name in FFMPEG_FILENAMES]
FFMPEG_BINARIES = [*TK_BINARIES, *FFMPEG_BINARIES]
FFMPEG_NOTICE_DATAS = [
    (_find_bundle_file(name), "licenses/ffmpeg") for name in FFMPEG_NOTICE_FILENAMES
]

a = Analysis(
    ["main.py"],
    pathex=[str(PROJECT_ROOT)],
    binaries=FFMPEG_BINARIES,
    datas=[
        *TK_DATAS,
        ("LICENSE", "."),
        ("THIRD_PARTY_NOTICES.md", "."),
        *FFMPEG_NOTICE_DATAS,
    ],
    hiddenimports=[
        "tkinter",
        "tkinter.ttk",
        "tkinter.messagebox",
        "tkinter.filedialog",
        "tkinter.simpledialog",
        "_tkinter",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(PROJECT_ROOT / "pyinstaller_tk_runtime_hook.py")],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_EXE_BASENAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=APP_VERSION_INFO,
)
