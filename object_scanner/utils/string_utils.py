"""
String utilities shared across the application.
"""

from __future__ import annotations

import re


def truncate(text: str, max_len: int = 80, suffix: str = "…") -> str:
    """Truncate *text* to *max_len* characters, appending *suffix* if cut."""
    if len(text) <= max_len:
        return text
    return text[: max_len - len(suffix)] + suffix


def normalise_whitespace(text: str) -> str:
    """Collapse multiple whitespace characters into a single space."""
    return re.sub(r"\s+", " ", text).strip()


def slugify(text: str, max_len: int = 60) -> str:
    """Convert *text* to a filesystem-safe slug."""
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[\s_-]+", "_", slug)
    return slug[:max_len].strip("_")
