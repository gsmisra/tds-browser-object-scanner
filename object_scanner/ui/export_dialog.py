"""
ExportDialog — Combined dialog for exporting results.

Provides options for:
- Creating new export files (JSON, CSV, Properties)
- Appending to existing files
"""

from __future__ import annotations

import logging
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk, filedialog
from typing import Optional, TYPE_CHECKING

from ui import theme

if TYPE_CHECKING:
    from services.export_service import ExportService
    from services.session_service import SessionService

logger = logging.getLogger(__name__)


class ExportDialog:
    """Dialog for exporting scan results with multiple options."""

    def __init__(
        self,
        parent: tk.Tk,
        session: SessionService,
        exporter: ExportService,
    ) -> None:
        self.session = session
        self.exporter = exporter
        self.result: Optional[tuple[str, Path | tuple[Path, Path, Path]]] = None
        
        # Create modal dialog
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Download Results")
        self.dialog.geometry("520x410")  # Increased height for screenshot checkbox
        self.dialog.resizable(False, False)
        self.dialog.transient(parent)
        self.dialog.grab_set()
        
        # Center on parent
        self.dialog.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() // 2) - (520 // 2)
        y = parent.winfo_y() + (parent.winfo_height() // 2) - (410 // 2)
        self.dialog.geometry(f"+{x}+{y}")
        
        self._create_widgets()
        
        # Bind escape to cancel
        self.dialog.bind("<Escape>", lambda e: self._on_cancel())
        
        # Wait for dialog to complete
        self.dialog.wait_window()

    def _create_widgets(self) -> None:
        """Create all dialog widgets."""
        main_frame = ttk.Frame(self.dialog, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Title
        title_label = tk.Label(
            main_frame,
            text="Export Scan Results",
            font=("Segoe UI", 12, "bold"),
            bg=theme.BG,
            fg=theme.FG,
        )
        title_label.pack(pady=(0, 15))
        
        # Export mode selection
        self.export_mode = tk.StringVar(value="new")
        
        # Option 1: Create new files
        new_frame = ttk.LabelFrame(main_frame, text="Create New Files", padding=15)
        new_frame.pack(fill=tk.X, pady=(0, 10))
        
        new_radio = ttk.Radiobutton(
            new_frame,
            text="Export to new JSON, CSV, and Properties files",
            variable=self.export_mode,
            value="new"
        )
        new_radio.pack(anchor=tk.W)
        
        new_desc = tk.Label(
            new_frame,
            text="Creates 3 new files in the exports directory with timestamp",
            font=("Segoe UI", 8),
            fg=theme.FG_DIM,
            bg=theme.BG,
            justify=tk.LEFT,
        )
        new_desc.pack(anchor=tk.W, padx=(25, 0), pady=(3, 0))
        
        # Option 2: Append to existing
        existing_frame = ttk.LabelFrame(main_frame, text="Update Existing File", padding=15)
        existing_frame.pack(fill=tk.X, pady=(0, 20))
        
        existing_radio = ttk.Radiobutton(
            existing_frame,
            text="Append to or update an existing file",
            variable=self.export_mode,
            value="existing"
        )
        existing_radio.pack(anchor=tk.W)
        
        existing_desc = tk.Label(
            existing_frame,
            text="Updates existing entries with timestamp, appends new entries",
            font=("Segoe UI", 8),
            fg=theme.FG_DIM,
            bg=theme.BG,
            justify=tk.LEFT,
        )
        existing_desc.pack(anchor=tk.W, padx=(25, 0), pady=(3, 0))
        
        # Screenshot download option
        screenshot_frame = ttk.Frame(main_frame)
        screenshot_frame.pack(fill=tk.X, pady=(0, 20))
        
        self.include_screenshots = tk.BooleanVar(value=False)  # Unchecked by default
        
        screenshot_check = ttk.Checkbutton(
            screenshot_frame,
            text="Download captured screenshots",
            variable=self.include_screenshots
        )
        screenshot_check.pack(anchor=tk.W)
        
        screenshot_desc = tk.Label(
            screenshot_frame,
            text="Saves all in-memory screenshots to disk alongside export files",
            font=("Segoe UI", 8),
            fg=theme.FG_DIM,
            bg=theme.BG,
            justify=tk.LEFT,
        )
        screenshot_desc.pack(anchor=tk.W, padx=(25, 0), pady=(3, 0))
        
        # Buttons
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(10, 0))
        
        cancel_btn = ttk.Button(
            btn_frame,
            text="Cancel",
            command=self._on_cancel,
            width=12
        )
        cancel_btn.pack(side=tk.RIGHT, padx=(5, 0))
        
        export_btn = ttk.Button(
            btn_frame,
            text="Export",
            command=self._on_export,
            width=12
        )
        export_btn.pack(side=tk.RIGHT)

    def _on_export(self) -> None:
        """Handle export button click."""
        pages = self.session.pages
        if not pages:
            messagebox.showwarning(
                "Nothing to Export",
                "No scan results in this session.",
                parent=self.dialog
            )
            return
        
        mode = self.export_mode.get()
        
        try:
            if mode == "new":
                self._export_new_files(pages)
            else:
                self._export_to_existing(pages)
        except Exception as exc:
            logger.exception("Export failed")
            messagebox.showerror(
                "Export Error",
                f"Export failed:\n{exc}",
                parent=self.dialog
            )

    def _export_new_files(self, pages) -> None:
        """Export to new JSON, CSV, and Properties files."""
        try:
            json_path, csv_path, props_path = self.exporter.export_all(pages)
            
            # Save screenshots if checkbox is checked
            screenshot_count = 0
            if self.include_screenshots.get():
                screenshot_count = self._save_screenshots(pages, json_path.parent)
            
            self.result = ("new", (json_path, csv_path, props_path), screenshot_count)
            
            # Build success message
            msg = (f"Files created successfully:\n\n"
                   f"• {json_path.name}\n"
                   f"• {csv_path.name}\n"
                   f"• {props_path.name}")
            
            if screenshot_count > 0:
                msg += f"\n• {screenshot_count} screenshots saved"
            
            msg += f"\n\nLocation: {json_path.parent}"
            
            messagebox.showinfo(
                "Export Complete",
                msg,
                parent=self.dialog
            )
            self.dialog.destroy()
            
        except Exception as exc:
            logger.exception("New file export failed")
            raise

    def _export_to_existing(self, pages) -> None:
        """Export/append to an existing file."""
        # Show file dialog to select existing file
        file_path = filedialog.askopenfilename(
            title="Select Existing File to Update",
            filetypes=[
                ("All supported", "*.properties *.json *.csv"),
                ("Properties files", "*.properties"),
                ("JSON files", "*.json"),
                ("CSV files", "*.csv"),
            ],
            parent=self.dialog
        )
        
        if not file_path:
            # User cancelled
            return
        
        try:
            updated_path = self.exporter.append_to_existing_file(pages, Path(file_path))
            
            # Save screenshots if checkbox is checked
            screenshot_count = 0
            if self.include_screenshots.get():
                screenshot_count = self._save_screenshots(pages, updated_path.parent)
            
            self.result = ("existing", updated_path, screenshot_count)
            
            # Determine file type
            file_ext = updated_path.suffix.lower()
            if file_ext == ".properties":
                file_type = "Properties"
            elif file_ext == ".json":
                file_type = "JSON"
            elif file_ext == ".csv":
                file_type = "CSV"
            else:
                file_type = "File"
            
            # Build success message
            msg = (f"{file_type} file updated successfully:\n\n"
                   f"{updated_path}\n\n"
                   f"• Existing entries updated with timestamp\n"
                   f"• New entries appended")
            
            if screenshot_count > 0:
                msg += f"\n• {screenshot_count} screenshots saved"
            
            messagebox.showinfo(
                "Update Complete",
                msg,
                parent=self.dialog
            )
            self.dialog.destroy()
            
        except FileNotFoundError as exc:
            messagebox.showerror("File Not Found", str(exc), parent=self.dialog)
            raise
        except ValueError as exc:
            messagebox.showerror("Invalid File Type", str(exc), parent=self.dialog)
            raise
        except Exception as exc:
            logger.exception("Append to existing failed")
            raise

    def _on_cancel(self) -> None:
        """Handle cancel button click."""
        self.result = None
        self.dialog.destroy()
    
    def _save_screenshots(self, pages, export_dir: Path) -> int:
        """
        Save all in-memory screenshots to disk.
        
        Args:
            pages: List of ScannedPage objects
            export_dir: Directory where export files are saved
            
        Returns:
            Number of screenshots saved
        """
        from pathlib import Path
        
        screenshots_dir = export_dir / "screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        
        count = 0
        for page in pages:
            for el in page.elements:
                if el.screenshot_data:
                    # Generate filename based on element name
                    safe_name = "".join(c if c.isalnum() or c in ('-', '_') else '_' 
                                       for c in (el.element_name or f"element_{el.element_index}"))
                    filename = f"{safe_name}_{el.element_id[:8]}.png"
                    screenshot_path = screenshots_dir / filename
                    
                    try:
                        # Write bytes to file
                        screenshot_path.write_bytes(el.screenshot_data)
                        count += 1
                        logger.debug("Saved screenshot: %s", filename)
                    except Exception as exc:
                        logger.warning("Failed to save screenshot %s: %s", filename, exc)
        
        if count > 0:
            logger.info("Saved %d screenshots to %s", count, screenshots_dir)
        
        return count
