"""Fixed-location discovery pointer plus the environment header.

app.py picks a random port and (before this module) recorded it only under
--dev, so nothing outside the WebView could reach a release build's API. The
logsetup landing-path fallback also makes the events path unknowable in
advance. runtime.json answers both, from a path that never moves.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any

RUNTIME_SCHEMA = 1


def _default_runtime_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / ".tthol-reader"
    return base / "tthol-reader"


RUNTIME_DIR = _default_runtime_dir()


def runtime_json_path() -> Path:
    return RUNTIME_DIR / "runtime.json"


def _maybe_hex(value: Any) -> str | None:
    return hex(value) if isinstance(value, int) else None


def _maybe_hex_list(values: Any) -> list[str] | None:
    if not isinstance(values, (list, tuple)):
        return None
    return [hex(v) for v in values]


def _knowledge_facts() -> tuple[str, float]:
    from services._paths import bundled

    path = bundled("knowledge.json")
    if not path.exists():
        return ("", 0.0)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:8]
    return (digest, path.stat().st_mtime)


def _items_rows() -> int:
    import sqlite3

    from services._paths import bundled

    db = bundled("tthol.sqlite")
    if not db.exists():
        return -1
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return int(con.execute("SELECT COUNT(*) FROM items").fetchone()[0])
        finally:
            con.close()
    except Exception:
        return -1


def environment_header() -> dict[str, Any]:
    """Everything needed to tell one install apart from another.

    The pointer-chain constants matter most: a game update invalidates them,
    and having them here lets that be confirmed or ruled out at a glance.
    """
    import reader

    from services.backup import APP_VERSION

    sha8, mtime = _knowledge_facts()
    return {
        "app_version": APP_VERSION,
        "python": sys.version.split()[0],
        "os": f"{platform.system()} {platform.version()}",
        "frozen": bool(getattr(sys, "frozen", False)),
        "exe": sys.executable,
        "knowledge_sha8": sha8,
        "knowledge_mtime": mtime,
        "items_rows": _items_rows(),
        # STATIC_BASE / STATIC_OFFSETS are absent from reader.py at present
        # (only the player HP chain survives). getattr keeps the header working
        # either way, and reports None so a reader can tell "no session chain"
        # from "chain is stale".
        "static_base": _maybe_hex(getattr(reader, "STATIC_BASE", None)),
        "static_offsets": _maybe_hex_list(getattr(reader, "STATIC_OFFSETS", None)),
        "player_hp_chain_base": _maybe_hex(getattr(reader, "PLAYER_HP_CHAIN_BASE", None)),
        "player_hp_chain_offsets": _maybe_hex_list(
            getattr(reader, "PLAYER_HP_CHAIN_OFFSETS", None)
        ),
    }


def write_runtime_json(port: int, events_path: Path | str) -> Path:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": RUNTIME_SCHEMA,
        "pid": os.getpid(),
        "port": port,
        "started_at": time.time(),
        "events_path": str(events_path),
        **environment_header(),
    }
    path = runtime_json_path()
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def read_runtime_json() -> dict[str, Any] | None:
    path = runtime_json_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def is_stale(info: dict[str, Any]) -> bool:
    """True when the recorded process is gone.

    A crash leaves the file behind; treating it as stale rather than deleting
    it on read keeps a usable post-mortem pointer to events_path.
    """
    pid = info.get("pid")
    if not isinstance(pid, int):
        return True
    try:
        import psutil

        return not psutil.pid_exists(pid)
    except Exception:
        return False


def clear_runtime_json() -> None:
    try:
        runtime_json_path().unlink(missing_ok=True)
    except Exception:
        pass
