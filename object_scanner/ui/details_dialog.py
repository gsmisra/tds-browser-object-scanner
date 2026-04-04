"""
ui/details_dialog.py  —  Modal popup showing full element detail.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Optional

from models.element_model import ScannedElement
from utils.clipboard_utils import copy_to_clipboard


class DetailsDialog(tk.Toplevel):
    """Modal window that shows every field of a :class:`ScannedElement`."""

    def __init__(self, parent: tk.Misc, element: ScannedElement) -> None:
        super().__init__(parent)
        self.title("Element Details")
        self.resizable(True, True)
        self.grab_set()                # modal
        self.transient(parent)

        self._element = element
        self._build_ui()
        self.geometry("700x520")
        self.minsize(500, 400)

        # Centre over parent
        self.update_idletasks()
        px = parent.winfo_rootx() + parent.winfo_width() // 2 - self.winfo_width() // 2
        py = parent.winfo_rooty() + parent.winfo_height() // 2 - self.winfo_height() // 2
        self.geometry(f"+{px}+{py}")

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        el = self._element

        # ----- scrollable detail area -----
        frame = ttk.Frame(self, padding=8)
        frame.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(frame, borderwidth=0, highlightthickness=0)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)

        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        inner = ttk.Frame(canvas)
        canvas_window = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_frame_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(event):
            canvas.itemconfig(canvas_window, width=event.width)

        inner.bind("<Configure>", _on_frame_configure)
        canvas.bind("<Configure>", _on_canvas_configure)

        # Mousewheel scroll
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # ----- fields -----
        fields = [
            ("Tag",            el.tag),
            ("Type",           el.element_type),
            ("ID",             el.element_id),
            ("Name",           el.name),
            ("Label",          el.label),
            ("Placeholder",    el.placeholder),
            ("Visible Text",   el.visible_text),
            ("Aria Label",     el.aria_label),
            ("data-testid",    el.data_testid),
            ("Confidence",     el.confidence),
            ("CSS Selector",   el.css_selector),
            ("XPath",          el.xpath),
            ("Page URL",       el.page_url),
            ("Page Title",     el.page_title),
            ("IFrame Src",     el.iframe_src),
        ]

        for row_idx, (label_text, value) in enumerate(fields):
            lbl = ttk.Label(inner, text=label_text + ":", width=16, anchor="e",
                            font=("TkDefaultFont", 9, "bold"))
            lbl.grid(row=row_idx, column=0, sticky="ne", padx=(4, 6), pady=3)

            val_var = tk.StringVar(value=value or "—")
            entry = ttk.Entry(inner, textvariable=val_var, state="readonly", width=70)
            entry.grid(row=row_idx, column=1, sticky="ew", padx=4, pady=3)

        inner.columnconfigure(1, weight=1)

        # ----- raw attributes -----
        attr_label = ttk.Label(inner, text="Attributes:", anchor="e", width=16,
                               font=("TkDefaultFont", 9, "bold"))
        attr_row = len(fields)
        attr_label.grid(row=attr_row, column=0, sticky="ne", padx=(4, 6), pady=3)

        attr_text = tk.Text(inner, height=6, wrap=tk.WORD, state=tk.DISABLED,
                            font=("Courier", 9))
        attr_text.grid(row=attr_row, column=1, sticky="ew", padx=4, pady=3)
        attr_text.configure(state=tk.NORMAL)
        import json as _json
        attr_text.insert("1.0", _json.dumps(el.attributes, indent=2, ensure_ascii=False))
        attr_text.configure(state=tk.DISABLED)

        # ----- action buttons -----
        btn_frame = ttk.Frame(self, padding=(8, 4))
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM)

        ttk.Button(btn_frame, text="Copy CSS",
                   command=lambda: copy_to_clipboard(el.css_selector, self)
                   ).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="Copy XPath",
                   command=lambda: copy_to_clipboard(el.xpath, self)
                   ).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="Close",
                   command=self.destroy
                   ).pack(side=tk.RIGHT, padx=4)
