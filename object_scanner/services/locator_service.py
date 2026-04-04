"""
LocatorService — deterministic, rule-based CSS and XPath generator.

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
from typing import Optional

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
    """Collapse whitespace and truncate for use in selectors."""
    return " ".join(text.split())[:max_len]


# ---------------------------------------------------------------------------
# LocatorService
# ---------------------------------------------------------------------------

class LocatorService:
    """
    Generates CSS and XPath locators for each ScannedElement.
    Call ``decorate_elements`` with the full list so that uniqueness
    checks operate across the whole page batch.
    """

    def decorate_elements(self, elements: list[ScannedElement]) -> None:
        """
        In-place: generate css_selector, xpath, selector_quality, and
        selector_notes for every element in the list.
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

    # ------------------------------------------------------------------
    # CSS generation
    # ------------------------------------------------------------------

    def _build_css(
        self,
        el: ScannedElement,
        seen: set[str],
    ) -> tuple[str, str, str]:
        """Returns (selector, quality, note)."""
        tag = el.tag or "*"

        # 1. Unique stable id
        if el.attr_id and _is_stable_id(el.attr_id):
            sel = f'#{el.attr_id}'
            if sel not in seen:
                return sel, SelectorQuality.HIGH, ""
            # id exists but is not unique in this scan — warn and fall through
            logger.debug("Duplicate CSS id selector '%s'", sel)

        # 2. data-testid
        if el.data_testid:
            sel = f'[data-testid="{_css_escape_id(el.data_testid)}"]'
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

        # 6. id + tag even if not stable (better than nothing)
        if el.attr_id:
            sel = f'{tag}#{el.attr_id}'
            if sel not in seen:
                return sel, SelectorQuality.MEDIUM, "id may not be stable"

        # 7. Visible text for buttons/links (limited, escape issues)
        if el.tag in ("button", "a") and el.visible_text:
            text = _normalise_text(el.visible_text)
            if text and len(text) < 60 and '"' not in text:
                # No CSS text-content selector; best we can do is type + class
                pass  # fall through — CSS has no :has-text, use XPath for this

        # 8. tag + type for inputs
        if el.tag == "input" and el.element_type:
            sel = f'input[type="{_css_escape_id(el.element_type)}"]'
            if sel not in seen:
                return sel, SelectorQuality.LOW, "non-unique input type"

        # 9. Nth-child positional fallback
        sel = self._css_nth_fallback(el)
        note = "positional selector — may be brittle"
        return sel, SelectorQuality.LOW, note

    # ------------------------------------------------------------------
    # XPath generation
    # ------------------------------------------------------------------

    def _build_xpath(
        self,
        el: ScannedElement,
        seen: set[str],
    ) -> tuple[str, str, str]:
        """Returns (xpath, quality, note)."""
        tag = el.tag or "*"

        # 1. Unique stable id
        if el.attr_id and _is_stable_id(el.attr_id):
            xp = f'//*[@id={_xpath_escape(el.attr_id)}]'
            if xp not in seen:
                return xp, SelectorQuality.HIGH, ""

        # 2. data-testid
        if el.data_testid:
            xp = f'//*[@data-testid={_xpath_escape(el.data_testid)}]'
            if xp not in seen:
                return xp, SelectorQuality.HIGH, ""

        # 3. aria-label
        if el.aria_label:
            xp = f'//{tag}[@aria-label={_xpath_escape(el.aria_label)}]'
            if xp not in seen:
                return xp, SelectorQuality.HIGH, ""

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

        # 7. Visible text (buttons, links, options)
        if el.tag in ("button", "a", "option") and el.visible_text:
            text = _normalise_text(el.visible_text)
            if text and len(text) < 80:
                xp = f'//{tag}[normalize-space()={_xpath_escape(text)}]'
                if xp not in seen:
                    return xp, SelectorQuality.MEDIUM, "text-based"
                xp = f'//{tag}[contains(normalize-space(), {_xpath_escape(text[:40])})]'
                if xp not in seen:
                    return xp, SelectorQuality.MEDIUM, "partial text-based"

        # 8. role
        if el.role:
            xp = f'//*[@role={_xpath_escape(el.role)}]'
            if xp not in seen:
                return xp, SelectorQuality.LOW, "role-only — likely non-unique"

        # 9. tag + type for inputs
        if el.tag == "input" and el.element_type:
            xp = f'//input[@type={_xpath_escape(el.element_type)}]'
            if xp not in seen:
                return xp, SelectorQuality.LOW, "type-only — likely non-unique"

        # 10. Positional fallback
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

        index_part = f":nth-of-type({el.element_index + 1})"
        return f"{tag}{class_part}{index_part}"
