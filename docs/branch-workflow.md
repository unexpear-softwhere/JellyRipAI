# Branch Workflow

JellyRip uses a simple trunk-style branch model.

`main` is the trunk branch. It is the source branch for releases, and it
is the default start point for all new non-branch-specific work.

The AI branch is an integration branch for assistant work. It must stay
reconciled against `main`, and non-AI behavior should follow `main`
unless this branch intentionally carries an AI-only or debug-only
difference.

## Rules

- start new non-AI work from `main`
- keep work branches short-lived and scoped to one task
- merge finished work back into `main`
- do not cut releases from topic branches
- do not commit build output such as `dist/`

If a branch depends on another in-flight branch, call that out
explicitly. The default is still to branch from `main`, not from another
topic branch.

## Starting a branch

Use the current `main` branch as the base:

```bash
git switch main
git pull --ff-only origin main
git switch -c fix/short-description
```

This matches Git's documented `git switch -c <new-branch>
[<start-point>]` behavior for creating and switching to a new branch in
one step.

## Merge expectations

Before merging back into `main`:

- run the relevant tests for the change
- keep the diff narrow and task-focused
- update docs when behavior or setup expectations change
- update `CHANGELOG.md` for notable user-facing changes

For this AI branch:

- reconcile against `origin/main` in small slices
- let `main` win for non-AI behavior by default
- preserve AI-only systems, tests, and release-lane naming
- keep debug-only additions gated so default behavior does not change

After merge, other contributors should branch from the updated `main`
branch rather than from the completed topic branch.

## Release note

JellyRip release automation is intentionally tied to `main`. The release
script aborts when run from any other branch, so branch cleanup should
happen before release work starts.
