# Tasks

This is the AI branch's task tracking file. The discipline mirrors MAIN's [TASKS.md](../MAIN/TASKS.md) — items get scoped to real file:line references, audits are recorded so future sessions don't repeat assumptions about what the code does, and completed work that pinned a contract gets kept under Done.

The AI branch additionally tracks the AI/provider surface — see [docs/ai-assist-branch.md](docs/ai-assist-branch.md) for the map of what lives only here, and [CLAUDE.md](CLAUDE.md) for the "don't drop AI-layer features when reconciling against `main`" rule that scopes how shared work lands here.

## Active

(empty)

## Waiting On

(empty)

## Someday

- **PySide6 migration of the GUI layer.** Replace tkinter in `gui/` and most of `ui/` with PySide6. The AI branch is the natural starting point — user-visible upside is concentrated here (chat sidebar markdown rendering, provider dialog, AI-driven inline overrides, MKV preview before commit), and the dependency cost is already paid (provider SDKs shipped). Layer 1 (engine, ffprobe) and Layer 2 (classifier, scoring, controller, AI provider registry) are unchanged. Plan, trade-offs, and open questions live in [docs/pyside6-migration-plan.md](docs/pyside6-migration-plan.md). Status: Proposed, not scheduled. Not before workflow stabilization.

- **UX copy and accessibility cleanup.** 2026-05-02 audit found 26 issues across user-visible strings, contrast, focus indicators, screen-reader exposure, and AI prompt copy on this branch. AI-branch-specific items: AI Explain prompt under-leverages the JellyRip context (most-promoted AI feature has the most generic system prompt), AI text-action dialog titles collapse to tool-flavored shorthand, AI provider dialog has no Escape binding, main assistant system prompt is verbose, AI fix/rewrite/explain prompts could share a base, AI provider dialog button workflow order is implicit. Cross-cutting items: white-on-blue primary buttons fail WCAG contrast at 2.5:1, `relief="flat"` buttons have no visible focus indicator, ALL-CAPS classification labels read as code constants. Plan, full findings, contrast measurements, and proposed `docs/copy-style.md` + `docs/glossary.md` foundations live in [docs/ux-copy-and-accessibility-plan.md](docs/ux-copy-and-accessibility-plan.md). Status: Proposed. Quick-win items don't depend on the PySide6 migration; framework-limited items do.

## Done

(empty — cross-branch audit history that pinned shared contracts is recorded in MAIN's [TASKS.md](../MAIN/TASKS.md))
