# Repository Layout

JellyRip currently uses a flat Python project layout rather than a `src/` layout.

That is intentional for this project type because the main workflow is running a Windows desktop app directly from the repository during development and building a PyInstaller executable from the root entrypoint.

## Top-level structure

- `main.py` - primary app entrypoint
- `JellyRip.py` - compatibility entrypoint and recovery map
- `controller/` - workflow orchestration
- `engine/` - external-tool execution and file operations
- `gui/` - tkinter interface
- `utils/` - helper modules
- `shared/` - shared runtime defaults and cross-module constants
- `shared/ai/` - provider registry, credential storage, and provider adapters
- `tools/` - developer utilities (folder scanner, ffmpeg staging script)
- `tests/` - automated regression coverage
- `docs/` - repository and architecture documentation
- `installer/` - installer definition assets
- `.github/` - issue templates and automation

## Why flat layout here

Compared with a `src/` layout, flat layout is simpler for this repo because:

- the app is run directly with `python main.py`
- the primary deliverable is a Windows executable, not a reusable Python package
- contributors are more likely to debug from the repository root than install an editable package

## Repository hygiene expectations

Tracked:

- source code
- tests
- docs
- installer scripts
- changelog and release-facing docs

Ignored:

- build output
- generated executables
- caches
- local config and logs

## Documentation split

- `README.md` - user-facing overview and quick start
- `CHANGELOG.md` - curated release history
- `TESTERS.md` - manual validation worksheet
- `docs/architecture.md` - code structure and design boundaries
- `docs/ai-assist-branch.md` - AI branch-specific behavior, boundaries,
  provider stack, and diagnostics map
- `docs/repository-layout.md` - repo organization rationale

## When to restructure further

Move to a stricter package layout only if the project starts optimizing for one of these:

- publishing as a Python package
- editable-install based contributor workflow
- cross-platform packaging conventions

Right now, the current layered flat layout is the better fit.
