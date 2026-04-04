"""
services/locator_service.py  —  rule-based CSS + XPath locator generation.

Confidence levels
-----------------
HIGH   – unique stable id, data-testid, aria-label
MEDIUM – name, label text, placeholder, visible text
LOW    – type-only, role-only, or positional nth-child
"""
from __future__ import annotations

from typing import Tuple

from models.element_model import Confidence, ScannedElement
from utils.string_utils import is_stable_id


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_locators(element: ScannedElement) -> Tuple[str, str, str]:
    """Return (css_selector, xpath, confidence) for *element*.

    The function is pure: it reads fields already populated on the element
    dataclass and returns the best selectors it can construct.
    """
    css, xpath, confidence = _try_high(element)
    if confidence == Confidence.HIGH:
        return css, xpath, confidence

    css_m, xpath_m, conf_m = _try_medium(element)
    if conf_m == Confidence.MEDIUM:
        return css_m, xpath_m, conf_m

    return _build_low(element)


# ---------------------------------------------------------------------------
# Confidence tiers
# ---------------------------------------------------------------------------

def _try_high(el: ScannedElement) -> Tuple[str, str, str]:
    """Attempt to build a HIGH-confidence selector."""

    # data-testid takes top priority (framework-agnostic, stable by convention)
    if el.data_testid:
        css = f'[data-testid="{_esc_css(el.data_testid)}"]'
        xpath = f'//*[@data-testid="{_esc_xpath(el.data_testid)}"]'
        return css, xpath, Confidence.HIGH

    # Stable element id
    if el.element_id and is_stable_id(el.element_id):
        css = f'#{_esc_css(el.element_id)}'
        xpath = f'//*[@id="{_esc_xpath(el.element_id)}"]'
        return css, xpath, Confidence.HIGH

    # aria-label
    if el.aria_label:
        css = f'[aria-label="{_esc_css(el.aria_label)}"]'
        xpath = f'//*[@aria-label="{_esc_xpath(el.aria_label)}"]'
        return css, xpath, Confidence.HIGH

    return "", "", Confidence.LOW


def _try_medium(el: ScannedElement) -> Tuple[str, str, str]:
    """Attempt to build a MEDIUM-confidence selector."""
    tag = el.tag.lower() if el.tag else "*"

    # name attribute (forms)
    if el.name:
        css = f'{tag}[name="{_esc_css(el.name)}"]'
        xpath = f'//{tag}[@name="{_esc_xpath(el.name)}"]'
        return css, xpath, Confidence.MEDIUM

    # placeholder
    if el.placeholder:
        css = f'{tag}[placeholder="{_esc_css(el.placeholder)}"]'
        xpath = f'//{tag}[@placeholder="{_esc_xpath(el.placeholder)}"]'
        return css, xpath, Confidence.MEDIUM

    # visible text (buttons, links, labels)
    if el.visible_text:
        txt = el.visible_text.strip()
        xpath = f'//{tag}[normalize-space(.)="{_esc_xpath(txt)}"]'
        css = f'{tag}'      # CSS has no reliable text-content selector without :contains
        return css, xpath, Confidence.MEDIUM

    # label association
    if el.label:
        xpath = f'//{tag}[@id=//label[normalize-space(.)="{_esc_xpath(el.label)}"]/@for]'
        css = f'{tag}'
        return css, xpath, Confidence.MEDIUM

    return "", "", Confidence.LOW


def _build_low(el: ScannedElement) -> Tuple[str, str, str]:
    """Build a LOW-confidence positional selector as a last resort."""
    tag = el.tag.lower() if el.tag else "*"
    el_type = el.element_type.lower() if el.element_type else ""

    if el_type and tag == "input":
        css = f'input[type="{_esc_css(el_type)}"]'
        xpath = f'//input[@type="{_esc_xpath(el_type)}"]'
    elif el_type:
        css = f'{tag}[role="{_esc_css(el_type)}"]'
        xpath = f'//{tag}[@role="{_esc_xpath(el_type)}"]'
    else:
        css = tag
        xpath = f'//{tag}'

    # Append nth-child if we have a positional index
    nth = el.nth_index
    if nth and nth > 0:
        css = f'{css}:nth-of-type({nth})'
        xpath = f'({xpath})[{nth}]'

    return css, xpath, Confidence.LOW


# ---------------------------------------------------------------------------
# Escaping helpers
# ---------------------------------------------------------------------------

def _esc_css(value: str) -> str:
    """Escape special characters inside a CSS attribute-value string."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _esc_xpath(value: str) -> str:
    """Escape double-quotes in an XPath string literal."""
    # XPath has no escape character; if the value contains both quote types
    # we use concat().
    if '"' not in value:
        return value
    # Wrap segments around single quotes
    parts = value.split('"')
    concat_args = ', \'"\', '.join(f'"{p}"' for p in parts)
    return f"concat({concat_args})"
