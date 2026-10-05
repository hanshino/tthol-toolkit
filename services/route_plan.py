"""Cross-map route planning: which maps to pass and how to get from one to the next.

Three ways to change maps, cheapest first by the user's rules (2026-10-05):

1. 家族馬夫. A 家族總管 (in a dozen towns) sends the player into the family
   manor; the manor's 家族馬夫 rides to a list of places. What the menus offer
   depends on the manor: the menu triggers test C84 (cmp, manor sestage id),
   so a higher manor unlocks more places. The manor comes from the hook's 0x31
   (services/family.py).
2. Town horses, 車伕 and boatmen (莫愁谷馬夫, 成都車伕, 船夫, ...): fixed
   menus, some places with a level floor.
3. Walking out of a map through a walk-on exit (an `arrival` zone whose map
   event warps, A3).

Exit gating. A walk-on exit is a map event: a list of triggers, the first
whose conditions hold runs. A trigger warps directly (A3) or opens a message
(A42 / A12) that warps, shows options, or just says something. So an exit is
judged per character level by evaluating the triggers in order:

- level (C4) is compared with the character's level;
- any other positive condition (a mission step, an item, gear, sect, chance,
  map variables ...) is taken as not met: a route never relies on it;
- a negated one of those is taken as met (a fresh character holds no item and
  has no mission running);
- money (C26) is taken as met: a fare is a few thousand.

The first trigger that applies decides. User-checked 2026-10-05: 天外天一號道's
"secret passage" to 天外密道 needs mission steps in every warping branch and
otherwise only shows 「牆上看到隱隱約約有記號」, so it is not a route.

Coordinates are game tiles (origin bottom-left), as map_placements.tile_x/y.
"""

from __future__ import annotations

import heapq
import math
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from services import map_regions
from services._paths import bundled

Tile = tuple[int, int]

# Costs, in "maps walked". Walking inside a map adds WALK_TILE per tile.
COST_WALK = 1.0
COST_TOWN = 0.8
COST_FAMILY = 0.6  # 家族總管 -> manor -> 家族馬夫, one step for the planner
WALK_TILE = 1 / 150

# The manor's three 家族馬夫 and the menu each opens (name_id 30473). Live
# 2026-10-06 in 天巧莊: npc 6382 opened the 低階 list. Placements agree:
# 人X莊 have only 6382, 地X莊 add 6347, 天X莊 add 6348.
FAMILY_HORSES = {6382: 24793, 6347: 24794, 6348: 24795}  # 低階 / 中階 / 高階
STEWARD = 6381  # 家族總管
STORY_STAGES = frozenset({311})  # 劇情用地圖: a cut-scene map, never a route

LEVEL = 4  # condition op: character level (cmp, value)
MANOR = 84  # condition op: family manor sestage id (cmp, value)
# Positive conditions taken as met: 銀兩 (C26) — a horse fare is a few thousand.
ASSUMED = frozenset({26})
WARP = 3  # action: warp (stage, tag)
WARP_STAGE = 98  # action: warp to a stage's default spot
# action: warp into the player's own family manor. Unnamed in op_defs; read from
# 家族總管 「傳送至家族據點」 -> 「好的, 現在就送你過去」 -> A99 (live 2026-10-06).
WARP_MANOR = 99
WARP_POINT = 9  # action: teleport to a trigger tag on the same map (doors)
DOOR = -1  # warps_from_* stage for an A9: the same map
COST_DOOR = 0.2
STEP_ON = 2  # map_events.event_kind of zones that fire when walked into
REGION_SNAP = 4  # exit zones sit on blocked edge cells: look this far for walkable ground
CALL_MSG = (12, 42)  # actions that continue in a message
MAX_DEPTH = 14  # menu -> fare choice -> pay -> ride -> warp runs 7-8 deep


@dataclass(frozen=True)
class TownHorse:
    npc_id: int
    menus: tuple[int, ...]  # the menu message(s) talking to it opens


# Town NPCs that ride somewhere, by their menu message (name_id in parentheses).
# Where they stand comes from map_placements; where they go and the level each
# place needs come from evaluating the menu like any other message.
TOWN_HORSES = (
    TownHorse(6624, (3871,)),  # 莫愁谷馬夫 (30261)
    TownHorse(6623, (3880,)),  # 天外天馬夫 (30055)
    TownHorse(6625, (3862,)),  # 飛雁山莊馬夫 (30260)
    TownHorse(6004, (3979,)),  # 藏海村馬夫 (30344)
    TownHorse(6743, (37242,)),  # 成都碼頭船夫 (30571)
    TownHorse(6744, (37003,)),  # 無名村船夫 (30572)
    # 車伕 (user, 2026-10-05: 成都 rides straight to 曼陀羅城)
    TownHorse(6024, (21320,)),  # 成都車伕 (30341): 檀泉 / 洛陽 / 名劍山莊 / 狐隱村 / 曼陀羅城
    TownHorse(6025, (18106,)),  # 檀泉別苑車伕 (30342)
    TownHorse(6026, (18100,)),  # 洛陽車伕 (30343)
    TownHorse(7236, (34396,)),  # 名劍山莊車伕 (30941)
    TownHorse(6975, (34454,)),  # 狐隱村車伕 (31107)
    TownHorse(6976, (34455,)),  # 曼陀羅車伕 (31108)
)


@dataclass(frozen=True)
class Step:
    kind: str  # walk / door (in-map teleport, src == dst) / town / family
    src: int
    dst: int
    at: Tile | None  # exit zone tile, or the NPC to talk to
    npc_id: int | None = None
    label: str | None = None  # exit menu option text, when the exit asks
    # walk / door: the zone fires on a click on its map object (hook `objects` /
    # `touch`), not on stepping in. map_events.event_kind 2 = step on; the
    # 清音瀑布 crane statues (kind 1) needed a click (live 2026-10-06).
    touch: bool = False
    horse: int | None = None  # family: the manor 家族馬夫 whose menu has `dst`


@dataclass
class Route:
    steps: list[Step]
    cost: float


Node = tuple[int, int | None]  # (stage, walkable region; None = not known)


@dataclass
class _Edge:
    cost: float
    step: Step
    land: Tile | None  # where the step puts the player on `step.dst`
    src_region: int | None = None
    dst_region: int | None = None


@dataclass
class _Graph:
    edges: dict[int, list[_Edge]] = field(default_factory=dict)  # by source stage
    region_of: object = None  # (stage, tile) -> region index or None

    def add(self, e: _Edge) -> None:
        self.edges.setdefault(e.step.src, []).append(e)

    def out(self, node: Node) -> list[_Edge]:
        """Edges usable from `node`: those starting in its region, or anywhere
        on the map when either side's region is unknown."""
        stage, region = node
        return [
            e
            for e in self.edges.get(stage, [])
            if region is None or e.src_region is None or e.src_region == region
        ]


def _connect(db_path: Path | None) -> sqlite3.Connection:
    path = db_path or bundled("tthol.sqlite")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.text_factory = lambda b: b.decode("utf-8", errors="replace")
    return con


def _compare(cmp: int, have: int, want: int) -> bool | None:
    # 1 = "<" and 4 = ">=" are confirmed by the 「等級還沒有到達N級」 texts;
    # 0 = "==" by the manor checks; 2 / 3 read as ">" / "<=" (likely).
    return {
        0: have == want,
        1: have < want,
        2: have > want,
        3: have <= want,
        4: have >= want,
    }.get(cmp)


def _holds(op: int, negated: bool, a0, a1, level: int, manor: int | None) -> bool:
    if op == LEVEL:
        ok = _compare(a0, level, a1)
    elif op == MANOR:
        ok = None if manor is None else _compare(a0, manor, a1)
    elif op in ASSUMED:
        return not negated
    else:
        return bool(negated)  # see the module doc: positive unknowns never hold
    if ok is None:
        return False
    return ok != bool(negated)


Trigger = tuple[list, list]  # (conditions, actions), each (op, negated, a0, a1)


class _Tables:
    """The DB rows routing reads, loaded once: the message tables have no index on
    msg_id alone, so per-message queries cost ~30 s for one graph."""

    def __init__(self, con: sqlite3.Connection) -> None:
        self.stages = dict(con.execute("SELECT id, name FROM stages WHERE kind = 'stage'"))
        self.msg_triggers = self._group(
            con.execute(
                "SELECT msg_id, trigger_idx, kind, op, negated, a0, a1 FROM trigger_ops"
                " ORDER BY msg_id, trigger_idx, kind DESC, seq"
            )
        )
        self.event_triggers: dict[tuple[int, int], list[list[Trigger]]] = {}
        rows = con.execute(
            "SELECT stage_id, event_tag, event_id, trigger_idx, kind, op, negated, a0, a1"
            " FROM map_event_ops WHERE stage_kind = 'stage'"
            " ORDER BY stage_id, event_tag, event_id, trigger_idx, kind DESC, seq"
        )
        events: dict[tuple[int, int], dict[int, dict[int, Trigger]]] = {}
        for sid, tag, ev, ti, kind, op, neg, a0, a1 in rows:
            trig = events.setdefault((sid, tag), {}).setdefault(ev, {}).setdefault(ti, ([], []))
            (trig[0] if kind == "C" else trig[1]).append((op, neg, a0, a1))
        for key, by_event in events.items():
            self.event_triggers[key] = [[t[i] for i in sorted(t)] for t in by_event.values()]
        self.options: dict[int, list[int]] = {}
        for msg, jump in con.execute(
            "SELECT msg_id, jump_to FROM message_options WHERE jump_to IS NOT NULL"
            " ORDER BY msg_id, opt_index"
        ):
            self.options.setdefault(msg, []).append(jump)
        self.jump = dict(
            con.execute("SELECT msg_id, jump_to FROM messages WHERE jump_to IS NOT NULL")
        )
        self.event_kind: dict[tuple[int, int], int] = {
            (sid, tag): kind
            for sid, tag, kind in con.execute(
                "SELECT stage_id, event_tag, event_kind FROM map_events WHERE stage_kind = 'stage'"
            )
        }
        self.tiles: dict[tuple[int, str, int], list[Tile]] = {}
        for sid, cat, npc, tag, event, x, y in con.execute(
            "SELECT stage_id, category, npc_id, tag_id, event_tag, tile_x, tile_y"
            " FROM map_placements WHERE stage_kind = 'stage'"
            " AND category IN ('arrival', 'trigger', 'npc')"
        ):
            key = {"arrival": event, "trigger": tag, "npc": npc}[cat]
            if key is not None:
                self.tiles.setdefault((sid, cat, key), []).append((x, y))

    @staticmethod
    def _group(rows) -> dict[int, list[Trigger]]:
        out: dict[int, dict[int, Trigger]] = {}
        for msg, ti, kind, op, neg, a0, a1 in rows:
            trig = out.setdefault(msg, {}).setdefault(ti, ([], []))
            (trig[0] if kind == "C" else trig[1]).append((op, neg, a0, a1))
        return {m: [t[i] for i in sorted(t)] for m, t in out.items()}

    def middle(self, stage: int, category: str, key: int) -> Tile | None:
        """The cell nearest the rest of a zone (or an NPC's own tile)."""
        cells = self.tiles.get((stage, category, key))
        if not cells:
            return None
        return min(cells, key=lambda c: sum(math.dist(c, o) for o in cells))


class _Script:
    """Message / map-event evaluation against one character (level, manor)."""

    def __init__(self, t: _Tables, level: int, manor: int | None) -> None:
        self.t = t
        self.level = level
        self.manor = manor

    def _first(self, triggers: list[Trigger]) -> list | None:
        for conds, acts in triggers:
            if all(_holds(op, neg, a0, a1, self.level, self.manor) for op, neg, a0, a1 in conds):
                return acts
        return None

    def warps_from_event(self, stage_id: int, tag: int) -> list[tuple[int, int]]:
        out: list[tuple[int, int]] = []
        for triggers in self.t.event_triggers.get((stage_id, tag), []):
            out += self._run(self._first(triggers) or [], 0)
        return out

    def warps_from_msg(
        self, msg_id: int, depth: int = 0, seen: frozenset[int] = frozenset()
    ) -> list[tuple[int, int]]:
        if depth > MAX_DEPTH or msg_id in seen:
            return []  # too deep, or a loop (a 下一頁 option back to the first page)
        seen = seen | {msg_id}
        triggers = self.t.msg_triggers.get(msg_id)
        if triggers:
            acts = self._first(triggers)
            # A trigger that only has side effects (A34 reset, A30 money) leaves
            # the message to show its options.
            if acts is not None and any(
                op in (WARP, WARP_STAGE, WARP_MANOR) or op in CALL_MSG for op, *_ in acts
            ):
                return self._run(acts, depth + 1, seen)
        out: list[tuple[int, int]] = []
        for jump in self.t.options.get(msg_id, []):
            out += self.warps_from_msg(jump, depth + 1, seen)
        jump = self.t.jump.get(msg_id)
        if jump and not out:
            out += self.warps_from_msg(jump, depth + 1, seen)
        return out

    def _run(
        self, acts: list, depth: int, seen: frozenset[int] = frozenset()
    ) -> list[tuple[int, int]]:
        for op, _neg, a0, a1 in acts:
            if op == WARP:
                return [(a0, a1 or 0)]
            if op == WARP_STAGE:
                return [(a0, 0)]
            if op == WARP_MANOR:
                return [] if self.manor is None else [(self.manor, 0)]
            if op == WARP_POINT:
                return [(DOOR, a0)]
            if op in CALL_MSG and a0:
                return self.warps_from_msg(a0, depth, seen)
        return []


def _family_dests(script: _Script) -> list[tuple[int, int, int]]:
    """Places the manor's 家族馬夫 ride to: (stage, landing tag, horse npc)."""
    out: list[tuple[int, int, int]] = []
    for horse, menu in FAMILY_HORSES.items():
        out += [(d, tag, horse) for d, tag in script.warps_from_msg(menu)]
    return out


@lru_cache(maxsize=2)
def _tables(db_path: Path | None = None) -> _Tables:
    con = _connect(db_path)
    try:
        return _Tables(con)
    finally:
        con.close()


class _Regions:
    """Walkable regions per map, labelled on first use (map_regions, 4-connected).

    One map image often packs several separate spaces (a town and its
    interiors); a walk-on exit or an NPC is only reachable from its own space.
    """

    def __init__(self, db_path: Path | None) -> None:
        self._db_path = db_path
        self._cache: dict[tuple[str, int], map_regions.Regions | None] = {}

    def _get(self, stage: int, kind: str) -> map_regions.Regions | None:
        if (kind, stage) not in self._cache:
            con = _connect(self._db_path)
            try:
                row = con.execute(
                    "SELECT width, height, walk_mask FROM map_walkability"
                    " WHERE stage_kind = ? AND stage_id = ?",
                    (kind, stage),
                ).fetchone()
            finally:
                con.close()
            ok = row is not None and row[2] and len(row[2]) == row[0] * row[1]
            self._cache[(kind, stage)] = map_regions._label(*row) if ok else None
        return self._cache[(kind, stage)]

    def __call__(self, stage: int, tile: Tile | None, kind: str = "stage") -> int | None:
        reg = self._get(stage, kind)
        if reg is None or tile is None:
            return None
        col, row = tile[0], reg.height - 1 - tile[1]
        best: tuple[int, int] | None = None
        for dc in range(-REGION_SNAP, REGION_SNAP + 1):
            for dr in range(-REGION_SNAP, REGION_SNAP + 1):
                c, r = col + dc, row + dr
                if not (0 <= c < reg.width and 0 <= r < reg.height):
                    continue
                label = reg._labels[r * reg.width + c]
                if label < 0 or reg.boxes[label][4] < map_regions.MIN_REGION_CELLS:
                    continue
                d = max(abs(dc), abs(dr))
                if best is None or d < best[0]:
                    best = (d, label)
        return best[1] if best else None


@lru_cache(maxsize=2)
def _regions(db_path: Path | None = None) -> _Regions:
    return _Regions(db_path)


def room_doors(
    kind: str, stage: int, start: Tile, goal: Tile, db_path: Path | None = None
) -> list[tuple[Tile, bool]] | None:
    """Door zones to take, in order, to get from `start` to `goal`'s space on one
    map: [(zone tile, needs a touch)], [] when already in it, None when no way.

    For maps the graph does not cover, like the family manor (a sestage): its
    家族馬夫 stand in a room reached from the grounds by an A9 door.
    """
    region = _regions(db_path)
    src, dst = region(stage, start, kind), region(stage, goal, kind)
    if src is None or dst is None or src == dst:
        return [] if src == dst else None
    con = _connect(db_path)
    try:
        events = con.execute(
            "SELECT o.event_tag, o.a0, e.event_kind FROM map_event_ops o"
            " JOIN map_events e ON e.id = o.event_id"
            " WHERE o.stage_kind = ? AND o.stage_id = ? AND o.kind = 'A' AND o.op = ?",
            (kind, stage, WARP_POINT),
        ).fetchall()
        cells: dict[tuple[str, int], list[Tile]] = {}
        for cat, key, x, y in con.execute(
            "SELECT category, CASE category WHEN 'arrival' THEN event_tag ELSE tag_id END,"
            " tile_x, tile_y FROM map_placements WHERE stage_kind = ? AND stage_id = ?"
            " AND category IN ('arrival', 'trigger')",
            (kind, stage),
        ):
            cells.setdefault((cat, key), []).append((x, y))
    finally:
        con.close()

    def middle(c: list[Tile]) -> Tile | None:
        return min(c, key=lambda a: sum(math.dist(a, b) for b in c)) if c else None

    edges: dict[int, list[tuple[Tile, bool, int]]] = {}
    for tag, point, ekind in events:
        a = middle(cells.get(("arrival", tag), []))
        b = middle(cells.get(("trigger", point), []))
        ra, rb = region(stage, a, kind), region(stage, b, kind)
        if a and b and ra is not None and rb is not None and ra != rb:
            edges.setdefault(ra, []).append((a, ekind != STEP_ON, rb))
    prev: dict[int, tuple[int, Tile, bool] | None] = {src: None}
    queue = [src]
    for r in queue:
        for a, touch, rb in edges.get(r, []):
            if rb not in prev:
                prev[rb] = (r, a, touch)
                queue.append(rb)
    if dst not in prev:
        return None
    hops: list[tuple[Tile, bool]] = []
    r = dst
    while prev[r] is not None:
        r, a, touch = prev[r]
        hops.append((a, touch))
    return hops[::-1]


def build_graph(level: int, manor: int | None, db_path: Path | None = None) -> _Graph:
    t = _tables(db_path)
    script = _Script(t, level, manor)
    stages = t.stages
    region = _regions(db_path)
    g = _Graph(region_of=region)

    def add(cost: float, step: Step, land: Tile | None) -> None:
        g.add(_Edge(cost, step, land, region(step.src, step.at), region(step.dst, land)))

    def landing(stage: int, tag: int) -> Tile | None:
        return t.middle(stage, "trigger", tag)

    for sid, cat, tag in list(t.tiles):
        if cat != "arrival" or sid in STORY_STAGES:
            continue
        at = t.middle(sid, "arrival", tag)
        touch = t.event_kind.get((sid, tag), STEP_ON) != STEP_ON
        for dst, dtag in script.warps_from_event(sid, tag):
            if dst == DOOR:
                land = landing(sid, dtag)
                if land is not None:
                    add(COST_DOOR, Step("door", sid, sid, at, touch=touch), land)
                continue
            if dst == sid or dst not in stages or dst in STORY_STAGES:
                continue
            add(COST_WALK, Step("walk", sid, dst, at, touch=touch), landing(dst, dtag))

    for horse in TOWN_HORSES:
        dests = {d: tag for menu in horse.menus for d, tag in script.warps_from_msg(menu)}
        for sid, cat, key in list(t.tiles):
            if cat != "npc" or key != horse.npc_id:
                continue
            at = t.middle(sid, "npc", horse.npc_id)
            for dst, dtag in dests.items():
                if dst != sid and dst in stages:
                    add(COST_TOWN, Step("town", sid, dst, at, horse.npc_id), landing(dst, dtag))

    if manor is not None:
        stewards = sorted({sid for sid, cat, key in t.tiles if cat == "npc" and key == STEWARD})
        for dst, dtag, horse in _family_dests(script):
            if dst not in stages:
                continue
            for sid in stewards:
                if sid != dst:
                    at = t.middle(sid, "npc", STEWARD)
                    step = Step("family", sid, dst, at, STEWARD, horse=horse)
                    add(COST_FAMILY, step, landing(dst, dtag))
    return g


def plan(
    g: _Graph, src: int, dst: int, start: Tile | None = None, goal_tile: Tile | None = None
) -> Route | None:
    """Cheapest route from map `src` (standing on `start`, when known) to map `dst`,
    or to the space of `dst` that holds `goal_tile` (a room inside a town)."""
    first: Node = (src, g.region_of(src, start) if g.region_of else None)
    want = g.region_of(dst, goal_tile) if g.region_of and goal_tile else None
    best: dict[Node, float] = {first: 0.0}
    prev: dict[Node, tuple[Node, Step]] = {}
    pos: dict[Node, Tile | None] = {first: start}
    pq: list[tuple[float, int, Node]] = [(0.0, 0, first)]
    tie = 0

    def arrived(node: Node) -> bool:
        return node[0] == dst and (want is None or node[1] is None or node[1] == want)

    goal: Node | None = first if arrived(first) else None
    while pq and goal is None:
        cost, _t, here = heapq.heappop(pq)
        if arrived(here):
            goal = here
            break
        if cost > best.get(here, math.inf):
            continue
        for e in g.out(here):
            walk = 0.0
            if pos.get(here) is not None and e.step.at is not None:
                walk = math.dist(pos[here], e.step.at) * WALK_TILE
            nxt: Node = (e.step.dst, e.dst_region)
            c = cost + e.cost + walk
            if c < best.get(nxt, math.inf):
                best[nxt] = c
                prev[nxt] = (here, e.step)
                pos[nxt] = e.land
                tie += 1
                heapq.heappush(pq, (c, tie, nxt))
    if goal is None:
        return None
    steps: list[Step] = []
    at = goal
    while at != first:
        here, step = prev[at]
        steps.append(step)
        at = here
    return Route(steps[::-1], best.get(goal, 0.0))


@lru_cache(maxsize=8)
def cached_graph(level: int, manor: int | None) -> _Graph:
    return build_graph(level, manor)
