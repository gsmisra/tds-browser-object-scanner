@echo off
setlocal

echo ============================================================
echo  TDS QE Browser Object Scanner - Build Script
echo ============================================================
echo.

:: Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found on PATH.
    pause
    exit /b 1
)

:: Install / upgrade PyInstaller
echo [1/3] Installing PyInstaller...
pip install --quiet --upgrade pyinstaller
if errorlevel 1 (
    echo ERROR: Failed to install PyInstaller.
    pause
    exit /b 1
)

:: Install app dependencies
echo [2/3] Installing app dependencies...
pip install --quiet -r object_scanner\requirements.txt
if errorlevel 1 (
    echo ERROR: Failed to install app dependencies.
    pause
    exit /b 1
)

:: Run PyInstaller
echo [3/3] Building executable...
pyinstaller --clean TDS-Object-Scanner.spec
if errorlevel 1 (
    echo ERROR: PyInstaller build failed.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  BUILD COMPLETE
echo  Executable: dist\TDS-Object-Scanner.exe
echo.
echo  NOTE: Playwright browser binaries are NOT bundled.
echo  Run the exe once on each new machine and it will offer
echo  to download Chromium automatically (~150 MB, one-time).
echo ============================================================
echo.
pause
