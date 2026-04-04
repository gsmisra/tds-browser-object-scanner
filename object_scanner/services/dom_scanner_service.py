"""
services/dom_scanner_service.py  —  JS injection + interactive-element extraction.

Injects a small JavaScript snippet into the current page to discover every
interactive element and return its attributes as a JSON array.  The raw data
is then enriched with generated CSS / XPath locators via the locator service.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import List

from models.element_model import ScannedElement, ScannedPage
from services.locator_service import build_locators

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JavaScript injected into the page
# ---------------------------------------------------------------------------

_SCAN_JS = """
(function scanPage(skipHidden) {
    var TAGS = ['input', 'button', 'select', 'textarea', 'a',
                'label', 'form', '[role]', '[tabindex]'];

    function isHidden(el) {
        if (!skipHidden) return false;
        var s = window.getComputedStyle(el);
        return s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0';
    }

    function getXPath(el) {
        if (el.id) return '//*[@id="' + el.id + '"]';
        var parts = [];
        while (el && el.nodeType === 1) {
            var idx = 1;
            var sib = el.previousElementSibling;
            while (sib) {
                if (sib.tagName === el.tagName) idx++;
                sib = sib.previousElementSibling;
            }
            parts.unshift(el.tagName.toLowerCase() + '[' + idx + ']');
            el = el.parentElement;
        }
        return '/' + parts.join('/');
    }

    function getNthIndex(el) {
        var idx = 1;
        var sib = el.previousElementSibling;
        while (sib) {
            if (sib.tagName === el.tagName) idx++;
            sib = sib.previousElementSibling;
        }
        return idx;
    }

    function getVisibleText(el) {
        return (el.innerText || el.textContent || '').trim().substring(0, 200);
    }

    function getLabelText(el) {
        // explicit for/id association
        if (el.id) {
            var lbl = document.querySelector('label[for="' + el.id + '"]');
            if (lbl) return (lbl.innerText || lbl.textContent || '').trim();
        }
        // wrapping label
        var parent = el.closest('label');
        if (parent) return (parent.innerText || parent.textContent || '').trim();
        // aria-labelledby
        var lblId = el.getAttribute('aria-labelledby');
        if (lblId) {
            var lblEl = document.getElementById(lblId);
            if (lblEl) return (lblEl.innerText || lblEl.textContent || '').trim();
        }
        return '';
    }

    function allAttrs(el) {
        var out = {};
        for (var i = 0; i < el.attributes.length; i++) {
            var a = el.attributes[i];
            out[a.name] = a.value;
        }
        return out;
    }

    var seen = new WeakSet();
    var results = [];

    TAGS.forEach(function(sel) {
        var nodes;
        try { nodes = document.querySelectorAll(sel); } catch(e) { return; }
        nodes.forEach(function(el) {
            if (seen.has(el)) return;
            seen.add(el);
            if (isHidden(el)) return;
            // Skip password field VALUES — never capture them
            var elType = (el.getAttribute('type') || el.tagName).toLowerCase();
            results.push({
                tag:         el.tagName.toLowerCase(),
                element_type: elType,
                element_id:  el.id || '',
                name:        el.name || el.getAttribute('name') || '',
                placeholder: el.placeholder || el.getAttribute('placeholder') || '',
                visible_text: (elType === 'password') ? '' : getVisibleText(el),
                aria_label:  el.getAttribute('aria-label') || '',
                data_testid: el.getAttribute('data-testid') || '',
                label:       getLabelText(el),
                xpath:       getXPath(el),
                nth_index:   getNthIndex(el),
                attributes:  allAttrs(el)
            });
        });
    });

    return JSON.stringify(results);
})(arguments[0]);
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_page(page, skip_hidden: bool = True, include_iframes: bool = True) -> ScannedPage:
    """Scan *page* (a Playwright Page object) and return a :class:`ScannedPage`.

    Parameters
    ----------
    page:
        A live ``playwright.sync_api.Page`` instance.
    skip_hidden:
        If *True*, elements with ``display:none`` / ``visibility:hidden`` are
        excluded from results.
    include_iframes:
        If *True*, attempt to scan same-origin iframes as well.
    """
    url = page.url
    title = page.title()
    scanned_at = datetime.now(timezone.utc).isoformat()

    elements: List[ScannedElement] = []

    # --- main frame ---
    elements.extend(_scan_frame(page, url, title, "", skip_hidden))

    # --- iframes ---
    if include_iframes:
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            iframe_src = frame.url or ""
            try:
                elements.extend(_scan_frame(frame, url, title, iframe_src, skip_hidden))
            except Exception as exc:
                log.warning("Skipping cross-origin or inaccessible iframe %s: %s", iframe_src, exc)

    return ScannedPage(url=url, title=title, scanned_at=scanned_at, elements=elements)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _scan_frame(frame, page_url: str, page_title: str, iframe_src: str, skip_hidden: bool) -> List[ScannedElement]:
    """Execute the scan script inside *frame* and return enriched elements."""
    try:
        raw_json: str = frame.evaluate(_SCAN_JS, skip_hidden)
    except Exception as exc:
        log.warning("JS evaluation failed in frame %s: %s", iframe_src or "main", exc)
        return []

    try:
        raw_items = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError) as exc:
        log.error("Failed to parse scan JSON: %s", exc)
        return []

    elements = []
    for idx, item in enumerate(raw_items, start=1):
        el = ScannedElement(
            tag=item.get("tag", ""),
            element_type=item.get("element_type", ""),
            element_id=item.get("element_id", ""),
            name=item.get("name", ""),
            placeholder=item.get("placeholder", ""),
            visible_text=item.get("visible_text", ""),
            aria_label=item.get("aria_label", ""),
            data_testid=item.get("data_testid", ""),
            label=item.get("label", ""),
            xpath=item.get("xpath", ""),
            nth_index=item.get("nth_index", idx),
            page_url=page_url,
            page_title=page_title,
            iframe_src=iframe_src,
            attributes=item.get("attributes", {}),
        )
        css, xpath, confidence = build_locators(el)
        # Prefer the JS-built xpath only if we couldn't build a better one
        if not el.xpath:
            el.xpath = xpath
        el.css_selector = css
        el.confidence = confidence
        elements.append(el)

    return elements
