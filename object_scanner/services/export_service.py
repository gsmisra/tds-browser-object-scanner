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

_EXCLUDED_EXPORT_FIELDS = {"tag", "element_type", "visible_text", "attr_id"}

# Flat CSV columns in display order
_CSV_COLUMNS = [
    "page_title",
    "page_url",
    "page_label",
    "scan_timestamp",
    "element_index",
    "frame_index",
    "attr_name",
    "element_name",
    "attr_class",
    "attr_placeholder",
    "aria_label",
    "role",
    "href",
    "data_testid",
    "label_text",
    "nearby_heading",
    "is_shadow_element",
    "shadow_host_tag",
    "shadow_host_id",
    "shadow_host_class",
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
        """Export all pages to a simplified JSON file with minimal metadata."""
        path = self._resolve_path(filename, "json")
        
        # Simplified structure: only page_title and locators
        simplified_pages = []
        for page in pages:
            page_data = {
                "page_title": page.page_title,
                "locators": []
            }
            for el in page.elements:
                locator_entry = {
                    "element_name": el.element_name or el.attr_name or "unnamed",
                }
                if el.xpath:
                    locator_entry["locator_type"] = "xpath"
                    locator_entry["locator_value"] = el.xpath
                    locator_entry["element_count"] = getattr(el, 'xpath_element_count', 0)
                elif el.css_selector:
                    locator_entry["locator_type"] = "css"
                    locator_entry["locator_value"] = el.css_selector
                    locator_entry["element_count"] = getattr(el, 'css_element_count', 0)
                else:
                    continue  # Skip elements without locators
                page_data["locators"].append(locator_entry)
            simplified_pages.append(page_data)

        payload = {"pages": simplified_pages}
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("JSON export written to %s", path)
        return path

    def export_csv(
        self,
        pages: list[ScannedPage],
        filename: Optional[str] = None,
    ) -> Path:
        """Export all elements to a simplified CSV file with minimal columns."""
        path = self._resolve_path(filename, "csv")

        rows: list[dict] = []
        for page in pages:
            for el in page.elements:
                if el.xpath:
                    rows.append({
                        "page_title": page.page_title,
                        "element_name": el.element_name or el.attr_name or "unnamed",
                        "locator_type": "xpath",
                        "locator_value": el.xpath,
                        "element_count": getattr(el, 'xpath_element_count', 0),
                    })
                elif el.css_selector:
                    rows.append({
                        "page_title": page.page_title,
                        "element_name": el.element_name or el.attr_name or "unnamed",
                        "locator_type": "css",
                        "locator_value": el.css_selector,
                        "element_count": getattr(el, 'css_element_count', 0),
                    })

        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["page_title", "element_name", "locator_type", "locator_value", "element_count"])
            writer.writeheader()
            writer.writerows(rows)

        logger.info("CSV export written to %s (%d rows)", path, len(rows))
        return path

    def export_properties(
        self,
        pages: list[ScannedPage],
        filename: Optional[str] = None,
    ) -> Path:
        """Export all elements to a .properties file format."""
        path = self._resolve_path(filename, "properties")

        lines = []
        lines.append("# TDS Object Scanner - Locator Properties")
        lines.append(f"# Generated: {datetime.now().isoformat(timespec='seconds')}")
        lines.append("")

        for page in pages:
            lines.append(f"# Page: {page.page_title}")
            lines.append("")
            for el in page.elements:
                element_name = el.element_name or el.attr_name or "unnamed"
                # Sanitize element name for property key
                key = element_name.replace(" ", "_").replace("-", "_")
                
                if el.xpath:
                    value = f"xpath,{el.xpath}"
                    lines.append(f"{key}={value}")
                elif el.css_selector:
                    value = f"css,{el.css_selector}"
                    lines.append(f"{key}={value}")
            lines.append("")

        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Properties export written to %s", path)
        return path

    def export_all(
        self,
        pages: list[ScannedPage],
        base_filename: Optional[str] = None,
    ) -> tuple[Path, Path, Path]:
        """Convenience: export JSON, CSV, and Properties with the same base filename."""
        json_path = self.export_json(pages, filename=f"{base_filename}.json" if base_filename else None)
        csv_path = self.export_csv(pages, filename=f"{base_filename}.csv" if base_filename else None)
        props_path = self.export_properties(pages, filename=f"{base_filename}.properties" if base_filename else None)
        return json_path, csv_path, props_path
    
    def append_to_existing_file(
        self,
        pages: list[ScannedPage],
        file_path: Path,
    ) -> Path:
        """
        Append/update locators to an existing file based on file extension.
        Supports .properties, .json, and .csv files.
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        ext = file_path.suffix.lower()
        
        if ext == ".properties":
            return self._append_to_properties(pages, file_path)
        elif ext == ".json":
            return self._append_to_json(pages, file_path)
        elif ext == ".csv":
            return self._append_to_csv(pages, file_path)
        else:
            raise ValueError(f"Unsupported file extension: {ext}. Supported: .properties, .json, .csv")
    
    def _append_to_properties(self, pages: list[ScannedPage], file_path: Path) -> Path:
        """Append/update locators in existing .properties file."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Read existing file
        existing_lines = []
        existing_keys = {}  # key -> line_index
        
        if file_path.exists():
            content = file_path.read_text(encoding="utf-8")
            existing_lines = content.split("\n")
            
            # Parse existing keys
            for i, line in enumerate(existing_lines):
                line_stripped = line.strip()
                if line_stripped and not line_stripped.startswith("#") and "=" in line_stripped:
                    key = line_stripped.split("=")[0].strip()
                    existing_keys[key] = i
        
        # Prepare new entries
        new_entries = []
        updated_indices = set()
        
        for page in pages:
            new_entries.append(f"# Page: {page.page_title}")
            for el in page.elements:
                element_name = el.element_name or el.attr_name or "unnamed"
                key = element_name.replace(" ", "_").replace("-", "_")
                
                if el.xpath:
                    value = f"xpath,{el.xpath}"
                elif el.css_selector:
                    value = f"css,{el.css_selector}"
                else:
                    continue
                
                property_line = f"{key}={value}"
                
                # Check if key already exists
                if key in existing_keys:
                    # Update existing line with comment
                    idx = existing_keys[key]
                    existing_lines[idx] = f"{property_line}  # Updated: {timestamp}"
                    updated_indices.add(idx)
                else:
                    # Add as new entry
                    new_entries.append(property_line)
            new_entries.append("")
        
        # Combine existing and new
        final_lines = existing_lines.copy()
        
        # Add header comment for new additions if there are any new entries
        if len(new_entries) > 0:
            final_lines.append("")
            final_lines.append(f"# Appended entries: {timestamp}")
            final_lines.extend(new_entries)
        
        file_path.write_text("\n".join(final_lines), encoding="utf-8")
        logger.info("Updated properties file: %s (%d keys updated, %d new)", 
                    file_path, len(updated_indices), len(new_entries) - new_entries.count("") - 1)
        return file_path
    
    def _append_to_json(self, pages: list[ScannedPage], file_path: Path) -> Path:
        """Append/update locators in existing .json file."""
        timestamp = datetime.now().isoformat(timespec='seconds')
        
        # Read existing JSON
        existing_data = json.loads(file_path.read_text(encoding="utf-8"))
        
        # Ensure structure exists
        if "pages" not in existing_data:
            existing_data["pages"] = []
        
        # Build lookup for existing locators by element_name
        existing_locators = {}
        for page_data in existing_data["pages"]:
            page_title = page_data.get("page_title", "")
            for locator in page_data.get("locators", []):
                key = (page_title, locator.get("element_name", ""))
                existing_locators[key] = locator
        
        # Process new pages
        updated_count = 0
        new_count = 0
        
        for page in pages:
            # Find or create page entry
            page_entry = None
            for p in existing_data["pages"]:
                if p.get("page_title") == page.page_title:
                    page_entry = p
                    break
            
            if not page_entry:
                page_entry = {
                    "page_title": page.page_title,
                    "locators": []
                }
                existing_data["pages"].append(page_entry)
            
            # Process elements
            for el in page.elements:
                element_name = el.element_name or el.attr_name or "unnamed"
                
                locator_entry = {
                    "element_name": element_name,
                }
                
                if el.xpath:
                    locator_entry["locator_type"] = "xpath"
                    locator_entry["locator_value"] = el.xpath
                    locator_entry["element_count"] = getattr(el, 'xpath_element_count', 0)
                elif el.css_selector:
                    locator_entry["locator_type"] = "css"
                    locator_entry["locator_value"] = el.css_selector
                    locator_entry["element_count"] = getattr(el, 'css_element_count', 0)
                else:
                    continue
                
                key = (page.page_title, element_name)
                
                # Check if locator exists
                if key in existing_locators:
                    # Update existing locator
                    existing_loc = existing_locators[key]
                    existing_loc["locator_type"] = locator_entry["locator_type"]
                    existing_loc["locator_value"] = locator_entry["locator_value"]
                    existing_loc["element_count"] = locator_entry["element_count"]
                    existing_loc["updated_at"] = timestamp
                    updated_count += 1
                else:
                    # Add new locator
                    locator_entry["added_at"] = timestamp
                    page_entry["locators"].append(locator_entry)
                    new_count += 1
        
        # Write back
        file_path.write_text(json.dumps(existing_data, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Updated JSON file: %s (%d updated, %d new)", file_path, updated_count, new_count)
        return file_path
    
    def _append_to_csv(self, pages: list[ScannedPage], file_path: Path) -> Path:
        """Append/update locators in existing .csv file."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Read existing CSV
        existing_rows = []
        existing_keys = {}  # (page_title, element_name) -> row_index
        
        with file_path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            
            for i, row in enumerate(reader):
                existing_rows.append(row)
                key = (row.get("page_title", ""), row.get("element_name", ""))
                existing_keys[key] = i
        
        # Process new data
        updated_count = 0
        new_count = 0
        
        for page in pages:
            for el in page.elements:
                element_name = el.element_name or el.attr_name or "unnamed"
                
                row_data = {
                    "page_title": page.page_title,
                    "element_name": element_name,
                }
                
                if el.xpath:
                    row_data["locator_type"] = "xpath"
                    row_data["locator_value"] = el.xpath
                    row_data["element_count"] = getattr(el, 'xpath_element_count', 0)
                elif el.css_selector:
                    row_data["locator_type"] = "css"
                    row_data["locator_value"] = el.css_selector
                    row_data["element_count"] = getattr(el, 'css_element_count', 0)
                else:
                    continue
                
                key = (page.page_title, element_name)
                
                # Check if row exists
                if key in existing_keys:
                    # Update existing row
                    idx = existing_keys[key]
                    existing_rows[idx].update(row_data)
                    existing_rows[idx]["updated_at"] = timestamp
                    updated_count += 1
                else:
                    # Add new row
                    row_data["added_at"] = timestamp
                    existing_rows.append(row_data)
                    new_count += 1
        
        # Ensure all fieldnames are present
        all_fieldnames = set(fieldnames) if fieldnames else set()
        for row in existing_rows:
            all_fieldnames.update(row.keys())
        
        # Write back with updated fieldnames
        fieldnames_list = ["page_title", "element_name", "locator_type", "locator_value", "element_count"]
        if "updated_at" in all_fieldnames:
            fieldnames_list.append("updated_at")
        if "added_at" in all_fieldnames:
            fieldnames_list.append("added_at")
        
        with file_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames_list)
            writer.writeheader()
            writer.writerows(existing_rows)
        
        logger.info("Updated CSV file: %s (%d updated, %d new)", file_path, updated_count, new_count)
        return file_path

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
