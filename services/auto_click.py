"""Auto-click manager: per-PID background thread that issues Win32 PostMessageW
clicks on the game window for the merchant-summon button.

Logic ported from ``gui/auto_click_tab.py`` (Qt UI dropped). The original tab
ran a Qt timer with a state machine (merchant click spam -> collect/destroy
buttons -> finish round). The HTTP API only exposes ``interval_seconds`` and
``merchant_idx`` so we implement the simpler recurring-click contract here.
The test_click handler mirrors ``AutoClickTab._on_test_click``.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import threading
import time

from services.api_types import AutoClickConfig, AutoClickStatus
from services.process_detector import _hwnd_for_pid

user32 = ctypes.windll.user32

# Win32 messages
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001

# Reference coordinate space: 800x600 (game's logical resolution).
# At click time, coordinates are scaled to the actual client rect size
# so clicks work at any DPI and any window size.
REF_WIDTH = 800
REF_HEIGHT = 600

MERCHANT_COORDS: list[tuple[int, int]] = [
    (167, 333),  # Merchant 1
    (283, 333),  # Merchant 2
    (400, 333),  # Merchant 3
    (517, 333),  # Merchant 4
    (633, 333),  # Merchant 5
]


def _scale_coord(hwnd: int, ref_x: int, ref_y: int) -> tuple[int, int]:
    """Scale reference 800x600 coordinates to the actual client rect size."""
    rect = ctypes.wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    actual_w = rect.right - rect.left
    actual_h = rect.bottom - rect.top
    if actual_w <= 0 or actual_h <= 0:
        return ref_x, ref_y
    x = int(ref_x * actual_w / REF_WIDTH)
    y = int(ref_y * actual_h / REF_HEIGHT)
    return x, y


def background_click(hwnd: int, ref_x: int, ref_y: int) -> None:
    """Single background click with a 50ms gap between WM_LBUTTONDOWN/UP.

    ``ref_x``/``ref_y`` are in 800x600 reference space and are scaled to the
    actual client rect size before posting.
    """
    if not hwnd:
        return
    x, y = _scale_coord(hwnd, ref_x, ref_y)
    lparam = (y << 16) | (x & 0xFFFF)
    user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
    time.sleep(0.05)
    user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lparam)


def _resolve_hwnd(pid: int) -> int:
    """Look up the top-level HWND for a PID; returns 0 if not found."""
    try:
        return _hwnd_for_pid(pid)
    except Exception:
        return 0


class _Job:
    def __init__(self, pid: int, config: AutoClickConfig) -> None:
        self.pid = pid
        self.config = config
        self.started_at = time.time()
        self.last_click_at: float | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> AutoClickStatus:
        return AutoClickStatus(
            running=self._thread.is_alive() and not self._stop.is_set(),
            started_at=self.started_at,
            runtime_seconds=int(time.time() - self.started_at),
            last_click_at=self.last_click_at,
        )

    def _run(self) -> None:  # pragma: no cover - Win32 only
        """Recurring background click on the configured merchant button.

        Resolves HWND once at start; if the window goes away we keep retrying
        on every tick so a brief disconnect does not kill the job.
        """
        merchant_idx = self.config.merchant_idx
        if merchant_idx < 0 or merchant_idx >= len(MERCHANT_COORDS):
            merchant_idx = 0
        ref_x, ref_y = MERCHANT_COORDS[merchant_idx]
        interval = max(0.05, float(self.config.interval_seconds))

        hwnd = _resolve_hwnd(self.pid)
        while not self._stop.is_set():
            if not hwnd:
                hwnd = _resolve_hwnd(self.pid)
            if hwnd:
                try:
                    background_click(hwnd, ref_x, ref_y)
                    self.last_click_at = time.time()
                except Exception:
                    # Best-effort: window may have closed mid-click; force re-resolve.
                    hwnd = 0
            # Wait interval but break out promptly on stop.
            self._stop.wait(interval)


class AutoClickManager:
    """Manage one auto-click job per PID."""

    def __init__(self) -> None:
        self._jobs: dict[int, _Job] = {}

    def start(self, pid: int, config: AutoClickConfig) -> None:
        # Stop any prior job for this PID before starting a new one.
        prior = self._jobs.pop(pid, None)
        if prior is not None:
            prior.stop()
        job = _Job(pid, config)
        self._jobs[pid] = job
        job.start()

    def stop(self, pid: int) -> None:
        job = self._jobs.pop(pid, None)
        if job is not None:
            job.stop()

    def test_click(self, pid: int, merchant_idx: int) -> None:  # pragma: no cover - Win32 only
        """One-off click for coordinate debugging (mirrors ``_on_test_click``)."""
        if merchant_idx < 0 or merchant_idx >= len(MERCHANT_COORDS):
            merchant_idx = 0
        hwnd = _resolve_hwnd(pid)
        if not hwnd:
            return
        ref_x, ref_y = MERCHANT_COORDS[merchant_idx]
        background_click(hwnd, ref_x, ref_y)

    def status(self, pid: int) -> AutoClickStatus:
        job = self._jobs.get(pid)
        if job is None:
            return AutoClickStatus(running=False)
        return job.status()
