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
        self._all_pages: list[Page] = []
        self._new_page_callback = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """True once launch() has succeeded and before close() is called."""
        return self._is_running

    @property
    def current_page(self) -> Optional[Page]:
        """Return the currently active page."""
        if self.is_running and self._context and self._context.pages:
            # Always return the most recently active page to handle navigation
            return self._context.pages[-1]
        return None

    @property
    def all_pages(self) -> list[Page]:
        """Return all open pages/tabs."""
        if not self.is_running or not self._context:
            return []
        # Get fresh list from context
        return self._context.pages

    def set_new_page_callback(self, callback) -> None:
        """Set a callback function to be called when a new page/tab is opened."""
        self._new_page_callback = callback

    @property
    def current_url(self) -> str:
        """Return the URL of the current page or empty string."""
        try:
            # Always get the freshest page from context to catch navigation changes
            if self.is_running and self._context and self._context.pages:
                # Get the most recently focused/active page
                active_page = self._context.pages[-1]
                if active_page:
                    # Evaluate the URL directly from the page's JavaScript context
                    # This ensures we get the actual current URL even after navigation
                    try:
                        url = active_page.evaluate("window.location.href")
                        return url
                    except Exception:
                        # Fallback to url property if evaluate fails
                        return active_page.url if active_page else ""
            return ""
        except Exception as exc:
            logger.warning("current_url error: %s", exc)
            return ""

    @property
    def current_title(self) -> str:
        """Return the title of the current page or empty string."""
        try:
            page = self.current_page
            if page:
                return page.title()
            return ""
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
        
        # Set up listener for new pages/tabs
        self._context.on("page", self._on_new_page)
        
        self._page = self._context.new_page()
        self._all_pages = [self._page]

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
        if not self.is_running:
            return
        
        page = self.current_page
        if page:
            try:
                page.bring_to_front()
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
        if not self.is_running:
            return False
        
        # Always get the current active page (handles navigation)
        page = self.current_page
        if page is None:
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

    // Scroll element into view immediately for instant focus
    el.scrollIntoView({ behavior: 'auto', block: 'center', inline: 'center' });

    // Brief delay to ensure element is positioned and visible
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
    }, 100);

    return true;
}
"""
        # Un-minimize the browser window so it becomes visible to the user.
        # CDP Browser.setWindowBounds works for Chromium/Chrome/Edge; the
        # try/except ensures Firefox and WebKit fall through silently.
        try:
            cdp = self._context.new_cdp_session(page)
            win_info = cdp.send("Browser.getWindowForTarget", {})
            cdp.send(
                "Browser.setWindowBounds",
                {"windowId": win_info["windowId"], "bounds": {"windowState": "normal"}},
            )
            cdp.detach()
        except Exception as exc:
            logger.debug("CDP window restore skipped: %s", exc)

        try:
            page.bring_to_front()
            result = page.evaluate(js, [css_selector, xpath])
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

    def _on_new_page(self, page: Page) -> None:
        """Called when a new page/tab is opened in the browser context."""
        logger.info("New page detected: %s", page.url)
        self._all_pages.append(page)
        
        # Switch to the new page as the current page
        self._page = page
        
        # Call the callback if set
        if self._new_page_callback:
            try:
                self._new_page_callback(page)
            except Exception as exc:
                logger.exception("Error in new page callback: %s", exc)

    def capture_element_screenshot(
        self, css_selector: str, xpath: str, is_shadow_element: bool = False
    ) -> Optional[bytes]:
        """
        Capture a screenshot with a red box around the specified element.
        Returns PNG image data as bytes if successful, None otherwise.
        Must be called from the playwright worker thread.
        
        Args:
            css_selector: CSS selector for the element
            xpath: XPath for the element
            is_shadow_element: If True, uses pierce selector to handle Shadow DOM
            
        Returns:
            PNG image bytes with red box drawn, or None if failed
        """
        if not self.is_running:
            return None
        
        # Always get the current active page (handles navigation)
        page = self.current_page
        if page is None:
            return None

        try:
            from PIL import Image, ImageDraw
            import tempfile
            from pathlib import Path
            import io

            # First, try to locate the element
            element = None
            
            # For Shadow DOM elements, use pierce selector which penetrates shadow roots
            if is_shadow_element and css_selector:
                try:
                    # Pierce selector can find elements inside shadow DOM
                    element = page.query_selector(f"pierce={css_selector}")
                    if element:
                        logger.debug("Shadow DOM element found using pierce selector: %s", css_selector)
                except Exception as e:
                    logger.debug("Pierce selector failed: %s", e)
            
            # Prioritize XPath if available (usually more specific with text matching)
            # Note: XPath doesn't work for Shadow DOM elements
            if not element and xpath and not is_shadow_element:
                try:
                    element = page.query_selector(f"xpath={xpath}")
                    if element:
                        logger.debug("Element found using XPath: %s", xpath)
                except Exception as e:
                    logger.debug("XPath selector failed: %s", e)
                    pass
            
            if not element and css_selector and not is_shadow_element:
                try:
                    element = page.query_selector(css_selector)
                    if element:
                        logger.debug("Element found using CSS: %s", css_selector)
                except Exception as e:
                    logger.debug("CSS selector failed: %s", e)
                    pass
            
            if not element:
                logger.warning("Element not found for screenshot (is_shadow=%s)", is_shadow_element)
                return None

            # Get element bounding box
            box = element.bounding_box()
            if not box:
                logger.warning("Element has no bounding box")
                return None

            # Scroll element into view
            element.scroll_into_view_if_needed()
            
            # Take viewport screenshot (not full page) to temporary file
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                temp_path = tmp.name
            
            # Try screenshot with reasonable timeout first
            screenshot_success = False
            primary_timeout = config.SCREENSHOT_TIMEOUT_MS
            retry_timeout = max(5000, primary_timeout // 2)  # Half of primary, min 5s
            
            try:
                page.screenshot(
                    path=temp_path, 
                    full_page=False, 
                    timeout=primary_timeout,
                    animations="disabled"  # Skip waiting for animations/transitions
                )
                screenshot_success = True
            except Exception as e:
                logger.debug("First screenshot attempt timed out, retrying with %dms: %s", retry_timeout, str(e)[:100])
                # Retry with more aggressive timeout - some pages have slow font loading
                try:
                    page.screenshot(
                        path=temp_path,
                        full_page=False,
                        timeout=retry_timeout,
                        animations="disabled"
                    )
                    screenshot_success = True
                except Exception as e2:
                    logger.warning("Screenshot failed after retry: %s", str(e2)[:100])
                    Path(temp_path).unlink(missing_ok=True)
                    return None
            
            if not screenshot_success:
                Path(temp_path).unlink(missing_ok=True)
                return None

            # Open the screenshot and draw red box
            img = Image.open(temp_path)
            draw = ImageDraw.Draw(img)
            
            # Get updated bounding box after scroll (coordinates relative to viewport)
            box = element.bounding_box()
            if not box:
                logger.warning("Element bounding box lost after scroll")
                Path(temp_path).unlink(missing_ok=True)
                return None
            
            # Draw red rectangle around element
            x = box["x"]
            y = box["y"]
            width = box["width"]
            height = box["height"]
            
            # Draw thick red border
            for offset in range(5):  # 5 pixel thick border
                draw.rectangle(
                    [x - offset, y - offset, x + width + offset, y + height + offset],
                    outline="red"
                )
            
            # Convert image to PNG bytes
            img_bytes_io = io.BytesIO()
            img.save(img_bytes_io, format='PNG')
            img_bytes = img_bytes_io.getvalue()
            
            # Clean up temp file
            Path(temp_path).unlink(missing_ok=True)
            
            logger.info("Element screenshot captured (%d bytes)", len(img_bytes))
            return img_bytes
            
        except Exception as exc:
            logger.exception("Failed to capture element screenshot: %s", exc)
            return None
