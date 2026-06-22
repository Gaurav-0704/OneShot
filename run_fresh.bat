@echo off
REM ============================================================
REM  OneShot - clean restart
REM  Kills any stuck python processes BEFORE cleaning, so the
REM  next run never hits "Access is denied" from a leftover
REM  Flask thread holding python.exe open.
REM ============================================================

setlocal

echo.
echo === OneShot fresh restart ===
echo.

cd /d "%~dp0"

echo [0/5] Killing any orphaned python processes ...
taskkill /F /IM python.exe  >nul 2>&1
taskkill /F /IM pythonw.exe >nul 2>&1
REM Brief pause so the OS releases file handles before we try to launch again
timeout /t 1 /nobreak >nul

echo [1/5] Removing __pycache__ folders ...
for /d /r "%~dp0." %%d in (__pycache__) do (
    if exist "%%d" rmdir /s /q "%%d" 2>nul
)
del /s /q "%~dp0*.pyc" 2>nul

echo [2/5] Clearing run logs ...
if exist "%~dp0outputs\logs" del /q "%~dp0outputs\logs\*.log" 2>nul

echo [3/5] Clearing last-run snapshots ...
if exist "%~dp0outputs\last_discovered.json" del /q "%~dp0outputs\last_discovered.json"
if exist "%~dp0outputs\last_run.json"        del /q "%~dp0outputs\last_run.json"

if /i "%~1"=="--hard" (
    echo [4/5] HARD reset - wiping application history ...
    if exist "%~dp0outputs\applied_jobs.csv"   del /q "%~dp0outputs\applied_jobs.csv"
    if exist "%~dp0outputs\pending_review.csv" del /q "%~dp0outputs\pending_review.csv"
    if exist "%~dp0outputs\failed_jobs.csv"    del /q "%~dp0outputs\failed_jobs.csv"
    if exist "%~dp0outputs\api_usage.json"     del /q "%~dp0outputs\api_usage.json"
    if exist "%~dp0outputs\applications" rmdir /s /q "%~dp0outputs\applications"
    if exist "%~dp0outputs\tailored"     rmdir /s /q "%~dp0outputs\tailored"
) else (
    echo [4/5] Keeping applied/pending/failed CSVs ^(pass --hard to wipe these too^)
)

echo [5/5] Verifying venv is intact ...
if not exist "%~dp0venv\Scripts\python.exe" (
    echo.
    echo *** ERROR: venv\Scripts\python.exe is missing.
    echo *** Re-create it with:
    echo ***   python -m venv venv
    echo ***   venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    exit /b 1
)

echo.
echo === Cleaned. Starting OneShot ===
echo === In your browser, hit Ctrl+Shift+R to bypass cached JS/CSS ===
echo.

"%~dp0venv\Scripts\python.exe" "%~dp0run.py"

endlocal
