"""
Global configuration for the Object Scanner application.
Edit these values or override via environment variables as needed.
"""

from pathlib import Path

# -------------------------------------------------------------------
# Browser settings
# -------------------------------------------------------------------
BROWSER_TYPE: str = "chromium"   # "chromium" | "firefox" | "webkit"
START_URL: str = "about:blank"
HEADLESS: bool = False           # Always False for manual-assisted scanning
SLOW_MO: int = 0                 # ms delay between Playwright actions (0 = none)

# -------------------------------------------------------------------
# Scanning settings
# -------------------------------------------------------------------
SCAN_TIMEOUT_MS: int = 10_000    # Max ms to wait for page load before scanning
INCLUDE_IFRAMES: bool = True     # Attempt to scan iframe contents
SKIP_HIDDEN_ELEMENTS: bool = True  # Skip elements with display:none / visibility:hidden

# -------------------------------------------------------------------
# Export settings
# -------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
EXPORT_DIR: Path = Path.home() / "Downloads"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

# -------------------------------------------------------------------
# UI settings
# -------------------------------------------------------------------
APP_TITLE: str = "TDS QE Browser Object Scanner"
WINDOW_GEOMETRY: str = "1400x800"
TABLE_ROW_HEIGHT: int = 24
