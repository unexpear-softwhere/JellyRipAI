# JellyRip — Copy Style Guide

**Status:** Proposed (2026-05-03). One-page rule sheet for
user-facing copy across dialogs, buttons, labels, and log lines.
Lives next to [ux-copy-and-accessibility-plan.md](ux-copy-and-accessibility-plan.md)
and codifies the patterns the audit's closed findings established.
Future copy should follow these rules to prevent the kinds of
drift that prompted the 2026-05-02 audit (product name in three
forms, ALL-CAPS classification labels, ABORT-button harshness,
config-key leaks into dialogs, raw exception strings as dialog
bodies, etc.).

## Voice

### Person and tense
- **Default to second person, present tense**.
  *"Choose where to save the rip."* — yes.
  *"The user selects a destination."* — no (third person).
  *"You will be prompted to select a destination."* — no (future
  passive).
- Imperative mood is fine for prompts and buttons:
  *"Pick a destination folder."*
- Avoid the royal "we": *"We've finished ripping"* → *"Rip
  complete."*

### Tone
- **Calm and concrete**. The pre-alpha status of the project
  argues for honesty over reassurance. Avoid marketing-flavored
  copy.
- *"Smart Rip — confident pick of the main feature."* — yes.
  *"Smart Rip — no guessing, no surprises."* — no (oversells; the
  classifier *can* be wrong, and when it is, "no surprises" reads
  as a lie).
- Don't apologize repeatedly. Once is plenty:
  *"Couldn't open the destination folder."* — yes.
  *"Sorry, we're really sorry, we couldn't open..."* — no.

### Sentence case for buttons and labels
- *"Open settings"*, *"Stop session"*, *"Pick a folder"* — yes.
- *"Open Settings"* (title case) — also acceptable for proper
  surfaces (window titles, dialog headers).
- **No ALL-CAPS** on classification or category labels.
  *"MAIN"* / *"DUPLICATE"* / *"EXTRA"* / *"UNKNOWN"* read as code
  constants, not categories. Use *Main* / *Duplicate* / *Extra* /
  *Unknown* per closed Finding #4.
- ALL-CAPS allowed for: literal acronyms (*UHD*, *HEVC*, *AAC*,
  *SHA256*, *FFmpeg*), and one-word interjection labels in log
  surfaces where it's a styled visual element (rare —
  most log lines are sentence case).

## What user-facing copy must NOT do

### No marketing reassurance
- ❌ *"No guessing, no surprises."*
- ❌ *"Powered by AI."*
- ❌ *"The smartest way to..."*

### No jargon without inline gloss
- ❌ *"LibreDrive: enabled"* → ✅ *"LibreDrive: enabled — disc
  decryption ready"* (per closed Finding #11)
- ❌ *"ENOSPC"* anywhere user-visible → ✅ *"Out of disk space"*
- ❌ *"H.265 CRF 22"* without explanation in user-facing surfaces
  → ✅ *"H.265 (smaller files, good quality), balanced quality
  (CRF 22)"* (this is the
  [profile_summary.py](../transcode/profile_summary.py)
  friendly-mode pattern)
- See [glossary.md](glossary.md) for the canonical short
  definitions of `LibreDrive`, `UHD`, `HEVC`, `CRF`, `Main`,
  `Duplicate`, `Extra`, `Unknown`, etc. Inline gloss should
  match the glossary's wording so the same term is explained the
  same way everywhere.

### No config-key names in user dialogs
- ❌ *"Set opt_update_signer_thumbprint in Settings to..."*
- ✅ *"Open Settings → Advanced → Update Signer Thumbprint and..."*
- Config keys are developer-facing — they belong in log files,
  README, and config docs, not user-visible dialog bodies. Per
  closed Finding #9.

### No raw exception strings in dialog bodies
- ❌ `messagebox.showerror("Title", f"Could not save:\n{exc}")`
- ✅ `messagebox.showerror("Title", friendly_error("Could not save.",
  exc))` — the helper in
  [ui/dialogs.py](../ui/dialogs.py) maps exception types to
  recovery hints. Raw exception text still goes to the session
  log via `controller.log()`. Per closed Finding #8.

### No three product names
- ❌ *"Jellyfin Raw Ripper"* / *"Raw Jelly Ripper"*
- ✅ Always reference the constant — `APP_DISPLAY_NAME` in
  `shared/runtime.py`. MAIN's value is `"JellyRip"`; AI BRANCH's
  is `"JellyRip AI"`. Per closed Finding #1.
- Drift-guard test in `tests/test_app_display_name.py` enforces
  this — any new hardcoded *"Jellyfin Raw Ripper"* / *"Raw Jelly
  Ripper"* string in `gui/main_window.py` or `main.py` fails
  CI before shipping.

## What user-facing copy MUST do

### Errors say what happened *and* what to try next
WCAG 3.3.3 (Error Suggestion). The
`friendly_error(base, exc)` helper enforces this for messagebox
dialogs by appending recovery text per exception type. For other
error surfaces (log lines, status pills), the same principle
applies:

- ❌ *"Operation failed."*
- ✅ *"Couldn't write the output folder. Check that the destination
  drive isn't full and that you have write access."*

### Action buttons use causal verbs
- *"Stop"* over *"Abort"* (per closed Finding #7 — softer,
  user-controlled connotation; consistent with modern OS
  conventions).
- *"Pick"* over *"Map"* for content selection (audit Finding #6
  — *"Content Mapping"* reads as a database term).
- *"Choose"* over *"Select"* when the action is causal and
  user-driven.
- *"Save"* over *"Persist"* / *"Commit"* in user-visible copy
  (developer terms leak into dialogs occasionally).

### Time and size formatting consistency
- Durations: *"2h 14m"* or *"2:14:00"* — pick one per surface.
  Setup wizard uses `_format_duration`; logs may use either; be
  consistent within a single dialog.
- File sizes: GB to one decimal place (*"4.3 GB"*). MB only for
  files under 1 GB (*"850 MB"*).
- Percentages: integer (*"75%"*) for progress, one decimal
  (*"73.5%"*) for size-validation reports.

### Always-substitute the product name
Every user-visible reference to JellyRip uses
`APP_DISPLAY_NAME` (or the AI BRANCH equivalent), never a
hardcoded literal. F-string substitution is the canonical pattern:

- ✅ `self.title(f"{APP_DISPLAY_NAME} v{__version__}")`
- ✅ `f"{APP_DISPLAY_NAME} - Error"` for dialog titles
- ❌ `"JellyRip - Error"` (hardcoded — drifts when the constant
  changes)

## When to ALL-CAPS

Per the rule above, ALL-CAPS is restricted. Allowed:

- Literal acronyms: *UHD*, *HEVC*, *AAC*, *MKV*, *SHA256*, *DVD*,
  *FFmpeg* (yes, FFmpeg is its own canonical capitalization),
  *PySide6*.
- Section header bands in section dividers (`_section_header`)
  if they're stylistic visual elements with no semantic
  category claim — but per
  [ux-copy-and-accessibility-plan.md](ux-copy-and-accessibility-plan.md)
  Finding #4, the *category* labels (*Main*, *Duplicate*, etc.)
  are NOT this case.
- Status indicators where the convention is established (*OK*,
  *FAIL*) — but prefer sentence case (*Done*, *Failed*) where
  possible.

## When to use which dialog pattern

### `messagebox.showerror`
- Use for **errors that block the user from continuing**.
- Body: `friendly_error(base_message, exception)` — base
  message is what failed, helper appends recovery hint.
- Title: `f"{APP_DISPLAY_NAME} - Error"` or a domain-specific
  title (*"Update Blocked"*, *"Settings Error"*).

### `messagebox.showinfo`
- Use for **completion confirmations** and informational status.
- Body: short, sentence case. (*"Rip complete."*, *"Files moved."*)
- No exclamation points unless the user just performed a
  long-running success (*"Rip complete!"* — earned punctuation;
  *"Files moved!"* — overwrought).

### `ask_yes_no` (from `ui/dialogs.py`)
- Use for **destructive confirmations** and major branching choices.
- Question form: *"Stop the current session first, or wait for
  it to finish?"*, *"Replace existing files?"*

### Log lines (`controller.log()`)
- More technical than dialog bodies.
- Config keys are OK here (logs are developer-facing for
  debugging).
- Prefer sentence case unless the line is a status banner where
  ALL-CAPS is established convention.

## Glossary anchor

When a user-visible string introduces a term that needs
explanation, the in-line gloss should match the wording in
[glossary.md](glossary.md). This keeps explanations consistent
across surfaces (the LibreDrive status strings, the
`profile_summary.py` friendly summaries, hover tooltips when
they exist, etc.).

## Drift guards in tests

Several of these rules are enforced by drift-guard tests so
violations fail CI rather than ship:

- `tests/test_app_display_name.py` — no legacy product-name
  variants in source
- `tests/test_button_contrast.py` — no `bg=_ACCENT, fg="white"`
  pattern (failing-WCAG buttons)
- `tests/test_focus_indicators.py` — `option_add` calls present
- `tests/test_label_color_and_libredrive.py` — EXTRA label color
  distinct from `_FG_DIM`; LibreDrive status strings have inline
  glosses
- `tests/test_friendly_error.py` — raw exception text doesn't
  leak into dialog bodies; recovery text is type-appropriate
- `tests/test_security_hardening.py` — Update Blocked dialog
  doesn't leak the `opt_update_signer_thumbprint` config-key name

When adding new user-visible copy, write a similar drift-guard
test if the rule is mechanical enough to enforce.

## Not a commitment

This document is a working style guide, not a contract. Update
when:

- A new closed finding establishes a rule worth codifying here
- The PySide6 migration introduces Qt-specific copy conventions
  (e.g., status bar messages, `QMessageBox` usage patterns) that
  should be standardized
- A user-research signal indicates a current rule isn't serving
  users well

## Related

- [ux-copy-and-accessibility-plan.md](ux-copy-and-accessibility-plan.md)
  — the audit findings these rules came from
- [glossary.md](glossary.md) — canonical term definitions
- [pyside6-migration-plan.md](pyside6-migration-plan.md) — the
  migration that will move tkinter-specific patterns
  (`messagebox.show*`, `option_add`, etc.) onto Qt equivalents
  (`QMessageBox`, QSS, etc.)
