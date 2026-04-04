# Browser Object Scanner

A production-style Python desktop application for manually-assisted DOM scanning
and XPath/CSS locator generation against enterprise web applications.

---

## Overview

The app launches a real browser window (Playwright), leaves all navigation
entirely to you (login, MFA, page-by-page flow), and on demand scans the
current page DOM to extract interactive elements and generate deterministic
CSS and XPath locators.

---

## Quick Start

### 1 — Create and activate a virtual environment

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
```

### 2 — Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

> To use Firefox or WebKit instead, also run `playwright install firefox` or
> `playwright install webkit`.

### 3 — Run the application

```bash
cd object_scanner
python app.py
```

---

## Usage Workflow

1. Select browser type in the dropdown (default: chromium).
2. Click **Launch Browser** — a visible browser window opens.
3. Manually log in to your target web application.
4. Navigate to the page you want to scan.
5. Click **Scan Current Page** in the desktop app.
6. Inspect results in the table.  Click a row to see the full element detail.
7. Right-click any row or use the buttons to copy CSS or XPath.
8. Navigate to the next page, repeat from step 5.
9. Click **Export Results** to write JSON + CSV files to `data/exports/`.

---

## Configuration

Edit `config.py` for project-level defaults:

| Setting               | Default          | Description                              |
|-----------------------|------------------|------------------------------------------|
| `BROWSER_TYPE`        | `chromium`       | chromium / firefox / webkit              |
| `START_URL`           | `about:blank`    | URL opened on browser launch             |
| `HEADLESS`            | `False`          | Never True for manual-assisted scanning  |
| `SCAN_TIMEOUT_MS`     | `10000`          | Max ms to wait for DOM before scanning   |
| `INCLUDE_IFRAMES`     | `True`           | Attempt to scan iframe contents          |
| `SKIP_HIDDEN_ELEMENTS`| `True`           | Skip display:none / visibility:hidden    |
| `EXPORT_DIR`          | `data/exports/`  | Output directory for JSON/CSV exports    |

---

## Project Structure

```
object_scanner/
├── app.py                      # Entry point
├── config.py                   # Configuration constants
├── requirements.txt
├── README.md
├── models/
│   └── element_model.py        # ScannedElement, ScannedPage dataclasses
├── services/
│   ├── browser_service.py      # Playwright browser lifecycle
│   ├── dom_scanner_service.py  # JS injection + element extraction
│   ├── locator_service.py      # CSS + XPath generation (rule-based)
│   ├── export_service.py       # JSON + CSV export
│   └── session_service.py      # In-memory session tracking
├── ui/
│   ├── main_window.py          # Root window + toolbar + status bar
│   ├── table_view.py           # Sortable Treeview of scanned elements
│   └── details_dialog.py       # Modal full-detail popup
├── utils/
│   ├── clipboard_utils.py
│   └── string_utils.py
└── data/
    └── exports/                # Auto-created; exported JSON/CSV land here
```

---

## Selector Confidence Levels

| Quality  | Meaning                                            |
|----------|----------------------------------------------------|
| HIGH     | Unique stable id, data-testid, or aria-label       |
| MEDIUM   | Name, label, placeholder, visible text             |
| LOW      | Type-only, role-only, or positional nth-child      |

Rows are colour-coded green/amber/red in the table accordingly.

---

## Enterprise Notes & Caveats

- **No credentials captured** — the app never reads input values for
  `type="password"` fields, and does not access cookies, local storage,
  or session tokens.
- **Single-page-app (SPA) support** — the scanner waits for
  `domcontentloaded` before extraction; on heavy React/Angular apps you
  may need to let the page fully settle before clicking "Scan Current Page".
- **Shadow DOM** — elements inside closed shadow roots are not accessible
  via standard `querySelectorAll`; the scanner captures their host element
  only.  An optional `page.evaluate` extension point can be added per-app.
- **iframe cross-origin** — iframes from a different origin block JS access
  due to browser security policy; the scanner silently skips them and logs
  a warning.
- **Dynamic element IDs** — many frameworks (Material-UI, AG Grid, etc.)
  generate unstable numeric or GUID ids at render time.  The locator service
  detects these heuristically and downgrades confidence, preferring
  `data-testid` or `aria-label` instead.
- **Playwright vs Selenium** — Playwright is used here because it ships its
  own browser binaries, eliminating WebDriver version-mismatch issues common
  in enterprise environments with managed Chrome rollouts.
