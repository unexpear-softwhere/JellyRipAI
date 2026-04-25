import subprocess
import sys
import time
from datetime import datetime
from typing import Any
from utils.helpers import MakeMKVDrive
from utils.parsing import parse_cli_args, parse_duration_to_seconds, parse_size_to_bytes, safe_int
from shared.ai_diagnostics import ProcessCapture, diag_process, diag_record, diag_exception

def _parse_drive_info(
    msg_lines: list[str],
    drive_rows: list[MakeMKVDrive] | None = None,
    selected_drive_index: int | None = None,
) -> dict[str, Any]:
    """Extract LibreDrive / UHD / disc-type info from MakeMKV MSG lines.

    ``libre_drive`` is tri-state:
      - ``"enabled"``  – LibreDrive active, full-speed reads available.
      - ``"possible"`` – drive hardware supports it but it is not yet
        active (firmware patch may be needed).
      - ``"unavailable"`` – drive does not support LibreDrive.
      - ``None``       – MakeMKV did not report anything (very old build
        or non-optical source).
    """
    info: dict[str, Any] = {
        "disc_type": None,           # "DVD", "Blu-ray", "UHD", or None
        "libre_drive": None,         # "enabled"/"possible"/"unavailable"/None
        "libre_drive_raw": "",       # verbatim MakeMKV line for debug log
        "uhd_friendly": None,        # True/False/None
        "drive_index": None,
        "drive_name": "",
        "disc_name": "",
        "device_path": "",
        "visible": None,
        "enabled": None,
        "flags": None,
        "usability_state": "",
        "firmware": "",
        "messages": [],              # all relevant MSG strings for diagnostics
    }
    for msg in msg_lines:
        msg_lower = msg.lower()

        # LibreDrive detection (three-state)
        if "libredrive" in msg_lower or "libre drive" in msg_lower:
            info["messages"].append(msg)
            info["libre_drive_raw"] = msg
            if "enabled" in msg_lower or "active" in msg_lower:
                info["libre_drive"] = "enabled"
            elif "possible" in msg_lower or "not yet" in msg_lower:
                info["libre_drive"] = "possible"
            elif (
                "not available" in msg_lower
                or "disabled" in msg_lower
                or "not detected" in msg_lower
                or "not supported" in msg_lower
            ):
                info["libre_drive"] = "unavailable"

        # Disc type detection
        if "uhd" in msg_lower or "ultra hd" in msg_lower:
            info["messages"].append(msg)
            info["disc_type"] = "UHD"
        elif "blu-ray" in msg_lower or "bluray" in msg_lower or "bdmv" in msg_lower:
            info["messages"].append(msg)
            if info["disc_type"] is None:
                info["disc_type"] = "Blu-ray"
        elif "dvd" in msg_lower and "disc" in msg_lower:
            info["messages"].append(msg)
            if info["disc_type"] is None:
                info["disc_type"] = "DVD"

        # Drive name / firmware
        if "drive name" in msg_lower or "product id" in msg_lower:
            info["messages"].append(msg)
            info["drive_name"] = msg
        if "firmware" in msg_lower:
            info["messages"].append(msg)
            info["firmware"] = msg

        # AACS / encryption hints
        if "aacs" in msg_lower:
            info["messages"].append(msg)
        if "bus encryption" in msg_lower:
            info["messages"].append(msg)

    # UHD-friendly heuristic: UHD discs need LibreDrive enabled
    if info["disc_type"] == "UHD":
        if info["libre_drive"] == "enabled":
            info["uhd_friendly"] = True
        else:
            info["uhd_friendly"] = False

    selected_drive = None
    if drive_rows:
        if selected_drive_index is not None:
            for drive in drive_rows:
                if drive.index == selected_drive_index:
                    selected_drive = drive
                    break
        if selected_drive is None:
            selected_drive = drive_rows[0]

    if selected_drive is not None:
        info["drive_index"] = selected_drive.index
        info["drive_name"] = selected_drive.drive_name or info["drive_name"]
        info["disc_name"] = selected_drive.disc_name
        info["device_path"] = selected_drive.device_path
        info["visible"] = selected_drive.visible
        info["enabled"] = selected_drive.enabled
        info["flags"] = selected_drive.flags
        info["usability_state"] = selected_drive.usability_state
    return info


def format_drive_compatibility(info: dict[str, Any]) -> list[str]:
    """Build the Drive Status block shown in the scan-results dialog."""
    lines: list[str] = []
    disc = info.get("disc_type")
    ld = info.get("libre_drive")
    drive_name = str(info.get("drive_name", "") or "").strip()
    disc_name = str(info.get("disc_name", "") or "").strip()
    device_path = str(info.get("device_path", "") or "").strip()
    usability_state = str(info.get("usability_state", "") or "").strip()

    if drive_name:
        label = f"Drive: {drive_name}"
        if device_path:
            label += f" [{device_path}]"
        lines.append(label)
    elif device_path:
        lines.append(f"Drive path: {device_path}")

    if disc_name:
        lines.append(f"Disc label: {disc_name}")

    if usability_state:
        lines.append(f"MakeMKV state: {usability_state}")

    if disc:
        lines.append(f"Disc type: {disc}")

    # LibreDrive status with visual indicator
    if ld == "enabled":
        lines.append("[OK] LibreDrive enabled")
    elif ld == "possible":
        lines.append("[!!] LibreDrive possible (firmware patch may be needed)")
    elif ld == "unavailable":
        lines.append("[XX] LibreDrive not available (UHD discs may not work)")

    # UHD verdict
    if disc == "UHD":
        if info.get("uhd_friendly") is True:
            lines.append("[OK] UHD rip: full quality expected")
        else:
            lines.append("[XX] UHD rip: may fail or produce degraded output")
    elif disc == "Blu-ray" and ld == "enabled":
        lines.append("[OK] Blu-ray rip: full quality expected")

    return lines


def scan_disc(engine, on_log, on_progress):
    makemkvcon  = engine._get_makemkvcon()
    disc_target = engine.get_disc_target()
    global_args = parse_cli_args(
        engine.cfg.get("opt_makemkv_global_args", ""),
        on_log,
        "MakeMKV global args"
    )
    info_args = parse_cli_args(
        engine.cfg.get("opt_makemkv_info_args", ""),
        on_log,
        "MakeMKV info args"
    )
    minlength = int(engine.cfg.get("opt_minlength_seconds", 0) or 0)
    minlength_args = [f"--minlength={minlength}"] if minlength > 0 else []

    on_log(f"Scanning disc ({disc_target})...")
    disc_info   = {}
    titles      = {}
    title_count = 0
    _msg_lines: list[str] = []
    _scan_cmd = [makemkvcon] + global_args + ["-r", "info", disc_target] + minlength_args + info_args
    _diag_capture = ProcessCapture(
        command=_scan_cmd,
        start_time=datetime.now().isoformat(),
        working_directory="",
    )
    _diag_raw_lines: list[str] = []
    _scan_start = time.time()
    try:
        popen_kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "bufsize": 1,
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = 0x08000000
        proc = subprocess.Popen(
            _scan_cmd,
            **popen_kwargs
        )
        valid_titles = []
        if proc.stdout is not None:
            for line in iter(proc.stdout.readline, ""):
                if engine.abort_event.is_set():
                    proc.kill()
                    return None
                line = line.strip()
                if not line:
                    continue
                # AI diagnostics: capture raw scan line
                if len(_diag_raw_lines) < 5000:
                    _diag_raw_lines.append(line)
                if line.startswith("CINFO:"):
                    parts = line[6:].split(",", 2)
                    if len(parts) < 3:
                        continue
                    try:
                        attr = int(parts[0])
                        val  = parts[2].strip().strip('"')
                    except (ValueError, IndexError):
                        continue
                    if attr == 2:
                        disc_info["title"] = val
                    elif attr == 28:
                        disc_info["lang_code"] = val
                    elif attr == 29:
                        disc_info["lang_name"] = val
                    elif attr == 32:
                        disc_info["volume_id"] = val
                elif line.startswith("TINFO:"):
                    parts = line[6:].split(",", 3)
                    if len(parts) < 4:
                        continue
                    try:
                        tid  = int(parts[0])
                        attr = int(parts[1])
                        val  = parts[3].strip().strip('"')
                    except (ValueError, IndexError):
                        continue
                    if tid not in titles:
                        titles[tid] = {
                            "id":               tid,
                            "name":             f"Title {tid+1}",
                            "duration":         "",
                            "duration_seconds": 0,
                            "size":             "",
                            "size_bytes":       0,
                            "chapters":         0,
                            "_invalid":         False,
                            "streams":          {},
                        }
                        title_count += 1
                        on_progress(
                            min(5 + title_count, 90)
                        )
                    if attr == 2:
                        titles[tid]["name"] = val
                    elif attr == 9:
                        titles[tid]["duration"] = val
                        dur_seconds = parse_duration_to_seconds(val)
                        titles[tid]["duration_seconds"] = dur_seconds
                        if val and dur_seconds <= 0:
                            titles[tid]["_invalid"] = True
                    elif attr == 8:
                        titles[tid]["chapters"] = safe_int(val)
                    elif attr == 11:
                        size_bytes = parse_size_to_bytes(val)
                        titles[tid]["size_bytes"] = size_bytes
                        if val and size_bytes <= 0:
                            titles[tid]["_invalid"] = True
                        if size_bytes > 0:
                            gb = size_bytes / (1024**3)
                            titles[tid]["size"] = f"{gb:.2f} GB"
                        else:
                            titles[tid]["size"] = val
                elif line.startswith("MSG:"):
                    parts = line[4:].split(",", 4)
                    if len(parts) >= 5:
                        msg = parts[4].strip().strip('"')
                        if msg:
                            _msg_lines.append(msg)
                elif line.startswith("SINFO:"):
                    parts = line[6:].split(",", 4)
                    if len(parts) < 5:
                        continue
                    try:
                        tid  = int(parts[0])
                        stid = int(parts[1])
                        attr = int(parts[2])
                        val  = parts[4].strip().strip('"')
                    except (ValueError, IndexError):
                        continue
                    if tid not in titles:
                        continue
                    streams = titles[tid]["streams"]
                    if stid not in streams:
                        streams[stid] = {}
                    streams[stid][attr] = val
            proc.wait()
            _diag_capture.exit_code = proc.returncode
            _diag_capture.end_time = datetime.now().isoformat()
            _diag_capture.duration_seconds = time.time() - _scan_start
            _diag_capture.stdout = "\n".join(_diag_raw_lines[-200:])
            if proc.returncode != 0:
                on_log(f"MakeMKV scan failed (exit code {proc.returncode})")
                diag_process(_diag_capture, success=False, category="scan_anomaly")
                return None
            # Parse and store drive/disc compatibility info
            engine.last_drive_info = _parse_drive_info(_msg_lines)
            # Filter and sort titles
            valid_titles = [t for t in titles.values() if not t["_invalid"]]
            valid_titles.sort(key=lambda t: -t["duration_seconds"])
            # AI diagnostics: flag scan anomaly if no valid titles despite success
            if not valid_titles and titles:
                diag_record("warning", "scan_anomaly",
                            f"Scan succeeded (exit 0) but all {len(titles)} titles were invalid",
                            details={"raw_title_count": len(titles), "disc_info": disc_info})
        return valid_titles
    except Exception as e:
        on_log(f"Scan failed: {e}")
        _diag_capture.end_time = datetime.now().isoformat()
        _diag_capture.duration_seconds = time.time() - _scan_start
        _diag_capture.stdout = "\n".join(_diag_raw_lines[-200:])
        diag_exception(e, context="scan_disc", category="scan_anomaly")
        return None
