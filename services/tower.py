"""神武玄天塔 layout from the game DB, and the geometry the tower module walks by.

One 關 is one sestage (1704 辰星 ... 1713), ten rooms in a row; one room is one
floor (floor = 關 index * 10 + room). In room n (map_placements, tile
coordinates, bottom-left origin like the hook's `near` / 40):

- start: trigger with tag_id 100 + n
- exit zone: arrival tiles with event_tag n (stepping on it raises the
  "continue" dialog once every body in the room has faded)
- monsters: spawn groups 2n - 1 (the pack) and 2n (the elite); how many to
  kill is the sum of their spawn_count

The flow follows tthol-hook's local/scripts/tower.py (route B), which the hook
user cleared as public: it only calls hook API commands.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from services._paths import bundled

LOBBY_STAGE = 357  # 玄天之境
FIRST_STAGE = 1704  # 辰星關
ROOMS = 10
YAN = 7602  # 燕飄風 in 玄天之境: enters the tower
# 九尾狐仙 at the start of each 關 -> the 關's sestage id
FOX_STAGE = {7603: 1704, 7604: 1705, 7605: 1706, 7606: 1707, 7607: 1708, 7608: 1709, 7609: 1710}

# Dialog jump ids (messages.msg_id the option leads to): pick by id, never by position.
YAN_WANT = (65846, 65847, 65851)  # enter; 65847 also declines the 狐光靈珠 skip
YAN_AVOID = frozenset({65852})
FOX_AVOID = frozenset({65862})  # decline
LEAVE = 65873  # the room exit's "leave the tower" option
TOO_LOW = 65874  # the next floor needs a higher level
STAGING = 4  # stand this many tiles off the exit: stepping on it from closer does not fire


@dataclass
class TowerStage:
    stage_id: int
    starts: dict[int, tuple[int, int]]  # room -> start tile
    exits: dict[int, list[tuple[int, int]]]  # room -> exit zone tiles
    monsters: dict[int, frozenset[int]]  # room -> its monster npc ids
    expect: dict[int, int]  # room -> how many to kill
    spawns: dict[int, list[tuple[int, int]]] = field(default_factory=dict)  # elite first
    fox: tuple[int, int] | None = None  # 九尾狐仙's tile: the 關 start, outside every room
    npc_hp: dict[int, int] = field(default_factory=dict)  # monster npc id -> base max HP
    npc_dodge: dict[int, int] = field(default_factory=dict)  # monster npc id -> base_dodge
    npc_name: dict[int, str] = field(default_factory=dict)
    elites: frozenset[int] = frozenset()  # npc ids that are alone in their room

    def strength(self) -> dict[int, tuple[int, int]]:
        """npc id -> (elite, base HP): what "weakest first" sorts by."""
        ids = set().union(*self.monsters.values()) if self.monsters else set()
        return {i: (int(i in self.elites), self.npc_hp.get(i, 0)) for i in ids}

    @property
    def index(self) -> int:
        return self.stage_id - FIRST_STAGE

    def at_start(self, tile: tuple[int, int]) -> bool:
        """At the 關 start (by the fox) rather than in a room: nearer the fox
        than any room start. Works before `near` lists anything after a map change."""
        if self.fox is None:
            return False
        to_room = min(math.dist(tile, s) for s in self.starts.values())
        return math.dist(tile, self.fox) < to_room

    def floor(self, room: int) -> int:
        return self.index * ROOMS + room

    def room_of(self, tile: tuple[int, int]) -> int:
        """The room whose start is nearest (rooms are far apart on one map)."""
        return min(self.starts, key=lambda r: math.dist(self.starts[r], tile))

    def staging(self, room: int, dist: int = STAGING) -> tuple[int, int]:
        """A tile `dist` steps from the exit zone's middle toward the room start."""
        zone = self.exits[room]
        zx, zy = zone[len(zone) // 2]
        sx, sy = self.starts[room]
        d = math.dist((zx, zy), (sx, sy)) or 1
        return round(zx + (sx - zx) * dist / d), round(zy + (sy - zy) * dist / d)

    def exit_tiles(self, room: int) -> list[tuple[int, int]]:
        """Exit zone tiles, nearest to the staging tile first."""
        stand = self.staging(room)
        return sorted(self.exits[room], key=lambda t: math.dist(t, stand))


def load_tower(stage_id: int, db_path: Path | None = None) -> TowerStage | None:
    """The 關 on `stage_id`, or None when it is not a tower sestage with ten rooms."""
    path = db_path or bundled("tthol.sqlite")
    if not path.exists():
        return None
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT category, npc_id, tag_id, tile_x, tile_y, spawn_group, spawn_count, event_tag"
            " FROM map_placements WHERE stage_kind = 'sestage' AND stage_id = ?",
            (stage_id,),
        ).fetchall()
        spawn_ids = {r[1] for r in rows if r[0] == "spawn" and r[1]}
        npc_rows = (
            con.execute(
                "SELECT id, hp, base_dodge, name FROM npc"
                f" WHERE id IN ({','.join('?' * len(spawn_ids))})",
                tuple(spawn_ids),
            ).fetchall()
            if spawn_ids
            else []
        )
        npc_hp = {r[0]: r[1] for r in npc_rows}
    finally:
        con.close()
    starts: dict[int, tuple[int, int]] = {}
    exits: dict[int, list[tuple[int, int]]] = {}
    monsters: dict[int, set[int]] = {}
    expect: dict[int, int] = {}
    spawns: dict[int, list[tuple[int, int, int]]] = {}
    per_room: dict[int, dict[int, int]] = {}  # room -> npc id -> how many spawn
    fox = None
    for cat, npc, tag, x, y, group, count, event in rows:
        if cat == "trigger" and tag and 101 <= tag <= 100 + ROOMS:
            starts[tag - 100] = (x, y)
        elif cat == "arrival" and event and 1 <= event <= ROOMS:
            exits.setdefault(event, []).append((x, y))
        elif cat == "npc" and npc in FOX_STAGE:
            fox = (x, y)
        elif cat == "spawn" and group:
            room = (group + 1) // 2
            counts = per_room.setdefault(room, {})
            counts[npc] = counts.get(npc, 0) + (count or 1)
            monsters.setdefault(room, set()).add(npc)
            expect[room] = expect.get(room, 0) + (count or 1)
            spawns.setdefault(room, []).append((group, x, y))
    # The elite (user's rule): the one monster of its kind in a room with others.
    elites = {
        npc
        for counts in per_room.values()
        if len(counts) > 1
        for npc, n in counts.items()
        if n == 1
    }
    rooms = set(range(1, ROOMS + 1))
    if not (rooms <= starts.keys() and rooms <= exits.keys() and rooms <= expect.keys()):
        return None
    return TowerStage(
        stage_id=stage_id,
        starts=starts,
        exits=exits,
        monsters={r: frozenset(ids) for r, ids in monsters.items()},
        expect=expect,
        # the elite's group (2n) first: it is the one that wanders off screen
        spawns={r: [(x, y) for _g, x, y in sorted(v, reverse=True)] for r, v in spawns.items()},
        fox=fox,
        npc_hp={k: v or 0 for k, v in npc_hp.items()},
        npc_dodge={r[0]: r[2] or 0 for r in npc_rows},
        npc_name={r[0]: r[3] for r in npc_rows},
        elites=frozenset(elites),
    )


def pick_option(options: list[int], want: tuple[int, ...], avoid: frozenset[int]) -> int | None:
    """Index of the option to choose: the first wanted jump id, else any not avoided."""
    for w in want:
        if w in options:
            return options.index(w)
    for i, o in enumerate(options):
        if o and o not in avoid:
            return i
    return None


# ---- how far a character can climb -------------------------------------------------

# Hit needed against a room's highest base_dodge. The game's hit formula is the
# server's and unknown; this is calibrated on the user's run of 2026-10-05 with
# hit 1089: the tightest floor it cleared was 61 (dodge 1062, x1.025), and it
# stalled on 66 (dodge 1105, x0.986).
HIT_RATIO = 1.0


def floor_gates(db_path: Path | None = None) -> list[int]:
    """Level needed to continue past floor n+1 at index n: each room's
    "continue" dialog checks the level (trigger_ops op 4) or answers 65874."""
    path = db_path or bundled("tthol.sqlite")
    if not path.exists():
        return []
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT msg_id, a1 FROM trigger_ops WHERE op = 4 AND a0 = 4 AND negated = 1"
            " AND msg_id IN (SELECT msg_id FROM trigger_ops WHERE op = 12 AND a0 = ?)"
            " ORDER BY msg_id",
            (TOO_LOW,),
        ).fetchall()
    finally:
        con.close()
    return [level for _msg, level in rows]


@dataclass
class Reach:
    max_floor: int  # highest floor expected to clear (0: not even the first)
    blocker: str | None  # why the next floor is out of reach, in user words


def estimate_reach(
    hit: int,
    level: int,
    stages: list[TowerStage],
    gates: list[int],
    ratio: float = HIT_RATIO,
) -> Reach:
    """Climb floor by floor until the level gate or a room's dodge stops it."""
    cleared = 0
    for stage in stages:
        for room in range(1, ROOMS + 1):
            floor = stage.floor(room)
            if floor >= 2 and floor - 2 < len(gates) and level < gates[floor - 2]:
                return Reach(cleared, f"第 {floor} 層要 LV{gates[floor - 2]}")
            ids = stage.monsters.get(room, frozenset())
            if ids:
                hardest = max(ids, key=lambda i: stage.npc_dodge.get(i, 0))
                dodge = stage.npc_dodge.get(hardest, 0)
                if hit < dodge * ratio:
                    name = stage.npc_name.get(hardest, str(hardest))
                    return Reach(cleared, f"第 {floor} 層 {name} 迴避 {dodge}，命中不夠")
            cleared = floor
    return Reach(cleared, None)
