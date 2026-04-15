"""Configuration implementation for package-style imports."""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from os import PathLike
from typing import TypeAlias, cast

from shared.runtime import CONFIG_FILE, DEFAULTS, get_config_dir

ConfigDict: TypeAlias = dict[str, object]
ToolValidationResult: TypeAlias = tuple[bool, str]
ToolValidator: TypeAlias = Callable[[str], ToolValidationResult]
_MIN_FFMPEG_LIBAVCODEC_MAJOR = 58


@dataclass(frozen=True)
class StartupConfigIssue:
    code: str
    message: str
    should_open_settings: bool = False


@dataclass(frozen=True)
class StartupConfigResult:
    config: ConfigDict
    issues: tuple[StartupConfigIssue, ...] = ()

    @property
    def open_settings(self) -> bool:
        return any(issue.should_open_settings for issue in self.issues)


@dataclass(frozen=True)
class AppConfig:
    source: str
    output: str
    quality: str

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("source is required")
        if not self.output:
            raise ValueError("output is required")


def _normalize_pathlike(path: str | PathLike[str] | None) -> str:
    if not path:
        return ""
    return os.path.normpath(os.fspath(path))


def _is_file(path: str | PathLike[str] | None) -> bool:
    normalized = _normalize_pathlike(path)
    return bool(normalized) and os.path.isfile(normalized)


def _run_probe_output(
    executable: str | PathLike[str] | None,
    args: Iterable[str],
) -> tuple[bool, str, str]:
    exe = _normalize_pathlike(executable)
    if not _is_file(exe):
        return False, "Path does not exist", ""

    command = [exe, *list(args)]
    try:
        if platform.system() == "Windows":
            proc = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                creationflags=0x08000000,
            )
        else:
            proc = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
            )
    except Exception as exc:
        return False, str(exc), ""

    output = b"\n".join(
        part for part in (proc.stdout, proc.stderr) if isinstance(part, bytes)
    ).decode("utf-8", errors="replace")

    if proc.returncode != 0:
        return False, f"Exited with code {proc.returncode}", output
    return True, "", output


def _run_probe(executable: str | PathLike[str] | None, args: Iterable[str]) -> ToolValidationResult:
    ok, reason, _output = _run_probe_output(executable, args)
    return ok, reason


def _bundled_binary_candidates(filename: str) -> list[str]:
    candidates: list[str] = []
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(str(getattr(sys, "executable", "") or ""))
        if exe_dir:
            candidates.append(os.path.join(exe_dir, filename))
        meipass = str(getattr(sys, "_MEIPASS", "") or "")
        if meipass:
            candidates.append(os.path.join(meipass, filename))

    app_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(app_dir, filename))
    candidates.append(os.path.join(app_dir, "dist", filename))

    resolved: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = _normalize_pathlike(candidate)
        if not normalized:
            continue
        key = os.path.normcase(normalized)
        if key in seen:
            continue
        seen.add(key)
        resolved.append(normalized)
    return resolved


def auto_locate_ffmpeg() -> str:
    """Find ffmpeg using common paths and PATH."""
    for path in _bundled_binary_candidates("ffmpeg.exe" if os.name == "nt" else "ffmpeg"):
        if os.path.isfile(path):
            return path
    candidates = [
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\ffmpeg\ffmpeg.exe",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return shutil.which("ffmpeg") or ""


def auto_locate_handbrake() -> str:
    """Find HandBrakeCLI using common paths and PATH."""
    for path in _bundled_binary_candidates(
        "HandBrakeCLI.exe" if os.name == "nt" else "HandBrakeCLI"
    ):
        if os.path.isfile(path):
            return path
    candidates = [
        r"C:\Program Files\HandBrake\HandBrakeCLI.exe",
        r"C:\Program Files (x86)\HandBrake\HandBrakeCLI.exe",
        r"C:\HandBrake\HandBrakeCLI.exe",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return shutil.which("HandBrakeCLI") or ""


def handbrake_gui_installed() -> bool:
    """Return True when the HandBrake *GUI* is installed but HandBrakeCLI is absent.

    HandBrakeCLI.exe is a separate download from the HandBrake GUI installer
    (handbrake.fr → Downloads → CLI).  This helper lets callers show a helpful
    hint rather than a generic "not found" message.
    """
    gui_dirs = [
        r"C:\Program Files\HandBrake",
        r"C:\Program Files (x86)\HandBrake",
    ]
    for d in gui_dirs:
        if os.path.isfile(os.path.join(d, "HandBrake.exe")):
            return True
    return False


def _ffmpeg_libavcodec_major(version_output: str) -> int | None:
    match = re.search(r"libavcodec\s+(\d+)\.", version_output)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def validate_ffmpeg(path: str | PathLike[str] | None) -> ToolValidationResult:
    ok, reason, output = _run_probe_output(path, ["-version"])
    if not ok:
        return ok, reason

    libavcodec_major = _ffmpeg_libavcodec_major(output)
    if libavcodec_major is None:
        return False, "Could not read FFmpeg libavcodec version"
    if libavcodec_major < _MIN_FFMPEG_LIBAVCODEC_MAJOR:
        return (
            False,
            "FFmpeg is too old for JellyRip transcode commands "
            f"(libavcodec {libavcodec_major}; need "
            f"{_MIN_FFMPEG_LIBAVCODEC_MAJOR}+ / FFmpeg 4.0+).",
        )
    return True, ""


def validate_handbrake(path: str | PathLike[str] | None) -> ToolValidationResult:
    return _run_probe(path, ["--version"])


def _load_raw_config() -> tuple[ConfigDict, list[StartupConfigIssue]]:
    issues: list[StartupConfigIssue] = []
    if not os.path.exists(CONFIG_FILE):
        return {}, issues

    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            loaded = json.load(f)
    except json.JSONDecodeError as exc:
        logging.warning("Config file is corrupt, resetting to defaults: %s", exc)
        issues.append(
            StartupConfigIssue(
                code="config_malformed",
                message=(
                    "Config file was unreadable. JellyRip started with defaults so "
                    "you can review Settings."
                ),
                should_open_settings=True,
            )
        )
        return {}, issues
    except Exception as exc:
        logging.warning("Could not read config, using defaults: %s", exc)
        issues.append(
            StartupConfigIssue(
                code="config_unreadable",
                message=(
                    "Config file could not be read. JellyRip started with defaults "
                    "so you can review Settings."
                ),
                should_open_settings=True,
            )
        )
        return {}, issues

    if not isinstance(loaded, dict):
        issues.append(
            StartupConfigIssue(
                code="config_invalid_shape",
                message=(
                    "Config file had the wrong shape. JellyRip started with defaults "
                    "so you can review Settings."
                ),
                should_open_settings=True,
            )
        )
        return {}, issues

    return cast(ConfigDict, loaded), issues


def _merge_config(raw: Mapping[str, object]) -> ConfigDict:
    cfg: ConfigDict = dict(DEFAULTS)
    cfg.update(raw)
    theme_overrides = cfg.get("opt_theme_overrides", {})
    if isinstance(theme_overrides, Mapping):
        cfg["opt_theme_overrides"] = {
            str(key): value
            for key, value in theme_overrides.items()
        }
    else:
        cfg["opt_theme_overrides"] = {}

    if "opt_naming_mode" not in cfg and "opt_fallback_title_mode" in cfg:
        cfg["opt_naming_mode"] = cfg.get(
            "opt_fallback_title_mode",
            DEFAULTS["opt_naming_mode"],
        )
    cfg.pop("opt_fallback_title_mode", None)
    return cfg


def _required_blank_fields(cfg: Mapping[str, object]) -> list[str]:
    required = ("temp_folder", "tv_folder", "movies_folder")
    return [
        field
        for field in required
        if not str(cfg.get(field, "") or "").strip()
    ]


def _invalid_path_fields(cfg: Mapping[str, object]) -> list[str]:
    path_fields = (
        "makemkvcon_path",
        "ffprobe_path",
        "ffmpeg_path",
        "handbrake_path",
        "temp_folder",
        "tv_folder",
        "movies_folder",
    )
    return [
        field
        for field in path_fields
        if not isinstance(cfg.get(field, DEFAULTS.get(field, "")), str)
    ]


def load_config() -> ConfigDict:
    """Load config as the mutable mapping shape used by the GUI and controller."""
    raw, _issues = _load_raw_config()
    cfg = _merge_config(raw)

    missing_fields = _required_blank_fields(cfg)
    if missing_fields:
        field_list = ", ".join(missing_fields)
        raise ValueError(f"Missing required config field(s): {field_list}")

    return cfg


def load_startup_config() -> StartupConfigResult:
    """Load config for startup without preventing the app shell from opening."""
    raw, issues = _load_raw_config()
    cfg = _merge_config(raw)

    invalid_path_fields = _invalid_path_fields(cfg)
    if invalid_path_fields:
        for field in invalid_path_fields:
            cfg[field] = DEFAULTS[field]
        issues.append(
            StartupConfigIssue(
                code="config_invalid_path_values",
                message=(
                    "One or more path settings had invalid values and were reset "
                    "to defaults for this launch."
                ),
                should_open_settings=True,
            )
        )

    missing_fields = _required_blank_fields(cfg)
    if missing_fields:
        for field in missing_fields:
            cfg[field] = DEFAULTS[field]
        issues.append(
            StartupConfigIssue(
                code="config_missing_required_paths",
                message=(
                    "One or more library/temp folders were blank in Settings and were "
                    "reset to defaults for this launch."
                ),
                should_open_settings=True,
            )
        )

    return StartupConfigResult(config=cfg, issues=tuple(issues))


def save_config(cfg: Mapping[str, object] | AppConfig) -> None:
    payload = asdict(cfg) if isinstance(cfg, AppConfig) else dict(cfg)
    try:
        get_config_dir()
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, CONFIG_FILE)
    except Exception as exc:
        raise RuntimeError(f"Could not save config: {exc}") from exc


def validate_makemkvcon(path: str | PathLike[str] | None) -> ToolValidationResult:
    return _run_probe(path, ["-r", "--version"])


def _resolve_ffprobe_from_dir(dirpath: str | PathLike[str] | None) -> str | None:
    """Given a directory, look for ffprobe.exe inside it."""
    normalized = _normalize_pathlike(dirpath)
    if not normalized or not os.path.isdir(normalized):
        return None
    for subpath in ("ffprobe.exe", os.path.join("bin", "ffprobe.exe")):
        candidate = os.path.join(normalized, subpath)
        if os.path.isfile(candidate):
            return candidate
    return None


def validate_ffprobe(path: str | PathLike[str] | None) -> ToolValidationResult:
    """Validate ffprobe. Accepts a direct exe path or a folder containing it."""
    normalized = _normalize_pathlike(path)
    if os.path.isdir(normalized):
        resolved = _resolve_ffprobe_from_dir(normalized)
        if not resolved:
            return False, "No ffprobe.exe found in folder"
        return _run_probe(resolved, ["-version"])
    return _run_probe(normalized, ["-version"])


def should_keep_current_tool_path(
    current_path: str | PathLike[str] | None,
    candidate_path: str | PathLike[str] | None,
    validator: ToolValidator,
) -> bool:
    """Return True when current working path should be preserved."""
    current = _normalize_pathlike(current_path)
    candidate = _normalize_pathlike(candidate_path)

    if not current or candidate == current:
        return False

    current_ok, _ = validator(current)
    if not current_ok:
        return False

    candidate_ok, _ = validator(candidate)
    return not candidate_ok


def resolve_tool(
    configured_path: str | PathLike[str] | None,
    common_paths: Iterable[str],
    env_tool_name: str,
) -> str:
    """Resolve an executable path in order: config, common paths, then PATH."""
    configured = _normalize_pathlike(configured_path)
    if _is_file(configured):
        return configured
    for path in common_paths:
        if _is_file(path):
            return _normalize_pathlike(path)
    return shutil.which(env_tool_name) or configured


def resolve_makemkvcon(configured_path: str | PathLike[str] | None) -> str:
    fallbacks = [
        r"C:\Program Files (x86)\MakeMKV\makemkvcon.exe",
        r"C:\Program Files\MakeMKV\makemkvcon.exe",
    ]
    return resolve_tool(configured_path, fallbacks, "makemkvcon")


def resolve_ffprobe(configured_path: str | PathLike[str] | None) -> tuple[str, str]:
    """Resolve ffprobe. Accepts a direct exe path OR a directory (ffmpeg folder)."""
    configured = _normalize_pathlike(configured_path)
    if _is_file(configured):
        return configured, "configured"

    found = _resolve_ffprobe_from_dir(configured)
    if found:
        return found, "configured folder"

    for bundled in _bundled_binary_candidates("ffprobe.exe" if os.name == "nt" else "ffprobe"):
        if os.path.isfile(bundled):
            return bundled, "bundled"

    for directory in (
        r"C:\Program Files\ffmpeg",
        r"C:\Program Files\ffmpeg\bin",
        r"C:\Program Files (x86)\ffmpeg",
        r"C:\Program Files (x86)\ffmpeg\bin",
        r"C:\ffmpeg",
        r"C:\ffmpeg\bin",
    ):
        found = _resolve_ffprobe_from_dir(directory)
        if found:
            return found, f"common path ({directory})"

    found = shutil.which("ffprobe")
    if found:
        return found, f"PATH ({found})"
    return configured, "not found"


def _locate_makemkvcon_registry() -> str | None:
    """Check Windows registry for a MakeMKV installation directory."""
    if platform.system() != "Windows":
        return None
    try:
        import winreg

        reg_paths = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\MakeMKV"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\MakeMKV"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\MakeMKV"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\MakeMKV"),
        ]
        for hive, subkey in reg_paths:
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    for value_name in ("DisplayIcon", "InstallLocation", "InstallDir", "Path"):
                        try:
                            value, _ = winreg.QueryValueEx(key, value_name)
                            if not value:
                                continue
                            raw = str(value).strip().strip('"')
                            if raw.lower().endswith(".exe") and os.path.isfile(raw):
                                return raw
                            candidate = os.path.join(raw, "makemkvcon.exe")
                            if os.path.isfile(candidate):
                                return candidate
                        except OSError:
                            continue
                    try:
                        uninstall, _ = winreg.QueryValueEx(key, "UninstallString")
                        install_dir = os.path.dirname(str(uninstall).strip().strip('"'))
                        candidate = os.path.join(install_dir, "makemkvcon.exe")
                        if os.path.isfile(candidate):
                            return candidate
                    except OSError:
                        pass
            except OSError:
                continue
    except Exception as exc:
        logging.warning("_locate_makemkvcon_registry failed: %s", exc)
    return None


def _locate_ffprobe_registry() -> str | None:
    """Check Windows registry and known package-manager paths for ffprobe."""
    if platform.system() != "Windows":
        return None
    try:
        import winreg

        reg_paths = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\ffmpeg"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\ffmpeg"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Gyan.FFmpeg"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Gyan.FFmpeg"),
        ]
        for hive, subkey in reg_paths:
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    for value_name in ("DisplayIcon", "InstallLocation", "InstallDir", "Path"):
                        try:
                            value, _ = winreg.QueryValueEx(key, value_name)
                            raw = str(value).strip().strip('"')
                            if not raw:
                                continue
                            if raw.lower().endswith(".exe"):
                                if os.path.isfile(raw) and os.path.basename(raw).lower() == "ffprobe.exe":
                                    return raw
                                found = _resolve_ffprobe_from_dir(os.path.dirname(raw))
                            else:
                                found = _resolve_ffprobe_from_dir(raw)
                            if found:
                                return found
                        except OSError:
                            continue
                    try:
                        uninstall, _ = winreg.QueryValueEx(key, "UninstallString")
                        install_dir = os.path.dirname(str(uninstall).strip().strip('"'))
                        found = _resolve_ffprobe_from_dir(install_dir)
                        if found:
                            return found
                    except OSError:
                        pass
            except OSError:
                continue
    except Exception as exc:
        logging.warning("_locate_ffprobe_registry failed: %s", exc)

    choco = r"C:\tools\ffmpeg"
    return _resolve_ffprobe_from_dir(choco)


def auto_locate_tools() -> tuple[str, str]:
    """Find MakeMKV and ffprobe using Windows registry, common paths, and PATH."""
    makemkvcon = _locate_makemkvcon_registry() or resolve_makemkvcon("")
    if not makemkvcon or not os.path.isfile(makemkvcon):
        makemkvcon = ""

    ffprobe = _locate_ffprobe_registry()
    if not ffprobe:
        ffprobe, _ = resolve_ffprobe("")
    if not ffprobe or not os.path.isfile(ffprobe):
        ffprobe = ""

    return makemkvcon, ffprobe


__all__ = [
    "AppConfig",
    "CONFIG_FILE",
    "DEFAULTS",
    "auto_locate_ffmpeg",
    "auto_locate_handbrake",
    "auto_locate_tools",
    "get_config_dir",
    "load_config",
    "resolve_ffprobe",
    "resolve_makemkvcon",
    "resolve_tool",
    "save_config",
    "should_keep_current_tool_path",
    "validate_ffmpeg",
    "validate_ffprobe",
    "validate_handbrake",
    "validate_makemkvcon",
]
