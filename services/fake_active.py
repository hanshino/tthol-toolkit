"""Keep the game window rendering when not focused.

Each PID gets a daemon thread that owns its own Win32 message pump and
installs a WinEvent hook on EVENT_SYSTEM_FOREGROUND. When the foreground
window changes away from the game we send a synthetic activation suite
so the game keeps rendering.

The pump thread terminates by receiving WM_QUIT via PostThreadMessageW —
this works because GetMessageW returns 0 on WM_QUIT.

Note: SetWinEventHook with WINEVENT_OUTOFCONTEXT requires the calling
thread to run a message loop, otherwise the callback is never invoked.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import threading
import time

from services.api_types import KeepActiveStatus
from services.process_detector import _hwnd_for_pid

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Window messages
WM_ACTIVATEAPP = 0x001C
WM_ACTIVATE = 0x0006
WM_SETFOCUS = 0x0007
WM_NCACTIVATE = 0x0086
WM_QUIT = 0x0012
WA_ACTIVE = 1

# WinEvent constants
EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000

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


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.wintypes.HWND),
        ("message", ctypes.wintypes.UINT),
        ("wParam", ctypes.wintypes.WPARAM),
        ("lParam", ctypes.wintypes.LPARAM),
        ("time", ctypes.wintypes.DWORD),
        ("pt_x", ctypes.c_long),
        ("pt_y", ctypes.c_long),
    ]


def _send_fake_active(hwnd: int) -> None:
    """Send full suite of activation messages to counter deactivation."""
    user32.SendMessageW(hwnd, WM_NCACTIVATE, 1, 0)
    user32.SendMessageW(hwnd, WM_ACTIVATEAPP, 1, 0)
    user32.SendMessageW(hwnd, WM_ACTIVATE, WA_ACTIVE, 0)
    user32.SendMessageW(hwnd, WM_SETFOCUS, 0, 0)


class _Job:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.started_at = time.time()
        self.last_send_at: float | None = None
        self._hwnd = 0
        self._thread_id = 0
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()
        # Block until the pump has its thread id, otherwise stop() races
        # with a not-yet-pumping thread and the WM_QUIT post would no-op.
        self._ready.wait(timeout=2.0)

    def stop(self) -> None:
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self._thread.join(timeout=2.0)

    def alive(self) -> bool:
        return self._thread.is_alive()

    def status(self) -> KeepActiveStatus:
        return KeepActiveStatus(
            running=self.alive(),
            started_at=self.started_at,
            runtime_seconds=int(time.time() - self.started_at),
            last_send_at=self.last_send_at,
        )

    def _on_foreground_change(
        self, hWinEventHook, event, hwnd, idObject, idChild, idEventThread, dwmsEventTime
    ):
        if self._hwnd and event == EVENT_SYSTEM_FOREGROUND and hwnd != self._hwnd:
            try:
                _send_fake_active(self._hwnd)
                self.last_send_at = time.time()
            except Exception:
                pass

    def _run(self) -> None:  # pragma: no cover - Win32 only
        try:
            self._hwnd = _hwnd_for_pid(self.pid)
        except Exception:
            self._hwnd = 0
        if not self._hwnd:
            self._ready.set()
            return

        # Pin the bound callback so ctypes doesn't GC it while the hook
        # is live; unhooking is not enough — Windows can still call back
        # before UnhookWinEvent finishes draining queued events.
        callback = WINEVENTPROC(self._on_foreground_change)
        hook = user32.SetWinEventHook(
            EVENT_SYSTEM_FOREGROUND,
            EVENT_SYSTEM_FOREGROUND,
            None,
            callback,
            0,
            0,
            WINEVENT_OUTOFCONTEXT,
        )
        if not hook:
            self._ready.set()
            return

        self._thread_id = kernel32.GetCurrentThreadId()
        self._ready.set()

        msg = _MSG()
        try:
            while True:
                ret = user32.GetMessageW(ctypes.byref(msg), 0, 0, 0)
                if ret <= 0:  # 0 = WM_QUIT, -1 = error
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.UnhookWinEvent(hook)


class KeepActiveManager:
    """Per-PID activation keeper."""

    def __init__(self) -> None:
        self._jobs: dict[int, _Job] = {}
        self._lock = threading.Lock()

    def start(self, pid: int) -> None:
        with self._lock:
            prior = self._jobs.pop(pid, None)
        if prior is not None:
            prior.stop()
        job = _Job(pid)
        job.start()
        with self._lock:
            self._jobs[pid] = job

    def stop(self, pid: int) -> None:
        with self._lock:
            job = self._jobs.pop(pid, None)
        if job is not None:
            job.stop()

    def status(self, pid: int) -> KeepActiveStatus:
        with self._lock:
            job = self._jobs.get(pid)
        if job is None:
            return KeepActiveStatus(running=False)
        return job.status()
