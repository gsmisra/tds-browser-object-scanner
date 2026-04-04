"""
DetailsDialog — modal popup window showing full metadata for a ScannedElement.

Provides:
- Read-only display of all element fields
- One-click Copy CSS and Copy XPath buttons
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from models.element_model import ScannedElement
from ui import theme
from utils.clipboard_utils import copy_to_clipboard

# Each tuple: (label, attribute_name, multiline)
_FIELD_DEFS: list[tuple[str, str, bool]] = [
    ("Page Title",      "page_title",       False),
    ("Page URL",        "page_url",         False),
    ("Frame Index",     "frame_index",      False),
    ("Tag",             "tag",              False),
    ("Type",            "element_type",     False),
    ("Visible Text",    "visible_text",     True),
    ("ID",              "attr_id",          False),
    ("Name",            "attr_name",        False),
    ("Class",           "attr_class",       True),
    ("Placeholder",     "attr_placeholder", False),
    ("ARIA Label",      "aria_label",       False),
    ("Role",            "role",             False),
    ("HREF",            "href",             False),
    ("data-testid",     "data_testid",      False),
    ("Label Text",      "label_text",       False),
    ("Nearby Heading",  "nearby_heading",   False),
    ("Visible",         "is_visible",       False),
    ("Enabled",         "is_enabled",       False),
    ("Password Field",  "is_password_field",False),
    ("CSS Selector",    "css_selector",     True),
    ("XPath",           "xpath",            True),
    ("Quality",         "selector_quality", False),
    ("Selector Notes",  "selector_notes",   False),
    ("Element Index",   "element_index",    False),
]


class DetailsDialog(tk.Toplevel):
    """Modal detail view for a single ScannedElement."""

    def __init__(self, parent: tk.Widget, element: ScannedElement) -> None:
        super().__init__(parent)
        self._element = element
        self._parent = parent

        self.title(f"Element Detail — {element.tag} [{element.element_index}]")
        self.geometry("760x640")
        self.resizable(True, True)
        self.configure(bg=theme.BG)
        self.grab_set()            # Modal
        self.focus_set()

        self._build()
        self.transient(parent)

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        # Scrollable container
        canvas = tk.Canvas(self, borderwidth=0)
        theme.style_canvas_widget(canvas)
        scrollbar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.grid(row=0, column=1, sticky="ns")
        canvas.grid(row=0, column=0, sticky="nsew")

        inner = ttk.Frame(canvas, padding=10)
        canvas_window = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_inner_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(event):
            canvas.itemconfig(canvas_window, width=event.width)

        inner.bind("<Configure>", _on_inner_configure)
        canvas.bind("<Configure>", _on_canvas_configure)

        # Bind mousewheel
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        self.bind("<Destroy>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        inner.columnconfigure(1, weight=1)

        for row, (label, attr, multiline) in enumerate(_FIELD_DEFS):
            raw_value = getattr(self._element, attr, "")
            value = str(raw_value) if raw_value is not None else ""

            ttk.Label(inner, text=label + ":", anchor="e", width=16).grid(
                row=row, column=0, sticky="ne", padx=(0, 8), pady=2
            )

            if multiline:
                txt = tk.Text(inner, height=3, wrap=tk.WORD, font=("Consolas", 9))
                theme.style_text_widget(txt)
                txt.insert("1.0", value)
                txt.configure(state=tk.DISABLED)
                txt.grid(row=row, column=1, sticky="ew", pady=2)
            else:
                var = tk.StringVar(value=value)
                entry = ttk.Entry(inner, textvariable=var, state="readonly")
                entry.grid(row=row, column=1, sticky="ew", pady=2)

        # Buttons
        btn_frame = ttk.Frame(self, padding=(10, 6))
        btn_frame.grid(row=1, column=0, columnspan=2, sticky="ew")

        ttk.Button(
            btn_frame, text="Copy CSS Selector",
            command=self._copy_css, width=20
        ).pack(side=tk.LEFT, padx=4)

        ttk.Button(
            btn_frame, text="Copy XPath",
            command=self._copy_xpath, width=16
        ).pack(side=tk.LEFT, padx=4)

        ttk.Button(
            btn_frame, text="Close",
            command=self.destroy, width=10
        ).pack(side=tk.RIGHT, padx=4)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _copy_css(self) -> None:
        copy_to_clipboard(self, self._element.css_selector)

    def _copy_xpath(self) -> None:
        copy_to_clipboard(self, self._element.xpath)
