"""
ui/main_window.py  —  Root Tk window, toolbar, and status bar.
"""
from __future__ import annotations

import logging
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

import config
from services.browser_service import BrowserService
from services.dom_scanner_service import scan_page
from services.export_service import export
from services.session_service import SessionService
from ui.table_view import TableView
from utils.clipboard_utils import copy_to_clipboard

log = logging.getLogger(__name__)


class MainWindow(tk.Tk):
    """Application root window."""

    def __init__(self) -> None:
        super().__init__()
        self.title(config.APP_TITLE)
        self.geometry("1200x700")
        self.minsize(800, 500)

        self._browser = BrowserService()
        self._session = SessionService()

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # ---- toolbar ----
        toolbar = ttk.Frame(self, padding=(4, 4))
        toolbar.pack(fill=tk.X, side=tk.TOP)

        # Browser type selector
        ttk.Label(toolbar, text="Browser:").pack(side=tk.LEFT, padx=(0, 4))
        self._browser_var = tk.StringVar(value=config.BROWSER_TYPE)
        self._browser_combo = ttk.Combobox(
            toolbar, textvariable=self._browser_var,
            values=["chromium", "firefox", "webkit"],
            state="readonly", width=10,
        )
        self._browser_combo.pack(side=tk.LEFT, padx=(0, 8))

        self._btn_launch = ttk.Button(toolbar, text="Launch Browser",
                                      command=self._on_launch)
        self._btn_launch.pack(side=tk.LEFT, padx=2)

        self._btn_close_browser = ttk.Button(toolbar, text="Close Browser",
                                             command=self._on_close_browser,
                                             state=tk.DISABLED)
        self._btn_close_browser.pack(side=tk.LEFT, padx=2)

        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT,
                                                         fill=tk.Y, padx=6)

        self._btn_scan = ttk.Button(toolbar, text="Scan Current Page",
                                    command=self._on_scan, state=tk.DISABLED)
        self._btn_scan.pack(side=tk.LEFT, padx=2)

        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT,
                                                         fill=tk.Y, padx=6)

        self._btn_export = ttk.Button(toolbar, text="Export Results",
                                      command=self._on_export, state=tk.DISABLED)
        self._btn_export.pack(side=tk.LEFT, padx=2)

        self._btn_clear = ttk.Button(toolbar, text="Clear Session",
                                     command=self._on_clear, state=tk.DISABLED)
        self._btn_clear.pack(side=tk.LEFT, padx=2)

        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT,
                                                         fill=tk.Y, padx=6)

        # Copy buttons
        self._btn_copy_css = ttk.Button(toolbar, text="Copy CSS",
                                        command=self._on_copy_css,
                                        state=tk.DISABLED)
        self._btn_copy_css.pack(side=tk.LEFT, padx=2)

        self._btn_copy_xpath = ttk.Button(toolbar, text="Copy XPath",
                                          command=self._on_copy_xpath,
                                          state=tk.DISABLED)
        self._btn_copy_xpath.pack(side=tk.LEFT, padx=2)

        # ---- main content: table ----
        self._table = TableView(self, on_select=self._on_element_selected)
        self._table.pack(fill=tk.BOTH, expand=True, padx=4, pady=(4, 0))

        # ---- status bar ----
        status_frame = ttk.Frame(self, relief=tk.SUNKEN, padding=(4, 2))
        status_frame.pack(fill=tk.X, side=tk.BOTTOM)

        self._status_var = tk.StringVar(value="Ready.")
        ttk.Label(status_frame, textvariable=self._status_var, anchor="w"
                  ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._count_var = tk.StringVar(value="Elements: 0  |  Pages: 0")
        ttk.Label(status_frame, textvariable=self._count_var, anchor="e"
                  ).pack(side=tk.RIGHT)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_launch(self) -> None:
        if self._browser.is_open:
            messagebox.showinfo("Browser", "Browser is already running.")
            return
        browser_type = self._browser_var.get()
        self._set_status(f"Launching {browser_type}…")
        try:
            self._browser.launch(browser_type=browser_type,
                                 start_url=config.START_URL)
        except Exception as exc:
            log.exception("Failed to launch browser")
            messagebox.showerror("Launch Error", str(exc))
            self._set_status("Browser launch failed.")
            return

        self._btn_launch.config(state=tk.DISABLED)
        self._browser_combo.config(state=tk.DISABLED)
        self._btn_close_browser.config(state=tk.NORMAL)
        self._btn_scan.config(state=tk.NORMAL)
        self._set_status(f"{browser_type.capitalize()} browser launched. "
                         "Navigate to your target page, then click 'Scan Current Page'.")

    def _on_close_browser(self) -> None:
        self._browser.close()
        self._btn_launch.config(state=tk.NORMAL)
        self._browser_combo.config(state="readonly")
        self._btn_close_browser.config(state=tk.DISABLED)
        self._btn_scan.config(state=tk.DISABLED)
        self._set_status("Browser closed.")

    def _on_scan(self) -> None:
        if not self._browser.is_open:
            messagebox.showwarning("Scan", "Browser is not open.")
            return
        self._set_status("Scanning page DOM…")
        self._btn_scan.config(state=tk.DISABLED)

        def _do_scan():
            try:
                self._browser.wait_for_load(config.SCAN_TIMEOUT_MS)
                result = scan_page(
                    self._browser.page,
                    skip_hidden=config.SKIP_HIDDEN_ELEMENTS,
                    include_iframes=config.INCLUDE_IFRAMES,
                )
                self._session.add_page(result)
                self.after(0, lambda: self._on_scan_complete(result))
            except Exception as exc:
                log.exception("Scan failed")
                self.after(0, lambda: self._on_scan_error(exc))

        threading.Thread(target=_do_scan, daemon=True).start()

    def _on_scan_complete(self, result) -> None:
        self._table.append(result.elements)
        self._btn_scan.config(state=tk.NORMAL)
        self._btn_export.config(state=tk.NORMAL)
        self._btn_clear.config(state=tk.NORMAL)
        self._update_counts()
        self._set_status(
            f"Scan complete: {len(result.elements)} elements found on '{result.title or result.url}'."
        )

    def _on_scan_error(self, exc: Exception) -> None:
        self._btn_scan.config(state=tk.NORMAL)
        messagebox.showerror("Scan Error", str(exc))
        self._set_status("Scan failed — see logs for details.")

    def _on_export(self) -> None:
        if not self._session.pages:
            messagebox.showinfo("Export", "No scan results to export yet.")
            return
        try:
            json_path, csv_path = export(self._session.pages, config.EXPORT_DIR)
            messagebox.showinfo(
                "Export Complete",
                f"Results exported:\n\n  JSON: {json_path}\n  CSV:  {csv_path}",
            )
            self._set_status(f"Exported → {json_path}")
        except Exception as exc:
            log.exception("Export failed")
            messagebox.showerror("Export Error", str(exc))

    def _on_clear(self) -> None:
        if not messagebox.askyesno("Clear Session",
                                   "Remove all scan results from this session?"):
            return
        self._session.clear()
        self._table.clear()
        self._btn_export.config(state=tk.DISABLED)
        self._btn_clear.config(state=tk.DISABLED)
        self._btn_copy_css.config(state=tk.DISABLED)
        self._btn_copy_xpath.config(state=tk.DISABLED)
        self._update_counts()
        self._set_status("Session cleared.")

    def _on_element_selected(self, element) -> None:
        self._selected_element = element
        self._btn_copy_css.config(state=tk.NORMAL)
        self._btn_copy_xpath.config(state=tk.NORMAL)

    def _on_copy_css(self) -> None:
        el = getattr(self, "_selected_element", None) or self._table.selected_element()
        if el:
            copy_to_clipboard(el.css_selector, self)
            self._set_status(f"Copied CSS: {el.css_selector}")

    def _on_copy_xpath(self) -> None:
        el = getattr(self, "_selected_element", None) or self._table.selected_element()
        if el:
            copy_to_clipboard(el.xpath, self)
            self._set_status(f"Copied XPath: {el.xpath}")

    def _on_close(self) -> None:
        if self._browser.is_open:
            if messagebox.askyesno("Quit", "Close the browser and exit?"):
                self._browser.close()
                self.destroy()
        else:
            self.destroy()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_status(self, message: str) -> None:
        self._status_var.set(message)
        self.update_idletasks()

    def _update_counts(self) -> None:
        self._count_var.set(
            f"Elements: {self._session.element_count}  |  "
            f"Pages: {self._session.page_count}"
        )
