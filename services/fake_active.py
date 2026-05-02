"""
Keep the game window rendering when not focused.

Installs a WinEvent hook to detect foreground changes, then sends
fake activation messages to counter deactivation. Works within the
PySide6 main thread's message loop (no separate pump needed).
"""

import ctypes
import ctypes.wintypes

user32 = ctypes.windll.user32

# Windows messages
WM_ACTIVATEAPP = 0x001C
WM_ACTIVATE = 0x0006
WM_SETFOCUS = 0x0007
WM_NCACTIVATE = 0x0086
WA_ACTIVE = 1

# WinEvent constants
EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000

# Callback type for SetWinEventHook
WINEVENTPROC = ctypes.WINFUNCTYPE(
    None,
    ctypes.wintypes.HANDLE,  # hWinEventHook
    ctypes.wintypes.DWORD,  # event
    ctypes.wintypes.HWND,  # hwnd
    ctypes.c_long,  # idObject
    ctypes.c_long,  # idChild
    ctypes.wintypes.DWORD,  # idEventThread
    ctypes.wintypes.DWORD,  # dwmsEventTime
)


def _send_fake_active(hwnd: int) -> None:
    """Send full suite of activation messages to counter deactivation."""
    user32.SendMessageW(hwnd, WM_NCACTIVATE, 1, 0)
    user32.SendMessageW(hwnd, WM_ACTIVATEAPP, 1, 0)
    user32.SendMessageW(hwnd, WM_ACTIVATE, WA_ACTIVE, 0)
    user32.SendMessageW(hwnd, WM_SETFOCUS, 0, 0)


class FakeActiveKeeper:
    """Keeps a game window rendering by faking activation on focus loss.

    Must be created and used from the main (GUI) thread so the WinEvent
    hook callback fires via Qt's message loop.
    """

    def __init__(self):
        self._hwnd: int = 0
        self._hook = None
        # prevent GC of the callback
        self._callback = WINEVENTPROC(self._on_foreground_change)

    def start(self, hwnd: int) -> None:
        """Start keeping *hwnd* active. Stops any previous hook first."""
        self.stop()
        if not hwnd:
            return
        self._hwnd = hwnd
        self._hook = user32.SetWinEventHook(
            EVENT_SYSTEM_FOREGROUND,
            EVENT_SYSTEM_FOREGROUND,
            None,
            self._callback,
            0,
            0,
            WINEVENT_OUTOFCONTEXT,
        )

    def stop(self) -> None:
        """Unhook and stop keeping the window active."""
        if self._hook:
            user32.UnhookWinEvent(self._hook)
            self._hook = None
        self._hwnd = 0

    @property
    def active(self) -> bool:
        return self._hook is not None and self._hwnd != 0

    def _on_foreground_change(
        self, hWinEventHook, event, hwnd, idObject, idChild, idEventThread, dwmsEventTime
    ):
        if self._hwnd and event == EVENT_SYSTEM_FOREGROUND and hwnd != self._hwnd:
            _send_fake_active(self._hwnd)
