"""Tests for the Everyday + Reliability settings tabs.

Pins:

* Each tab renders a checkbox/spinbox/combo for every documented
  cfg key with sensible defaults.
* ``apply()`` writes every widget's current value into cfg + calls
  ``save_cfg`` (best-effort).
* ``cancel()`` resets every widget to the snapshot taken at
  construction.
* The Settings dialog hosts all four tabs in the documented order:
  Everyday, Paths, Reliability, Appearance.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QCheckBox, QComboBox, QLineEdit, QSpinBox

from gui_qt.settings.tab_everyday import EverydayTab
from gui_qt.settings.tab_reliability import ReliabilityTab


# ─── Everyday tab ──────────────────────────────────────────────────


@pytest.fixture
def everyday_cfg() -> dict:
    """Cfg with every documented Everyday key explicitly set so the
    tab renders without falling back to defaults."""
    return {
        "opt_save_logs": True,
        "opt_confirm_before_rip": True,
        "opt_confirm_before_move": True,
        "opt_show_temp_manager": True,
        "opt_auto_delete_temp": True,
        "opt_auto_delete_session_metadata": True,
        "opt_clean_partials_startup": True,
        "opt_warn_out_of_order_episodes": True,
        "opt_naming_mode": "timestamp",
        "opt_extras_folder_mode": "single",
        "opt_bonus_folder_name": "featurettes",
        "opt_smart_rip_mode": False,
        "opt_smart_min_minutes": 20,
        "opt_session_failure_report": True,
        "opt_allow_path_tool_resolution": False,
    }


def test_everyday_tab_renders_all_checkboxes(qtbot, everyday_cfg):
    tab = EverydayTab(cfg=everyday_cfg)
    qtbot.addWidget(tab)

    expected_checkboxes = (
        "opt_save_logs",
        "opt_confirm_before_rip",
        "opt_confirm_before_move",
        "opt_show_temp_manager",
        "opt_auto_delete_temp",
        "opt_auto_delete_session_metadata",
        "opt_clean_partials_startup",
        "opt_warn_out_of_order_episodes",
        "opt_smart_rip_mode",
        "opt_session_failure_report",
        "opt_allow_path_tool_resolution",
    )
    for key in expected_checkboxes:
        cb = tab.findChild(QCheckBox, f"settingsCheck_{key}")
        assert cb is not None, f"missing checkbox for {key!r}"


def test_everyday_tab_renders_naming_combo(qtbot, everyday_cfg):
    tab = EverydayTab(cfg=everyday_cfg)
    qtbot.addWidget(tab)
    combo = tab.findChild(QComboBox, "settingsCombo_opt_naming_mode")
    assert combo is not None
    # Default cfg has "timestamp" which should be selected.
    assert combo.currentData() == "timestamp"


def test_everyday_apply_writes_all_widgets_to_cfg(qtbot, everyday_cfg):
    saved: list[dict] = []

    tab = EverydayTab(
        cfg=everyday_cfg,
        save_cfg=lambda c: saved.append(dict(c)),
    )
    qtbot.addWidget(tab)

    # Flip a checkbox, change the combo, edit the spinbox + line edit.
    tab._checkboxes["opt_save_logs"].setChecked(False)
    tab._combos["opt_naming_mode"].setCurrentIndex(1)  # "disc"
    tab._spinboxes["opt_smart_min_minutes"].setValue(45)
    tab._lineedits["opt_bonus_folder_name"].setText("Specials")

    tab.apply()

    assert everyday_cfg["opt_save_logs"] is False
    assert everyday_cfg["opt_naming_mode"] == "disc"
    assert everyday_cfg["opt_smart_min_minutes"] == 45
    assert everyday_cfg["opt_bonus_folder_name"] == "Specials"
    assert len(saved) == 1


def test_everyday_cancel_resets_widgets(qtbot, everyday_cfg):
    tab = EverydayTab(cfg=everyday_cfg)
    qtbot.addWidget(tab)

    original_save_logs = tab._checkboxes["opt_save_logs"].isChecked()
    original_naming = tab._combos["opt_naming_mode"].currentData()

    # User edits.
    tab._checkboxes["opt_save_logs"].setChecked(not original_save_logs)
    tab._combos["opt_naming_mode"].setCurrentIndex(1)

    tab.cancel()

    assert tab._checkboxes["opt_save_logs"].isChecked() == original_save_logs
    assert tab._combos["opt_naming_mode"].currentData() == original_naming
    # cfg untouched.
    assert everyday_cfg["opt_save_logs"] == original_save_logs
    assert everyday_cfg["opt_naming_mode"] == original_naming


def test_everyday_apply_handles_missing_save_cfg(qtbot, everyday_cfg):
    """Calling apply() without a save_cfg callable still writes to cfg."""
    tab = EverydayTab(cfg=everyday_cfg, save_cfg=None)
    qtbot.addWidget(tab)
    tab._checkboxes["opt_save_logs"].setChecked(False)
    tab.apply()
    assert everyday_cfg["opt_save_logs"] is False


def test_everyday_apply_save_failure_does_not_break(qtbot, everyday_cfg):
    def boom(_c):
        raise OSError("disk full")

    tab = EverydayTab(cfg=everyday_cfg, save_cfg=boom)
    qtbot.addWidget(tab)
    tab._checkboxes["opt_save_logs"].setChecked(False)

    # Should not raise.
    tab.apply()
    assert everyday_cfg["opt_save_logs"] is False


# ─── Reliability tab ───────────────────────────────────────────────


@pytest.fixture
def reliability_cfg() -> dict:
    return {
        "opt_stall_detection": True,
        "opt_stall_timeout_seconds": 120,
        "opt_file_stabilization": True,
        "opt_stabilize_timeout_seconds": 60,
        "opt_stabilize_required_polls": 4,
        "opt_auto_retry": True,
        "opt_clean_mkv_before_retry": True,
        "opt_retry_attempts": 3,
        "opt_min_rip_size_gb": 1,
        "opt_expected_size_ratio_pct": 70,
        "opt_hard_fail_ratio_pct": 40,
        "opt_check_dest_space": True,
        "opt_warn_low_space": True,
        "opt_hard_block_gb": 20,
        "opt_atomic_move": True,
        "opt_fsync": True,
        "opt_move_verify_retries": 5,
        "opt_minlength_seconds": 0,
    }


def test_reliability_tab_renders_all_documented_keys(qtbot, reliability_cfg):
    tab = ReliabilityTab(cfg=reliability_cfg)
    qtbot.addWidget(tab)

    expected_checkboxes = (
        "opt_stall_detection",
        "opt_file_stabilization",
        "opt_auto_retry",
        "opt_clean_mkv_before_retry",
        "opt_check_dest_space",
        "opt_warn_low_space",
        "opt_atomic_move",
        "opt_fsync",
    )
    for key in expected_checkboxes:
        cb = tab.findChild(QCheckBox, f"settingsCheck_{key}")
        assert cb is not None, f"missing checkbox for {key!r}"

    expected_spinboxes = (
        "opt_stall_timeout_seconds",
        "opt_stabilize_timeout_seconds",
        "opt_stabilize_required_polls",
        "opt_retry_attempts",
        "opt_min_rip_size_gb",
        "opt_expected_size_ratio_pct",
        "opt_hard_fail_ratio_pct",
        "opt_hard_block_gb",
        "opt_move_verify_retries",
        "opt_minlength_seconds",
    )
    for key in expected_spinboxes:
        spin = tab.findChild(QSpinBox, f"settingsSpin_{key}")
        assert spin is not None, f"missing spinbox for {key!r}"


def test_reliability_apply_writes_back(qtbot, reliability_cfg):
    saved: list[dict] = []
    tab = ReliabilityTab(
        cfg=reliability_cfg,
        save_cfg=lambda c: saved.append(dict(c)),
    )
    qtbot.addWidget(tab)

    tab._checkboxes["opt_atomic_move"].setChecked(False)
    tab._spinboxes["opt_hard_block_gb"].setValue(50)
    tab._spinboxes["opt_retry_attempts"].setValue(5)

    tab.apply()

    assert reliability_cfg["opt_atomic_move"] is False
    assert reliability_cfg["opt_hard_block_gb"] == 50
    assert reliability_cfg["opt_retry_attempts"] == 5
    assert len(saved) == 1


def test_reliability_cancel_resets(qtbot, reliability_cfg):
    tab = ReliabilityTab(cfg=reliability_cfg)
    qtbot.addWidget(tab)

    original = tab._spinboxes["opt_retry_attempts"].value()
    tab._spinboxes["opt_retry_attempts"].setValue(10)
    tab.cancel()
    assert tab._spinboxes["opt_retry_attempts"].value() == original
    assert reliability_cfg["opt_retry_attempts"] == original


def test_reliability_clamps_invalid_cfg_values(qtbot):
    """If cfg has out-of-range or non-int values, the tab clamps to
    the spinbox's range rather than crashing."""
    cfg = {
        "opt_retry_attempts": 999,  # well above max=10
        "opt_hard_block_gb": -50,   # below min=0
        "opt_stabilize_required_polls": "garbage",  # not even an int
    }
    tab = ReliabilityTab(cfg=cfg)
    qtbot.addWidget(tab)
    # Each clamped to the documented range.
    assert tab._spinboxes["opt_retry_attempts"].value() == 10
    assert tab._spinboxes["opt_hard_block_gb"].value() == 0
    # "garbage" → falls back to documented default 4.
    assert tab._spinboxes["opt_stabilize_required_polls"].value() == 4


# ─── Settings dialog integration ───────────────────────────────────


def test_settings_dialog_hosts_four_tabs_in_documented_order(qtbot):
    from gui_qt.settings.dialog import SettingsDialog
    from gui_qt.themes import theme_ids

    cfg = {}
    dlg = SettingsDialog(
        cfg=cfg,
        list_themes=lambda: list(theme_ids()),
        load_theme=lambda _name: None,
    )
    qtbot.addWidget(dlg)

    tabs = dlg._tabs
    assert tabs.count() == 4
    assert tabs.tabText(0) == "Everyday"
    assert tabs.tabText(1) == "Paths"
    assert tabs.tabText(2) == "Reliability"
    assert tabs.tabText(3) == "Appearance"


def test_settings_dialog_ok_applies_all_four_tabs(qtbot):
    """OK on the Settings dialog must apply Everyday + Paths +
    Reliability + Appearance, not just whichever is currently
    visible."""
    from gui_qt.settings.dialog import SettingsDialog
    from gui_qt.themes import theme_ids

    cfg = {
        "opt_save_logs": True,
        "opt_retry_attempts": 3,
        "temp_folder": r"C:\old",
    }
    dlg = SettingsDialog(
        cfg=cfg,
        list_themes=lambda: list(theme_ids()),
        load_theme=lambda _name: None,
    )
    qtbot.addWidget(dlg)

    # Edit a field in each non-Appearance tab without switching to it.
    dlg.everyday_tab._checkboxes["opt_save_logs"].setChecked(False)
    dlg.paths_tab._edits["temp_folder"].setText(r"E:\new")
    dlg.reliability_tab._spinboxes["opt_retry_attempts"].setValue(5)

    dlg._on_ok()

    assert cfg["opt_save_logs"] is False
    assert cfg["temp_folder"] == r"E:\new"
    assert cfg["opt_retry_attempts"] == 5
