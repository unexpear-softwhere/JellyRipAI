# JellyRip — Glossary

**Status:** Proposed (2026-05-03). Canonical short definitions for
the terms users encounter in JellyRip without prior background.
Lives next to [copy-style.md](copy-style.md) and is the source of
truth for in-line glosses, hover tooltips (when added), and the
non-technical-user-friendly profile summaries from
[transcode/profile_summary.py](../transcode/profile_summary.py).

## Why this document exists

User-facing text in JellyRip currently uses a mix of MakeMKV,
FFmpeg, Jellyfin, and disc-format jargon. Several of the closed
audit findings (*LibreDrive inline gloss*, *friendly profile
summary*, *title-case classification labels*) leaned on consistent
explanations of these terms — but those explanations were each
written ad-hoc. This file consolidates the canonical wording so
the same term gets the same explanation everywhere. When you
write new user copy that mentions one of these terms, copy the
short definition directly, or use the term's short label and rely
on the user being able to look it up here.

## Terms

### LibreDrive
A MakeMKV-specific mode that lets the app read encrypted discs
directly when the optical drive's hardware supports it. Without
LibreDrive, modern UHD Blu-ray discs typically can't be decrypted
and ripped — so when the status appears as *"not available"*, UHD
discs may not work on this drive even if other formats do.

States the app surfaces:
- *enabled — disc decryption ready*
- *possible — firmware patch may help*
- *not available — UHD discs may not work*

### UHD
**Ultra-HD Blu-ray** — 4K resolution disc format. Distinct from
"4K" alone, which can refer to several things (display resolution,
streaming format, etc.). UHD discs use AACS 2.0 encryption that
requires LibreDrive support to decrypt.

### Blu-ray
1080p HD-resolution disc format. Uses AACS encryption (less
strict than UHD's AACS 2.0). Most LibreDrive-capable drives
handle Blu-ray cleanly.

### DVD
SD-resolution (480p / 576p) disc format. Uses CSS encryption,
which MakeMKV handles without needing LibreDrive support.

### MKV
**Matroska Video** — the container format JellyRip rips to.
Holds video, audio, subtitles, and chapter markers in a single
file. Container-only — doesn't dictate codec choice (MKV files
can contain H.264, H.265, AAC, etc.).

### HEVC / H.265
A modern video codec. Produces smaller files than H.264 at
similar quality. CPU-expensive to encode but inexpensive to
decode on modern hardware. JellyRip's transcode profiles target
H.265 by default for the best quality/size tradeoff.

### H.264
The older mainstream video codec. Universally supported by
hardware decoders, which makes H.264 files broadly compatible.
Produces larger files than H.265 at the same quality.

### CRF
**Constant Rate Factor** — a quality target for transcoding. A
single number from 0 (lossless, huge files) to 51 (very low
quality, tiny files). JellyRip's "balanced" profile uses CRF 22.
Lower numbers = higher quality + bigger files.

### AAC
**Advanced Audio Coding** — a common audio codec used inside
MKV files. Subjective quality is excellent at moderate bitrates
(192-256 kbps); it's the default JellyRip targets when re-encoding
audio.

### Main track
The audio track most likely to be the primary audio for the
content — typically the original-language stereo mix or 5.1
surround. JellyRip's classifier picks the main audio track
based on language tags, channel count, and stream order. When
the classifier guesses wrong, the user can override it via the
session setup dialog.

### Burn (subtitles)
*"Burn"* in this context means **permanently render subtitles
into the video frame**, rather than keeping them as a selectable
track inside the MKV. Burned subtitles can't be turned off; they
become part of the picture. JellyRip defaults to keeping subtitles
as selectable tracks unless the user opts in to burning.

### Forced subtitles
A subtitle track that only appears for **untranslated content** —
e.g., when characters speak a different language briefly inside
an otherwise English film. Most modern Blu-rays include a
"forced" subtitle track separately from the full subtitles. The
forced-only mode in transcode profiles keeps these and drops
the full track.

### Metadata: preserve / drop
Whether existing tags (title, artist, year, etc.) embedded in
the source MKV are kept during transcode. Default: preserve.
Drop is useful when the source has incorrect or stale metadata
that you want stripped before re-tagging in your library.

### Main / Duplicate / Extra / Unknown
JellyRip's classifier labels for disc titles. **Internal** values
are uppercase (`MAIN`, `DUPLICATE`, etc.) — they're enum-style
code constants. **User-facing** rendering is title case: *Main*,
*Duplicate*, *Extra*, *Unknown* (per closed Finding #4).

| Label | What it means |
| --- | --- |
| **Main** | Most likely the primary content (longest title with high quality signals) |
| **Duplicate** | Looks like a near-copy of the Main title — possibly a different aspect ratio, language, or director's cut |
| **Extra** | A short bonus feature — trailer, behind-the-scenes, deleted scene, etc. Usually under 20 minutes |
| **Unknown** | Couldn't classify confidently — the user picks |

### Smart Rip
JellyRip's automated workflow that picks the main feature and
extras for the user — scan disc, classify titles, identify the
movie via TMDB / OpenDB, map content, preview the output plan,
rip. The user confirms each step but doesn't have to make
title-by-title selections.

### Dump All
The opposite of Smart Rip — rips every title to a temp folder
without classification. Used when the user wants to triage the
output manually after the fact, or when the disc has unusual
content the classifier can't make sense of.

### Stabilization (file)
A wait period after a MakeMKV rip completes during which JellyRip
verifies the output file's size has stopped changing. MakeMKV
sometimes finishes the subprocess before all bytes flush to disk;
stabilization catches that. Tunable via
`opt_stabilize_timeout_seconds` and
`opt_stabilize_required_polls`.

### Validation (rip)
The post-rip checks that the output file is structurally sound:
ffprobe parses it, the duration is plausible against the
expected disc duration, and the file size is within the
configured ratio of the expected size. A "validation failure"
means one of these checks failed; a "warning" means a soft
threshold was crossed but the file looks usable.

### Temp folder
Working directory where MakeMKV writes the raw rip before
JellyRip moves files into the library structure. Lives under
`opt_temp_folder` (default: `%APPDATA%\JellyRip\temp` on
Windows). Sessions in temp folders can be resumed if the user
aborts mid-rip.

### Session
One run of the workflow, from disc-scan through final move.
Tracked by a `_rip_meta.json` metadata file in the temp folder.
Failed or aborted sessions can be resumed; completed sessions
get cleaned up (temp files deleted) per `opt_auto_delete_temp`.

### LibreDrive-capable drive
An optical drive whose firmware MakeMKV recognizes as
LibreDrive-compatible. Not every drive qualifies; the LibreDrive
status field reports the verdict for the currently-attached
drive. Some drives can be patched via firmware to gain LibreDrive
capability — that's what the *"firmware patch may help"* state
references.

### Jellyfin-style folder structure
The conventions Jellyfin (and Emby, Kodi) expect for
auto-detection of media libraries:

- Movies: `Movies/Movie Name (Year)/Movie Name (Year).mkv`
- TV: `TV Shows/Show Name (Year)/Season N/Show Name S0NE0M.mkv`
- Extras (Jellyfin): subdirectories like `featurettes`, `behind
  the scenes`, `deleted scenes`, `interviews`, `trailers`, etc.

JellyRip's organize step writes to these paths automatically.
See [Jellyfin's docs](https://jellyfin.org/docs/general/server/media/movies/#extras)
for the canonical extras folder names.

## Maintenance

When you add a new term to user-facing copy:
1. Check this glossary for an existing entry.
2. If absent, add it here with a 2-3 sentence definition.
3. Reference the same wording in your inline gloss / tooltip /
   profile-summary string so the same term gets the same
   explanation everywhere.

When you remove or rename a term in the codebase, update or
remove its glossary entry. A drift-guard test in
`tests/test_label_color_and_libredrive.py` already pins the
LibreDrive inline gloss strings; future term-introductions
should land with similar drift guards if the rule is mechanical
enough to enforce.

## Related

- [copy-style.md](copy-style.md) — the voice and copy conventions
  that govern how these terms get phrased
- [ux-copy-and-accessibility-plan.md](ux-copy-and-accessibility-plan.md)
  — the audit findings that established the need for in-line
  glosses (LibreDrive, friendly profile summary, etc.)
- [transcode/profile_summary.py](../transcode/profile_summary.py)
  — the friendly profile summary helper that uses the H.265 / CRF
  / AAC / Main-track wording from this glossary
- [pyside6-migration-plan.md](pyside6-migration-plan.md) — the
  migration that will surface terms in QSS-themed tooltips and
  HTML/markdown-rendered help text (a richer surface than tk
  currently allows)
