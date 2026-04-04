"""
Clipboard utilities — thin wrappers around Tkinter's clipboard API.
"""

from __future__ import annotations

import tkinter as tk


def copy_to_clipboard(widget: tk.Widget, text: str) -> None:
    """Copy *text* to the system clipboard via the given Tkinter widget."""
    if not text:
        return
    widget.clipboard_clear()
    widget.clipboard_append(text)
    widget.update()   # Required on some platforms for the clipboard to flush
