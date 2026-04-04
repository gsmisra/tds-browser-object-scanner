"""
ui/table_view.py  —  sortable Treeview of scanned DOM elements.

Colour coding:
  HIGH   → green  (#d4edda)
  MEDIUM → amber  (#fff3cd)
  LOW    → red    (#f8d7da)
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, List, Optional

from models.element_model import Confidence, ScannedElement


# Visible columns and their display labels
_COLUMNS = [
    ("confidence",    "Conf.",   60),
    ("tag",           "Tag",     60),
    ("element_type",  "Type",    70),
    ("element_id",    "ID",      120),
    ("name",          "Name",    120),
    ("label",         "Label",   120),
    ("visible_text",  "Text",    160),
    ("css_selector",  "CSS",     200),
    ("xpath",         "XPath",   200),
    ("page_url",      "Page URL",180),
]

_CONF_COLORS = {
    Confidence.HIGH:   "#d4edda",
    Confidence.MEDIUM: "#fff3cd",
    Confidence.LOW:    "#f8d7da",
}


class TableView(ttk.Frame):
    """Sortable Treeview widget that displays a flat list of ScannedElements."""

    def __init__(self, parent: tk.Misc,
                 on_select: Optional[Callable[[ScannedElement], None]] = None,
                 **kwargs) -> None:
        super().__init__(parent, **kwargs)
        self._on_select = on_select
        self._elements: List[ScannedElement] = []
        self._sort_col: str = ""
        self._sort_reverse: bool = False
        self._build_ui()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        col_ids = [c[0] for c in _COLUMNS]
        self._tree = ttk.Treeview(self, columns=col_ids, show="headings",
                                  selectmode="browse")

        for col_id, col_label, col_width in _COLUMNS:
            self._tree.heading(col_id, text=col_label,
                               command=lambda c=col_id: self._sort_by(c))
            self._tree.column(col_id, width=col_width, minwidth=40, stretch=True)

        # Configure confidence-level row tags
        for conf, color in _CONF_COLORS.items():
            self._tree.tag_configure(conf, background=color)

        vsb = ttk.Scrollbar(self, orient="vertical", command=self._tree.yview)
        hsb = ttk.Scrollbar(self, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self._tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self._tree.bind("<Double-1>", self._on_double_click)

        # Right-click context menu
        self._ctx_menu = tk.Menu(self, tearoff=False)
        self._ctx_menu.add_command(label="Copy CSS Selector",
                                   command=self._copy_css)
        self._ctx_menu.add_command(label="Copy XPath",
                                   command=self._copy_xpath)
        self._ctx_menu.add_separator()
        self._ctx_menu.add_command(label="View Details",
                                   command=self._view_details)
        self._tree.bind("<Button-3>", self._on_right_click)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, elements: List[ScannedElement]) -> None:
        """Replace the current table contents with *elements*."""
        self._elements = list(elements)
        self._redraw()

    def append(self, elements: List[ScannedElement]) -> None:
        """Append *elements* to the existing table rows."""
        self._elements.extend(elements)
        self._redraw()

    def clear(self) -> None:
        """Remove all rows from the table."""
        self._elements.clear()
        self._redraw()

    def selected_element(self) -> Optional[ScannedElement]:
        """Return the currently selected :class:`ScannedElement`, or ``None``."""
        sel = self._tree.selection()
        if not sel:
            return None
        idx = self._tree.index(sel[0])
        try:
            return self._current_order[idx]
        except (IndexError, AttributeError):
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _redraw(self) -> None:
        """Clear and re-populate the Treeview from ``self._elements``."""
        self._tree.delete(*self._tree.get_children())
        self._current_order = list(self._elements)

        if self._sort_col:
            self._current_order.sort(
                key=lambda e: (getattr(e, self._sort_col, "") or "").lower(),
                reverse=self._sort_reverse,
            )

        for el in self._current_order:
            values = tuple(
                _truncate(getattr(el, col_id, "") or "", 80)
                for col_id, *_ in _COLUMNS
            )
            tag = el.confidence if el.confidence in _CONF_COLORS else Confidence.LOW
            self._tree.insert("", tk.END, values=values, tags=(tag,))

    def _sort_by(self, col: str) -> None:
        if self._sort_col == col:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_col = col
            self._sort_reverse = False
        self._redraw()

    def _on_tree_select(self, event) -> None:
        el = self.selected_element()
        if el and self._on_select:
            self._on_select(el)

    def _on_double_click(self, event) -> None:
        self._view_details()

    def _on_right_click(self, event) -> None:
        row = self._tree.identify_row(event.y)
        if row:
            self._tree.selection_set(row)
            self._ctx_menu.post(event.x_root, event.y_root)

    def _copy_css(self) -> None:
        el = self.selected_element()
        if el:
            from utils.clipboard_utils import copy_to_clipboard
            copy_to_clipboard(el.css_selector, self)

    def _copy_xpath(self) -> None:
        el = self.selected_element()
        if el:
            from utils.clipboard_utils import copy_to_clipboard
            copy_to_clipboard(el.xpath, self)

    def _view_details(self) -> None:
        el = self.selected_element()
        if el:
            from ui.details_dialog import DetailsDialog
            DetailsDialog(self.winfo_toplevel(), el)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"
