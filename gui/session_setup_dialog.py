"""Session setup dialogs for movie and TV rip workflows.

Each dialog is a Toplevel that blocks until the user confirms or cancels.
These are called from worker threads via JellyRipperGUI.ask_movie_setup() /
ask_tv_setup() — never instantiated directly from worker threads.
"""
from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk

from gui.theme import dialog_palette

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class MovieSessionSetup:
    title: str
    year: str
    edition: str          # "" | "Theatrical Cut" | "Director's Cut" | etc.
    metadata_provider: str  # "TMDB" | "OpenDB"
    metadata_id: str      # raw ID value entered by user
    replace_existing: bool
    keep_raw: bool
    extras_mode: str      # "ask" | "keep" | "skip"


@dataclass
class TVSessionSetup:
    title: str
    year: str
    season: int
    starting_disc: int
    episode_mapping: str  # "auto" | "manual"
    metadata_provider: str  # "TMDB" | "OpenDB"
    metadata_id: str
    multi_episode: str    # "auto" | "split" | "merge"
    specials: str         # "ask" | "season0" | "skip"
    replace_existing: bool
    keep_raw: bool


# ---------------------------------------------------------------------------
# Internal style constants
# ---------------------------------------------------------------------------

_COLORS = dialog_palette()
_BG        = _COLORS["surface_deep"]
_BG2       = _COLORS["surface"]
_BG3       = _COLORS["border"]
_FG        = _COLORS["text"]
_FG_DIM    = _COLORS["muted"]
_ACCENT    = _COLORS["accent"]
_GREEN     = _COLORS["primary_button_bg"]
_CANCEL_BG = _COLORS["secondary_button_bg"]

_EDITION_OPTIONS = [
    "",
    "Standard",
    "Theatrical Cut",
    "Director's Cut",
    "Extended Edition",
    "Unrated",
    "Custom…",
]

_METADATA_PROVIDERS = ["TMDB", "OpenDB"]
_METADATA_PROVIDER_LABELS = {
    value.lower(): value for value in _METADATA_PROVIDERS
}

_EXTRAS_LABELS  = ["Ask per disc", "Always keep", "Always skip"]
_EXTRAS_VALUES  = ["ask",          "keep",        "skip"]

_MULTI_EP_LABELS = ["Auto-detect",  "Split titles", "Merge to one"]
_MULTI_EP_VALUES = ["auto",         "split",        "merge"]

_SPECIALS_LABELS = ["Ask per disc", "Put in Season 00", "Skip specials"]
_SPECIALS_VALUES = ["ask",          "season0",          "skip"]


# ---------------------------------------------------------------------------
# Shared widget helpers
# ---------------------------------------------------------------------------

def _entry(parent: tk.Misc, var: tk.StringVar, width: int = 32) -> tk.Entry:
    return tk.Entry(
        parent, textvariable=var,
        bg=_BG, fg=_FG,
        font=("Segoe UI", 11),
        insertbackground="white",
        relief="flat", bd=4, width=width,
    )


def _combo(
    parent: tk.Misc,
    var: tk.StringVar,
    values: list[str],
    width: int = 20,
) -> ttk.Combobox:
    return ttk.Combobox(
        parent, textvariable=var,
        values=values, state="readonly", width=width,
    )


def _check(parent: tk.Misc, text: str, var: tk.BooleanVar) -> tk.Checkbutton:
    return tk.Checkbutton(
        parent, text=text, variable=var,
        bg=_BG2, fg=_FG,
        selectcolor=_BG3,
        activebackground=_BG2,
        activeforeground=_FG,
        font=("Segoe UI", 10),
        anchor="w",
    )


def _section_header(parent: tk.Misc, text: str) -> None:
    """Thin divider + dim section title."""
    tk.Frame(parent, bg=_BG3, height=1).pack(fill="x", padx=16, pady=(14, 0))
    tk.Label(
        parent, text=f"  {text}",
        bg=_BG2, fg=_FG_DIM,
        font=("Segoe UI", 9, "bold"),
        anchor="w",
    ).pack(fill="x", padx=0)


def _row(parent: tk.Misc) -> tk.Frame:
    f = tk.Frame(parent, bg=_BG2)
    f.pack(fill="x", padx=20, pady=3)
    return f


def _label_in_row(row: tk.Frame, text: str, width: int = 18) -> tk.Label:
    lbl = tk.Label(
        row, text=text,
        bg=_BG2, fg=_FG_DIM,
        font=("Segoe UI", 10),
        width=width, anchor="w",
    )
    lbl.pack(side="left")
    return lbl


def _required_star(row: tk.Frame) -> None:
    tk.Label(
        row, text="*",
        bg=_BG2, fg=_COLORS["danger_fg"],
        font=("Segoe UI", 10, "bold"),
    ).pack(side="left", padx=(0, 4))


def _metadata_provider_label(value: str) -> str:
    return _METADATA_PROVIDER_LABELS.get(str(value or "").strip().lower(), "TMDB")


def _prepare_identity_dialog_window(win: tk.Toplevel, parent: tk.Misc) -> None:
    """Keep setup dialogs focused without grabbing the whole app."""
    win.configure(bg=_BG2)
    win.resizable(False, False)
    try:
        win.transient(parent)
    except Exception:
        pass
    try:
        win.lift(parent)
    except Exception:
        pass
    win.focus_force()


# ---------------------------------------------------------------------------
# Movie setup dialog
# ---------------------------------------------------------------------------

def build_movie_setup_dialog(
    parent: tk.Misc,
    default_title: str = "",
    default_year: str = "",
    default_metadata_provider: str = "TMDB",
    default_metadata_id: str = "",
) -> MovieSessionSetup | None:
    """Build and show the movie rip setup dialog. Returns result or None."""

    result: list[MovieSessionSetup | None] = [None]

    win = tk.Toplevel(parent)
    win.title("Movie \u2014 Library Identity")
    _prepare_identity_dialog_window(win, parent)

    # ── Header ──────────────────────────────────────────────────────────────
    tk.Label(
        win, text="Step 2: Library Identity",
        bg=_BG2, fg=_ACCENT,
        font=("Segoe UI", 14, "bold"),
    ).pack(pady=(18, 4), padx=20, anchor="w")
    tk.Label(
        win, text="What does this become in Jellyfin?  (* required)",
        bg=_BG2, fg=_FG_DIM,
        font=("Segoe UI", 10),
    ).pack(padx=20, anchor="w")

    # ── Main info ────────────────────────────────────────────────────────────
    _section_header(win, "MOVIE IDENTITY")

    title_var = tk.StringVar(value=default_title)
    year_var  = tk.StringVar(value=default_year)

    r = _row(win)
    _label_in_row(r, "Movie title")
    _required_star(r)
    title_entry = _entry(r, title_var, width=34)
    title_entry.pack(side="left")

    r = _row(win)
    _label_in_row(r, "Release year")
    _required_star(r)
    _entry(r, year_var, width=8).pack(side="left")

    # Edition dropdown + optional custom entry
    edition_var        = tk.StringVar(value="")
    edition_custom_var = tk.StringVar()

    r = _row(win)
    _label_in_row(r, "Edition / version")
    edition_combo = _combo(r, edition_var, _EDITION_OPTIONS, width=22)
    edition_combo.pack(side="left")

    custom_frame = tk.Frame(win, bg=_BG2)
    custom_entry = _entry(custom_frame, edition_custom_var, width=26)

    def _on_edition_change(*_):
        if edition_var.get() == "Custom…":
            custom_frame.pack(fill="x", padx=20, pady=(0, 2))
            custom_entry.pack(side="left", padx=(120, 0))
            custom_entry.focus_set()
        else:
            custom_frame.pack_forget()

    edition_combo.bind("<<ComboboxSelected>>", _on_edition_change)  # type: ignore[arg-type]

    # ── Metadata ────────────────────────────────────────────────────────────
    _section_header(win, "METADATA")

    meta_provider = _metadata_provider_label(default_metadata_provider)
    meta_source_var = tk.StringVar(value=meta_provider)
    meta_id_var     = tk.StringVar(value=default_metadata_id)

    r = _row(win)
    _label_in_row(r, "Provider")
    meta_source_combo = _combo(r, meta_source_var, _METADATA_PROVIDERS, width=8)
    meta_source_combo.pack(side="left")
    tk.Label(r, text="  Metadata ID:", bg=_BG2, fg=_FG_DIM,
             font=("Segoe UI", 10)).pack(side="left")
    meta_id_entry = _entry(r, meta_id_var, width=20)
    meta_id_entry.pack(side="left", padx=(4, 0))

    tk.Label(
        win,
        text="   Enter an ID to fetch directly, or leave blank to fall back to title/year lookup.",
        bg=_BG2, fg=_FG_DIM,
        font=("Segoe UI", 9, "italic"),
        anchor="w",
    ).pack(fill="x", padx=20)

    # ── Options ──────────────────────────────────────────────────────────────
    _section_header(win, "OPTIONS")

    replace_var = tk.BooleanVar(value=False)
    keep_raw_var = tk.BooleanVar(value=False)

    r = _row(win)
    _check(r, "Replace existing file if destination already has content",
           replace_var).pack(side="left")

    r = _row(win)
    _check(r, "Keep raw rip after transcode",
           keep_raw_var).pack(side="left")

    # ── Buttons ──────────────────────────────────────────────────────────────
    tk.Frame(win, bg=_BG3, height=1).pack(fill="x", padx=0, pady=(16, 0))
    btn_row = tk.Frame(win, bg=_BG2)
    btn_row.pack(pady=14, padx=20)

    error_var = tk.StringVar()
    error_lbl = tk.Label(
        win, textvariable=error_var,
        bg=_BG2, fg=_COLORS["danger_fg"],
        font=("Segoe UI", 9),
    )
    error_lbl.pack(padx=20, anchor="w")

    def _submit():
        title = title_var.get().strip()
        year  = year_var.get().strip()
        if not title:
            error_var.set("Movie title is required.")
            title_entry.focus_set()
            return
        if not year:
            error_var.set("Release year is required.")
            return

        raw_edition = edition_var.get()
        if raw_edition == "Custom…":
            edition = edition_custom_var.get().strip()
        else:
            edition = raw_edition

        result[0] = MovieSessionSetup(
            title=title,
            year=year,
            edition=edition,
            metadata_provider=meta_source_var.get(),
            metadata_id=meta_id_var.get().strip(),
            replace_existing=replace_var.get(),
            keep_raw=keep_raw_var.get(),
            extras_mode="ask",
        )
        win.destroy()

    def _cancel():
        result[0] = None
        win.destroy()

    tk.Button(
        btn_row, text="Cancel",
        command=_cancel,
        bg=_CANCEL_BG, fg=_COLORS["secondary_button_fg"],
        font=("Segoe UI", 10),
        width=10, relief="flat",
    ).pack(side="left", padx=(0, 8))
    tk.Button(
        btn_row, text="Next  \u2192",
        command=_submit,
        bg=_GREEN, fg=_COLORS["primary_button_fg"],
        font=("Segoe UI", 11, "bold"),
        width=14, relief="flat",
    ).pack(side="left")

    win.bind("<Return>", lambda _e: _submit())  # type: ignore[arg-type]
    win.bind("<Escape>", lambda _e: _cancel())  # type: ignore[arg-type]
    win.protocol("WM_DELETE_WINDOW", _cancel)

    # Center over parent
    win.update_idletasks()
    px = parent.winfo_x() + (parent.winfo_width()  - win.winfo_width())  // 2
    py = parent.winfo_y() + (parent.winfo_height() - win.winfo_height()) // 2
    win.geometry(f"+{px}+{py}")

    title_entry.focus_set()
    win.wait_window()
    return result[0]


# ---------------------------------------------------------------------------
# TV setup dialog
# ---------------------------------------------------------------------------

def build_tv_setup_dialog(
    parent: tk.Misc,
    default_title: str = "",
    default_season: str = "1",
    default_metadata_provider: str = "TMDB",
    default_metadata_id: str = "",
) -> TVSessionSetup | None:
    """Build and show the TV rip setup dialog. Returns result or None."""

    result: list[TVSessionSetup | None] = [None]

    win = tk.Toplevel(parent)
    win.title("TV Show \u2014 Library Identity")
    _prepare_identity_dialog_window(win, parent)

    # ── Header ──────────────────────────────────────────────────────────────
    tk.Label(
        win, text="Step 2: Library Identity",
        bg=_BG2, fg=_ACCENT,
        font=("Segoe UI", 14, "bold"),
    ).pack(pady=(18, 4), padx=20, anchor="w")
    tk.Label(
        win, text="What does this become in Jellyfin?  (* required)",
        bg=_BG2, fg=_FG_DIM,
        font=("Segoe UI", 10),
    ).pack(padx=20, anchor="w")

    # ── Show info ────────────────────────────────────────────────────────────
    _section_header(win, "SHOW IDENTITY")

    title_var  = tk.StringVar(value=default_title)
    year_var   = tk.StringVar()
    season_var = tk.StringVar(value=default_season)
    disc_var   = tk.StringVar(value="1")

    r = _row(win)
    _label_in_row(r, "Show title")
    _required_star(r)
    title_entry = _entry(r, title_var, width=34)
    title_entry.pack(side="left")

    r = _row(win)
    _label_in_row(r, "Release year")
    _entry(r, year_var, width=8).pack(side="left")
    tk.Label(r, text="  (optional, for disambiguation)",
             bg=_BG2, fg=_FG_DIM, font=("Segoe UI", 9)).pack(side="left")

    r = _row(win)
    _label_in_row(r, "Season number")
    _required_star(r)
    season_entry = _entry(r, season_var, width=6)
    season_entry.pack(side="left")

    r = _row(win)
    _label_in_row(r, "Starting disc #")
    _entry(r, disc_var, width=6).pack(side="left")
    tk.Label(r, text="  (auto-increments for each disc)",
             bg=_BG2, fg=_FG_DIM, font=("Segoe UI", 9)).pack(side="left")

    # ── Episode mapping ───────────────────────────────────────────────────────
    _section_header(win, "EPISODE MAPPING")

    ep_map_var = tk.StringVar(value="auto")

    r = _row(win)
    for label, value in [("Auto-detect", "auto"), ("Manual map", "manual")]:
        tk.Radiobutton(
            r, text=label, variable=ep_map_var, value=value,
            bg=_BG2, fg=_FG,
            selectcolor=_BG3,
            activebackground=_BG2,
            activeforeground=_FG,
            font=("Segoe UI", 10),
        ).pack(side="left", padx=(0, 16))

    tk.Label(
        win,
        text="   Auto-detect assigns episode numbers in title order.\n"
             "   Manual map lets you enter numbers for each title.",
        bg=_BG2, fg=_FG_DIM,
        font=("Segoe UI", 9, "italic"),
        justify="left",
    ).pack(fill="x", padx=20)

    # ── Metadata ────────────────────────────────────────────────────────────
    _section_header(win, "METADATA")

    meta_provider = _metadata_provider_label(default_metadata_provider)
    meta_source_var = tk.StringVar(value=meta_provider)
    meta_id_var     = tk.StringVar(value=default_metadata_id)

    r = _row(win)
    _label_in_row(r, "Provider")
    _combo(r, meta_source_var, _METADATA_PROVIDERS, width=8).pack(side="left")
    tk.Label(r, text="  Metadata ID:", bg=_BG2, fg=_FG_DIM,
             font=("Segoe UI", 10)).pack(side="left")
    _entry(r, meta_id_var, width=20).pack(side="left", padx=(4, 0))

    tk.Label(
        win,
        text="   Enter an ID to fetch directly, or leave blank to fall back to title/year lookup.",
        bg=_BG2, fg=_FG_DIM,
        font=("Segoe UI", 9, "italic"),
        anchor="w",
    ).pack(fill="x", padx=20)

    # ── Options ──────────────────────────────────────────────────────────────
    _section_header(win, "OPTIONS")

    multi_ep_var  = tk.StringVar(value="Auto-detect")
    specials_var  = tk.StringVar(value="Ask per disc")
    replace_var   = tk.BooleanVar(value=False)
    keep_raw_var  = tk.BooleanVar(value=False)

    r = _row(win)
    _label_in_row(r, "Multi-ep titles")
    _combo(r, multi_ep_var, _MULTI_EP_LABELS, width=16).pack(side="left")

    r = _row(win)
    _label_in_row(r, "Specials / OVAs")
    _combo(r, specials_var, _SPECIALS_LABELS, width=18).pack(side="left")

    r = _row(win)
    _check(r, "Replace existing files if destination already has content",
           replace_var).pack(side="left")

    r = _row(win)
    _check(r, "Keep raw rip after transcode",
           keep_raw_var).pack(side="left")

    def _label_to_value(label: str, labels: list[str], values: list[str]) -> str:
        try:
            return values[labels.index(label)]
        except ValueError:
            return values[0]

    # ── Buttons ──────────────────────────────────────────────────────────────
    tk.Frame(win, bg=_BG3, height=1).pack(fill="x", padx=0, pady=(16, 0))
    btn_row = tk.Frame(win, bg=_BG2)
    btn_row.pack(pady=14, padx=20)

    error_var = tk.StringVar()
    error_lbl = tk.Label(
        win, textvariable=error_var,
        bg=_BG2, fg=_COLORS["danger_fg"],
        font=("Segoe UI", 9),
    )
    error_lbl.pack(padx=20, anchor="w")

    def _submit():
        title = title_var.get().strip()
        if not title:
            error_var.set("Show title is required.")
            title_entry.focus_set()
            return

        season_raw = season_var.get().strip()
        if not season_raw.isdigit():
            error_var.set("Season number must be a whole number.")
            season_entry.focus_set()
            return
        season = int(season_raw)

        disc_raw = disc_var.get().strip()
        starting_disc = int(disc_raw) if disc_raw.isdigit() else 1

        result[0] = TVSessionSetup(
            title=title,
            year=year_var.get().strip(),
            season=season,
            starting_disc=starting_disc,
            episode_mapping=ep_map_var.get(),
            metadata_provider=meta_source_var.get(),
            metadata_id=meta_id_var.get().strip(),
            multi_episode=_label_to_value(
                multi_ep_var.get(), _MULTI_EP_LABELS, _MULTI_EP_VALUES
            ),
            specials=_label_to_value(
                specials_var.get(), _SPECIALS_LABELS, _SPECIALS_VALUES
            ),
            replace_existing=replace_var.get(),
            keep_raw=keep_raw_var.get(),
        )
        win.destroy()

    def _cancel():
        result[0] = None
        win.destroy()

    tk.Button(
        btn_row, text="Cancel",
        command=_cancel,
        bg=_CANCEL_BG, fg=_COLORS["secondary_button_fg"],
        font=("Segoe UI", 10),
        width=10, relief="flat",
    ).pack(side="left", padx=(0, 8))
    tk.Button(
        btn_row, text="Next  \u2192",
        command=_submit,
        bg=_GREEN, fg=_COLORS["primary_button_fg"],
        font=("Segoe UI", 11, "bold"),
        width=14, relief="flat",
    ).pack(side="left")

    win.bind("<Return>", lambda _e: _submit())  # type: ignore[arg-type]
    win.bind("<Escape>", lambda _e: _cancel())  # type: ignore[arg-type]
    win.protocol("WM_DELETE_WINDOW", _cancel)

    # Center over parent
    win.update_idletasks()
    px = parent.winfo_x() + (parent.winfo_width()  - win.winfo_width())  // 2
    py = parent.winfo_y() + (parent.winfo_height() - win.winfo_height()) // 2
    win.geometry(f"+{px}+{py}")

    title_entry.focus_set()
    win.wait_window()
    return result[0]
