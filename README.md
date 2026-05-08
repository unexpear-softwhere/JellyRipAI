# JellyRip

JellyRip is a Windows-first desktop app that uses MakeMKV and ffprobe
to rip discs, validate output, and organize media into a
Jellyfin-friendly library structure.

The project is currently pre-alpha. The codebase is actively tested and
being hardened, but live disc workflows can still change quickly and
should be treated as non-final.

## Project Status

- Current unstable line: `ai-v1.0.21` (latest unstable AI pre-release)
- AI release page: [ai-v1.0.21](https://github.com/unexpear-softwhere/JellyRipAI/releases/tag/ai-v1.0.21)
- MAIN release page: [v1.0.21](https://github.com/unexpear/JellyRip/releases/tag/v1.0.21) (non-AI baseline, separate repo)
- Project site: [unexpear-softwhere.github.io/JellyRipAI](https://unexpear-softwhere.github.io/JellyRipAI/)
- Platform target: Windows
- Runtime target: Python 3.13+
- Distribution target: standalone `JellyRipAI.exe` and optional installer
- Quality target: practical and safe for testing,
  not yet stable enough to treat as finished software

## Active Forks

JellyRip is maintained as two separate repositories:

- **[unexpear/JellyRip](https://github.com/unexpear/JellyRip)** — the
  non-AI baseline. Core ripping, validation, organization, logging,
  and update workflows without assistant or AI-driven features.
  Releases tagged `vX.Y.Z`.
- **[unexpear-softwhere/JellyRipAI](https://github.com/unexpear-softwhere/JellyRipAI)**
  (this repo) — the assist-feature fork. Baseline workflows plus
  chat sidebar, AI provider integrations, diagnostic backends. Releases
  tagged `ai-vX.Y.Z`.

Rule of thumb: deterministic core behavior belongs in the non-AI fork
first. Assistive features can suggest or prefill, but they must stay
visible, optional, reversible, and weaker than explicit user input.

Branch-specific documentation for the assist line:

- [docs/ai-assist-branch.md](docs/ai-assist-branch.md)

## What JellyRip Does

- rips movie and TV discs with MakeMKV
- validates outputs with ffprobe and file stabilization checks
- organizes files into Jellyfin-style movie and TV folder structures
- supports interactive, unattended, and smart-rip workflows
- keeps session logs and end-of-run warning summaries

## Quick Start

### From GitHub release

(recommended, currently `ai-v1.0.21` unstable AI pre-release)

1. Go to the [current unstable AI release page](https://github.com/unexpear-softwhere/JellyRipAI/releases/tag/ai-v1.0.21).
2. Download `JellyRipAIInstaller.exe` (installer) or `JellyRipAI.exe` (standalone).
3. If SmartScreen/Defender flags the file, whitelist the download folder
  first (common PyInstaller false positive).
4. Run and open **Settings** to confirm MakeMKV and ffprobe paths before first rip.

### From source (git clone)

```bash
# AI fork (this repo) — chat sidebar + AI provider integrations
git clone https://github.com/unexpear-softwhere/JellyRipAI.git
cd JellyRipAI
# pulls PySide6 plus the Anthropic SDK for Claude support
pip install -r requirements.txt
python main.py
```

If you don't want the AI features, clone the non-AI baseline instead
(`https://github.com/unexpear/JellyRip.git`).  The two are
intentionally separate repositories, not branches of one — releases
on each side keep their own tag prefix (`v*` for MAIN, `ai-v*` here)
so they can never collide.

First launch tip: open **Settings** and confirm MakeMKV and ffprobe
paths before the first rip.

## Requirements

- Windows
- MakeMKV
- FFmpeg (`ffmpeg` and `ffprobe`) for source runs; release builds bundle
  the GPLv3 Gyan full build
- optical drive for live ripping

## Main Workflows

- **TV Disc**: interactive disc ripping with episode-oriented organization (some testing)
- **Movie Disc**: interactive movie ripping with metadata prompts (some testing)
- **Smart Rip**: auto-pick the best main feature (some testing)
- **Dump All**: raw dump mode for all titles (some testing)
- **Organize Existing MKVs**: move and sort already-ripped files (not tested)
- **Unattended Modes**: operator-assisted multi-disc flows with
  blocking confirmations and safety checks (light testing — exercises the
  same disc-rip core as TV/Movie/Smart Rip)
- **Prep for and use FFmpeg or HandBrake**: simple transcoding (not tested)

## Configuration

Settings are stored at `%APPDATA%\JellyRipAI\config.json` on Windows.

You can configure:

- MakeMKV and ffprobe paths
- optional FFmpeg and HandBrakeCLI executable paths
- temp, movie, and TV folders
- retry behavior and quiet/stall warnings
- file stabilization and validation thresholds
- unattended prompt and disc-swap timeout behavior
- update-signature settings
- debug logging options

Windows tool lookup prefers explicit configured paths, bundled binaries,
and known install locations before falling back to PATH discovery.

App-directory `.env` files are no longer loaded at startup.

## Development

### Repository layout

- [main.py](main.py) - primary entrypoint
- [JellyRip.py](JellyRip.py) - compatibility entrypoint and project map
- [gui_qt](gui_qt) - PySide6 (Qt) UI layer (themes, dialogs, preview, AI chat sidebar)
- [gui_qt/qss](gui_qt/qss) - generated theme stylesheets (six themes)
- [controller](controller) - workflow orchestration
- [engine](engine) - MakeMKV, ffprobe, and file operations
- [utils](utils) - helper modules
- [shared](shared) - shared runtime defaults and constants
- [tests](tests) - automated regression coverage
- [docs/architecture.md](docs/architecture.md) - architecture overview
- [docs/ai-assist-branch.md](docs/ai-assist-branch.md) - AI branch
  feature map, provider stack, diagnostics behavior, and branch rules
- [docs/repository-layout.md](docs/repository-layout.md) - repository layout rationale

### Testing

```bash
python -m pytest -q
```

Manual live-rip validation worksheet:

- [TESTERS.md](TESTERS.md)

Contribution and security guidance:

- [CONTRIBUTING.md](CONTRIBUTING.md)
- [SECURITY.md](SECURITY.md)

## Building Releases

### Standalone executable

```bash
build.bat
```

The AI build scripts place standalone build artifacts under `dist\ai`.
`build.bat` wraps `pyinstaller JellyRip.spec` with the AI artifact and
work directories preconfigured.
The spec bundles the Gyan FFmpeg full build (`ffmpeg.exe`, `ffprobe.exe`,
and `ffplay.exe`). Put the extracted build under
`%USERPROFILE%\Desktop\ffmpeg`, `.\ffmpeg\`, or `..\ffmpeg\`, or set
`JELLYRIP_FFMPEG_DIR` before building. Release builds must also ship
`FFmpeg-LICENSE.txt` and `FFmpeg-README.txt`.

### Executable plus installer

```bash
build_installer.bat
```

Commercial installer builds require an appropriate Inno Setup license.

Expected outputs:

- `dist/ai/JellyRipAI.exe`
- `dist/ai/JellyRipAIInstaller.exe`
- `dist\ai\ffmpeg.exe`, `dist\ai\ffprobe.exe`, and `dist\ai\ffplay.exe`
- `dist\ai\FFmpeg-LICENSE.txt` and `dist\ai\FFmpeg-README.txt`

Build output is intentionally git-ignored and should be published
through GitHub Releases rather than committed to the repository.

### Full release pipeline

```bash
release.bat 1.0.21
```

This runs tests, checks version consistency, builds both executables,
pushes code, and publishes a GitHub release with assets attached in the
correct order. It also refuses to run from a dirty working tree or a
branch other than `main`. AI releases are tagged as `ai-vX.Y.Z` so they
can never collide with MAIN's `vX.Y.Z` tags. Never create a release
without assets.

## Support and Reporting

- Issues: [GitHub Issues](https://github.com/unexpear-softwhere/JellyRipAI/issues)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Release post text: [release_notes.txt](release_notes.txt)
- Readable release notes: [release_notes.md](release_notes.md)
- Tester worksheet: [TESTERS.md](TESTERS.md)

If Windows Defender flags the executable, whitelist the download folder
before retrying. This is a known false-positive pattern for
PyInstaller-built Windows executables.

## License

JellyRip is licensed under GPLv3. See [LICENSE](LICENSE) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Credits

Acknowledgments for the tools, libraries, AI provider integrations,
and people that make the AI fork possible live at
[CREDITS.md](CREDITS.md).
