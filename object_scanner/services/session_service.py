"""
SessionService — tracks scanned pages within the current application run.

Responsibilities:
- Maintain an ordered list of ScannedPage results for this session
- Support overwrite-or-append semantics when the same URL is rescanned
- Provide lookup by page_id or URL
"""

from __future__ import annotations

import logging
from typing import Optional

from models.element_model import ScannedPage

logger = logging.getLogger(__name__)


class SessionService:
    """In-memory store of scanned pages for the current session."""

    def __init__(self) -> None:
        self._pages: list[ScannedPage] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def pages(self) -> list[ScannedPage]:
        """Ordered list of scanned pages (oldest first)."""
        return list(self._pages)

    @property
    def page_count(self) -> int:
        return len(self._pages)

    def add_or_replace(self, page: ScannedPage, overwrite: bool = True) -> None:
        """
        Add a new scanned page to the session.

        If ``overwrite`` is True and a page with the same URL already exists,
        replace it.  Otherwise append (creating a duplicate entry).
        """
        if overwrite:
            existing_index = self._find_index_by_url(page.page_url)
            if existing_index is not None:
                logger.info(
                    "Replacing existing scan for URL: %s", page.page_url
                )
                self._pages[existing_index] = page
                return

        self._pages.append(page)
        logger.info(
            "Added scan for page '%s' (%d elements total in session across %d pages)",
            page.page_title,
            self.total_element_count(),
            len(self._pages),
        )

    def get_page_by_id(self, page_id: str) -> Optional[ScannedPage]:
        for p in self._pages:
            if p.page_id == page_id:
                return p
        return None

    def get_page_by_url(self, url: str) -> Optional[ScannedPage]:
        for p in self._pages:
            if p.page_url == url:
                return p
        return None

    def remove_page(self, page_id: str) -> bool:
        for i, p in enumerate(self._pages):
            if p.page_id == page_id:
                del self._pages[i]
                return True
        return False

    def clear(self) -> None:
        """Remove all pages from the session."""
        self._pages.clear()
        logger.info("Session cleared.")

    def total_element_count(self) -> int:
        return sum(len(p.elements) for p in self._pages)

    def set_page_label(self, page_id: str, label: str) -> bool:
        page = self.get_page_by_id(page_id)
        if page:
            page.page_label = label
            return True
        return False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _find_index_by_url(self, url: str) -> Optional[int]:
        for i, p in enumerate(self._pages):
            if p.page_url == url:
                return i
        return None
