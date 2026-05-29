# JellyRip AI v1.0.23 Release Notes

JellyRip AI v1.0.23 — the AI assistant becomes genuinely hands-on:
docked and usable while dialogs are open, able to look things up on
the web and TMDB, and aware of the full per-title disc scan.  Plus two
real scan-path bug fixes.

## Download

- Direct download: [JellyRipAI.exe](https://github.com/unexpear-softwhere/JellyRipAI/releases/download/ai-v1.0.23/JellyRipAI.exe)
- Installer: [JellyRipAIInstaller.exe](https://github.com/unexpear-softwhere/JellyRipAI/releases/download/ai-v1.0.23/JellyRipAIInstaller.exe)
- Release page: [ai-v1.0.23 release](https://github.com/unexpear-softwhere/JellyRipAI/releases/tag/ai-v1.0.23)
- Project site: [unexpear-softwhere.github.io/JellyRipAI](https://unexpear-softwhere.github.io/JellyRipAI/)

## Highlights

### A real assistant

- **Docked chat panel, usable during dialogs.**  The chat reflows the
  layout (no floating window) and stays interactive WHILE the
  identity / duplicate / space-override dialogs are open — those
  dialogs are non-modal now, with the workflow buttons soft-locked so
  a running rip can't desync.
- **Web lookup (Web toggle).**  The assistant searches DuckDuckGo
  (keyless) and TMDB (your own free v3 API key) before answering, with
  a model-formulated query from the disc context and cited source
  links.  Works with the local Ollama model.
- **Sees the disc scan in detail.**  The drive's disc label before a
  full scan, and per-title duration / size / chapters / audio tracks /
  subtitle languages / main-vs-extra after one.

### Bug fixes

- **Bundled FFmpeg / ffprobe is always used.**  A blank configured
  path became `"."` via `os.path.normpath`, which the resolver
  mis-read as a configured directory and then discarded the bundled
  binary — surfacing as "tool not found" on a scan.
- **Drive picker lists the real drive + disc again.**  It referenced a
  removed `MakeMKVDriveInfo` type while the scanner returns
  `MakeMKVDrive`, so the label formatter and the coercion crashed.

### TMDB compliance

- Required attribution notice + the TMDB logo are shown in
  Settings -> AI -> Web lookup and in CREDITS.md.  The TMDB key is
  per-user and never persisted to shipped config.

## What's NOT in this release

The disc-ripping core is unchanged.  This release is the AI assistant
plus two scan-path bug fixes.

## Companion fork: JellyRip MAIN

The non-AI baseline ships the same disc-ripping core without AI
assistance.

- MAIN release page: [v1.0.22 release](https://github.com/unexpear/JellyRip/releases/tag/v1.0.22)
- MAIN project site: [unexpear.github.io/JellyRip](https://unexpear.github.io/JellyRip/)
