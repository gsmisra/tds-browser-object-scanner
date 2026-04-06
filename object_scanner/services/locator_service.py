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
from typing import Any, Optional

from models.element_model import ScannedElement, SelectorQuality

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
    try { return document.querySelectorAll(selector).length; } catch(e) { return -1; }
}
"""

_COUNT_XPATH_JS = """
(selector) => {
    try {
        const r = document.evaluate(selector, document, null, 7, null);
        return r.snapshotLength;
    } catch(e) { return -1; }
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
            css, css_quality, css_note = self._build_css(el, seen_css)
            xpath, xp_quality, xp_note = self._build_xpath(el, seen_xpath)

            el.css_selector = css
            el.xpath = xpath

            # Overall quality = worst of the two
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

        selectors = [
            {"css": el.css_selector, "xpath": el.xpath, "id": el.element_id}
            for el in elements
        ]

        try:
            results = page.evaluate(_VALIDATE_BATCH_JS, selectors)
        except Exception as exc:
            logger.warning("Batch selector validation failed: %s", exc)
            return

        el_map = {el.element_id: el for el in elements}

        for result in results:
            el = el_map.get(result.get("id"))
            if not el:
                continue

            css_count = result.get("css_count", 0)
            xpath_count = result.get("xpath_count", 0)
            refined = False

            if css_count != 1:
                new_css = self._refine_css(el, page)
                if new_css:
                    el.css_selector = new_css
                    refined = True

            if xpath_count != 1:
                new_xpath = self._refine_xpath(el, page)
                if new_xpath:
                    el.xpath = new_xpath
                    refined = True

            if refined:
                self._regrade_quality(el)

    def _refine_css(self, el: ScannedElement, page: Any) -> Optional[str]:
        """Try progressively more specific CSS strategies until unique."""
        tag = el.tag or "*"
        candidates: list[str] = []

        base = self._base_css_attr(el)

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

        # Strategy 2: parent tag > base:nth-of-type
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

        # Strategy 0: id + visible text (for non-unique id selectors)
        if el.attr_id and el.visible_text:
            text = _normalise_text(el.visible_text)
            if text and len(text) < 80:
                text_pred = f'text()={_xpath_escape(text)}' if el.has_direct_text else f'normalize-space()={_xpath_escape(text)}'
                candidates.append(
                    f'//{tag}[@id={_xpath_escape(el.attr_id)} and {text_pred}]'
                )

        # Strategy 0b: data-autom attribute
        if el.data_autom:
            candidates.append(
                f'//{tag}[@data-autom={_xpath_escape(el.data_autom)}]'
            )

        # Strategy 1: parent id context
        if el.parent_id and _is_stable_id(el.parent_id):
            candidates.append(
                f'//*[@id={_xpath_escape(el.parent_id)}]/{tag}{base_pred}'
            )

        # Strategy 2: parent tag + position
        if el.parent_tag and el.nth_of_type:
            candidates.append(
                f'//{el.parent_tag}/{tag}{base_pred}[{el.nth_of_type}]'
            )

        # Strategy 3: following-sibling from prev sibling with id
        if el.prev_sibling_tag and el.prev_sibling_id and _is_stable_id(el.prev_sibling_id):
            candidates.append(
                f'//*[@id={_xpath_escape(el.prev_sibling_id)}]/following-sibling::{tag}[1]'
            )

        # Strategy 4: following-sibling from prev sibling with text
        if el.prev_sibling_tag and el.prev_sibling_text:
            text = _normalise_text(el.prev_sibling_text, 40)
            if text:
                nbsp = "\u00a0"
                candidates.append(
                    f'//{el.prev_sibling_tag}[contains(translate(normalize-space(), "{nbsp}", " "), {_xpath_escape(text)})]/following-sibling::{tag}[1]'
                )

        # Strategy 5: preceding-sibling from next sibling with id
        if el.next_sibling_tag and el.next_sibling_id and _is_stable_id(el.next_sibling_id):
            candidates.append(
                f'//*[@id={_xpath_escape(el.next_sibling_id)}]/preceding-sibling::{tag}[1]'
            )

        # Strategy 6: parent id + nth child
        if el.parent_id and _is_stable_id(el.parent_id) and el.nth_of_type:
            candidates.append(
                f'//*[@id={_xpath_escape(el.parent_id)}]/{tag}[{el.nth_of_type}]'
            )

        # Strategy 7: parent class context
        if el.parent_class:
            tokens = [
                t for t in el.parent_class.split()
                if t and not re.match(r'^[0-9a-f]{5,}$', t, re.I)
            ]
            if tokens:
                cls = tokens[0]
                candidates.append(
                    f'//*[contains(@class, {_xpath_escape(cls)})]/{tag}{base_pred}'
                )

        # Strategy 8: ancestor heading context
        if el.nearby_heading and el.nearby_heading_tag:
            heading_text = _normalise_text(el.nearby_heading, 40)
            ht = el.nearby_heading_tag
            if heading_text:
                nbsp = "\u00a0"
                translate_expr = f'translate(., "{nbsp}", " ")'
                # 8a: with class predicate
                class_tokens = [
                    t for t in (el.attr_class or "").split()
                    if t and not re.match(r'^[0-9a-f]{5,}$', t, re.I)
                ][:2]
                if class_tokens:
                    cls_pred = " and ".join(
                        f'contains(@class, {_xpath_escape(t)})'
                        for t in class_tokens
                    )
                    candidates.append(
                        f'//*[.//{ht}[contains({translate_expr}, '
                        f'{_xpath_escape(heading_text)})]]//{tag}[{cls_pred}]'
                    )
                # 8b: with base predicate
                if base_pred:
                    candidates.append(
                        f'//*[.//{ht}[contains({translate_expr}, '
                        f'{_xpath_escape(heading_text)})]]//{tag}{base_pred}'
                    )
                # 8c: just tag (if element is specific enough)
                if tag not in ("div", "span", "*"):
                    candidates.append(
                        f'//*[.//{ht}[contains({translate_expr}, '
                        f'{_xpath_escape(heading_text)})]]//{tag}'
                    )

        # Strategy 9: text combined with class attributes
        if el.visible_text and el.attr_class:
            text = _normalise_text(el.visible_text)
            if text and len(text) < 80:
                class_tokens = [
                    t for t in el.attr_class.split()
                    if t and not re.match(r'^[0-9a-f]{5,}$', t, re.I)
                ][:3]
                if class_tokens:
                    cls_pred = " and ".join(
                        f'contains(@class, {_xpath_escape(t)})'
                        for t in class_tokens
                    )
                    candidates.append(
                        f'//{tag}[{cls_pred} and normalize-space()={_xpath_escape(text)}]'
                    )

        # Strategy 10: parent class + text
        if el.visible_text and el.parent_class and el.parent_tag:
            text = _normalise_text(el.visible_text)
            if text and len(text) < 80:
                parent_tokens = [
                    t for t in el.parent_class.split()
                    if t and not re.match(r'^[0-9a-f]{5,}$', t, re.I)
                ]
                ptag = el.parent_tag
                if parent_tokens:
                    # 10a: parent tag + exact full class + text()
                    full_cls = " ".join(parent_tokens)
                    text_pred = f'text()={_xpath_escape(text)}' if el.has_direct_text else f'normalize-space()={_xpath_escape(text)}'
                    candidates.append(
                        f'//{ptag}[@class={_xpath_escape(full_cls)}]/{tag}[{text_pred}]'
                    )
                    # 10b: parent tag + contains first class + text()
                    candidates.append(
                        f'//{ptag}[contains(@class, {_xpath_escape(parent_tokens[0])})]/{tag}[{text_pred}]'
                    )
                    # 10c: any parent with contains class + normalize-space
                    candidates.append(
                        f'//*[contains(@class, {_xpath_escape(parent_tokens[0])})]/'
                        f'{tag}[normalize-space()={_xpath_escape(text)}]'
                    )

        # Strategy 11: role + class / role + text / parent > role
        if el.role:
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
                    candidates.append(
                        f'//{tag}[@role={_xpath_escape(el.role)} and {cls_pred}]'
                    )
            if el.visible_text:
                text = _normalise_text(el.visible_text)
                if text:
                    text_pred = f'text()={_xpath_escape(text)}' if el.has_direct_text and len(text) < 80 else f'contains(normalize-space(), {_xpath_escape(text[:40])})'
                    candidates.append(
                        f'//{tag}[@role={_xpath_escape(el.role)} and {text_pred}]'
                    )
            if el.parent_class and el.parent_tag:
                parent_tokens = [
                    t for t in el.parent_class.split()
                    if t and not re.match(r'^[0-9a-f]{5,}$', t, re.I)
                ][:2]
                if parent_tokens:
                    pc = " and ".join(
                        f'contains(@class, {_xpath_escape(t)})'
                        for t in parent_tokens
                    )
                    candidates.append(
                        f'//{el.parent_tag}[{pc}]/{tag}[@role={_xpath_escape(el.role)}]'
                    )

        for candidate in candidates:
            try:
                count = page.evaluate(_COUNT_XPATH_JS, candidate)
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
        """Re-grade selector quality after refinement."""
        notes: list[str] = []
        if "following-sibling" in el.xpath or "preceding-sibling" in el.xpath:
            notes.append("sibling-based XPath")
        if "nth-of-type" in el.css_selector or "nth-child" in el.css_selector:
            notes.append("position-based CSS")
        if " + " in el.css_selector or " ~ " in el.css_selector:
            notes.append("sibling-based CSS")
        if notes:
            el.selector_quality = SelectorQuality.MEDIUM
            el.selector_notes = "; ".join(notes) + " (refined for uniqueness)"

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

        # 1. Any id attribute — tag-qualified for specificity
        if el.attr_id:
            sel = f'{tag}#{_css_escape_id(el.attr_id)}'
            if sel not in seen:
                quality = SelectorQuality.HIGH if _is_stable_id(el.attr_id) else SelectorQuality.MEDIUM
                note = "" if _is_stable_id(el.attr_id) else "id may not be stable"
                return sel, quality, note
            logger.debug("Duplicate CSS id selector '%s'", sel)

        # 2. data-testid
        if el.data_testid:
            sel = f'[data-testid="{_css_escape_id(el.data_testid)}"]'
            if sel not in seen:
                return sel, SelectorQuality.HIGH, ""

        # 2b. data-autom / data-qa / data-cy
        if el.data_autom:
            sel = f'[data-autom="{_css_escape_id(el.data_autom)}"]'
            if sel not in seen:
                return sel, SelectorQuality.HIGH, ""

        # 3. aria-label
        if el.aria_label:
            sel = f'{tag}[aria-label="{_css_escape_id(el.aria_label)}"]'
            if sel not in seen:
                return sel, SelectorQuality.HIGH, ""
            # non-unique aria-label
            sel = f'[aria-label="{_css_escape_id(el.aria_label)}"]'
            if sel not in seen:
                return sel, SelectorQuality.MEDIUM, "aria-label not unique"

        # 4. name attribute (useful for form fields)
        if el.attr_name and tag in ("input", "select", "textarea", "button"):
            sel = f'{tag}[name="{_css_escape_id(el.attr_name)}"]'
            if sel not in seen:
                return sel, SelectorQuality.MEDIUM, ""

        # 5. placeholder
        if el.attr_placeholder:
            sel = f'{tag}[placeholder="{_css_escape_id(el.attr_placeholder)}"]'
            if sel not in seen:
                return sel, SelectorQuality.MEDIUM, ""

        # (step 6 removed — id handled in step 1 for all ids)

        # 7. Parent context — parent #id > base
        if el.parent_id and _is_stable_id(el.parent_id):
            base = self._base_css_attr(el)
            sel = f'#{_css_escape_id(el.parent_id)} > {base}'
            if sel not in seen:
                return sel, SelectorQuality.MEDIUM, "parent-scoped"

        # 8. Adjacent sibling — #prevId + tag
        if el.prev_sibling_id and _is_stable_id(el.prev_sibling_id):
            sel = f'#{_css_escape_id(el.prev_sibling_id)} + {tag}'
            if sel not in seen:
                return sel, SelectorQuality.MEDIUM, "sibling-based"

        # 9. Parent class > tag + own class (mirrors XPath parent-scoped text logic)
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
                # 9a: parent tag.class > tag.class
                sel = f'{ptag}{pc} > {tag}{ec}'
                if sel not in seen:
                    return sel, SelectorQuality.MEDIUM, "parent-scoped class-based"
                # 9b: parent tag.class tag.class (descendant)
                sel = f'{ptag}{pc} {tag}{ec}'
                if sel not in seen:
                    return sel, SelectorQuality.MEDIUM, "parent-scoped class-based"

        # 10. Tag + class combination
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

        # 11. tag + type for inputs
        if el.tag == "input" and el.element_type:
            sel = f'input[type="{_css_escape_id(el.element_type)}"]'
            if sel not in seen:
                return sel, SelectorQuality.LOW, "non-unique input type"

        # 11. Nth-child positional fallback
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

        # 1. Any id attribute — tag-qualified for specificity
        if el.attr_id:
            xp = f'//{tag}[@id={_xpath_escape(el.attr_id)}]'
            if xp not in seen:
                quality = SelectorQuality.HIGH if _is_stable_id(el.attr_id) else SelectorQuality.MEDIUM
                note = "" if _is_stable_id(el.attr_id) else "id may not be stable"
                return xp, quality, note

        # 2. data-testid
        if el.data_testid:
            xp = f'//*[@data-testid={_xpath_escape(el.data_testid)}]'
            if xp not in seen:
                return xp, SelectorQuality.HIGH, ""

        # 2b. data-autom / data-qa / data-cy
        if el.data_autom:
            xp = f'//*[@data-autom={_xpath_escape(el.data_autom)}]'
            if xp not in seen:
                return xp, SelectorQuality.HIGH, ""

        # 3. aria-label
        if el.aria_label:
            xp = f'//{tag}[@aria-label={_xpath_escape(el.aria_label)}]'
            if xp not in seen:
                return xp, SelectorQuality.HIGH, ""

        # 3b. Clear visible text — direct text() match
        # Only use text() when the element owns its text directly (not from descendants)
        if el.visible_text and el.has_direct_text:
            text = _normalise_text(el.visible_text)
            if text and len(text) < 80:
                # Prefer parent-scoped text() when parent class is available
                # (plain text() is often non-unique for common labels)
                if el.parent_class and el.parent_tag:
                    parent_tokens = [
                        t for t in el.parent_class.split()
                        if t and not re.match(r'^[0-9a-f]{5,}$', t, re.I)
                    ]
                    if parent_tokens:
                        full_cls = " ".join(parent_tokens)
                        ptag = el.parent_tag
                        xp = f'//{ptag}[@class={_xpath_escape(full_cls)}]/{tag}[text()={_xpath_escape(text)}]'
                        if xp not in seen:
                            return xp, SelectorQuality.MEDIUM, "parent-scoped text-based"

                # Plain text() match
                xp = f'//{tag}[text()={_xpath_escape(text)}]'
                if xp not in seen:
                    return xp, SelectorQuality.MEDIUM, "text-based"
                # Fallback: normalize-space for whitespace tolerance
                xp = f'//{tag}[normalize-space()={_xpath_escape(text)}]'
                if xp not in seen:
                    return xp, SelectorQuality.MEDIUM, "text-based"

        # 4. label text association
        if el.label_text:
            text = _normalise_text(el.label_text)
            if text:
                xp = (
                    f'//{tag}[@id=//label[normalize-space()='
                    f'{_xpath_escape(text)}]/@for]'
                )
                if xp not in seen:
                    return xp, SelectorQuality.MEDIUM, "label-based"

        # 5. name attribute
        if el.attr_name:
            xp = f'//{tag}[@name={_xpath_escape(el.attr_name)}]'
            if xp not in seen:
                return xp, SelectorQuality.MEDIUM, ""

        # 6. placeholder
        if el.attr_placeholder:
            xp = f'//{tag}[@placeholder={_xpath_escape(el.attr_placeholder)}]'
            if xp not in seen:
                return xp, SelectorQuality.MEDIUM, ""

        # 7. Visible text — translate + contains fallback for any element
        if el.visible_text:
            text = _normalise_text(el.visible_text)
            nbsp = "\u00a0"
            if text and len(text) < 80:
                # 7a. For elements without direct text, combine text with class
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

                # 7b. Plain normalize-space text match
                xp = f'//{tag}[translate(normalize-space(), "{nbsp}", " ")={_xpath_escape(text)}]'
                if xp not in seen:
                    return xp, SelectorQuality.MEDIUM, "text-based"
                xp = f'//{tag}[contains(translate(normalize-space(), "{nbsp}", " "), {_xpath_escape(text[:40])})]'
                if xp not in seen:
                    return xp, SelectorQuality.MEDIUM, "partial text-based"

        # 8. Parent id context
        if el.parent_id and _is_stable_id(el.parent_id):
            base = self._base_xpath_predicate(el)
            xp = f'//*[@id={_xpath_escape(el.parent_id)}]/{tag}{base}'
            if xp not in seen:
                return xp, SelectorQuality.MEDIUM, "parent-scoped"

        # 9. Following-sibling from previous sibling with id
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
