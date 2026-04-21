"""
DetailsDialog — modal popup window showing full metadata for a ScannedElement.

Provides:
- Read-only display of most element fields
- Editable fields: Element Name, CSS Selector, XPath
- Save button to persist changes
- One-click Copy CSS and Copy XPath buttons
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from models.element_model import ScannedElement
from ui import theme
from utils.clipboard_utils import copy_to_clipboard

# Each tuple: (label, attribute_name, multiline, editable)
_FIELD_DEFS: list[tuple[str, str, bool, bool]] = [
    ("Page Title",      "page_title",       False, False),
    ("Page URL",        "page_url",         False, False),
    ("Frame Index",     "frame_index",      False, False),
    ("Tag",             "tag",              False, False),
    ("Type",            "element_type",     False, False),
    ("Visible Text",    "visible_text",     True,  False),
    ("ID",              "attr_id",          False, False),
    ("Name",            "element_name",     False, True),   # EDITABLE
    ("DOM Name",        "attr_name",        False, False),
    ("Class",           "attr_class",       True,  False),
    ("Placeholder",     "attr_placeholder", False, False),
    ("ARIA Label",      "aria_label",       False, False),
    ("Role",            "role",             False, False),
    ("HREF",            "href",             False, False),
    ("data-testid",     "data_testid",      False, False),
    ("Label Text",      "label_text",       False, False),
    ("Nearby Heading",  "nearby_heading",   False, False),
    ("Shadow DOM",      "is_shadow_element",False, False),
    ("Shadow Host Tag", "shadow_host_tag",  False, False),
    ("Shadow Host ID",  "shadow_host_id",   False, False),
    ("Shadow Host Class","shadow_host_class",True, False),
    ("Visible",         "is_visible",       False, False),
    ("Enabled",         "is_enabled",       False, False),
    ("Password Field",  "is_password_field",False, False),
    ("CSS Selector",    "css_selector",     False, True),   # EDITABLE - Changed to single-line
    ("XPath",           "xpath",            False, True),   # EDITABLE - Changed to single-line
    ("Quality",         "selector_quality", False, False),
    ("Selector Notes",  "selector_notes",   False, False),
    ("Element Index",   "element_index",    False, False),
]


class DetailsDialog(tk.Toplevel):
    """Modal detail view for a single ScannedElement."""

    def __init__(
        self, 
        parent: tk.Widget, 
        element: ScannedElement,
        on_save: Optional[Callable[[ScannedElement], None]] = None
    ) -> None:
        super().__init__(parent)
        self._element = element
        self._parent = parent
        self._on_save = on_save
        
        # Store editable field widgets
        self._editable_widgets: dict[str, tk.Widget] = {}

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

        # Add helpful instruction note at the top
        note_frame = ttk.Frame(inner)
        note_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        note_label = tk.Label(
            note_frame,
            text="Fields marked with * are editable. Changes will be reflected in exports.",
            font=("Segoe UI", 9, "italic"),
            fg="#888",
            bg=theme.BG,
            anchor="w"
        )
        note_label.pack(fill="x", padx=5)

        # Start fields from row 1 instead of 0
        for row_idx, (label, attr, multiline, editable) in enumerate(_FIELD_DEFS, start=1):
            raw_value = getattr(self._element, attr, "")
            value = str(raw_value) if raw_value is not None else ""

            # Label with visual indicator for editable fields
            label_text = label + ("*:" if editable else ":")
            ttk.Label(inner, text=label_text, anchor="e", width=16).grid(
                row=row_idx, column=0, sticky="ne", padx=(0, 8), pady=2
            )

            if multiline:
                txt = tk.Text(inner, height=3, wrap=tk.WORD, font=("Consolas", 9))
                theme.style_text_widget(txt)
                txt.insert("1.0", value)
                
                if editable:
                    # Keep editable - no state change
                    self._editable_widgets[attr] = txt
                else:
                    txt.configure(state=tk.DISABLED)
                    
                txt.grid(row=row_idx, column=1, sticky="ew", pady=2)
            else:
                if editable:
                    # Editable fields use regular Entry with white/light background
                    var = tk.StringVar(value=value)
                    entry = ttk.Entry(inner, textvariable=var, font=("Consolas", 9))
                    entry.grid(row=row_idx, column=1, sticky="ew", pady=2)
                    self._editable_widgets[attr] = var
                else:
                    var = tk.StringVar(value=value)
                    entry = ttk.Entry(inner, textvariable=var, state="readonly")
                    entry.grid(row=row_idx, column=1, sticky="ew", pady=2)

        # Buttons
        btn_frame = ttk.Frame(self, padding=(10, 6))
        btn_frame.grid(row=1, column=0, columnspan=2, sticky="ew")

        ttk.Button(
            btn_frame, text="Save Changes",
            command=self._save_changes, width=16
        ).pack(side=tk.LEFT, padx=4)

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

    def _save_changes(self) -> None:
        """Save edited values back to the element and notify parent."""
        from tkinter import messagebox
        
        changes_made = False
        
        for attr, widget in self._editable_widgets.items():
            # Get current value from widget
            if isinstance(widget, tk.StringVar):
                new_value = widget.get().strip()
            elif isinstance(widget, tk.Text):
                new_value = widget.get("1.0", tk.END).strip()
            else:
                continue
            
            # Get original value
            old_value = str(getattr(self._element, attr, "") or "").strip()
            
            # Check if value changed
            if new_value != old_value:
                setattr(self._element, attr, new_value)
                changes_made = True
        
        if changes_made:
            # Notify parent via callback
            if self._on_save:
                self._on_save(self._element)
            
            messagebox.showinfo(
                "Changes Saved",
                "Element details have been updated.\nChanges will be reflected in exports.",
                parent=self
            )
            # Close the dialog after successful save
            self.destroy()
        else:
            messagebox.showinfo(
                "No Changes",
                "No changes were detected.",
                parent=self
            )

    def _copy_css(self) -> None:
        copy_to_clipboard(self, self._element.css_selector)

    def _copy_xpath(self) -> None:
        copy_to_clipboard(self, self._element.xpath)
