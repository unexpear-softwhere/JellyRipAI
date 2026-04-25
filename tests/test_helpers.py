import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import helpers


def test_parse_makemkv_drive_row_preserves_full_identity():
    drive = helpers.parse_makemkv_drive_row(
        'DRV:0,2,999,12,"BD-RE HL-DT-ST BD-RE  WH16NS60 1.00 KLAM6E84217","STE_S1_D3","D:"'
    )

    assert drive is not None
    assert drive.index == 0
    assert drive.visible == 2
    assert drive.enabled == 999
    assert drive.flags == 12
    assert drive.drive_name == "BD-RE HL-DT-ST BD-RE  WH16NS60 1.00 KLAM6E84217"
    assert drive.disc_name == "STE_S1_D3"
    assert drive.device_path == "D:"
    assert drive.usability_state == "visible=2, enabled=999, flags=12"


def test_get_available_drives_parses_full_drv_rows(monkeypatch):
    seen = {}

    class _FakeStdout:
        def __init__(self, lines):
            self._lines = iter(lines)

        def readline(self):
            return next(self._lines, "")

    class _FakeProc:
        def __init__(self, lines):
            self.stdout = _FakeStdout(lines)

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    lines = [
        'DRV:0,0,999,0,"BD-RE HL-DT-ST BD-RE WH12LS38 1.00 K93B6JL2922","","E:"\n',
        'DRV:1,256,999,0,"","",""\n',
        "",
    ]

    monkeypatch.setattr(
        helpers.subprocess,
        "Popen",
        lambda *args, **kwargs: (
            seen.setdefault("command", args[0]),
            _FakeProc(lines),
        )[1],
    )

    drives = helpers.get_available_drives(r"C:\Trusted\makemkvcon64.exe")

    assert seen["command"][1:] == ["-r", "--cache=1", "info", "disc:9999"]
    assert len(drives) == 2
    assert drives[0].index == 0
    assert drives[0].drive_name == "BD-RE HL-DT-ST BD-RE WH12LS38 1.00 K93B6JL2922"
    assert drives[0].disc_name == ""
    assert drives[0].device_path == "E:"
    assert drives[0].usability_state == "visible=0, enabled=999, flags=0"
    assert drives[1].index == 1
    assert drives[1].drive_name == ""
    assert drives[1].disc_name == ""
    assert drives[1].device_path == ""
    assert drives[1].usability_state == "visible=256, enabled=999, flags=0"
