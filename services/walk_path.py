"""Walk planning for click-to-move: a path over the walk mask, cut into clicks.

The cost model is genbu's ``walkPath`` (src/lib/queries/maps.ts): Dijkstra
with a wall-clearance penalty. Instead of string-pulling one polyline, the
Dijkstra runs from the goal and each click greedily takes the on-screen cell
with the lowest cost left, so a hop can step around an NPC or a UI panel that
covers the direct line. Everything
here works in game tile coordinates (origin bottom-left, y up); a game tile
(x, y) is walk_mask cell (x, H - 1 - y), see services/map_regions.py.

What the live probe on 成都少城 (2026-10-02) showed, and how it shapes this:

- Clicking client (400 + dx*40, 300 - dy*40) sends the character to exactly
  tile (x + dx, y + dy), but only on-screen clicks count, and UI panels
  swallow clicks. So a path is cut into hops, each one a click whose screen
  point clears the UI (NO_CLICK_RECTS).
- The game pathfinds between clicks on its own, so a hop behind a corner would
  let it pick its own route. Every hop is therefore in straight line of sight
  of the previous one.
- Walking near an ``arrival`` placement teleports (seen at Chebyshev 2), so
  those cells and a ring around them are walls, except the zone the goal is in.
- Clicking an NPC sprite walks to the NPC and opens its dialog instead. A
  sprite stands on its tile and reaches up about two tiles, and its hit box
  also covers the tile below, so hops never land there. Walking past an NPC is fine.
- The hop math assumes the camera is centred on the character. Near a map edge
  the camera may stop following; that offset is not measured yet.
"""

from __future__ import annotations

import heapq
import math
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache

from services.map_db import DB_PATH

ARRIVAL_RADIUS = 2  # Chebyshev tiles around an arrival cell that teleport
SNAP_RADIUS = 2  # a player mid-step can read a blocked cell next to the path

# Click geometry in the game's 800x600 client space.
TILE_PX = 40
CENTER_X, CENTER_Y = 400, 300
EDGE_MARGIN = 12  # keep clicks off the very edge of the client
# Screen rects (x0, y0, x1, y1) that a click must not land on: the skill bar,
# the chat log and input, and the bottom bar from the middle to the right,
# whose panels (個人狀態, 選單, ...) expand upward on hover and can be pinned.
NO_CLICK_RECTS = (
    (0, 0, 490, 52),
    (0, 470, 325, 600),
    (322, 420, 800, 600),
)
NPC_SPRITE_UP = 2  # tiles an NPC sprite reaches above its own tile
# Live 2026-10-02: a click one tile below NPC 6715 (成都少城 (81,139)) still hit it.
NPC_SPRITE_DOWN = 1
NPC_SPRITE_SIDE = 1  # tiles it reaches to either side

ORTHO = ((0, 1), (0, -1), (1, 0), (-1, 0))
DIRS = ORTHO + ((1, 1), (1, -1), (-1, 1), (-1, -1))


@dataclass
class MapGrid:
    width: int
    height: int
    walkable: set[tuple[int, int]]  # game tiles
    arrivals: list[tuple[int, int, int | None]]  # (x, y, event_tag)
    npcs: list[tuple[int, int]]


@dataclass
class WalkPlanResult:
    path: list[tuple[float, float]] = field(default_factory=list)  # start + hops, tile centres
    hops: list[tuple[int, int]] = field(default_factory=list)  # tiles to click, in order
    goal: tuple[int, int] | None = None  # the target after snapping to a walkable tile
    reason: str | None = None  # why there is no plan, or why it stops short


@lru_cache(maxsize=8)
def load_grid(stage_id: int) -> MapGrid | None:
    con = sqlite3.connect(str(DB_PATH))
    try:
        row = con.execute(
            "SELECT width, height, walk_mask, stage_kind FROM map_walkability WHERE stage_id = ?",
            (stage_id,),
        ).fetchone()
        if row is None or not row[2] or len(row[2]) != row[0] * row[1]:
            return None
        placements = con.execute(
            """
            SELECT category, tile_x, tile_y, event_tag FROM map_placements
            WHERE stage_kind = ? AND stage_id = ? AND category IN ('arrival', 'npc')
              AND tile_x IS NOT NULL AND tile_y IS NOT NULL
            """,
            (row[3], stage_id),
        ).fetchall()
    finally:
        con.close()
    width, height, mask, _ = row
    walkable = {(i % width, height - 1 - i // width) for i, ch in enumerate(mask) if ch == "1"}
    arrivals = [(x, y, tag) for cat, x, y, tag in placements if cat == "arrival"]
    npcs = [(x, y) for cat, x, y, _ in placements if cat == "npc"]
    return MapGrid(width, height, walkable, arrivals, npcs)


def _snap(cells: set[tuple[int, int]], x: int, y: int, radius: int) -> tuple[int, int] | None:
    for r in range(radius + 1):
        ring = [
            (x + dx, y + dy)
            for dx in range(-r, r + 1)
            for dy in range(-r, r + 1)
            if max(abs(dx), abs(dy)) == r
        ]
        ring.sort(key=lambda c: (c[0] - x) ** 2 + (c[1] - y) ** 2)
        for c in ring:
            if c in cells:
                return c
    return None


ZONE_LINK = 3  # arrival cells this close (Chebyshev) belong to the same door


def _zones(grid: MapGrid) -> list[set[tuple[int, int]]]:
    """Arrival cells grouped by place, not by event tag.

    One tag can cover doors far apart (成都少城 tag 301 spans ~100 tiles) and
    some arrivals have no tag, so only cells close together count as one door.
    """
    left = [(x, y) for x, y, _ in grid.arrivals]
    zones: list[set[tuple[int, int]]] = []
    while left:
        group = [left.pop()]
        i = 0
        while i < len(group):
            gx, gy = group[i]
            for j in range(len(left) - 1, -1, -1):
                if max(abs(left[j][0] - gx), abs(left[j][1] - gy)) <= ZONE_LINK:
                    group.append(left.pop(j))
            i += 1
        zones.append(set(group))
    return zones


def _ring(zone: set[tuple[int, int]]) -> set[tuple[int, int]]:
    r = ARRIVAL_RADIUS
    return {(x + dx, y + dy) for x, y in zone for dx in range(-r, r + 1) for dy in range(-r, r + 1)}


def _teleport_cells(grid: MapGrid, goal: tuple[int, int]) -> set[tuple[int, int]]:
    """Cells that would set off a teleport, minus the door the goal itself is at."""
    out: set[tuple[int, int]] = set()
    for zone in _zones(grid):
        ring = _ring(zone)
        if goal not in ring:
            out |= ring
    return out


def _escape(walkable, blocked, start) -> list[tuple[int, int]] | None:
    """Shortest walk (BFS) from a start inside a teleport ring to the first cell outside all rings.

    The player can stand next to a door without being sent through it (e.g.
    right after arriving), and every neighbour is then inside the ring.
    """
    prev = {start: None}
    queue = [start]
    for cur in queue:
        if cur not in blocked:
            path = [cur]
            while prev[path[-1]] is not None:
                path.append(prev[path[-1]])
            return path[::-1]
        x, y = cur
        for dx, dy in ORTHO:  # orthogonal steps: no corner cutting to reason about
            k = (x + dx, y + dy)
            if k in walkable and k not in prev:
                prev[k] = cur
                queue.append(k)
    return None


def _clearance(cells: set[tuple[int, int]]) -> dict[tuple[int, int], int]:
    """Chebyshev distance from each open cell to the nearest closed one (multi-source BFS)."""
    dist: dict[tuple[int, int], int] = {}
    frontier = []
    for x, y in cells:
        if any((x + dx, y + dy) not in cells for dx, dy in DIRS):
            dist[(x, y)] = 1
            frontier.append((x, y))
    while frontier:
        nxt = []
        for x, y in frontier:
            d = dist[(x, y)] + 1
            for dx, dy in DIRS:
                c = (x + dx, y + dy)
                if c in cells and c not in dist:
                    dist[c] = d
                    nxt.append(c)
        frontier = nxt
    return dist


def _penalty(clearance: int) -> int:
    # Same constants as genbu: tuned by eye, not derived.
    if clearance >= 3:
        return 0
    if clearance == 2:
        return 2
    return 6


def _goal_distance(cells, clearance, goal) -> dict[tuple[int, int], float]:
    """Walking cost from every reachable cell to the goal (Dijkstra run from the goal).

    Edge cost is the step length times (1 + wall penalty), so a cell hugging a
    wall costs more than one in the middle of the corridor (genbu walkPath).
    """
    dist = {goal: 0.0}
    heap = [(0.0, goal)]
    done: set[tuple[int, int]] = set()
    while heap:
        cost, cur = heapq.heappop(heap)
        if cur in done:
            continue
        done.add(cur)
        x, y = cur
        for dx, dy in DIRS:
            k = (x + dx, y + dy)
            if k not in cells or k in done:
                continue
            # No corner cutting: a diagonal step needs both orthogonal neighbours open.
            if dx and dy and ((x + dx, y) not in cells or (x, y + dy) not in cells):
                continue
            step = math.sqrt(2) if dx and dy else 1.0
            pen = _penalty(min(clearance.get(cur, 0), clearance.get(k, 0)))
            nc = cost + step * (1 + pen)
            if nc < dist.get(k, math.inf):
                dist[k] = nc
                heapq.heappush(heap, (nc, k))
    return dist


def _traverse(a, b, visit) -> bool:
    """Visit every cell the segment a->b passes through (supercover, genbu traverseCells).

    a and b are in tile units (a cell centre is x + 0.5). Passing exactly through
    a grid corner visits both side cells, matching the no-corner-cutting rule.
    """
    col, row = math.floor(a[0]), math.floor(a[1])
    end_col, end_row = math.floor(b[0]), math.floor(b[1])
    if not visit(col, row):
        return False
    if (col, row) == (end_col, end_row):
        return True
    dx, dy = b[0] - a[0], b[1] - a[1]
    step_c = (dx > 0) - (dx < 0)
    step_r = (dy > 0) - (dy < 0)
    t_dx = abs(1 / dx) if dx else math.inf
    t_dy = abs(1 / dy) if dy else math.inf
    t_mx = ((col + 1 if step_c > 0 else col) - a[0]) / dx if dx else math.inf
    t_my = ((row + 1 if step_r > 0 else row) - a[1]) / dy if dy else math.inf
    for _ in range(100_000):
        if abs(t_mx - t_my) < 1e-9:
            if not visit(col + step_c, row) or not visit(col, row + step_r):
                return False
            col += step_c
            row += step_r
            t_mx += t_dx
            t_my += t_dy
        elif t_mx < t_my:
            col += step_c
            t_mx += t_dx
        else:
            row += step_r
            t_my += t_dy
        if not visit(col, row):
            return False
        if (col, row) == (end_col, end_row):
            return True
    return True


def click_point(frm: tuple[int, int], to: tuple[int, int]) -> tuple[int, int]:
    """Client point (800x600 space) that sends a character standing on `frm` to `to`."""
    return (CENTER_X + (to[0] - frm[0]) * TILE_PX, CENTER_Y - (to[1] - frm[1]) * TILE_PX)


def clickable(sx: int, sy: int) -> bool:
    if not (EDGE_MARGIN <= sx <= 800 - EDGE_MARGIN and EDGE_MARGIN <= sy <= 600 - EDGE_MARGIN):
        return False
    return not any(x0 <= sx < x1 and y0 <= sy < y1 for x0, y0, x1, y1 in NO_CLICK_RECTS)


def _npc_sprite_cells(grid: MapGrid) -> set[tuple[int, int]]:
    return {
        (nx + dx, ny + dy)
        for nx, ny in grid.npcs
        for dx in range(-NPC_SPRITE_SIDE, NPC_SPRITE_SIDE + 1)
        for dy in range(-NPC_SPRITE_DOWN, NPC_SPRITE_UP + 1)
    }


MAX_HOP_X = (800 - 2 * EDGE_MARGIN) // (2 * TILE_PX)  # tiles reachable in one click
MAX_HOP_Y = (600 - 2 * EDGE_MARGIN) // (2 * TILE_PX)
MAX_HOPS = 300


def _cut_hops(start, goal, cells, dist, npc_cells) -> tuple[list[tuple[int, int]], str | None]:
    """Pick clicks greedily: each the on-screen cell closest to the goal by walking cost.

    A candidate must be in straight line of sight (the game walks a click
    straight when it can), clear of the UI and of NPC sprites. Picking from the
    whole screen rather than only along one polyline lets a hop step around an
    NPC or a UI panel that covers the direct line.
    """

    def centre(c):
        return c[0] + 0.5, c[1] + 0.5

    hops: list[tuple[int, int]] = []
    cur = start
    while cur != goal and len(hops) < MAX_HOPS:
        best, best_d = None, dist[cur]
        for dx in range(-MAX_HOP_X, MAX_HOP_X + 1):
            for dy in range(-MAX_HOP_Y, MAX_HOP_Y + 1):
                cand = (cur[0] + dx, cur[1] + dy)
                d = dist.get(cand)
                if d is None or d >= best_d or cand in npc_cells:
                    continue
                if not clickable(*click_point(cur, cand)):
                    continue
                if _traverse(centre(cur), centre(cand), lambda c, r: (c, r) in cells):
                    best, best_d = cand, d
        if best is None:
            return hops, "no clickable step from here"
        hops.append(best)
        cur = best
    return hops, None if cur == goal else "too many steps"


@lru_cache(maxsize=16)
def safe_cells(stage_id: int, goal: tuple[int, int]) -> frozenset[tuple[int, int]]:
    """Walkable tiles outside every teleport ring except the goal's door (empty without a mask)."""
    grid = load_grid(stage_id)
    if grid is None:
        return frozenset()
    goal_cell = _snap(grid.walkable, *goal, SNAP_RADIUS) or goal
    return frozenset(grid.walkable - _teleport_cells(grid, goal_cell))


def plan(stage_id: int, start: tuple[int, int], goal: tuple[int, int]) -> WalkPlanResult:
    grid = load_grid(stage_id)
    if grid is None:
        return WalkPlanResult(reason="no walk mask for this map")
    return plan_on(grid, start, goal)


def plan_on(grid: MapGrid, start: tuple[int, int], goal: tuple[int, int]) -> WalkPlanResult:
    goal_cell = _snap(grid.walkable, *goal, SNAP_RADIUS)
    if goal_cell is None:
        return WalkPlanResult(reason="target is not walkable")
    start_cell = _snap(grid.walkable, *start, SNAP_RADIUS)
    if start_cell is None:
        return WalkPlanResult(reason="player is not on a walkable tile")
    if start_cell == goal_cell:
        return WalkPlanResult(path=[(start_cell[0] + 0.5, start_cell[1] + 0.5)], goal=goal_cell)
    blocked = _teleport_cells(grid, goal_cell)
    cells = grid.walkable - blocked
    if start_cell in blocked:
        way_out = _escape(grid.walkable, blocked, start_cell)
        if way_out is None:
            return WalkPlanResult(reason="target is not reachable on foot", goal=goal_cell)
        cells |= set(way_out)
    dist = _goal_distance(cells, _clearance(cells), goal_cell)
    if start_cell not in dist:
        return WalkPlanResult(reason="target is not reachable on foot", goal=goal_cell)
    # A sprite on the goal itself is fine: the user asked to go there.
    npc_cells = _npc_sprite_cells(grid) - {goal_cell}
    hops, reason = _cut_hops(start_cell, goal_cell, cells, dist, npc_cells)
    path = [(c[0] + 0.5, c[1] + 0.5) for c in [start_cell, *hops]]
    return WalkPlanResult(path=path, hops=hops, goal=goal_cell, reason=reason)
