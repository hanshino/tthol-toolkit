"""Walkable regions of a map, for cropping the minimap to where the player is.

Many maps pack several disconnected spaces into one image (成都少城 is the city
plus a dozen interiors drawn below it). Each 4-connected component of
map_walkability.walk_mask is one such space; walk_mask cells are (col, row)
with row 0 at the top of the image.

The game's own coordinates (player position, map_placements.tile_y) have the
origin at the bottom-left, so a game tile (x, y) is mask cell (x, H - 1 - y).
Verified 2026-10-01: 42 walked samples on 成都少城 all land on walkable cells
under that mapping, 7% without the flip.
"""

from __future__ import annotations

import sqlite3
from collections import deque
from functools import lru_cache

from services.map_db import DB_PATH

MIN_REGION_CELLS = 12  # smaller components are stray walkable specks, not spaces
SNAP_RADIUS = 2  # a player mid-step can sit on a blocked cell next to the path


class Regions:
    def __init__(self, width: int, height: int, labels: list[int], boxes: list[tuple]) -> None:
        self.width = width
        self.height = height
        self._labels = labels  # per cell: component index, or -1 when blocked
        self.boxes = boxes  # per component: (col0, row0, col1, row1, cells)

    def at(self, x: int, y: int) -> int | None:
        """Component index for game tile (x, y), snapping to a nearby walkable cell."""
        col, row = x, self.height - 1 - y
        for r in range(SNAP_RADIUS + 1):
            for dr in range(-r, r + 1):
                for dc in range(-r, r + 1):
                    if max(abs(dr), abs(dc)) != r:
                        continue
                    c, w = col + dc, row + dr
                    if 0 <= c < self.width and 0 <= w < self.height:
                        label = self._labels[w * self.width + c]
                        if label >= 0 and self.boxes[label][4] >= MIN_REGION_CELLS:
                            return label
        return None


def _label(width: int, height: int, mask: str) -> Regions:
    labels = [-1] * (width * height)
    boxes: list[tuple] = []
    for start in range(width * height):
        if mask[start] != "1" or labels[start] != -1:
            continue
        idx = len(boxes)
        labels[start] = idx
        queue = deque([start])
        c0, r0, c1, r1, cells = width, height, -1, -1, 0
        while queue:
            i = queue.popleft()
            col, row = i % width, i // width
            cells += 1
            c0, c1 = min(c0, col), max(c1, col)
            r0, r1 = min(r0, row), max(r1, row)
            for nc, nr in ((col + 1, row), (col - 1, row), (col, row + 1), (col, row - 1)):
                if 0 <= nc < width and 0 <= nr < height:
                    j = nr * width + nc
                    if mask[j] == "1" and labels[j] == -1:
                        labels[j] = idx
                        queue.append(j)
        boxes.append((c0, r0, c1, r1, cells))
    return Regions(width, height, labels, boxes)


@lru_cache(maxsize=8)
def regions(stage_id: int) -> Regions | None:
    con = sqlite3.connect(str(DB_PATH))
    try:
        row = con.execute(
            "SELECT width, height, walk_mask FROM map_walkability WHERE stage_id = ?",
            (stage_id,),
        ).fetchone()
    finally:
        con.close()
    if row is None or not row[2] or len(row[2]) != row[0] * row[1]:
        return None
    return _label(row[0], row[1], row[2])


def region_box(stage_id: int, x: int, y: int, tile_px: int) -> tuple[int, int, int, int] | None:
    """Pixel box (x0, y0, x1, y1) in game coordinates of the space holding tile (x, y).

    y0 is the bottom edge, y1 the top edge (game y grows upward).
    """
    reg = regions(stage_id)
    if reg is None:
        return None
    label = reg.at(x, y)
    if label is None:
        return None
    c0, r0, c1, r1, _ = reg.boxes[label]
    return (
        c0 * tile_px,
        (reg.height - 1 - r1) * tile_px,
        (c1 + 1) * tile_px,
        (reg.height - r0) * tile_px,
    )
