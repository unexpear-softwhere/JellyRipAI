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

from shared.ai_profile import load_ai_profile
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


@dataclass(frozen=True)
class ResolvedTool:
    path: str
    source: str
    error: str = ""
    suggestion_path: str = ""
    suggestion_source: str = ""


def _normalize_pathlike(path: str | PathLike[str] | None) -> str:
    if not path:
        return ""
    return os.path.normpath(os.fspath(path))


def _is_file(path: str | PathLike[str] | None) -> bool:
    normalized = _normalize_pathlike(path)
    return bool(normalized) and os.path.isfile(normalized)


def _is_64bit_windows() -> bool:
    return platform.system() == "Windows" and bool(
        os.environ.get("ProgramW6432")
        or os.environ.get("PROCESSOR_ARCHITEW6432")
        or os.environ.get("PROGRAMFILES(X86)")
    )


def _makemkvcon_candidate_names() -> list[str]:
    if _is_64bit_windows():
        return ["makemkvcon64.exe", "makemkvcon.exe"]
    return ["makemkvcon.exe", "makemkvcon64.exe"]


def _prefer_makemkvcon64(path: "str | PathLike[str] | None") -> str:
    """Upgrade a configured 32-bit ``makemkvcon.exe`` to the 64-bit
    ``makemkvcon64.exe`` sitting beside it, when present.

    MakeMKV ships both; the 32-bit console prints a deprecation warning
    on every rip ("You are running a 32-bit (x86) version…") and is the
    older build.  A saved config that still points at the 32-bit exe
    (an older default or a manual pick) is silently moved to the 64-bit
    one so the app runs the modern build.  Anything else — a custom
    name, no 64-bit sibling, non-Windows — is left untouched.
    """
    text = str(path or "")
    try:
        if (
            platform.system() == "Windows"
            and os.path.basename(text).lower() == "makemkvcon.exe"
        ):
            sibling = os.path.join(os.path.dirname(text), "makemkvcon64.exe")
            if os.path.isfile(sibling):
                return sibling
    except Exception:
        pass
    return text


def _makemkvcon_common_paths() -> list[str]:
    if platform.system() != "Windows":
        return []
    roots = [
        r"C:\Program Files (x86)\MakeMKV",
        r"C:\Program Files\MakeMKV",
    ]
    candidates: list[str] = []
    seen: set[str] = set()
    for name in _makemkvcon_candidate_names():
        for root in roots:
            candidate = os.path.normpath(os.path.join(root, name))
            if candidate in seen:
                continue
            seen.add(candidate)
            candidates.append(candidate)
    return candidates


def _resolve_makemkvcon_from_dir(directory: str | PathLike[str] | None) -> str | None:
    normalized = _normalize_pathlike(directory)
    if not normalized:
        return None
    for name in _makemkvcon_candidate_names():
        candidate = os.path.join(normalized, name)
        if os.path.isfile(candidate):
            return _normalize_pathlike(candidate)
    return None


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


def _path_lookup_allowed(allow_path_lookup: bool) -> bool:
    return platform.system() != "Windows" or bool(allow_path_lookup)


def _reason_or_default(reason: str, default: str = "validation failed") -> str:
    text = str(reason or "").strip()
    return text or default


def _iter_unique_candidates(
    candidates: Iterable[tuple[str, str]],
) -> Iterable[tuple[str, str]]:
    seen: set[str] = set()
    for raw_path, source in candidates:
        normalized = _normalize_pathlike(raw_path)
        if not normalized:
            continue
        key = os.path.normcase(normalized)
        if key in seen:
            continue
        seen.add(key)
        yield normalized, source


def _configured_resolution(
    configured_path: str | PathLike[str] | None,
    *,
    validator: ToolValidator,
    tool_label: str,
    allow_directory: bool = False,
    directory_resolver: Callable[[str], str | None] | None = None,
) -> ResolvedTool | None:
    configured = _normalize_pathlike(configured_path)
    if not configured:
        return None

    if _is_file(configured):
        ok, reason = validator(configured)
        if ok:
            return ResolvedTool(path=configured, source="configured executable")
        return ResolvedTool(
            path="",
            source="",
            error=(
                f"Configured {tool_label} executable failed validation: "
                f"{_reason_or_default(reason)}"
            ),
        )

    if allow_directory and os.path.isdir(configured):
        resolved = directory_resolver(configured) if directory_resolver else None
        if not resolved:
            return ResolvedTool(
                path="",
                source="",
                error=f"No {tool_label} executable found in the configured folder.",
            )
        ok, reason = validator(resolved)
        if ok:
            return ResolvedTool(path=resolved, source="configured folder")
        return ResolvedTool(
            path="",
            source="",
            error=(
                f"Configured {tool_label} folder failed validation: "
                f"{_reason_or_default(reason)}"
            ),
        )

    return ResolvedTool(
        path="",
        source="",
        error=f"Configured {tool_label} path does not exist.",
    )


def _detected_resolution(
    candidates: Iterable[tuple[str, str]],
    *,
    validator: ToolValidator,
) -> ResolvedTool:
    invalid_reasons: list[str] = []
    for candidate, source in _iter_unique_candidates(candidates):
        if not _is_file(candidate):
            continue
        ok, reason = validator(candidate)
        if ok:
            return ResolvedTool(path=candidate, source=source)
        invalid_reasons.append(f"{source}: {_reason_or_default(reason)}")

    message = ""
    if invalid_reasons:
        message = "; ".join(invalid_reasons)
    return ResolvedTool(path="", source="", error=message)


def _attach_suggestion(
    failure: ResolvedTool,
    suggestion: ResolvedTool,
) -> ResolvedTool:
    if not suggestion.path:
        return failure
    return ResolvedTool(
        path="",
        source="",
        error=failure.error,
        suggestion_path=suggestion.path,
        suggestion_source=suggestion.source,
    )


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


def _resolve_bundled_tool(
    filename: str,
    validator: ToolValidator,
) -> "ResolvedTool | None":
    """Return the bundled copy of ``filename`` if present and valid.

    Used to *always* prefer the binary shipped alongside the app (e.g.
    FFmpeg / ffprobe) so the user never has to point Settings at it.
    Returns ``None`` when running from source (no bundle present),
    letting the caller fall back to configured / PATH resolution.
    """
    for candidate in _bundled_binary_candidates(filename):
        if not _is_file(candidate):
            continue
        ok, _reason = validator(candidate)
        if ok:
            return ResolvedTool(path=candidate, source="bundled")
    return None


def auto_locate_ffmpeg(*, allow_path_lookup: bool = False) -> str:
    """Find a validated FFmpeg executable."""
    return resolve_ffmpeg("", allow_path_lookup=allow_path_lookup).path


def auto_locate_handbrake(*, allow_path_lookup: bool = False) -> str:
    """Find a validated HandBrakeCLI executable."""
    return resolve_handbrake("", allow_path_lookup=allow_path_lookup).path


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


def validate_vlc(path: str | PathLike[str] | None) -> ToolValidationResult:
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
    # Prefer 64-bit MakeMKV: a saved config that still points at the
    # 32-bit makemkvcon.exe is upgraded to makemkvcon64.exe when it
    # exists next to it (avoids MakeMKV's 32-bit deprecation warning).
    _mk = cfg.get("makemkvcon_path")
    if isinstance(_mk, str) and _mk:
        cfg["makemkvcon_path"] = _prefer_makemkvcon64(_mk)
    theme_overrides = cfg.get("opt_theme_overrides", {})
    if isinstance(theme_overrides, Mapping):
        cfg["opt_theme_overrides"] = {
            str(key): value
            for key, value in theme_overrides.items()
        }
    else:
        cfg["opt_theme_overrides"] = {}
    cfg["opt_ai_profile"] = load_ai_profile(cfg).to_dict()

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
    # MakeMKV's documented automation example for enumerating drives is
    # `makemkvcon -r --cache=1 info disc:9999`; Windows builds do not
    # support `--version`.
    return _run_probe(path, ["-r", "--cache=1", "info", "disc:9999"])


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
    *,
    allow_path_lookup: bool = False,
) -> ResolvedTool:
    """Resolve a validated executable path from configured/common/PATH candidates."""
    configured_result = _configured_resolution(
        configured_path,
        validator=lambda path: (True, ""),
        tool_label=env_tool_name,
    )
    detected_candidates = [
        *[(path, "known location") for path in common_paths],
        *_maybe_path_candidate(
            env_tool_name,
            allow_path_lookup=allow_path_lookup,
        ),
    ]
    detected_result = _detected_resolution(
        detected_candidates,
        validator=lambda path: (True, ""),
    )
    if configured_result is not None:
        if configured_result.path:
            return configured_result
        return _attach_suggestion(configured_result, detected_result)
    if detected_result.path:
        return detected_result
    return ResolvedTool(path="", source="", error=f"{env_tool_name} was not found.")


def _ffmpeg_known_location_candidates() -> list[tuple[str, str]]:
    return [
        (r"C:\Program Files\ffmpeg\bin\ffmpeg.exe", "known location"),
        (r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe", "known location"),
        (r"C:\ffmpeg\bin\ffmpeg.exe", "known location"),
        (r"C:\ffmpeg\ffmpeg.exe", "known location"),
    ]


def _handbrake_known_location_candidates() -> list[tuple[str, str]]:
    return [
        (r"C:\Program Files\HandBrake\HandBrakeCLI.exe", "known location"),
        (r"C:\Program Files (x86)\HandBrake\HandBrakeCLI.exe", "known location"),
        (r"C:\HandBrake\HandBrakeCLI.exe", "known location"),
    ]


def _makemkv_known_location_candidates() -> list[tuple[str, str]]:
    install_dirs = [
        r"C:\Program Files (x86)\MakeMKV",
        r"C:\Program Files\MakeMKV",
    ]
    return [
        (os.path.join(install_dir, executable_name), "known location")
        for executable_name in _makemkv_executable_names()
        for install_dir in install_dirs
    ]


def _ffprobe_known_location_candidates() -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    for directory in (
        r"C:\Program Files\ffmpeg",
        r"C:\Program Files\ffmpeg\bin",
        r"C:\Program Files (x86)\ffmpeg",
        r"C:\Program Files (x86)\ffmpeg\bin",
        r"C:\ffmpeg",
        r"C:\ffmpeg\bin",
        r"C:\tools\ffmpeg",
    ):
        found = _resolve_ffprobe_from_dir(directory)
        if found:
            candidates.append((found, "known location"))
    return candidates


def _vlc_known_location_candidates() -> list[tuple[str, str]]:
    return [
        (r"C:\Program Files\VideoLAN\VLC\vlc.exe", "known location"),
        (r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe", "known location"),
    ]


def _windows_host_is_64bit() -> bool:
    if platform.system() != "Windows":
        return False
    for raw_value in (
        os.environ.get("PROCESSOR_ARCHITEW6432", ""),
        os.environ.get("PROCESSOR_ARCHITECTURE", ""),
        platform.machine(),
    ):
        value = str(raw_value or "").upper()
        if value in {"AMD64", "ARM64", "IA64", "X86_64"}:
            return True
    return False


def _makemkv_executable_names() -> list[str]:
    if platform.system() != "Windows":
        return ["makemkvcon"]
    if _windows_host_is_64bit():
        return ["makemkvcon64.exe", "makemkvcon.exe"]
    return ["makemkvcon.exe"]


def _resolve_makemkv_from_dir(dirpath: str | PathLike[str] | None) -> str | None:
    normalized = _normalize_pathlike(dirpath)
    if not normalized or not os.path.isdir(normalized):
        return None
    for executable_name in _makemkv_executable_names():
        candidate = os.path.join(normalized, executable_name)
        if os.path.isfile(candidate):
            return candidate
    return None


def _maybe_path_candidate(
    env_tool_name: str,
    *,
    allow_path_lookup: bool,
) -> list[tuple[str, str]]:
    if not _path_lookup_allowed(allow_path_lookup):
        return []
    found = shutil.which(env_tool_name)
    if not found:
        return []
    source = (
        "PATH via advanced toggle"
        if platform.system() == "Windows"
        else "PATH"
    )
    return [(found, source)]


def _maybe_path_candidates(
    env_tool_names: Iterable[str],
    *,
    allow_path_lookup: bool,
) -> list[tuple[str, str]]:
    if not _path_lookup_allowed(allow_path_lookup):
        return []
    source = (
        "PATH via advanced toggle"
        if platform.system() == "Windows"
        else "PATH"
    )
    return [
        (found, source)
        for tool_name in env_tool_names
        for found in [shutil.which(tool_name)]
        if found
    ]


def _resolve_required_tool(
    configured_path: str | PathLike[str] | None,
    *,
    validator: ToolValidator,
    tool_label: str,
    detected_candidates: Iterable[tuple[str, str]],
    allow_directory: bool = False,
    directory_resolver: Callable[[str], str | None] | None = None,
) -> ResolvedTool:
    configured_result = _configured_resolution(
        configured_path,
        validator=validator,
        tool_label=tool_label,
        allow_directory=allow_directory,
        directory_resolver=directory_resolver,
    )
    detected_result = _detected_resolution(
        detected_candidates,
        validator=validator,
    )
    if configured_result is not None:
        if configured_result.path:
            return configured_result
        return _attach_suggestion(configured_result, detected_result)
    if detected_result.path:
        return detected_result
    if detected_result.error:
        return ResolvedTool(path="", source="", error=detected_result.error)
    return ResolvedTool(
        path="",
        source="",
        error=f"{tool_label} executable not found.",
    )


def resolve_makemkvcon(
    configured_path: str | PathLike[str] | None,
    *,
    allow_path_lookup: bool = False,
) -> ResolvedTool:
    if configured_path:
        configured_path = _prefer_makemkvcon64(configured_path)
    detected_candidates: list[tuple[str, str]] = []
    registry_candidate = _locate_makemkvcon_registry()
    if registry_candidate:
        detected_candidates.append((registry_candidate, "registry"))
    detected_candidates.extend(_makemkv_known_location_candidates())
    detected_candidates.extend(
        _maybe_path_candidates(
            [
                os.path.splitext(executable_name)[0]
                for executable_name in _makemkv_executable_names()
            ],
            allow_path_lookup=allow_path_lookup,
        )
    )
    return _resolve_required_tool(
        configured_path,
        validator=validate_makemkvcon,
        tool_label="MakeMKV",
        detected_candidates=detected_candidates,
    )


def resolve_ffprobe(
    configured_path: str | PathLike[str] | None,
    *,
    allow_path_lookup: bool = False,
) -> ResolvedTool:
    # ffprobe ships bundled with the app — always prefer the bundled
    # copy so the user never has to configure or choose it.  The
    # candidates below are a fallback only for running from source.
    _bundled = _resolve_bundled_tool(
        "ffprobe.exe" if os.name == "nt" else "ffprobe", validate_ffprobe
    )
    if _bundled is not None:
        return _bundled
    detected_candidates = [
        *(
            (bundled, "bundled")
            for bundled in _bundled_binary_candidates(
                "ffprobe.exe" if os.name == "nt" else "ffprobe"
            )
        ),
    ]
    registry_candidate = _locate_ffprobe_registry()
    if registry_candidate:
        detected_candidates.append((registry_candidate, "registry"))
    detected_candidates.extend(_ffprobe_known_location_candidates())
    detected_candidates.extend(
        _maybe_path_candidate("ffprobe", allow_path_lookup=allow_path_lookup)
    )
    return _resolve_required_tool(
        configured_path,
        validator=validate_ffprobe,
        tool_label="ffprobe",
        detected_candidates=detected_candidates,
        allow_directory=True,
        directory_resolver=_resolve_ffprobe_from_dir,
    )


def resolve_ffmpeg(
    configured_path: str | PathLike[str] | None,
    *,
    allow_path_lookup: bool = False,
) -> ResolvedTool:
    # FFmpeg ships bundled with the app — always prefer the bundled copy
    # so the user never has to configure or choose it.  The candidates
    # below are a fallback only for running from source.
    _bundled = _resolve_bundled_tool(
        "ffmpeg.exe" if os.name == "nt" else "ffmpeg", validate_ffmpeg
    )
    if _bundled is not None:
        return _bundled
    detected_candidates = [
        *(
            (bundled, "bundled")
            for bundled in _bundled_binary_candidates(
                "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
            )
        ),
        *_ffmpeg_known_location_candidates(),
        *_maybe_path_candidate("ffmpeg", allow_path_lookup=allow_path_lookup),
    ]
    return _resolve_required_tool(
        configured_path,
        validator=validate_ffmpeg,
        tool_label="FFmpeg",
        detected_candidates=detected_candidates,
    )


def resolve_handbrake(
    configured_path: str | PathLike[str] | None,
    *,
    allow_path_lookup: bool = False,
) -> ResolvedTool:
    detected_candidates = [
        *(
            (bundled, "bundled")
            for bundled in _bundled_binary_candidates(
                "HandBrakeCLI.exe" if os.name == "nt" else "HandBrakeCLI"
            )
        ),
        *_handbrake_known_location_candidates(),
        *_maybe_path_candidate(
            "HandBrakeCLI",
            allow_path_lookup=allow_path_lookup,
        ),
    ]
    return _resolve_required_tool(
        configured_path,
        validator=validate_handbrake,
        tool_label="HandBrakeCLI",
        detected_candidates=detected_candidates,
    )


def resolve_vlc(*, allow_path_lookup: bool = False) -> ResolvedTool:
    detected_candidates = [
        *_vlc_known_location_candidates(),
        *_maybe_path_candidate("vlc", allow_path_lookup=allow_path_lookup),
    ]
    detected_result = _detected_resolution(
        detected_candidates,
        validator=validate_vlc,
    )
    if detected_result.path:
        return detected_result
    if detected_result.error:
        return ResolvedTool(path="", source="", error=detected_result.error)
    return ResolvedTool(path="", source="", error="VLC executable not found.")


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
                                base_name = os.path.basename(raw).lower()
                                if base_name in {
                                    name.lower()
                                    for name in _makemkv_executable_names()
                                }:
                                    return raw
                                found = _resolve_makemkv_from_dir(
                                    os.path.dirname(raw)
                                )
                                if found:
                                    return found
                            found = _resolve_makemkv_from_dir(raw)
                            if found:
                                return found
                        except OSError:
                            continue
                    try:
                        uninstall, _ = winreg.QueryValueEx(key, "UninstallString")
                        install_dir = os.path.dirname(str(uninstall).strip().strip('"'))
                        found = _resolve_makemkv_from_dir(install_dir)
                        if found:
                            return found
                    except OSError:
                        pass
            except OSError:
                continue
    except Exception as exc:
        logging.warning("_locate_makemkvcon_registry failed: %s", exc)
    return None


def _locate_ffprobe_registry() -> str | None:
    """Check Windows registry for an ffprobe installation directory."""
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
    return None

def auto_locate_tools(*, allow_path_lookup: bool = False) -> tuple[str, str]:
    """Find validated MakeMKV and ffprobe executables."""
    makemkvcon = resolve_makemkvcon(
        "",
        allow_path_lookup=allow_path_lookup,
    ).path
    ffprobe = resolve_ffprobe(
        "",
        allow_path_lookup=allow_path_lookup,
    ).path
    return makemkvcon, ffprobe


__all__ = [
    "AppConfig",
    "CONFIG_FILE",
    "DEFAULTS",
    "ResolvedTool",
    "StartupConfigIssue",
    "StartupConfigResult",
    "auto_locate_ffmpeg",
    "auto_locate_handbrake",
    "auto_locate_tools",
    "get_config_dir",
    "load_config",
    "load_startup_config",
    "resolve_ffmpeg",
    "resolve_ffprobe",
    "resolve_handbrake",
    "resolve_makemkvcon",
    "resolve_vlc",
    "resolve_tool",
    "save_config",
    "should_keep_current_tool_path",
    "validate_ffmpeg",
    "validate_ffprobe",
    "validate_handbrake",
    "validate_makemkvcon",
    "validate_vlc",
]
