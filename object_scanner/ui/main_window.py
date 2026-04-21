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

import json
import logging
import os
import sys
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk, filedialog
from typing import Optional
from PIL import Image, ImageTk

import config
from models.element_model import ScannedPage, ScannedElement, SelectorQuality
from services.browser_service import BrowserService
from services.dom_scanner_service import DOMScannerService
from services.export_service import ExportService
from services.locator_service import LocatorService
from services.session_service import SessionService
from ui import theme
from ui.details_dialog import DetailsDialog
from ui.export_dialog import ExportDialog
from ui.settings_dialog import SettingsDialog
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

# ---------------------------------------------------------------------------
# JavaScript for manual element picker
# ---------------------------------------------------------------------------
_MANUAL_PICK_JS = """
() => {
    return new Promise((resolve) => {
        // Clean up any leftover picker elements
        document.querySelectorAll('[data-arsim-picker]').forEach(e => e.remove());

        const overlay = document.createElement('div');
        overlay.setAttribute('data-arsim-picker', '1');
        overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;z-index:2147483646;cursor:crosshair;background:transparent;';
        document.body.appendChild(overlay);

        const highlight = document.createElement('div');
        highlight.setAttribute('data-arsim-picker', '1');
        highlight.style.cssText = 'position:fixed;pointer-events:none;z-index:2147483647;border:3px solid red;border-radius:2px;background:rgba(255,0,0,0.08);display:none;transition:top 0.06s,left 0.06s,width 0.06s,height 0.06s;';
        document.body.appendChild(highlight);

        const tooltip = document.createElement('div');
        tooltip.setAttribute('data-arsim-picker', '1');
        tooltip.style.cssText = 'position:fixed;top:8px;left:50%;transform:translateX(-50%);z-index:2147483647;background:rgba(0,0,0,0.85);color:#fff;padding:8px 18px;border-radius:6px;font:13px/1.4 sans-serif;pointer-events:none;white-space:nowrap;';
        tooltip.textContent = 'Click an element to capture it  ·  Press ESC to cancel';
        document.body.appendChild(tooltip);

        let lastEl = null;

        function getNearestLabel(el) {
            const lblId = el.getAttribute('aria-labelledby');
            if (lblId) { const lbl = document.getElementById(lblId); if (lbl) return (lbl.innerText || '').trim(); }
            const id = el.getAttribute('id');
            if (id) { try { const lbl = document.querySelector('label[for=\"'+CSS.escape(id)+'\"]'); if (lbl) return (lbl.innerText || '').trim(); } catch(e){} }
            let p = el.parentElement, d = 0;
            while (p && d < 3) { if (p.tagName === 'LABEL') return (p.innerText || '').trim(); p = p.parentElement; d++; }
            return '';
        }

        function getNearestHeading(el) {
            let node = el.parentElement, depth = 0;
            while (node && depth < 8) {
                const h = node.querySelector('h1,h2,h3,h4,h5,h6');
                if (h) return (h.innerText || '').trim().substring(0, 100);
                if (/^H[1-6]$/.test(node.tagName)) return (node.innerText || '').trim().substring(0, 100);
                node = node.parentElement; depth++;
            }
            return '';
        }

        function getParentInfo(el) {
            const p = el.parentElement;
            if (!p) return {tag:'',id:'',cls:''};
            return { tag: p.tagName.toLowerCase(), id: p.getAttribute('id')||'', cls: (p.getAttribute('class')||'').trim() };
        }

        function getSiblingInfo(sib) {
            if (!sib || sib.nodeType !== 1) return {tag:'',id:'',text:'',name:''};
            return { tag: sib.tagName.toLowerCase(), id: sib.getAttribute('id')||'', text: (sib.innerText||'').trim().substring(0,80), name: sib.getAttribute('name')||'' };
        }

        function getNthOfType(el) {
            const tag = el.tagName;
            let n = 1, sib = el.previousElementSibling;
            while (sib) { if (sib.tagName === tag) n++; sib = sib.previousElementSibling; }
            return n;
        }

        function hasDirectText(el) {
            for (let i = 0; i < el.childNodes.length; i++) {
                const node = el.childNodes[i];
                if (node.nodeType === 3 && node.textContent.trim().length > 0) {
                    return true;
                }
            }
            return false;
        }

        function isInShadowDOM(el) {
            let node = el;
            while (node) {
                if (node.getRootNode && node.getRootNode() instanceof ShadowRoot) {
                    return true;
                }
                node = node.parentNode;
            }
            return false;
        }

        overlay.addEventListener('mousemove', (e) => {
            overlay.style.pointerEvents = 'none';
            let el = document.elementFromPoint(e.clientX, e.clientY);
            overlay.style.pointerEvents = 'auto';
            if (el && el !== lastEl && !el.hasAttribute('data-arsim-picker')) {
                // Smart element preview: show parent if SVG child
                const hoverTag = el.tagName.toLowerCase();
                const isSvgChild = ['svg', 'path', 'circle', 'rect', 'g', 'polygon', 'line', 'ellipse', 'polyline'].includes(hoverTag);
                
                if (isSvgChild) {
                    let parent = el.parentElement;
                    let depth = 0;
                    while (parent && depth < 5) {
                        const hasAriaLabel = parent.hasAttribute('aria-label');
                        const hasRole = parent.hasAttribute('role');
                        const isButton = parent.tagName === 'BUTTON';
                        const isLink = parent.tagName === 'A';
                        const hasClickHandler = parent.hasAttribute('onclick') || 
                                               parent.hasAttribute('jsaction') ||
                                               parent.getAttribute('role') === 'button' ||
                                               parent.getAttribute('tabindex');
                        
                        if (hasAriaLabel || hasRole || isButton || isLink || hasClickHandler) {
                            el = parent;
                            break;
                        }
                        
                        parent = parent.parentElement;
                        depth++;
                    }
                }
                
                lastEl = el;
                const rect = el.getBoundingClientRect();
                highlight.style.display = 'block';
                highlight.style.top = (rect.top - 2) + 'px';
                highlight.style.left = (rect.left - 2) + 'px';
                highlight.style.width = (rect.width + 4) + 'px';
                highlight.style.height = (rect.height + 4) + 'px';
            }
        });

        overlay.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            overlay.style.pointerEvents = 'none';
            let el = document.elementFromPoint(e.clientX, e.clientY);
            overlay.style.pointerEvents = 'auto';
            if (el && !el.hasAttribute('data-arsim-picker')) {
                // Smart element selection: if clicked on SVG child, traverse up to find meaningful parent
                const clickedTag = el.tagName.toLowerCase();
                const isSvgChild = ['svg', 'path', 'circle', 'rect', 'g', 'polygon', 'line', 'ellipse', 'polyline'].includes(clickedTag);
                
                if (isSvgChild) {
                    // Look for a parent with aria-label, role, or clickable attributes
                    let parent = el.parentElement;
                    let depth = 0;
                    while (parent && depth < 5) {
                        const hasAriaLabel = parent.hasAttribute('aria-label');
                        const hasRole = parent.hasAttribute('role');
                        const isButton = parent.tagName === 'BUTTON';
                        const isLink = parent.tagName === 'A';
                        const hasClickHandler = parent.hasAttribute('onclick') || 
                                               parent.hasAttribute('jsaction') ||
                                               parent.getAttribute('role') === 'button' ||
                                               parent.getAttribute('tabindex');
                        
                        // If parent is more meaningful, use it instead
                        if (hasAriaLabel || hasRole || isButton || isLink || hasClickHandler) {
                            el = parent;
                            break;
                        }
                        
                        parent = parent.parentElement;
                        depth++;
                    }
                }
                
                const tag = el.tagName.toLowerCase();
                const elType = el.getAttribute('type') || '';
                const parentInfo = getParentInfo(el);
                const prevSib = getSiblingInfo(el.previousElementSibling);
                const nextSib = getSiblingInfo(el.nextElementSibling);
                const data = {
                    tag: tag,
                    element_type: elType || tag,
                    visible_text: (el.innerText || el.textContent || '').trim().substring(0, 200),
                    attr_id: el.getAttribute('id') || '',
                    attr_name: el.getAttribute('name') || '',
                    attr_class: (el.getAttribute('class') || '').trim(),
                    attr_placeholder: el.getAttribute('placeholder') || '',
                    aria_label: el.getAttribute('aria-label') || '',
                    role: el.getAttribute('role') || '',
                    href: el.getAttribute('href') || '',
                    data_testid: el.getAttribute('data-testid') || el.getAttribute('data-test-id') || el.getAttribute('data-test') || '',
                    label_text: getNearestLabel(el),
                    nearby_heading: getNearestHeading(el),
                    is_visible: true,
                    is_enabled: !el.disabled && el.getAttribute('aria-disabled') !== 'true',
                    is_password_field: tag === 'input' && elType.toLowerCase() === 'password',
                    is_shadow_element: isInShadowDOM(el),
                    parent_tag: parentInfo.tag,
                    parent_id: parentInfo.id,
                    parent_class: parentInfo.cls,
                    nth_of_type: getNthOfType(el),
                    prev_sibling_tag: prevSib.tag,
                    prev_sibling_id: prevSib.id,
                    prev_sibling_text: prevSib.text,
                    next_sibling_tag: nextSib.tag,
                    next_sibling_id: nextSib.id,
                    next_sibling_text: nextSib.text,
                    has_direct_text: hasDirectText(el)
                };
                document.querySelectorAll('[data-arsim-picker]').forEach(e => e.remove());
                resolve(JSON.stringify(data));
            }
        });

        function escHandler(e) {
            if (e.key === 'Escape') {
                document.querySelectorAll('[data-arsim-picker]').forEach(el => el.remove());
                document.removeEventListener('keydown', escHandler, true);
                resolve(null);
            }
        }
        document.addEventListener('keydown', escHandler, true);
    });
}
"""


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

        # Browser selection
        self._browser_var = tk.StringVar(value="chrome")  # Default to Chrome

        # Auto-scan state
        self._auto_scan_enabled = tk.BooleanVar(value=False)
        self._auto_scan_enabled.trace_add("write", lambda *_: self._on_auto_scan_changed())
        self._last_scanned_url: Optional[str] = "about:blank"  # Skip initial about:blank
        self._auto_scan_check_interval = 2000  # 2 seconds
        
        # Screenshot capture state
        self._screenshot_enabled = tk.BooleanVar(value=False)
        
        # Store button references for enabling/disabling
        self._scan_button: Optional[ttk.Button] = None

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

    @staticmethod
    def _resource_path(relative_path: str) -> str:
        """Resolve path for bundled PyInstaller resources or dev-time files."""
        base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, relative_path)

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self._root, padding=(6, 4))
        bar.grid(row=0, column=0, sticky="ew")

        # --- TD Bank logo (top-left) ---
        try:
            logo_path = self._resource_path("TD-Bank-Logo.png")
            img = Image.open(logo_path)
            img.thumbnail((120, 60))
            photo = ImageTk.PhotoImage(img)
            logo_label = ttk.Label(bar, image=photo)
            logo_label.image = photo  # type: ignore[attr-defined]
            logo_label.grid(row=0, column=0, padx=(0, 16))
        except Exception as exc:
            logger.warning("Could not load logo: %s", exc)

        btn_defs = [
            ("Launch Browser",     self._cmd_launch,       "primary"),
            ("Scan Current Page",  self._cmd_scan,         "action"),
            ("Manual Scan",        self._cmd_manual_scan,  "action"),
            ("Clear Results",      self._cmd_clear,        "secondary"),
            ("Download",           self._cmd_download,     "secondary"),
            ("Highlight on Page",  self._cmd_highlight,    "secondary"),
            ("Settings",           self._cmd_settings,     "secondary"),
        ]

        col_offset = 1  # column 0 is the logo
        for i, (label, cmd, _style) in enumerate(btn_defs):
            btn = ttk.Button(bar, text=label, command=cmd, width=16)
            btn.grid(row=0, column=col_offset + i, padx=3, pady=2)
            # Store reference to Scan Current Page button
            if label == "Scan Current Page":
                self._scan_button = btn

        # Exit button (far right)
        ttk.Button(bar, text="Exit", command=self._on_exit, width=8).grid(
            row=0, column=col_offset + len(btn_defs), padx=(16, 3)
        )

    def _build_status_bar(self) -> None:
        frame = ttk.Frame(self._root, padding=(6, 2))
        frame.grid(row=2, column=0, sticky="ew")
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=2)

        labels_left = [
            ("Browser:", "_lbl_browser"),
            ("URL:",     "_lbl_url"),
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
            on_delete=self._cmd_delete_elements,
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
            
            # Set up callback for new tabs/pages
            self._browser.set_new_page_callback(self._on_new_tab_opened)
            
            self._root.after(0, lambda: self._set_info("Browser ready."))
            
            # Start auto-scan monitoring if enabled
            if self._auto_scan_enabled.get():
                self._root.after(0, self._start_auto_scan_monitoring)
        except Exception as exc:
            logger.exception("Launch failed")
            msg = str(exc)
            browser_type = self._browser_var.get()
            self._root.after(
                0, lambda: self._on_launch_failed(browser_type, msg)
            )
        finally:
            self._root.after(0, self._refresh_status)

    def _on_launch_failed(self, browser_type: str, error_msg: str) -> None:
        """Show a user-friendly error and prompt to select a different browser."""
        messagebox.showerror(
            "Browser Launch Failed",
            f"Could not launch '{browser_type}' browser.\n\n"
            f"Error: {error_msg}\n\n"
            f"Please select a different browser type from the dropdown "
            f"and click 'Launch Browser' again.\n\n"
            f"Available options: chromium, chrome, firefox, webkit, edge",
        )
        self._set_info(
            f"Launch failed for '{browser_type}' — select a different browser.",
            error=True,
        )

    def _cmd_scan(self) -> None:
        if not self._browser.is_running:
            messagebox.showwarning("No Browser", "Please launch a browser first.")
            return
        if self._pending_future and not self._pending_future.done():
            messagebox.showinfo("Scanning", "A scan is already in progress.")
            return
        self._set_info("Scanning current page…")
        self._run_in_thread(self._do_scan)

    def _cmd_settings(self) -> None:
        """Open the settings dialog."""
        SettingsDialog(
            self._root,
            self._browser_var,
            self._auto_scan_enabled,
            self._screenshot_enabled
        )

    def _on_auto_scan_changed(self) -> None:
        """Called when auto-scan setting changes - enable/disable Scan button."""
        if self._scan_button:
            if self._auto_scan_enabled.get():
                self._scan_button.config(state="disabled")
                self._set_info("Auto Scanning enabled — listening for URL changes")
                # Start monitoring
                if self._browser.is_running:
                    self._last_scanned_url = None
                    self._start_auto_scan_monitoring()
            else:
                self._scan_button.config(state="normal")
                self._set_info("Auto Scanning disabled — use Scan Current Page or Manual Scan buttons")

    def _do_scan(self) -> None:
        try:
            page = self._browser.current_page
            if page is None:
                raise RuntimeError("Browser page not available.")

            # Get current URL to check if already scanned
            current_url = self._browser.current_url
            
            # Check if page already scanned
            if self._check_page_already_scanned(current_url):
                return  # User was notified, skip scan

            scanned: ScannedPage = self._scanner.scan_page(page)
            self._locator.decorate_elements(scanned.elements, page=page)
            
            # Capture screenshots if enabled
            if self._screenshot_enabled.get():
                self._capture_screenshots(scanned.elements, page)
            
            self._session.add_or_replace(scanned, overwrite=True)

            self._root.after(0, lambda: self._on_scan_complete(scanned))
        except Exception as exc:
            logger.exception("Scan failed")
            error_msg = str(exc)
            self._root.after(0, lambda: self._set_info(f"Scan error: {error_msg}", error=True))
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

    def _cmd_download(self) -> None:
        """Open export dialog to download results."""
        pages = self._session.pages
        if not pages:
            messagebox.showwarning("Nothing to Export", "No scan results in this session.")
            return
        
        # Open export dialog
        dialog = ExportDialog(self._root, self._session, self._exporter)
        
        # Handle result
        if dialog.result:
            export_type, paths = dialog.result
            
            if export_type == "new":
                json_path, csv_path, props_path = paths
                self._set_info(f"Exported: {json_path.name}, {csv_path.name}, {props_path.name}")
            else:  # existing
                self._set_info(f"Updated: {paths.name}")
        else:
            self._set_info("Export cancelled")

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
            DetailsDialog(self._root, el, on_save=self._on_element_edited)

    def _on_element_edited(self, element: ScannedElement) -> None:
        """Callback when element is edited in DetailsDialog - update table view."""
        self._table.update_element(element)
        self._set_info(f"Updated element: {element.element_name or element.attr_name}")
        logger.info(
            "Element edited - Name: %s, CSS: %s, XPath: %s",
            element.element_name,
            element.css_selector,
            element.xpath
        )
    
    def _cmd_delete_elements(self) -> None:
        """Delete selected elements from the table and session."""
        selected_elements = self._table.get_selected_elements()
        if not selected_elements:
            messagebox.showwarning("No Selection", "Select one or more elements to delete.")
            return
        
        count = len(selected_elements)
        confirm = messagebox.askyesno(
            "Confirm Delete",
            f"Delete {count} selected element{'s' if count > 1 else ''}?\n\nThis action cannot be undone.",
            parent=self._root
        )
        
        if not confirm:
            return
        
        # Delete screenshot files from disk
        for el in selected_elements:
            if el.screenshot_path:
                try:
                    screenshot_file = Path(el.screenshot_path)
                    if screenshot_file.exists():
                        screenshot_file.unlink()
                        logger.info("Deleted screenshot: %s", screenshot_file.name)
                except Exception as exc:
                    logger.warning("Failed to delete screenshot %s: %s", el.screenshot_path, exc)
        
        # Delete from table view (returns deleted elements)
        deleted_elements = self._table.delete_selected_rows()
        
        # Clear detail panel since selected element was deleted
        self._clear_detail_panel()
        
        # Delete from session (also removes empty pages)
        element_ids = [el.element_id for el in deleted_elements]
        removed_count, pages_removed = self._session.remove_elements(element_ids)
        
        # Update counts
        all_pages = self._session.pages
        total = sum(len(p.elements) for p in all_pages)
        self._lbl_element_count_var.set(str(total))      # type: ignore[attr-defined]
        
        # Update status message
        msg = f"Deleted {removed_count} element{'s' if removed_count != 1 else ''}"
        if pages_removed > 0:
            msg += f" ({pages_removed} page{'s' if pages_removed != 1 else ''} removed)"
        self._set_info(msg)
        logger.info("Deleted %d elements from session, removed %d empty pages", removed_count, pages_removed)

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
            error_msg = str(exc)
            self._root.after(
                0, lambda: self._set_info(f"Highlight error: {error_msg}", error=True)
            )

    # ------------------------------------------------------------------
    # Auto-scan handlers
    # ------------------------------------------------------------------

    def _on_new_tab_opened(self, page: Any) -> None:
        """
        Called when a new tab/page is opened in the browser.
        This runs on the Playwright worker thread.
        """
        try:
            # Wait for the page to load
            page.wait_for_load_state("domcontentloaded", timeout=10000)
            
            # Notify UI thread
            page_url = page.url
            page_title = page.title()
            self._root.after(0, lambda: self._set_info(f"New tab detected: {page_title}"))
            
            # If auto-scan is enabled, scan the new tab
            if self._auto_scan_enabled.get():
                self._root.after(100, lambda: self._trigger_new_tab_scan(page, page_url))
            
        except Exception as exc:
            logger.exception("Error handling new tab: %s", exc)

    def _trigger_new_tab_scan(self, page: Any, url: str) -> None:
        """Trigger a scan for a newly opened tab."""
        if not self._auto_scan_enabled.get():
            return
        
        # Prevent duplicate scans
        if self._pending_future and not self._pending_future.done():
            return

        # Check if page already scanned
        if self._check_page_already_scanned_async(url):
            logger.info("New tab scan skipped - page already scanned: %s", url)
            self._last_scanned_url = url
            return

        self._last_scanned_url = url
        self._set_info(f"Auto-scanning new tab: {url}")
        self._run_in_thread(lambda: self._do_scan_specific_page(page))

    def _do_scan_specific_page(self, page: Any) -> None:
        """Scan a specific page (for new tab scanning)."""
        try:
            scanned = self._scanner.scan_page(page)
            self._locator.decorate_elements(scanned.elements, page=page)
            
            # Capture screenshots if enabled
            if self._screenshot_enabled.get():
                self._capture_screenshots(scanned.elements, page)
            
            self._session.add_or_replace(scanned, overwrite=True)

            self._root.after(0, lambda: self._on_scan_complete(scanned))
        except Exception as exc:
            logger.exception("New tab scan failed")
            error_msg = str(exc)
            self._root.after(0, lambda: self._set_info(f"New tab scan error: {error_msg}", error=True))
        finally:
            self._root.after(0, self._refresh_status)

    def _on_auto_scan_toggle(self) -> None:
        """Maintained for compatibility - actual toggle now handled by _on_auto_scan_changed."""
        pass
    
    def _capture_screenshots(self, elements: list, page: Any) -> None:
        """Capture screenshots with red boxes for all elements."""
        from pathlib import Path
        import config
        
        screenshots_dir = Path(config.EXPORT_DIR) / "screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        
        for el in elements:
            if el.css_selector or el.xpath:
                # Generate filename based on element name
                safe_name = "".join(c if c.isalnum() or c in ('-', '_') else '_' 
                                   for c in (el.element_name or f"element_{el.element_index}"))
                filename = f"{safe_name}_{el.element_id[:8]}.png"
                output_path = str(screenshots_dir / filename)
                
                # Capture screenshot
                success = self._browser.capture_element_screenshot(
                    el.css_selector, el.xpath, output_path, el.is_shadow_element
                )
                
                if success:
                    el.screenshot_path = output_path
                    logger.info("Screenshot captured for %s", el.element_name)
                else:
                    logger.warning("Failed to capture screenshot for %s", el.element_name)

    def _start_auto_scan_monitoring(self) -> None:
        """Schedule periodic URL checks for auto-scanning."""
        if not self._auto_scan_enabled.get():
            return
        
        logger.info("Starting auto-scan monitoring - interval: %d ms", self._auto_scan_check_interval)
        try:
            self._root.after(self._auto_scan_check_interval, self._check_for_url_change)
        except Exception as exc:
            logger.exception("Failed to schedule URL check: %s", exc)

    def _check_for_url_change(self) -> None:
        """Check if URL has changed and trigger scan if needed."""
        try:
            if not self._auto_scan_enabled.get():
                return
            
            if not self._browser.is_running:
                return

            # Fetch current URL on the playwright worker thread
            self._pw_executor.submit(self._worker_check_url_change)

            # Schedule next check
            self._root.after(self._auto_scan_check_interval, self._check_for_url_change)
        except Exception as exc:
            logger.exception("Error in _check_for_url_change: %s", exc)

    def _worker_check_url_change(self) -> None:
        """Runs on the playwright worker thread to check URL."""
        try:
            current_url = self._browser.current_url
            
            # Skip about:blank unless it's the first scan
            if current_url == "about:blank" and self._last_scanned_url != "about:blank":
                return
            
            if current_url and current_url != self._last_scanned_url:
                # URL changed, trigger auto-scan
                logger.info("URL changed detected - triggering auto-scan: %s", current_url)
                self._root.after(0, lambda: self._trigger_auto_scan(current_url))
        except Exception as exc:
            logger.exception("Auto-scan URL check error: %s", exc)

    def _trigger_auto_scan(self, url: str) -> None:
        """Trigger an automatic scan when URL changes."""
        if not self._auto_scan_enabled.get():
            return
        
        # Prevent duplicate scans
        if self._pending_future and not self._pending_future.done():
            logger.debug("Auto-scan skipped - scan already in progress")
            return

        # Check if page already scanned (will show popup on main thread)
        if self._check_page_already_scanned_async(url):
            logger.info("Auto-scan skipped - page already scanned: %s", url)
            self._last_scanned_url = url  # Update to prevent repeated popups
            return

        self._last_scanned_url = url
        logger.info("Auto-scan triggered for URL: %s", url)
        self._set_info(f"Auto-scanning: {url[:60]}...")
        self._run_in_thread(self._do_scan)

    # ------------------------------------------------------------------
    # Manual scan
    # ------------------------------------------------------------------

    def _cmd_manual_scan(self) -> None:
        if not self._browser.is_running:
            messagebox.showwarning("No Browser", "Please launch a browser first.")
            return
        if self._pending_future and not self._pending_future.done():
            messagebox.showinfo("Busy", "Another operation is already in progress.")
            return
        self._set_info("Manual scan — click an element in the browser…")
        self._root.iconify()  # Minimize the app window
        self._run_in_thread(self._do_manual_scan)

    def _do_manual_scan(self) -> None:
        try:
            page = self._browser.current_page
            if page is None:
                raise RuntimeError("Browser page not available.")

            self._browser.bring_to_front()

            # Inject picker JS — blocks until user clicks or presses ESC
            raw_json = page.evaluate(_MANUAL_PICK_JS)

            if raw_json is None:
                self._root.after(0, self._on_manual_scan_cancelled)
                return

            raw = json.loads(raw_json)

            from models.element_model import ScannedElement

            el = ScannedElement(
                page_title=page.title(),
                page_url=page.url,
                frame_index=0,
                tag=raw.get("tag", ""),
                element_type=raw.get("element_type", ""),
                visible_text=raw.get("visible_text", "")[:300],
                attr_id=raw.get("attr_id", ""),
                attr_name=raw.get("attr_name", ""),
                element_name=raw.get("attr_name", ""),
                attr_class=raw.get("attr_class", ""),
                attr_placeholder=raw.get("attr_placeholder", ""),
                aria_label=raw.get("aria_label", ""),
                role=raw.get("role", ""),
                href=raw.get("href", ""),
                data_testid=raw.get("data_testid", ""),
                label_text=raw.get("label_text", "")[:300],
                nearby_heading=raw.get("nearby_heading", "")[:100],
                is_visible=True,
                is_enabled=bool(raw.get("is_enabled", True)),
                is_password_field=bool(raw.get("is_password_field", False)),
                is_shadow_element=bool(raw.get("is_shadow_element", False)),
                parent_tag=raw.get("parent_tag", ""),
                parent_id=raw.get("parent_id", ""),
                parent_class=raw.get("parent_class", ""),
                nth_of_type=int(raw.get("nth_of_type", 0)),
                prev_sibling_tag=raw.get("prev_sibling_tag", ""),
                prev_sibling_id=raw.get("prev_sibling_id", ""),
                prev_sibling_text=raw.get("prev_sibling_text", "")[:80],
                next_sibling_tag=raw.get("next_sibling_tag", ""),
                next_sibling_id=raw.get("next_sibling_id", ""),
                next_sibling_text=raw.get("next_sibling_text", "")[:80],
                has_direct_text=bool(raw.get("has_direct_text", True)),
            )

            # Generate locators (with DOM validation)
            self._locator.decorate_elements([el], page=page)
            
            # Capture screenshot if enabled
            if self._screenshot_enabled.get():
                self._capture_screenshots([el], page)

            # Add to session
            self._session.add_element_to_url(page.url, page.title(), el)

            self._root.after(0, lambda: self._on_manual_scan_complete(el))

        except Exception as exc:
            logger.exception("Manual scan failed")
            self._root.after(
                0, lambda: self._on_manual_scan_error(str(exc))
            )

    def _on_manual_scan_complete(self, el) -> None:
        self._root.deiconify()
        self._root.lift()
        all_pages = self._session.pages
        self._table.load_pages(all_pages)
        total = sum(len(p.elements) for p in all_pages)
        self._lbl_element_count_var.set(str(total))      # type: ignore[attr-defined]
        
        # Log both CSS and XPath for verification
        logger.info(
            "Manual scan complete - Tag: %s, CSS: %s, XPath: %s",
            el.tag,
            el.css_selector[:80] if el.css_selector else "(empty)",
            el.xpath[:80] if el.xpath else "(empty)"
        )
        
        self._set_info(
            f"Manual scan captured: {el.tag} — CSS: {el.css_selector[:60] if el.css_selector else 'N/A'} | XPath: {el.xpath[:60] if el.xpath else 'N/A'}"
        )

    def _on_manual_scan_cancelled(self) -> None:
        self._root.deiconify()
        self._root.lift()
        self._set_info("Manual scan cancelled.")

    def _on_manual_scan_error(self, error_msg: str) -> None:
        self._root.deiconify()
        self._root.lift()
        self._set_info(f"Manual scan error: {error_msg}", error=True)

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
            self._lbl_browser_var.set("Not running")      # type: ignore[attr-defined]
            self._lbl_url_var.set("—")                    # type: ignore[attr-defined]

        self._root.after(3000, self._refresh_status)

    def _worker_fetch_status(self) -> None:
        """Runs on the playwright worker thread — safe to call Playwright here."""
        try:
            url = self._browser.current_url
            running = self._browser.is_running
        except Exception:
            url, running = "", False
        self._root.after(0, lambda: self._apply_status(running, url, ""))

    def _apply_status(self, running: bool, url: str, title: str) -> None:
        """Runs on the UI thread — safe to update Tkinter vars here."""
        if running:
            self._lbl_browser_var.set(f"{self._browser_var.get()} (Running)")  # type: ignore[attr-defined]
            self._lbl_url_var.set(url or "—")                    # type: ignore[attr-defined]
        else:
            self._lbl_browser_var.set("Not running")      # type: ignore[attr-defined]
            self._lbl_url_var.set("—")                    # type: ignore[attr-defined]

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
            f"Name:           {el.element_name or el.attr_name}",
            f"DOM Name:       {el.attr_name}",
            f"Class:          {el.attr_class}",
            f"Placeholder:    {el.attr_placeholder}",
            f"ARIA Label:     {el.aria_label}",
            f"Role:           {el.role}",
            f"Label Text:     {el.label_text}",
            f"Heading:        {el.nearby_heading}",
            f"Shadow DOM:     {el.is_shadow_element}",
            f"Shadow Host:    {el.shadow_host_tag}#{el.shadow_host_id}",
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
    # Duplicate page detection
    # ------------------------------------------------------------------

    def _check_page_already_scanned(self, url: str) -> bool:
        """
        Check if a page with this URL is already scanned.
        Shows a blocking popup if already scanned.
        Returns True if already scanned (skip scan), False if new (proceed with scan).
        Must be called from worker thread.
        """
        if not url or url == "about:blank":
            return False
        
        existing_page = self._session.get_page_by_url(url)
        if existing_page:
            # Show popup on main thread and wait for user to click OK
            logger.info("Page already scanned: %s (title: %s)", url, existing_page.page_title)
            # Schedule on main thread and block until done
            import queue
            result_queue = queue.Queue()
            
            def show_popup():
                messagebox.showinfo(
                    "Page Already Scanned",
                    f"This page has already been scanned:\\n\\n{existing_page.page_title}\\n\\n"
                    f"Scan skipped. Auto-scan will continue monitoring for new URLs.",
                    parent=self._root
                )
                result_queue.put(True)
            
            self._root.after(0, show_popup)
            result_queue.get()  # Block until popup is closed
            return True
        
        return False

    def _check_page_already_scanned_async(self, url: str) -> bool:
        """
        Check if a page with this URL is already scanned.
        Shows a non-blocking popup if already scanned.
        Returns True if already scanned (skip scan), False if new (proceed with scan).
        Can be called from any thread.
        """
        if not url or url == "about:blank":
            return False
        
        existing_page = self._session.get_page_by_url(url)
        if existing_page:
            logger.info("Page already scanned: %s (title: %s)", url, existing_page.page_title)
            # Schedule popup on main thread (non-blocking)
            def show_popup():
                messagebox.showinfo(
                    "Page Already Scanned",
                    f"This page has already been scanned:\\n\\n{existing_page.page_title}\\n\\n"
                    f"Scan skipped. Auto-scan will continue monitoring for new URLs.",
                    parent=self._root
                )
                self._set_info("Scan skipped - page already scanned")
            
            self._root.after(0, show_popup)
            return True
        
        return False

    # ------------------------------------------------------------------
    # Thread helper
    # ------------------------------------------------------------------

    def _run_in_thread(self, target) -> None:
        """Submit *target* to the single-worker Playwright executor."""
        self._pending_future = self._pw_executor.submit(target)
