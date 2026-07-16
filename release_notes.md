# JellyRip AI v1.0.28 Release Notes

JellyRip AI v1.0.28 — more say over what gets ripped.  Each title in the
picker now has an audio dropdown so you can keep only the tracks you want,
the AI chat sidebar uses messenger-style bubbles, Tab-to-number works
again while labelling episodes, and MakeMKV runs as 64-bit when it can.

## Download

- Portable: [JellyRipAI-portable.zip](https://github.com/unexpear-softwhere/JellyRipAI/releases/download/ai-v1.0.28/JellyRipAI-portable.zip)
- Installer: [JellyRipAIInstaller.exe](https://github.com/unexpear-softwhere/JellyRipAI/releases/download/ai-v1.0.28/JellyRipAIInstaller.exe)
- Release page: [ai-v1.0.28 release](https://github.com/unexpear-softwhere/JellyRipAI/releases/tag/ai-v1.0.28)
- Project site: [unexpear-softwhere.github.io/JellyRipAI](https://unexpear-softwhere.github.io/JellyRipAI/)

## Added

- **Per-title audio track selection.**  Every title in the "Select Titles
  to Rip" picker now has an audio dropdown listing the tracks the scan
  found.  Keep them all (the default — a normal rip) or untick the ones
  you don't want; trimmed titles are remuxed after the rip to drop the
  unchecked tracks.  If a trim ever fails, the full all-tracks file is
  kept, so a rip can never be lost to it.

## Changed

- **The AI chat sidebar uses chat bubbles.**  The assistant transcript is
  now rounded messenger-style bubbles — your messages on the right, the
  assistant's on the left, each with the sender's name above it — and they
  follow the active theme's colours instead of a flat text panel.
- **MakeMKV runs 64-bit.**  When a 64-bit `makemkvcon64.exe` sits next to
  a configured 32-bit `makemkvcon.exe`, JellyRip AI uses the 64-bit one
  automatically, so MakeMKV stops warning that the 32-bit build is
  deprecated.

## Fixed

- **Tab moves down the picker again.**  While numbering episodes you can
  press Tab to jump straight to the next row's number/name cell and keep
  typing — a regression introduced by adding the audio column.

## Baseline: JellyRip (non-AI)

JellyRip AI tracks the non-AI JellyRip line and ships the same changes
plus its assistant layer (chat sidebar, AI providers, and disc
auto-identification).

- MAIN release page: [v1.0.28 release](https://github.com/unexpear/JellyRip/releases/tag/v1.0.28)
- MAIN project site: [unexpear.github.io/JellyRip](https://unexpear.github.io/JellyRip/)
