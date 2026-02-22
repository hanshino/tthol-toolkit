# PySide6 Launcher Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace `start.bat`'s console-window-producing update logic with a PySide6 launcher window that shows real-time git pull / pip install output, then hands off to the main GUI — no black window left behind.

**Architecture:** A new `launcher.py` entry point is invoked via `pythonw.exe` (no console). It shows `gui/launcher_window.py`, which runs an `UpdateWorker` QThread that streams subprocess output to the UI. On success, `gui_main.py` is started via `subprocess.Popen` and the launcher exits.

**Tech Stack:** PySide6 (QThread, QProcess-style signals), Python subprocess, existing `gui/theme.py` palette constants for colours.

---

## Task 1: pip output filter helper (TDD first)

This helper decides which pip output lines are worth showing to the user. It's pure logic — no GUI — so we test it first.

**Files:**
- Create: `gui/launcher_window.py` (just the helper for now)
- Create: `tests/test_launcher_window.py`

**Step 1: Write the failing test**

Create `tests/test_launcher_window.py`:

```python
"""Tests for launcher window helpers."""
from gui.launcher_window import format_pip_line


def test_skips_already_satisfied():
    assert format_pip_line("Requirement already satisfied: psutil in ...") is None


def test_skips_empty_line():
    assert format_pip_line("") is None
    assert format_pip_line("   ") is None


def test_keeps_installing_line():
    result = format_pip_line("Installing collected packages: psutil")
    assert result == "Installing collected packages: psutil"


def test_keeps_successfully_installed():
    result = format_pip_line("Successfully installed psutil-7.2.2")
    assert result == "Successfully installed psutil-7.2.2"


def test_keeps_error_line():
    result = format_pip_line("ERROR: Could not find a version that satisfies")
    assert result == "ERROR: Could not find a version that satisfies"


def test_skips_downloading_line():
    assert format_pip_line("Downloading psutil-7.2.2-cp311-win32.whl (380 kB)") is None


def test_strips_trailing_whitespace():
    result = format_pip_line("Successfully installed psutil-7.2.2   \n")
    assert result == "Successfully installed psutil-7.2.2"
```

**Step 2: Run test to verify it fails**

```
uv run pytest tests/test_launcher_window.py -v
```

Expected: `ImportError` — `gui/launcher_window.py` does not exist yet.

**Step 3: Create `gui/launcher_window.py` with just the helper**

```python
"""PySide6 launcher window: shows update progress before starting the main GUI."""

from __future__ import annotations


_SKIP_PREFIXES = (
    "Requirement already satisfied",
    "Downloading ",
    "Using cached ",
    "Obtaining ",
    "  Downloading",
)


def format_pip_line(line: str) -> str | None:
    """Return a cleaned pip output line to display, or None to skip it."""
    stripped = line.strip()
    if not stripped:
        return None
    for prefix in _SKIP_PREFIXES:
        if stripped.startswith(prefix):
            return None
    return stripped
```

**Step 4: Run test to verify it passes**

```
uv run pytest tests/test_launcher_window.py -v
```

Expected: all 7 tests PASS.

**Step 5: Commit**

```bash
git add gui/launcher_window.py tests/test_launcher_window.py
git commit -m "feat: add launcher_window module with pip line filter helper (TDD)"
```

---

## Task 2: UpdateWorker QThread

Add the background thread that runs git pull then pip install, streaming output via Qt signals.

**Files:**
- Modify: `gui/launcher_window.py` — append UpdateWorker class

**Step 1: Append UpdateWorker to `gui/launcher_window.py`**

Add these imports at the top of the file (after `from __future__ import annotations`):

```python
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QThread, Signal
```

Then append the class at the bottom of the file:

```python
class UpdateWorker(QThread):
    """Runs git pull then pip install in a background thread."""

    line_ready = Signal(str)   # a single line to append to the log
    status_changed = Signal(str)  # short status label text
    finished = Signal()        # all steps succeeded
    failed = Signal(str)       # fatal error message (pip install failed)

    def run(self) -> None:
        self._run_git()
        self._run_pip()

    # ── git ──────────────────────────────────────────────────────────────────

    def _run_git(self) -> None:
        self.status_changed.emit("Checking for updates...")

        # Fix detached HEAD (happens on first run after zip extraction)
        check = subprocess.run(
            ["git", "symbolic-ref", "HEAD"],
            capture_output=True,
        )
        if check.returncode != 0:
            self.line_ready.emit("Detached HEAD detected, switching to main branch...")
            subprocess.run(["git", "checkout", "main"], capture_output=True)
            subprocess.run(
                ["git", "branch", "--set-upstream-to=origin/main", "main"],
                capture_output=True,
            )

        # git pull
        proc = subprocess.Popen(
            ["git", "pull", "--ff-only"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for raw_line in proc.stdout:
            self.line_ready.emit(raw_line.rstrip())
        proc.wait()

        if proc.returncode != 0:
            self.line_ready.emit("WARNING: git pull failed. Running with current version.")

    # ── pip ──────────────────────────────────────────────────────────────────

    def _run_pip(self) -> None:
        self.status_changed.emit("Syncing dependencies...")

        req = Path(__file__).parent.parent / "requirements.txt"
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m", "pip", "install",
                "-r", str(req),
                "--progress-bar", "off",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for raw_line in proc.stdout:
            display = format_pip_line(raw_line)
            if display:
                self.line_ready.emit(display)
        proc.wait()

        if proc.returncode != 0:
            self.failed.emit("pip install failed. Cannot start the application.")
            return

        self.finished.emit()
```

**Step 2: Verify the file has no syntax errors**

```
uv run python -c "from gui.launcher_window import UpdateWorker; print('OK')"
```

Expected: `OK`

**Step 3: Run existing tests to make sure nothing broke**

```
uv run pytest tests/test_launcher_window.py -v
```

Expected: all 7 tests still PASS.

**Step 4: Commit**

```bash
git add gui/launcher_window.py
git commit -m "feat: add UpdateWorker QThread to launcher_window"
```

---

## Task 3: LauncherWindow widget

Build the visible window that displays the worker's output.

**Files:**
- Modify: `gui/launcher_window.py` — append LauncherWindow class

**Step 1: Add Qt widget imports at the top of the file**

After the existing PySide6 imports, add:

```python
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt
```

**Step 2: Append LauncherWindow at the bottom of the file**

```python
class LauncherWindow(QWidget):
    """Small update-progress window shown before the main GUI launches."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Tthol Reader")
        self.setFixedSize(460, 220)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowTitleHint)

        icon_path = Path(__file__).parent.parent / "icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._build_ui()
        self._start_worker()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(8)

        self._status = QLabel("Starting...")
        self._status.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self._bar = QProgressBar()
        self._bar.setRange(0, 0)   # indeterminate (marquee)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(200)
        self._log.setFixedHeight(100)
        mono = QFont("Consolas", 8)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._log.setFont(mono)

        layout.addWidget(self._status)
        layout.addWidget(self._bar)
        layout.addWidget(self._log)

    # ── Worker wiring ─────────────────────────────────────────────────────────

    def _start_worker(self) -> None:
        self._worker = UpdateWorker()
        self._worker.line_ready.connect(self._append_log)
        self._worker.status_changed.connect(self._status.setText)
        self._worker.finished.connect(self._on_success)
        self._worker.failed.connect(self._on_failure)
        self._worker.start()

    def _append_log(self, line: str) -> None:
        self._log.appendPlainText(line)
        self._log.verticalScrollBar().setValue(
            self._log.verticalScrollBar().maximum()
        )

    def _on_success(self) -> None:
        self._bar.setRange(0, 1)
        self._bar.setValue(1)
        self._status.setText("Launching...")
        # Launch main GUI as detached process so it survives launcher exit
        subprocess.Popen(
            [sys.executable, str(Path(__file__).parent.parent / "gui_main.py")],
            close_fds=True,
        )
        self.close()

    def _on_failure(self, message: str) -> None:
        self._bar.setRange(0, 1)
        self._bar.setValue(0)
        self._status.setText("Error — cannot start")
        self._append_log(f"\nERROR: {message}")
        # Add a close button so user can dismiss
        btn = QPushButton("Close")
        btn.clicked.connect(self.close)
        self.layout().addWidget(btn)
```

**Step 3: Verify the file has no syntax errors**

```
uv run python -c "from gui.launcher_window import LauncherWindow; print('OK')"
```

Expected: `OK`

**Step 4: Run existing tests**

```
uv run pytest tests/test_launcher_window.py -v
```

Expected: all 7 tests PASS.

**Step 5: Commit**

```bash
git add gui/launcher_window.py
git commit -m "feat: add LauncherWindow widget to launcher_window"
```

---

## Task 4: `launcher.py` entry point

The file that `pythonw.exe` actually runs.

**Files:**
- Create: `launcher.py` (project root)

**Step 1: Create `launcher.py`**

```python
"""Launcher entry point — run via pythonw.exe to avoid a console window.

Sequence:
1. Show LauncherWindow (git pull + pip install with live output).
2. On success, LauncherWindow starts gui_main.py and exits.
"""

import sys
from PySide6.QtWidgets import QApplication
from gui.launcher_window import LauncherWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Tthol Reader")
    window = LauncherWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
```

**Step 2: Verify the file has no syntax errors**

```
uv run python -c "import launcher; print('OK')"
```

Expected: `OK`

**Step 3: Commit**

```bash
git add launcher.py
git commit -m "feat: add launcher.py entry point for pythonw.exe"
```

---

## Task 5: Trim `start.bat`

Remove all update logic from `start.bat`. The bat now only handles UAC elevation, sets up PATH, and calls `pythonw.exe launcher.py`.

**Files:**
- Modify: `start.bat`

**Step 1: Replace the entire contents of `start.bat`**

```bat
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
```

Note: `start ""` is used so that `start.bat` itself exits immediately after spawning `pythonw.exe`. The empty string `""` is the window title argument required by `start`.

**Step 2: Manual smoke test**

> This step cannot be automated — it requires a real game session with the toolkit folder present.
> Double-click `start.bat`. Verify:
> - No black console window appears or disappears immediately
> - The LauncherWindow appears
> - git pull output is shown in the log
> - pip install output is shown (or "Requirement already satisfied" lines are hidden)
> - After completion, the main GUI opens
> - The LauncherWindow closes

**Step 3: Commit**

```bash
git add start.bat
git commit -m "feat: trim start.bat to UAC + pythonw launcher call only"
```

---

## Task 6: Verify and cleanup

**Step 1: Run full test suite**

```
uv run pytest -v
```

Expected: all tests PASS (no regressions).

**Step 2: Check for leftover references to old logic**

```
grep -r "pip install" start.bat
grep -r "git pull" start.bat
```

Expected: no matches (all that logic is now in `UpdateWorker`).

**Step 3: Final commit if any cleanup needed**

```bash
git add -p
git commit -m "chore: cleanup after launcher refactor"
```
