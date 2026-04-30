# AI Assist Branch

JellyRip's `ai` line keeps the baseline ripping
workflows and adds an optional assistant layer on top. This document is
the branch-specific map for that AI surface: what it does, where it
lives, and which boundaries it is expected to respect.

## Branch goals

- keep the non-AI ripping and organization workflows intact
- let AI explain, suggest, summarize, or prefill without silently taking
  control of the pipeline
- keep all AI behavior visible and reversible from the UI
- degrade cleanly when providers are unavailable, slow, rate limited, or
  misconfigured

The core rule for this branch is unchanged from the main README:
deterministic ripping behavior stays stronger than AI output.

## User-facing AI features

### AI mode selector

Main window integration in [gui/main_window.py](../gui/main_window.py):

- `off` disables assistant requests
- `cloud` prefers the configured cloud provider and can fall back to
  local where appropriate
- `local` only uses the local provider

The active mode is stored in `opt_ai_mode`.

### AI sidebar chat

The sidebar chat is owned by [gui/main_window.py](../gui/main_window.py).
It is designed to help with the current session rather than act as a
generic hidden control path.

Important behavior:

- requests include a UI snapshot and recent live log lines
- "what should I do next" style prompts can be answered by an app-side
  fallback without making a provider call
- payload echoes or provider failures fall back to a local explanatory
  answer instead of crashing the UI
- the sidebar remembers width and open/closed state in config

### AI provider setup dialog

Provider management lives in
[gui/ai_provider_dialog.py](../gui/ai_provider_dialog.py).

The dialog lets the user:

- enter or update cloud API keys
- choose provider models
- test provider connectivity
- choose the active cloud provider
- inspect local-model availability

Supported providers in this branch:

- Claude (Anthropic)
- OpenAI
- Gemini
- Local (Ollama)

Install note:

- `pip install -r requirements.txt` installs the Anthropic SDK used by
  the Claude provider

### AI diagnostics

The diagnostic bus is implemented in
[shared/ai_diagnostics.py](../shared/ai_diagnostics.py).

This layer listens for meaningful failure events and can request AI help
for diagnosis. It is advisory only.

The intended behavior is:

- capture structured failure context
- write durable logs even if the GUI becomes unreliable
- call AI only on meaningful failure categories
- never auto-patch code, rewrite config behind the user's back, or block
  a rip workflow while waiting for AI

## Provider and credential architecture

### Registry and adapters

The single lookup point is
[shared/ai/provider_registry.py](../shared/ai/provider_registry.py).

Provider adapters live under
[shared/ai/providers](../shared/ai/providers):

- [claude_provider.py](../shared/ai/providers/claude_provider.py)
- [openai_provider.py](../shared/ai/providers/openai_provider.py)
- [gemini_provider.py](../shared/ai/providers/gemini_provider.py)
- [local_provider.py](../shared/ai/providers/local_provider.py)

The registry is responsible for:

- listing providers for the dialog
- applying saved credentials
- resolving the active cloud provider
- resolving the local provider

### Credential storage

Credential persistence lives in
[shared/ai/credential_store.py](../shared/ai/credential_store.py).

Current storage model:

- file path: `%APPDATA%\\JellyRipAI\\ai_credentials.json`
- credentials are stored per provider so cloud and local backends can
  coexist for fallback
- the active cloud choice is tracked separately from the local provider
- Windows-first protection with DPAPI for sensitive values
- plaintext fallback only when DPAPI is unavailable or fails
- transparent migration of older plaintext entries on load

The main config remains in `config.json`; AI credentials are kept
separate on purpose.

## Diagnostic pipeline

The diagnostics manager fans out to several outputs:

- GUI log mirror
- persistent system log at `%LOCALAPPDATA%\\JellyRipAI\\logs\\system.log`
- `session.log`
- `session.ai.log`
- `session.state.json`
- in-memory ring buffer for recent events

Important trigger categories include:

- uncaught exceptions
- subprocess non-zero exits
- repeated retry loops
- output validation failures
- file stabilization failures
- disc read errors
- move verification failures
- network-share related failures

Cloud reasoning is preferred for the highest-severity categories, while
less severe categories can use local models when available.

## AI-specific config surface

AI-related defaults currently live in
[shared/runtime.py](../shared/runtime.py).

The main keys worth knowing are:

- `opt_ai_mode`
- `opt_ai_cloud_enabled`
- `opt_ai_local_enabled`
- `opt_ai_local_provider`
- `opt_ai_local_model`
- `opt_ai_cloud_timeout_seconds`
- `opt_ai_local_timeout_seconds`
- `opt_ai_max_calls_per_session`
- `opt_ai_disable_after_failures`
- `opt_ai_diagnostics_enabled`
- `opt_ai_diagnostics_mode`
- `opt_ai_log_to_gui`
- `opt_ai_log_to_file`
- `opt_ai_capture_raw_process_output`
- `opt_ai_emit_state_json`
- `opt_ai_sidebar_open`
- `opt_ai_sidebar_width`

These are branch features, not baseline requirements. A fully usable
non-AI workflow should still exist when AI is off or unavailable.

## Local provider expectations

The local adapter currently targets Ollama over HTTP:

- default base URL: `http://localhost:11434`
- model selection comes from actually pulled local models
- if the saved model is stale, the UI prefers an installed local model
  instead of failing silently

This keeps the local path usable on test machines without requiring a
cloud key.

## Testing map

The branch already has dedicated AI coverage:

- [tests/test_ai_chat_sidebar.py](../tests/test_ai_chat_sidebar.py)
- [tests/test_ai_provider_dialog.py](../tests/test_ai_provider_dialog.py)
- [tests/test_local_provider.py](../tests/test_local_provider.py)
- [tests/test_provider_registry.py](../tests/test_provider_registry.py)

Behavior guards in
[tests/test_behavior_guards.py](../tests/test_behavior_guards.py) also
cover branch safety expectations around diagnostics and fallback logic.

## Documentation intent

This document is meant to answer:

- what belongs only in the AI branch
- where provider and diagnostics code live
- which config keys and logs matter during debugging
- which boundaries must not be crossed when adding more AI features

If a new AI feature lands in this branch, update this file alongside the
code so the branch does not drift back into "assistant stuff lives
somewhere in main_window.py".
