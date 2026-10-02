"""帶我去: pick a tile near a stall for the click-to-walk runner.

Getting near is enough: once the player is close they can tell which stall it
is (user, 2026-10-02). So a character within NEAR_RADIUS does not walk at all,
and the goal is any walkable tile within GOAL_RADIUS of the stall, not taken
by another stall, cheapest to walk to. The stall's own tile is never the goal:
the seller stands there, and the runner's last click on a player stops the
walk ("clicked an NPC or player").
"""

from __future__ import annotations

from services import walk_path

Tile = tuple[int, int]

NEAR_RADIUS = 3  # tiles (Chebyshev): this close already counts as there
GOAL_RADIUS = 2  # tiles (Chebyshev) around the stall the goal may be on
FAR_PENALTY = 1.0  # walking tiles a goal must save per tile further from the stall


def _cost(plan: walk_path.WalkPlanResult) -> float:
    pts = plan.path
    return sum(abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in zip(pts, pts[1:]))


def _dist(a: Tile, b: Tile) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def pick_goal(
    stage_id: int, stall: Tile, start: Tile, occupied: set[Tile]
) -> tuple[Tile | None, str | None]:
    """(goal, reason): the tile to walk to near `stall`, or None and why not."""
    if _dist(start, stall) <= NEAR_RADIUS:
        return start, "already there"
    sx, sy = stall
    around = [
        (sx + dx, sy + dy)
        for dx in range(-GOAL_RADIUS, GOAL_RADIUS + 1)
        for dy in range(-GOAL_RADIUS, GOAL_RADIUS + 1)
        if (dx, dy) != (0, 0)
    ]
    grid = walk_path.load_grid(stage_id)
    if grid is None:
        # No walk mask: let the runner try the tile below the stall.
        return (sx, sy - 1), None
    best: tuple[float, int, Tile] | None = None
    for cell in around:
        if cell not in grid.walkable or cell in occupied:
            continue
        plan = walk_path.plan_on(grid, start, cell)
        if plan.goal != cell or not plan.path or plan.reason:
            continue
        d = _dist(cell, stall)
        key = (_cost(plan) + FAR_PENALTY * (d - 1), d, cell)
        if best is None or key < best:
            best = key
    if best is None:
        return None, "no walkable tile near the stall"
    return best[2], None
