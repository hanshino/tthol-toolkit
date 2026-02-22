@echo off
setlocal EnableDelayedExpansion

:: ── UAC elevation ─────────────────────────────────────────────────────────
:: If not running as admin, re-launch with elevation and exit.
net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

:: ── Paths ──────────────────────────────────────────────────────────────────
set "_root=%~dp0"
set "PATH=%_root%toolkit\python;%_root%toolkit\python\Scripts;%_root%toolkit\git\cmd;%PATH%"
set "PYTHONPATH=%_root%"

cd /d "%_root%"

:: ── Launch launcher (pythonw = no console window) ─────────────────────────
start "" "%_root%toolkit\python\pythonw.exe" "%_root%launcher.py"
