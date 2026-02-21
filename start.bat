@echo off
setlocal EnableDelayedExpansion

:: ── UAC elevation ────────────────────────────────────────────────────
:: If not running as admin, re-launch with elevation and exit.
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

:: ── Paths ─────────────────────────────────────────────────────
set "_root=%~dp0"
set "PATH=%_root%toolkit\python;%_root%toolkit\python\Scripts;%_root%toolkit\git\cmd;%PATH%"
set "PYTHONPATH=%_root%"

cd /d "%_root%"

:: ── Update ────────────────────────────────────────────────────
title Tthol Reader - Updating...
echo [1/2] Pulling latest code...

:: If in detached HEAD (e.g. first run after extracting release zip), switch to main.
git symbolic-ref HEAD >nul 2>&1
if %errorlevel% neq 0 (
    echo Detached HEAD detected, switching to main branch...
    git checkout main >nul 2>&1
    if %errorlevel% neq 0 (
        git checkout -b main origin/main >nul 2>&1
    )
    git branch --set-upstream-to=origin/main main >nul 2>&1
)

git pull --ff-only
if %errorlevel% neq 0 (
    echo.
    echo WARNING: git pull failed. Running with current version.
    echo Press any key to continue anyway...
    pause >nul
)

echo [2/2] Syncing dependencies...
python -m pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo.
    echo ERROR: pip install failed. Cannot continue.
    pause
    exit /b 1
)

:: ── Launch ────────────────────────────────────────────────────
title Tthol Reader
python gui_main.py
if %errorlevel% neq 0 (
    echo.
    echo Application exited with an error.
    pause
)
