import io

from controller.controller import RipperController
from engine.scan_ops import _parse_drive_info
from gui.main_window import JellyRipperGUI
from utils import helpers


class _Var:
    def __init__(self, value: str = ""):
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


def test_parse_makemkv_drive_row_keeps_drive_disc_and_device_fields():
    drive = helpers.parse_makemkv_drive_row(
        'DRV:1,2,999,1,"HL-DT-ST, BD-RE WH16NS60","OVER THE HEDGE","F:"'
    )

    assert drive == helpers.MakeMKVDrive(
        index=1,
        visible=2,
        enabled=999,
        flags=1,
        drive_name="HL-DT-ST, BD-RE WH16NS60",
        disc_name="OVER THE HEDGE",
        device_path="F:",
    )
    assert drive.usability_state == "visible=2, enabled=999, flags=1"
    assert (
        helpers.format_makemkv_drive_label(drive)
        == "Drive 1: HL-DT-ST, BD-RE WH16NS60 [F:] | Disc: OVER THE HEDGE"
    )


def test_get_available_drives_uses_structured_drv_rows(monkeypatch):
    seen = {}

    class _Proc:
        def __init__(self):
            self.stdout = io.StringIO(
                'DRV:0,2,999,1,"PIONEER BD-RW BDR-XS07U","DISC_A","E:"\n'
                'DRV:1,2,999,1,"LG WH16NS60","DISC_B","F:"\n'
            )
            self.returncode = 0

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    def _fake_popen(command, **kwargs):
        seen["command"] = command
        return _Proc()

    monkeypatch.setattr(helpers._sys, "platform", "linux")
    monkeypatch.setattr(helpers.subprocess, "Popen", _fake_popen)

    drives = helpers.get_available_drives("makemkvcon")

    assert seen["command"] == ["makemkvcon", "-r", "--cache=1", "info", "disc:9999"]
    assert [drive.index for drive in drives] == [0, 1]
    assert drives[0].drive_name == "PIONEER BD-RW BDR-XS07U"
    assert drives[1].disc_name == "DISC_B"
    assert drives[1].device_path == "F:"


def test_get_available_drives_can_disable_default_fallback(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise OSError("enumeration failed")

    monkeypatch.setattr(helpers._sys, "platform", "linux")
    monkeypatch.setattr(helpers.subprocess, "Popen", _boom)

    assert helpers.get_available_drives("makemkvcon", allow_fallback=False) == []


def test_update_drive_menu_uses_drive_identity_instead_of_disc_label():
    gui = object.__new__(JellyRipperGUI)
    gui.cfg = {"opt_drive_index": 0}
    gui.drive_var = _Var()
    gui.drive_menu = {}

    JellyRipperGUI._update_drive_menu(
        gui,
        [
            helpers.MakeMKVDrive(
                index=0,
                visible=2,
                enabled=999,
                flags=1,
                drive_name="LG WH16NS60",
                disc_name="OVER THE HEDGE",
                device_path="F:",
            )
        ],
    )

    assert gui.drive_var.get() == "Drive 0: LG WH16NS60 [F:] | Disc: OVER THE HEDGE"
    assert gui.drive_menu["values"] == [
        "Drive 0: LG WH16NS60 [F:] | Disc: OVER THE HEDGE"
    ]


def test_update_drive_menu_falls_back_to_default_drive_when_empty():
    gui = object.__new__(JellyRipperGUI)
    gui.cfg = {"opt_drive_index": 0}
    gui.drive_var = _Var()
    gui.drive_menu = {}

    JellyRipperGUI._update_drive_menu(gui, [])

    assert gui.drive_var.get() == "Drive 0: Default Drive [disc:0]"
    assert gui.drive_menu["values"] == ["Drive 0: Default Drive [disc:0]"]


def test_parse_drive_info_merges_selected_drive_row_and_ai_facts():
    info = _parse_drive_info(
        ["LibreDrive enabled", "BDMV directory present"],
        drive_rows=[
            helpers.MakeMKVDrive(
                index=0,
                visible=2,
                enabled=999,
                flags=1,
                drive_name="Drive Zero",
                disc_name="DISC_ZERO",
                device_path="E:",
            ),
            helpers.MakeMKVDrive(
                index=1,
                visible=2,
                enabled=999,
                flags=5,
                drive_name="Drive One",
                disc_name="DISC_ONE",
                device_path="F:",
            ),
        ],
        selected_drive_index=1,
    )

    facts = RipperController._build_ai_drive_facts(info)

    assert info["drive_index"] == 1
    assert info["drive_name"] == "Drive One"
    assert info["disc_name"] == "DISC_ONE"
    assert info["device_path"] == "F:"
    assert info["usability_state"] == "visible=2, enabled=999, flags=5"
    assert facts["drive_name"] == "Drive One"
    assert facts["disc_name"] == "DISC_ONE"
    assert facts["device_path"] == "F:"
    assert facts["drive_index"] == 1
    assert facts["flags"] == 5
