"""
Data models for scanned page elements.
All fields use plain Python types so the objects are easily serialisable.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ---------------------------------------------------------------------------
# Confidence / quality enum-like constants
# ---------------------------------------------------------------------------

class SelectorQuality:
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Core element model
# ---------------------------------------------------------------------------

@dataclass
class ScannedElement:
    """Represents a single interactive element captured from a DOM scan."""

    # --- Identity ---
    element_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # --- Page context ---
    page_title: str = ""
    page_url: str = ""
    page_id: str = ""          # UUID of the parent ScannedPage
    frame_index: int = 0       # 0 = main frame; >0 = iframe index

    # --- DOM attributes ---
    tag: str = ""
    element_type: str = ""     # input[type], "button", "a", etc.
    visible_text: str = ""
    attr_id: str = ""
    attr_name: str = ""
    element_name: str = ""   # Display-friendly synthesized name (can differ from DOM @name)
    attr_class: str = ""
    attr_placeholder: str = ""
    screenshot_data: Optional[bytes] = None  # In-memory screenshot image data (PNG bytes)
    aria_label: str = ""
    role: str = ""
    href: str = ""
    data_testid: str = ""
    data_autom: str = ""       # data-autom, data-qa, data-cy, etc.

    # --- Contextual metadata ---
    label_text: str = ""       # <label for="..."> association
    nearby_heading: str = ""   # Nearest ancestor h1-h6 text
    nearby_heading_tag: str = ""  # e.g., "h3"

    # --- State ---
    is_visible: bool = True
    is_enabled: bool = True
    is_password_field: bool = False

    # --- Generated selectors ---
    css_selector: str = ""
    xpath: str = ""
    selector_quality: str = SelectorQuality.UNKNOWN
    selector_notes: str = ""
    css_element_count: int = 0      # Number of elements matching CSS selector on page
    xpath_element_count: int = 0    # Number of elements matching XPath on page

    # --- Parent/sibling context (for locator generation) ---
    parent_tag: str = ""
    parent_id: str = ""
    parent_class: str = ""
    nth_of_type: int = 0
    prev_sibling_tag: str = ""
    prev_sibling_id: str = ""
    prev_sibling_text: str = ""
    next_sibling_tag: str = ""
    next_sibling_id: str = ""
    next_sibling_text: str = ""
    is_shadow_element: bool = False
    shadow_host_tag: str = ""
    shadow_host_id: str = ""
    shadow_host_class: str = ""

    # --- Text ownership ---
    has_direct_text: bool = True   # True if text is from direct text nodes, not descendants

    # --- Ordering ---
    element_index: int = 0     # DOM order position on page

    def to_dict(self) -> dict:
        return {
            "element_id": self.element_id,
            "page_title": self.page_title,
            "page_url": self.page_url,
            "page_id": self.page_id,
            "frame_index": self.frame_index,
            "tag": self.tag,
            "element_type": self.element_type,
            "visible_text": self.visible_text,
            "attr_id": self.attr_id,
            "attr_name": self.attr_name,
            "element_name": self.element_name,
            "attr_class": self.attr_class,
            "attr_placeholder": self.attr_placeholder,
            "aria_label": self.aria_label,
            "role": self.role,
            "href": self.href,
            "data_testid": self.data_testid,
            "data_autom": self.data_autom,
            "label_text": self.label_text,
            "nearby_heading": self.nearby_heading,
            "nearby_heading_tag": self.nearby_heading_tag,
            "is_visible": self.is_visible,
            "is_enabled": self.is_enabled,
            "is_password_field": self.is_password_field,
            "css_selector": self.css_selector,
            "xpath": self.xpath,
            "selector_quality": self.selector_quality,
            "selector_notes": self.selector_notes,
            "parent_tag": self.parent_tag,
            "parent_id": self.parent_id,
            "parent_class": self.parent_class,
            "nth_of_type": self.nth_of_type,
            "prev_sibling_tag": self.prev_sibling_tag,
            "prev_sibling_id": self.prev_sibling_id,
            "prev_sibling_text": self.prev_sibling_text,
            "next_sibling_tag": self.next_sibling_tag,
            "next_sibling_id": self.next_sibling_id,
            "next_sibling_text": self.next_sibling_text,
            "is_shadow_element": self.is_shadow_element,
            "shadow_host_tag": self.shadow_host_tag,
            "shadow_host_id": self.shadow_host_id,
            "shadow_host_class": self.shadow_host_class,
            "has_direct_text": self.has_direct_text,
            "element_index": self.element_index,
            # screenshot_data excluded - not serialized (memory only)
            "css_element_count": self.css_element_count,
            "xpath_element_count": self.xpath_element_count,
        }


# ---------------------------------------------------------------------------
# Page scan result
# ---------------------------------------------------------------------------

@dataclass
class ScannedPage:
    """Represents a single page-level scan result."""

    page_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    page_title: str = ""
    page_url: str = ""
    page_label: str = ""           # Optional user-assigned nickname
    scan_timestamp: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )
    elements: list[ScannedElement] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "page_id": self.page_id,
            "page_title": self.page_title,
            "page_url": self.page_url,
            "page_label": self.page_label,
            "scan_timestamp": self.scan_timestamp,
            "element_count": len(self.elements),
            "elements": [e.to_dict() for e in self.elements],
        }
