"""Areas, portals and spawn groups of one map, read from the game DB.

An area is one 8-way connected part of the map's walkable cells
(map_walkability): a room or a hall that only portals connect. Spawn groups,
portal zones, landing tiles and NPCs are placed in the area that holds them, so
a module can tell from the own tile where it is and what it can do there.

A portal is a zone event (map_events bind 'zone') whose trigger moves the
character: A3 (another map) or A9 (a point on this map). Its C37 conditions
say which spawn groups must be all dead, or still alive, for it to work.
Other conditions (map variables, missions, items) are only flagged: the client
cannot read them.

Tiles here are game tiles: x to the east, y to the north (the HP+416/+420
tile, `near` pixels / 40). The walk mask's row 0 is the north edge, so
row = height - 1 - y. Placements are read from raw_x / raw_y (pixels, y down
from the north edge), not tile_y: tile_y = height - round(raw_y / 40) is one
tile too far north whenever raw_y sits in the top half of its cell. Live
2026-10-09: 凌霄閣's last exit (raw 4164, 3844) is (104, 8), tile_y says 9, and
the character stood on (104, 9) without it firing; landings seen after a warp
(杭州城 (29, 3) / (39, 108), a manor (11, 32)) all match the raw cell, tile_y
is one north of each.
"""

from __future__ import annotations

import functools
import json
import sqlite3
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from services._paths import bundled

Tile = tuple[int, int]

# C37 / C52 compare codes (genbu docs/dungeon-guide-authoring.md).
_CMP = {
    0: lambda n, v: n == v,
    1: lambda n, v: n < v,
    2: lambda n, v: n > v,
    3: lambda n, v: n <= v,
    4: lambda n, v: n >= v,
}
_WARP_OPS = (3, 9)  # A3: to a map and tag; A9: to a tag on this map
TILE_PX = 40
NEAR_PART = 2  # tiles searched around a point that sits on a blocked cell


@dataclass(frozen=True)
class Portal:
    event: int  # map_events.event_tag
    area: int | None  # area that holds the zone
    zone: tuple[Tile, ...]  # tiles to step on
    dead: frozenset[int]  # spawn groups that must be all dead
    alive: frozenset[int]  # spawn groups that must still be alive
    other: bool  # also checks something the client cannot read (variable, mission)
    to_stage: int | None  # target map; None = this map
    to_tag: int | None  # landing tag on the target map

    def lands_here(self) -> bool:
        return self.to_stage is None


@dataclass
class MapAreas:
    kind: str
    stage: int
    width: int
    height: int
    _part: list[int] = field(repr=False)
    portals: dict[int, Portal] = field(default_factory=dict)
    group_area: dict[int, int | None] = field(default_factory=dict)
    group_npcs: dict[int, frozenset[int]] = field(default_factory=dict)
    group_tiles: dict[int, list[Tile]] = field(default_factory=dict)
    group_count: dict[int, int] = field(default_factory=dict)  # monsters a group spawns
    landings: dict[int, list[Tile]] = field(default_factory=dict)  # tag -> trigger tiles
    npcs: dict[int, list[Tile]] = field(default_factory=dict)  # npc id -> tiles

    def area_of(self, tile: Tile) -> int | None:
        """The area holding `tile`, or the nearest one within NEAR_PART tiles."""
        x0, y0 = tile
        for r in range(NEAR_PART + 1):
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    x, row = x0 + dx, self.height - 1 - (y0 + dy)
                    if 0 <= x < self.width and 0 <= row < self.height:
                        a = self._part[row * self.width + x]
                        if a >= 0:
                            return a
        return None

    def walkable(self, tile: Tile) -> bool:
        x, row = tile[0], self.height - 1 - tile[1]
        return (
            0 <= x < self.width and 0 <= row < self.height and self._part[row * self.width + x] >= 0
        )

    def landing_area(self, tag: int) -> int | None:
        tiles = self.landings.get(tag) or []
        return _most(self.area_of(t) for t in tiles)

    def groups_in(self, area: int | None) -> list[int]:
        return [g for g, a in self.group_area.items() if a == area and a is not None]


def _most(values) -> int | None:
    counts: dict[int, int] = {}
    for v in values:
        if v is not None:
            counts[v] = counts.get(v, 0) + 1
    return max(counts, key=counts.get) if counts else None


def _components(width: int, height: int, mask: str) -> list[int]:
    part = [-1] * (width * height)
    n = 0
    for start in range(width * height):
        if mask[start] != "1" or part[start] >= 0:
            continue
        part[start] = n
        q = deque([start])
        while q:
            i = q.popleft()
            x, y = i % width, i // width
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < width and 0 <= ny < height:
                        j = ny * width + nx
                        if mask[j] == "1" and part[j] < 0:
                            part[j] = n
                            q.append(j)
        n += 1
    return part


def _group_need(cmp: int, value: int, negated: bool) -> str | None:
    """'dead' / 'alive' for a C37 condition on a group, None when it is neither."""
    test = _CMP.get(cmp)
    if test is None:
        return None
    at = [bool(test(n, value)) != negated for n in (0, 1, 10)]
    if at == [True, False, False]:
        return "dead"
    if at == [False, True, True]:
        return "alive"
    return None


def _connect(path: Path | None) -> sqlite3.Connection:
    p = path or bundled("tthol.sqlite")
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    con.text_factory = lambda b: b.decode("utf-8", errors="replace")
    return con


def _cell(raw_x: int, raw_y: int, height: int) -> Tile:
    return raw_x // TILE_PX, height - 1 - raw_y // TILE_PX


def _height(con, kind: str, stage: int) -> int | None:
    row = con.execute(
        "SELECT height FROM map_walkability WHERE stage_kind = ? AND stage_id = ?",
        (kind, stage),
    ).fetchone()
    return row[0] if row else None


def _landing_tiles(con, kind: str, stage: int, height: int) -> dict[int, list[Tile]]:
    out: dict[int, list[Tile]] = {}
    for tag, rx, ry in con.execute(
        "SELECT tag_id, raw_x, raw_y FROM map_placements"
        " WHERE stage_kind = ? AND stage_id = ? AND category = 'trigger' ORDER BY id",
        (kind, stage),
    ):
        out.setdefault(tag, []).append(_cell(rx, ry, height))
    return out


@functools.lru_cache(maxsize=32)
def load_areas(kind: str, stage: int, db_path: Path | None = None) -> MapAreas | None:
    """The areas of map (kind, stage); None when the DB has no walk mask for it."""
    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT width, height, walk_mask FROM map_walkability"
            " WHERE stage_kind = ? AND stage_id = ?",
            (kind, stage),
        ).fetchone()
        if row is None or not row[2]:
            return None
        width, height, mask = row
        areas = MapAreas(kind, stage, width, height, _components(width, height, mask))
        areas.landings = _landing_tiles(con, kind, stage, height)
        zones: dict[int, list[Tile]] = {}
        for cat, npc, ev, group, count, rx, ry in con.execute(
            "SELECT category, npc_id, event_tag, spawn_group, spawn_count, raw_x, raw_y"
            " FROM map_placements WHERE stage_kind = ? AND stage_id = ? ORDER BY id",
            (kind, stage),
        ):
            x, y = _cell(rx, ry, height)
            if cat == "arrival" and ev is not None:
                zones.setdefault(ev, []).append((x, y))
            elif cat == "spawn" and group is not None and npc:
                areas.group_tiles.setdefault(group, []).append((x, y))
                areas.group_count[group] = areas.group_count.get(group, 0) + (count or 1)
                areas.group_npcs[group] = areas.group_npcs.get(group, frozenset()) | {npc}
            elif cat == "npc" and npc:
                areas.npcs.setdefault(npc, []).append((x, y))
        for g, tiles in areas.group_tiles.items():
            areas.group_area[g] = _most(areas.area_of(t) for t in tiles)
        for tag, raw in con.execute(
            "SELECT event_tag, raw_ops FROM map_events"
            " WHERE stage_kind = ? AND stage_id = ? AND bind = 'zone'",
            (kind, stage),
        ):
            portal = _portal(tag, raw, zones.get(tag, []), areas)
            if portal is not None:
                areas.portals[tag] = portal
        return areas
    finally:
        con.close()


def _portal(tag: int, raw: str | None, zone: list[Tile], areas: MapAreas) -> Portal | None:
    """The portal of zone event `tag`, from its raw trigger list; None when no
    trigger moves the character."""
    if not zone or not raw:
        return None
    try:
        triggers = json.loads(raw)
    except ValueError:
        return None
    dead: frozenset[int] | None = None
    alive: frozenset[int] | None = None
    other = False
    target: tuple[int | None, int] | None = None
    for t in triggers:
        warp = next((a for a in t.get("acts", []) if a and a[0] in _WARP_OPS), None)
        if warp is None:
            continue
        if target is None:
            target = (warp[1], warp[2]) if warp[0] == 3 else (None, warp[1])
        d: set[int] = set()
        al: set[int] = set()
        for c in t.get("conds", []):
            # [op, negated, a0, a1, a2, ...]
            op, neg = c[0], bool(c[1])
            if op == 32:  # leading no-op of MPC events
                continue
            if op == 37:
                need = _group_need(c[2], c[3], neg)
                if need == "dead":
                    d.add(c[4])
                elif need == "alive":
                    al.add(c[4])
                else:
                    other = True
            else:
                other = True
        dead = frozenset(d) if dead is None else dead & d
        alive = frozenset(al) if alive is None else alive & al
    if target is None:
        return None
    return Portal(
        event=tag,
        area=_most(areas.area_of(t) for t in zone),
        zone=tuple(zone),
        dead=dead or frozenset(),
        alive=alive or frozenset(),
        other=other,
        to_stage=target[0],
        to_tag=target[1],
    )


@functools.lru_cache(maxsize=64)
def landing_tiles(kind: str, stage: int, tag: int, db_path: Path | None = None) -> list[Tile]:
    """Trigger tiles of `tag` on map (kind, stage): where a portal to it lands."""
    con = _connect(db_path)
    try:
        height = _height(con, kind, stage)
        if height is None:
            return []
        return _landing_tiles(con, kind, stage, height).get(tag, [])
    finally:
        con.close()
