"""
ExportService — writes scan results to disk in JSON and/or CSV formats.

Responsibilities:
- Accept a list of ScannedPage objects
- Serialise to JSON (structured) and CSV (flat)
- Return the path(s) of written file(s)
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import config
from models.element_model import ScannedPage

logger = logging.getLogger(__name__)

# Flat CSV columns in display order
_CSV_COLUMNS = [
    "page_title",
    "page_url",
    "page_label",
    "scan_timestamp",
    "element_index",
    "frame_index",
    "tag",
    "element_type",
    "visible_text",
    "attr_id",
    "attr_name",
    "attr_class",
    "attr_placeholder",
    "aria_label",
    "role",
    "href",
    "data_testid",
    "label_text",
    "nearby_heading",
    "is_visible",
    "is_enabled",
    "is_password_field",
    "css_selector",
    "xpath",
    "selector_quality",
    "selector_notes",
]


class ExportService:
    """Serialises session scan results to JSON and CSV."""

    def __init__(self, export_dir: Optional[Path] = None) -> None:
        self._export_dir = export_dir or config.EXPORT_DIR
        self._export_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def export_json(
        self,
        pages: list[ScannedPage],
        filename: Optional[str] = None,
    ) -> Path:
        """Export all pages to a single structured JSON file."""
        path = self._resolve_path(filename, "json")
        payload = {
            "export_timestamp": datetime.now().isoformat(timespec="seconds"),
            "page_count": len(pages),
            "pages": [p.to_dict() for p in pages],
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("JSON export written to %s", path)
        return path

    def export_csv(
        self,
        pages: list[ScannedPage],
        filename: Optional[str] = None,
    ) -> Path:
        """Export all elements across all pages to a flat CSV file."""
        path = self._resolve_path(filename, "csv")

        rows: list[dict] = []
        for page in pages:
            for el in page.elements:
                row = el.to_dict()
                # Merge page label from parent
                row["page_label"] = page.page_label
                row["scan_timestamp"] = page.scan_timestamp
                rows.append(row)

        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

        logger.info("CSV export written to %s (%d rows)", path, len(rows))
        return path

    def export_both(
        self,
        pages: list[ScannedPage],
        base_filename: Optional[str] = None,
    ) -> tuple[Path, Path]:
        """Convenience: export JSON and CSV with the same base filename."""
        json_path = self.export_json(pages, filename=f"{base_filename}.json" if base_filename else None)
        csv_path = self.export_csv(pages, filename=f"{base_filename}.csv" if base_filename else None)
        return json_path, csv_path

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_path(self, filename: Optional[str], ext: str) -> Path:
        if filename:
            p = Path(filename)
            if not p.is_absolute():
                p = self._export_dir / p
            return p
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return self._export_dir / f"scan_{timestamp}.{ext}"
