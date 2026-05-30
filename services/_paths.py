"""Path resolution helpers that work in both dev and PyInstaller frozen layouts.

`app_root()` — directory containing the .exe (or the repo root in dev).
    Use for files that must live at the user-visible top level: user data
    (e.g. the legacy tthol_inventory.db), the install root itself.

`bundled(*parts)` — read-only data files shipped inside the bundle.
    Resolves to PyInstaller's _MEIPASS (i.e. _internal/) when frozen,
    repo root when running from source. Use for assets included via the
    spec's `datas` list: webui/dist, knowledge.json, tthol.sqlite.
"""

from __future__ import annotations

import sys
from pathlib import Path


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundled(*parts: str) -> Path:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass).joinpath(*parts)
    return Path(__file__).resolve().parent.parent.joinpath(*parts)
