"""
Dark theme for TDS QE Browser Object Scanner.

Call ``apply(root)`` once at startup before any windows are shown.
All colour constants are exposed so other modules can use them directly
for tk (non-ttk) widgets that must be styled manually.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# ---------------------------------------------------------------------------
# Palette - Clean, Modern Light Theme
# ---------------------------------------------------------------------------

BG          = "#ffffff"   # root and plain Frame backgrounds - clean white
BG_PANEL    = "#f8f9fa"   # LabelFrame / status panel backgrounds - light grey
BG_WIDGET   = "#ffffff"   # Entry, Combobox, Treeview field area - white
BTN_BG      = "#00A550"   # TD Bank green (button normal)
BTN_ACTIVE  = "#008C43"   # TD Bank green – darker (button hover / pressed)
BTN_FG      = "#ffffff"   # Button label colour (bright white)
SEL_BG      = "#0078d4"   # Selection / focus highlight - modern blue
BORDER      = "#e1e4e8"   # Border / separator colour - light grey
FG          = "#24292e"   # Primary text - dark grey
FG_DIM      = "#6a737d"   # Secondary / disabled text
FG_HEADING  = "#586069"   # Treeview column headings
BG_ROW_ALT  = "#f6f8fa"   # Alternating odd-row background in table - very light grey
TREEVIEW_GRID = "#d1d5da" # Grid / separator colour for Treeview - light grey grid

# Quality row background colours (light theme)
QUALITY_HIGH_BG    = "#d4edda"   # light green
QUALITY_MED_BG     = "#fff3cd"   # light amber
QUALITY_LOW_BG     = "#f8d7da"   # light red
QUALITY_UNKNOWN_BG = "#e9ecef"   # neutral light grey


# ---------------------------------------------------------------------------
# Theme application
# ---------------------------------------------------------------------------

def apply(root: tk.Tk) -> ttk.Style:
    """
    Apply the dark theme to *root* and return the configured Style instance.
    Must be called from the main thread before mainloop starts.
    """
    root.configure(bg=BG)

    style = ttk.Style(root)
    style.theme_use("clam")   # clam is fully re-styleable on all platforms

    # ── Global defaults ──────────────────────────────────────────────────────
    style.configure(
        ".",
        background=BG,
        foreground=FG,
        fieldbackground=BG_WIDGET,
        bordercolor=BORDER,
        darkcolor=BG_PANEL,
        lightcolor=BG_PANEL,
        troughcolor=BG_PANEL,
        selectbackground=SEL_BG,
        selectforeground=FG,
        insertcolor=FG,
        relief="flat",
        font=("Segoe UI", 9),
    )

    # ── TFrame ───────────────────────────────────────────────────────────────
    style.configure("TFrame", background=BG)

    # ── TLabel ───────────────────────────────────────────────────────────────
    style.configure("TLabel", background=BG, foreground=FG)

    # ── TLabelframe ──────────────────────────────────────────────────────────
    style.configure(
        "TLabelframe",
        background=BG_PANEL,
        bordercolor=BORDER,
        relief="groove",
    )
    style.configure(
        "TLabelframe.Label",
        background=BG_PANEL,
        foreground=FG,
        font=("Segoe UI", 9, "bold"),
    )

    # ── TButton ──────────────────────────────────────────────────────────────
    style.configure(
        "TButton",
        background=BTN_BG,
        foreground=BTN_FG,
        bordercolor=BTN_ACTIVE,
        focuscolor=BTN_ACTIVE,
        padding=(6, 4),
        relief="flat",
        font=("Segoe UI", 9, "bold"),
    )
    style.map(
        "TButton",
        background=[("active", BTN_ACTIVE), ("pressed", "#007038")],
        foreground=[("active", BTN_FG), ("pressed", BTN_FG), ("disabled", FG_DIM)],
        relief=[("pressed", "flat")],
    )

    # ── TCheckbutton ─────────────────────────────────────────────────────────
    style.configure(
        "TCheckbutton",
        background=BG,
        foreground=FG,
        font=("Segoe UI", 9, "bold"),
    )
    style.map(
        "TCheckbutton",
        background=[("active", BG), ("selected", BG)],
        foreground=[("active", BTN_ACTIVE), ("selected", BTN_ACTIVE)],
    )

    # ── TCombobox ────────────────────────────────────────────────────────────
    style.configure(
        "TCombobox",
        fieldbackground=BG_WIDGET,
        background=BTN_BG,
        foreground=FG,
        bordercolor=BORDER,
        arrowcolor=FG,
        selectbackground=SEL_BG,
        selectforeground=FG,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", BG_WIDGET)],
        foreground=[("readonly", FG), ("disabled", FG_DIM)],
        selectbackground=[("readonly", SEL_BG)],
        selectforeground=[("readonly", FG)],
    )
    # Dropdown list colours (tk Listbox under the hood)
    root.option_add("*TCombobox*Listbox.background", BG_WIDGET)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", SEL_BG)
    root.option_add("*TCombobox*Listbox.selectForeground", FG)

    # ── TEntry ───────────────────────────────────────────────────────────────
    style.configure(
        "TEntry",
        fieldbackground=BG_WIDGET,
        foreground=FG,
        insertcolor=FG,
        bordercolor=BORDER,
        selectbackground=SEL_BG,
        selectforeground=FG,
    )

    # ── Treeview ─────────────────────────────────────────────────────────────
    style.configure(
        "Treeview",
        background=BG_WIDGET,
        fieldbackground=BG_WIDGET,
        foreground=FG,
        bordercolor=TREEVIEW_GRID,
        borderwidth=1,
        relief="solid",
        rowheight=25,
    )
    style.configure(
        "Treeview.Heading",
        background=BG_PANEL,
        foreground=FG_HEADING,
        relief="flat",
        bordercolor=BORDER,
        font=("Segoe UI", 9, "bold"),
    )
    style.map(
        "Treeview",
        background=[("selected", SEL_BG)],
        foreground=[("selected", FG)],
    )
    style.map(
        "Treeview.Heading",
        background=[("active", BTN_ACTIVE)],
    )
    
    # Configure grid layout with light grey separators
    style.layout("Treeview", [
        ("Treeview.treearea", {"sticky": "nswe"})
    ])
    
    # Set grid line colors using option_add for cell borders
    root.option_add("*Treeview*borderWidth", 1)
    root.option_add("*Treeview*relief", "solid")
    root.option_add("*Treeview*highlightThickness", 0)

    # ── TScrollbar ───────────────────────────────────────────────────────────
    style.configure(
        "TScrollbar",
        background=BTN_BG,
        troughcolor=BG_PANEL,
        arrowcolor=FG,
        bordercolor=BORDER,
        relief="flat",
    )
    style.map("TScrollbar", background=[("active", BTN_ACTIVE)])

    # ── TPanedwindow / Sash ───────────────────────────────────────────────────
    style.configure("TPanedwindow", background=BG)
    style.configure("Sash", sashthickness=5, background=BORDER)

    # ── TSeparator ───────────────────────────────────────────────────────────
    style.configure("TSeparator", background=BORDER)

    return style


def style_text_widget(widget: tk.Text) -> None:
    """Apply dark colours to a plain tk.Text widget."""
    widget.configure(
        bg=BG_WIDGET,
        fg=FG,
        insertbackground=FG,
        selectbackground=SEL_BG,
        selectforeground=FG,
        relief="flat",
        borderwidth=1,
        highlightbackground=BORDER,
        highlightcolor=SEL_BG,
    )


def style_canvas_widget(widget: tk.Canvas) -> None:
    """Apply dark colours to a plain tk.Canvas widget."""
    widget.configure(
        bg=BG_PANEL,
        highlightbackground=BORDER,
        highlightthickness=1,
    )
