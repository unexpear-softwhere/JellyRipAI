
# JellyRip Feature Map

This document lists the main features of JellyRip and where they are
implemented in the codebase. Use this as a quick reference for navigation
and code search.

---

## Folder Scanning & Temp Session Management

- **Find old temp folders:**
  - `engine/ripper_engine.py`: `find_old_temp_folders()`

- **Find resumable sessions:**
  - `engine/ripper_engine.py`: `find_resumable_sessions()`

- **Temp/session folder naming:**
  - `utils/helpers.py`: `make_rip_folder_name()`

## Disc Scanning & Title Analysis

- **Disc scan (MakeMKV):**
  - `engine/ripper_engine.py`: `scan_disc()`
  - `controller/controller.py`: `scan_with_retry()` (UI/logic wrapper)

- **Title scoring, parsing:**
  - `engine/ripper_engine.py`: `score_title()`,
    `parse_duration_to_seconds()`, etc.

## File Validation & Analysis

- **ffprobe validation (container integrity):**
  - `engine/ripper_engine.py`: `_quick_ffprobe_ok()`,
    `_probe_file_duration_and_size()`, `analyze_files()`
  - Used throughout `controller/controller.py` for pre/post-move checks

- **ffprobe path resolution:**
  - `config.py`: `resolve_ffprobe()`
  - `engine/ripper_engine.py`: `validate_tools()`

## Folder Picker & Library Scanning

- **Folder picker dialogs:**
  - `gui/main_window.py`: `ask_directory()`, `ask_input()`

- **Library scan (attach to existing show):**
  - `controller/controller.py`: `_scan_library_folder()`,
    `_scan_episode_files()`

## Extras/Bonus Folder Logic

- **Extras/bonus folder assignment:**
  - `controller/controller.py`: logic in move/organize flows
  - `gui/main_window.py`: extras/bonus picker dialogs

## Settings & Configuration

- **Settings dialog:**
  - `gui/main_window.py`: settings tab logic

- **Config defaults/validation:**
  - `config.py`, `controller/controller.py`

## AI Assistant, Providers, and Diagnostics

- **AI sidebar chat and mode selector:**
  - `gui/main_window.py`: AI mode UI, sidebar chat, snapshot-based help,
    provider selection, and request routing

- **AI provider connection dialog:**
  - `gui/ai_provider_dialog.py`: provider setup, model selection,
    connectivity tests, and active cloud-provider selection

- **Provider registry and adapters:**
  - `shared/ai/provider_registry.py`
  - `shared/ai/providers/`

- **AI credential storage:**
  - `shared/ai/credential_store.py`: DPAPI-backed credential persistence

- **AI diagnostics and durable system logging:**
  - `shared/ai_diagnostics.py`: diagnostics manager, event capture,
    backend routing, advisory AI analysis
  - `shared/ai/diagnostics.py`: persistent system log and GUI-safe
    mirroring

## Update & Download Logic

- **Update check/download:**
  - `gui/update_ui.py`: `check_for_updates()`,
    `launch_downloaded_update()`
  - Used in `gui/main_window.py`

## Test Coverage

- **Tests for core behaviors:**
  - `tests/` (various test_*.py files)

---

For more details, see inline docstrings in each module.
