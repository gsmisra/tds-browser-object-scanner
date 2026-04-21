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
        merge new elements into the existing page, preserving any previously
        scanned elements (e.g. from manual scans).
        Otherwise append (creating a duplicate entry).
        """
        if overwrite:
            existing_index = self._find_index_by_url(page.page_url)
            if existing_index is not None:
                existing_page = self._pages[existing_index]
                self._merge_elements(existing_page, page)
                # Update metadata
                existing_page.page_title = page.page_title
                existing_page.scan_timestamp = page.scan_timestamp
                logger.info(
                    "Merged scan into existing page for URL: %s (%d elements)",
                    page.page_url, len(existing_page.elements),
                )
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
    
    def remove_elements(self, element_ids: list[str]) -> tuple[int, int]:
        """Remove specific elements by their IDs across all pages.
        
        Returns:
            tuple[int, int]: (count of elements removed, count of pages removed)
        """
        element_id_set = set(element_ids)
        removed_count = 0
        
        # Remove elements from pages
        for page in self._pages:
            original_count = len(page.elements)
            page.elements = [el for el in page.elements if el.element_id not in element_id_set]
            removed_count += (original_count - len(page.elements))
        
        # Remove pages that have no elements left
        pages_before = len(self._pages)
        self._pages = [page for page in self._pages if len(page.elements) > 0]
        pages_removed = pages_before - len(self._pages)
        
        logger.info("Removed %d elements from session, removed %d empty pages", removed_count, pages_removed)
        return removed_count, pages_removed

    def clear(self) -> None:
        """Remove all pages from the session."""
        self._pages.clear()
        logger.info("Session cleared.")

    def total_element_count(self) -> int:
        return sum(len(p.elements) for p in self._pages)

    def add_element_to_url(self, url: str, title: str, element) -> None:
        """Add a single element to the page with the given URL, creating if needed."""
        page = self.get_page_by_url(url)
        if page:
            max_idx = max((e.element_index for e in page.elements), default=-1)
            element.element_index = max_idx + 1
            element.page_id = page.page_id
            page.elements.append(element)
            logger.info("Appended manual element to existing page '%s'", title)
        else:
            new_page = ScannedPage(page_url=url, page_title=title)
            element.element_index = 0
            element.page_id = new_page.page_id
            new_page.elements.append(element)
            self._pages.append(new_page)
            logger.info("Created new page '%s' for manual element", title)

    def set_page_label(self, page_id: str, label: str) -> bool:
        page = self.get_page_by_id(page_id)
        if page:
            page.page_label = label
            return True
        return False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_elements(existing: ScannedPage, incoming: ScannedPage) -> None:
        """
        Merge elements from *incoming* into *existing*, avoiding duplicates.
        An element is considered a duplicate if it has the same tag,
        visible_text, attr_id, attr_name, and attr_class.
        Previously scanned elements (e.g. manual picks) are always retained.
        """
        def _element_key(el) -> tuple:
            return (
                el.tag,
                el.visible_text.strip()[:100],
                el.attr_id,
                el.attr_name,
                el.attr_class,
            )

        existing_keys = {_element_key(e) for e in existing.elements}
        max_idx = max((e.element_index for e in existing.elements), default=-1)

        added = 0
        for el in incoming.elements:
            key = _element_key(el)
            if key not in existing_keys:
                max_idx += 1
                el.element_index = max_idx
                el.page_id = existing.page_id
                existing.elements.append(el)
                existing_keys.add(key)
                added += 1

        logger.debug(
            "Merge: %d new elements added, %d duplicates skipped, %d total",
            added, len(incoming.elements) - added, len(existing.elements),
        )

    def _find_index_by_url(self, url: str) -> Optional[int]:
        for i, p in enumerate(self._pages):
            if p.page_url == url:
                return i
        return None
