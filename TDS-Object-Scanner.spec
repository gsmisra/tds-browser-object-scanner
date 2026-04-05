# TDS-Object-Scanner.spec
# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for TDS QE Browser Object Scanner
#
# Build with:  pyinstaller --clean TDS-Object-Scanner.spec
#
# IMPORTANT: Playwright browser binaries are NOT bundled here.
# They live in %LOCALAPPDATA%\ms-playwright and are managed by Playwright.
# The exe will trigger the auto-install dialog on first run if browsers are
# not yet installed on the target machine.

from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

# Collect the entire playwright Python package (code only, not browsers)
pw_datas, pw_binaries, pw_hiddenimports = collect_all("playwright")

a = Analysis(
    ["object_scanner\\app.py"],
    pathex=["object_scanner"],
    binaries=pw_binaries,
    datas=pw_datas + [("object_scanner\\TD-Bank-Logo.png", ".")],
    hiddenimports=pw_hiddenimports + [
        "tkinter",
        "tkinter.ttk",
        "tkinter.messagebox",
        "tkinter.filedialog",
        "tkinter.simpledialog",
        "playwright.sync_api",
        "playwright._impl._sync_base",
        "playwright._impl._browser",
        "playwright._impl._page",
        "greenlet",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="TDS-Object-Scanner",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    # console=True keeps the log window visible — useful for a QA dev tool.
    # Set to False for a fully silent desktop app (logs will not be visible).
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
