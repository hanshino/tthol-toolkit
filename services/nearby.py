"""Live characters around the player, read from the client's sprite table.

Read-only and on demand: the 周遭 list and the minimap's live mode poll
/api/characters/{pid}/nearby about once a second while they are open. The
client only holds objects in or near view, so this is the game screen's
surroundings, not the whole map.
"""

from __future__ import annotations

import functools
import sqlite3
import struct

import reader
from services._paths import bundled
from services.api_types import Nearby, NearbyEntity

DB_PATH = bundled("tthol.sqlite")
PLAYER_NPC_IDS = range(60001, 60011)  # npc rows 劍客男 .. 道士女: the player classes


@functools.lru_cache(maxsize=1)
def _npc_table() -> dict[int, tuple[str | None, int | None, bool]]:
    """npc.id -> (name, level, is_monster), loaded once."""
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.text_factory = lambda b: b.decode("utf-8", errors="replace")
    try:
        rows = con.execute("SELECT id, name, level, is_monster FROM npc").fetchall()
    finally:
        con.close()
    return {i: (name, level, bool(monster)) for i, name, level, monster in rows}


def read_nearby(
    pm, hp_addr, _compat_mode
) -> tuple[list[reader.NearbyObject], tuple[int, int] | None]:
    """WorkerManager.read_locked reader: the sprite table plus the own tile."""
    objects = reader.scan_nearby(pm, hp_addr)
    try:
        own = struct.unpack("<ii", pm.read_bytes(hp_addr + reader.TILE_X_OFFSET, 8))
    except Exception:
        own = None
    if own is not None and min(own) < 0:
        own = None  # -1 right after a map change, until the first step
    return objects, own


def build(objects, own_tile, npcs=None) -> Nearby:
    """Classify and place the raw objects; nearest first: players, followers, monsters, NPCs."""
    npcs = _npc_table() if npcs is None else npcs
    entities = []
    for o in objects:
        if o.is_self:
            continue
        name, level, is_monster = npcs.get(o.npc_id, (None, None, False))
        if o.npc_id in PLAYER_NPC_IDS:
            kind, name, level = "player", o.name, None
        elif o.tag:
            kind = "follower"  # flagged is_monster in the npc table, but owned by a player
        elif is_monster:
            kind = "monster"
        else:
            kind, level = "npc", None
        x, y = o.px // reader.TILE_PX, o.py // reader.TILE_PX
        distance = max(abs(x - own_tile[0]), abs(y - own_tile[1])) if own_tile else None
        entities.append(
            NearbyEntity(
                kind=kind,
                handle=o.handle,
                npc_id=o.npc_id,
                instance=o.instance,
                # Followers carry their own display name (～絳雪～ for npc ●絳雪).
                name=(o.name or name) if kind == "follower" else (name or o.name),
                level=level,
                hp_pct=max(0, min(100, o.hp_pct)) if kind == "monster" else None,
                stalling=kind == "player" and o.state == reader.CHAR_STATE_STALLING,
                family=o.tag if kind == "player" else None,
                owner=o.tag if kind == "follower" else None,
                px=o.px,
                py=o.py,
                x=x,
                y=y,
                distance=distance,
            )
        )
    order = {"player": 0, "follower": 1, "monster": 2, "npc": 3}
    entities.sort(
        key=lambda e: (order[e.kind], e.distance if e.distance is not None else 1 << 30, e.handle)
    )
    return Nearby(entities=entities)
