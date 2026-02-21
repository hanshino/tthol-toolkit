"""Entry point for the PySide6 GUI."""

import sys
from PySide6.QtWidgets import QApplication
from gui.main_window import MainWindow
from gui.theme import ThemeManager
from gui.config import load_theme


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Tthol Reader")
    ThemeManager.apply(app, load_theme())
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
