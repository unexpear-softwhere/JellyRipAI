from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Sequence, TypedDict

from config import resolve_ffprobe
from tools.folder_scanner import FolderScanEntry, get_sort_mode_label


class FolderScanOptions(TypedDict):
    mode: int | str
    recursive: bool


@dataclass(frozen=True)
class FolderScanRequest:
    folder: str
    mode: int | str
    recursive: bool
    include_dirs: bool
    log_path: str
    ffprobe_exe: str | None


class FolderScanRow(TypedDict):
    iid: str
    entry: FolderScanEntry
    values: tuple[str, str, str, str, str, str]


@dataclass(frozen=True)
class FolderScanResultsModel:
    subtitle: str
    status_text: str
    rows: list[FolderScanRow]


def folder_scan_requires_ffprobe(mode: int | str) -> bool:
    return str(mode or "").strip().lower() in {"duration_desc", "duration_asc"}


def build_folder_scan_log_path(main_log: str, home_dir: str | None = None) -> str:
    raw_main_log = str(main_log or "").strip()
    if raw_main_log:
        normalized = os.path.normpath(raw_main_log)
        if os.path.isdir(normalized):
            log_dir = normalized
        else:
            log_dir = os.path.dirname(os.path.abspath(normalized))
    else:
        log_dir = str(home_dir or os.path.expanduser("~"))
    return os.path.join(log_dir, "folder_scan_log.txt")


def build_folder_scan_request(
    *,
    folder: str,
    scan_options: Mapping[str, object],
    main_log: str,
    ffprobe_path: str,
    include_dirs: bool = False,
    home_dir: str | None = None,
    allow_path_lookup: bool = False,
) -> FolderScanRequest:
    mode = scan_options.get("mode", "size_desc")
    recursive = bool(scan_options.get("recursive", True))
    ffprobe_exe = None
    if folder_scan_requires_ffprobe(mode):
        ffprobe_exe = resolve_ffprobe(
            os.path.normpath(ffprobe_path),
            allow_path_lookup=allow_path_lookup,
        ).path or None
    return FolderScanRequest(
        folder=os.path.normpath(folder),
        mode=mode,
        recursive=recursive,
        include_dirs=include_dirs,
        log_path=build_folder_scan_log_path(main_log, home_dir=home_dir),
        ffprobe_exe=ffprobe_exe,
    )


def build_folder_scan_results_model(
    results: Sequence[FolderScanEntry],
    scan_options: Mapping[str, object],
) -> FolderScanResultsModel:
    recursive_text = (
        "Recursive"
        if bool(scan_options.get("recursive", True))
        else "Current folder only"
    )
    subtitle = (
        f"Sort: {get_sort_mode_label(scan_options.get('mode', 'size_desc'))} | "
        f"Scope: {recursive_text} | MKV files only"
    )
    status_text = (
        f"{len(results)} MKV file(s) found"
        if results
        else "No MKV files found"
    )
    rows: list[FolderScanRow] = []
    for index, entry in enumerate(results):
        relative_folder = os.path.dirname(entry["relative_path"]) or "."
        rows.append(
            {
                "iid": f"scan_{index}",
                "entry": entry,
                "values": (
                    entry["name"],
                    relative_folder,
                    entry["size_str"],
                    entry["duration_str"],
                    entry["modified_str"],
                    entry["status"],
                ),
            }
        )
    return FolderScanResultsModel(
        subtitle=subtitle,
        status_text=status_text,
        rows=rows,
    )


def select_folder_scan_entries(
    rows: Sequence[FolderScanRow],
    selected_iids: Sequence[str],
) -> list[FolderScanEntry]:
    selected = set(selected_iids)
    return [row["entry"] for row in rows if row["iid"] in selected]


def select_folder_scan_paths(
    rows: Sequence[FolderScanRow],
    selected_iids: Sequence[str],
) -> list[str]:
    return [
        entry["path"]
        for entry in select_folder_scan_entries(rows, selected_iids)
    ]
