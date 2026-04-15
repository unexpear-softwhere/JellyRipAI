from config import AppConfig
# Example test using direct AppConfig construction
def test_appconfig_direct_construction():
    config = AppConfig(source="makemkvcon.exe", output="ffprobe.exe", quality="high")
    assert config.source == "makemkvcon.exe"
    assert config.output == "ffprobe.exe"
    assert config.quality == "high"
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # type: ignore[import-not-found]
from engine.ripper_engine import RipperEngine


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")


def test_resolve_tool_prefers_configured_path(tmp_path):
    configured = tmp_path / "cfg" / "ffprobe.exe"
    fallback = tmp_path / "fallback" / "ffprobe.exe"
    _touch(configured)
    _touch(fallback)

    resolved = config.resolve_tool(
        str(configured),
        [str(fallback)],
        "ffprobe",
    )

    assert os.path.normpath(resolved) == os.path.normpath(str(configured))


def test_resolve_tool_prefers_common_path_over_env(monkeypatch, tmp_path):
    configured = tmp_path / "missing" / "ffprobe.exe"
    fallback = tmp_path / "fallback" / "ffprobe.exe"
    env_tool = tmp_path / "path" / "ffprobe.exe"
    _touch(fallback)
    _touch(env_tool)

    monkeypatch.setattr(config.shutil, "which", lambda _name: str(env_tool))

    resolved = config.resolve_tool(
        str(configured),
        [str(fallback)],
        "ffprobe",
    )

    assert os.path.normpath(resolved) == os.path.normpath(str(fallback))


def test_resolve_tool_falls_back_to_env_path(monkeypatch, tmp_path):
    configured = tmp_path / "missing" / "ffprobe.exe"
    env_tool = tmp_path / "path" / "ffprobe.exe"
    _touch(env_tool)

    monkeypatch.setattr(config.shutil, "which", lambda _name: str(env_tool))

    resolved = config.resolve_tool(
        str(configured),
        [],
        "ffprobe",
    )

    assert os.path.normpath(resolved) == os.path.normpath(str(env_tool))


def test_auto_locate_ffmpeg_prefers_bundled_binary(monkeypatch, tmp_path):
    bundled = tmp_path / "ffmpeg.exe"
    bundled.write_text("x", encoding="utf-8")

    monkeypatch.setattr(config, "_bundled_binary_candidates", lambda _name: [str(bundled)])

    assert os.path.normpath(config.auto_locate_ffmpeg()) == os.path.normpath(str(bundled))


def test_validate_ffmpeg_rejects_old_panda3d_style_build(monkeypatch, tmp_path):
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_text("x", encoding="utf-8")

    class _Result:
        returncode = 0
        stdout = (
            b"ffmpeg version N-55702-g920046a\n"
            b"libavcodec     55. 29.100 / 55. 29.100\n"
        )
        stderr = b""

    monkeypatch.setattr(config.subprocess, "run", lambda *args, **kwargs: _Result())

    ok, reason = config.validate_ffmpeg(str(ffmpeg))

    assert ok is False
    assert "too old" in reason
    assert "libavcodec 55" in reason


def test_validate_ffmpeg_accepts_modern_build(monkeypatch, tmp_path):
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_text("x", encoding="utf-8")

    class _Result:
        returncode = 0
        stdout = (
            b"ffmpeg version 2026-04-01-git-eedf8f0165-full_build\n"
            b"libavcodec     62. 29.101 / 62. 29.101\n"
        )
        stderr = b""

    monkeypatch.setattr(config.subprocess, "run", lambda *args, **kwargs: _Result())

    ok, reason = config.validate_ffmpeg(str(ffmpeg))

    assert ok is True
    assert reason == ""


def test_validate_makemkvcon_uses_documented_drive_probe(monkeypatch, tmp_path):
    makemkvcon = tmp_path / "makemkvcon.exe"
    makemkvcon.write_text("x", encoding="utf-8")
    seen = {}

    class _Result:
        returncode = 0
        stdout = b"DRV:0,2,999,1,\"Drive\",\"Disc\",\"F:\""
        stderr = b""

    def _fake_run(command, **kwargs):
        seen["command"] = command
        return _Result()

    monkeypatch.setattr(config.subprocess, "run", _fake_run)

    ok, reason = config.validate_makemkvcon(str(makemkvcon))

    assert ok is True
    assert reason == ""
    assert seen["command"][1:] == ["-r", "--cache=1", "info", "disc:9999"]


def test_should_keep_current_tool_path_when_new_is_invalid():
    def validator(path):
        return (path == "good.exe", "bad")

    keep_current = config.should_keep_current_tool_path(
        "good.exe",
        "bad.exe",
        validator,
    )

    assert keep_current is True


def test_should_not_keep_current_when_existing_path_is_not_working():
    def validator(path):
        return (path == "new-good.exe", "bad")

    keep_current = config.should_keep_current_tool_path(
        "old-bad.exe",
        "new-good.exe",
        validator,
    )

    assert keep_current is False


def test_ripper_engine_validate_tools_accepts_resolved_binaries(tmp_path, monkeypatch):
    makemkvcon = tmp_path / "makemkvcon.exe"
    ffprobe = tmp_path / "ffprobe.exe"
    _touch(makemkvcon)
    _touch(ffprobe)

    engine = RipperEngine(
        {
            "makemkvcon_path": "configured-makemkvcon",
            "ffprobe_path": "configured-ffprobe",
        }
    )

    monkeypatch.setattr(
        "engine.ripper_engine.resolve_makemkvcon",
        lambda _path: str(makemkvcon),
    )
    monkeypatch.setattr(
        "engine.ripper_engine.resolve_ffprobe",
        lambda _path: (str(ffprobe), "configured"),
    )

    ok, reason = engine.validate_tools()

    assert ok is True
    assert reason == ""
    assert engine._resolved_makemkvcon == os.path.normpath(str(makemkvcon))
    assert engine._resolved_makemkvcon_src == os.path.normpath("configured-makemkvcon")
    assert engine._ffprobe_source == "configured"
