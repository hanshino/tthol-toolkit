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
