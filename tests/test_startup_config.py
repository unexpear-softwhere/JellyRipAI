import importlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import shared.runtime as runtime


def test_load_startup_config_recovers_blank_required_paths(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "temp_folder": "",
                "tv_folder": "   ",
                "movies_folder": "",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CONFIG_FILE", str(cfg_path))

    result = config.load_startup_config()

    assert result.open_settings is True
    assert len(result.issues) == 1
    assert result.issues[0].code == "config_missing_required_paths"
    assert result.config["temp_folder"] == config.DEFAULTS["temp_folder"]
    assert result.config["tv_folder"] == config.DEFAULTS["tv_folder"]
    assert result.config["movies_folder"] == config.DEFAULTS["movies_folder"]


def test_load_startup_config_recovers_malformed_json(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("{bad json", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_FILE", str(cfg_path))

    result = config.load_startup_config()

    assert result.open_settings is True
    assert [issue.code for issue in result.issues] == ["config_malformed"]
    assert result.config["temp_folder"] == config.DEFAULTS["temp_folder"]


def test_load_startup_config_recovers_invalid_path_value_types(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "temp_folder": 123,
                "ffprobe_path": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CONFIG_FILE", str(cfg_path))

    result = config.load_startup_config()

    assert result.open_settings is True
    assert [issue.code for issue in result.issues] == ["config_invalid_path_values"]
    assert result.config["temp_folder"] == config.DEFAULTS["temp_folder"]
    assert result.config["ffprobe_path"] == config.DEFAULTS["ffprobe_path"]


def test_runtime_import_does_not_create_config_dir(monkeypatch):
    calls = []

    def _fake_makedirs(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(os, "makedirs", _fake_makedirs)
    importlib.reload(runtime)

    assert calls == []
    assert runtime.CONFIG_FILE.endswith("config.json")
