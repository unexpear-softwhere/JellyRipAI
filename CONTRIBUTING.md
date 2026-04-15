# Contributing to JellyRip

> **This project is still being built.**
> The core workflows exist and mostly work, but the codebase is not stable yet.
> The right contribution right now is **fixes, not features**.

JellyRip is a Windows-first desktop tool for MakeMKV-assisted ripping and Jellyfin library organization.

## What stage are we at?

Pre-alpha. The app runs and rips discs correctly in tested configurations, but:

- edge cases and hardware combinations are still being discovered
- the workflow layer is being hardened as real-world usage surfaces bugs
- test coverage for live-rip paths is incomplete
- the release process is still being standardized

This means the codebase needs **reliability work** more than it needs new capabilities.

## What to contribute

**Good contributions right now:**

- bug fixes for ripping, file handling, or path edge cases
- regression tests for issues that have been reported or fixed
- small correctness improvements that are clearly safe
- documentation corrections or missing setup details
- tester feedback that identifies specific failure modes

**Not the right time for:**

- new ripping modes or UI workflows
- refactors that change behavior across multiple files
- features that require unstable external integrations
- anything that makes the codebase harder for a solo maintainer to follow

If you want to add something larger, open an issue first and describe the problem it solves. Building on a broken foundation is harder than fixing it first.

## Priorities

Fix focus order:

1. ripping correctness and failure handling
2. Windows path safety and file handling
3. regression tests for real user-reported issues
4. Windows UX stability and prompt clarity
5. documentation that helps testers reproduce failure modes

Avoid mixing unrelated refactors with behavior changes unless the refactor is required to fix the issue safely.

## Branch expectations

JellyRip currently has two maintained lines:

- `codex/non-ai-main` - deterministic non-AI baseline work
- `codex/assist-layer-split` - assist, provider, diagnostic, and other
  AI-assisted work

Put baseline ripping and file-move fixes in `codex/non-ai-main` first.
If the same fix is needed in the assist line, port it after the baseline
version is correct.

For anything AI-touched, keep the behavior bounded:

- suggestions must be visible
- suggestions must stay optional
- suggestions must be reversible
- explicit user input must win every time

AI-assisted logic must not silently choose final rip targets, extras,
episode numbering, metadata IDs, or move destinations without a manual
confirmation layer.

## Development setup

Requirements:

- Windows
- Python 3.13+
- MakeMKV installed for live testing
- ffprobe (from ffmpeg)

Install and run:

```bash
pip install -r requirements.txt
python main.py
```

Run tests:

```bash
python -m pytest -q
```

## Repository layout

- `main.py` - current application entrypoint
- `JellyRip.py` - compatibility entrypoint and project map
- `gui/` - tkinter UI layer
- `controller/` - workflow and orchestration layer
- `engine/` - MakeMKV, ffprobe, and file operation layer
- `utils/` - shared helpers and domain utilities
- `tests/` - regression and behavior-guard tests
- `docs/` - project documentation and repo structure notes

## Change expectations

Before opening a PR or preparing a release-quality patch:

- keep changes narrow and task-focused
- preserve current user-visible workflows unless the issue specifically requires a workflow change
- add or update tests for bug fixes whenever practical
- update `CHANGELOG.md` for notable user-facing changes
- update `README.md` or `docs/` when setup, workflows, or expectations change

## Testing guidance

Automated tests are required for logic changes when the behavior can be reproduced without real hardware.

For disc-drive workflows that require manual validation:

- use `TESTERS.md`
- capture the mode used, disc type, drive model, and key logs
- attach the first relevant failure log excerpt when filing an issue

## Commit guidance

The current history uses short conventional-style subjects such as:

- `fix: ...`
- `docs: ...`
- `test: ...`
- `chore: ...`

Keep commit messages short and specific to the user-visible or technical effect.

## Pull request guidance

Include:

- what changed
- why it changed
- how it was tested
- whether the change affects live ripping, unattended flows, or file moves

If a change is risky, call out rollback considerations explicitly.

## Scope notes

JellyRip is not trying to be a general media manager. The repo should stay biased toward:

- reliable disc ingestion
- clear operator prompts
- safe file movement into Jellyfin-compatible folders
- practical Windows distribution through `JellyRip.exe`
