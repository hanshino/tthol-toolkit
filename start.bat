@echo off
setlocal

REM UAC elevation: re-launch with elevation if not already admin.
net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

REM Paths.
set "_root=%~dp0"
set "PATH=%_root%toolkit\python;%_root%toolkit\python\Scripts;%PATH%"
set "PYTHONPATH=%_root%"

cd /d "%_root%"

REM Launch bootstrap (pythonw = no console window).
start "" "%_root%toolkit\python\pythonw.exe" "%_root%bootstrap.py"
