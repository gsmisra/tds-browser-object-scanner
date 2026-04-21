"""
SettingsDialog — modal popup window for application settings.

Provides:
- Browser selection dropdown
- Enable auto scanning mode checkbox
- Capture screenshot checkbox
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ui import theme


class SettingsDialog(tk.Toplevel):
    """Modal settings window."""

    def __init__(
        self,
        parent: tk.Widget,
        browser_var: tk.StringVar,
        auto_scan_var: tk.BooleanVar,
        screenshot_var: tk.BooleanVar,
    ) -> None:
        super().__init__(parent)
        self._parent = parent
        self._browser_var = browser_var
        self._auto_scan_var = auto_scan_var
        self._screenshot_var = screenshot_var

        self.title("Settings")
        self.geometry("400x250")
        self.resizable(False, False)
        self.configure(bg=theme.BG)
        self.grab_set()  # Modal
        self.focus_set()

        self._build()
        self.transient(parent)

    def _build(self) -> None:
        main_frame = ttk.Frame(self, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Browser Selection
        ttk.Label(
            main_frame, text="Browser:", font=("Segoe UI", 10, "bold")
        ).grid(row=0, column=0, sticky="w", pady=(0, 5))
        
        browser_combo = ttk.Combobox(
            main_frame,
            textvariable=self._browser_var,
            values=["chromium", "chrome", "firefox", "webkit", "edge"],
            width=25,
            state="readonly",
        )
        browser_combo.grid(row=1, column=0, sticky="ew", pady=(0, 20))

        # Auto Scanning
        ttk.Checkbutton(
            main_frame,
            text="Enable Auto Scanning Mode",
            variable=self._auto_scan_var,
            style="TCheckbutton"
        ).grid(row=2, column=0, sticky="w", pady=5)

        # Screenshot Capture
        ttk.Checkbutton(
            main_frame,
            text="Capture Screenshot",
            variable=self._screenshot_var,
            style="TCheckbutton"
        ).grid(row=3, column=0, sticky="w", pady=5)

        ttk.Label(
            main_frame,
            text="(Screenshots will show red box around scanned elements)",
            font=("Segoe UI", 8),
            foreground=theme.FG_DIM
        ).grid(row=4, column=0, sticky="w", pady=(0, 20))

        # Buttons
        btn_frame = ttk.Frame(main_frame)
        btn_frame.grid(row=5, column=0, sticky="e")

        ttk.Button(
            btn_frame, text="Close", command=self.destroy, width=12
        ).pack(side=tk.RIGHT, padx=4)
