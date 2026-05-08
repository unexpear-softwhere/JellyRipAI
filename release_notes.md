# JellyRip AI v1.0.20 Release Notes

JellyRip AI v1.0.20 — Claude provider model-ID fix, GitHub Pages
landing page, and the README rewrite for the two-repo fork model.
Existing users with a saved Claude configuration may see API errors
on first connection-test; re-save credentials and the dialog falls
back to the new default model automatically.

## Download

- Direct download: [JellyRipAI.exe](https://github.com/unexpear-softwhere/JellyRipAI/releases/download/ai-v1.0.20/JellyRipAI.exe)
- Installer: [JellyRipAIInstaller.exe](https://github.com/unexpear-softwhere/JellyRipAI/releases/download/ai-v1.0.20/JellyRipAIInstaller.exe)
- Release page: [ai-v1.0.20 release](https://github.com/unexpear-softwhere/JellyRipAI/releases/tag/ai-v1.0.20)
- Project site: [unexpear-softwhere.github.io/JellyRipAI](https://unexpear-softwhere.github.io/JellyRipAI/)

## What's New in 1.0.20

### Fixed

- Claude provider model identifiers realigned against the live
  Anthropic lineup.  The defaults shipped in ai-v1.0.19 were stale
  or never-released:
  - `claude-opus-4-6`          → `claude-opus-4-7`
  - `claude-sonnet-4-20250514` → `claude-sonnet-4-6`
  - `claude-haiku-4-5-20251001` unchanged (was already correct)

  Default model for new installs flips from Sonnet 4 (May 2025) to
  Sonnet 4.6, the current price/perf sweet spot for diagnostics +
  chat.  The pricing table in the AI Providers dialog was refreshed
  to match.

### Added

- GitHub Pages site published at
  [unexpear-softwhere.github.io/JellyRipAI](https://unexpear-softwhere.github.io/JellyRipAI/).
  Cayman theme, source = `main` branch / `docs/` folder.  All eight
  files under `docs/` ship to the site as-is.
- Landing page with download CTA, AI-feature blurb, project-info
  links, and a cross-link back to the non-AI baseline fork.

### Changed

- `README.md` "Active Branches" → "Active Forks".  Previous text
  described a legacy single-repo two-branch layout (`main` + `ai` on
  one origin) which hadn't been accurate since the AI fork moved to
  its own repository.  Anyone following the old clone instructions
  ran `git switch --track origin/ai` and silently landed on the
  non-AI baseline.
- "From source" rewritten to clone from the AI fork repo directly
  with no branch-switching, plus a one-line pointer to the non-AI
  baseline for users without AI needs.

### Removed

- `ui_visual_assets_copy/` untracked from the repo — ~9000 lines of
  retired tkinter UI snapshot, including the 6700-line
  `main_window.py` mirror.  Kept locally and gitignored.

### What's NOT in this release

No new AI features or workflow changes since ai-v1.0.19.  The chat
sidebar, AI provider dialog, diagnostics routing, and on-device
fallback behave identically.

## Companion fork: JellyRip MAIN

The non-AI baseline ships the same disc-ripping core without any AI
assistance.

- MAIN release page: [v1.0.20 release](https://github.com/unexpear/JellyRip/releases/tag/v1.0.20)
- MAIN project site: [unexpear.github.io/JellyRip](https://unexpear.github.io/JellyRip/)
