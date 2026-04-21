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

    function hasDirectText(el) {
        for (let i = 0; i < el.childNodes.length; i++) {
            const node = el.childNodes[i];
            if (node.nodeType === 3 && node.textContent.trim().length > 0) {
                return true;
            }
        }
        return false;
    }

    function getNearestLabel(el) {
        const lblId = el.getAttribute('aria-labelledby');
        if (lblId) {
            const lbl = document.getElementById(lblId);
            if (lbl) return (lbl.innerText || lbl.textContent || '').trim();
        }
        const id = el.getAttribute('id');
        if (id) {
            const lbl = document.querySelector('label[for="' + CSS.escape(id) + '"]');
            if (lbl) return (lbl.innerText || lbl.textContent || '').trim();
        }
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
                return {
                    text: (heading.innerText || heading.textContent || '').trim().substring(0, 100),
                    tag: heading.tagName.toLowerCase()
                };
            }
            if (/^H[1-6]$/.test(node.tagName)) {
                return {
                    text: (node.innerText || node.textContent || '').trim().substring(0, 100),
                    tag: node.tagName.toLowerCase()
                };
            }
            node = node.parentElement;
            depth++;
        }
        return {text: '', tag: ''};
    }

    function isVisible(el) {
        if (!el.offsetParent && el.tagName !== 'BODY' && el.tagName !== 'HTML') return false;

        // Check aria-hidden on element and ancestors
        if (el.getAttribute('aria-hidden') === 'true') return false;
        let anc = el.parentElement;
        while (anc) {
            if (anc.getAttribute('aria-hidden') === 'true') return false;
            anc = anc.parentElement;
        }

        const style = window.getComputedStyle(el);
        if (style.display === 'none') return false;
        if (style.visibility === 'hidden') return false;
        if (parseFloat(style.opacity) === 0) return false;

        // Skip very small elements (tracking pixels, hidden inputs)
        if (el.offsetWidth < 3 || el.offsetHeight < 3) return false;

        return true;
    }

    function isInteractable(el) {
        const style = window.getComputedStyle(el);
        if (style.pointerEvents === 'none') return false;
        return true;
    }

    function isEnabled(el) {
        return !el.disabled && el.getAttribute('aria-disabled') !== 'true';
    }

    function getParentInfo(el) {
        const p = el.parentElement;
        if (!p) return {tag: '', id: '', cls: ''};
        return {
            tag: p.tagName.toLowerCase(),
            id: p.getAttribute('id') || '',
            cls: (p.getAttribute('class') || '').trim()
        };
    }

    function getSiblingInfo(sib) {
        if (!sib || sib.nodeType !== 1) return {tag: '', id: '', text: '', name: ''};
        return {
            tag: sib.tagName.toLowerCase(),
            id: sib.getAttribute('id') || '',
            text: (sib.innerText || '').trim().substring(0, 80),
            name: sib.getAttribute('name') || ''
        };
    }

    function getNthOfType(el) {
        const tag = el.tagName;
        let n = 1;
        let sib = el.previousElementSibling;
        while (sib) {
            if (sib.tagName === tag) n++;
            sib = sib.previousElementSibling;
        }
        return n;
    }

    function collectInteractiveElements() {
        const roots = [document];
        const all = [];
        const seenRoots = new Set();

        while (roots.length > 0) {
            const root = roots.pop();
            if (!root || seenRoots.has(root)) continue;
            seenRoots.add(root);

            try {
                root.querySelectorAll(INTERACTIVE_SELECTORS).forEach(el => all.push(el));
            } catch (e) {}

            try {
                root.querySelectorAll('*').forEach(node => {
                    if (node.shadowRoot) {
                        roots.push(node.shadowRoot);
                    }
                });
            } catch (e) {}
        }

        return all;
    }

    const seen = new Set();
    const results = [];
    let index = 0;

    collectInteractiveElements().forEach(function(el) {
        if (seen.has(el)) return;
        seen.add(el);

        const vis = isVisible(el);
        if (!vis) return;
        if (!isInteractable(el)) return;
        if (!isEnabled(el)) return;

        const tag = el.tagName.toLowerCase();
        const elType = el.getAttribute('type') || '';
        const isPassword = tag === 'input' && elType.toLowerCase() === 'password';
        const parentInfo = getParentInfo(el);
        const prevSib = getSiblingInfo(el.previousElementSibling);
        const nextSib = getSiblingInfo(el.nextElementSibling);
        const nthOfType = getNthOfType(el);
        const headingInfo = getNearestHeading(el);
        const rootNode = el.getRootNode();
        const isShadow = !!(rootNode && rootNode.host);
        const shadowHost = isShadow ? rootNode.host : null;

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
            data_autom: el.getAttribute('data-autom')
                || el.getAttribute('data-automation-id')
                || el.getAttribute('data-auto')
                || el.getAttribute('data-qa')
                || el.getAttribute('data-cy')
                || '',
            label_text: getNearestLabel(el),
            nearby_heading: headingInfo.text,
            nearby_heading_tag: headingInfo.tag,
            is_visible: vis,
            is_enabled: isEnabled(el),
            is_password_field: isPassword,
            element_index: index++,
            parent_tag: parentInfo.tag,
            parent_id: parentInfo.id,
            parent_class: parentInfo.cls,
            nth_of_type: nthOfType,
            prev_sibling_tag: prevSib.tag,
            prev_sibling_id: prevSib.id,
            prev_sibling_text: prevSib.text,
            next_sibling_tag: nextSib.tag,
            next_sibling_id: nextSib.id,
            next_sibling_text: nextSib.text,
            has_direct_text: hasDirectText(el),
            is_shadow_element: isShadow,
            shadow_host_tag: shadowHost ? shadowHost.tagName.toLowerCase() : '',
            shadow_host_id: shadowHost ? (shadowHost.getAttribute('id') || '') : '',
            shadow_host_class: shadowHost ? ((shadowHost.getAttribute('class') || '').trim()) : ''
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
        # Step 1: Wait for initial DOM
        try:
            page.wait_for_load_state(
                "domcontentloaded", timeout=config.SCAN_TIMEOUT_MS
            )
        except Exception as exc:
            logger.warning("Page did not finish loading before scan: %s", exc)

        # Step 2: Wait for JavaScript-rendered content (SPAs)
        # Try networkidle first (all network activity settled)
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
            logger.debug("Page reached networkidle state")
        except Exception:
            # Networkidle may timeout on streaming/polling pages
            # Fallback: wait for at least one interactive element to appear
            try:
                page.wait_for_selector(
                    'button, a[href], input, select, [role="button"], [onclick]',
                    state="visible",
                    timeout=3000
                )
                logger.debug("At least one interactive element found")
            except Exception:
                # Last resort: small delay for JS frameworks to render
                logger.debug("No interactive elements detected yet, adding 1s delay for JS rendering")
                page.wait_for_timeout(1000)

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

        element_count = len(scanned.elements)
        if element_count == 0:
            logger.warning(
                "Scan found 0 elements on '%s' (%s) - page may require more time to render or uses complex JavaScript framework",
                page_title,
                page_url
            )
        else:
            logger.info(
                "Scan complete — %d elements found on '%s'",
                element_count,
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
            
            if not raw_list:
                logger.debug(
                    "Frame %d: JS extraction returned empty array - no interactive elements found",
                    frame_index
                )
        except json.JSONDecodeError as exc:
            logger.error("Frame %d: Failed to parse JSON from JS extraction: %s", frame_index, exc)
            return []
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
                element_name=self._safe_str(raw.get("attr_name", "")),
                attr_class=raw.get("attr_class", ""),
                attr_placeholder=raw.get("attr_placeholder", ""),
                aria_label=raw.get("aria_label", ""),
                role=raw.get("role", ""),
                href=raw.get("href", ""),
                data_testid=raw.get("data_testid", ""),
                data_autom=raw.get("data_autom", ""),
                label_text=self._safe_str(raw.get("label_text", "")),
                nearby_heading=self._safe_str(raw.get("nearby_heading", "")),
                nearby_heading_tag=raw.get("nearby_heading_tag", ""),
                is_visible=bool(raw.get("is_visible", True)),
                is_enabled=bool(raw.get("is_enabled", True)),
                is_password_field=bool(raw.get("is_password_field", False)),
                element_index=int(raw.get("element_index", 0)),
                parent_tag=raw.get("parent_tag", ""),
                parent_id=raw.get("parent_id", ""),
                parent_class=raw.get("parent_class", ""),
                nth_of_type=int(raw.get("nth_of_type", 0)),
                prev_sibling_tag=raw.get("prev_sibling_tag", ""),
                prev_sibling_id=raw.get("prev_sibling_id", ""),
                prev_sibling_text=self._safe_str(raw.get("prev_sibling_text", "")),
                next_sibling_tag=raw.get("next_sibling_tag", ""),
                next_sibling_id=raw.get("next_sibling_id", ""),
                next_sibling_text=self._safe_str(raw.get("next_sibling_text", "")),
                has_direct_text=bool(raw.get("has_direct_text", True)),
                is_shadow_element=bool(raw.get("is_shadow_element", False)),
                shadow_host_tag=raw.get("shadow_host_tag", ""),
                shadow_host_id=raw.get("shadow_host_id", ""),
                shadow_host_class=raw.get("shadow_host_class", ""),
            )
            elements.append(el)
        return elements

    @staticmethod
    def _safe_str(value: Any, max_length: int = 300) -> str:
        if value is None:
            return ""
        s = str(value).strip()
        return s[:max_length] if len(s) > max_length else s
