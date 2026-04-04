"""
utils/string_utils.py  —  small string-manipulation helpers.
"""
from __future__ import annotations

import re


# Patterns that indicate a framework-generated (unstable) id
_UNSTABLE_ID_PATTERNS = [
    re.compile(r"\d{3,}"),                  # long numeric suffix
    re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}", re.I),  # GUID fragment
    re.compile(r":r[0-9a-z]+:"),            # React 18 auto-ids
    re.compile(r"^mat-", re.I),             # Angular Material auto-ids
    re.compile(r"^ng-"),                    # Angular auto-ids
    re.compile(r"ember\d+"),                # Ember generated
]


def is_stable_id(id_value: str) -> bool:
    """Return *True* when the id looks like a developer-assigned stable value."""
    if not id_value:
        return False
    for pattern in _UNSTABLE_ID_PATTERNS:
        if pattern.search(id_value):
            return False
    return True


def truncate(text: str, max_len: int = 80) -> str:
    """Truncate *text* to *max_len* characters, appending '…' if needed."""
    if not text:
        return ""
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def normalise_whitespace(text: str) -> str:
    """Collapse consecutive whitespace characters to a single space."""
    return re.sub(r"\s+", " ", text).strip()


def sanitise_filename(name: str) -> str:
    """Replace characters that are unsafe in file-names with underscores."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
