"""
services/browser_service.py  —  Playwright browser lifecycle management.

Wraps the synchronous Playwright API so the rest of the application never
needs to import Playwright directly.
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)


class BrowserService:
    """Manages a single visible Playwright browser instance."""

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._browser_type: str = "chromium"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def launch(self, browser_type: str = "chromium", start_url: str = "about:blank") -> None:
        """Start Playwright and open a visible browser window.

        Parameters
        ----------
        browser_type:
            ``"chromium"``, ``"firefox"``, or ``"webkit"``.
        start_url:
            Initial URL to open (default: ``about:blank``).
        """
        if self._browser is not None:
            log.warning("Browser already launched; call close() first.")
            return

        from playwright.sync_api import sync_playwright  # lazy import

        self._browser_type = browser_type.lower()
        self._playwright = sync_playwright().start()

        launcher = getattr(self._playwright, self._browser_type, None)
        if launcher is None:
            raise ValueError(f"Unknown browser type: {browser_type!r}")

        self._browser = launcher.launch(headless=False)
        self._context = self._browser.new_context()
        self._page = self._context.new_page()

        if start_url and start_url != "about:blank":
            try:
                self._page.goto(start_url, wait_until="domcontentloaded")
            except Exception as exc:
                log.warning("Could not navigate to start_url %r: %s", start_url, exc)

        log.info("Browser launched: %s", self._browser_type)

    def close(self) -> None:
        """Gracefully close the browser and clean up Playwright resources."""
        try:
            if self._page and not self._page.is_closed():
                self._page.close()
        except Exception:
            pass
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        log.info("Browser closed.")

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    @property
    def is_open(self) -> bool:
        """Return *True* if a browser page is currently open and usable."""
        try:
            return self._page is not None and not self._page.is_closed()
        except Exception:
            return False

    @property
    def page(self):
        """Return the active Playwright :class:`Page`, or ``None``."""
        return self._page

    @property
    def current_url(self) -> str:
        """Return the URL currently displayed in the browser."""
        try:
            return self._page.url if self.is_open else ""
        except Exception:
            return ""

    @property
    def current_title(self) -> str:
        """Return the document title of the page currently displayed."""
        try:
            return self._page.title() if self.is_open else ""
        except Exception:
            return ""

    def wait_for_load(self, timeout_ms: int = 10_000) -> None:
        """Wait for the page to reach ``domcontentloaded`` state."""
        if not self.is_open:
            return
        try:
            self._page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except Exception as exc:
            log.warning("wait_for_load timed out: %s", exc)
