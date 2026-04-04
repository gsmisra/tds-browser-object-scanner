"""
models/element_model.py  —  data-transfer objects for scanned DOM elements.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


# ---------------------------------------------------------------------------
# Confidence levels
# ---------------------------------------------------------------------------

class Confidence:
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# ---------------------------------------------------------------------------
# ScannedElement
# ---------------------------------------------------------------------------

@dataclass
class ScannedElement:
    """Represents a single interactive DOM element captured during a scan."""

    # --- identity ---
    tag: str = ""
    element_type: str = ""          # input type, role, tag name, …
    element_id: str = ""
    name: str = ""
    label: str = ""
    placeholder: str = ""
    visible_text: str = ""
    aria_label: str = ""
    data_testid: str = ""

    # --- position ---
    xpath: str = ""
    css_selector: str = ""
    nth_index: int = 0              # 1-based positional index among siblings

    # --- quality ---
    confidence: str = Confidence.LOW

    # --- source ---
    page_url: str = ""
    page_title: str = ""
    iframe_src: str = ""            # empty string = main frame

    # --- raw attribute bag (everything else) ---
    attributes: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# ScannedPage
# ---------------------------------------------------------------------------

@dataclass
class ScannedPage:
    """Represents one scan result for a complete browser page."""

    url: str = ""
    title: str = ""
    scanned_at: str = ""            # ISO-8601 timestamp
    elements: List[ScannedElement] = field(default_factory=list)
