"""The character's family, from the hook's inbound 0x31.

The server sends 0x31 (family summary) when the family window opens in game,
in the same millisecond as 0x3D (notice) and 0x32 (member list). It is not
known to arrive on login or a map change, so the last one seen is kept per
character name in the settings table and shown until a newer one comes.

Layout, offsets from the sub-type byte (tthol-hook capture of 2026-10-05,
checked in game by the user: 王者、天下, level 12, 天巧莊 1121, 72/90):

    +1   family name, Big5, NUL-terminated
    +33  u16 members
    +35  u16 member cap
    +41  u8  family level
    +42  u16 manor = stages.kind 'sestage' id (1121-1124 are all 天巧莊)

The manor is what the 家族馬夫 menus test (trigger condition C84 compares it),
so the route planner reads it from here.
"""

from __future__ import annotations

import logging
import sqlite3
import struct
import threading
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

from services._paths import bundled
from services.api_types import FamilyInfo
from services.hook_hub import _cstr

log = logging.getLogger("tthol.family")

FAMILY_PACKET = 0x31
SECTION = "family"
NAME_END = 33
MIN_LEN = 44


@lru_cache(maxsize=1)
def _manor_names(db_path: Path | None = None) -> dict[int, str]:
    path = db_path or bundled("tthol.sqlite")
    if not path.exists():
        return {}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return dict(con.execute("SELECT id, name FROM stages WHERE kind = 'sestage'"))
    finally:
        con.close()


def decode_family(raw: bytes, ts: float) -> FamilyInfo | None:
    if len(raw) < MIN_LEN or raw[0] != FAMILY_PACKET:
        return None
    members, cap = struct.unpack_from("<HH", raw, 33)
    level = raw[41]
    (manor,) = struct.unpack_from("<H", raw, 42)
    return FamilyInfo(
        name=_cstr(raw[1:NAME_END]),
        level=level,
        manor_id=manor,
        manor_name=_manor_names().get(manor),
        members=members,
        member_cap=cap,
        received_at=ts,
    )


class FamilyTracker:
    """Last family summary per character, live from 0x31 and saved by name."""

    def __init__(self, character_name: Callable[[int], str | None], db=None) -> None:
        self._name = character_name
        self._db = db  # anything with get_setting / set_setting (SnapshotDB)
        self._lock = threading.Lock()
        self._by_name: dict[str, FamilyInfo | None] = {}

    def on_packet(self, pid: int, raw: bytes, ts: float, _own: bytes | None) -> None:
        info = decode_family(raw, ts)
        name = self._name(pid)
        if info is None or not name:
            return
        with self._lock:
            self._by_name[name] = info
        log.info(
            "family pid=%d %s lv%d manor=%d %d/%d",
            pid,
            info.name,
            info.level,
            info.manor_id,
            info.members,
            info.member_cap,
            extra={"cat": "hook"},
        )
        if self._db is None:
            return
        try:
            self._db.set_setting(name, SECTION, info.model_dump(mode="json"))
        except Exception:
            log.exception("family save failed pid=%d", pid, extra={"cat": "hook"})

    def get(self, name: str | None) -> FamilyInfo | None:
        if not name:
            return None
        with self._lock:
            if name in self._by_name:
                return self._by_name[name]
        info = self._load(name)
        with self._lock:
            self._by_name.setdefault(name, info)
            return self._by_name[name]

    def _load(self, name: str) -> FamilyInfo | None:
        raw = self._db.get_setting(name, SECTION) if self._db is not None else None
        if not raw:
            return None
        try:
            return FamilyInfo.model_validate(raw)
        except ValueError:
            return None
