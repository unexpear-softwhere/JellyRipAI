---
title: JellyRip AI
description: AI-assisted disc ripping for Jellyfin libraries
---

JellyRip AI is the assistant-enabled fork of
[JellyRip](https://github.com/unexpear/JellyRip).  Everything in the
non-AI baseline — disc ripping with MakeMKV, ffprobe validation,
Jellyfin-style library organization — comes from MAIN.  This fork adds
an AI assistant layer on top: a chat sidebar, an AI provider dialog,
and adapters for Anthropic Claude, OpenAI, Google Gemini, and a local
Ollama server.

The project is **pre-alpha**.  The codebase is actively tested and being
hardened, but live disc workflows can still change quickly and should be
treated as non-final.

## Download

[Latest release: ai-v1.0.23](https://github.com/unexpear-softwhere/JellyRipAI/releases/tag/ai-v1.0.23){: .btn .btn-primary }
[Installer (.exe)](https://github.com/unexpear-softwhere/JellyRipAI/releases/download/ai-v1.0.23/JellyRipAIInstaller.exe){: .btn }
[Standalone (.exe)](https://github.com/unexpear-softwhere/JellyRipAI/releases/download/ai-v1.0.23/JellyRipAI.exe){: .btn }

If Windows SmartScreen flags the executable, whitelist the download
folder before retrying — known false-positive for PyInstaller binaries.

## What's different from MAIN

The disc-ripping, validation, and Jellyfin-organization workflows are
identical.  The AI fork adds:

- **Chat sidebar** with replay logging and an on-device fallback path
  for when no provider is configured.
- **AI provider dialog** that stores keys via Windows DPAPI (encrypted
  at rest) and lets the user pick a provider per session.
- **Adapters** for Anthropic Claude (via the `anthropic` SDK), OpenAI
  (HTTP, no extra dependency), Google Gemini (HTTP), and Ollama for
  local models.
- **Diagnostics** that route session-level errors through whichever
  provider is configured, with a deterministic local fallback.

The maintainer's rule of thumb: deterministic core behavior belongs in
the non-AI line first.  Assistive features can suggest or prefill, but
they must stay visible, optional, reversible, and weaker than explicit
user input.

## Project information

- [README](https://github.com/unexpear-softwhere/JellyRipAI/blob/main/README.md) —
  project overview, requirements, build instructions
- [Changelog](https://github.com/unexpear-softwhere/JellyRipAI/blob/main/CHANGELOG.md) —
  version-by-version diff
- [Release notes](https://github.com/unexpear-softwhere/JellyRipAI/blob/main/release_notes.md) —
  human-readable narrative for the latest tag
- [Credits](https://github.com/unexpear-softwhere/JellyRipAI/blob/main/CREDITS.md) —
  bundled tools, AI provider integrations, AI-assisted development
- [Third-party notices](https://github.com/unexpear-softwhere/JellyRipAI/blob/main/THIRD_PARTY_NOTICES.md) —
  legal license text for bundled components
- [Security](https://github.com/unexpear-softwhere/JellyRipAI/blob/main/SECURITY.md) —
  reporting policy
- [Contributing](https://github.com/unexpear-softwhere/JellyRipAI/blob/main/CONTRIBUTING.md) —
  contribution and development guidance
- [Testers' worksheet](https://github.com/unexpear-softwhere/JellyRipAI/blob/main/TESTERS.md) —
  manual live-rip validation steps
- [Feature map](https://github.com/unexpear-softwhere/JellyRipAI/blob/main/FEATURE_MAP.md) —
  file-to-feature mapping

## Documentation

- [AI assist branch reference]({% link ai-assist-branch.md %}) —
  feature map, provider stack, diagnostics behavior, branch rules
- [Architecture overview]({% link architecture.md %})
- [Repository layout]({% link repository-layout.md %})
- [Branch workflow]({% link branch-workflow.md %})
- [Glossary]({% link glossary.md %})
- [Copy style guide]({% link copy-style.md %})
- [PySide6 migration plan]({% link pyside6-migration-plan.md %})
- [UX copy and accessibility plan]({% link ux-copy-and-accessibility-plan.md %})

## Non-AI baseline: JellyRip

If you don't want or need AI features, the non-AI baseline is the
appropriate fork.

- [JellyRip on GitHub](https://github.com/unexpear/JellyRip)
- [JellyRip documentation site](https://unexpear.github.io/JellyRip/)
- [Latest MAIN release: v1.0.23](https://github.com/unexpear/JellyRip/releases/tag/v1.0.23)

## Source and license

- GitHub: [unexpear-softwhere/JellyRipAI](https://github.com/unexpear-softwhere/JellyRipAI)
- License: [GPL-3.0](https://github.com/unexpear-softwhere/JellyRipAI/blob/main/LICENSE)
- Issues / bug reports: [GitHub Issues](https://github.com/unexpear-softwhere/JellyRipAI/issues)
