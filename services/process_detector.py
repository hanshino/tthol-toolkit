"""Enumerate running tthola.dat processes and their window handles."""

import ctypes
import psutil
import win32con
import win32gui
import win32process

PROCESS_NAME = "tthola.dat"  # exact case as reported by psutil on Windows


def _hwnd_for_pid(target_pid: int) -> int:
    """Return the first visible top-level HWND belonging to target_pid, or 0."""
    found = 0

    def _callback(hwnd, _):
        nonlocal found
        if not win32gui.IsWindowVisible(hwnd):
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid == target_pid:
            found = hwnd
            return False  # stop enumeration
        return True

    try:
        win32gui.EnumWindows(_callback, None)
    except Exception:
        pass  # EnumWindows raises if callback returns False (stop signal); ignore
    return found


def bring_window_to_front(hwnd: int) -> None:
    """Reliably bring hwnd to the foreground using AttachThreadInput workaround.

    Plain SetForegroundWindow is blocked by Windows when the calling process is
    not the current foreground process.  Temporarily attaching the input queue
    of the foreground thread to the target thread bypasses this restriction.
    """
    if not hwnd:
        return
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

        fg_hwnd = win32gui.GetForegroundWindow()
        fg_tid = win32process.GetWindowThreadProcessId(fg_hwnd)[0] if fg_hwnd else 0
        target_tid = win32process.GetWindowThreadProcessId(hwnd)[0]

        if fg_tid and fg_tid != target_tid:
            ctypes.windll.user32.AttachThreadInput(fg_tid, target_tid, True)
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
            ctypes.windll.user32.AttachThreadInput(fg_tid, target_tid, False)
        else:
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass  # best-effort; do not crash if window state changes mid-call


def detect_game_windows() -> list[tuple[int, int, str]]:
    """
    Return list of (pid, hwnd, label) for all running tthola.dat processes,
    sorted by pid. Labels are "視窗 1", "視窗 2", etc.
    """
    pids = sorted(
        proc.info["pid"]
        for proc in psutil.process_iter(["pid", "name"])
        if proc.info["name"] == PROCESS_NAME
    )
    result = []
    for i, pid in enumerate(pids, start=1):
        hwnd = _hwnd_for_pid(pid)
        result.append((pid, hwnd, f"視窗 {i}"))
    return result
