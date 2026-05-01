# JellyRip (AI branch) — Working Memory

## Project
Windows-first desktop app: rips discs with MakeMKV, validates with ffprobe, organizes into a Jellyfin-friendly library. Pre-alpha. This is the **AI branch** — same product as `main`, but with the assistant/provider layer kept and extended. Current line `ai-v1.0.18`. Python 3.13+, tkinter UI, distributed as `JellyRipAI.exe` (+ optional installer).

Owner: GitHub `unexpear`. License: GPLv3.

## Layout
- `main.py` — entrypoint
- `gui/` — tkinter UI (includes `ai_provider_dialog.py`, `ai_chat_sidebar.*`)
- `ui/` — UI adapters / dialogs / settings
- `controller/` — workflow orchestration, session lifecycle
- `core/` — pipeline + media scan
- `engine/` — MakeMKV, ffprobe, file ops (rip_ops, scan_ops, ripper_engine)
- `transcode/` — ffmpeg/HandBrake planning, queue, recommendations
- `utils/` — helpers, parsing, classifier, updater, state machine
- `shared/` — runtime, events, windows_exec, ai_diagnostics, ai_chat_*, ai_profile
- `shared/ai/` — provider registry, credential storage, provider adapters (Claude, OpenAI, Gemini, local)
- `tests/` — pytest suite (includes AI-specific tests: provider, chat sidebar, profile, etc.)
- `config.py` — settings
- `docs/architecture.md`, `docs/repository-layout.md`, `docs/ai-assist-branch.md`, `FEATURE_MAP.md`

## Workflow status (per README)
- TV Disc, Movie Disc, Dump All — some testing
- Organize Existing MKVs — not tested
- FFmpeg / HandBrake transcoding — not tested
- AI assistant features — under active reconciliation

## Quality bar
- `python -m pytest` — full suite green; the focused regression block (behavior_guards + AI/provider/session/imports/etc.) ran 481 passed last verified
- `pyright` strict mode — large amount of type noise inherited from `main`; use as a *trend*, not a gate
- `release.bat <version>` is the AI release pipeline (`ai-v*` tag lane); refuses dirty tree or non-`ai` branch

## Conventions
- Windows-first; bash via Git Bash, PowerShell available
- Explicit binary paths preferred; PATH lookup is opt-in via Settings → Advanced
- Config at `%APPDATA%\JellyRip\config.json`
- Build artifacts under `dist/ai/`, git-ignored
- AI release tags use `ai-v*` prefix; main release tags use `v*`. Do not mix lanes.

## Working preferences
- **Local writes are fine; no git updates without explicit go-ahead.** Don't commit, push, tag, or run `release.bat` unless I say so. Editing files locally is OK.
- **No git worktrees.** Work directly in this directory. Do not create extra worktrees, and if a `.claude/worktrees/<name>` folder appears, remove it. From the repository root:
  ```bash
  git worktree remove --force .claude/worktrees/<name>
  git branch -D claude/<name>
  ```
  If files are locked because another session has them open, close that session and remove any leftover folder with `Remove-Item -Recurse -Force .claude/worktrees/<name>` from PowerShell. `.claude/worktrees/` is in `.gitignore` so accidental appearances will not be committed.
- **Don't drop AI-layer features when reconciling against `main`.** `main` deliberately strips the AI/provider layer. Treat the in-flight diff as the slice; flag scope concerns rather than recommending reverts.

## Current focus
(empty — update as we work)
