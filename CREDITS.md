# Credits — JellyRip AI fork

JellyRip AI is the assistant-enabled fork of
[JellyRip](https://github.com/unexpear/JellyRip).  Everything in the
non-AI baseline — disc ripping, validation, library organization —
comes from MAIN.  This fork adds an AI assistant layer on top.

This page is the human-readable acknowledgments.  For legal license
detail, see [`LICENSE`](LICENSE) and
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

## Maintainer

* **[unexpear](https://github.com/unexpear)** — author and primary
  maintainer.

## External tools the user must install

* **[MakeMKV](https://www.makemkv.com/)** — the disc-ripping engine.
  JellyRip is a workflow wrapper around `makemkvcon`.
* **[Jellyfin](https://jellyfin.org/)** — the media server the
  output folder layout targets.

## Bundled with release builds

* **[FFmpeg](https://ffmpeg.org/)** (Gyan Windows full build) —
  `ffmpeg.exe`, `ffprobe.exe`, `ffplay.exe`.
* **[Python](https://www.python.org/)** — embedded interpreter.
* **[PySide6 / Qt](https://www.qt.io/qt-for-python)** — the Qt UI
  toolkit.  Phase 4 (the AI BRANCH PySide6 port) ported the chat
  sidebar and AI provider dialog onto the same Qt foundation MAIN
  ships.
* **[PyInstaller](https://pyinstaller.org/)** — produces
  `JellyRipAI.exe`.
* **[Inno Setup](https://jrsoftware.org/isinfo.php)** — produces
  `JellyRipAIInstaller.exe`.

## AI provider integrations

The fork ships adapters for several AI providers in
[`shared/ai/providers/`](shared/ai/providers/).  The user supplies
their own API keys via the in-app **AI Providers** dialog; nothing
phones home without explicit configuration.

* **[Anthropic Claude](https://www.anthropic.com/)** — via the
  [`anthropic`](https://pypi.org/project/anthropic/) Python SDK
  (the only AI provider with a pinned SDK dependency).
* **[OpenAI](https://openai.com/)** — talks to the OpenAI HTTP API
  via the standard library (no extra dependency).
* **[Google Gemini](https://ai.google.dev/)** — talks to the
  Generative Language API via the standard library.
* **[Ollama](https://ollama.com/)** — for users who want to run a
  local model.  Talks to a local Ollama server's HTTP API.

The chat sidebar's on-device fallback path (the "what's happening
on screen?" summary that fires when no provider is available) is
all-local heuristics — no provider needed.

## Optional user-installed integrations

* **[HandBrakeCLI](https://handbrake.fr/)** — alternate transcode
  backend.
* **[VLC](https://www.videolan.org/)** — fallback preview player.

## AI-assisted development

A substantial portion of v1.0.x development was paired with
multiple AI coding assistants over the project's lifespan.  Each
contributed to different stretches of the work:

* **[Claude Code](https://claude.com/claude-code)** (Anthropic) —
  the Phase 4 Qt port, the chat backend (with on-device fallback +
  replay logging), the AI provider dialog Qt rewrite, the
  multi-instance `--profile` feature, the settings tabs, and the
  smoke-session hardening backports.  Commits show
  `Co-Authored-By: Claude Opus 4.7 …` where this happened.
* **[Codex](https://github.com/openai/codex)** (OpenAI) — earlier
  development passes; local `.codex_backup/` snapshots of those
  sessions are gitignored.
* **[Grok](https://x.ai/)** (xAI) — additional pairing during
  development.

The maintainer reviewed and accepted every change before it
landed; the AI assistants were collaborators, not autonomous
authors.

## License

JellyRip AI is **[GPL-3.0](LICENSE)**, same as MAIN.  Bundled
FFmpeg is GPL-3.0.  The Anthropic SDK is MIT-licensed.  Other
bundled components carry their own licenses documented in
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
