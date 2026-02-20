"""Enumerate running tthola.dat processes and their window handles."""
import psutil
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
            return False   # stop enumeration
        return True

    try:
        win32gui.EnumWindows(_callback, None)
    except Exception:
        pass   # EnumWindows raises if callback returns False (stop signal); ignore
    return found


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
