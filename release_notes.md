# JellyRip AI v1.0.22 Release Notes

JellyRip AI v1.0.22 — deep audit cleanup.  Mirrors MAIN's v1.0.22
across the engine + settings UI, plus AI-fork-specific security
hardening, a new AI Settings tab, and a long list of small fixes.

## Download

- Direct download: [JellyRipAI.exe](https://github.com/unexpear-softwhere/JellyRipAI/releases/download/ai-v1.0.22/JellyRipAI.exe)
- Installer: [JellyRipAIInstaller.exe](https://github.com/unexpear-softwhere/JellyRipAI/releases/download/ai-v1.0.22/JellyRipAIInstaller.exe)
- Release page: [ai-v1.0.22 release](https://github.com/unexpear-softwhere/JellyRipAI/releases/tag/ai-v1.0.22)
- Project site: [unexpear-softwhere.github.io/JellyRipAI](https://unexpear-softwhere.github.io/JellyRipAI/)

## Highlights

### Security

- **Gemini API key moved from URL to header.**  Previously embedded
  as `?key=...` in the URL, leaked into HTTPError strings, GUI
  log, and `ai_chat_replay.jsonl`.  Now sent as the documented
  `x-goog-api-key` header.
- **`credential_store` warns on plaintext fallback + 0o600 on POSIX.**
  Was a silent plaintext-on-disk fallback if DPAPI failed or on
  non-Windows.  Now emits a one-shot WARNING and sets file mode
  0o600 (was 0o644 / world-readable on POSIX).
- **`ai_chat_replay.jsonl` now scrubs API keys + rotates at 5 MB.**
  Six regex patterns redact common provider key shapes.  File
  rotates to `.jsonl.1` at 5 MB so it can't grow without bound.
- **Quota detector no longer false-fires on "token".**  Was
  triggering 5-minute provider cooldowns on unrelated auth
  failures like "invalid token from bad_key".

### Bug fixes (AI-side)

- **"Set as Active" actually persists now.**  The AI Provider
  dialog's cfg lookup used `parent.cfg` but MainWindow stores it
  as `parent._cfg`.  Set-Active was updating the runtime
  diagnostics manager but not saving to disk.  One-char fix.
- **Clicking "Check for Updates" no longer crashes.**  The
  utility chip was importing `tools.update_check` (file missing
  on AI).  Ported the stub from MAIN.
- **Local provider availability check now 200ms instead of 5s.**
  Was a 5-second HTTP timeout that froze the UI on every error
  event when Ollama wasn't running.  Now a TCP-only probe.
- **Drive-probe waits now abort-aware.**  Stop button responds
  within ~250ms during a probe instead of waiting out the full
  backoff (up to 40s in worst case).
- **`connect_single_provider` now merges instead of replacing.**
  Saving just a model preference no longer wipes the previously-
  saved API key.

### New

- **AI Settings tab.**  About 14 previously hidden `opt_ai_*`
  config knobs (cloud/local timeouts, max calls per session,
  failure thresholds, local model name, diagnostics toggles) now
  editable from Settings → AI tab.  Internal state keys remain
  managed by the AI Providers dialog + chat sidebar (no
  two-source-of-truth UX confusion).
- **Pages docs site navigation now works.**  Same baseurl fix as
  MAIN — all documentation links from the landing page now
  resolve.

### Engine improvements (same as MAIN's v1.0.22)

- `stabilize_timeout` now a real deadline.
- ffprobe cache key normcased on Windows.
- 5 magic numbers named as constants.
- `print()` routed through logging.
- ffprobe duration return type unified.
- `_move_extras_to_categories` returns bool, callers flag
  partial sessions appropriately.
- Dead `scan_disc` delegate removed.
- 24 mojibake sites cleaned in engine (was actually fixed
  in ai-v1.0.20 — this release cleans 14 more in tests/).
- Engine→controller layer violation removed.
- TINFO parser tid leak defensively reset.

### Provider stack

- Default Gemini model bumped to `gemini-2.5-flash`.
- Drive-probe defaults harmonized with MAIN (5 retries / 2s base
  backoff, now in DEFAULTS dict so Settings can surface them).

### Tests

- All MAIN test-suite changes (3 reconstructions + 2 security
  reframes) mirrored where applicable.
- 14 mojibake sites in `tests/test_behavior_guards.py` cleaned.

## What's NOT in this release

No new AI features or workflow changes.  The chat sidebar, AI
provider dialog, diagnostics routing, and on-device fallback
behave identically to ai-v1.0.21.  Existing users with a saved
Gemini configuration may want to re-test connection after
upgrading (the key now flows through a header, not the URL).

## Companion fork: JellyRip MAIN

The non-AI baseline ships the same disc-ripping core without AI
assistance.

- MAIN release page: [v1.0.22 release](https://github.com/unexpear/JellyRip/releases/tag/v1.0.22)
- MAIN project site: [unexpear.github.io/JellyRip](https://unexpear.github.io/JellyRip/)
