# JellyRip

JellyRip is a Windows-first desktop app that uses MakeMKV and ffprobe
to rip discs, validate output, and organize media into a
Jellyfin-friendly library structure.

The project is currently pre-alpha. The codebase is actively tested and
being hardened, but live disc workflows can still change quickly and
should be treated as non-final.

## Project Status

- Current unstable line: `v1.0.16` (latest unstable pre-release)
- Platform target: Windows
- Runtime target: Python 3.13+
- Distribution target: standalone `JellyRip.exe` and optional installer
- Quality target: practical and safe for testing,
  not yet stable enough to treat as finished software

## Active Branches

JellyRip is currently maintained as two separate lines:

- `codex/non-ai-main` - the non-AI baseline. This line keeps the core
  ripping, validation, organization, logging, and update workflows
  without assistant or AI-driven features.
- `codex/assist-layer-split` - the assist-feature line. This line
  includes the baseline workflows plus assistant, provider, diagnostic,
  and other AI-assisted features.

Rule of thumb: deterministic core behavior belongs in the non-AI line
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

(recommended, currently `v1.0.16` unstable pre-release)

1. Go to the [current unstable release page](https://github.com/unexpear/JellyRip/releases/tag/v1.0.16).
2. Download `JellyRipInstaller.exe` (installer) or `JellyRip.exe` (standalone).
3. If SmartScreen/Defender flags the file, whitelist the download folder
  first (common PyInstaller false positive).
4. Run and open **Settings** to confirm MakeMKV and ffprobe paths before first rip.

### From source (git clone)

```bash
git clone https://github.com/unexpear/JellyRip.git
cd JellyRip
git fetch origin
# non-AI baseline
# git switch --track origin/codex/non-ai-main
# assist/AI line
git switch --track origin/codex/assist-layer-split
pip install -r requirements.txt
python main.py
```

First launch tip: open **Settings** and confirm MakeMKV and ffprobe
paths before the first rip.

## Requirements

- Windows
- MakeMKV
- FFmpeg (`ffmpeg` and `ffprobe`) for source runs; release builds bundle
  the GPLv3 Gyan full build
- optical drive for live ripping

## Main Workflows

- **TV Disc**: interactive disc ripping with episode-oriented organization
- **Movie Disc**: interactive movie ripping with metadata prompts
- **Smart Rip**: auto-pick the best main feature
- **Dump All**: raw dump mode for all titles
- **Organize Existing MKVs**: move and sort already-ripped files
- **Unattended Modes**: operator-assisted multi-disc flows with
  blocking confirmations and safety checks

## Configuration

Settings are stored at `%APPDATA%\JellyRip\config.json` on Windows.

You can configure:

- MakeMKV and ffprobe paths
- temp, movie, and TV folders
- retry behavior and quiet/stall warnings
- file stabilization and validation thresholds
- unattended prompt and disc-swap timeout behavior
- update-signature settings
- debug logging options

## Development

### Repository layout

- [main.py](main.py) - primary entrypoint
- [JellyRip.py](JellyRip.py) - compatibility entrypoint and project map
- [gui](gui) - tkinter UI layer
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
pip install pyinstaller
pyinstaller JellyRip.spec
```

The spec bundles the Gyan FFmpeg full build (`ffmpeg.exe`, `ffprobe.exe`,
and `ffplay.exe`). Put the extracted build under `.\ffmpeg\` or `..\ffmpeg\`,
or set `JELLYRIP_FFMPEG_DIR` before building. Release builds must also ship
`FFmpeg-LICENSE.txt` and `FFmpeg-README.txt`.

### Executable plus installer

```bash
build_installer.bat
```

Commercial installer builds require an appropriate Inno Setup license.

Expected outputs:

- `dist/JellyRip.exe`
- `dist/JellyRipInstaller.exe`
- `dist\ffmpeg.exe`, `dist\ffprobe.exe`, and `dist\ffplay.exe`
- `dist\FFmpeg-LICENSE.txt` and `dist\FFmpeg-README.txt`

Build output is intentionally git-ignored and should be published
through GitHub Releases rather than committed to the repository.

### Full release pipeline

```bash
release.bat 1.0.16
```

This runs tests, checks version consistency, builds both executables,
pushes code, and publishes a GitHub release with assets attached in the
correct order. It also refuses to run from a dirty working tree or a
branch other than `codex/non-ai-main`. Official releases should be cut
from the canonical non-AI line. Never create a release without assets.

## Support and Reporting

- Issues: [GitHub Issues](https://github.com/unexpear/JellyRip/issues)
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
