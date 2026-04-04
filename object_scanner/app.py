"""
app.py — application entry point.

Sets up logging, creates the Tk root window, and starts the event loop.
Run with:  python app.py
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox

# Make sure the package root is on sys.path when running directly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# When running as a PyInstaller one-file exe, Playwright is extracted to a
# temporary directory and cannot find its browser binaries there.
# Pointing PLAYWRIGHT_BROWSERS_PATH to the user's real ms-playwright folder
# fixes this.  We only set it when it isn't already overridden by the user.
os.environ.setdefault(
    "PLAYWRIGHT_BROWSERS_PATH",
    os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ms-playwright"),
)

import config
from ui.main_window import MainWindow
from ui import theme


def _ensure_playwright_browsers() -> bool:
    """
    Check whether the Playwright Chromium browser binary is available.
    If not, offer to install it automatically.
    Returns True if browsers are ready, False if the user declined or install failed.
    """
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            # Try to get the executable path; raises if not installed
            _ = pw.chromium.executable_path
        return True
    except Exception:
        pass

    # Binary not found — ask the user
    answer = messagebox.askyesno(
        "Playwright Browser Not Found",
        "The Chromium browser required by this app is not installed.\n\n"
        "This is a one-time ~150 MB download managed by Playwright "
        "(not a system Chrome install).\n\n"
        "Download and install it now?",
    )
    if not answer:
        messagebox.showwarning(
            "Cannot Continue",
            "Chromium is required. Run this command manually and restart:\n\n"
            "    playwright install chromium",
        )
        return False

    # Run install in a blocking subprocess so the user sees progress in the console
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
        )
        messagebox.showinfo(
            "Installation Complete",
            "Chromium installed successfully. The app is ready to use.",
        )
        return True
    except subprocess.CalledProcessError as exc:
        messagebox.showerror(
            "Installation Failed",
            f"playwright install chromium failed:\n{exc}\n\n"
            "Try running it manually from the command line.",
        )
        return False


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def main() -> None:
    _configure_logging()

    root = tk.Tk()
    root.withdraw()   # Hide root while we do pre-flight checks

    if not _ensure_playwright_browsers():
        root.destroy()
        return

    root.deiconify()

    # Apply dark theme (must happen before any widgets are created)
    theme.apply(root)

    # Ensure data/exports directory exists
    config.EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    _app = MainWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
