"""PySide6 launcher window: shows update progress before starting the main GUI."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QThread, Signal


_SKIP_PREFIXES = (
    "Requirement already satisfied",
    "Downloading ",
    "Using cached ",
    "Obtaining ",
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


class UpdateWorker(QThread):
    """Runs git pull then pip install in a background thread."""

    line_ready = Signal(str)  # a single line to append to the log
    status_changed = Signal(str)  # short status label text
    finished = Signal()  # all steps succeeded
    failed = Signal(str)  # fatal error message (pip install failed)

    def run(self) -> None:
        try:
            self._run_git()
            self._run_pip()
        except Exception as exc:
            self.failed.emit(f"Unexpected error: {exc}")

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
                "-m",
                "pip",
                "install",
                "-r",
                str(req),
                "--progress-bar",
                "off",
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
