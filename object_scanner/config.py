# ---------------------------------------------------------------------------
# config.py  —  project-level configuration constants
# ---------------------------------------------------------------------------

# Browser to launch: chromium | firefox | webkit
BROWSER_TYPE: str = "chromium"

# URL opened when the browser is first launched
START_URL: str = "about:blank"

# Never set True for manual-assisted scanning
HEADLESS: bool = False

# Maximum milliseconds to wait for the DOM before scanning
SCAN_TIMEOUT_MS: int = 10_000

# Attempt to scan iframe contents (same-origin only)
INCLUDE_IFRAMES: bool = True

# Skip elements whose computed style is display:none or visibility:hidden
SKIP_HIDDEN_ELEMENTS: bool = True

# Directory where JSON / CSV exports are written (created automatically)
EXPORT_DIR: str = "data/exports"

# Window title shown in the desktop application
APP_TITLE: str = "Browser Object Scanner"

# Colour scheme for confidence-level rows in the results table
ROW_COLOR_HIGH: str = "#d4edda"    # green
ROW_COLOR_MEDIUM: str = "#fff3cd"  # amber
ROW_COLOR_LOW: str = "#f8d7da"     # red
