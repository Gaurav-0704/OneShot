@echo off
REM ============================================================
REM  Start Skyvern server in its own Python 3.13 environment.
REM  Leave this window open while you use OneShot.
REM  Ctrl+C to stop.
REM ============================================================

echo.
echo === Skyvern server ===
echo === Listening on http://localhost:8000 ===
echo === Leave this window open. Press Ctrl+C to stop. ===
echo.

if not exist "D:\skyvern-env\Scripts\skyvern.exe" (
    echo *** ERROR: D:\skyvern-env\Scripts\skyvern.exe not found.
    echo *** Run setup first:
    echo ***   py -3.13 -m venv D:\skyvern-env
    echo ***   D:\skyvern-env\Scripts\python.exe -m pip install skyvern
    echo ***   D:\skyvern-env\Scripts\skyvern init
    pause
    exit /b 1
)

D:\skyvern-env\Scripts\skyvern.exe run server
