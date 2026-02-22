# Launcher Design: PySide6 GUI Launcher to Replace start.bat Console Window

**Date:** 2026-02-22
**Status:** Approved

## Problem

Two related UX issues with the current `start.bat` approach:

1. **Black console window persists** — After the GUI opens, the cmd window stays alive (it must, to keep the process running). Users cannot close it without killing the app.
2. **pip install is silent** — First-time install with `-q` flag shows nothing. Users think the app is frozen.

## Solution

Replace the heavy `start.bat` startup logic with a small PySide6 `launcher.py`, invoked via `pythonw.exe` (which has no console window). The launcher window handles git pull and pip install with real-time output, then hands off to the main GUI.

## Architecture

```
start.bat  (trimmed to ~10 lines: UAC elevation only)
  └─→ toolkit\python\pythonw.exe launcher.py
        ├─ Shows LauncherWindow immediately
        ├─ Worker thread: git pull  → streams output to window
        ├─ Worker thread: pip install → streams output to window
        └─ On success: subprocess gui_main.py, then self.close()
```

## Components

### `start.bat` (trimmed)

Responsibilities reduced to:
- UAC elevation via PowerShell if not already admin
- Set PATH to `toolkit\python` and `toolkit\git\cmd`
- Run `toolkit\python\pythonw.exe launcher.py`
- Exit (does not wait for launcher)

### `launcher.py` (new file, project root)

A standalone PySide6 application (~150 lines):
- Creates `QApplication` and shows `LauncherWindow`
- Runs update steps sequentially in a `QThread` worker
- On success, launches `gui_main.py` via `subprocess.Popen` and exits

### `gui/launcher_window.py` (new file)

`LauncherWindow(QWidget)`:
- Fixed small window (~420×200px), no resize, no taskbar entry (`Qt.Tool`)
- Centered on screen
- Shows: app icon, title, status label, progress bar, scrolling log output
- Emits signals from worker thread to update UI safely

### Worker Thread

Runs sequentially:
1. `git pull --ff-only` — captures stdout/stderr line by line, emits to log
2. `pip install -r requirements.txt --progress-bar on` — captures output line by line, emits to log
3. Emits `finished` signal on success or `error` signal on failure

### Error Handling

- `git pull` failure: show warning in log, continue to pip install (non-blocking, same as current behavior)
- `pip install` failure: show error dialog, do not launch main GUI (blocking, same as current behavior)

## UI Layout

```
┌──────────────────────────────────────────┐
│  [icon]  Tthol Reader                    │
│                                          │
│  正在檢查更新...                          │
│  ████████████░░░░░░░░  60%              │
│                                          │
│  > Already up to date.                  │
│  > Installing psutil-7.2.2...           │
│  > Successfully installed psutil-7.2.2  │
└──────────────────────────────────────────┘
```

- Log area: read-only, auto-scrolls to bottom, monospace font, max ~4 visible lines
- Progress bar: indeterminate during git pull, step-based during pip install

## pip install Progress

Remove the `-q` flag. Use `--progress-bar on` and parse output lines:
- Lines matching `Installing ...` increment the progress bar
- Lines matching `Successfully installed` shown in log
- All output streamed in real time (no buffering)

## What Does NOT Change

- UAC elevation logic stays in `start.bat`
- PATH setup (`toolkit\python`, `toolkit\git\cmd`) stays in `start.bat`
- `gui_main.py` is unchanged — launcher just `Popen`s it and exits
- GitHub Actions release workflow unchanged
- `requirements.txt` unchanged

## Trade-offs Considered

| Approach | Verdict |
|---|---|
| VBScript wrapper | Rejected — Windows security warnings, not professional |
| Compiled Go/C# EXE | Rejected — adds language dependency to CI/CD |
| pythonw + PySide6 launcher | **Chosen** — uses existing stack, no new dependencies |

## Files Changed

| File | Change |
|---|---|
| `start.bat` | Trim to ~10 lines (UAC + PATH + pythonw call) |
| `launcher.py` | New — entry point, creates QApplication |
| `gui/launcher_window.py` | New — LauncherWindow widget + worker thread |
