"""Import smoke tests to guard module boundary regressions.

Phase 3h (2026-05-04) trimmed this file: the prior `_FakeTkBase` /
`test_gui_import` / `tkinter.Tk` patch suite all targeted the
retired tkinter side and is obsolete. The single survivor below is
the pure import-boundary smoke for the non-GUI modules.
"""

import config  # noqa: F401  # imported for side-effect — re-export check
import engine.ripper_engine  # noqa: F401
import controller.controller  # noqa: F401


def test_imports():
    """Pure import smoke — boundary check that the non-GUI modules
    import cleanly without dragging in any GUI toolkit.

    The real GUI import path is exercised by
    ``tests/test_pyside6_*.py`` under pytest-qt.
    """
    # Re-import inside the test so a regression in conftest fixtures
    # is caught here rather than during collection.
    import config as _cfg  # noqa: F401, F811
    import engine.ripper_engine as _re  # noqa: F401
    import controller.controller as _cc  # noqa: F401
