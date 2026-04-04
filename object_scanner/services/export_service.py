"""
services/export_service.py  —  JSON + CSV export of scan results.

Writes two files per export into *EXPORT_DIR*:
  - ``<timestamp>_scan.json``  — full detail for every scanned element
  - ``<timestamp>_scan.csv``   — flat table suitable for spreadsheet review
"""
from __future__ import annotations

import csv
import json
import logging
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

from models.element_model import ScannedPage
from utils.string_utils import sanitise_filename

log = logging.getLogger(__name__)

# Columns written to the CSV file (in order)
_CSV_COLUMNS = [
    "page_url",
    "page_title",
    "tag",
    "element_type",
    "element_id",
    "name",
    "label",
    "placeholder",
    "visible_text",
    "aria_label",
    "data_testid",
    "css_selector",
    "xpath",
    "confidence",
    "iframe_src",
]


def export(pages: List[ScannedPage], export_dir: str = "data/exports") -> Tuple[str, str]:
    """Write JSON and CSV files for *pages*.

    Parameters
    ----------
    pages:
        List of :class:`~models.element_model.ScannedPage` objects to export.
    export_dir:
        Directory path (relative to the current working directory, or absolute).

    Returns
    -------
    Tuple[str, str]
        Absolute paths of the created ``(json_path, csv_path)``.
    """
    out_dir = Path(export_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base_name = f"{stamp}_scan"

    json_path = out_dir / f"{base_name}.json"
    csv_path = out_dir / f"{base_name}.csv"

    # --- JSON ---
    payload = [asdict(p) for p in pages]
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    log.info("Exported JSON → %s", json_path)

    # --- CSV ---
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for page in pages:
            for el in page.elements:
                row = {
                    "page_url": el.page_url,
                    "page_title": el.page_title,
                    "tag": el.tag,
                    "element_type": el.element_type,
                    "element_id": el.element_id,
                    "name": el.name,
                    "label": el.label,
                    "placeholder": el.placeholder,
                    "visible_text": el.visible_text,
                    "aria_label": el.aria_label,
                    "data_testid": el.data_testid,
                    "css_selector": el.css_selector,
                    "xpath": el.xpath,
                    "confidence": el.confidence,
                    "iframe_src": el.iframe_src,
                }
                writer.writerow(row)
    log.info("Exported CSV  → %s", csv_path)

    return str(json_path.resolve()), str(csv_path.resolve())
