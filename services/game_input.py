"""Background keyboard / mouse input to a game window (PostMessage, the real
cursor does not move). Only for what the hook has no command for: the user
chose plain clicks for leaving the game (2026-10-05).

Verified live 2026-10-05: a posted Esc opens the system menu; a click on its
buttons registers when a WM_MOUSEMOVE to the spot comes first (a bare
down/up pair did not).

The game is DPI-unaware: it always works in its own 800x600 client, and
Windows only stretches the picture (to 1200x900 at 150 %). A DPI-aware caller
sees the stretched size, so everything here runs with the thread switched to
DPI-unaware: the client rect then reads the game's own size and coordinates
are game coordinates, whatever the app process's DPI mode is.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import time

import contextlib

from services.auto_click import REF_HEIGHT, REF_WIDTH, _resolve_hwnd

user32 = ctypes.windll.user32

WM_KEYDOWN, WM_KEYUP, WM_MOUSEMOVE = 0x0100, 0x0101, 0x0200
WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON = 0x0201, 0x0202, 0x0001
VK_ESCAPE = 0x1B
LOGOUT_BUTTON = (399, 280)  # 登出遊戲 in the Esc menu
BACK_BUTTON = (399, 319)  # 回到遊戲
MENU_WAIT = 0.8  # for the Esc menu to open


DPI_UNAWARE = ctypes.c_void_p(-1)  # DPI_AWARENESS_CONTEXT_UNAWARE
user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]


@contextlib.contextmanager
def _game_dpi():
    """Run as DPI-unaware, like the game window, then restore."""
    old = user32.SetThreadDpiAwarenessContext(DPI_UNAWARE)
    try:
        yield
    finally:
        if old:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(old))


def _game_point(hwnd: int, ref_x: int, ref_y: int) -> int:
    """lParam for a point given in 800x600 game coordinates (call inside _game_dpi)."""
    rect = ctypes.wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    x = int(ref_x * w / REF_WIDTH) if w > 0 else ref_x
    y = int(ref_y * h / REF_HEIGHT) if h > 0 else ref_y
    return (y << 16) | (x & 0xFFFF)


def press_escape(hwnd: int) -> None:
    scan = user32.MapVirtualKeyW(VK_ESCAPE, 0)
    user32.PostMessageW(hwnd, WM_KEYDOWN, VK_ESCAPE, 1 | (scan << 16))
    time.sleep(0.05)
    user32.PostMessageW(hwnd, WM_KEYUP, VK_ESCAPE, 1 | (scan << 16) | (0xC0 << 24))


def click_button(hwnd: int, ref_x: int, ref_y: int) -> None:
    """Move onto the spot first: the menu buttons ignore a bare click."""
    with _game_dpi():
        lp = _game_point(hwnd, ref_x, ref_y)
        user32.PostMessageW(hwnd, WM_MOUSEMOVE, 0, lp)
        time.sleep(0.1)
        user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp)
        time.sleep(0.05)
        user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lp)


def leave_game(pid: int) -> bool:
    """Esc -> 登出遊戲. False when the game window cannot be found."""
    hwnd = _resolve_hwnd(pid)
    if not hwnd:
        return False
    press_escape(hwnd)
    time.sleep(MENU_WAIT)
    click_button(hwnd, *LOGOUT_BUTTON)
    return True
