from core.media_scan import (
    build_folder_scan_log_path,
    build_folder_scan_request,
    build_folder_scan_results_model,
    select_folder_scan_entries,
    select_folder_scan_paths,
)
from config import ResolvedTool
from tools.folder_scanner import FolderScanEntry


def _entry(
    *,
    name: str,
    path: str,
    relative_path: str,
) -> FolderScanEntry:
    return {
        "name": name,
        "path": path,
        "relative_path": relative_path,
        "size": 0,
        "size_str": "0 bytes",
        "is_dir": False,
        "bad_name": False,
        "parent": None,
        "status": "OK",
        "modified_ts": 0.0,
        "modified_str": "n/a",
        "duration_seconds": None,
        "duration_str": "n/a",
    }


def test_build_folder_scan_request_resolves_duration_scan_ffprobe(monkeypatch, tmp_path):
    ffprobe_path = tmp_path / "ffmpeg" / "ffprobe.exe"
    ffprobe_path.parent.mkdir(parents=True, exist_ok=True)
    ffprobe_path.write_text("stub", encoding="utf-8")
    seen = {}

    def _fake_resolve(configured_path, *, allow_path_lookup=False):
        seen["configured_path"] = configured_path
        seen["allow_path_lookup"] = allow_path_lookup
        return ResolvedTool(path=str(ffprobe_path), source="configured")

    monkeypatch.setattr("core.media_scan.resolve_ffprobe", _fake_resolve)

    request = build_folder_scan_request(
        folder=str(tmp_path / "library"),
        scan_options={"mode": "duration_desc", "recursive": False},
        main_log=str(tmp_path / "logs" / "rip_log.txt"),
        ffprobe_path=str(ffprobe_path.parent),
        include_dirs=False,
    )

    assert request.mode == "duration_desc"
    assert request.recursive is False
    assert request.include_dirs is False
    assert seen["configured_path"] == str(ffprobe_path.parent)
    assert seen["allow_path_lookup"] is False
    assert request.ffprobe_exe == str(ffprobe_path)
    assert request.log_path.endswith("folder_scan_log.txt")


def test_build_folder_scan_request_honors_path_lookup_toggle(monkeypatch, tmp_path):
    ffprobe_path = tmp_path / "ffmpeg" / "ffprobe.exe"
    ffprobe_path.parent.mkdir(parents=True, exist_ok=True)
    ffprobe_path.write_text("stub", encoding="utf-8")
    seen = {}

    def _fake_resolve(configured_path, *, allow_path_lookup=False):
        seen["configured_path"] = configured_path
        seen["allow_path_lookup"] = allow_path_lookup
        return ResolvedTool(path=str(ffprobe_path), source="PATH via advanced toggle")

    monkeypatch.setattr("core.media_scan.resolve_ffprobe", _fake_resolve)

    request = build_folder_scan_request(
        folder=str(tmp_path / "library"),
        scan_options={"mode": "duration_desc", "recursive": True},
        main_log="",
        ffprobe_path=str(ffprobe_path.parent),
        include_dirs=False,
        allow_path_lookup=True,
    )

    assert seen["configured_path"] == str(ffprobe_path.parent)
    assert seen["allow_path_lookup"] is True
    assert request.ffprobe_exe == str(ffprobe_path)


def test_build_folder_scan_log_path_uses_directory_when_main_log_is_folder(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    path = build_folder_scan_log_path(str(log_dir))

    assert path == str(log_dir / "folder_scan_log.txt")


def test_build_folder_scan_log_path_uses_absolute_parent_for_relative_log_file(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(tmp_path)

    path = build_folder_scan_log_path("jellyrip.log")

    assert path == str(tmp_path / "folder_scan_log.txt")


def test_build_folder_scan_request_skips_ffprobe_for_non_duration_modes(monkeypatch, tmp_path):
    def _unexpected(_configured_path, *, allow_path_lookup=False):
        raise AssertionError("resolve_ffprobe should not run for size-only scans")

    monkeypatch.setattr("core.media_scan.resolve_ffprobe", _unexpected)

    request = build_folder_scan_request(
        folder=str(tmp_path / "library"),
        scan_options={"mode": "size_desc", "recursive": True},
        main_log="",
        ffprobe_path="",
        include_dirs=False,
        home_dir=str(tmp_path / "home"),
    )

    assert request.ffprobe_exe is None
    assert request.recursive is True
    assert request.log_path == str(tmp_path / "home" / "folder_scan_log.txt")


def test_build_folder_scan_results_model_creates_rows_and_subtitle():
    results = [
        _entry(
            name="Episode 01.mkv",
            path=r"C:\media\Episode 01.mkv",
            relative_path="Episode 01.mkv",
        ),
        _entry(
            name="Episode 02.mkv",
            path=r"C:\media\Season 01\Episode 02.mkv",
            relative_path=r"Season 01\Episode 02.mkv",
        ),
    ]

    model = build_folder_scan_results_model(
        results,
        {"mode": "size_desc", "recursive": True},
    )

    assert model.subtitle == (
        "Sort: Largest to Smallest | Scope: Recursive | MKV files only"
    )
    assert model.status_text == "2 MKV file(s) found"
    assert [row["iid"] for row in model.rows] == ["scan_0", "scan_1"]
    assert model.rows[0]["values"][1] == "."
    assert model.rows[1]["values"][1] == r"Season 01"


def test_select_folder_scan_entries_and_paths_preserve_row_order():
    rows = build_folder_scan_results_model(
        [
            _entry(
                name="A.mkv",
                path=r"C:\media\A.mkv",
                relative_path="A.mkv",
            ),
            _entry(
                name="B.mkv",
                path=r"C:\media\B.mkv",
                relative_path="B.mkv",
            ),
        ],
        {"mode": "name_asc", "recursive": False},
    ).rows

    selected_entries = select_folder_scan_entries(rows, ["scan_1", "scan_0"])
    selected_paths = select_folder_scan_paths(rows, ["scan_1", "scan_0"])

    assert [entry["name"] for entry in selected_entries] == ["A.mkv", "B.mkv"]
    assert selected_paths == [r"C:\media\A.mkv", r"C:\media\B.mkv"]
