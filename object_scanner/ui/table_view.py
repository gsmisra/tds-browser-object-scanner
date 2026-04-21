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
    ("attr_name",       "Name",        90,  True),
    ("css_selector",    "CSS Selector",200, True),
    ("xpath",           "XPath",       200, True),
    ("selector_quality","Quality",      70, False),
    ("element_count",   "Element Count", 90, False),
    ("is_shadow_element","Shadow DOM",  90, False),
    ("frame_index",     "Frame",        50, False),
    ("screenshot",      "Screenshot",   80, False),
]

# Quality text colors (for Quality column)
_QUALITY_FG = {
    SelectorQuality.HIGH:    "#28a745",  # Green
    SelectorQuality.MEDIUM:  "#ff8c00",  # Amber/Orange
    SelectorQuality.LOW:     "#6c757d",  # Dark Grey
    SelectorQuality.UNKNOWN: "#999999",  # Light Grey
}

_IFRAME_ROW_BG = "#fff9e6"  # Light yellow for light theme
_IFRAME_ROW_FG = "#24292e"  # Dark text for light background

_SHADOW_ROW_BG = "#000000"  # Black background for shadow DOM
_SHADOW_ROW_FG = "#ffffff"  # White text for shadow DOM

# Map column id → callable(ScannedElement) → str for search matching
_COL_VALUE: dict[str, Callable] = {
    "element_index":    lambda el: str(el.element_index),
    "page_title":       lambda el: el.page_title or "",
    "attr_name":        lambda el: el.element_name or el.attr_name or "",
    "css_selector":     lambda el: el.css_selector or "",
    "xpath":            lambda el: el.xpath or "",
    "selector_quality": lambda el: el.selector_quality or "",
    "element_count":    lambda el: str(getattr(el, 'xpath_element_count', 0) if getattr(el, 'xpath_element_count', 0) > 0 else getattr(el, 'css_element_count', 0)),
    "is_shadow_element":lambda el: "Yes" if el.is_shadow_element else "No",
    "frame_index":      lambda el: str(el.frame_index),
    "screenshot":       lambda el: "🖼️" if el.screenshot_path else "",
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
        on_delete: Optional[Callable] = None,
    ) -> None:
        super().__init__(parent)
        self._on_select = on_select
        self._on_copy_css = on_copy_css
        self._on_copy_xpath = on_copy_xpath
        self._on_show_detail = on_show_detail
        self._on_highlight = on_highlight
        self._on_delete = on_delete

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
    
    def get_selected_elements(self) -> list[ScannedElement]:
        """Get all currently selected elements (for multi-selection)."""
        selected_iids = self._tree.selection()
        elements = []
        for iid in selected_iids:
            if iid in self._element_map:
                elements.append(self._element_map[iid])
        return elements
    
    def delete_selected_rows(self) -> list[ScannedElement]:
        """Delete selected rows from the table and return the deleted elements."""
        selected_iids = self._tree.selection()
        deleted_elements = []
        
        for iid in selected_iids:
            if iid in self._element_map:
                deleted_elements.append(self._element_map[iid])
                self._tree.delete(iid)
                del self._element_map[iid]
                if iid in self._insertion_order:
                    self._insertion_order.remove(iid)
        
        self._selected_element = None
        self._update_match_label()
        return deleted_elements

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

    def _cmd_delete_selected(self) -> None:
        """Handle delete command from menu or keyboard."""
        if self._on_delete:
            self._on_delete()
    
    def _update_match_label(self) -> None:
        """Update the match/count label after changes to the table."""
        visible_items = self._tree.get_children()
        total = len(visible_items)
        term = self._search_var.get().strip().lower()
        
        if term:
            # If search is active, count matches
            matched = total  # All visible items are already filtered matches
            self._match_label_var.set(f"{matched} / {len(self._element_map)} results")
        else:
            # No search active, just show total count
            self._match_label_var.set(f"{total} elements" if total else "")
    
    def update_element(self, element: ScannedElement) -> None:
        """Update a single element row in the table after editing."""
        iid = element.element_id
        if iid not in self._element_map:
            return
        
        # Update the element in the map
        self._element_map[iid] = element
        
        # Update tree row values
        screenshot_icon = "🖼️" if element.screenshot_path else ""
        css_count = getattr(element, 'css_element_count', 0)
        xpath_count = getattr(element, 'xpath_element_count', 0)
        
        # Apply same element count display logic
        if xpath_count == 1:
            element_count_display = "1 ✓"
        elif css_count == 1 and (xpath_count == 0 or xpath_count > 1):
            element_count_display = "1 ✓"
        elif xpath_count > 1 and css_count > 1:
            min_count = min(xpath_count, css_count) if xpath_count > 0 and css_count > 0 else max(xpath_count, css_count)
            element_count_display = f"{min_count}"
        elif xpath_count > 1:
            element_count_display = str(xpath_count)
        elif css_count > 1:
            element_count_display = str(css_count)
        else:
            element_count_display = "-"
        
        values = (
            element.element_index,
            element.page_title[:60] if element.page_title else "",
            element.element_name or element.attr_name,
            element.css_selector,
            element.xpath,
            element.selector_quality,
            element_count_display,
            "Yes" if element.is_shadow_element else "No",
            element.frame_index,
            screenshot_icon,
        )
        
        try:
            self._tree.item(iid, values=values)
            # Reapply colors
            self._apply_row_colours()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=0)   # search bar – fixed height
        self.rowconfigure(1, weight=1)   # treeview  – expands

        self._build_search_bar()

        col_ids = [c[0] for c in _COLUMNS]
        
        # Create custom style for grid lines
        style = ttk.Style()
        style.configure("Grid.Treeview", 
                       bordercolor=theme.TREEVIEW_GRID,
                       lightcolor=theme.TREEVIEW_GRID,
                       darkcolor=theme.TREEVIEW_GRID)
        
        self._tree = ttk.Treeview(
            self,
            columns=col_ids,
            show="headings",
            selectmode="extended",  # Enable multi-selection
            height=20,
            style="Grid.Treeview",
        )

        for col_id, header, width, stretch in _COLUMNS:
            self._tree.heading(
                col_id,
                text=header,
                command=lambda c=col_id: self._sort_by(c),
            )
            # Add anchor and border configuration for grid appearance
            self._tree.column(col_id, width=width, stretch=stretch, minwidth=30, anchor='w')

        # Scrollbars
        vsb = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self._tree.yview)
        hsb = ttk.Scrollbar(self, orient=tk.HORIZONTAL, command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._tree.grid(row=1, column=0, sticky="nsew")
        vsb.grid(row=1, column=1, sticky="ns")
        hsb.grid(row=2, column=0, sticky="ew")

        self._tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self._tree.bind("<Double-1>", lambda _e: self._on_show_detail and self._on_show_detail())
        self._tree.bind("<Button-1>", self._on_tree_click)
        self._tree.bind("<Delete>", lambda _e: self._cmd_delete_selected())  # Delete key

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
        self._ctx_menu.add_separator()
        self._ctx_menu.add_command(label="Delete Selected",
                                   command=lambda: self._cmd_delete_selected())
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
        screenshot_icon = "🖼️" if el.screenshot_path else ""
        css_count = getattr(el, 'css_element_count', 0)
        xpath_count = getattr(el, 'xpath_element_count', 0)
        
        # Display element count logic:
        # - If both selectors exist, show the better (lower/unique) count
        # - XPath is text-based so usually more specific
        # - Prefer showing XPath count if it's unique (1)
        
        if xpath_count == 1:
            # XPath is unique - this is the gold standard
            element_count_display = "1 ✓"
        elif css_count == 1 and (xpath_count == 0 or xpath_count > 1):
            # CSS is unique but XPath isn't available or not unique
            element_count_display = "1 ✓"
        elif xpath_count > 1 and css_count > 1:
            # Both non-unique - show the minimum (more specific)
            min_count = min(xpath_count, css_count) if xpath_count > 0 and css_count > 0 else max(xpath_count, css_count)
            element_count_display = f"{min_count}"
        elif xpath_count > 1:
            # Only XPath available and non-unique
            element_count_display = str(xpath_count)
        elif css_count > 1:
            # Only CSS available and non-unique
            element_count_display = str(css_count)
        else:
            # Not validated or no selectors
            element_count_display = "-"
            
        values = (
            el.element_index,
            el.page_title[:60] if el.page_title else "",
            el.element_name or el.attr_name,
            el.css_selector,
            el.xpath,
            el.selector_quality,
            element_count_display,
            "Yes" if el.is_shadow_element else "No",
            el.frame_index,
            screenshot_icon,
        )
        self._tree.insert("", tk.END, iid=el.element_id, values=values)

    def _apply_row_colours(self) -> None:
        # Configure quality text color tags (foreground only)
        for quality, fg in _QUALITY_FG.items():
            self._tree.tag_configure(f"quality_{quality}", foreground=fg)
        
        # Configure Frame/Shadow DOM special backgrounds
        self._tree.tag_configure(
            "iframe_row", background=_IFRAME_ROW_BG, foreground=_IFRAME_ROW_FG
        )
        self._tree.tag_configure(
            "shadow_row", background=_SHADOW_ROW_BG, foreground=_SHADOW_ROW_FG
        )
        
        # Red text for iframe elements (removed shadow_text - using shadow_row instead)
        self._tree.tag_configure("iframe_text", foreground="#dc3545")  # Red text

        # Normal row alternating colors (no quality background)
        self._tree.tag_configure("even_row", background=theme.BG_WIDGET)
        self._tree.tag_configure("odd_row",  background=theme.BG_ROW_ALT)

        for i, iid in enumerate(self._tree.get_children()):
            el = self._element_map.get(iid)
            if el:
                parity = "even_row" if i % 2 == 0 else "odd_row"
                tags = [parity]
                
                # Add quality text color tag (only if not in Shadow DOM - shadow_row overrides all)
                if not el.is_shadow_element:
                    quality_tag = f"quality_{el.selector_quality}"
                    tags.append(quality_tag)
                
                # Add special styling for iframe and shadow DOM elements
                # These override the normal row background
                if el.is_shadow_element:
                    tags.remove(parity)  # Remove normal parity
                    tags.append("shadow_row")
                elif el.frame_index > 0:
                    tags.remove(parity)  # Remove normal parity
                    tags.append("iframe_row")
                    tags.append("iframe_text")
                
                self._tree.item(iid, tags=tuple(tags))

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

    def _on_tree_click(self, event: tk.Event) -> None:
        """Handle clicks on screenshot column to show zoomed image."""
        region = self._tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        
        col = self._tree.identify_column(event.x)
        item = self._tree.identify_row(event.y)
        
        if not item:
            return
        
        # Check if clicked on screenshot column (last column, index #10)
        col_index = int(col.replace('#', '')) - 1
        if col_index == 9:  # Screenshot column (0-indexed)
            el = self._element_map.get(item)
            if el and el.screenshot_path:
                self._show_screenshot(el)

    def _show_context_menu(self, event: tk.Event) -> None:
        row = self._tree.identify_row(event.y)
        if row:
            self._tree.selection_set(row)
            self._ctx_menu.post(event.x_root, event.y_root)

    def _show_screenshot(self, el: ScannedElement) -> None:
        """Show screenshot in borderless preview overlay."""
        from pathlib import Path
        from PIL import Image, ImageTk
        
        screenshot_path = Path(el.screenshot_path)
        if not screenshot_path.exists():
            return
        
        # Create borderless toplevel window
        viewer = tk.Toplevel(self)
        viewer.overrideredirect(True)  # Remove window decorations
        viewer.attributes("-topmost", True)  # Keep on top
        
        # Get main window geometry
        root = self.winfo_toplevel()
        root_x = root.winfo_x()
        root_y = root.winfo_y()
        root_width = root.winfo_width()
        root_height = root.winfo_height()
        
        # Set viewer to cover the main window
        viewer.geometry(f"{root_width}x{root_height}+{root_x}+{root_y}")
        
        # Create semi-transparent overlay
        overlay = tk.Frame(viewer, bg="black")
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        
        # Make overlay semi-transparent (70% opacity)
        try:
            viewer.attributes("-alpha", 0.95)  # Overall window transparency
        except tk.TclError:
            pass  # Alpha not supported on some systems
        
        # Load and display image
        try:
            img = Image.open(screenshot_path)
            
            # Calculate size to fit window with margins while maintaining aspect ratio
            max_width = int(root_width * 0.9)
            max_height = int(root_height * 0.9)
            
            # Get image dimensions
            img_width, img_height = img.size
            
            # Calculate scaling factor
            width_ratio = max_width / img_width
            height_ratio = max_height / img_height
            scale_factor = min(width_ratio, height_ratio, 1.0)  # Don't upscale
            
            # Resize if needed
            if scale_factor < 1.0:
                new_width = int(img_width * scale_factor)
                new_height = int(img_height * scale_factor)
                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            
            photo = ImageTk.PhotoImage(img)
            
            # Create container for image with white background
            img_container = tk.Frame(overlay, bg="white", relief=tk.FLAT, bd=0)
            img_container.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
            
            # Display image
            img_label = tk.Label(img_container, image=photo, bg="white", bd=0)
            img_label.image = photo  # Keep reference
            img_label.pack(padx=0, pady=0)
            
            # Add small info label below image
            info_text = f"{el.element_name or el.attr_name}"
            info_label = tk.Label(
                img_container,
                text=info_text,
                bg="white",
                fg="#333333",
                font=("Segoe UI", 9),
                pady=8
            )
            info_label.pack(side=tk.BOTTOM, fill=tk.X)
            
            # Bind escape key to close
            viewer.bind("<Escape>", lambda e: viewer.destroy())
            
            # Bind click on overlay (outside image) to close
            overlay.bind("<Button-1>", lambda e: viewer.destroy())
            
            # Also bind to the viewer itself
            viewer.bind("<Button-1>", lambda e: viewer.destroy())
            
            # Prevent clicks on image from closing
            img_label.bind("<Button-1>", lambda e: "break")
            img_container.bind("<Button-1>", lambda e: "break")
            info_label.bind("<Button-1>", lambda e: "break")
            
        except Exception as e:
            # Show error in overlay
            error_label = tk.Label(
                overlay,
                text=f"Error loading screenshot:\n{e}",
                fg="white",
                bg="black",
                font=("Segoe UI", 10)
            )
            error_label.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
            viewer.bind("<Escape>", lambda e: viewer.destroy())
            overlay.bind("<Button-1>", lambda e: viewer.destroy())
