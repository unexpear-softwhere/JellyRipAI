"""Shared modal dialog helpers."""

from __future__ import annotations

from tkinter import messagebox


def ask_yes_no(title: str, message: str, *, parent=None, icon: str | None = None) -> bool:
    kwargs = {"parent": parent}
    if icon:
        kwargs["icon"] = icon
    return bool(messagebox.askyesno(title, message, **kwargs))
