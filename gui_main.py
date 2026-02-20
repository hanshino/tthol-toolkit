"""Entry point for the PySide6 GUI."""
import sys
from PySide6.QtWidgets import QApplication
from gui.main_window import MainWindow
from gui.theme import DARK_QSS


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Tthol Reader")
    app.setStyleSheet(DARK_QSS)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
