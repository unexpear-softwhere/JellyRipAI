"""Tk helpers for hardened application startup."""

from __future__ import annotations

import tkinter as tk


class SecureTk(tk.Tk):
    """Tk root that ignores profile-script loading from HOME or cwd."""

    def readprofile(self, _base_name, _class_name) -> None:
        return None


__all__ = ["SecureTk"]
