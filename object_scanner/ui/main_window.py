"""
MainWindow — root Tkinter window and application controller.

Owns:
- Top toolbar (launch/scan/export buttons)
- Status bar
- TableView and DetailsDialog integration
- Coordinates BrowserService, DOMScannerService, LocatorService,
  SessionService, and ExportService
- Runs all blocking operations in a background thread to keep the UI responsive
"""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import Optional

import config
from models.element_model import ScannedPage, SelectorQuality
from services.browser_service import BrowserService
from services.dom_scanner_service import DOMScannerService
from services.export_service import ExportService
from services.locator_service import LocatorService
from services.session_service import SessionService
from ui import theme
from ui.details_dialog import DetailsDialog
from ui.table_view import TableView
from utils.clipboard_utils import copy_to_clipboard

logger = logging.getLogger(__name__)

# Quality → dark-safe row background colours
_QUALITY_COLOURS = {
    SelectorQuality.HIGH:    theme.QUALITY_HIGH_BG,
    SelectorQuality.MEDIUM:  theme.QUALITY_MED_BG,
    SelectorQuality.LOW:     theme.QUALITY_LOW_BG,
    SelectorQuality.UNKNOWN: theme.QUALITY_UNKNOWN_BG,
}


class MainWindow:
    """Root application window."""

    def __init__(self, root: tk.Tk) -> None:
        self._root = root
        self._root.title(config.APP_TITLE)
        self._root.geometry(config.WINDOW_GEOMETRY)
        self._root.protocol("WM_DELETE_WINDOW", self._on_exit)

        # Services
        self._browser = BrowserService()
        self._scanner = DOMScannerService()
        self._locator = LocatorService()
        self._session = SessionService()
        self._exporter = ExportService()

        # Single-worker executor: Playwright sync API requires all calls
        # to run on the same thread. max_workers=1 guarantees thread reuse.
        self._pw_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="playwright-worker"
        )
        self._pending_future: Optional[Future] = None

        self._build_ui()
        self._refresh_status()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._root.columnconfigure(0, weight=1)
        self._root.rowconfigure(1, weight=1)

        self._build_toolbar()
        self._build_status_bar()
        self._build_main_area()
        self._build_footer()

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self._root, padding=(6, 4))
        bar.grid(row=0, column=0, sticky="ew")

        btn_defs = [
            ("Launch Browser",     self._cmd_launch,  "primary"),
            ("Scan Current Page",  self._cmd_scan,    "action"),
            ("Rescan",             self._cmd_rescan,  "action"),
            ("Clear Results",      self._cmd_clear,   "secondary"),
            ("Export Results",     self._cmd_export,  "secondary"),
        ]

        for i, (label, cmd, _style) in enumerate(btn_defs):
            ttk.Button(bar, text=label, command=cmd, width=18).grid(
                row=0, column=i, padx=3, pady=2
            )

        # Highlight button (element must be selected first)
        ttk.Button(
            bar, text="Highlight on Page", command=self._cmd_highlight, width=18
        ).grid(row=0, column=len(btn_defs), padx=3, pady=2)

        # Browser type selector
        ttk.Label(bar, text="Browser:").grid(row=0, column=len(btn_defs) + 1, padx=(16, 2))
        self._browser_var = tk.StringVar(value=config.BROWSER_TYPE)
        browser_combo = ttk.Combobox(
            bar,
            textvariable=self._browser_var,
            values=["chromium", "chrome", "firefox", "webkit"],
            width=10,
            state="readonly",
        )
        browser_combo.grid(row=0, column=len(btn_defs) + 2, padx=2)

        # Exit
        ttk.Button(bar, text="Exit", command=self._on_exit, width=8).grid(
            row=0, column=len(btn_defs) + 3, padx=(16, 3)
        )

    def _build_status_bar(self) -> None:
        frame = ttk.LabelFrame(self._root, text="Status", padding=(6, 4))
        frame.grid(row=2, column=0, sticky="ew", padx=6, pady=(0, 4))
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)

        labels_left = [
            ("Browser:", "_lbl_browser_status"),
            ("URL:",     "_lbl_url"),
            ("Title:",   "_lbl_title"),
        ]
        labels_right = [
            ("Last Scan:", "_lbl_scan_time"),
            ("Elements:",  "_lbl_element_count"),
            ("Info:",      "_lbl_info"),
        ]

        for row, (text, attr) in enumerate(labels_left):
            ttk.Label(frame, text=text, width=8, anchor="e").grid(
                row=row, column=0, sticky="e", padx=(0, 4)
            )
            var = tk.StringVar(value="—")
            setattr(self, attr + "_var", var)
            ttk.Label(frame, textvariable=var, anchor="w").grid(
                row=row, column=1, sticky="ew"
            )

        for row, (text, attr) in enumerate(labels_right):
            ttk.Label(frame, text=text, width=10, anchor="e").grid(
                row=row, column=2, sticky="e", padx=(20, 4)
            )
            var = tk.StringVar(value="—")
            setattr(self, attr + "_var", var)
            lbl = ttk.Label(frame, textvariable=var, anchor="w")
            lbl.grid(row=row, column=3, sticky="ew")
            if attr == "_lbl_info":
                # Info label gets a slightly different style for visibility
                setattr(self, "_info_label", lbl)

    def _build_main_area(self) -> None:
        pane = ttk.PanedWindow(self._root, orient=tk.VERTICAL)
        pane.grid(row=1, column=0, sticky="nsew", padx=6, pady=4)

        # Table
        table_frame = ttk.LabelFrame(pane, text="Scanned Elements", padding=4)
        self._table = TableView(
            table_frame,
            on_select=self._on_element_selected,
            on_copy_css=self._cmd_copy_css,
            on_copy_xpath=self._cmd_copy_xpath,
            on_show_detail=self._cmd_show_details,
            on_highlight=self._cmd_highlight,
        )
        self._table.pack(fill=tk.BOTH, expand=True)
        pane.add(table_frame, weight=3)

        # Detail panel (compact, below table)
        detail_frame = ttk.LabelFrame(pane, text="Selected Element Detail", padding=4)
        self._detail_text = tk.Text(
            detail_frame, height=8, wrap=tk.WORD,
            state=tk.DISABLED, font=("Consolas", 9)
        )
        detail_sb = ttk.Scrollbar(detail_frame, command=self._detail_text.yview)
        self._detail_text.configure(yscrollcommand=detail_sb.set)
        detail_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._detail_text.pack(fill=tk.BOTH, expand=True)
        theme.style_text_widget(self._detail_text)
        pane.add(detail_frame, weight=1)

    def _build_footer(self) -> None:
        footer = tk.Frame(self._root, bg=theme.BG_PANEL)
        footer.grid(row=3, column=0, sticky="ew")
        tk.Label(
            footer,
            text="Developed By TDSecurities Quality Engineering Team",
            bg=theme.BG_PANEL,
            fg=theme.FG_DIM,
            font=("Segoe UI", 8),
            anchor="center",
            pady=4,
        ).pack(fill=tk.X)

    # ------------------------------------------------------------------
    # Commands (button handlers) — run in background thread where needed
    # ------------------------------------------------------------------

    def _cmd_launch(self) -> None:
        if self._browser.is_running:
            messagebox.showinfo("Browser Active", "A browser session is already running.")
            # bring_to_front must run on the playwright worker thread
            self._run_in_thread(self._browser.bring_to_front)
            return

        self._set_info("Launching browser…")
        self._run_in_thread(self._do_launch)

    def _do_launch(self) -> None:
        try:
            browser_type = self._browser_var.get()
            self._browser.launch(browser_type=browser_type)
            self._root.after(0, lambda: self._set_info("Browser ready."))
        except Exception as exc:
            logger.exception("Launch failed")
            self._root.after(0, lambda: self._set_info(f"Launch failed: {exc}", error=True))
        finally:
            self._root.after(0, self._refresh_status)

    def _cmd_scan(self) -> None:
        if not self._browser.is_running:
            messagebox.showwarning("No Browser", "Please launch a browser first.")
            return
        if self._pending_future and not self._pending_future.done():
            messagebox.showinfo("Scanning", "A scan is already in progress.")
            return
        self._set_info("Scanning current page…")
        self._run_in_thread(self._do_scan)

    def _cmd_rescan(self) -> None:
        self._cmd_scan()

    def _do_scan(self) -> None:
        try:
            page = self._browser.current_page
            if page is None:
                raise RuntimeError("Browser page not available.")

            scanned: ScannedPage = self._scanner.scan_page(page)
            self._locator.decorate_elements(scanned.elements)
            self._session.add_or_replace(scanned, overwrite=True)

            self._root.after(0, lambda: self._on_scan_complete(scanned))
        except Exception as exc:
            logger.exception("Scan failed")
            self._root.after(0, lambda: self._set_info(f"Scan error: {exc}", error=True))
        finally:
            self._root.after(0, self._refresh_status)

    def _on_scan_complete(self, scanned: ScannedPage) -> None:
        all_pages = self._session.pages
        self._table.load_pages(all_pages)
        count = len(scanned.elements)
        total = sum(len(p.elements) for p in all_pages)
        page_count = len(all_pages)
        suffix = (
            f" | {total} total across {page_count} pages"
            if page_count > 1 else ""
        )
        self._set_info(
            f"Scan complete — {count} elements on '{scanned.page_title}'{suffix}"
        )
        self._lbl_scan_time_var.set(scanned.scan_timestamp)   # type: ignore[attr-defined]
        self._lbl_element_count_var.set(str(total))           # type: ignore[attr-defined]

    def _cmd_clear(self) -> None:
        if messagebox.askyesno("Clear Results", "Clear all scan results from this session?"):
            self._session.clear()
            self._table.clear()
            self._set_info("Session cleared.")
            self._lbl_element_count_var.set("0")              # type: ignore[attr-defined]
            self._lbl_scan_time_var.set("—")                  # type: ignore[attr-defined]
            self._clear_detail_panel()

    def _cmd_export(self) -> None:
        pages = self._session.pages
        if not pages:
            messagebox.showwarning("Nothing to Export", "No scan results in this session.")
            return

        self._set_info("Exporting…")
        try:
            json_path, csv_path = self._exporter.export_both(pages)
            self._set_info(f"Exported: {json_path.name}, {csv_path.name}")
            messagebox.showinfo(
                "Export Complete",
                f"Files written to:\n{json_path}\n{csv_path}",
            )
        except Exception as exc:
            logger.exception("Export failed")
            self._set_info(f"Export failed: {exc}", error=True)

    def _cmd_copy_css(self) -> None:
        el = self._table.selected_element
        if el:
            copy_to_clipboard(self._root, el.css_selector)
            self._set_info(f"Copied CSS: {el.css_selector}")

    def _cmd_copy_xpath(self) -> None:
        el = self._table.selected_element
        if el:
            copy_to_clipboard(self._root, el.xpath)
            self._set_info(f"Copied XPath: {el.xpath}")

    def _cmd_show_details(self) -> None:
        el = self._table.selected_element
        if el:
            DetailsDialog(self._root, el)

    def _cmd_highlight(self) -> None:
        el = self._table.selected_element
        if not el:
            messagebox.showwarning("No Selection", "Select an element in the table first.")
            return
        if not self._browser.is_running:
            messagebox.showwarning("No Browser", "Browser is not running.")
            return
        self._set_info(f"Highlighting: {el.css_selector}")
        self._run_in_thread(lambda: self._do_highlight(el))

    def _do_highlight(self, el) -> None:
        try:
            found = self._browser.highlight_element(
                css_selector=el.css_selector,
                xpath=el.xpath,
            )
            if not found:
                self._root.after(
                    0,
                    lambda: self._set_info(
                        f"Element not found on page: {el.css_selector}", error=True
                    ),
                )
        except Exception as exc:
            logger.warning("Highlight failed: %s", exc)
            self._root.after(
                0, lambda: self._set_info(f"Highlight error: {exc}", error=True)
            )

    # ------------------------------------------------------------------
    # UI event handlers
    # ------------------------------------------------------------------

    def _on_element_selected(self, element) -> None:
        if element is None:
            self._clear_detail_panel()
            return
        self._populate_detail_panel(element)

    def _on_exit(self) -> None:
        if self._browser.is_running:
            if not messagebox.askyesno(
                "Exit",
                "A browser session is active. Close browser and exit?",
            ):
                return
            # Submit close() to the playwright worker thread and wait briefly.
            # Blocking the UI thread here is intentional — we are shutting down.
            try:
                future = self._pw_executor.submit(self._browser.close)
                future.result(timeout=8)
            except Exception:
                pass
        self._pw_executor.shutdown(wait=False)
        self._root.destroy()

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def _refresh_status(self) -> None:
        """Called from the UI thread every 3 s. Delegates Playwright access to the worker thread."""
        if self._browser.is_running:
            # Fetch URL / title on the playwright worker thread, post result back here
            self._pw_executor.submit(self._worker_fetch_status)
        else:
            self._lbl_browser_status_var.set("Not running")      # type: ignore[attr-defined]
            self._lbl_url_var.set("—")                           # type: ignore[attr-defined]
            self._lbl_title_var.set("—")                         # type: ignore[attr-defined]

        self._root.after(3000, self._refresh_status)

    def _worker_fetch_status(self) -> None:
        """Runs on the playwright worker thread — safe to call Playwright here."""
        try:
            url = self._browser.current_url
            title = self._browser.current_title
            running = self._browser.is_running
        except Exception:
            url, title, running = "", "", False
        self._root.after(0, lambda: self._apply_status(running, url, title))

    def _apply_status(self, running: bool, url: str, title: str) -> None:
        """Runs on the UI thread — safe to update Tkinter vars here."""
        if running:
            self._lbl_browser_status_var.set("Running")          # type: ignore[attr-defined]
            self._lbl_url_var.set(url or "—")                    # type: ignore[attr-defined]
            self._lbl_title_var.set(title or "—")                # type: ignore[attr-defined]
        else:
            self._lbl_browser_status_var.set("Not running")      # type: ignore[attr-defined]
            self._lbl_url_var.set("—")                           # type: ignore[attr-defined]
            self._lbl_title_var.set("—")                         # type: ignore[attr-defined]

    def _set_info(self, message: str, error: bool = False) -> None:
        self._lbl_info_var.set(message)  # type: ignore[attr-defined]
        logger.info("UI info: %s", message)

    # ------------------------------------------------------------------
    # Detail panel helpers
    # ------------------------------------------------------------------

    def _populate_detail_panel(self, el) -> None:
        lines = [
            f"Tag:            {el.tag}",
            f"Type:           {el.element_type}",
            f"Visible Text:   {el.visible_text}",
            f"ID:             {el.attr_id}",
            f"Name:           {el.attr_name}",
            f"Class:          {el.attr_class}",
            f"Placeholder:    {el.attr_placeholder}",
            f"ARIA Label:     {el.aria_label}",
            f"Role:           {el.role}",
            f"Label Text:     {el.label_text}",
            f"Heading:        {el.nearby_heading}",
            f"data-testid:    {el.data_testid}",
            f"Visible:        {el.is_visible}  |  Enabled: {el.is_enabled}",
            f"Password Field: {el.is_password_field}",
            "",
            f"CSS Selector:   {el.css_selector}",
            f"XPath:          {el.xpath}",
            f"Quality:        {el.selector_quality}",
            f"Notes:          {el.selector_notes}",
            "",
            f"Page:           {el.page_title}",
            f"URL:            {el.page_url}",
            f"Frame Index:    {el.frame_index}",
        ]
        text = "\n".join(lines)
        self._detail_text.configure(state=tk.NORMAL)
        self._detail_text.delete("1.0", tk.END)
        self._detail_text.insert("1.0", text)
        self._detail_text.configure(state=tk.DISABLED)

    def _clear_detail_panel(self) -> None:
        self._detail_text.configure(state=tk.NORMAL)
        self._detail_text.delete("1.0", tk.END)
        self._detail_text.configure(state=tk.DISABLED)

    # ------------------------------------------------------------------
    # Thread helper
    # ------------------------------------------------------------------

    def _run_in_thread(self, target) -> None:
        """Submit *target* to the single-worker Playwright executor."""
        self._pending_future = self._pw_executor.submit(target)
