"""
TableView — Tkinter Treeview wrapper that displays scanned elements.

Features:
- Sortable columns (click header)
- Row colour coding by selector quality
- Context menu for copy/detail actions
- Single-selection with callback on change
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from models.element_model import ScannedElement, ScannedPage, SelectorQuality
from ui import theme

# Columns: (id, display header, width, stretch)
_COLUMNS: list[tuple[str, str, int, bool]] = [
    ("element_index",   "#",           40,  False),
    ("page_title",      "Page Title",  160, True),
    ("tag",             "Tag",         60,  False),
    ("element_type",    "Type",        70,  False),
    ("visible_text",    "Visible Text",160,  True),
    ("attr_id",         "ID",         120,  True),
    ("attr_name",       "Name",        90,  True),
    ("css_selector",    "CSS Selector",200, True),
    ("xpath",           "XPath",       200, True),
    ("selector_quality","Quality",      70, False),
    ("frame_index",     "Frame",        50, False),
]

_QUALITY_BG = {
    SelectorQuality.HIGH:    theme.QUALITY_HIGH_BG,
    SelectorQuality.MEDIUM:  theme.QUALITY_MED_BG,
    SelectorQuality.LOW:     theme.QUALITY_LOW_BG,
    SelectorQuality.UNKNOWN: theme.QUALITY_UNKNOWN_BG,
}

# Map column id → callable(ScannedElement) → str for search matching
_COL_VALUE: dict[str, Callable] = {
    "element_index":    lambda el: str(el.element_index),
    "page_title":       lambda el: el.page_title or "",
    "tag":              lambda el: el.tag or "",
    "element_type":     lambda el: el.element_type or "",
    "visible_text":     lambda el: el.visible_text or "",
    "attr_id":          lambda el: el.attr_id or "",
    "attr_name":        lambda el: el.attr_name or "",
    "css_selector":     lambda el: el.css_selector or "",
    "xpath":            lambda el: el.xpath or "",
    "selector_quality": lambda el: el.selector_quality or "",
    "frame_index":      lambda el: str(el.frame_index),
}

_SEARCH_COL_ALL = "All Columns"
_SEARCH_COL_OPTIONS = [_SEARCH_COL_ALL] + [hdr for _, hdr, _, _ in _COLUMNS]


class TableView(tk.Frame):
    """
    Scrollable, sortable Treeview of ScannedElement rows.

    Parameters
    ----------
    parent : tk widget
    on_select : called with (ScannedElement | None) when selection changes
    on_copy_css : called with no args to copy CSS of selected element
    on_copy_xpath : called with no args to copy XPath of selected element
    on_show_detail : called with no args to open detail dialog
    """

    def __init__(
        self,
        parent: tk.Widget,
        on_select: Optional[Callable] = None,
        on_copy_css: Optional[Callable] = None,
        on_copy_xpath: Optional[Callable] = None,
        on_show_detail: Optional[Callable] = None,
        on_highlight: Optional[Callable] = None,
    ) -> None:
        super().__init__(parent)
        self._on_select = on_select
        self._on_copy_css = on_copy_css
        self._on_copy_xpath = on_copy_xpath
        self._on_show_detail = on_show_detail
        self._on_highlight = on_highlight

        # element_id → ScannedElement
        self._element_map: dict[str, ScannedElement] = {}
        self._insertion_order: list[str] = []   # all element_ids in load order
        self._selected_element: Optional[ScannedElement] = None

        # Sort state: (column_id, ascending)
        self._sort_state: tuple[str, bool] = ("element_index", True)

        # Search state
        self._search_var = tk.StringVar()
        self._search_col_var = tk.StringVar(value=_SEARCH_COL_ALL)
        self._match_label_var = tk.StringVar(value="")
        self._search_var.trace_add("write", lambda *_: self._apply_filter())
        self._search_col_var.trace_add("write", lambda *_: self._apply_filter())

        self._build()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def selected_element(self) -> Optional[ScannedElement]:
        return self._selected_element

    def load_page(self, page: ScannedPage) -> None:
        """Replace current rows with elements from a single ScannedPage."""
        self.load_pages([page])

    def load_pages(self, pages: list[ScannedPage]) -> None:
        """Reload table with elements from ALL provided pages (clears first)."""
        self.clear()
        for page in pages:
            for el in page.elements:
                self._element_map[el.element_id] = el
                self._insertion_order.append(el.element_id)
                self._insert_row(el)
        self._apply_row_colours()
        self._apply_filter()

    def clear(self) -> None:
        """Remove all rows, including any detached (filtered-out) items."""
        # Delete visible items
        self._tree.delete(*self._tree.get_children())
        # Also delete detached items that aren't visible children
        for iid in list(self._insertion_order):
            try:
                self._tree.delete(iid)
            except Exception:
                pass
        self._element_map.clear()
        self._insertion_order.clear()
        self._selected_element = None
        self._match_label_var.set("")

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=0)   # search bar – fixed height
        self.rowconfigure(1, weight=1)   # treeview  – expands

        self._build_search_bar()

        col_ids = [c[0] for c in _COLUMNS]
        self._tree = ttk.Treeview(
            self,
            columns=col_ids,
            show="headings",
            selectmode="browse",
            height=20,
        )

        for col_id, header, width, stretch in _COLUMNS:
            self._tree.heading(
                col_id,
                text=header,
                command=lambda c=col_id: self._sort_by(c),
            )
            self._tree.column(col_id, width=width, stretch=stretch, minwidth=30)

        # Scrollbars
        vsb = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self._tree.yview)
        hsb = ttk.Scrollbar(self, orient=tk.HORIZONTAL, command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._tree.grid(row=1, column=0, sticky="nsew")
        vsb.grid(row=1, column=1, sticky="ns")
        hsb.grid(row=2, column=0, sticky="ew")

        self._tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self._tree.bind("<Double-1>", lambda _e: self._on_show_detail and self._on_show_detail())

        # Context menu
        self._ctx_menu = tk.Menu(self, tearoff=0)
        self._ctx_menu.add_command(label="Copy CSS Selector",
                                   command=lambda: self._on_copy_css and self._on_copy_css())
        self._ctx_menu.add_command(label="Copy XPath",
                                   command=lambda: self._on_copy_xpath and self._on_copy_xpath())
        self._ctx_menu.add_separator()
        self._ctx_menu.add_command(label="Highlight on Page",
                                   command=lambda: self._on_highlight and self._on_highlight())
        self._ctx_menu.add_separator()
        self._ctx_menu.add_command(label="View Details…",
                                   command=lambda: self._on_show_detail and self._on_show_detail())
        self._tree.bind("<Button-3>", self._show_context_menu)

    def _build_search_bar(self) -> None:
        bar = tk.Frame(self, bg=theme.BG_PANEL)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 2))
        bar.columnconfigure(2, weight=1)   # search entry stretches

        tk.Label(
            bar, text="Search:", bg=theme.BG_PANEL, fg=theme.FG,
            font=("Segoe UI", 9),
        ).grid(row=0, column=0, padx=(4, 4), pady=4, sticky="w")

        col_combo = ttk.Combobox(
            bar,
            textvariable=self._search_col_var,
            values=_SEARCH_COL_OPTIONS,
            state="readonly",
            width=16,
        )
        col_combo.grid(row=0, column=1, padx=(0, 6), pady=4, sticky="w")

        search_entry = ttk.Entry(bar, textvariable=self._search_var)
        search_entry.grid(row=0, column=2, padx=(0, 6), pady=4, sticky="ew")
        # Escape clears the search
        search_entry.bind("<Escape>", lambda _e: self._search_var.set(""))

        ttk.Button(
            bar, text="✕ Clear", width=8,
            command=lambda: self._search_var.set(""),
        ).grid(row=0, column=3, padx=(0, 4), pady=4)

        tk.Label(
            bar, textvariable=self._match_label_var,
            bg=theme.BG_PANEL, fg=theme.FG_DIM,
            font=("Segoe UI", 8),
        ).grid(row=0, column=4, padx=(0, 8), pady=4, sticky="e")

    # ------------------------------------------------------------------
    # Row operations
    # ------------------------------------------------------------------

    def _insert_row(self, el: ScannedElement) -> None:
        values = (
            el.element_index,
            el.page_title[:60] if el.page_title else "",
            el.tag,
            el.element_type,
            el.visible_text[:80] if el.visible_text else "",
            el.attr_id,
            el.attr_name,
            el.css_selector,
            el.xpath,
            el.selector_quality,
            el.frame_index,
        )
        self._tree.insert("", tk.END, iid=el.element_id, values=values)

    def _apply_row_colours(self) -> None:
        # Quality colours
        for quality, bg in _QUALITY_BG.items():
            self._tree.tag_configure(quality, background=bg)

        self._tree.tag_configure("even_row", background=theme.BG_WIDGET)
        self._tree.tag_configure("odd_row",  background=theme.BG_ROW_ALT)

        for i, iid in enumerate(self._tree.get_children()):
            el = self._element_map.get(iid)
            if el:
                parity = "even_row" if i % 2 == 0 else "odd_row"
                self._tree.item(iid, tags=(parity, el.selector_quality))

    # ------------------------------------------------------------------
    # Search / filter
    # ------------------------------------------------------------------

    def _apply_filter(self) -> None:
        """Show only rows whose fields contain the search term."""
        term = self._search_var.get().strip().lower()
        selected_header = self._search_col_var.get()

        # Map header back to col_id (or None = all columns)
        col_id: Optional[str] = None
        if selected_header != _SEARCH_COL_ALL:
            for cid, hdr, _, _ in _COLUMNS:
                if hdr == selected_header:
                    col_id = cid
                    break

        # Detach every row (preserves item data)
        for iid in self._insertion_order:
            try:
                self._tree.detach(iid)
            except Exception:
                pass

        matched = 0
        total = len(self._insertion_order)

        for iid in self._insertion_order:
            el = self._element_map.get(iid)
            if el is None:
                continue
            if not term or self._matches(el, term, col_id):
                try:
                    self._tree.move(iid, "", tk.END)
                    matched += 1
                except Exception:
                    pass

        self._apply_row_colours()

        if term:
            self._match_label_var.set(f"{matched} / {total} results")
        else:
            self._match_label_var.set(f"{total} elements" if total else "")

    def _matches(self, el: ScannedElement, term: str, col_id: Optional[str]) -> bool:
        """Return True if *el* contains *term* in the target column(s)."""
        if col_id:
            getter = _COL_VALUE.get(col_id)
            return getter is not None and term in getter(el).lower()
        # All columns
        return any(term in getter(el).lower() for getter in _COL_VALUE.values())

    # ------------------------------------------------------------------
    # Sort
    # ------------------------------------------------------------------

    def _sort_by(self, col_id: str) -> None:
        _, ascending = self._sort_state
        if self._sort_state[0] == col_id:
            ascending = not ascending
        else:
            ascending = True
        self._sort_state = (col_id, ascending)

        rows = [
            (self._tree.set(iid, col_id), iid)
            for iid in self._tree.get_children()
        ]

        def sort_key(item):
            val = item[0]
            try:
                return (0, int(val))
            except (ValueError, TypeError):
                return (1, str(val).lower())

        rows.sort(key=sort_key, reverse=not ascending)
        for order, (_, iid) in enumerate(rows):
            self._tree.move(iid, "", order)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def _on_tree_select(self, _event) -> None:
        selected = self._tree.selection()
        if selected:
            iid = selected[0]
            el = self._element_map.get(iid)
            self._selected_element = el
            if self._on_select:
                self._on_select(el)
        else:
            self._selected_element = None
            if self._on_select:
                self._on_select(None)

    def _show_context_menu(self, event: tk.Event) -> None:
        row = self._tree.identify_row(event.y)
        if row:
            self._tree.selection_set(row)
            self._ctx_menu.post(event.x_root, event.y_root)
