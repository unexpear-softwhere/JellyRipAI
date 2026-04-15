from pathlib import Path

from shared.ai import diagnostics


def test_system_log_dir_uses_ai_localappdata_root(monkeypatch, tmp_path):
    local_appdata = tmp_path / "localappdata"
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setattr(diagnostics.platform, "system", lambda: "Windows")

    log_dir = Path(diagnostics._system_log_dir())

    assert log_dir == local_appdata / "JellyRipAI" / "logs"
    assert log_dir.is_dir()
    assert not (local_appdata / "JellyRip").exists()
