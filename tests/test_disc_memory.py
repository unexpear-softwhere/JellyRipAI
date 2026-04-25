from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.ripper_engine import RipperEngine
from shared.disc_memory import build_disc_memory_record


def _engine_cfg(**overrides):
    cfg = {
        "makemkvcon_path": "makemkvcon",
        "ffprobe_path": "ffprobe",
        "opt_makemkv_global_args": "",
        "opt_makemkv_info_args": "",
        "opt_makemkv_rip_args": "",
        "opt_drive_index": 0,
        "opt_auto_retry": True,
        "opt_retry_attempts": 3,
        "opt_clean_mkv_before_retry": True,
    }
    cfg.update(overrides)
    return cfg


def _sample_titles():
    return [
        {
            "id": 7,
            "duration_seconds": 5400,
            "size_bytes": 10_000_000_000,
            "chapters": 12,
            "audio_tracks": [
                {
                    "codec": "DTS-HD MA",
                    "lang": "eng",
                    "lang_name": "English",
                    "channels": "5.1",
                }
            ],
            "subtitle_tracks": [
                {"lang": "eng", "lang_name": "English"},
                {"lang": "spa", "lang_name": "Spanish"},
            ],
        },
        {
            "id": 2,
            "duration_seconds": 420,
            "size_bytes": 700_000_000,
            "chapters": 3,
            "audio_tracks": [
                {
                    "codec": "AC3",
                    "lang": "eng",
                    "lang_name": "English",
                    "channels": "2.0",
                }
            ],
            "subtitle_tracks": [],
        },
    ]


def test_build_disc_memory_record_is_order_stable_and_track_sensitive():
    disc_info = {
        "title": "Demo Disc",
        "volume_id": "VOL001",
        "lang_name": "English",
        "size_signature": "10000000000,700000000",
    }

    record_a = build_disc_memory_record(_sample_titles(), disc_info)
    record_b = build_disc_memory_record(list(reversed(_sample_titles())), disc_info)
    mutated_titles = _sample_titles()
    mutated_titles[0]["audio_tracks"][0]["codec"] = "TrueHD"
    record_c = build_disc_memory_record(mutated_titles, disc_info)

    assert record_a is not None
    assert record_b is not None
    assert record_c is not None
    assert record_a["structure_hash"] == record_b["structure_hash"]
    assert record_a["identity_hash"] == record_b["identity_hash"]
    assert record_a["structure_hash"] != record_c["structure_hash"]
    assert record_a["identity_hash"] != record_c["identity_hash"]


def test_commit_current_disc_memory_persists_cross_session_memory(tmp_path, monkeypatch):
    monkeypatch.setattr("shared.runtime.get_config_dir", lambda create=True: str(tmp_path))
    engine = RipperEngine(_engine_cfg())

    record = engine.remember_last_disc_scan(
        _sample_titles(),
        disc_info={
            "title": "Demo Disc",
            "volume_id": "VOL001",
            "size_signature": "10000000000,700000000",
        },
    )

    assert record is not None
    assert engine.current_disc_memory is not None
    assert not Path(tmp_path, "last_disc_memory.json").exists()

    engine.commit_current_disc_memory()

    saved_path = Path(tmp_path, "last_disc_memory.json")
    assert saved_path.exists()

    saved = json.loads(saved_path.read_text(encoding="utf-8"))
    loaded = engine.read_last_disc_memory()

    assert saved["identity_hash"] == record["identity_hash"]
    assert saved["structure_hash"] == record["structure_hash"]
    assert loaded is not None
    assert loaded["disc_title"] == "Demo Disc"
    assert loaded["volume_id"] == "VOL001"


def test_match_last_disc_memory_can_fall_back_to_structure_match(tmp_path, monkeypatch):
    monkeypatch.setattr("shared.runtime.get_config_dir", lambda create=True: str(tmp_path))
    engine = RipperEngine(_engine_cfg())

    engine.remember_last_disc_scan(
        _sample_titles(),
        disc_info={
            "title": "Demo Disc",
            "volume_id": "VOL001",
            "size_signature": "10000000000,700000000",
        },
    )
    engine.commit_current_disc_memory()
    engine.remember_last_disc_scan(
        _sample_titles(),
        disc_info={
            "title": "Different Title",
            "volume_id": "VOL999",
            "size_signature": "10000000000,700000000",
        },
    )

    match = engine.match_last_disc_memory(
        _sample_titles(),
        disc_info={
            "title": "Different Title",
            "volume_id": "VOL999",
            "size_signature": "10000000000,700000000",
        },
    )

    assert match is not None
    assert match["match_type"] == "structure"
    assert match["saved"]["disc_title"] == "Demo Disc"


def test_scan_disc_persists_last_disc_memory(tmp_path, monkeypatch):
    monkeypatch.setattr("shared.runtime.get_config_dir", lambda create=True: str(tmp_path))
    engine = RipperEngine(_engine_cfg())

    class _FakeStdout:
        def __init__(self):
            self._lines = iter(
                [
                    'CINFO:2,0,"Demo Disc"\n',
                    'CINFO:32,0,"VOL001"\n',
                    'TINFO:0,2,0,"Main Feature"\n',
                    'TINFO:0,9,0,"01:30:00"\n',
                    'TINFO:0,8,0,"12"\n',
                    'TINFO:0,11,0,"10.0 GB"\n',
                    'SINFO:0,1,1,0,"Audio"\n',
                    'SINFO:0,1,2,0,"DTS-HD MA"\n',
                    'SINFO:0,1,3,0,"eng"\n',
                    'SINFO:0,1,4,0,"5.1"\n',
                    'SINFO:0,1,21,0,"English"\n',
                    "",
                ]
            )

        def readline(self):
            return next(self._lines, "")

    class _FakeProc:
        def __init__(self):
            self.stdout = _FakeStdout()
            self.returncode = 0

        def wait(self, timeout=None):
            _ = timeout
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(
        "engine.ripper_engine.subprocess.Popen",
        lambda *args, **kwargs: _FakeProc(),
    )

    result = engine.scan_disc(lambda _msg: None, lambda _value: None)

    assert result
    assert engine.current_disc_memory is not None
    assert not Path(tmp_path, "last_disc_memory.json").exists()

    engine.commit_current_disc_memory()

    saved = json.loads(
        Path(tmp_path, "last_disc_memory.json").read_text(encoding="utf-8")
    )
    assert saved["disc_title"] == "Demo Disc"
    assert saved["volume_id"] == "VOL001"
    assert saved["identity_hash"] == engine.current_disc_memory["identity_hash"]


def test_update_last_disc_session_state_merges_onto_persisted_record(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("shared.runtime.get_config_dir", lambda create=True: str(tmp_path))
    engine = RipperEngine(_engine_cfg())

    engine.remember_last_disc_scan(
        _sample_titles(),
        disc_info={
            "title": "Demo Disc",
            "volume_id": "VOL001",
            "size_signature": "10000000000,700000000",
        },
    )
    engine.commit_current_disc_memory()

    updated = engine.update_last_disc_session_state(
        {
            "media_type": "movie",
            "title": "Finding Nemo",
            "year": "2003",
            "selected_title_ids": [7],
        }
    )

    assert updated is not None
    saved = json.loads(
        Path(tmp_path, "last_disc_memory.json").read_text(encoding="utf-8")
    )
    assert saved["session_info"]["title"] == "Finding Nemo"
    assert saved["session_info"]["year"] == "2003"
    assert saved["session_info"]["selected_title_ids"] == [7]


def test_update_last_disc_session_state_persists_nested_session_state(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("shared.runtime.get_config_dir", lambda create=True: str(tmp_path))
    engine = RipperEngine(_engine_cfg())

    engine.remember_last_disc_scan(
        _sample_titles(),
        disc_info={
            "title": "Demo Disc",
            "volume_id": "VOL001",
            "size_signature": "10000000000,700000000",
        },
    )
    engine.commit_current_disc_memory()

    updated = engine.update_last_disc_session_state(
        {
            "run_path_overrides": {
                "movies_folder": r"C:\Media\Movies",
                "tv_folder": r"C:\Media\TV",
                "temp_folder": r"C:\Temp\Rip",
            },
            "session_paths": {
                "movies": r"C:\Media\Movies",
                "tv": r"C:\Media\TV",
                "temp": r"C:\Temp\Rip",
            },
            "output_plan": {
                "dest_folder": r"C:\Media\Movies\Finding Nemo (2003)",
                "main_label": "Finding Nemo (2003).mkv",
                "extras_preview": {"Featurettes": ["Title 2.mkv"]},
                "confirmed": False,
            },
        }
    )

    assert updated is not None
    saved = json.loads(
        Path(tmp_path, "last_disc_memory.json").read_text(encoding="utf-8")
    )
    assert saved["session_info"]["run_path_overrides"]["movies_folder"] == r"C:\Media\Movies"
    assert saved["session_info"]["session_paths"]["temp"] == r"C:\Temp\Rip"
    assert saved["session_info"]["output_plan"]["confirmed"] is False
    assert saved["session_info"]["output_plan"]["extras_preview"]["Featurettes"] == [
        "Title 2.mkv"
    ]
