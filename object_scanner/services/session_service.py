"""
services/session_service.py  —  in-memory session tracking.

Keeps an ordered list of :class:`~models.element_model.ScannedPage` objects
recorded during the current desktop-app session.  No persistence is applied
here; call :mod:`export_service` to write files.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from models.element_model import ScannedElement, ScannedPage

log = logging.getLogger(__name__)


class SessionService:
    """Thread-safe in-memory store for scan results."""

    def __init__(self) -> None:
        self._pages: List[ScannedPage] = []

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_page(self, page: ScannedPage) -> None:
        """Append a freshly scanned page to the session."""
        self._pages.append(page)
        log.info("Session: added page %r with %d elements", page.url, len(page.elements))

    def clear(self) -> None:
        """Remove all scan results from the current session."""
        self._pages.clear()
        log.info("Session cleared.")

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def pages(self) -> List[ScannedPage]:
        """Return a shallow copy of the list of scanned pages."""
        return list(self._pages)

    @property
    def all_elements(self) -> List[ScannedElement]:
        """Flatten all elements across all scanned pages into a single list."""
        result: List[ScannedElement] = []
        for p in self._pages:
            result.extend(p.elements)
        return result

    @property
    def page_count(self) -> int:
        return len(self._pages)

    @property
    def element_count(self) -> int:
        return sum(len(p.elements) for p in self._pages)

    def latest_page(self) -> Optional[ScannedPage]:
        """Return the most recently added page, or ``None`` if empty."""
        return self._pages[-1] if self._pages else None
