# Architecture Overview

JellyRip is a Windows-first desktop application built around a simple layered architecture. The codebase is intentionally split so UI behavior, workflow decisions, and external tool execution do not collapse into one file.

## Layers

### GUI

File: `gui/main_window.py`

Responsibilities:

- owns all tkinter widgets and dialogs
- renders status, logs, prompts, and settings
- starts background tasks without containing rip logic

The GUI should stay thin. It is responsible for presentation and user interaction, not MakeMKV policy.

### Controller

Files:

- `controller/controller.py` - workflow orchestration entrypoint
- `controller/legacy_compat.py` - compatibility helpers carried from the pre-refactor controller
- `controller/library_scan.py` - episode-name parsing and existing-library scanning
- `controller/naming.py` - title and preview naming, metadata provider aliasing
- `controller/rip_validation.py` - rip output size and integrity validation
- `controller/session.py` - per-session callback helpers and log routing
- `controller/session_paths.py` - temp/movie/TV path setup and validation
- `controller/session_recovery.py` - resumable session selection and title restoration

Responsibilities:

- session orchestration
- multi-step ripping workflows
- user decision handling
- session summaries and reporting
- path validation and move policy

This is the workflow layer. Most behavior changes belong here first.

### Engine

File: `engine/ripper_engine.py`

Responsibilities:

- calling MakeMKV
- calling ffprobe
- subprocess lifecycle management
- low-level file operations
- rip retry execution and output inspection

This layer should avoid GUI decisions and should expose behavior that the controller can combine safely.

### Utilities

Files under `utils/`

Responsibilities:

- parsing helpers
- scoring and media helpers
- session/result helpers
- fallback policy helpers
- updater support

These modules should remain reusable and narrowly scoped.

### AI Assist Layer

Files:

- `gui/ai_provider_dialog.py`
- `shared/ai/`
- `shared/ai_diagnostics.py`
- `controller/assist.py` - assist-layer helpers (identity suggestions) kept separate from the deterministic workflow

Responsibilities:

- provider setup and connection testing
- AI sidebar chat and operator-facing assistant UX
- advisory diagnostics and failure summaries
- credential persistence and provider resolution

This layer is intentionally downstream of the core rip pipeline. It may
observe state, summarize failures, and suggest next steps, but it should
not become a hidden control path for deterministic ripping behavior.

## Runtime entrypoints

- `main.py` - primary entrypoint for development and packaging
- `JellyRip.py` - compatibility entrypoint kept as a fallback map and import surface

## Design goals

The repository is optimized for:

- safe ripping on Windows
- deterministic user prompts
- strong failure visibility
- easy regression testing for workflow logic

It is not optimized for cross-platform packaging or library-style reuse.

## Testing strategy

The test suite is behavior-first.

- `tests/test_behavior_guards.py` protects workflow and regression behavior
- `tests/test_imports.py` protects import boundaries and GUI import safety
- `tests/test_parsing.py` protects parsing and helper correctness
- `tests/test_ai_chat_sidebar.py` and `tests/test_ai_provider_dialog.py`
  protect the AI branch UI behavior

Hardware-driven paths still require manual validation, which is why `TESTERS.md` remains part of the repo.
