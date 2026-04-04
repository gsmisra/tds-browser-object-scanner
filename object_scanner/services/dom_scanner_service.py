"""
DOMScannerService — injects JavaScript into the active page to extract
interactive element metadata.

Responsibilities:
- Build the JS extraction payload
- Execute it against the main frame and optionally iframes
- Return a list of raw element dicts (not yet decorated with locators)
- Handle timing, stale-reference, and navigation errors gracefully
"""

from __future__ import annotations

import json
import logging
from typing import Any

from playwright.sync_api import Page

import config
from models.element_model import ScannedElement, ScannedPage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JavaScript injected into the page
# ---------------------------------------------------------------------------
# NOTE: This script runs in the browser's JS context.
# It must be a self-contained expression that returns a JSON-compatible value.

_DOM_EXTRACTION_JS = """
(skipHidden) => {
    const INTERACTIVE_SELECTORS = [
        'input', 'button', 'select', 'textarea', 'a[href]',
        '[role="button"]', '[role="link"]', '[role="checkbox"]',
        '[role="radio"]', '[role="menuitem"]', '[role="tab"]',
        '[role="option"]', '[role="switch"]', '[role="combobox"]',
        '[contenteditable="true"]', '[contenteditable=""]',
        '[onclick]', '[tabindex]'
    ].join(',');

    function getText(el) {
        const text = (el.innerText || el.textContent || '').trim();
        return text.length > 200 ? text.substring(0, 200) : text;
    }

    function getNearestLabel(el) {
        // Check aria-labelledby
        const lblId = el.getAttribute('aria-labelledby');
        if (lblId) {
            const lbl = document.getElementById(lblId);
            if (lbl) return (lbl.innerText || lbl.textContent || '').trim();
        }
        // Check explicit <label for="id">
        const id = el.getAttribute('id');
        if (id) {
            const lbl = document.querySelector('label[for="' + CSS.escape(id) + '"]');
            if (lbl) return (lbl.innerText || lbl.textContent || '').trim();
        }
        // Check wrapping <label>
        let parent = el.parentElement;
        let depth = 0;
        while (parent && depth < 3) {
            if (parent.tagName === 'LABEL') {
                return (parent.innerText || parent.textContent || '').trim();
            }
            parent = parent.parentElement;
            depth++;
        }
        return '';
    }

    function getNearestHeading(el) {
        let node = el.parentElement;
        let depth = 0;
        while (node && depth < 8) {
            const heading = node.querySelector('h1,h2,h3,h4,h5,h6');
            if (heading) {
                return (heading.innerText || heading.textContent || '').trim().substring(0, 100);
            }
            // Also check if node itself is a heading
            if (/^H[1-6]$/.test(node.tagName)) {
                return (node.innerText || node.textContent || '').trim().substring(0, 100);
            }
            node = node.parentElement;
            depth++;
        }
        return '';
    }

    function isVisible(el) {
        if (!el.offsetParent && el.tagName !== 'BODY') return false;
        const style = window.getComputedStyle(el);
        return style.display !== 'none'
            && style.visibility !== 'hidden'
            && style.opacity !== '0'
            && el.offsetWidth > 0
            && el.offsetHeight > 0;
    }

    function isEnabled(el) {
        return !el.disabled && !el.getAttribute('aria-disabled');
    }

    const seen = new Set();
    const results = [];
    let index = 0;

    document.querySelectorAll(INTERACTIVE_SELECTORS).forEach(function(el) {
        if (seen.has(el)) return;
        seen.add(el);

        const vis = isVisible(el);
        if (skipHidden && !vis) return;

        const tag = el.tagName.toLowerCase();
        const elType = el.getAttribute('type') || '';
        const isPassword = tag === 'input' && elType.toLowerCase() === 'password';

        results.push({
            tag: tag,
            element_type: elType || tag,
            visible_text: getText(el),
            attr_id: el.getAttribute('id') || '',
            attr_name: el.getAttribute('name') || '',
            attr_class: (el.getAttribute('class') || '').trim(),
            attr_placeholder: el.getAttribute('placeholder') || '',
            aria_label: el.getAttribute('aria-label') || '',
            role: el.getAttribute('role') || '',
            href: el.getAttribute('href') || '',
            data_testid: el.getAttribute('data-testid')
                || el.getAttribute('data-test-id')
                || el.getAttribute('data-test')
                || '',
            label_text: getNearestLabel(el),
            nearby_heading: getNearestHeading(el),
            is_visible: vis,
            is_enabled: isEnabled(el),
            is_password_field: isPassword,
            element_index: index++
        });
    });

    return JSON.stringify(results);
}
"""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class DOMScannerService:
    """Extracts interactive elements from the active Playwright page."""

    def scan_page(self, page: Page) -> ScannedPage:
        """
        Full page scan: main frame + optional iframes.
        Returns a populated ScannedPage (elements have no locators yet).
        """
        try:
            page.wait_for_load_state(
                "domcontentloaded", timeout=config.SCAN_TIMEOUT_MS
            )
        except Exception as exc:
            logger.warning("Page did not finish loading before scan: %s", exc)

        page_url = page.url
        page_title = ""
        try:
            page_title = page.title()
        except Exception:
            page_title = "(unknown)"

        scanned = ScannedPage(page_url=page_url, page_title=page_title)

        # --- Main frame ---
        main_elements = self._extract_from_frame(page, frame_index=0)
        scanned.elements.extend(main_elements)

        # --- Iframes ---
        if config.INCLUDE_IFRAMES:
            frames = page.frames
            for i, frame in enumerate(frames[1:], start=1):   # skip main frame
                try:
                    frame_elements = self._extract_from_frame(frame, frame_index=i)
                    scanned.elements.extend(frame_elements)
                    logger.debug(
                        "iframe %d: extracted %d elements", i, len(frame_elements)
                    )
                except Exception as exc:
                    logger.warning("Could not scan iframe %d: %s", i, exc)

        # Stamp page context onto each element
        for el in scanned.elements:
            el.page_id = scanned.page_id
            el.page_title = scanned.page_title
            el.page_url = scanned.page_url

        logger.info(
            "Scan complete — %d elements found on '%s'",
            len(scanned.elements),
            page_title,
        )
        return scanned

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_from_frame(
        self, frame_or_page: Any, frame_index: int
    ) -> list[ScannedElement]:
        """Run JS extraction against a single frame and return ScannedElements."""
        try:
            raw_json: str = frame_or_page.evaluate(
                _DOM_EXTRACTION_JS, config.SKIP_HIDDEN_ELEMENTS
            )
            raw_list: list[dict] = json.loads(raw_json)
        except Exception as exc:
            logger.warning("JS extraction failed on frame %d: %s", frame_index, exc)
            return []

        elements: list[ScannedElement] = []
        for raw in raw_list:
            el = ScannedElement(
                frame_index=frame_index,
                tag=raw.get("tag", ""),
                element_type=raw.get("element_type", ""),
                visible_text=self._safe_str(raw.get("visible_text", "")),
                attr_id=raw.get("attr_id", ""),
                attr_name=raw.get("attr_name", ""),
                attr_class=raw.get("attr_class", ""),
                attr_placeholder=raw.get("attr_placeholder", ""),
                aria_label=raw.get("aria_label", ""),
                role=raw.get("role", ""),
                href=raw.get("href", ""),
                data_testid=raw.get("data_testid", ""),
                label_text=self._safe_str(raw.get("label_text", "")),
                nearby_heading=self._safe_str(raw.get("nearby_heading", "")),
                is_visible=bool(raw.get("is_visible", True)),
                is_enabled=bool(raw.get("is_enabled", True)),
                is_password_field=bool(raw.get("is_password_field", False)),
                element_index=int(raw.get("element_index", 0)),
            )
            elements.append(el)
        return elements

    @staticmethod
    def _safe_str(value: Any, max_length: int = 300) -> str:
        if value is None:
            return ""
        s = str(value).strip()
        return s[:max_length] if len(s) > max_length else s
