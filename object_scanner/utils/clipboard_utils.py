"""
utils/clipboard_utils.py  —  OS-level clipboard helpers via Tkinter.
"""
from __future__ import annotations

import tkinter as tk


def copy_to_clipboard(text: str, root: tk.Misc | None = None) -> None:
    """Copy *text* to the system clipboard.

    If *root* is provided (a live Tk/Toplevel widget), that widget's
    clipboard is used directly.  Otherwise a temporary hidden root window
    is created just for the operation (works without a visible GUI).
    """
    if root is not None:
        _write(root, text)
        return

    tmp = tk.Tk()
    tmp.withdraw()
    try:
        _write(tmp, text)
    finally:
        tmp.destroy()


def _write(widget: tk.Misc, text: str) -> None:
    widget.clipboard_clear()
    widget.clipboard_append(text)
    widget.update()
