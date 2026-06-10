# JellyRip AI v1.0.24 Release Notes

JellyRip AI v1.0.24 — a large bug-fix + packaging release: a working
Stop button, a data-loss fix, disc auto-identification via TMDB and
OMDb, a Theme Maker with 9 new themes, and a new app format.

## DOWNLOAD FORMAT CHANGED

The standalone download is now **JellyRipAI-portable.zip** (a folder
you unzip and run) instead of a single `JellyRipAI.exe`. The app now
starts instantly — the old single-exe format unpacked ~600 MB to your
temp folder on every launch. If you previously downloaded the bare
exe, grab the portable zip or the installer this time.

## Download

- Portable: [JellyRipAI-portable.zip](https://github.com/unexpear-softwhere/JellyRipAI/releases/download/ai-v1.0.24/JellyRipAI-portable.zip)
- Installer: [JellyRipAIInstaller.exe](https://github.com/unexpear-softwhere/JellyRipAI/releases/download/ai-v1.0.24/JellyRipAIInstaller.exe)
- Release page: [ai-v1.0.24 release](https://github.com/unexpear-softwhere/JellyRipAI/releases/tag/ai-v1.0.24)
- Project site: [unexpear-softwhere.github.io/JellyRipAI](https://unexpear-softwhere.github.io/JellyRipAI/)

## Highlights

### AI

- **Disc auto-identification**: hit the reload button by the drive bar
  and the assistant looks the disc label up on TMDB (and OMDb when
  both keys are set — OMDb adds the IMDb id only when the two services
  agree on the title). Results post to the chat and the Live Log.
- **TMDB lookups run automatically** whenever a key is saved — no Web
  toggle needed. Optional **OMDb key** field added in Settings → AI.
- The chat's mode selector is now a **model dropdown** listing the
  active provider's usable models.
- **The AI Providers dialog's Test / Save / Set as Active actually
  complete now** — results were silently lost, so keys never saved
  from that dialog. If a key you saved before never "took", re-enter
  it.
- Chat no longer reads live UI state from a worker thread (crash risk
  while chatting mid-rip).

### Themes

- **Theme Maker** — live full-app preview, save, export to a shareable
  `.json`, import themes others made.
- **9 new built-in themes**: Monokai, Rosé Pine, Tokyo Night,
  Catppuccin Mocha, Everforest Dark, Synthwave, Ayu Mirage, IBM
  Carbon, Palenight (plus the Basic Dark/Light starting points).

### Packaging

- **One-DIR app format**: instant launches, nothing written to %TEMP%,
  no leftover `_MEI` folders after crashes. FFmpeg ships once (inside
  `_internal\`); the installer shrinks to ~150 MB and cleans up the old
  staged FFmpeg on upgrade. Unused `ffplay.exe` dropped (~130 MB).

### Critical fixes (shared with MAIN v1.0.24)

- **Stop Session works**; stopping a multi-disc Dump All can no longer
  delete the most recently completed disc's files.
- **Organize Existing MKVs**: real season numbers, safe auto-delete,
  honest verdict on failed moves.
- MakeMKV output decoded as **UTF-8**; the **progress bar** tracks the
  whole rip; moves **validate the staged copy before finalize**;
  truncated "degraded" rips rejected; labeled-disc title-file mapping.
- Title-bar ✕ behaves like Cancel; `crash.log`; updater signature +
  truncation fixes; blank-log-path junk file fixed.

## What's NOT in this release

FFmpeg/HandBrake transcoding remains unwired in the UI (its builder
got real fixes — AMD/Intel GPU support, correct GPU quality flags —
ready for when it lands).

## MAIN

The non-AI app receives the same core fixes, the theme system, and
the new packaging.

- MAIN release page: [v1.0.24 release](https://github.com/unexpear/JellyRip/releases/tag/v1.0.24)
- MAIN project site: [unexpear.github.io/JellyRip](https://unexpear.github.io/JellyRip/)
