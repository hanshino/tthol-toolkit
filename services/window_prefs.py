"""Window geometry: a default size clamped to the screen, and the last size remembered.

Geometry lives in %APPDATA%\\御心鑒\\window.json beside the snapshot DB so it
survives reinstalls. Everything here is best-effort: a missing or corrupt file
falls back to defaults and nothing raises into startup or shutdown.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

from services._paths import app_root

log = logging.getLogger("tthol.window_prefs")

DEFAULT_SIZE = (1280, 800)
MIN_SIZE = (1024, 700)
SCREEN_FRACTION = 0.9
# A saved rect must overlap some screen by at least this much on both axes,
# otherwise the window would open somewhere the user cannot grab it.
MIN_VISIBLE = 100
# Windows parks minimized windows near (-32000, -32000).
_MINIMIZED_COORD = -10000
_KEYS = ("width", "height", "x", "y")


class ScreenLike(Protocol):
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class Geometry:
    width: int
    height: int
    x: int | None
    y: int | None
    min_size: tuple[int, int]


def default_prefs_path() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) / "御心鑒" if appdata else app_root()
    return base / "window.json"


def load_saved(path: Path) -> dict[str, int] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        log.warning("window prefs unreadable, using defaults: %s", e)
        return None
    try:
        return {k: int(data[k]) for k in _KEYS}
    except (KeyError, TypeError, ValueError):
        log.warning("window prefs malformed, using defaults: %r", data)
        return None


def save(path: Path, width: int, height: int, x: int, y: int) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"width": int(width), "height": int(height), "x": int(x), "y": int(y)}
        path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError as e:
        log.warning("could not save window prefs: %s", e)


def _primary(screens: list[ScreenLike]) -> ScreenLike | None:
    for s in screens:
        if s.x == 0 and s.y == 0:
            return s
    return screens[0] if screens else None


def _visible_on(saved: dict[str, int], s: ScreenLike) -> bool:
    w = min(saved["x"] + saved["width"], s.x + s.width) - max(saved["x"], s.x)
    h = min(saved["y"] + saved["height"], s.y + s.height) - max(saved["y"], s.y)
    return w >= MIN_VISIBLE and h >= MIN_VISIBLE


def compute_geometry(saved: dict[str, int] | None, screens: Iterable[ScreenLike]) -> Geometry:
    screens = list(screens)
    primary = _primary(screens)
    w, h = DEFAULT_SIZE
    if primary is not None:
        w = min(w, int(primary.width * SCREEN_FRACTION))
        h = min(h, int(primary.height * SCREEN_FRACTION))
    min_size = (min(MIN_SIZE[0], w), min(MIN_SIZE[1], h))
    if saved and any(_visible_on(saved, s) for s in screens):
        return Geometry(
            max(saved["width"], min_size[0]),
            max(saved["height"], min_size[1]),
            saved["x"],
            saved["y"],
            min_size,
        )
    return Geometry(w, h, None, None, min_size)


def remember(path: Path, window, maximized: bool = False) -> None:
    """Persist the window's current geometry; called from the closing event.

    A maximized or minimized window keeps the previously saved normal rect.
    """
    if maximized:
        return
    try:
        width, height, x, y = window.width, window.height, window.x, window.y
    except Exception as e:  # the window may already be torn down
        log.warning("could not read window geometry: %s", e)
        return
    if x <= _MINIMIZED_COORD or y <= _MINIMIZED_COORD or width <= 0 or height <= 0:
        return
    save(path, width, height, x, y)
