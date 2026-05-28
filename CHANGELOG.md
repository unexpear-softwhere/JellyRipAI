# Changelog

<!-- markdownlint-disable MD013 -->

## [1.0.22] - 2026-05-28

Audit-driven cleanup release.  Mirrors MAIN's v1.0.22 cleanup
across the engine and settings UI, plus AI-fork-specific security
hardening and a new AI Settings tab.

### Security

- **Gemini API key moved from URL to ``x-goog-api-key`` header.**
  ``GeminiProvider._generate`` and ``._chat`` used to embed the key
  in the URL as ``?key={api_key}``.  On HTTPError, the URL with the
  key leaked into ``str(e)`` → ConnectionResult.error → the GUI log
  pane + session.ai.log + ai_chat_replay.jsonl.  Header auth keeps
  the key out of error strings, proxies, and access logs.

- **``credential_store`` warns loudly on plaintext fallback +
  chmods 0o600 on POSIX.**  Was a silent plaintext-on-disk
  fallback if DPAPI failed or on non-Windows.  Now emits a one-
  shot WARNING-level log line per process.  POSIX file mode now
  set to 0o600 instead of the umask default of 0o644
  (world-readable).

- **``ai_chat_replay.jsonl`` scrubs API keys + rotates at 5 MB.**
  Was unredacted and unbounded.  Now applies six regex patterns
  to every string value (Anthropic ``sk-ant-api...``, OpenAI
  ``sk-proj-``/``sk-``, Google ``AIza``, Bearer tokens,
  ``x-goog-api-key`` header values) and replaces with
  ``[REDACTED:provider]`` tokens.  File rotates to ``.jsonl.1`` at
  5 MB.

- **Quota-error pattern tightened.**  ``_QUOTA_ERROR_PATTERNS`` no
  longer includes bare ``"token"`` — that was matching auth
  failures like ``"invalid token from bad_key"`` and triggering
  the 5-minute cooldown spuriously.  Replaced with specific
  phrases (``"token_limit"``, ``"token quota"``, ``"out of tokens"``).

### Fixed

- **"Set as Active" actually persists ``opt_ai_mode`` now.**
  ``AIProviderDialog._apply_parent_mode`` looked up
  ``parent.cfg`` but MainWindow stores it as ``parent._cfg``
  (private-by-convention).  The cfg-write was skipped silently;
  only the runtime diagnostics manager was updated.  One-char fix.

- **``tools/update_check.py`` stub ported.**  Clicking
  "Check for Updates" on the Utility menu used to raise
  ``ModuleNotFoundError`` (swallowed to log-pane).  MAIN has had
  the stub since Phase 3h; AI BRANCH was missing it.  Ported with
  the AI fork's releases URL.

- **Local provider availability now 200ms TCP probe, not 5s HTTP.**
  ``LocalProvider.is_available`` called ``_get_available_models``
  via HTTP with a 5-second timeout, freezing the UI on every
  diagnostic error event when Ollama wasn't running.  Now a bare
  TCP connect — typically <5ms when up, ~200ms when not.

- **Drive-probe backoff is abort-aware.**  ``_wait_for_drive_ready``
  used bare ``time.sleep(delay)``; clicking Stop during a probe
  could be ignored for up to 40 seconds.  Ported MAIN's
  ``_sleep_with_abort`` helper that polls abort_event every
  0.25s, so Stop responds within ~250ms.

- **``connect_single_provider`` now merges instead of replacing.**
  Calling it with ``model="opus"`` after a save with ``api_key="..."``
  used to wipe the api_key.  Now preserves previously saved fields.

- **Same engine-layer fixes as MAIN.**  All of: ``stabilize_timeout``
  now a real deadline (was single 1s sleep); ffprobe cache key
  normcased; bare ``print()`` routed through logging; ffprobe
  duration return type unified to float; ``_move_extras_to_categories``
  bool semantics with caller wiring; 24 mojibake sites in
  ``engine/ripper_engine.py`` cleaned (was actually fixed in
  v1.0.20 — this release cleans the remaining 14 sites in tests/
  test_behavior_guards.py).

- **Engine→controller layer violation removed.**  ``engine/
  ripper_engine.py`` was importing ``build_movie_main_filename``
  from ``controller.naming`` — inverting the engine/controller
  dependency order.  Inlined the 2-line filename construction at
  the one use site.

- **TINFO parser tid leak defensively reset.**  ``get_disc_size``
  could carry tid across loop iterations if a TINFO line had too
  few parts.  Today benign because of an adjacent gate, but a
  future edit could expose the latent bug.  Now ``tid = None``
  at the top of every iteration.

### Added

- **AI Settings tab.**  About 14 previously-hidden ``opt_ai_*``
  config knobs (diagnostics toggles, cloud/local timeouts, max
  calls per session, failure thresholds, local model name) are
  now editable from a new "AI" tab in the Settings dialog.  Sits
  between Reliability and Appearance.  Internal state keys
  (active provider, sidebar width, profile state) remain managed
  by the AI Providers dialog and chat sidebar.

- **Drive-probe defaults added to DEFAULTS dict.**
  ``opt_drive_probe_retries=5`` and
  ``opt_drive_probe_backoff_seconds=2.0`` — previously
  in-code-only fallbacks invisible to the Settings UI.
  Harmonized with MAIN.

- **5 named constants in ``engine/ripper_engine.py``** replacing
  4 magic numbers (same as MAIN: raw-line cap, ambiguity threshold,
  scan-cache TTL, log-rollover threshold).

- **``opt_disc_presence_probe_seconds`` added to DEFAULTS.**
  Same fix as MAIN — was read with a hardcoded fallback but
  missing from the master schema.

### Changed

- **Default Gemini model bumped to ``gemini-2.5-flash``** (was
  ``gemini-2.0-flash``).  Matches the price/perf tier shift we
  did for Claude in v1.0.20.

- **Same engine/UI ergonomics as MAIN's v1.0.22.**  Settings tab
  persist failures now log; utility chip dispatch failures
  surface in 3 places; Appearance tab live-apply/cancel-restore
  swallows now log; chat_controller's mode-change save_config
  failure now logs (was silent).

- **Pages baseurl set** so docs nav resolves correctly.

### Removed

- **Dead ``scan_disc`` delegate** (same as MAIN).
- **Dead resume-scaffolding NOT removed on AI** (MAIN's version
  was already dead; AI's is still wired up via ``check_resume`` —
  feature kept).
- **Unused ``import json``** in ``controller/controller.py``.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>

## [1.0.21] - 2026-05-08

Audit-driven cleanup release.  Provider-stack consistency fix
(Gemini default lined up with what we did for Claude in v1.0.20),
plus repository hygiene.

### Changed

- Default Gemini model bumped from `gemini-2.0-flash` to
  `gemini-2.5-flash` — matches the price/perf tier shift we did for
  Claude in v1.0.20 (defaulting to the current generation rather
  than the previous one).  `gemini-2.0-flash` and
  `gemini-2.0-flash-lite` stay in the dropdown for users who want
  them explicitly.  Pricing table in `gui_qt/dialogs/ai_provider.py`
  already had `gemini-2.5-flash` so no change there.
- `shared/ai/credential_store.py` docstring example updated to
  match the new Gemini default.
- README "Main Workflows" — added "(some testing)" / "(not tested)"
  qualifiers to match MAIN's honesty about test status.  Same code
  in both forks; status should match.
- `pyproject.toml` keywords updated: dropped `tkinter` (retired
  alongside MAIN's Phase 3h), added `pyside6`, `qt`, `claude`,
  `ai`.

### Removed

- `gui_qt/qss/warm.qss` — empty 0-byte placeholder, same cleanup
  MAIN did in v1.0.21.

### Repo hygiene

- `dashboard.html` added to `.gitignore` defensively — file doesn't
  exist on AI side today, but if it ever lands it must not get
  tracked alongside the gitignored `CLAUDE.md`.
- `*.tmp` added to `.gitignore` so stray scratch artifacts like
  `origin_main_controller.tmp` don't surface on `git add -A`.

## [1.0.20] - 2026-05-08

Bug-fix and repo-hygiene release.  Ships the Claude provider model-ID
fix that ai-v1.0.19 missed, plus a public documentation site and
README rewrite for the two-repo fork model.

### Fixed

- Claude provider model identifiers realigned against the live
  Anthropic lineup.  The defaults shipped in ai-v1.0.19 were stale or
  never released:
  - `claude-opus-4-6`         → `claude-opus-4-7`
  - `claude-sonnet-4-20250514`→ `claude-sonnet-4-6`
  - `claude-haiku-4-5-20251001` unchanged (was already correct)

  Default model for new installs flips from Sonnet 4 (May 2025) to
  Sonnet 4.6.  Existing users with a saved Claude config pointing at
  an old ID will see API errors on first connection-test; re-save the
  credentials and the dialog falls back to the new default.

### Added

- GitHub Pages site published at
  [unexpear-softwhere.github.io/JellyRipAI](https://unexpear-softwhere.github.io/JellyRipAI/).
  Cayman theme, source = `main` branch / `docs/` folder.  All eight
  files under `docs/` (already curated for public consumption) ship
  to the site as-is.
- `docs/index.md` landing page with download CTA, AI-feature blurb,
  link set to top-level project info, and cross-link back to the
  non-AI baseline fork.

### Changed

- `README.md` "Active Branches" → "Active Forks".  Previous text
  described a legacy single-repo two-branch layout (`main` + `ai` on
  one origin) which hasn't been accurate since the AI fork moved to
  its own repository (`unexpear-softwhere/JellyRipAI`) and the legacy
  repo (`unexpear/JellyRipAI-old`) was archived.  Anyone following
  the old clone instructions ran `git switch --track origin/ai` and
  silently landed on the non-AI baseline.
- `README.md` "From source" — clones from the AI fork repo directly,
  drops the obsolete branch-switching step, points users without AI
  needs at the non-AI baseline.

### Removed

- `ui_visual_assets_copy/` — visual-asset reference snapshot of the
  retired tkinter UI (9 files, ~9000 lines including the 6700-line
  `main_window.py` mirror).  Untracked; kept locally.

## [1.0.19] - 2026-05-04

Phase 4 — the AI BRANCH PySide6 port. AI BRANCH inherits MAIN's Qt UI
foundation (`gui_qt/`) and ports the AI Provider Setup dialog onto
it. Chat sidebar still ships on tkinter for this release; the Qt port
is scheduled as Phase 4b polish work.

### Added

- `gui_qt/` package — full Qt UI foundation grafted from MAIN. Six
  switchable themes (`dark_github`, `light_inverted`, `dracula_light`,
  `hc_dark`, `slate`, `frost`) generated from a shared token table by
  `tools/build_qss.py`. Setup wizard, status bar, log pane, tray
  icon, splash screen, toolbar, drive scanner, MKV preview dialog —
  all the same code as MAIN.
- `gui_qt/dialogs/ai_provider.py` — Qt port of
  `gui/ai_provider_dialog.py`. New "✦ AI Providers" toolbar chip
  opens it. Same UX surface as the tkinter original: provider cards,
  API key field with show/hide, model selector with pricing label,
  Test/Save/Set Active/Disconnect buttons, status state machine,
  setup-hint flow with browser hand-off.
- `shared/wizard_types.py` and `shared/session_setup_types.py`
  lifted into AI BRANCH so wizard dataclasses live in a Qt-agnostic
  home.
- `tools/build_qss.py` — copied from MAIN. Regenerates the QSS theme
  files when tokens change.
- `requirements-dev.txt` pinning `pyinstaller>=6`, `PySide6>=6.5`,
  `pytest>=7`, `pytest-qt>=4`.
- `tests/test_pyside6_ai_provider_dialog.py` — 9 widget-level tests
  covering construction, header copy, API-key vs URL field by
  category, status state machine, toolbar wiring, and the
  open-entry-point return code.

### Changed

- `main.py` rewritten to launch the Qt UI via `gui_qt.app.run_qt_app`,
  with the same splash + "Loading interface..." pattern as MAIN.
- `JellyRip.spec` adds `gui_qt` + PySide6 hidden imports, bundles the
  six QSS theme files, and adds the AI provider abstraction
  (`shared.ai`, `shared.workflow_history`, etc.) to the explicit
  hidden-imports list.
- `requirements.txt` lists PySide6 alongside the existing `anthropic`
  pin.
- `shared/runtime.py` bumped to `__version__ = "1.0.19"` and gained
  the Qt-related defaults inherited from MAIN: `opt_pyside6_theme`,
  `opt_log_color_levels`, `opt_log_glyph_prefix`,
  `opt_drive_state_glyph`, `opt_tray_icon_enabled`, `opt_show_splash`.
  AI-specific keys (`opt_ai_*`, `APP_DISPLAY_NAME = "JellyRip AI"`,
  `APP_AUMID = "JellyRip.AI.1"`, etc.) are preserved unchanged.
- `tests/test_ai_provider_dialog.py` re-pointed at
  `gui_qt.dialogs.ai_provider`. Pure helpers
  (`_sort_models_by_power`, `_classify_connection_error`,
  `_resolve_local_model_selection`) were lifted verbatim, so the
  state-machine tests for `_handle_save_result` survived intact.

### Inherited from MAIN's 1.0.19

- MakeMKV `-r` (robot mode) on every invocation so live progress is
  parseable.
- `engine.run_job(on_log=, on_progress=)` keyword forwarding so the
  GUI log + progress bar stay updated through the rip.
- `WorkflowLauncher` runs `engine.validate_tools()` pre-flight on
  every disc-touching workflow click — missing `makemkvcon` shows a
  friendly "Required Tool Not Found" dialog instead of `[Errno 2]`.
- `SessionStateMachine.cancel(reason)` + `was_cancelled` flag so a
  user-cancelled session no longer reports "completed successfully"
  in the done dialog.
- `gui_qt.theme.load_theme` catches `OSError` and `UnicodeDecodeError`
  and raises `FileNotFoundError` with the available-themes hint, so
  a corrupt or locked `.qss` file can no longer crash startup.

### Phase 4b — chat sidebar (added 2026-05-05)

End-to-end Qt chat now ships.  Type into the sidebar input, click
Send, the configured provider responds, the answer renders with
markdown.  No tkinter dependency on the chat path.

#### Backend (`gui_qt/chat_controller.py`)

- `ChatController(QObject)` — Qt-native chat backend.  Owns history
  + busy state.  Provider call runs on a daemon worker thread;
  results land on the GUI thread via Qt signals (no `after()`
  polling, no `threading.Event` polling).
- Provider resolution honors `opt_ai_mode`
  (`"cloud"`/`"local"`/`"off"`), `opt_ai_cloud_enabled`,
  `opt_ai_local_enabled`.  Default `"cloud"` mode tries cloud
  first, falls back to local rather than refusing the request.
  `is_available()` failures count as not configured.
- Friendly error formatting for the four common cases: timeouts
  (cloud vs local), 401 auth, 429/quota, and a length-truncated
  fallback for unknown errors so the chat bubble doesn't bloat
  with a stack trace.
- Failed turns don't pollute the history list — a network error
  or rate limit doesn't poison the next prompt's context.
- Best-effort `shared.workflow_history.append_workflow_event`
  log on every successful response so existing diagnostics keep
  visibility.
- Welcome message on first sidebar open (idempotent — re-opens
  skip it).
- 17 controller tests in
  `tests/test_pyside6_chat_controller.py` covering signal wiring,
  empty-prompt guard, success path, error path (history-pollution
  guard), New Chat, Copy Chat (with + without empty transcript),
  Suggest Next Step canned prompt, all four provider-resolution
  modes, and the four friendly-error categories.

#### Shell (`gui_qt/ai_chat_sidebar.py`)

- `gui_qt/ai_chat_sidebar.py` — new Qt sidebar widget. `QDockWidget`
  the user can detach / re-dock, with a `QTextBrowser` transcript
  (markdown rendering — code fences, bold, links all work natively
  vs tkinter's literal-text rendering), `QPlainTextEdit` input,
  and the four action buttons (Suggest Next Step / New Chat / Copy
  Chat / Send). Same widget surface as the tkinter sidebar
  embedded in `gui/main_window.py` (~320 references in 10,700
  lines), with cleaner Qt-native theming via objectName selectors.
- New "☰ Chat" toolbar chip toggles the sidebar dock. Lazy
  construction — the `QTextBrowser` cost only lands when the user
  opens the chat.
- Public Qt signals (`message_submitted`, `suggest_requested`,
  `new_chat_requested`, `copy_chat_requested`, `closed`) and slots
  (`append_user_message`, `append_assistant_message`,
  `set_status`, `set_busy`, `clear_transcript`,
  `clear_input`) define the contract a future controller hook
  attaches to.
- Plain Enter submits, Shift+Enter inserts a newline.
- 18 widget tests in
  `tests/test_pyside6_ai_chat_sidebar.py` pin the shell contract.

### Phase 4b backend wired (2026-05-05 evening)

- `gui_qt/chat_controller.py` now drives the sidebar end-to-end.
  Send button + Enter both fire the provider call; markdown renders
  in the transcript; busy state pulses correctly.
- `gui_qt/app.py` constructs the controller after the MainWindow,
  shows the welcome message, and holds the reference on the window
  so the sidebar survives across the app's lifetime.

### On-device fallback wired (added 2026-05-05 evening)

- ``prompt_looks_like_ui_help`` / ``looks_like_ai_payload_echo`` /
  ``build_ui_help_fallback`` lifted into ``gui_qt/chat_controller.py``
  alongside the ``_QUOTA_ERROR_PATTERNS`` constant.  Per-toolkit
  copies for now; the tkinter copies retire when ``gui/`` does.
- New ``MainWindow.get_chat_ui_snapshot()`` method snapshots the
  live UI state (status, drive, ai_mode, abort_button_state,
  progress_percent, live_log_tail) into a dict the fallback
  helper consumes.
- Worker now hits three fallback paths:
    - Provider unavailable AND prompt is UI-help → on-device
      summary instead of "no provider configured" error.
    - Provider returns the request payload verbatim (some smaller
      models do this) → on-device summary instead of gibberish.
    - Provider errors on a UI-help prompt → on-device summary
      instead of the bare error.
- Successful fallbacks render with backend label ``"fallback"`` so
  workflow-history events distinguish them from real provider
  responses.
- 8 new tests in ``tests/test_pyside6_chat_controller.py`` cover
  the pure helpers + the three worker paths + the snapshot helper.

### Tkinter retirement (added 2026-05-05 evening)

The legacy tkinter UI is retired across AI BRANCH's live import
surface — same shape as MAIN's Phase 3h retirement.  Qt is now the
only path.

#### Removed

- `gui/main_window.py` (10,700 lines), `gui/setup_wizard.py`,
  `gui/session_setup_dialog.py`, `gui/secure_tk.py`, `gui/theme.py`,
  `gui/update_ui.py`, `gui/ai_provider_dialog.py`, `gui/__init__.py`.
  The entire `gui/` directory is gone.
- `pyinstaller_tk_runtime_hook.py` — the runtime hook that bootstrapped
  tkinter at PyInstaller startup is no longer needed.
- Tkinter-coupled tests deleted: `test_ai_chat_sidebar.py` (1,302
  lines, all tkinter-bound; pure helpers covered by
  `test_pyside6_chat_controller.py`), `test_ai_profile.py`,
  `test_session_setup_dialog.py`, `test_setup_wizard.py`,
  `test_theme.py`, `test_main_status_indicator.py`,
  `test_drive_enumeration.py`, `test_label_color_and_libredrive.py`,
  `test_main_window_formatters.py`.
- `JellyRip.py` no longer exports `JellyRipperGUI` (the tkinter
  shell class) — the compatibility shim now exposes only
  toolkit-agnostic helpers.

#### Changed

- `JellyRip.spec` no longer bundles `tkinter`, `_tkinter`,
  `tkinter.ttk`, `tkinter.messagebox`, `tkinter.filedialog`, or
  `tkinter.simpledialog` as hidden imports.  The Tcl/Tk runtime
  hook is removed from `runtime_hooks=[]`.
- Tests that pulled `ContentSelection` / `ExtrasAssignment` /
  `OutputPlan` / `JELLYFIN_EXTRAS_CATEGORIES` from the deleted
  `gui.setup_wizard` now import from `shared.wizard_types`.  Tests
  that pulled `MovieSessionSetup` / `TVSessionSetup` /
  `DumpSessionSetup` from `gui.session_setup_dialog` now import
  from `shared.session_setup_types`.  `controller/controller.py`
  three deferred-imports inside `RipperController` similarly flipped.
- `tests/test_button_contrast.py` and `tests/test_focus_indicators.py`
  replaced with the empty-tombstone versions from MAIN's Phase 3h —
  the underlying tkinter constants are gone, and per-theme WCAG
  contrast is pinned by `tests/test_pyside6_themes.py` instead.

#### Kept (defensive)

- `_bootstrap_tk_paths` in `main.py` and
  `_configure_tcl_tk_environment` in `JellyRip.spec` — same
  belt-and-suspenders pattern MAIN keeps.  PyInstaller's bundled
  Python still ships Tcl/Tk by default; pointing TCL_LIBRARY /
  TK_LIBRARY at the right place keeps a stray third-party
  `import tkinter` from killing startup.

### Replay logging wired (added 2026-05-05 evening)

- Chat controller now writes the same JSONL replay records that
  tkinter ``_record_ai_chat_replay`` writes — every turn produces
  a correlated ``request`` / ``response`` / ``error`` triple
  keyed by a UUID ``replay_id``, in
  ``%APPDATA%\\JellyRipAI\\ai_chat_replay.jsonl``.
- Phase contract matches the tkinter side so existing replay
  bundle viewers parse Qt-emitted records unchanged.
- Fallback responses (no provider, payload echo, provider error
  on UI-help prompt) are recorded with ``backend="fallback"`` and
  a ``reason`` key in details so debug runs distinguish them from
  real provider responses.
- Replay-write failures are swallowed — the chat path never
  breaks because debug logging did.
- 7 new tests in ``tests/test_pyside6_chat_controller.py`` cover
  the request/response/error/fallback record shapes plus the
  best-effort failure path.

### Still deferred (post-v1.0.19, intentional MVP scope)

- **AIChatMemory integration** — long conversations would benefit
  from the memory module's summarization + pinned-facts handling.
  The Qt controller keeps a flat in-memory history list for now.
- **Streaming responses** — the `BaseProvider.chat()` interface
  is synchronous (`-> str`).  Adding streaming is a provider-
  interface refactor that affects the diagnostics + summarize
  paths too; tracked as a separate effort.

## [1.0.18] - 2026-04-19

### Changed

- Carried the AI release lane forward to `1.0.18` across runtime, installer, docs, tester worksheet, and release notes.
- AI builds now stage `JellyRipAI.exe`, `JellyRipAIInstaller.exe`, bundled FFmpeg binaries, and notice files under `dist/ai`.
- The AI update and packaging lane remains isolated with `ai-v*` tags and JellyRip AI app identities.

### Fixed

- Startup no longer loads an adjacent app-directory `.env` file before opening the GUI.
- Manual movie-disc runs now preserve the selected edition in controller state, temp metadata, and destination folder naming.
- Signed-update cleanup and completion notifications now launch PowerShell from the trusted Windows system location.
- The signed-update block path now shows the intended GUI error instead of throwing a callback arity exception.
- Verification fallback retries no longer report a rejected transcode as completed before the retry starts.

## [1.0.17] - 2026-04-15

### Changed

- Carried the AI release lane forward to `1.0.17` across runtime, installer, docs, tester worksheet, and release notes.
- Kept the dedicated AI artifacts and update channel in place with `JellyRipAI.exe`, `JellyRipAIInstaller.exe`, and `ai-v*` release tags.
- Release builds now prefer the FFmpeg full build provided at `%USERPROFILE%\Desktop\ffmpeg` before falling back to other bundle search roots.

## [1.0.16] - 2026-04-13

### Changed

- Improved FFmpeg abort handling, copy-progress logging, and transcode validation for more reliable encode and packaging flows.
- Restored the richer `JellyRip.spec` release configuration so packaged builds consistently carry version metadata, bundled FFmpeg assets, and runtime dependencies.
- Aligned release metadata on the `1.0.16` line across the app runtime, installer, docs, tester worksheet, and release notes.
- Carved out an AI-only release lane with separate `JellyRipAI.exe` and `JellyRipAIInstaller.exe` artifacts, an `ai-v*` GitHub tag channel, and isolated AI app/install identities so packaged AI builds do not collide with the main line.

### Fixed

- Build output policy is back in sync with the docs: release executables stay in GitHub Releases instead of being tracked in the repository.
- In-app update checks now follow the newest published release, including unstable prereleases, so the updater no longer falls back to the older stable line.

## [1.0.15] - 2026-04-11

### Fixed

- Restored a visible top-level `ABORT SESSION` control so the user can abort a running task even when the inline prompt bar is hidden.
- Fixed log auto-follow behavior by checking whether the log was already near the bottom before appending new text.

## [1.0.14] - 2026-04-11

### Added

- Added plain controller boundary modules for session paths, rip validation/retry policy, session recovery, and TV library scanning.
- Added direct unit coverage for those extracted modules so the logic can be tested without importing Tk or the legacy controller mixin.
- Added a release consistency guard that checks version alignment and prevents the root `JellyRip.exe` binary from being tracked in git.

### Changed

- Shrank `controller/legacy_compat.py` by turning session path, rip validation, session recovery, and library scan helpers into thin compatibility wrappers.
- Moved resume selection prompt-model logic out of `SessionHelpers.check_resume` while keeping the UI yes/no callback at the edge.
- Kept the full behavior-guard suite passing while expanding the test suite to cover 250 checks.

## [1.0.13] - 2026-04-09

### Added

- FFmpeg source handling now offers two user-facing modes: `Safe (Copy First)` and `Fast (Read Original)`, with plain-language explanations in Settings and the queue builder.

### Changed

- The current working release line is now aligned as `1.0.13`, with `1.0.12` left as the previous git revision point.
- FFmpeg queue jobs and one-click recommendations now carry the selected source-handling mode through to logs and execution.

### Fixed

- `JellyRip.exe` now embeds Windows file and product version metadata so the next build shows a clean application version in Explorer.
- Release metadata files (`README`, release notes, installer version, and release script examples) are synchronized for the `1.0.13` line.

## [1.0.12] - 2026-04-04

### Changed

- In-app updater now launches `JellyRipInstaller.exe` in silent in-place update mode (`/VERYSILENT /CLOSEAPPLICATIONS /NORESTART`) and falls back to normal launch if silent invocation fails.
- Inno Setup installer is now explicitly configured for update flows (`UsePreviousAppDir`, `CloseApplications`, `CloseApplicationsFilter`, `RestartApplications=no`) so reinstalling acts as an updater.
- Build and release scripts now build via `JellyRip.spec` instead of ad-hoc CLI flags, ensuring release artifacts consistently include the same runtime hooks and bundled dependencies as tested builds.

### Fixed

- Movie resume path now always uses a fresh temp rip folder instead of reusing the previous session folder, preventing `_purge_rip_target_files` from deleting previously successful MKVs during a retry.
- Smart Rip now exits immediately when abort is triggered during metadata prompts (title/year/metadata), avoiding fallback-name/0000-year continuation after user abort.
- Auto-title fallback is now informational log output only and no longer appears as a session warning/failure summary line.

## [1.0.11] - 2026-04-03

### Added

- CINFO disc-level parsing: scan now extracts disc title, language code, language name, and volume ID from MakeMKV output.
- `--minlength` scan filter: new `opt_minlength_seconds` setting (Advanced → MakeMKV) tells MakeMKV to skip titles shorter than the configured threshold during scan.
- Jellyfin metadata ID prompts: Smart Rip, Manual Disc, and Organize flows now ask for an optional TMDB/IMDB/TVDB ID. Folder names get Jellyfin-compatible tags like `[tmdbid-603]` or `[imdbid-tt1375666]`.
- `parse_metadata_id()` accepts flexible input formats: `tmdb:12345`, `tmdb-12345`, `tt1234567`, `tvdb:79168`, or bare integers (assumed TMDB).
- `build_movie_folder_name()` and `build_tv_folder_name()` centralize Jellyfin-style folder naming with optional metadata tags.
- 34 new tests for the naming module (180 total tests passing).

### Changed

- `build_fallback_title()` now prefers CINFO disc name over per-title TINFO name when the disc name is available and non-generic.
- Generic disc name detection expanded to catch both "Title NN" and "Title_NN" patterns plus "Disc"-prefixed names.

### Fixed

- Fixed 73 mojibake em dashes in controller.py (triple-encoded UTF-8 bytes replaced with proper U+2014).

## [1.0.10] - 2026-04-02

### Added

- Added repository-standard project files: `CONTRIBUTING.md`, `SECURITY.md`, `pyproject.toml`, and a Windows GitHub Actions test workflow.
- Added `docs/architecture.md` and `docs/repository-layout.md` to explain the app's layered design and flat-layout rationale.
- Added settings for optional prompt auto-timeouts and optional unattended disc-swap timeouts.
- Added unified tool resolver layer (`resolve_tool`, `resolve_makemkvcon`, `resolve_ffprobe`) with resolution order: saved config → common install paths → PATH environment variable.
- Added `validate_makemkvcon` and `validate_ffprobe` helpers that run a live probe command and return a success flag and error message.
- Added `should_keep_current_tool_path` safeguard: a working saved tool path is never replaced by an unvalidated new path.
- Added `tests/test_config_tools.py` with 5 regression tests covering resolver order and the overwrite-guard rule.

### Changed

- Reworked repository documentation to better match the expectations of a maintained small Windows desktop app project.
- Updated preview-related tests so pytest never launches a real media player during local or CI test runs.
- Settings save flow now validates new tool paths before accepting them; rejects silently-broken replacements and logs the rejection reason.
- Engine `validate_tools` now routes both MakeMKV and ffprobe through the resolver layer so PATH installs and custom locations are found automatically.
- Multi-disc dump mode renamed from "Unattended" to "Dump All" in UI labels and log messages for clarity.
- Extras selection changed from a yes/no keep-all toggle to a multi-select picker so individual extra titles can be deselected.

### Fixed

- Multi-disc dump flow now pauses with an explicit between-disc confirmation prompt and no longer times out by default while waiting for user swap actions.
- Unrecognized discs during multi-disc dump can now be advanced manually (bypass) or stopped instead of being forced into retry-only behavior.
- GUI prompt timeout behaviour is now configurable via Settings → Advanced → Interactive Timeouts instead of being a hard-coded 300-second safety value.

## [1.0.9] - 2026-03-29

### Windows UX + path hardening

- Removed browse UI flows for now (Settings browse buttons and inline input browse) to keep path entry deterministic while Windows dialog stability work is deferred.
- Added `CREATE_NO_WINDOW` flags to Windows subprocess calls to stop black console-window flashes during MakeMKV/ffprobe/PowerShell operations.
- Update installer launch now uses `os.startfile(...)` with a pre-launch UAC notice so elevation is handed off to Windows correctly.
- Added Windows reserved-name and empty-name guards in `clean_name` (e.g. `NUL`, `COM1`) to avoid invalid or dangerous filenames.
- Added Windows-friendly path validation using probe-write checks (instead of `os.access(..., os.W_OK)`) and drive-letter-agnostic system-path blocking.
- Added long-path (`\\?\\`) file-I/O handling in the engine for move/copy/log/metadata operations so deep TV paths do not fail at 260 characters.

### Docs / release metadata sync

- README build instructions now use `main.py`, matching the current packaged entrypoint.
- Release notes now include both `JellyRip.exe` and `JellyRipInstaller.exe` download links for `v1.0.9` and `latest`.
- Documentation now states explicitly that generated `dist/` binaries are published through GitHub Releases and are not committed to the repository.

### Safety / Deadlock fixes (gui/main_window.py)

- **`ask_yesno`**: the wait loop now checks `abort_event` and applies a 300-second safety timeout, matching the existing guard in `ask_input`. Previously the loop was unbounded — if the `_abort_watch` thread failed to fire `done`, the worker thread would hang forever.
- **`ask_input` race condition**: added `self._input_lock = threading.Lock()` (initialised in `__init__`). Every call to `ask_input` acquires this lock before touching the shared `_input_result`/`_input_event` state, serialising concurrent prompts and eliminating the read-clobber race.

### Correctness fixes

- **`rip_selected_titles`** (engine/ripper_engine.py): return value changed from `(not abort, failed_titles)` to `(not abort and not bool(failed_titles), failed_titles)`. Previously `True` was returned even when individual titles failed, which was misleading — `_normalize_rip_result` was the real gate but the signal was confusing. The change is safe: callers all pass the result through `_normalize_rip_result` which does file-presence + ffprobe validation.
- **`SessionStateMachine.complete()`** (utils/state_machine.py): new method that forces the state to COMPLETED if the session has not already failed. Used by `_run_disc` which manages its own multi-disc loop and never tracked intermediate state transitions.
- **`_run_disc`** (controller/controller.py): calls `self.sm.complete()` before `write_session_summary()` at the end of the disc loop. Previously the state machine was always in INIT (never transitioned by this flow), causing `write_session_summary` to skip the COMPLETED branch and miss the warning-list display logic.

### Security fixes

- **Update download TOCTOU** (gui/main_window.py): replaced the predictable fixed temp path (`tempdir/JellyRipUpdate/`) with `tempfile.mkdtemp(prefix="JellyRipUpdate_")`. The unique directory is cleaned up (`shutil.rmtree`) on every failure path and before early returns, preventing stale downloads from lingering.
- **Path injection in `get_authenticode_signature`** (utils/updater.py): replaced string-formatting the file path into a PowerShell command (only escaped single quotes, leaving backticks and `$(...)` injectable) with a `param([string]$p)` block and `-LiteralPath $p`. The path is now passed as a PowerShell parameter value, never interpolated into command text.

### Architecture / Bad patterns

- **`handle_fallback`** (utils/fallback.py): removed three `hasattr(controller, "_record_fallback_event")` duck-checks. The function is hardwired to `RipperController` — pretending to be generic via `hasattr` added noise without value. Direct calls are cleaner and any AttributeError is now a real programming error.
- **`shared/runtime.py` `__all__`**: removed all stdlib re-exports (`os`, `re`, `json`, `threading`, `datetime`, `tk`, `ttk`, etc.). `__all__` now only lists runtime constants and project-specific helpers. Stdlib is imported directly by callers.
- **`gui/main_window.py`**: replaced `from shared.runtime import *` with explicit imports of stdlib (os, re, json, tkinter, etc.) and the specific runtime symbols it needs.
- **`JellyRip.py`**: replaced `from shared.runtime import *` with explicit imports of only the symbols it re-exports.
- **`config.py`** and **`utils/helpers.py`**: replaced `from shared.runtime import json, os, shutil, ...` with direct stdlib imports.
- **`tests/test_imports.py`**: split GUI import into its own `test_gui_import` function guarded by `unittest.mock.patch("tkinter.Tk")`. The original test crashed on headless CI because tkinter requires a display at import time.

## [1.0.8] - 2026-03-28

### Code review fixes (10 issues closed)

- **`_normalize_rip_result`**: glob pattern changed to `**/*.mkv` with `recursive=True` — previously missed MKV files when MakeMKV wrote into subdirectories, causing rips to be silently treated as failures.
- **`get_available_drives`**: added `proc.wait(timeout=30)` with `kill()` on `TimeoutExpired` — previously the thread could hang indefinitely if `makemkvcon` stalled on startup.
- **`check_disk_space`**: removed `os.makedirs` side-effect; now returns early with a log warning if the target path doesn't exist rather than silently creating it.
- **`config.py` `load_config`**: split `except Exception` into `except json.JSONDecodeError` (logs "config corrupt, resetting") and a general `except Exception` (logs the actual error). Config loss is now visible to the user.
- **`clean_name`**: regex extended to strip ASCII control characters and null bytes (`\x00–\x1f`) in addition to forbidden filename characters — disc names with embedded control chars could silently corrupt filenames.
- **`scan_with_retry`**: removed unreachable `if result is not None` branch that followed an `if result is None: continue`.
- **`choose_best_title`**: pre-computes `score_title` once per candidate into a list and selects the max — previously called `score_title` twice on the winning title.
- **`_parse_int_or_default`** removed; call sites replaced with `safe_int(…)` (already imported from `utils.parsing`) with an `or 1` fallback where the default was non-zero.
- **`DummyGUI`** in `tests/test_behavior_guards.py`: added `set_progress`, `start_indeterminate`, `stop_indeterminate` stubs — missing methods caused `AttributeError` in any test that hit `scan_with_retry`.
- **README**: version header updated from v1.0.6 to v1.0.8.

### Correctness hardening — tiered integrity validation

- Replaced the single 60% duration threshold in `_verify_container_integrity` with a three-tier model: severe (<50% or <40% for short titles), likely-truncation (50–75% / 40–60%), minor mismatch (75–90% / 60–85%). Normal variance (≥90%) produces no warning.
- Added multi-signal escalation: a file only escalates to TRUNCATION ERROR when **both** duration and size are below threshold simultaneously, preventing false positives from inaccurate disc-scan metadata.
- Expected size values below 200 MB are now excluded from size-based escalation (disc scan metadata is unreliable for small titles).
- Multi-file titles (seamless branching) now aggregate their total duration/size before comparing against expected, preventing per-file false warnings.
- Duplicate warnings are deduplicated by `title_id` — at most one warning per logical title regardless of how many physical files it spans.
- Short titles (expected < 600 s) use widened tiers to account for higher relative variance in disc timing metadata.
- Both `run_smart_rip` and `_run_disc` (TV path) now pass `expected_durations`, `expected_sizes`, and `title_file_map` to `_verify_container_integrity`, making the tiered check universal across all ripping modes.
- In strict mode (`opt_strict_mode`), any tier below "minor" (< 75%) escalates to a hard failure.

### Attach to existing library

- New "Continue an existing show folder?" prompt at the start of every TV disc rip. Users can point JellyRip at a show folder that was created in a previous session (or by another tool); the app scans for existing season folders and episode files and writes new episodes directly into that folder.
- `_scan_library_folder(show_root)` scans a show root for `Season XX` subdirectories and their episode files, returning a dict of `{season_num: [ep_nums]}`. Used to display which seasons already exist in the prompt.
- `_scan_episode_files(folder, season)` recognises three naming formats: `SxxEyy` (standard), `Nx01` (1x01), and `Episode N`. Case-insensitive. Only reads the directory listing.
- `get_next_episode(existing)` implements gap-fill logic: returns the lowest missing episode number rather than simply appending after the highest. If Season 1 has E01, E02, E04 (E03 missing), the next suggestion is E03.
- Episode number prompt in `_select_and_move` now pre-fills using gap-fill logic and logs whether it is "gap-filling from" or "continuing from" the detected offset.
- When an existing library folder is selected the season prompt shows which seasons were detected (e.g. `S01  S02`) and the destination is written directly into the selected folder rather than constructing a new path under `tv_root`.

### Regression tests

- 5 regression tests for tiered integrity: severe warn-only (no size), severe + size escalation, expected-size clamping, strict-mode failure, minor mismatch passes.
- 8 regression tests for integrity aggregation: multi-file title aggregation, dedup (one warning per title), size floor, short-title tolerance.
- 4 tests for `get_next_episode` gap-fill.
- 6 tests for `_scan_highest_episode` / `_scan_episode_files` (compat wrapper, case-insensitive, season isolation, missing folder, None dest_folder, `1x01` format, `Episode N` format).
- 5 tests for `_scan_library_folder` (season dir detection, non-season dir exclusion, empty root, missing path, sorted episode lists).

## [1.0.7] - 2026-03-28

### Correctness hardening

- Eliminated duplicate ffprobe analysis passes in TV/Movie disc (`_run_disc`) and Smart Rip (`run_smart_rip`) pipelines. `analyze_files()` result is now shared with `_verify_container_integrity()` via a new `analyzed=` parameter — each file is probed exactly once per pipeline step.
- Fixed `run_smart_rip` state machine: `STABILIZED` transition was missing on the normal (non-retry) path; `VALIDATED` transition was dead code (placed after an unconditional `return`). Both are now correctly placed on the success path.
- Fixed `run_organize()` path drift: removed early `cfg["tv_folder"]` / `cfg["movies_folder"]` / `cfg["temp_folder"]` reads that silently bypassed run-time path overrides. All three folder roots now derive exclusively from `get_path()` after `_init_session_paths()`.
- Added `_ensure_session_paths()` guard: raises `RuntimeError` immediately if `session_paths` has not been initialized, making misconfigured calls fail loudly rather than silently writing to the wrong folder.
- Cleaned up post-stabilization size advisory log to use the format: `X MB (below threshold — expected Y GB → threshold Z GB)`.
- Replaced exit-code-based rip failure forcing with validation-based degraded success classification. MakeMKV frequently exits non-zero on real discs even when output is usable; the engine now checks whether files were actually produced. Non-zero exit + files present → degraded success (warning added to session report, downstream stabilization and ffprobe still validate the file). Non-zero exit + no files → real failure, unchanged. Session summary now distinguishes "All discs completed successfully" from "Completed with warnings" when degraded titles were detected.

### Test coverage

- Added 7 regression tests covering `_ensure_session_paths`, `_verify_container_integrity` with and without pre-analyzed data, integrity failure cases (zero duration, count mismatch), and advisory log format.
- Added 4 regression tests for degraded rip classification: degraded success path, real failure path (no files), session report population, and session summary warnings vs. clean success branching.

## [1.0.6] - 2026-03-27

### New features

- Added per-title Preview actions in the title picker, including short disposable sample rips and VLC launch support.
- Added fallback title naming mode in Settings with friendly options: timestamp, auto title, or auto title plus timestamp.

### Resume and workflow improvements

- Expanded workflow-level resume metadata to restore key inputs (title, year, season, selected titles, episode fields).
- Added resume-aware defaults in input prompts so interrupted sessions can continue with less manual re-entry.
- Added metadata phase tracking updates through setup, ripping, analyzing, moving, failed, and complete states.

### Reliability hardening

- Enforced all-or-nothing rip normalization: aborts, failed titles, missing outputs, or invalid outputs are treated as session failure.
- Added failed-session cleanup that wipes output files while preserving metadata for future workflow resume.
- Added pre-rip target file purge for `.mkv` and `.partial` files to prevent file-level resume artifacts.
- Added regression guard coverage that enforces `rc != 0` as failure even if MKV output files exist.
- Added complementary guard coverage that enforces abort as failure even when output files are present and ffprobe-valid.

### UI and settings

- Reworked Settings into tabbed sections for easier navigation across Paths, Everyday, Validation, Advanced, and Logs.
- Added low-confidence Smart Rip threshold control and improved naming-mode presentation in the settings UI.
- Added a top-bar Check Updates action that checks GitHub Releases and can download/launch update packages.

### Distribution

- Added Inno Setup installer support (`installer/JellyRip.iss`) targeting per-user install at `%LOCALAPPDATA%\Programs\JellyRip`.
- Added `build_installer.bat` to build both `JellyRip.exe` and `JellyRipInstaller.exe`.

## [1.0.5] - 2026-03-25

### Critical bug fixes

- **Fixed Movie/Unattended mode deadlock**: Movie and Unattended buttons now work without freezing. Moved mode-picker logic from main thread to background thread to prevent tkinter callback deadlock during dialog prompts.
- **Fixed log file path handling**: Log file paths without `.txt` extension are now automatically suffixed to prevent "Permission denied" errors on Windows.
- **Enhanced analysis error handling**: Added explicit logging of analysis results and exception handling to help debug silent failures during file analysis.
- **Fixed abort during mode picker starting task**: Pressing Abort while the "Movie/Unattended mode?" prompt is showing no longer starts the rip — it now cancels cleanly.

### Features

- **Smart Rip now asks to keep extras**: After auto-selecting the main feature, Smart Rip now asks "Keep extras from this disc?" and rips/moves all additional titles to the Extras folder if yes.

### UI/UX improvements

- Better error messages when file analysis fails, with option to retry instead of silently skipping.

## [1.0.4] - 2026-03-25

### Reliability and parsing hardening

- Hardened duration parsing so malformed values safely default to 0.
- Hardened size parsing to tolerate variants like 3.7GB, 3,7 GB, and trailing text.
- Added safe dictionary access for optional audio and subtitle track lists in scoring/logging paths.

### Ripping and process stability

- Kept rip success based on MakeMKV exit code while preserving fallback behavior when files are actually produced on non-zero exits.
- Maintained abort-safe process handling with local subprocess snapshot usage.
- Added extra guardrails for destination path race conditions during atomic move.

### Observability and diagnostics

- Added optional safe_int debug warnings (de-duplicated and throttled; off by default).
- Added optional malformed-duration debug warnings (de-duplicated and throttled; off by default).
- Expanded score visibility with explicit score breakdown and ambiguity warning when top candidates are close.

### Testing and docs

- Added printable live-rip pass/fail worksheet for testers: TESTERS.md.
- Updated README links so testers can find worksheet and issue reporting flow quickly.
