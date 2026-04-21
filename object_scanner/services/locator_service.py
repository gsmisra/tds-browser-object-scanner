"""
LocatorService — deterministic, rule-based CSS and XPath generator.

Enhanced to:
- Always generate relative selectors (never absolute)
- Validate uniqueness against the actual page DOM when available
- Use sibling logic (following-sibling / preceding-sibling, + / ~) when
  standard approaches fail
- Build unique selectors using parent context and attribute combinations

Priority order (highest to lowest confidence):
  1. Unique id attribute              → HIGH
  2. data-testid / data-test*         → HIGH
  3. aria-label                       → HIGH / MEDIUM
  4. Unique name attribute            → MEDIUM
  5. label-derived text               → MEDIUM
  6. Visible button/link text         → MEDIUM
  7. tag + stable attribute combo     → MEDIUM
  8. Structural / nth-child fallback  → LOW

Uniqueness is validated against an in-memory set of all CSS selectors
already generated for the current scan batch.
"""

from __future__ import annotations

import logging
import re
from itertools import combinations
from typing import Any, Optional

from models.element_model import ScannedElement, SelectorQuality
from utils.string_utils import slugify

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _css_escape_id(value: str) -> str:
    """Minimal CSS id/value escaper for use in attribute selectors."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _xpath_escape(value: str) -> str:
    """
    Produce a safe XPath string literal.
    Handles values containing both single and double quotes via concat().
    """
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    # Value contains both — use concat()
    parts = value.split("'")
    concat_parts = ", \"'\", ".join(f"'{p}'" for p in parts)
    return f"concat({concat_parts})"


def _is_stable_id(value: str) -> bool:
    """
    Heuristic: an id is 'stable' if it does not look auto-generated.
    Rejects ids that are pure numbers, GUIDs, or contain long hex/random segments.
    """
    if not value:
        return False
    # Pure integer
    if value.isdigit():
        return False
    # GUID-like  e.g. "a3f2b9c1-…"
    if re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-', value, re.I):
        return False
    # Long hex string
    if re.match(r'^[0-9a-f]{12,}$', value, re.I):
        return False
    return True


def _normalise_text(text: str, max_len: int = 80) -> str:
    """Collapse whitespace (including \u00a0 non-breaking spaces) and truncate."""
    return " ".join(text.replace("\u00a0", " ").split())[:max_len]


# ---------------------------------------------------------------------------
# DOM validation JS snippets
# ---------------------------------------------------------------------------

_VALIDATE_BATCH_JS = """
(selectors) => {
    return selectors.map(s => {
        let cc = 0, xc = 0;
        try { cc = document.querySelectorAll(s.css).length; } catch(e) {}
        try {
            const r = document.evaluate(s.xpath, document, null, 7, null);
            xc = r.snapshotLength;
        } catch(e) {}
        return {id: s.id, css_count: cc, xpath_count: xc};
    });
}
"""

_COUNT_CSS_JS = """
(selector) => {
    try { 
        const elements = document.querySelectorAll(selector);
        // Count only visible elements
        let visibleCount = 0;
        elements.forEach(el => {
            if (el.offsetParent !== null || el.tagName === 'BODY') {
                visibleCount++;
            }
        });
        return visibleCount;
    } catch(e) { return -1; }
}
"""

_COUNT_XPATH_JS = """
(selector) => {
    try {
        // Create namespace resolver for SVG and other namespaced elements
        const nsResolver = (prefix) => {
            const ns = {
                'svg': 'http://www.w3.org/2000/svg',
                'xhtml': 'http://www.w3.org/1999/xhtml',
                'mathml': 'http://www.w3.org/1998/Math/MathML'
            };
            return ns[prefix] || null;
        };
        
        const r = document.evaluate(selector, document, nsResolver, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
        const totalCount = r.snapshotLength;
        
        // Count only visible elements
        let visibleCount = 0;
        for (let i = 0; i < totalCount; i++) {
            const el = r.snapshotItem(i);
            // For SVG elements, check if they're rendered
            if (el.namespaceURI === 'http://www.w3.org/2000/svg') {
                // SVG elements - check if parent svg is visible
                const svgRoot = el.closest('svg');
                if (svgRoot && (svgRoot.offsetParent !== null || svgRoot.getBoundingClientRect().width > 0)) {
                    visibleCount++;
                }
            } else if (el.offsetParent !== null || el.tagName === 'BODY') {
                visibleCount++;
            }
        }
        
        // Log when there are hidden duplicates (total > visible)
        if (totalCount > visibleCount && visibleCount > 0) {
            console.log(`XPath "${selector}" - Total in DOM: ${totalCount}, Visible: ${visibleCount} (${totalCount - visibleCount} hidden by CSS)`);
        }
        
        // Return both counts as: visibleCount * 1000 + totalCount
        // This allows us to extract both values: visible = Math.floor(result/1000), total = result % 1000
        return (visibleCount * 1000) + totalCount;
    } catch(e) { 
        console.error('XPath evaluation error:', e.message, 'for selector:', selector);
        return -1; 
    }
}
"""


# ---------------------------------------------------------------------------
# LocatorService
# ---------------------------------------------------------------------------

class LocatorService:
    """
    Generates CSS and XPath locators for each ScannedElement.
    Call ``decorate_elements`` with the full list so that uniqueness
    checks operate across the whole page batch.  Optionally pass a
    Playwright *page* to validate and refine selectors against the live DOM.
    """

    @staticmethod
    def _clean_human_text(value: str, max_len: int = 80) -> str:
        """Normalize candidate human text and strip noisy accessibility suffixes."""
        text = _normalise_text(value or "", max_len=max_len)
        if not text:
            return ""
        text = re.sub(
            r",\s*row\s+\d+\s+of\s+\d+.*$",
            "",
            text,
            flags=re.I,
        )
        text = re.sub(r"\(opens?\s+a\s+new\s+tab\)", "", text, flags=re.I)
        return _normalise_text(text, max_len=max_len)

    @staticmethod
    def _is_clear_name(value: str) -> bool:
        v = _normalise_text(value or "")
        if len(v) < 2:
            return False
        if re.fullmatch(r"[a-zA-Z]", v):
            return False
        return True

    def _best_human_label(self, el: ScannedElement) -> str:
        """Best user-facing text for naming and text-based XPath generation."""
        candidates = [
            self._clean_human_text(el.visible_text),
            self._clean_human_text(el.label_text),
            self._clean_human_text(el.aria_label),
            self._clean_human_text(el.attr_placeholder),
        ]
        for candidate in candidates:
            if self._is_clear_name(candidate):
                return candidate
        return ""

    @staticmethod
    def _name_suffix(el: ScannedElement) -> str:
        tag = (el.tag or "").lower()
        role = (el.role or "").lower()
        if tag == "a" or role == "link" or (el.href or "").strip():
            return "link"
        if tag == "button" or role == "button":
            return "button"
        if tag in ("select",) or role in ("combobox", "listbox"):
            return "dropdown"
        if tag == "textarea":
            return "textarea"
        if tag == "input":
            field_type = (el.element_type or "").lower()
            return f"{field_type}_input" if field_type and field_type != "input" else "input"
        if tag:
            return tag
        return "element"

    def _ensure_element_name(self, el: ScannedElement) -> None:
        """Populate a stable display name when DOM @name is absent or unclear."""
        dom_name = _normalise_text(el.attr_name or "", max_len=80)
        if self._is_clear_name(dom_name):
            el.element_name = slugify(dom_name)
            return

        label = self._best_human_label(el)
        if label:
            base = slugify(label)
        else:
            fallback = (
                el.data_testid
                or el.data_autom
                or el.attr_id
                or el.attr_placeholder
                or el.tag
                or "element"
            )
            base = slugify(_normalise_text(str(fallback), max_len=80)) or "element"

        suffix = self._name_suffix(el)
        if not base.endswith(f"_{suffix}"):
            base = f"{base}_{suffix}"
        el.element_name = base

    def decorate_elements(
        self, elements: list[ScannedElement], page: Any = None
    ) -> None:
        """
        In-place: generate css_selector, xpath, selector_quality, and
        selector_notes for every element in the list.
        When *page* is provided, selectors are validated against the DOM and
        non-unique ones are refined using parent/sibling strategies.
        """
        seen_css: set[str] = set()
        seen_xpath: set[str] = set()

        for el in elements:
            self._ensure_element_name(el)
            css, css_quality, css_note = self._build_css(el, seen_css)
            xpath, xp_quality, xp_note = self._build_xpath(el, seen_xpath)

            el.css_selector = css
            el.xpath = xpath
            
            # Log generated selectors for debugging
            logger.debug(
                "Generated selectors for %s: CSS='%s' (quality=%s), XPath='%s' (quality=%s)",
                el.tag or "element",
                css[:100] if css else "(empty)",
                css_quality,
                xpath[:100] if xpath else "(empty)",
                xp_quality
            )

            # Overall quality = worst of the two (before validation)
            quality_rank = {
                SelectorQuality.HIGH: 3,
                SelectorQuality.MEDIUM: 2,
                SelectorQuality.LOW: 1,
                SelectorQuality.UNKNOWN: 0,
            }
            overall = min(
                css_quality, xp_quality,
                key=lambda q: quality_rank.get(q, 0),
            )
            
            el.selector_quality = overall

            notes = "; ".join(filter(None, [css_note, xp_note]))
            el.selector_notes = notes

            seen_css.add(css)
            seen_xpath.add(xpath)

        # Validate and refine against the live DOM
        if page is not None:
            self._validate_and_refine(elements, page)

    # ------------------------------------------------------------------
    # DOM validation & refinement
    # ------------------------------------------------------------------

    def _validate_and_refine(
        self, elements: list[ScannedElement], page: Any
    ) -> None:
        """Validate selectors against the DOM and refine non-unique ones."""
        if not elements:
            return

        for el in elements:
            eval_target = self._resolve_eval_target(page, el.frame_index)

            if el.is_shadow_element:
                note = "shadow-dom element; skipped document-level uniqueness validation"
                if note not in (el.selector_notes or ""):
                    el.selector_notes = (
                        f"{el.selector_notes}; {note}" if el.selector_notes else note
                    )
                continue

            css_count = self._count_css(eval_target, el.css_selector)
            xpath_visible, xpath_total = self._count_xpath(eval_target, el.xpath)
            
            # Store counts - use TOTAL count for XPath (Playwright sees all elements, not just visible)
            el.css_element_count = css_count if css_count >= 0 else 0
            el.xpath_element_count = xpath_total if xpath_total >= 0 else 0
            
            # Add note if XPath has hidden duplicates
            if xpath_total > xpath_visible > 0:
                hidden_note = f"XPath: {xpath_visible} visible, {xpath_total} total ({xpath_total - xpath_visible} hidden)"
                el.selector_notes = (
                    f"{el.selector_notes}; {hidden_note}" if el.selector_notes else hidden_note
                )
                logger.debug("XPath has hidden duplicates: %s", hidden_note)
            
            refined = False

            if css_count != 1:
                new_css = self._refine_css(el, eval_target)
                if new_css:
                    el.css_selector = new_css
                    refined = True

            # Refine XPath if TOTAL count != 1 (even if visible count = 1)
            # Playwright locator() sees all elements, not just visible ones
            if xpath_total != 1:
                logger.debug("XPath needs refinement: total count=%d (visible=%d)", xpath_total, xpath_visible)
                new_xpath = self._refine_xpath(el, eval_target)
                if new_xpath:
                    el.xpath = new_xpath
                    refined = True
                    logger.debug("Refined XPath to: %s", new_xpath)
                else:
                    logger.warning("Failed to refine XPath - no unique candidate found")

            # Final uniqueness gate: never keep a non-unique XPath.
            final_css_count = self._count_css(eval_target, el.css_selector)
            final_xpath_visible, final_xpath_total = self._count_xpath(eval_target, el.xpath)
            
            # Update final counts after refinement - use TOTAL for XPath
            el.css_element_count = final_css_count if final_css_count >= 0 else 0
            el.xpath_element_count = final_xpath_total if final_xpath_total >= 0 else 0
            
            # Update hidden duplicates note after refinement
            if final_xpath_total > final_xpath_visible > 0:
                hidden_note = f"XPath: {final_xpath_visible} visible, {final_xpath_total} total ({final_xpath_total - final_xpath_visible} hidden)"
                # Replace old note if it exists
                if el.selector_notes and "XPath:" in el.selector_notes and "visible" in el.selector_notes:
                    import re
                    el.selector_notes = re.sub(r'XPath: \d+ visible, \d+ total \(\d+ hidden\)', hidden_note, el.selector_notes)
                elif hidden_note not in (el.selector_notes or ""):
                    el.selector_notes = (
                        f"{el.selector_notes}; {hidden_note}" if el.selector_notes else hidden_note
                    )
                logger.debug("After refinement: %s", hidden_note)

            # Upgrade quality if selectors are unique (count = 1)
            # Use TOTAL count for XPath - Playwright sees all elements
            if el.css_element_count == 1 and el.xpath_element_count == 1:
                # Both selectors are truly unique
                if el.selector_quality != SelectorQuality.HIGH:
                    logger.debug(
                        "Upgrading quality from %s to HIGH due to uniqueness: CSS count=%d, XPath count=%d",
                        el.selector_quality, el.css_element_count, el.xpath_element_count
                    )
                    el.selector_quality = SelectorQuality.HIGH
            elif el.css_element_count == 1:
                # Only CSS is unique - quality limited to MEDIUM
                if el.selector_quality == SelectorQuality.LOW:
                    logger.debug("Upgrading quality from LOW to MEDIUM (CSS unique, XPath not)")
                    el.selector_quality = SelectorQuality.MEDIUM

            # Don't suppress XPath - show count instead
            # if final_xpath_total != 1:
            #     el.xpath = ""
            #     note = "xpath suppressed: selector not unique"
            #     el.selector_notes = (
            #         f"{el.selector_notes}; {note}" if el.selector_notes else note
            #     )
            #     refined = True

            if final_css_count != 1:
                note = "css may be non-unique"
                el.selector_notes = (
                    f"{el.selector_notes}; {note}" if el.selector_notes else note
                )

            if refined:
                self._regrade_quality(el)

    @staticmethod
    def _resolve_eval_target(page: Any, frame_index: int) -> Any:
        """Return the proper Playwright evaluation target for this element."""
        if frame_index <= 0:
            return page
        try:
            frames = getattr(page, "frames", [])
            if 0 <= frame_index < len(frames):
                return frames[frame_index]
        except Exception:
            pass
        return page

    @staticmethod
    def _count_css(eval_target: Any, selector: str) -> int:
        if not selector:
            return 0
        try:
            return int(eval_target.evaluate(_COUNT_CSS_JS, selector))
        except Exception:
            return -1

    @staticmethod
    def _count_xpath(eval_target: Any, selector: str) -> tuple[int, int]:
        """Count XPath matches. Returns (visible_count, total_count)."""
        if not selector:
            return (0, 0)
        try:
            result = int(eval_target.evaluate(_COUNT_XPATH_JS, selector))
            if result < 0:
                return (-1, -1)
            # Decode: visible = result // 1000, total = result % 1000
            visible_count = result // 1000
            total_count = result % 1000
            return (visible_count, total_count)
        except Exception:
            return (-1, -1)

    def _refine_css(self, el: ScannedElement, page: Any) -> Optional[str]:
        """Try progressively more specific CSS strategies until unique."""
        tag = el.tag or "*"
        candidates: list[str] = []

        base = self._base_css_attr(el)

        # Strategy A: href-based for links (before multi-attribute)
        if el.href and el.tag == "a":
            # Full href
            candidates.append(f'a[href="{_css_escape_id(el.href)}"]')
            # href with class
            if el.attr_class:
                own_tokens = [
                    t for t in el.attr_class.split()
                    if t and not re.match(r'^[0-9a-f]{5,}$', t, re.I)
                ][:2]
                if own_tokens:
                    ec = "." + ".".join(own_tokens)
                    candidates.append(f'a{ec}[href="{_css_escape_id(el.href)}"]')
        
        # Strategy B: iterate multi-attribute combinations (2..3 attrs).
        attr_parts: list[str] = []
        if self._is_clear_name(el.attr_name):
            attr_parts.append(f'[name="{_css_escape_id(el.attr_name)}"]')
        if el.data_testid:
            attr_parts.append(f'[data-testid="{_css_escape_id(el.data_testid)}"]')
        if el.data_autom:
            attr_parts.append(f'[data-autom="{_css_escape_id(el.data_autom)}"]')
        if el.aria_label:
            cleaned_aria = self._clean_human_text(el.aria_label)
            if cleaned_aria and cleaned_aria != _normalise_text(el.aria_label):
                attr_parts.append(f'[aria-label^="{_css_escape_id(cleaned_aria)}"]')
            attr_parts.append(f'[aria-label="{_css_escape_id(el.aria_label)}"]')
        if el.attr_placeholder:
            attr_parts.append(f'[placeholder="{_css_escape_id(el.attr_placeholder)}"]')
        if el.role:
            attr_parts.append(f'[role="{_css_escape_id(el.role)}"]')
        if el.href:
            attr_parts.append(f'[href="{_css_escape_id(el.href)}"]')
        if el.tag == "input" and el.element_type:
            attr_parts.append(f'[type="{_css_escape_id(el.element_type)}"]')

        seen_parts: set[str] = set()
        dedup_parts: list[str] = []
        for p in attr_parts:
            if p not in seen_parts:
                dedup_parts.append(p)
                seen_parts.add(p)

        for size in (2, 3):
            for combo in combinations(dedup_parts, size):
                candidates.append(f"{tag}{''.join(combo)}")
                candidates.append("".join(combo))

        # Strategy 0: tag-qualified id (if initial #id was non-unique)
        if el.attr_id:
            candidates.append(f'{tag}#{_css_escape_id(el.attr_id)}')
            # id + class combination
            if el.attr_class:
                own_tokens = [
                    t for t in el.attr_class.split()
                    if t and not re.match(r'^[0-9a-f]{5,}$', t, re.I)
                ][:2]
                if own_tokens:
                    ec = "." + ".".join(own_tokens)
                    candidates.append(f'{tag}#{_css_escape_id(el.attr_id)}{ec}')

        # Strategy 0b: data-autom attribute
        if el.data_autom:
            candidates.append(
                f'{tag}[data-autom="{_css_escape_id(el.data_autom)}"]'
            )

        # Strategy 1: parent #id > base
        if el.parent_id and _is_stable_id(el.parent_id):
            candidates.append(
                f'#{_css_escape_id(el.parent_id)} > {base}'
            )

        # Strategy 1b: parent class context (before positional selectors)
        # Useful for responsive design and component variants
        if el.parent_class and el.parent_tag:
            parent_tokens = [
                t for t in el.parent_class.split()
                if t and not re.match(r'^[0-9a-f]{5,}$', t, re.I)
            ]
            if parent_tokens:
                # Try with all tokens, then reduce
                for num_tokens in range(len(parent_tokens), 0, -1):
                    tokens = parent_tokens[:num_tokens]
                    pc = "." + ".".join(tokens)
                    candidates.append(f'{el.parent_tag}{pc} > {base}')
                    # Also try descendant combinator
                    candidates.append(f'{el.parent_tag}{pc} {base}')
                    
                    # If base is just a tag, add element's own class
                    if base == tag and el.attr_class:
                        own_tokens = [
                            t for t in el.attr_class.split()
                            if t and not re.match(r'^[0-9a-f]{5,}$', t, re.I)
                        ][:2]
                        if own_tokens:
                            ec = "." + ".".join(own_tokens)
                            candidates.append(f'{el.parent_tag}{pc} > {tag}{ec}')
                            candidates.append(f'{el.parent_tag}{pc} {tag}{ec}')

        # Strategy 2: parent tag > base:nth-of-type (fallback to positional)
        if el.parent_tag and el.nth_of_type:
            candidates.append(
                f'{el.parent_tag} > {base}:nth-of-type({el.nth_of_type})'
            )

        # Strategy 3: parent #id > tag:nth-of-type
        if el.parent_id and _is_stable_id(el.parent_id) and el.nth_of_type:
            candidates.append(
                f'#{_css_escape_id(el.parent_id)} > {tag}:nth-of-type({el.nth_of_type})'
            )

        # Strategy 4: adjacent sibling (#prevId + tag)
        if el.prev_sibling_id and _is_stable_id(el.prev_sibling_id):
            candidates.append(
                f'#{_css_escape_id(el.prev_sibling_id)} + {tag}'
            )

        # Strategy 5: adjacent sibling (prevTag + base)
        if el.prev_sibling_tag:
            candidates.append(
                f'{el.prev_sibling_tag} + {base}'
            )

        # Strategy 6: general sibling ~ with nth-of-type
        if el.prev_sibling_id and _is_stable_id(el.prev_sibling_id) and el.nth_of_type:
            candidates.append(
                f'#{_css_escape_id(el.prev_sibling_id)} ~ {tag}:nth-of-type({el.nth_of_type})'
            )

        # Strategy 7: parent tag.class > tag.class (mirroring XPath parent-scoped logic)
        if el.parent_class and el.parent_tag and el.attr_class:
            parent_tokens = [
                t for t in el.parent_class.split()
                if t and not re.match(r'^[0-9a-f]{5,}$', t, re.I)
            ]
            own_tokens = [
                t for t in el.attr_class.split()
                if t and not re.match(r'^[0-9a-f]{5,}$', t, re.I)
            ]
            if parent_tokens and own_tokens:
                ptag = el.parent_tag
                pc = "." + ".".join(parent_tokens[:3])
                ec = "." + ".".join(own_tokens[:3])
                candidates.append(f'{ptag}{pc} > {tag}{ec}')
                candidates.append(f'{ptag}{pc} {tag}{ec}')

        # Strategy 8: parent class > base
        if el.parent_class:
            tokens = [
                t for t in el.parent_class.split()
                if t and not re.match(r'^[0-9a-f]{5,}$', t, re.I)
            ]
            if tokens:
                pc = "." + ".".join(tokens[:2])
                candidates.append(f'{pc} > {base}')
                if el.nth_of_type:
                    candidates.append(
                        f'{pc} > {tag}:nth-of-type({el.nth_of_type})'
                    )

        # Strategy 9: element's own class with parent class context (descendant)
        if el.attr_class:
            class_tokens = [
                t for t in el.attr_class.split()
                if t and not re.match(r'^[0-9a-f]{5,}$', t, re.I)
            ][:2]
            if class_tokens and el.parent_class:
                parent_tokens = [
                    t for t in el.parent_class.split()
                    if t and not re.match(r'^[0-9a-f]{5,}$', t, re.I)
                ][:2]
                if parent_tokens:
                    pc = "." + ".".join(parent_tokens)
                    ec = "." + ".".join(class_tokens)
                    candidates.append(f'{pc} {tag}{ec}')

        for candidate in candidates:
            try:
                count = page.evaluate(_COUNT_CSS_JS, candidate)
                if count == 1:
                    return candidate
            except Exception:
                continue

        return None

    def _refine_xpath(self, el: ScannedElement, page: Any) -> Optional[str]:
        """Try progressively more specific XPath strategies until unique."""
        tag = el.tag or "*"
        base_pred = self._base_xpath_predicate(el)
        candidates: list[str] = []

        # Priority 1: Text alone (visible text or element name)
        label_text = self._best_human_label(el)
        if self._is_clear_name(el.attr_name):
            candidates.append(f'//{tag}[@name={_xpath_escape(el.attr_name)}]')
        if label_text:
            if el.has_direct_text:
                candidates.append(f'//{tag}[text()={_xpath_escape(label_text)}]')
            candidates.append(f'//{tag}[normalize-space()={_xpath_escape(label_text)}]')
            nbsp = "\u00a0"
            candidates.append(
                f'//{tag}[contains(translate(normalize-space(), "{nbsp}", " "), '
                f'{_xpath_escape(label_text[:40])})]'
            )

        # Priority 2: Text + single attributes (if text available)
        if label_text:
            text_pred = f'text()={_xpath_escape(label_text)}' if el.has_direct_text else f'normalize-space()={_xpath_escape(label_text)}'
            
            # Text + common attributes
            if el.attr_id:
                candidates.append(f'//{tag}[{text_pred} and @id={_xpath_escape(el.attr_id)}]')
            if el.attr_class:
                class_tokens = [
                    t for t in el.attr_class.split()
                    if t and not re.match(r'^[0-9a-f]{5,}$', t, re.I)
                ][:2]
                if class_tokens:
                    cls_pred = " and ".join(f'contains(@class, {_xpath_escape(t)})' for t in class_tokens)
                    candidates.append(f'//{tag}[{text_pred} and {cls_pred}]')
            if el.role:
                candidates.append(f'//{tag}[{text_pred} and @role={_xpath_escape(el.role)}]')
            if el.data_testid:
                candidates.append(f'//{tag}[{text_pred} and @data-testid={_xpath_escape(el.data_testid)}]')
            if el.aria_label:
                candidates.append(f'//{tag}[{text_pred} and @aria-label={_xpath_escape(el.aria_label)}]')

        # Priority 3: Attributes alone (without text)
        attr_preds: list[str] = []
        if el.attr_id and _is_stable_id(el.attr_id):
            attr_preds.append(f'@id={_xpath_escape(el.attr_id)}')
        if self._is_clear_name(el.attr_name):
            attr_preds.append(f'@name={_xpath_escape(el.attr_name)}')
        if el.data_testid:
            attr_preds.append(f'@data-testid={_xpath_escape(el.data_testid)}')
        if el.data_autom:
            attr_preds.append(f'@data-autom={_xpath_escape(el.data_autom)}')
        if el.aria_label:
            cleaned_aria = self._clean_human_text(el.aria_label)
            if cleaned_aria and cleaned_aria != _normalise_text(el.aria_label):
                attr_preds.append(
                    f'starts-with(normalize-space(@aria-label), {_xpath_escape(cleaned_aria)})'
                )
            attr_preds.append(f'@aria-label={_xpath_escape(el.aria_label)}')
        if el.attr_placeholder:
            attr_preds.append(f'@placeholder={_xpath_escape(el.attr_placeholder)}')
        if el.role:
            attr_preds.append(f'@role={_xpath_escape(el.role)}')
        if el.href:
            attr_preds.append(f'@href={_xpath_escape(el.href)}')
        if el.tag == "input" and el.element_type:
            attr_preds.append(f'@type={_xpath_escape(el.element_type)}')
        if el.attr_class:
            class_tokens = [
                t for t in el.attr_class.split()
                if t and not re.match(r'^[0-9a-f]{5,}$', t, re.I)
            ][:2]
            if class_tokens:
                for token in class_tokens:
                    attr_preds.append(f'contains(@class, {_xpath_escape(token)})')

        seen_preds: set[str] = set()
        dedup_preds: list[str] = []
        for p in attr_preds:
            if p not in seen_preds:
                dedup_preds.append(p)
                seen_preds.add(p)

        # Single attributes
        for pred in dedup_preds:
            candidates.append(f'//{tag}[{pred}]')

        # Multi-attribute combinations
        for size in (2, 3):
            for combo in combinations(dedup_preds, size):
                combo_pred = " and ".join(combo)
                candidates.append(f'//{tag}[{combo_pred}]')

        # Priority 4: Parent class + text/attributes (before generic parent+text)
        # Use parent class tokens for disambiguation (e.g., responsive design variants)
        if el.parent_class:
            parent_tokens = [
                t for t in el.parent_class.split()
                if t and not re.match(r'^[0-9a-f]{5,}$', t, re.I)
            ]
            if parent_tokens and el.parent_tag:
                # Try all parent class tokens (not just first 2)
                for num_tokens in range(len(parent_tokens), 0, -1):
                    tokens = parent_tokens[:num_tokens]
                    pc = " and ".join(f'contains(@class, {_xpath_escape(t)})' for t in tokens)
                    
                    # With text
                    if label_text:
                        text_pred = f'text()={_xpath_escape(label_text)}' if el.has_direct_text else f'normalize-space()={_xpath_escape(label_text)}'
                        candidates.append(f'//{el.parent_tag}[{pc}]/{tag}[{text_pred}]')
                        candidates.append(f'//{el.parent_tag}[{pc}]//{tag}[{text_pred}]')
                    
                    # With attributes
                    if base_pred:
                        candidates.append(f'//{el.parent_tag}[{pc}]/{tag}{base_pred}')
                        candidates.append(f'//{el.parent_tag}[{pc}]//{tag}{base_pred}')

        # Priority 5: Parent + text (if text available)
        if label_text and el.parent_tag:
            text_pred = f'text()={_xpath_escape(label_text)}' if el.has_direct_text else f'normalize-space()={_xpath_escape(label_text)}'
            candidates.append(f'//{el.parent_tag}//{tag}[{text_pred}]')
            candidates.append(f'//{el.parent_tag}/{tag}[{text_pred}]')
            
            if el.parent_id and _is_stable_id(el.parent_id):
                candidates.append(f'//*[@id={_xpath_escape(el.parent_id)}]//{tag}[{text_pred}]')
                candidates.append(f'//*[@id={_xpath_escape(el.parent_id)}]/{tag}[{text_pred}]')

        # Priority 6: Parent + attributes (if no text or text+parent didn't work)
        if el.parent_id and _is_stable_id(el.parent_id):
            candidates.append(f'//*[@id={_xpath_escape(el.parent_id)}]/{tag}{base_pred}')
            if base_pred:
                candidates.append(f'//*[@id={_xpath_escape(el.parent_id)}]//{tag}{base_pred}')

        # Priority 7: Following-sibling (text-based if available)
        if label_text:
            text_pred = f'normalize-space()={_xpath_escape(label_text)}'
            if el.prev_sibling_tag and el.prev_sibling_text:
                sib_text = _normalise_text(el.prev_sibling_text, 40)
                if sib_text:
                    nbsp = "\u00a0"
                    candidates.append(
                        f'//{el.prev_sibling_tag}[contains(translate(normalize-space(), "{nbsp}", " "), {_xpath_escape(sib_text)})]/following-sibling::{tag}[{text_pred}][1]'
                    )
            if el.prev_sibling_tag and el.prev_sibling_id and _is_stable_id(el.prev_sibling_id):
                candidates.append(
                    f'//*[@id={_xpath_escape(el.prev_sibling_id)}]/following-sibling::{tag}[{text_pred}][1]'
                )

        # Priority 8: Following-sibling (attribute-based)
        if el.prev_sibling_tag and el.prev_sibling_id and _is_stable_id(el.prev_sibling_id):
            candidates.append(
                f'//*[@id={_xpath_escape(el.prev_sibling_id)}]/following-sibling::{tag}[1]'
            )
            if base_pred:
                candidates.append(
                    f'//*[@id={_xpath_escape(el.prev_sibling_id)}]/following-sibling::{tag}{base_pred}[1]'
                )

        if el.prev_sibling_tag and el.prev_sibling_text:
            text = _normalise_text(el.prev_sibling_text, 40)
            if text:
                nbsp = "\u00a0"
                candidates.append(
                    f'//{el.prev_sibling_tag}[contains(translate(normalize-space(), "{nbsp}", " "), {_xpath_escape(text)})]/following-sibling::{tag}[1]'
                )

        # Priority 9: Preceding-sibling
        if el.next_sibling_tag and el.next_sibling_id and _is_stable_id(el.next_sibling_id):
            candidates.append(
                f'//*[@id={_xpath_escape(el.next_sibling_id)}]/preceding-sibling::{tag}[1]'
            )

        # Priority 10: Parent + position (only as fallback after all other strategies exhausted)
        if el.parent_tag and el.nth_of_type:
            candidates.append(
                f'//{el.parent_tag}/{tag}{base_pred}[{el.nth_of_type}]'
            )

        if el.parent_id and _is_stable_id(el.parent_id) and el.nth_of_type:
            candidates.append(
                f'//*[@id={_xpath_escape(el.parent_id)}]/{tag}[{el.nth_of_type}]'
            )

        # Evaluate all candidates in priority order, return first unique match
        eval_target = self._resolve_eval_target(page, el.frame_index)
        for candidate in candidates:
            try:
                count, _ = self._count_xpath(eval_target, candidate)
                if count == 1:
                    return candidate
            except Exception:
                continue

        return None

    # ------------------------------------------------------------------
    # Helpers for refinement
    # ------------------------------------------------------------------

    @staticmethod
    def _base_css_attr(el: ScannedElement) -> str:
        """Build a basic CSS selector fragment from element attributes."""
        tag = el.tag or "*"
        if el.attr_id and _is_stable_id(el.attr_id):
            return f'{tag}#{_css_escape_id(el.attr_id)}'
        parts = [tag]
        if el.attr_name:
            parts.append(f'[name="{_css_escape_id(el.attr_name)}"]')
        elif el.data_testid:
            parts.append(f'[data-testid="{_css_escape_id(el.data_testid)}"]')
        elif el.data_autom:
            parts.append(f'[data-autom="{_css_escape_id(el.data_autom)}"]')
        elif el.aria_label:
            parts.append(f'[aria-label="{_css_escape_id(el.aria_label)}"]')
        elif el.attr_placeholder:
            parts.append(f'[placeholder="{_css_escape_id(el.attr_placeholder)}"]')
        elif el.element_type and el.tag == "input":
            parts.append(f'[type="{_css_escape_id(el.element_type)}"]')
        return "".join(parts)

    @staticmethod
    def _base_xpath_predicate(el: ScannedElement) -> str:
        """Build a basic XPath predicate fragment from element attributes."""
        if el.attr_id and _is_stable_id(el.attr_id):
            return f'[@id={_xpath_escape(el.attr_id)}]'
        if el.attr_name:
            return f'[@name={_xpath_escape(el.attr_name)}]'
        if el.data_testid:
            return f'[@data-testid={_xpath_escape(el.data_testid)}]'
        if el.data_autom:
            return f'[@data-autom={_xpath_escape(el.data_autom)}]'
        if el.aria_label:
            return f'[@aria-label={_xpath_escape(el.aria_label)}]'
        if el.attr_placeholder:
            return f'[@placeholder={_xpath_escape(el.attr_placeholder)}]'
        return ""

    @staticmethod
    def _regrade_quality(el: ScannedElement) -> None:
        """Re-grade selector quality after refinement.
        
        Note: If selectors are unique (count=1), they remain HIGH quality
        even if they use positional or sibling-based strategies.
        """
        logger.debug("_regrade_quality called for %s (current quality: %s)", el.element_name, el.selector_quality)
        
        # Don't downgrade if selectors are unique - uniqueness trumps strategy
        css_count = getattr(el, 'css_element_count', 0)
        xpath_count = getattr(el, 'xpath_element_count', 0)
        
        if css_count == 1 or xpath_count == 1:
            logger.debug("Skipping quality downgrade - selector is unique (CSS count=%d, XPath count=%d)", css_count, xpath_count)
            return
        
        notes: list[str] = []
        if "following-sibling" in el.xpath or "preceding-sibling" in el.xpath:
            notes.append("sibling-based XPath")
        if "nth-of-type" in el.css_selector or "nth-child" in el.css_selector:
            notes.append("position-based CSS")
        if " + " in el.css_selector or " ~ " in el.css_selector:
            notes.append("sibling-based CSS")
        if notes:
            logger.debug("Downgrading quality to MEDIUM due to: %s", "; ".join(notes))
            el.selector_quality = SelectorQuality.MEDIUM
            new_notes = "; ".join(notes) + " (refined for uniqueness)"
            if el.selector_notes:
                el.selector_notes = f"{el.selector_notes}; {new_notes}"
            else:
                el.selector_notes = new_notes
        else:
            logger.debug("No regrading needed")

    # ------------------------------------------------------------------
    # CSS generation (initial pass)
    # ------------------------------------------------------------------

    def _build_css(
        self,
        el: ScannedElement,
        seen: set[str],
    ) -> tuple[str, str, str]:
        """Returns (selector, quality, note)."""
        tag = el.tag or "*"

        # 1. Prefer element DOM name first (user-readable intent)
        if self._is_clear_name(el.attr_name):
            sel = f'{tag}[name="{_css_escape_id(el.attr_name)}"]'
            if sel not in seen:
                return sel, SelectorQuality.HIGH, "name-based"

        # 2. Prefer readable aria-label, but only use prefix match for dynamic labels
        cleaned_aria = self._clean_human_text(el.aria_label)
        if cleaned_aria:
            normalized_aria = _normalise_text(el.aria_label)
            logger.debug(
                "CSS aria-label check: cleaned='%s', normalized='%s', match=%s",
                cleaned_aria, normalized_aria, cleaned_aria == normalized_aria
            )
            if cleaned_aria == normalized_aria:
                # Exact match - aria-label is already clean and stable
                sel = f'{tag}[aria-label="{_css_escape_id(cleaned_aria)}"]'
                if sel not in seen:
                    logger.debug("CSS returning HIGH quality for exact aria-label: %s", sel)
                    return sel, SelectorQuality.HIGH, "aria-label"
            else:
                # Prefix match - aria-label contains dynamic/volatile content
                sel = f'{tag}[aria-label^="{_css_escape_id(cleaned_aria)}"]'
                if sel not in seen:
                    logger.debug("CSS returning MEDIUM quality for prefix aria-label: %s", sel)
                    return sel, SelectorQuality.MEDIUM, "human-readable aria-label prefix"

        # 3. Any id attribute — tag-qualified for specificity
        if el.attr_id:
            sel = f'{tag}#{_css_escape_id(el.attr_id)}'
            if sel not in seen:
                quality = SelectorQuality.HIGH if _is_stable_id(el.attr_id) else SelectorQuality.MEDIUM
                note = "" if _is_stable_id(el.attr_id) else "id may not be stable"
                return sel, quality, note
            logger.debug("Duplicate CSS id selector '%s'", sel)

        # 4. data-testid
        if el.data_testid:
            sel = f'[data-testid="{_css_escape_id(el.data_testid)}"]'
            if sel not in seen:
                return sel, SelectorQuality.HIGH, ""

        # 4b. data-autom / data-qa / data-cy
        if el.data_autom:
            sel = f'[data-autom="{_css_escape_id(el.data_autom)}"]'
            if sel not in seen:
                return sel, SelectorQuality.HIGH, ""

        # 5. aria-label exact fallback
        if el.aria_label:
            sel = f'{tag}[aria-label="{_css_escape_id(el.aria_label)}"]'
            if sel not in seen:
                return sel, SelectorQuality.HIGH, ""
            # non-unique aria-label
            sel = f'[aria-label="{_css_escape_id(el.aria_label)}"]'
            if sel not in seen:
                return sel, SelectorQuality.MEDIUM, "aria-label not unique"

        # 6. placeholder
        if el.attr_placeholder:
            sel = f'{tag}[placeholder="{_css_escape_id(el.attr_placeholder)}"]'
            if sel not in seen:
                return sel, SelectorQuality.MEDIUM, ""

        # 7. Parent class > tag.class (highly specific - avoids hidden duplicates)
        # Prioritize parent-scoped selectors to disambiguate elements with same attributes
        if el.parent_class and el.parent_tag and el.attr_class:
            parent_tokens = [
                t for t in el.parent_class.split()
                if t and not re.match(r'^[0-9a-f]{5,}$', t, re.I)
            ][:3]  # Use up to 3 parent class tokens
            own_tokens = [
                t for t in el.attr_class.split()
                if t and not re.match(r'^[0-9a-f]{5,}$', t, re.I)
            ][:3]  # Use up to 3 own class tokens
            if parent_tokens and own_tokens:
                ptag = el.parent_tag
                pc = "." + ".".join(parent_tokens)
                ec = "." + ".".join(own_tokens)
                # 7a: parent tag.class > tag.class (child combinator - most specific)
                sel = f'{ptag}{pc} > {tag}{ec}'
                if sel not in seen:
                    return sel, SelectorQuality.MEDIUM, "parent-scoped class-based"
                # 7b: parent tag.class tag.class (descendant combinator)
                sel = f'{ptag}{pc} {tag}{ec}'
                if sel not in seen:
                    return sel, SelectorQuality.MEDIUM, "parent-scoped class-based"

        # 8. Parent class > tag (even without own class)
        if el.parent_class and el.parent_tag:
            parent_tokens = [
                t for t in el.parent_class.split()
                if t and not re.match(r'^[0-9a-f]{5,}$', t, re.I)
            ][:2]
            if parent_tokens:
                ptag = el.parent_tag
                pc = "." + ".".join(parent_tokens)
                sel = f'{ptag}{pc} > {tag}'
                if sel not in seen:
                    return sel, SelectorQuality.MEDIUM, "parent-scoped"

        # 9. Parent ID context — parent #id > base
        if el.parent_id and _is_stable_id(el.parent_id):
            base = self._base_css_attr(el)
            sel = f'#{_css_escape_id(el.parent_id)} > {base}'
            if sel not in seen:
                return sel, SelectorQuality.MEDIUM, "parent-scoped"

        # 10. Adjacent sibling — #prevId + tag
        if el.prev_sibling_id and _is_stable_id(el.prev_sibling_id):
            sel = f'#{_css_escape_id(el.prev_sibling_id)} + {tag}'
            if sel not in seen:
                return sel, SelectorQuality.MEDIUM, "sibling-based"

        # 11. Tag + class combination (fallback - less specific)
        if el.attr_class:
            tokens = [
                t for t in el.attr_class.split()
                if t and not re.match(r'^[0-9a-f]{5,}$', t, re.I)
            ]
            if tokens:
                class_part = "." + ".".join(tokens[:3])
                sel = f'{tag}{class_part}'
                if sel not in seen:
                    return sel, SelectorQuality.MEDIUM, "class-based"

        # 12. tag + type for inputs
        if el.tag == "input" and el.element_type:
            sel = f'input[type="{_css_escape_id(el.element_type)}"]'
            if sel not in seen:
                return sel, SelectorQuality.LOW, "non-unique input type"

        # 13. Nth-child positional fallback
        sel = self._css_nth_fallback(el)
        note = "positional selector — may be brittle"
        return sel, SelectorQuality.LOW, note

    # ------------------------------------------------------------------
    # XPath generation (initial pass)
    # ------------------------------------------------------------------

    def _build_xpath(
        self,
        el: ScannedElement,
        seen: set[str],
    ) -> tuple[str, str, str]:
        """Returns (xpath, quality, note).  Always relative (starts with //)."""
        tag = el.tag or "*"

        # 1. Prefer DOM @name first when meaningful.
        if self._is_clear_name(el.attr_name):
            xp = f'//{tag}[@name={_xpath_escape(el.attr_name)}]'
            if xp not in seen:
                return xp, SelectorQuality.HIGH, "name-based"

        # 2. Prefer visible text / clear user-facing label as the first XPath strategy.
        label_text = self._best_human_label(el)
        if label_text:
            # PRIORITY 1: Simple text match (simplest and most readable)
            # Try direct text match first
            if el.has_direct_text:
                xp = f'//{tag}[text()={_xpath_escape(label_text)}]'
                if xp not in seen:
                    return xp, SelectorQuality.MEDIUM, "text-based"

            # Try normalize-space (handles whitespace variations)
            xp = f'//{tag}[normalize-space()={_xpath_escape(label_text)}]'
            if xp not in seen:
                return xp, SelectorQuality.MEDIUM, "text-based"

            # PRIORITY 2: Parent-scoped text match (for disambiguation when simple match is not unique)
            # Try parent with class context (e.g., footer-locale-small vs footer-locale-large)
            if el.parent_class and el.parent_tag:
                parent_tokens = [
                    t for t in el.parent_class.split()
                    if t and not re.match(r'^[0-9a-f]{5,}$', t, re.I)
                ][:3]  # Use up to 3 class tokens for specificity
                if parent_tokens:
                    ptag = el.parent_tag
                    # Use contains() for each class token - more flexible than exact match
                    pc = " and ".join(f'contains(@class, {_xpath_escape(t)})' for t in parent_tokens)
                    
                    # Try child combinator (/), not descendant (//)
                    if el.has_direct_text:
                        xp = f'//{ptag}[{pc}]/{tag}[text()={_xpath_escape(label_text)}]'
                        if xp not in seen:
                            return xp, SelectorQuality.MEDIUM, "parent-scoped text-based"
                    
                    xp = f'//{ptag}[{pc}]/{tag}[normalize-space()={_xpath_escape(label_text)}]'
                    if xp not in seen:
                        return xp, SelectorQuality.MEDIUM, "parent-scoped text-based"
                    
                    # Try descendant combinator (//) if child didn't work
                    xp = f'//{ptag}[{pc}]//{tag}[normalize-space()={_xpath_escape(label_text)}]'
                    if xp not in seen:
                        return xp, SelectorQuality.MEDIUM, "parent-scoped text-based"

            # PRIORITY 3: Parent with ID (if available)
            if el.parent_id and _is_stable_id(el.parent_id):
                if el.has_direct_text:
                    xp = f'//*[@id={_xpath_escape(el.parent_id)}]/{tag}[text()={_xpath_escape(label_text)}]'
                    if xp not in seen:
                        return xp, SelectorQuality.MEDIUM, "parent-id-scoped text-based"
                
                xp = f'//*[@id={_xpath_escape(el.parent_id)}]/{tag}[normalize-space()={_xpath_escape(label_text)}]'
                if xp not in seen:
                    return xp, SelectorQuality.MEDIUM, "parent-id-scoped text-based"

            # PRIORITY 4: Partial text match with translate (handles special chars)
            nbsp = "\u00a0"
            xp = (
                f'//{tag}[contains(translate(normalize-space(), "{nbsp}", " "), '
                f'{_xpath_escape(label_text[:40])})]'
            )
            if xp not in seen:
                return xp, SelectorQuality.MEDIUM, "partial text-based"

        # 3. data-testid
        if el.data_testid:
            xp = f'//*[@data-testid={_xpath_escape(el.data_testid)}]'
            if xp not in seen:
                return xp, SelectorQuality.HIGH, ""

        # 3b. data-autom / data-qa / data-cy
        if el.data_autom:
            xp = f'//*[@data-autom={_xpath_escape(el.data_autom)}]'
            if xp not in seen:
                return xp, SelectorQuality.HIGH, ""

        # 4. Any id attribute — tag-qualified for specificity
        if el.attr_id:
            xp = f'//{tag}[@id={_xpath_escape(el.attr_id)}]'
            if xp not in seen:
                quality = SelectorQuality.HIGH if _is_stable_id(el.attr_id) else SelectorQuality.MEDIUM
                note = "" if _is_stable_id(el.attr_id) else "id may not be stable"
                return xp, quality, note

        # 5. aria-label (prefer a readable prefix when label includes volatile row/column text)
        if el.aria_label:
            cleaned_aria = self._clean_human_text(el.aria_label)
            normalized_aria = _normalise_text(el.aria_label)
            logger.debug(
                "XPath aria-label check: cleaned='%s', normalized='%s', match=%s",
                cleaned_aria, normalized_aria, cleaned_aria != normalized_aria
            )
            if cleaned_aria and cleaned_aria != normalized_aria:
                xp = f'//{tag}[starts-with(normalize-space(@aria-label), {_xpath_escape(cleaned_aria)})]'
                if xp not in seen:
                    logger.debug("XPath returning MEDIUM quality for prefix aria-label: %s", xp)
                    return xp, SelectorQuality.MEDIUM, "human-readable aria-label"

            xp = f'//{tag}[@aria-label={_xpath_escape(el.aria_label)}]'
            if xp not in seen:
                logger.debug("XPath returning HIGH quality for exact aria-label: %s", xp)
                return xp, SelectorQuality.HIGH, ""

        # 6. label text association
        if el.label_text:
            text = _normalise_text(el.label_text)
            if text:
                xp = (
                    f'//{tag}[@id=//label[normalize-space()='
                    f'{_xpath_escape(text)}]/@for]'
                )
                if xp not in seen:
                    return xp, SelectorQuality.MEDIUM, "label-based"

        # 7. name attribute (fallback for non-clear names)
        if el.attr_name:
            xp = f'//{tag}[@name={_xpath_escape(el.attr_name)}]'
            if xp not in seen:
                return xp, SelectorQuality.MEDIUM, ""

        # 8. placeholder
        if el.attr_placeholder:
            xp = f'//{tag}[@placeholder={_xpath_escape(el.attr_placeholder)}]'
            if xp not in seen:
                return xp, SelectorQuality.MEDIUM, ""

        # 9. tag + class (important for SVG and graphical elements without text)
        if el.attr_class:
            class_tokens = [
                t for t in el.attr_class.split()
                if t and not re.match(r'^[0-9a-f]{5,}$', t, re.I)
            ]
            if class_tokens:
                # Try single class first (most specific)
                if len(class_tokens) == 1:
                    xp = f'//{tag}[contains(@class, {_xpath_escape(class_tokens[0])})]'
                    if xp not in seen:
                        return xp, SelectorQuality.MEDIUM, "class-based"
                
                # Try with parent context for disambiguation
                if el.parent_tag and el.parent_class:
                    parent_tokens = [
                        t for t in el.parent_class.split()
                        if t and not re.match(r'^[0-9a-f]{5,}$', t, re.I)
                    ][:2]
                    if parent_tokens:
                        ptag = el.parent_tag
                        pc = " and ".join(f'contains(@class, {_xpath_escape(t)})' for t in parent_tokens)
                        cls = " and ".join(f'contains(@class, {_xpath_escape(t)})' for t in class_tokens[:2])
                        xp = f'//{ptag}[{pc}]/{tag}[{cls}]'
                        if xp not in seen:
                            return xp, SelectorQuality.MEDIUM, "parent-class+class-based"
                
                # Multiple classes
                cls_pred = " and ".join(f'contains(@class, {_xpath_escape(t)})' for t in class_tokens[:3])
                xp = f'//{tag}[{cls_pred}]'
                if xp not in seen:
                    return xp, SelectorQuality.MEDIUM, "multi-class-based"

        # 8. Visible text — translate + contains fallback for any element
        if el.visible_text:
            text = _normalise_text(el.visible_text)
            nbsp = "\u00a0"
            if text and len(text) < 80:
                # 8a. For elements without direct text, combine text with class
                # to avoid matching ancestor wrappers that share normalize-space()
                if not el.has_direct_text and el.attr_class:
                    class_tokens = [
                        t for t in el.attr_class.split()
                        if t and not re.match(r'^[0-9a-f]{5,}$', t, re.I)
                    ][:2]
                    if class_tokens:
                        cls_pred = " and ".join(
                            f'contains(@class, {_xpath_escape(t)})'
                            for t in class_tokens
                        )
                        xp = f'//{tag}[{cls_pred} and normalize-space()={_xpath_escape(text)}]'
                        if xp not in seen:
                            return xp, SelectorQuality.MEDIUM, "text+class-based"

                # 8b. Plain normalize-space text match
                xp = f'//{tag}[translate(normalize-space(), "{nbsp}", " ")={_xpath_escape(text)}]'
                if xp not in seen:
                    return xp, SelectorQuality.MEDIUM, "text-based"
                xp = f'//{tag}[contains(translate(normalize-space(), "{nbsp}", " "), {_xpath_escape(text[:40])})]'
                if xp not in seen:
                    return xp, SelectorQuality.MEDIUM, "partial text-based"

        # 9. Parent id context
        if el.parent_id and _is_stable_id(el.parent_id):
            base = self._base_xpath_predicate(el)
            xp = f'//*[@id={_xpath_escape(el.parent_id)}]/{tag}{base}'
            if xp not in seen:
                return xp, SelectorQuality.MEDIUM, "parent-scoped"

        # 10. Following-sibling from previous sibling with id
        if el.prev_sibling_id and _is_stable_id(el.prev_sibling_id):
            xp = f'//*[@id={_xpath_escape(el.prev_sibling_id)}]/following-sibling::{tag}[1]'
            if xp not in seen:
                return xp, SelectorQuality.MEDIUM, "sibling-based"

        # 10. role — always combine with other attributes for uniqueness
        if el.role:
            # 10a: role + class attributes
            if el.attr_class:
                class_tokens = [
                    t for t in el.attr_class.split()
                    if t and not re.match(r'^[0-9a-f]{5,}$', t, re.I)
                ][:3]
                if class_tokens:
                    cls_pred = " and ".join(
                        f'contains(@class, {_xpath_escape(t)})'
                        for t in class_tokens
                    )
                    xp = f'//{tag}[@role={_xpath_escape(el.role)} and {cls_pred}]'
                    if xp not in seen:
                        return xp, SelectorQuality.MEDIUM, "role+class-based"

            # 10b: role + visible text
            if el.visible_text:
                text = _normalise_text(el.visible_text)
                if text:
                    text_pred = f'text()={_xpath_escape(text)}' if el.has_direct_text and len(text) < 80 else f'contains(normalize-space(), {_xpath_escape(text[:40])})'
                    xp = f'//{tag}[@role={_xpath_escape(el.role)} and {text_pred}]'
                    if xp not in seen:
                        return xp, SelectorQuality.MEDIUM, "role+text-based"

            # 10c: parent class > role
            if el.parent_class and el.parent_tag:
                parent_tokens = [
                    t for t in el.parent_class.split()
                    if t and not re.match(r'^[0-9a-f]{5,}$', t, re.I)
                ]
                if parent_tokens:
                    ptag = el.parent_tag
                    pc = " and ".join(
                        f'contains(@class, {_xpath_escape(t)})'
                        for t in parent_tokens[:2]
                    )
                    xp = f'//{ptag}[{pc}]/{tag}[@role={_xpath_escape(el.role)}]'
                    if xp not in seen:
                        return xp, SelectorQuality.MEDIUM, "parent-scoped role"

            # 10d: bare role — last resort
            xp = f'//{tag}[@role={_xpath_escape(el.role)}]'
            if xp not in seen:
                return xp, SelectorQuality.LOW, "role-only — likely non-unique"

        # 10b. Ancestor heading context — use nearby heading to scope
        if el.nearby_heading and el.nearby_heading_tag:
            heading_text = _normalise_text(el.nearby_heading, 40)
            ht = el.nearby_heading_tag
            if heading_text:
                nbsp = "\u00a0"
                translate_expr = f'translate(., "{nbsp}", " ")'
                class_tokens = [
                    t for t in (el.attr_class or "").split()
                    if t and not re.match(r'^[0-9a-f]{{5,}}$', t, re.I)
                ][:2]
                if class_tokens:
                    cls_pred = " and ".join(
                        f'contains(@class, {_xpath_escape(t)})'
                        for t in class_tokens
                    )
                    xp = (
                        f'//*[.//{ht}[contains({translate_expr}, '
                        f'{_xpath_escape(heading_text)})]]//{tag}[{cls_pred}]'
                    )
                else:
                    base_pred = self._base_xpath_predicate(el)
                    xp = (
                        f'//*[.//{ht}[contains({translate_expr}, '
                        f'{_xpath_escape(heading_text)})]]//{tag}{base_pred}'
                    )
                if xp not in seen:
                    return xp, SelectorQuality.MEDIUM, "ancestor-heading scoped"

        # 11. tag + type for inputs
        if el.tag == "input" and el.element_type:
            xp = f'//input[@type={_xpath_escape(el.element_type)}]'
            if xp not in seen:
                return xp, SelectorQuality.LOW, "type-only — likely non-unique"

        # 12. Positional fallback (always relative)
        xp = f'(//{tag})[{el.element_index + 1}]'
        return xp, SelectorQuality.LOW, "positional fallback — brittle"

    # ------------------------------------------------------------------
    # Positional CSS fallback
    # ------------------------------------------------------------------

    @staticmethod
    def _css_nth_fallback(el: ScannedElement) -> str:
        """
        Build a somewhat-stable CSS selector using tag + class fragment
        and nth-of-type if available.
        """
        tag = el.tag or "*"
        class_part = ""
        if el.attr_class:
            # Take first two non-empty class tokens to reduce noise
            tokens = [
                t for t in el.attr_class.split()
                if t and not re.match(r'^[0-9a-f]{5,}$', t, re.I)
            ]
            if tokens:
                class_part = "." + ".".join(tokens[:2])

        nth = el.nth_of_type if el.nth_of_type else el.element_index + 1
        index_part = f":nth-of-type({nth})"
        return f"{tag}{class_part}{index_part}"
