# PySide6 Migration Plan

**Status:** Approved direction (2026-05-02). MAIN-first per
decision #1 — AI BRANCH stays tkinter until MAIN's port is proven.
v1-blocking per decision #4. The eight open questions originally
listed below now have answers — see
[Decisions Captured 2026-05-02](#decisions-captured-2026-05-02).
AI-branch-specific decisions (chat sidebar parity etc.) are deferred
to the AI BRANCH port phase, which follows MAIN.

## Why

The current GUI layer is built on tkinter. [gui/main_window.py](../gui/main_window.py)
is the single largest source file in the repository (~7,800 lines), and
most of that bulk is layout, binding, and `tk.after()` polling glue
rather than product logic. Several capabilities the product wants are
either impossible or fragile under tkinter:

- **MKV preview before commit.** Confirming a selected title is the
  intended movie or episode before writing 30+ GB to disk requires a
  video widget tkinter does not provide. PySide6's `QtMultimedia`
  (`QMediaPlayer`) plays MKV directly. This is the single largest
  direct Jellyfin-outcome win available from a UI change, because the
  failure mode it prevents is *the wrong title gets ripped*.
- **AI chat sidebar UX.** The sidebar in
  [gui/main_window.py](../gui/main_window.py) renders streamed AI
  responses inside a tkinter `Text` widget with manual tag-config for
  formatting. PySide6's `QTextBrowser` + `QTextDocument` render
  markdown, code blocks, and inline links natively. The current
  ceiling on chat UX quality is the widget, not the model. See
  [ai-assist-branch.md](ai-assist-branch.md) for the full chat
  surface this affects.
- **Progress and stall visibility.** Multi-stage progress (scan →
  identify → rip → stabilize → validate → move → transcode →
  organize), per-task progress in a queue view, native Windows taskbar
  progress, and inline stall warnings on the bar itself are natural
  in Qt and cobbled-together in tkinter. Stall detection already
  exists in config; surfacing it well in the UI changes whether users
  notice and intervene before a hung MakeMKV writes a half-disc.
- **HiDPI on Windows.** Tkinter's mixed-DPI multi-monitor behavior is
  a known fight. Qt handles it natively.
- **Threading model.** MakeMKV streaming today flows through
  `tk.after()` polling against a queue. Qt replaces this with
  `QThread.signal -> slot` dispatch. Hundreds of lines of polling glue
  collapse to direct signal connections. The same pattern applies to
  AI provider streaming responses, which today flow through a similar
  queue + poll mechanism.
- **Native integrations.** Tray notifications on rip completion,
  drag-and-drop, splitter widgets, native file dialogs, dock panels,
  rich tooltips — all are built-in and thematically consistent in Qt.

## What changes

The migration touches the presentation layer only. In layered terms:

- **Layer 1** (MakeMKV, ffprobe, FFmpeg, HandBrake) — unchanged.
  [engine/ripper_engine.py](../engine/ripper_engine.py) and the
  scan/rip parsers are untouched.
- **Layer 2** (parsing, classifier, scoring, confidence) — unchanged.
  [utils/classifier.py](../utils/classifier.py),
  [utils/scoring.py](../utils/scoring.py), and the controller
  workflow in [controller/](../controller/) keep their contracts.
- **AI provider layer** ([shared/ai/](../shared/ai/)) — unchanged.
  Provider registry, credential storage, and adapters keep their
  contracts. Only the chat sidebar renderer and the provider dialog
  surface change.
- **Layer 3** (UI surface) — replaced.

Concretely, the files in scope:

- All of [gui/](../gui/) is rewritten. Per-screen modules replace the
  `~7,800` line `main_window.py`. `secure_tk.py` deletes — its job is
  to wrap tkinter for safety and Qt has its own safety patterns.
  [gui/ai_chat_sidebar.*](../gui/) and
  [gui/ai_provider_dialog.py](../gui/ai_provider_dialog.py) are
  rewritten as Qt widgets.
- Most of [ui/](../ui/) — the UI adapters and dialog helpers are
  coupled to the tkinter event model and need new equivalents.
- The dark GitHub theme moves from tkinter color palette wiring to a
  QSS stylesheet. This is a code reduction, not a rewrite.
- The threading + event glue around MakeMKV streaming and AI
  provider streaming flips from `tk.after()` polling to Qt signals.

## What does not change

The architecture vision continues to apply: this is a Layer 3
investment, not a rewrite of the decision engine. Specifically:

- The "decision engine over MakeMKV" thesis is unchanged.
- The classifier, scoring, and confidence engine continue to live in
  `utils/`.
- The session workflow (scan -> classify -> setup -> rip -> transcode
  -> organize) is unchanged.
- All MakeMKV / ffprobe / FFmpeg integration is unchanged.
- Provider adapters, credential storage, and the diagnostic bus
  ([shared/ai_diagnostics.py](../shared/ai_diagnostics.py)) keep
  their contracts.

The behavior-first test layer (`test_behavior_guards.py`,
`test_imports.py`, `test_parsing.py`, plus the AI-specific tests in
[tests/](../tests/)) survives the migration, since those tests cover
non-UI contracts. UI-touching tests (limited today because tkinter is
hard to test) gain `pytest-qt` as the canonical harness — `qtbot.click`,
`qtbot.waitSignal`, headless via the offscreen platform plugin. This
is a net coverage gain, especially for
[tests/test_ai_chat_sidebar.py](../tests/test_ai_chat_sidebar.py)
and [tests/test_ai_provider_dialog.py](../tests/test_ai_provider_dialog.py)
which currently have to fight tkinter to assert on rendered output.

## Trade-offs

| Dimension | Today (tkinter) | After (PySide6) |
| --- | --- | --- |
| Bundle size | small | +80 to 150 MB |
| Cold launch | near-instant | a few hundred ms |
| Resident memory | ~30-60 MB | ~150-300 MB |
| HiDPI / native feel | fight | free |
| MKV preview | impossible | trivial |
| AI chat rendering | tkinter `Text` + tag-config | `QTextBrowser` + markdown |
| Threading glue | hundreds of `.after()` lines | native signal/slot |
| UI test ceiling | "can't really test" | `pytest-qt` real coverage |
| PyInstaller spec complexity | moderate | higher (Qt plugins) |
| SmartScreen false-positive surface | already an issue | larger binary, same signing path |
| Tester recognition of UI | familiar | effectively a new app |

## Risk: timing relative to pre-alpha

The README documents that TV / Movie / Dump All workflows are at "some
testing" maturity and that Organize Existing MKVs and FFmpeg /
HandBrake transcoding are "not tested." AI assistant features are
under active reconciliation. Migrating the UI while workflow logic is
still stabilizing produces two unstable surfaces at once. Bug triage
gets harder because failures can be attributed either to a workflow
change or to a port miss.

The conservative sequencing is: lock workflow behavior first, then
port the UI. The aggressive sequencing is: port now and use the new
UI to drive workflow stabilization (with the cost of having to
re-validate everything in the new framework).

This document does not pick a sequence. It records the trade-off so
the choice is explicit.

## Branch positioning

The AI branch is the better starting point for the migration, for
three reasons:

1. **The user-visible upside is concentrated here.** Chat sidebar
   markdown, provider dialog, AI-driven inline overrides, streamed
   response rendering — all weighted toward AI branch product
   features.
2. **The dependency cost is already paid.** AI branch ships with the
   Anthropic SDK and provider-side dependencies. The marginal cost
   of adding PySide6 is small relative to the existing dependency
   tree.
3. **It does not destabilize MAIN.** MAIN's "stdlib-only" stance can
   be evaluated separately, after the migration is proved on AI
   branch.

The reverse direction — MAIN first — is also defensible if MAIN's
"lean branch" identity is no longer load-bearing. That is a product
positioning question rather than a technical one.

## Open questions

These need answers before scheduling, not before proposing.

1. **Scope.** AI branch first, MAIN only, or both at once.
2. **Timing.** Before or after workflow stabilization. The pre-alpha
   risk argument cuts toward "after."
3. **Single-shot vs incremental.** Replace the whole UI in one
   release vs run tkinter and Qt screens side-by-side behind a flag
   during the transition.
4. **MKV preview as a forcing function.** If MKV preview is treated
   as a required feature for v1, the migration moves from "Someday"
   to a v1 dependency.
5. **Test rewrite policy.** Adopt `pytest-qt` for new UI tests; do
   existing tkinter-touching tests get rewritten or deleted in
   place.
6. **Chat sidebar parity.** Match current behavior exactly during
   the port, or use the migration as an opportunity to redesign the
   chat surface.
7. **Theme parity.** QSS stylesheet that matches the current dark
   GitHub theme exactly, or take the migration as an opportunity to
   refresh the visual language.
8. **Distribution.** PyInstaller bundle structure, Inno Setup
   installer changes, and the SmartScreen story for a larger binary.

## Concrete capabilities unlocked

For reference when sizing the payoff:

- MKV preview before commit (catch wrong-title-selected before disk write)
- Per-task queue view with independent progress bars
- Multi-stage breadcrumb showing scan -> rip -> validate -> ...
- Native Windows taskbar progress overlay
- System tray completion notifications
- Stall warnings rendered on the progress bar itself
- Cancel button wired natively to thread interruption
- Markdown / code-block / streamed rendering in the AI chat sidebar
- Naming and folder-structure preview pane (what Jellyfin will see)
- Confidence sliders and inline override UI for classifier decisions
- Real HiDPI behavior on Windows multi-monitor setups

## Not a commitment

> **Update 2026-05-02**: This section is preserved for historical
> context, but the eight open questions have now been answered — see
> [Decisions Captured 2026-05-02](#decisions-captured-2026-05-02)
> below. The migration is approved direction. **MAIN goes first per
> decision #1**; AI BRANCH stays tkinter until MAIN's port ships.
> AI-branch-specific decisions (chat sidebar parity, AI Explain
> rewrite under Qt) revisit then.

This document is a planning artifact. It does not authorize the
migration, set a date, or change current priorities. Workflow
stabilization, the test-coverage push, AI feature reconciliation,
and shipping v1 take precedence. Update this document when the open
questions are answered.

## Decisions Captured 2026-05-02

The eight open questions above received answers in a session on
2026-05-02. They are recorded here so the AI BRANCH plan stays
synchronized with MAIN's. **Decisions apply across both branches**;
the only branch-specific implication is sequencing (decision #1).

| # | Question | Decision | Implication for AI BRANCH |
| --- | --- | --- | --- |
| 1 | Scope | **MAIN first** | AI BRANCH stays tkinter until MAIN's port is proven. AI-branch-specific findings (Explain prompt rewrite, chat sidebar redesign, provider dialog port, etc.) wait for MAIN's port to land. |
| 2 | Timing | **After workflow stabilization** | Same gate as MAIN. AI BRANCH port follows. |
| 3 | Single-shot vs incremental | **Single-shot. tkinter only where impossible — not just hard.** | Applies to AI BRANCH port too — when AI BRANCH port begins, it's a complete release, not a hybrid. |
| 4 | MKV preview as forcing function | **Yes — v1-blocking** | v1 ship requires PySide6 (because MKV preview requires it). AI BRANCH v1.x or equivalent depends on MAIN's port shipping first. |
| 5 | Test rewrite policy | **pytest-qt for new UI tests; existing tkinter-touching tests get rewritten or deleted in place** | AI-specific tests (test_ai_chat_sidebar.py, test_ai_provider_dialog.py) gain pytest-qt during AI BRANCH port. |
| 6 | Chat sidebar parity (AI branch) | **N/A in MAIN-first phase. Revisit when AI BRANCH port begins.** | Decision deferred — chat sidebar redesign happens at AI BRANCH port time, informed by what worked in MAIN's port. |
| 7 | Theme parity | **Refresh — equipable theme system from day 1, 2-3 themes initial** | Theme system lands in MAIN port. AI BRANCH inherits the same theme infrastructure during its port — same QSS files, same theme registry. AI-branch-specific theme additions (chat sidebar accents, etc.) layer on top. |
| 8 | SmartScreen / signing | **SignPath.io OSS program (free, no strings, best fit for OSS)** | Single SignPath project covers both branches' release artifacts. Tracked separately in [code-signing-plan.md](code-signing-plan.md) on MAIN. |

**Status update flowing from these decisions:**
- This plan moves from `Status: Proposed` to `Status: Approved direction, MAIN-first, gated on workflow stabilization`.
- AI BRANCH PySide6 work is **deferred until MAIN's port lands** — no AI BRANCH PySide6 code in flight, no scoping work yet.
- AI BRANCH framework-limited items in [ux-copy-and-accessibility-plan.md](ux-copy-and-accessibility-plan.md) inherit the same MAIN-first sequencing.

**What's still NOT decided** for AI BRANCH:
- Specific AI BRANCH port timing relative to MAIN's release (right after, or with a gap to gather feedback)
- Whether AI Explain rewrite happens during the port or as a separate pre-port AI-branch quick-win
- Chat sidebar redesign vs parity — answers when AI BRANCH port begins, per decision #6
