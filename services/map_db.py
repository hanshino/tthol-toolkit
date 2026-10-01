"""Read-only access to map data inside tthol.sqlite (stages / monster_spawns / npc / map_warps).

This module never writes to the DB. Each call opens a short-lived connection so
the worker thread / async handlers can use it without sharing a cursor.
"""

from __future__ import annotations

import sqlite3

from services._paths import bundled

DB_PATH = bundled("tthol.sqlite")


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    con.text_factory = lambda b: b.decode("utf-8", errors="replace")
    return con


def all_stage_names() -> set[str]:
    """Return every distinct stage name as a UTF-8 string set. Used as a whitelist
    when scanning heap memory for the current map name — heuristic padding checks
    became unreliable after the 2026-05 game update, so we validate candidates
    against the canonical map list instead.
    """
    with _connect() as con:
        rows = con.execute("SELECT DISTINCT name FROM stages").fetchall()
        return {r["name"] for r in rows if r["name"]}


def stage_by_name(name: str) -> dict | None:
    if not name:
        return None
    with _connect() as con:
        row = con.execute(
            "SELECT id, name FROM stages WHERE name = ? LIMIT 1",
            (name,),
        ).fetchone()
        return {"id": row["id"], "name": row["name"]} if row else None


def monsters_on_stage(stage_id: int) -> list[dict]:
    """Aggregate monster spawns by npc_id with level / hp / drop info."""
    with _connect() as con:
        rows = con.execute(
            """
            SELECT
                s.npc_id AS npc_id,
                COUNT(*) AS count,
                COALESCE(n.name, m.name) AS name,
                COALESCE(n.level, m.level) AS level,
                COALESCE(n.hp, m.hp) AS hp,
                m.drop_money_min AS drop_money_min,
                m.drop_money_max AS drop_money_max,
                m.drop_exp AS drop_exp
            FROM monster_spawns s
            LEFT JOIN npc n ON n.id = s.npc_id
            LEFT JOIN monsters m ON m.id = s.npc_id
            WHERE s.stage_id = ?
            GROUP BY s.npc_id
            ORDER BY level ASC, count DESC
            """,
            (stage_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def spawn_points(stage_id: int) -> list[dict]:
    with _connect() as con:
        rows = con.execute(
            """
            SELECT s.npc_id, s.x, s.y, COALESCE(n.name, m.name) AS name
            FROM monster_spawns s
            LEFT JOIN npc n ON n.id = s.npc_id
            LEFT JOIN monsters m ON m.id = s.npc_id
            WHERE s.stage_id = ?
            """,
            (stage_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def warps_from_stage(stage_id: int) -> list[dict]:
    with _connect() as con:
        rows = con.execute(
            """
            SELECT
                w.dst_stage_id AS dst_stage_id,
                s.name AS dst_name,
                w.dst_tag AS dst_tag
            FROM map_warps w
            LEFT JOIN stages s ON s.id = w.dst_stage_id
            WHERE w.src_stage_id = ?
            ORDER BY s.name
            """,
            (stage_id,),
        ).fetchall()
        seen = set()
        out = []
        for r in rows:
            key = r["dst_stage_id"]
            if key in seen:
                continue
            seen.add(key)
            out.append(dict(r))
        return out


# ---- Minimap -------------------------------------------------------------
# Every minimap coordinate is a map pixel with a top-left origin: the space of
# map_images, map_placements.raw_x/raw_y and the player's pixel position
# (reader HP+636/+640). map_placements.tile_y is bottom-origin, so it is not used.


def minimap_base(stage_id: int) -> dict | None:
    """Stage name plus map pixel size and image row; None for an unknown stage."""
    with _connect() as con:
        row = con.execute(
            """
            SELECT s.id AS stage_id, s.name AS name,
                   i.url AS url, i.origin AS origin, i.tile_px AS tile_px,
                   COALESCE(i.map_w_tiles, d.width) AS w_tiles,
                   COALESCE(i.map_h_tiles, d.height) AS h_tiles
            FROM stages s
            LEFT JOIN map_images i ON i.stage_id = s.id
            LEFT JOIN map_dims d ON d.stage_id = s.id
            WHERE s.id = ?
            """,
            (stage_id,),
        ).fetchone()
        return dict(row) if row else None


def minimap_warps(stage_id: int) -> list[dict]:
    """One entry per walk-on warp zone: its centroid and every destination.

    A zone is the set of `arrival` points sharing an event tag; map_warps
    `map_event` rows say where that tag's script sends the player. Zones whose
    script does something else (traps, dialogue) have no warp row and are left
    out. Auto (`mpc_sec3`) warps carry no point and cannot be placed.
    """
    with _connect() as con:
        points = con.execute(
            """
            SELECT event_tag, AVG(raw_x) AS x, AVG(raw_y) AS y
            FROM map_placements
            WHERE stage_id = ? AND category = 'arrival' AND in_bounds = 1
              AND event_tag IS NOT NULL
            GROUP BY event_tag
            """,
            (stage_id,),
        ).fetchall()
        dests = con.execute(
            """
            SELECT DISTINCT w.event_tag AS event_tag, w.dst_stage_id AS stage_id, s.name AS name
            FROM map_warps w
            LEFT JOIN stages s ON s.id = w.dst_stage_id
            WHERE w.src_stage_id = ? AND w.warp_kind = 'map_event'
            ORDER BY w.dst_stage_id
            """,
            (stage_id,),
        ).fetchall()
    by_tag: dict[int, list[dict]] = {}
    for d in dests:
        by_tag.setdefault(d["event_tag"], []).append(
            {"stage_id": d["stage_id"], "name": d["name"] or ""}
        )
    return [
        {"x": round(p["x"]), "y": round(p["y"]), "destinations": by_tag[p["event_tag"]]}
        for p in points
        if p["event_tag"] in by_tag
    ]


def minimap_npcs(stage_id: int) -> list[dict]:
    with _connect() as con:
        rows = con.execute(
            """
            SELECT p.npc_id AS npc_id, p.raw_x AS x, p.raw_y AS y, n.name AS name
            FROM map_placements p
            LEFT JOIN npc n ON n.id = p.npc_id
            WHERE p.stage_id = ? AND p.category = 'npc' AND p.in_bounds = 1
            ORDER BY p.record_idx
            """,
            (stage_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def minimap_spawns(stage_id: int) -> list[dict]:
    with _connect() as con:
        rows = con.execute(
            """
            SELECT p.npc_id AS npc_id, p.raw_x AS x, p.raw_y AS y,
                   COALESCE(n.name, m.name) AS name,
                   COALESCE(n.level, m.level) AS level
            FROM map_placements p
            LEFT JOIN npc n ON n.id = p.npc_id
            LEFT JOIN monsters m ON m.id = p.npc_id
            WHERE p.stage_id = ? AND p.category = 'spawn' AND p.in_bounds = 1
            ORDER BY p.record_idx
            """,
            (stage_id,),
        ).fetchall()
        return [dict(r) for r in rows]
