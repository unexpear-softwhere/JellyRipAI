# UX Copy and Accessibility Plan

**Status:** Proposed. Findings captured from a 2026-05-02 audit pass
across both branches. This document is the working list of what needs
attention in user-visible strings, contrast, keyboard behavior, and
screen-reader exposure. It does not authorize implementation.

## Motivation

The audit pass surfaced three categories of work:

1. **Accessibility issues with concrete fixes** — contrast, focus
   indicators, error-message recovery copy, keyboard parity. Most are
   small string or theme-constant changes.
2. **Accessibility issues that are framework-limited** — `tk.Canvas`
   based scrollable lists are opaque to screen readers, and tkinter
   on Windows exposes minimal MSAA / UI Automation. These are tracked
   here for completeness but their real fix sits inside the PySide6
   migration ([pyside6-migration-plan.md](pyside6-migration-plan.md)).
3. **Copy quality gaps** — voice inconsistency, ALL-CAPS labels read
   as code, jargon without inline gloss, and AI prompt copy that
   under-leverages the JellyRip context.

The biggest single accessibility lever the project has is the PySide6
migration. The biggest single copy lever is establishing a one-page
voice rule + a glossary so future strings stop drifting, and
tightening the AI prompts.

## Scope

In scope:

- Setup wizard and rip-flow dialogs ([gui/setup_wizard.py](../gui/setup_wizard.py),
  [gui/session_setup_dialog.py](../gui/session_setup_dialog.py))
- Main window toolbar, tabs, settings, log surface, abort flow
  ([gui/main_window.py](../gui/main_window.py))
- AI provider dialog
  ([gui/ai_provider_dialog.py](../gui/ai_provider_dialog.py))
- AI assistant + text-action system prompts
  ([gui/main_window.py:242-256, 3252-3302](../gui/main_window.py))
- AI chat error helpers
  ([gui/main_window.py:271-287](../gui/main_window.py))
- Transcode profile descriptions
  ([transcode/profiles.py](../transcode/profiles.py),
  [transcode/profile_summary.py](../transcode/profile_summary.py))
- Update flow ([gui/update_ui.py](../gui/update_ui.py))
- Live-log strings emitted from controller and engine layers
  ([controller/legacy_compat.py](../controller/legacy_compat.py),
  [controller/session.py](../controller/session.py),
  [engine/](../engine/))
- Color contrast across the dark-GitHub theme palette

Out of scope (until decided separately):

- Localization / `gettext` extraction — the work below assumes a
  single-language product. If localization is ever a goal, every
  hardcoded string flagged here would need extraction first.
- Complete enumeration of every string in
  [gui/main_window.py](../gui/main_window.py) (~7,800 lines).
  The audit sampled systematically; full coverage is a separate
  multi-hour pass.

## Findings — Critical (🔴)

### 1. AI Explain prompt lacks JellyRip context anchor

[gui/main_window.py:3281-3285](../gui/main_window.py) — the system
prompt for the "Explain Selection with AI" feature:

> "You are a helpful assistant inside JellyRip. Explain the selected
> text in plain English. Be concise and useful."

Compared to the rich main assistant prompt at
[gui/main_window.py:242-256](../gui/main_window.py), this prompt is
generic. Users selecting text in JellyRip almost always want a
JellyRip-meaningful explanation (a codec, a MakeMKV term, a Jellyfin
folder convention). The prompt does not steer toward that. This is
the single highest-leverage AI-branch copy fix because Explain is
the most-promoted user-facing AI feature.

**Fix direction:** Rewrite to include a domain anchor. Example:

> "You are an explainer assistant inside JellyRip, a Windows desktop
> app for ripping discs and preparing a Jellyfin library. The user
> has selected text and wants it explained. If the term is related to
> disc ripping, codecs, MakeMKV, FFmpeg, HandBrake, or Jellyfin
> library structure, lean toward that meaning. Default to friendly
> clarity over technical precision. Assume mixed technical depth.
> Keep it short."

### 2. White-on-blue primary buttons fail contrast

> ✅ **Closed 2026-05-02** (minimal fix — full equipable theme system
> deferred to PySide6 migration per decision #7). The AI branch's
> palette already had `_COLORS["accent_button_bg"]` defined (sourced
> from `APP_THEME["blue"]` = `#2b63f2` — measures **~4.94:1** against
> white, passes WCAG). The buttons in
> [gui/setup_wizard.py:474, 481](../gui/setup_wizard.py) were just
> using the wrong palette key (`_ACCENT`, which sources from
> `APP_THEME["title"]` = `#27b8ff`, ~2.19:1 — fails WCAG even worse
> than MAIN's bug). Fix: switched the buttons to use the correct
> existing palette key — `bg=_COLORS["accent_button_bg"]`. Drift-guard
> test in [tests/test_button_contrast.py](../tests/test_button_contrast.py)
> (6 tests): pins the palette structure, computes the WCAG ratio
> programmatically, asserts the prior failing pattern can't return.
> Fix landed in MAIN simultaneously (different shape there — MAIN had
> no palette infrastructure, so a new constant was added there).
> Original finding body retained below.

Movie / TV Show buttons in setup wizard Step 1
([gui/setup_wizard.py:288-301](../gui/setup_wizard.py)) use
`bg=_ACCENT (#58a6ff), fg="white"` at 12pt bold. White on `#58a6ff`
measures **2.5:1**. WCAG 1.4.3 AA requires 4.5:1 for normal text and
3:1 for large text. This fails both.

**Fix options:**
- Darken accent for button backgrounds (e.g., `#1f6feb` measures
  ~4.6:1 with white)
- Invert: white background, blue text

### 3. Flat buttons have no visible focus indicator

> ✅ **Closed 2026-05-02** (synchronized with MAIN, minimal
> `option_add` global default approach). Four `self.option_add()`
> calls in `JellyRipperGUI.__init__` ([gui/main_window.py](../gui/main_window.py))
> install tkinter-wide defaults for the `Button` class. Color values
> sourced from the AI BRANCH theme — `self._theme.get("surface")`
> for `highlightBackground`, `self._theme.get("title")` for
> `highlightColor`. Drift-guard test in
> [tests/test_focus_indicators.py](../tests/test_focus_indicators.py)
> (5 tests).

`relief="flat"` is used on most buttons across the wizard, dialogs,
and toolbar. Tk's default focus border is removed by `flat`, and no
`highlightthickness` / `highlightcolor` is configured. Keyboard users
cannot see which button has focus. WCAG 2.4.7 violation.

**Fix:** Add `highlightthickness=2, highlightbackground=_BG2,
highlightcolor=_ACCENT, takefocus=True` to the standard button style.

### 4. ALL-CAPS classification labels read as code constants

> ✅ **Closed 2026-05-02** (synchronized with MAIN). New
> `_LABEL_DISPLAY` mapping + `_label_display(label)` helper in
> [gui/setup_wizard.py](../gui/setup_wizard.py); display sites route
> through the helper to render `Main` / `Duplicate` / `Extra` /
> `Unknown`. The `_LABEL_COLORS` keys (sourced from
> `gui/theme.py:CLASSIFICATION_LABEL_COLORS`) stay uppercase. The
> "MAIN is pre-selected" subtitle in step 3 also softened to "Main".

`MAIN` / `DUPLICATE` / `EXTRA` / `UNKNOWN` are rendered directly as
user-facing labels in the wizard
([gui/setup_wizard.py:36-40, 212, 414](../gui/setup_wizard.py)).
ALL-CAPS conveys "this is an enum constant" rather than "this is a
category."

**Fix:** Title-case throughout. The hex color map can stay keyed by
upper-case; the display string changes.

### 5. Framework-limited: tkinter screen-reader exposure

`tk.Canvas`-based scrollable lists in setup wizard
([gui/setup_wizard.py:191-247](../gui/setup_wizard.py)) and the AI
provider dialog
([gui/ai_provider_dialog.py:285-301](../gui/ai_provider_dialog.py))
are announced as "Canvas" with no children. Rows, checkboxes, and
provider cards are invisible to Narrator and NVDA. WCAG 4.1.2
violation.

**Fix:** Framework-level. Tracked in
[pyside6-migration-plan.md](pyside6-migration-plan.md). Qt's
`QListView` / `QTreeView` exposes a proper accessibility tree.

## Findings — Major (🟡)

### 6. Step 3 title is jargon

[gui/setup_wizard.py:353](../gui/setup_wizard.py) — "Step 3: Content
Mapping." "Content Mapping" reads as a database term. Candidates:
*Pick Titles*, *Choose What to Rip*, *Select Titles*.

### 7. "ABORT SESSION" is harsh

> ✅ **Closed 2026-05-02** (synchronized with MAIN). All user-visible
> "ABORT" strings in [gui/main_window.py](../gui/main_window.py)
> softened to "Stop": button label, dialog body, log line, and
> in-flight "Stopping..." status. Existing test_imports.py assertions
> updated to pin the new strings.

[gui/main_window.py:1105, 1253, 7541, 7551, 7557](../gui/main_window.py)
and the related `"Abort the current session first"` message at line
6125. Modern convention: *Stop Session* / *Stopping…*. Same applies
to `"ABORT REQUESTED BY USER"` at line 7555 in the log surface.

### 8. AI text-action dialog titles collapse to tool-flavored shorthand

[gui/main_window.py:3250, 3265, 3279, 3292](../gui/main_window.py) —
context menu items are warm ("Fix Spelling & Grammar with AI",
"Explain Selection with AI") but the dialog titles users see *after*
clicking are terse ("AI Spell Check", "AI Explain"). Voice changes
mid-flow.

**Fix:** Match the dialog title to the warmer menu item, or pick a
single voice rule and apply it both places.

### 9. AI provider dialog has no Escape binding

[gui/ai_provider_dialog.py:316](../gui/ai_provider_dialog.py) — the
dialog binds `WM_DELETE_WINDOW` but not `<Escape>`. Setup wizard and
session dialogs all bind Escape. Accessibility convention is that
modal dialogs should always close on Escape. WCAG 2.1.2.

### 10. Error messages identify failure but rarely recovery

> ✅ **Closed 2026-05-03** (synchronized with MAIN). New
> `friendly_error(base_message, exception)` helper in
> [ui/dialogs.py](../ui/dialogs.py) maps caught exception types
> to user-facing recovery text. 17 raw-dump call sites in
> [gui/main_window.py](../gui/main_window.py) converted to use the
> helper. Drift-guard test in
> [tests/test_friendly_error.py](../tests/test_friendly_error.py)
> (18 tests).

Pattern: `f"Could not save expert profile:\n{exc}"` raw-dumps the
exception into a dialog. WCAG 3.3.3 (Error Suggestion) wants a
recovery path. The AI chat error helper at
[gui/main_window.py:271-287](../gui/main_window.py) already does this
for AI errors and is the model to apply to file-system,
configuration, and network errors.

### 11. Config-key names leak into user dialogs

[gui/update_ui.py:251-256](../gui/update_ui.py) — the Update Blocked
dialog body contains:

> "Set opt_update_signer_thumbprint in Settings to your release
> certificate thumbprint before using auto-update."

`opt_update_signer_thumbprint` is a developer-facing config key. The
same dialog earlier already explains the action in plain language
("open Settings → Advanced, and set the 'Update Signer Thumbprint'
field..."), so the config-key paragraph is a redundant developer
leak.

### 12. EXTRA label color collides with muted body text

> ✅ **Closed 2026-05-03** (synchronized with MAIN). Changed
> `CLASSIFICATION_LABEL_COLORS["EXTRA"]` in
> [gui/theme.py](../gui/theme.py) from `DIALOG_THEME["muted"]` (gray)
> to **`#a371f7`** (purple) — distinct from the other label hues and
> from the muted text. Hardcoded rather than added to APP_THEME
> because the existing `APP_THEME["purple"]` (#a400ff) was too
> saturated and failed contrast on the dark blue surface.

[gui/setup_wizard.py:38](../gui/setup_wizard.py) — `"EXTRA": "#8b949e"`
is the same hex as `_FG_DIM` (the muted body-text color). Visually,
the category label "EXTRA" reads as muted body text. Hierarchy
collapses.

### 13. LibreDrive status assumes the term is known

> ✅ **Closed 2026-05-03** (synchronized with MAIN). All three
> LibreDrive status strings in
> [gui/setup_wizard.py](../gui/setup_wizard.py) now carry an inline
> gloss: *"enabled — disc decryption ready"*, *"possible — firmware
> patch may help"*, *"not available — UHD discs may not work"*.

[gui/setup_wizard.py:170-183](../gui/setup_wizard.py) — three
statuses (`enabled` / `possible` / `unavailable`) for a term most
users encounter once.

**Fix:** Inline gloss. "LibreDrive: enabled — disc decryption ready"
/ "LibreDrive: possible — firmware patch may help"  / "LibreDrive:
not available."

### 14. Tab order may not match visual reading order

[gui/setup_wizard.py:288-320](../gui/setup_wizard.py) — Cancel
button is created in a separate `btn_row` packed after the
Movie/TV/Standard buttons. Tab order follows widget creation. Verify
on the live wizard.

### 15. Disc-swap timeout has no in-context extension

Settings → Advanced has a configurable disc-swap timeout, but a user
mid-prompt cannot extend it — the timeout fires regardless. WCAG
2.2.1 (Timing Adjustable) is implicated.

**Fix:** Add a "Wait longer" button to any timed prompt.

### 16. Live-log emits dev-style strings to the user

[controller/legacy_compat.py](../controller/legacy_compat.py) and
related controller modules surface log lines such as:

- `"[Diagnostics][DEBUG] LibreDrive raw: \"{raw}\""`
- `"=" * 44` border separators around section headers (terminal-style)
- `"Auto-title fallback used: '{title}'"`
- `"Custom run-folder override selected — collecting paths."`
- `"Run override — {label}: {chosen}"`
- `"WARNING: {context} timed out; continuing."`

The log panel is a primary user-visible surface. Dev-style log output
undermines product polish.

**Fix:** Two-tier logging — keep developer/debug logs going to file
unconditionally, but route the live-log panel through a "user-style"
filter that either rewrites or suppresses lines tagged as
diagnostic.

### 17. AI assistant system prompt is verbose

[gui/main_window.py:242-256](../gui/main_window.py) — well-written
prompt overall, but mentions "JellyRip" / "the app" / "this desktop
app" multiple times. Models follow context cues; one mention is
enough. Tightening reduces token cost and improves response focus.

### 18. AI rewrite / fix prompts could share a base

[gui/main_window.py:3252-3271](../gui/main_window.py) — Fix and
Rewrite prompts repeat the "...inside JellyRip. ...Return only the
[corrected/rewritten] text." pattern. A small shared base ("You are
an editing assistant inside JellyRip. Return only the [verb] text;
do not add commentary.") would converge the four AI text actions.

## Findings — Minor (🟢)

### 19. Voice inconsistency across dialogs

- Wizard Step 1: passive third-person ("JellyRip has scanned…")
- Dump session: imperative second-person ("Choose how this dump
  session should run.")
- Step 5 subtitle: marketing-voice ("No guessing, no surprises.")

Each is fine in isolation; together they sound like multiple
products.

### 20. Required-field asterisks rely on legend lookup

[gui/session_setup_dialog.py:209, 399](../gui/session_setup_dialog.py)
— "(* required)" + inline `*` markers. Modern convention: mark
optional fields explicitly, or label inline ("Title — required").

### 21. Auto-generated copy minor inconsistency

[gui/session_setup_dialog.py:682, 715](../gui/session_setup_dialog.py)
— "auto-generated timestamp name" / "auto-generated batch folder
name." Both fine; flagged only for consistency review.

### 22. Step numbering skips and reuses across files

The wizard advertises Steps 1, 3, 4, 5 in
[setup_wizard.py](../gui/setup_wizard.py); Step 2 is in
[session_setup_dialog.py](../gui/session_setup_dialog.py). Numbers
work across files but a user only sees them in flow.

### 23. "No guessing, no surprises." oversells given pre-alpha status

[gui/setup_wizard.py:680](../gui/setup_wizard.py) — reassurance copy
that lands harder than the README's honesty about workflow maturity.

### 24. Friendly transcode summary already exists, sitting unused

> ✅ **Closed 2026-05-02**. Wired via opt-in Settings toggle
> ("Show plain-English transcode profile descriptions") rather than
> always-on, so users keep the terse summary by default. New
> `opt_plain_english_profile_summary` defaults to `False` in DEFAULTS;
> `ui/settings.py:summarize_profile` takes a `plain_english=` kwarg
> and dispatches to `profile_summary_readable` with safe fallback to
> `describe_profile` on shape mismatch. Both call sites in
> `gui/main_window.py` read the flag from cfg. Test coverage:
> [tests/test_settings_summarize_profile.py](../tests/test_settings_summarize_profile.py)
> (11 tests). Same fix landed in MAIN. Original finding body retained
> below.

[transcode/profile_summary.py](../transcode/profile_summary.py)
already produces plain-English profile descriptions ("Convert video
to H.265 (smaller files, good quality), balanced quality (CRF 22),
hardware acceleration if available"). It's a half-built feature
never wired into the GUI. The expert-mode summary in
[transcode/profiles.py:242](../transcode/profiles.py)
(`describe_profile`) is codec-jargon-dense.

**Fix:** Wire `profile_summary_readable` to the non-Expert summary
path. Cheapest copy win in the repo because the words already exist.

### 25. `ui_visual_assets_copy/` directories may drift from live UI

[ui_visual_assets_copy/](../ui_visual_assets_copy/) contains older
copies of `gui/main_window.py`, `gui/setup_wizard.py`,
`ui/settings.py`, etc. The AI prompts in particular already exist in
two places — the live `_AI_ASSISTANT_SYSTEM_PROMPT` at
[gui/main_window.py:242](../gui/main_window.py) and
[ui_visual_assets_copy/gui/main_window.py:189](../ui_visual_assets_copy/gui/main_window.py).
If both are being maintained, they will drift over time. The AI
text-action prompts (Fix / Rewrite / Explain / Search) likely have
the same shape.

**Fix:** Confirm whether `ui_visual_assets_copy/` is still
load-bearing. If yes, document its role and link it from the live
file. If no, archive it (move outside the source tree) so it stops
attracting edits.

### 26. AI provider dialog: Test sits before Save in the button row

[gui/ai_provider_dialog.py:608-640](../gui/ai_provider_dialog.py) —
per-provider button row, left-to-right, packs **Test (gray) → Save
(green primary) → Set as Active (blue, cloud only) → Disconnect (red
text, only when credentials exist)**. The color hierarchy is
actually good — Save is clearly primary, Disconnect is clearly
destructive. The position-order question is whether Test belongs
before Save: if `_test_provider` reads the unsaved form values, the
order makes sense (validate before commit); if it reads only saved
credentials, a user filling in a fresh card has to Save before Test
will do anything, making the leftmost button effectively
non-functional on first use.

**Fix:** Verify what `_test_provider` reads. If it tests saved
credentials, swap Test and Save so the first action a user can take
is the one that works on a fresh card. If it tests live form input,
the current order is correct and this can be closed.

## Color Contrast — Computed Ratios

Computed from the actual hex constants in
[gui/setup_wizard.py:26-40](../gui/setup_wizard.py).

| Element | FG | BG | Ratio | Required | Pass |
| --- | --- | --- | --- | --- | --- |
| Body text | `#c9d1d9` | `#0d1117` | 12.3:1 | 4.5 | ✅ |
| Body text | `#c9d1d9` | `#161b22` | 11.7:1 | 4.5 | ✅ |
| Dim text | `#8b949e` | `#0d1117` | 6.3:1 | 4.5 | ✅ |
| Dim text | `#8b949e` | `#161b22` | 6.0:1 | 4.5 | ✅ |
| Accent header | `#58a6ff` | `#161b22` | 7.3:1 | 4.5 | ✅ |
| LibreDrive enabled | `#3fb950` | `#161b22` | 7.2:1 | 4.5 | ✅ |
| LibreDrive possible | `#d29922` | `#161b22` | 7.2:1 | 4.5 | ✅ |
| LibreDrive unavailable | `#f85149` | `#161b22` | 5.6:1 | 4.5 | ✅ |
| `MAIN` label | `#58a6ff` | `#0d1117` | 7.5:1 | 4.5 | ✅ |
| `DUPLICATE` label | `#d29922` | `#0d1117` | 7.6:1 | 4.5 | ✅ |
| `EXTRA` label | `#8b949e` | `#0d1117` | 6.3:1 | 4.5 | ✅ (but see #12) |
| `UNKNOWN` label | `#f0883e` | `#0d1117` | 7.6:1 | 4.5 | ✅ |
| Green primary button | `#FFFFFF` | `#238636` | 4.65:1 | 4.5 | ✅ (just) |
| **Blue primary button** | `#FFFFFF` | `#58a6ff` | **2.5:1** | 4.5 | ❌ |

## Foundation Documents to Consider

These prevent the next 100 strings from drifting further. Both are
proposed-not-required.

### ~~`docs/copy-style.md` (proposed)~~ ✅ landed 2026-05-03 — see [copy-style.md](copy-style.md)

A one-page rule sheet. Suggested content:

- Default to second person, present tense
- Sentence case for buttons and labels (no ALL-CAPS except literal
  acronyms like UHD, HEVC, SHA256)
- No marketing reassurance ("no guessing, no surprises")
- No jargon without inline gloss
- No config-key names in user dialogs
- One product name (`APP_DISPLAY_NAME`), substituted via constant
- Errors say what happened *and* what to try next
- Prefer "Stop" over "Abort"; "Pick" over "Map"; "Choose" over
  "Select" when action is causal

### ~~`docs/glossary.md` (proposed)~~ ✅ landed 2026-05-03 — see [glossary.md](glossary.md)

A canonical short-definition list for the terms users will encounter
without prior background. Suggested first entries:

- LibreDrive — MakeMKV's mode that lets the app read encrypted discs
  directly when the optical drive supports it. Without it, some
  discs cannot be ripped.
- UHD — Ultra-HD Blu-ray (4K resolution discs).
- HEVC / H.265 — A modern video codec; produces smaller files than
  H.264 at similar quality.
- CRF — Constant Rate Factor; a quality target for transcoding.
  Lower numbers mean higher quality and bigger files.
- AAC — A common audio format used in MKV files.
- Main track — The audio track most likely to be the primary one
  (e.g., the original-language stereo mix).
- Burn (subtitles) — Permanently render subtitles into the video,
  rather than as a selectable track.
- Metadata: preserve / drop — Whether existing tags are kept or
  stripped during transcode.
- Main / Duplicate / Extra / Unknown — JellyRip's classifier labels
  for disc titles.

A glossary serves three audiences: the GUI (link from on-hover), the
AI Explain prompt (a domain anchor it can prefer — directly tied to
finding #1 above), and the
[profile_summary.py](../transcode/profile_summary.py) friendly
descriptions.

## Sequencing

Quick wins that do not depend on the PySide6 migration (the order
roughly maps to value-per-effort):

1. AI Explain prompt rewrite (#1) — single-string change with the
   highest direct user-visible impact on AI branch
2. ~~Wire up [profile_summary.py](../transcode/profile_summary.py) (#24)~~ ✅ closed 2026-05-02 (opt-in Settings toggle)
3. ~~Title-case classification labels (#4)~~ ✅ closed 2026-05-02
4. ~~Darken accent for button backgrounds (#2)~~ ✅ closed 2026-05-02 (minimal fix — switched buttons to use existing `_COLORS["accent_button_bg"]` palette key; theme-system version deferred to migration)
5. ~~Add focus indicators to flat buttons (#3)~~ ✅ closed 2026-05-02 (minimal `option_add` global default; full helper refactor deferred to PySide6 migration)
6. ~~Soften "ABORT" → "Stop" across button + dialog body + log (#7)~~ ✅ closed 2026-05-02
7. ~~Drop config-key paragraph from Update Blocked dialog (#11)~~ ✅ closed 2026-05-02 (the dropped paragraph was the same `opt_update_signer_thumbprint` dev-leak as MAIN's #9; mirrored from MAIN)
8. ~~EXTRA label color (#12)~~ ✅ closed 2026-05-03
9. ~~LibreDrive inline gloss (#13)~~ ✅ closed 2026-05-03
10. Bind Escape in AI provider dialog (#9)
11. Tighten main assistant prompt (#17) and converge text-action
    prompts (#18)

Items that wait for PySide6 *(per
[pyside6-migration-plan.md](pyside6-migration-plan.md), the migration
is now Approved direction — MAIN-first per decision #1; AI BRANCH
port follows. These items have a definite future home, not indefinite
Someday, but **AI BRANCH's PySide6 work waits for MAIN's port to
ship**)*:

- Screen-reader exposure of canvas-based lists (#5)
- MSAA / UI Automation in general
- Proper label-for-input association
- Live regions for log streaming
- **Equipable theme system** *(decision #7 — deferred to migration;
  AI BRANCH inherits MAIN's theme infrastructure during AI BRANCH's
  port phase, with AI-specific theme additions layered on top)*
- AI Explain prompt rewrite under Qt's `QTextBrowser` *(per AI
  BRANCH plan's Finding #1, currently flagged as a quick win; could
  alternatively wait for the AI BRANCH port to land it in the new
  framework — open per decision #6)*
- Chat sidebar markdown / code-block / streamed rendering parity

Items that need a separate decision:

- Voice rule in `docs/copy-style.md`
- Glossary in `docs/glossary.md`
- Two-tier log filter (user-style vs developer) (#16)
- Disc-swap timeout extension UX (#15)

## Open Questions

1. Is `docs/copy-style.md` worth committing to before the PySide6
   migration, or do we let the migration force a rewrite?
2. Should the glossary live as `docs/glossary.md`, or as inline
   tooltip strings? Both have trade-offs (single source vs.
   contextual proximity).
3. For the AI Explain prompt rewrite, should the new prompt also
   reference the glossary explicitly so the model can lean on it?
4. Two-tier log filtering — is this worth doing now, or does the
   migration's better log widget make it moot?
5. Verifying tab order across all dialogs is a hands-on task — who
   runs it and against which Windows + Narrator combination.

## Not a Commitment

This document is a tracked plan, not an authorization. Workflow
stabilization, the test-coverage push, AI feature reconciliation,
and shipping v1 take precedence. Items above can be addressed
incrementally — most are isolated string changes — without blocking
on each other or on the larger PySide6 migration.

## Related

- [pyside6-migration-plan.md](pyside6-migration-plan.md) — The
  framework-limited findings (#5, parts of #15) are tracked there.
- [ai-assist-branch.md](ai-assist-branch.md) — AI surface map this
  plan touches in findings #1, #8, #9, #17, #18.
