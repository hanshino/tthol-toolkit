"""PySide6 launcher window: shows update progress before starting the main GUI."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


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
        self._bar.setRange(0, 0)  # indeterminate (marquee)
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
        self._log.verticalScrollBar().setValue(self._log.verticalScrollBar().maximum())

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
