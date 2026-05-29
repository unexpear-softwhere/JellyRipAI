"""Embedded TMDB attribution logo (base64 PNG).

Shipped as a Python constant rather than a bundled data file so it is
always present in both source and frozen (PyInstaller) builds with no
.spec / datas plumbing.  Shown in Settings -> AI -> Web lookup to meet
TMDB's logo/attribution requirement.

Source: TMDB's official "primary long" logo (themoviedb.org).  Per
TMDB's terms it is displayed less prominently than JellyRip's own
branding and is labeled as non-endorsement.
"""

from __future__ import annotations

import base64

_TMDB_LOGO_PNG_B64 = (
    "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"
)


def tmdb_logo_pixmap(height: int = 18):
    """TMDB logo as a QPixmap scaled to ``height`` px (aspect kept).

    Returns an empty QPixmap on any failure - the logo is a nicety,
    never a crash risk."""
    from PySide6.QtGui import QPixmap

    try:
        from PySide6.QtCore import Qt

        pm = QPixmap()
        pm.loadFromData(base64.b64decode(_TMDB_LOGO_PNG_B64), "PNG")
        if pm.isNull():
            return pm
        return pm.scaledToHeight(
            int(height), Qt.TransformationMode.SmoothTransformation,
        )
    except Exception:
        return QPixmap()
