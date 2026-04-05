"""
BrowserService — owns the Playwright lifecycle.

Responsibilities:
- Launch and close the browser / page
- Expose the active page for other services
- Report browser state
"""

from __future__ import annotations

import logging
from typing import Optional

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    sync_playwright,
)

import config

logger = logging.getLogger(__name__)


class BrowserService:
    """Manages a single Playwright browser session."""

    def __init__(self) -> None:
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._is_running: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """True once launch() has succeeded and before close() is called."""
        return self._is_running

    @property
    def current_page(self) -> Optional[Page]:
        return self._page if self.is_running else None

    @property
    def current_url(self) -> str:
        try:
            return self._page.url if self.is_running else ""  # type: ignore[union-attr]
        except Exception:
            return ""

    @property
    def current_title(self) -> str:
        try:
            return self._page.title() if self.is_running else ""  # type: ignore[union-attr]
        except Exception:
            return ""

    def launch(
        self,
        browser_type: Optional[str] = None,
        start_url: Optional[str] = None,
    ) -> None:
        """
        Launch a new browser session.
        Raises RuntimeError if a session is already active.
        """
        if self.is_running:
            raise RuntimeError("A browser session is already active.")

        chosen_type = (browser_type or config.BROWSER_TYPE).lower()
        chosen_url = start_url or config.START_URL

        logger.info("Launching %s browser…", chosen_type)
        self._playwright = sync_playwright().start()

        launcher, channel = self._get_browser_launcher(chosen_type)
        launch_kwargs = dict(
            headless=config.HEADLESS,
            slow_mo=config.SLOW_MO,
            args=["--start-maximized"],
        )
        if channel:
            launch_kwargs["channel"] = channel

        try:
            self._browser = launcher.launch(**launch_kwargs)
        except Exception:
            # Ensure Playwright is fully torn down so the next launch attempt
            # doesn't collide with a stale asyncio event loop.
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
            raise

        # New context with no fixed viewport so the user can resize freely
        self._context = self._browser.new_context(no_viewport=True)
        self._page = self._context.new_page()

        if chosen_url and chosen_url != "about:blank":
            self._page.goto(chosen_url, timeout=config.SCAN_TIMEOUT_MS)

        self._is_running = True
        logger.info("Browser launched successfully.")

    def close(self) -> None:
        """Close the browser session and clean up."""
        logger.info("Closing browser session…")
        self._is_running = False

        for obj, method in [
            (self._context, "close"),
            (self._browser, "close"),
        ]:
            if obj is not None:
                try:
                    getattr(obj, method)()
                except Exception as exc:
                    logger.warning("Error during close: %s", exc)

        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception as exc:
                logger.warning("Error stopping playwright: %s", exc)

        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        logger.info("Browser session closed.")

    def bring_to_front(self) -> None:
        """Bring the browser window to the foreground."""
        if self.is_running and self._page:
            try:
                self._page.bring_to_front()
            except Exception as exc:
                logger.warning("bring_to_front failed: %s", exc)

    def highlight_element(
        self, css_selector: str, xpath: str = ""
    ) -> bool:
        """
        Scroll to the element and draw a thick blinking red border around it
        for 10 blink cycles, then remove the overlay.
        Returns True if the element was found, False otherwise.
        Must be called from the playwright worker thread.
        """
        if not self.is_running or self._page is None:
            return False

        js = """
([css, xpath]) => {
    let el = null;
    if (css) {
        try { el = document.querySelector(css); } catch(e) {}
    }
    if (!el && xpath) {
        try {
            const r = document.evaluate(
                xpath, document, null,
                XPathResult.FIRST_ORDERED_NODE_TYPE, null
            );
            el = r.singleNodeValue;
        } catch(e) {}
    }
    if (!el) return false;

    el.scrollIntoView({ behavior: 'smooth', block: 'center' });

    // Wait for scroll to settle before placing the overlay
    setTimeout(() => {
        const rect = el.getBoundingClientRect();
        const pad  = 6;
        const div  = document.createElement('div');
        div.setAttribute('data-arsim-highlight', '1');
        div.style.cssText = [
            'position:fixed',
            `top:${rect.top - pad}px`,
            `left:${rect.left - pad}px`,
            `width:${rect.width + pad * 2}px`,
            `height:${rect.height + pad * 2}px`,
            'border:5px solid red',
            'outline:2px solid rgba(255,0,0,0.45)',
            'border-radius:3px',
            'pointer-events:none',
            'z-index:2147483647',
            'box-sizing:border-box'
        ].join(';');
        document.body.appendChild(div);

        // 10 blinks = 20 half-cycles at 100ms each → 2 s total
        let tick = 0;
        const iv = setInterval(() => {
            tick++;
            div.style.visibility = (tick % 2 === 0) ? 'visible' : 'hidden';
            if (tick >= 20) {
                clearInterval(iv);
                setTimeout(() => { if (div.parentNode) div.remove(); }, 80);
            }
        }, 100);
    }, 250);

    return true;
}
"""
        # Un-minimize the browser window so it becomes visible to the user.
        # CDP Browser.setWindowBounds works for Chromium/Chrome/Edge; the
        # try/except ensures Firefox and WebKit fall through silently.
        try:
            cdp = self._context.new_cdp_session(self._page)
            win_info = cdp.send("Browser.getWindowForTarget", {})
            cdp.send(
                "Browser.setWindowBounds",
                {"windowId": win_info["windowId"], "bounds": {"windowState": "normal"}},
            )
            cdp.detach()
        except Exception as exc:
            logger.debug("CDP window restore skipped: %s", exc)

        try:
            self._page.bring_to_front()
            result = self._page.evaluate(js, [css_selector, xpath])
            return bool(result)
        except Exception as exc:
            logger.warning("highlight_element JS error: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_browser_launcher(self, browser_type: str):
        """Return (launcher, channel) tuple.
        channel is a string like 'chrome' or 'msedge' for system-installed
        browsers; None means use Playwright's own managed binary.
        """
        mapping = {
            "chromium": (self._playwright.chromium, None),        # type: ignore[union-attr]
            "chrome":   (self._playwright.chromium, "chrome"),    # type: ignore[union-attr]
            "firefox":  (self._playwright.firefox,  None),        # type: ignore[union-attr]
            "webkit":   (self._playwright.webkit,   None),        # type: ignore[union-attr]
            "edge":     (self._playwright.chromium, "msedge"),    # type: ignore[union-attr]
        }
        result = mapping.get(browser_type)
        if result is None:
            logger.warning(
                "Unknown browser type '%s', defaulting to chromium.", browser_type
            )
            result = (self._playwright.chromium, None)  # type: ignore[union-attr]
        return result
