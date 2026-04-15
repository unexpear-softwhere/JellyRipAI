# JellyRip AI v1.0.17 Release Notes

## Release Channel

Unstable pre-release.

## Download

- Direct download: [JellyRipAI.exe](https://github.com/unexpear/JellyRip/releases/download/ai-v1.0.17/JellyRipAI.exe)
- Installer: [JellyRipAIInstaller.exe](https://github.com/unexpear/JellyRip/releases/download/ai-v1.0.17/JellyRipAIInstaller.exe)
- Release page: [ai-v1.0.17 release](https://github.com/unexpear/JellyRip/releases/tag/ai-v1.0.17)
- All releases: [GitHub Releases](https://github.com/unexpear/JellyRip/releases)

## What's New in 1.0.17

### FFmpeg and transcode reliability

- Improved FFmpeg abort handling so queued work shuts down more cleanly.
- Expanded copy-progress logging and transcode validation around FFmpeg workflows.

### Release bundling and packaging

- Bundled FFmpeg runtime assets and notices more intentionally for packaged releases.
- Restored the richer PyInstaller spec so release builds carry version metadata and bundled runtime dependencies consistently.

### Release hygiene

- Release metadata now aligns on the `1.0.17` line across the app, installer, docs, and release notes.
- Build output remains a GitHub Releases artifact instead of a tracked repository binary.
- In-app update checks now follow the newest published release, including unstable prereleases.
