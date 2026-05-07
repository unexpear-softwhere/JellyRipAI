"""Settings UI for the PySide6 GUI (sub-phase 3d).

The dialog is a ``QTabWidget`` host.  Each tab is its own module
under this package so the migration can land tabs incrementally.

Currently shipping:

* ``tab_appearance.py`` — consolidated theme + UI customization
  (renamed from ``tab_themes.py`` 2026-05-04, see
  ``docs/handoffs/appearance-tab-spec.md``).  Lists the 6 themes
  generated in 3a-themes, swaps QSS at runtime via
  ``gui_qt.theme.load_theme``, plus checkboxes for log coloring,
  log glyph prefix, drive-state glyph, system tray, and splash.

Pending tabs (see ``docs/handoffs/phase-3d-port-settings-tabs.md``):

* ``tab_everyday.py`` — everyday options (paths, naming, etc.)
* ``tab_advanced.py`` — PATH lookup, debug flags
* ``tab_expert.py`` — transcode profile editor
"""

from gui_qt.settings.dialog import SettingsDialog, show_settings
from gui_qt.settings.tab_appearance import AppearanceTab, ThemesTab

__all__ = ["AppearanceTab", "SettingsDialog", "ThemesTab", "show_settings"]
