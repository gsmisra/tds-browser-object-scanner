"""
app.py  —  Entry point for the Browser Object Scanner desktop application.

Usage
-----
    cd object_scanner
    python app.py
"""
from __future__ import annotations

import logging
import os
import sys

# Ensure sibling packages are importable when running as `python app.py`
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)

from ui.main_window import MainWindow  # noqa: E402  (import after sys.path fixup)


def main() -> None:
    app = MainWindow()
    app.mainloop()


if __name__ == "__main__":
    main()
