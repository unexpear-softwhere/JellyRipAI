from pathlib import Path

from shared.ai import diagnostics
from shared.ai_diagnostics import DiagnosticsManager


def test_system_log_dir_uses_ai_localappdata_root(monkeypatch, tmp_path):
    local_appdata = tmp_path / "localappdata"
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setattr(diagnostics.platform, "system", lambda: "Windows")

    log_dir = Path(diagnostics._system_log_dir())

    assert log_dir == local_appdata / "JellyRipAI" / "logs"
    assert log_dir.is_dir()
    assert not (local_appdata / "JellyRip").exists()


def test_diagnostics_manager_respects_save_logs_disabled(monkeypatch, tmp_path):
    local_appdata = tmp_path / "localappdata"
    session_dir = tmp_path / "session"
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setattr(diagnostics.platform, "system", lambda: "Windows")

    manager = DiagnosticsManager(
        config={
            "opt_save_logs": False,
            "opt_ai_diagnostics_enabled": False,
            "opt_ai_log_to_file": True,
        },
        session_dir=str(session_dir),
    )

    manager.record("info", "test", "not persisted")
    manager._write_ai_log("not persisted")
    manager._write_state_json()

    assert manager.dump_ring_buffer() == ""
    assert not session_dir.exists()
    assert not local_appdata.exists()
