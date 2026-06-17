# JellyRip AI v1.0.26 Release Notes

JellyRip AI v1.0.26 — a workflow + UI pass on the TV ripping flow, plus
more TV disc-identification providers.  Watch a title before you rip it,
name and number episodes right in the disc picker (the old post-rip
prompts are gone), plus a tactile polish pass across the UI.

## Download

- Portable: [JellyRipAI-portable.zip](https://github.com/unexpear-softwhere/JellyRipAI/releases/download/ai-v1.0.26/JellyRipAI-portable.zip)
- Installer: [JellyRipAIInstaller.exe](https://github.com/unexpear-softwhere/JellyRipAI/releases/download/ai-v1.0.26/JellyRipAIInstaller.exe)
- Release page: [ai-v1.0.26 release](https://github.com/unexpear-softwhere/JellyRipAI/releases/tag/ai-v1.0.26)
- Project site: [unexpear-softwhere.github.io/JellyRipAI](https://unexpear-softwhere.github.io/JellyRipAI/)

## Added

- **Watch a title before ripping.** "Watch in VLC" in the disc picker
  rips the selected title to a temporary file and plays it, so you can
  confirm what a title is before committing to a full rip.
- **Name and number episodes in the picker.** Editable **Ep #** and
  **Episode name** columns, with each title's length and size beside it.
  A title left without a number is filed as an extra.
- **Cut / Copy / Paste in the picker's editable cells** (right-click
  menu; Ctrl+C / V / X also work).
- **TVmaze and TheTVDB TV disc identification.** Identify TV discs via
  TVmaze (keyless) and TheTVDB (API key + PIN), alongside TMDB / OMDb.

## Changed

- **The post-rip Episode Numbers / Episode Names prompts are gone.** A
  TV rip builds its plan straight from the picker.  Duplicate-number and
  existing-file safety checks still run, ending in a move preview.
- **TV picker and file lists sort by title number**, and each title
  shows both "Title N" and MakeMKV's real output filename.

## Polish

- **Tactile UI pass.** Press and focus states on buttons, pointing-hand
  cursors on clickable controls, and hover/selected states on inputs and
  rows — derived per-theme, so every built-in and custom theme gets it.

## Baseline: JellyRip (non-AI)

JellyRip AI tracks the non-AI JellyRip line and ships the same workflow
+ UI changes plus its assistant layer (chat sidebar, AI providers, and
disc auto-identification).

- MAIN release page: [v1.0.26 release](https://github.com/unexpear/JellyRip/releases/tag/v1.0.26)
- MAIN project site: [unexpear.github.io/JellyRip](https://unexpear.github.io/JellyRip/)
