from pathlib import Path

from engine.ripper_engine import RipperEngine


def _engine_cfg(**overrides):
    cfg = {
        "makemkvcon_path": "makemkvcon",
        "ffprobe_path": "ffprobe",
        "opt_makemkv_global_args": "",
        "opt_makemkv_rip_args": "",
        "opt_drive_index": 0,
        "opt_auto_retry": True,
        "opt_retry_attempts": 3,
        "opt_clean_mkv_before_retry": True,
        "opt_drive_probe_retries": 5,
        "opt_drive_probe_backoff_seconds": 1.0,
    }
    cfg.update(overrides)
    return cfg


def test_rip_all_titles_waits_for_drive_probe_before_launch(monkeypatch, tmp_path):
    engine = RipperEngine(_engine_cfg(opt_auto_retry=False, opt_retry_attempts=1))
    probe_calls = []
    sleep_calls = []
    run_calls = []
    logs = []

    monkeypatch.setattr(
        "engine.ripper_engine.get_available_drives",
        lambda *_args, **_kwargs: probe_calls.append("probe") or [],
    )
    monkeypatch.setattr(
        "engine.ripper_engine.time.sleep",
        lambda seconds: sleep_calls.append(seconds),
    )
    monkeypatch.setattr(
        engine,
        "_run_rip_process",
        lambda *_args, **_kwargs: run_calls.append("run") or True,
    )

    success = engine.rip_all_titles(
        str(tmp_path),
        on_progress=lambda _pct: None,
        on_log=logs.append,
    )

    assert success is False
    assert run_calls == []
    assert len(probe_calls) == 5
    assert sleep_calls == [1.0, 2.0, 4.0, 8.0]
    assert any("Drive probe 5/5 failed" in line for line in logs)
    assert any("Drive did not become ready before rip launch." in line for line in logs)


def test_rip_selected_titles_reprobes_before_each_title_retry(monkeypatch, tmp_path):
    engine = RipperEngine(_engine_cfg(opt_auto_retry=True, opt_retry_attempts=2))
    probe_contexts = []
    run_calls = []

    monkeypatch.setattr(
        engine,
        "_wait_for_drive_ready",
        lambda _on_log, *, context: probe_contexts.append(context) or True,
    )

    outcomes = iter([False, True])

    def _fake_run(cmd, _on_progress, _on_log):
        run_calls.append(list(cmd))
        success = next(outcomes)
        if success:
            tid = int(cmd[3])
            Path(tmp_path, f"title_t{tid:02d}.mkv").write_text("ok", encoding="utf-8")
        return success

    monkeypatch.setattr(engine, "_run_rip_process", _fake_run)

    success, failed = engine.rip_selected_titles(
        str(tmp_path),
        [0],
        on_progress=lambda _pct: None,
        on_log=lambda _message: None,
    )

    assert success is True
    assert failed == []
    assert probe_contexts == [
        "title 1 launch 1/2 (1/1)",
        "title 1 launch 2/2 (1/1)",
    ]
    assert len(run_calls) == 2
