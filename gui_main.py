"""Entry point for the PySide6 GUI."""

import sys
import ctypes
import ctypes.wintypes
from PySide6.QtWidgets import QApplication
from gui.main_window import MainWindow
from gui.theme import ThemeManager
from gui.config import load_theme

_MUTEX_NAME = "TtholReaderSingleInstance"


def _acquire_mutex():
    """Create a named mutex. Returns the handle, or None if another instance is running."""
    handle = ctypes.windll.kernel32.CreateMutexW(None, True, _MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.kernel32.CloseHandle(handle)
        return None
    return handle


def _bring_existing_to_front():
    """Find the existing MainWindow by title and bring it to the foreground."""
    hwnd = ctypes.windll.user32.FindWindowW(None, "武林小幫手")
    if not hwnd:
        # Fallback: partial match via EnumWindows
        target = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        def _enum_cb(hwnd, _):
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
            if "武林小幫手" in buf.value or "Tthol" in buf.value:
                target.append(hwnd)
                return False
            return True

        ctypes.windll.user32.EnumWindows(_enum_cb, 0)
        hwnd = target[0] if target else None

    if hwnd:
        # Restore if minimized, then bring to front
        SW_RESTORE = 9
        ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
        ctypes.windll.user32.SetForegroundWindow(hwnd)


def main():
    mutex = _acquire_mutex()
    if mutex is None:
        # Another instance is already running — just raise it.
        _bring_existing_to_front()
        sys.exit(0)

    try:
        app = QApplication(sys.argv)
        app.setApplicationName("Tthol Reader")
        ThemeManager.apply(app, load_theme())
        window = MainWindow()
        window.show()
        sys.exit(app.exec())
    finally:
        ctypes.windll.kernel32.ReleaseMutex(mutex)
        ctypes.windll.kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    main()
