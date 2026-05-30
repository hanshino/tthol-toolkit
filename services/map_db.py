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
