# JellyRip AI v1.0.21 Release Notes

JellyRip AI v1.0.21 — audit-driven cleanup.  Default Gemini model
bumped to match what we did for Claude in v1.0.20, plus repository
hygiene and README honesty around workflow test status.

## Download

- Direct download: [JellyRipAI.exe](https://github.com/unexpear-softwhere/JellyRipAI/releases/download/ai-v1.0.21/JellyRipAI.exe)
- Installer: [JellyRipAIInstaller.exe](https://github.com/unexpear-softwhere/JellyRipAI/releases/download/ai-v1.0.21/JellyRipAIInstaller.exe)
- Release page: [ai-v1.0.21 release](https://github.com/unexpear-softwhere/JellyRipAI/releases/tag/ai-v1.0.21)
- Project site: [unexpear-softwhere.github.io/JellyRipAI](https://unexpear-softwhere.github.io/JellyRipAI/)

## What's New in 1.0.21

### Provider stack

- **Default Gemini model bumped from `gemini-2.0-flash` to
  `gemini-2.5-flash`.**  Matches the price/perf tier shift we did
  for Claude in v1.0.20 (defaulting to current generation rather
  than the previous one).  `gemini-2.0-flash` and
  `gemini-2.0-flash-lite` stay in the dropdown for users who want
  them explicitly.  No change to Claude / OpenAI / local provider
  defaults.
- `credential_store.py` docstring example updated to match.

### Documentation

- README "Main Workflows" — added "(some testing)" / "(not tested)"
  qualifiers to match MAIN's honesty about test status.  Same code
  in both forks; status should match.

### Bundle / repo

- **Removed `gui_qt/qss/warm.qss`** — empty 0-byte placeholder, same
  cleanup MAIN did in v1.0.21.
- **`pyproject.toml` keywords** — dropped `tkinter`, added
  `pyside6`, `qt`, `claude`, `ai`.
- **`.gitignore` additions:**
  - `dashboard.html` (defensive — file doesn't exist on AI today,
    but if Claude ever drops it here it must not get tracked
    alongside the already-gitignored `CLAUDE.md`).
  - `*.tmp` (catches stray scratch artifacts).

### What's NOT in this release

No engine or workflow changes.  Chat sidebar, AI provider dialog,
diagnostics routing, on-device fallback all behave identically to
ai-v1.0.20.  Existing users with a saved Gemini configuration are
unaffected — their saved model preference takes precedence over the
new default.

## Companion fork: JellyRip MAIN

The non-AI baseline ships the same disc-ripping core without AI.

- MAIN release page: [v1.0.21 release](https://github.com/unexpear/JellyRip/releases/tag/v1.0.21)
- MAIN project site: [unexpear.github.io/JellyRip](https://unexpear.github.io/JellyRip/)
