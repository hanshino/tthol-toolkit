"""Read-only access to map data inside tthol.sqlite (stages / monster_spawns / npc / map_warps).

This module never writes to the DB. Each call opens a short-lived connection so
the worker thread / async handlers can use it without sharing a cursor.
"""

from __future__ import annotations

import re
import sqlite3

from services._paths import bundled

DB_PATH = bundled("tthol.sqlite")

TILE_PX = 40  # every map in the DB uses 40 px tiles


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
    """Spawn points in game tiles (bottom-left origin, like the player's x/y).

    monster_spawns.x/y are image pixels (top-left origin), so they are
    converted the same way map_placements derives tile_x/tile_y.
    """
    with _connect() as con:
        rows = con.execute(
            """
            SELECT s.npc_id,
                   CAST(ROUND(s.x / 40.0) AS INTEGER) AS x,
                   d.height - CAST(ROUND(s.y / 40.0) AS INTEGER) AS y,
                   COALESCE(n.name, m.name) AS name
            FROM monster_spawns s
            JOIN map_dims d ON d.stage_id = s.stage_id
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
# These return map_placements raw_x/raw_y: image pixels, top-left origin. The
# API flips y into game coordinates (bottom-left origin, like the player's
# position and map_placements.tile_y).


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


# Game dialogue text: <FONT COLOR=..> markup (sometimes missing its '>'), and
# line breaks written as a literal "\n", a real newline or a run of full-width
# spaces. Same rules as genbu's gameTextPlain (src/lib/format/game-text.ts).
_GAME_TEXT_TOKEN = re.compile(
    r"<FONT\s+COLOR\s*=\s*#?[0-9a-f]{1,8}\s*>?|</FONT\s*>|\\n|\r?\n|　{2,}|[ \t]{4,}",
    re.IGNORECASE,
)


def game_text_plain(raw: str | None) -> str | None:
    if not raw:
        return None
    out = _GAME_TEXT_TOKEN.sub(lambda m: "" if m.group(0)[0] == "<" else "\n", raw)
    text = re.sub(r"\n+", "\n", out).strip()
    return text or None


PORTAL_CLUSTER_PX = 120  # walk-on cells within 3 tiles (Chebyshev) form one exit


def _portal_clusters(cells: list[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    left = list(cells)
    out: list[list[tuple[int, int]]] = []
    while left:
        group = [left.pop()]
        i = 0
        while i < len(group):
            gx, gy = group[i]
            for j in range(len(left) - 1, -1, -1):
                x, y = left[j]
                if max(abs(x - gx), abs(y - gy)) <= PORTAL_CLUSTER_PX:
                    group.append(left.pop(j))
            i += 1
        out.append(group)
    return sorted(out, key=lambda g: (g[0][1], g[0][0]))


def portal_exits(stage_id: int) -> list[dict]:
    """Walk-on exits of a stage, ported from genbu's getPortalExits.

    Only `map_event` warps count: they are the exact A3 / A64 actions the map's
    own sec3 script fires. The legacy `mpc_sec3` byte scan and NPC dialogue
    warps are left out — the scan invents pairs (成都少城 -> 莫愁谷入口) and a
    dialogue warp has no spot on the map. An exit is a cluster of `arrival`
    cells sharing the event tag (a tag can cover several separate zones); a tag
    with no cells (e.g. a script-only warp) is not an exit. A tag whose script
    opens a dialogue menu yields several options, each with its menu text.
    """
    with _connect() as con:
        kind_row = con.execute("SELECT kind FROM stages WHERE id = ?", (stage_id,)).fetchone()
        if kind_row is None:
            return []
        kind = kind_row["kind"]
        warps = con.execute(
            """
            SELECT w.id, w.event_tag, w.dst_stage_id, s.kind AS dst_kind, s.name AS dst_name,
                   w.dst_tag, w.msg_file_no, w.msg_id, w.entry_msg_id, w.warp_op
            FROM map_warps w LEFT JOIN stages s ON s.id = w.dst_stage_id
            WHERE w.warp_kind = 'map_event' AND w.src_kind = ? AND w.src_stage_id = ?
            ORDER BY w.event_tag, w.id
            """,
            (kind, stage_id),
        ).fetchall()

        def text(file_no, msg_id):
            if file_no is None or msg_id is None:
                return None
            row = con.execute(
                "SELECT msg FROM messages WHERE file_no = ? AND msg_id = ?", (file_no, msg_id)
            ).fetchone()
            return game_text_plain(row["msg"]) if row else None

        by_tag: dict[int, list] = {}
        for w in warps:
            by_tag.setdefault(w["event_tag"], []).append(w)

        exits: list[dict] = []
        for tag, rows in by_tag.items():
            cells = [
                (r["raw_x"], r["raw_y"])
                for r in con.execute(
                    """
                    SELECT raw_x, raw_y FROM map_placements
                    WHERE stage_kind = ? AND stage_id = ? AND category = 'arrival'
                      AND event_tag = ?
                    ORDER BY raw_y, raw_x, id
                    """,
                    (kind, stage_id, tag),
                )
            ]
            groups = _portal_clusters(cells)
            if not groups:
                continue
            options = []
            for w in rows:
                landed = False
                if w["dst_kind"] is not None and w["dst_tag"] is not None:
                    landed = (
                        con.execute(
                            """
                            SELECT 1 FROM map_placements
                            WHERE stage_kind = ? AND stage_id = ? AND category = 'trigger'
                              AND tag_id = ? LIMIT 1
                            """,
                            (w["dst_kind"], w["dst_stage_id"], w["dst_tag"]),
                        ).fetchone()
                        is not None
                    )
                options.append(
                    {
                        "label": text(w["msg_file_no"], w["msg_id"]),
                        "stage_id": w["dst_stage_id"],
                        "name": w["dst_name"] or f"#{w['dst_stage_id']}",
                        "instance": w["warp_op"] == 64,
                        "landed": landed,
                    }
                )
            prompt = text(rows[0]["msg_file_no"], rows[0]["entry_msg_id"])
            for i, g in enumerate(groups):
                exits.append(
                    {
                        "key": f"{tag}-{i + 1}",
                        "event_tag": tag,
                        "part": i + 1,
                        "parts": len(groups),
                        "x": round(sum(p[0] for p in g) / len(g)),
                        "y": round(sum(p[1] for p in g) / len(g)),
                        "prompt": prompt,
                        "options": options,
                    }
                )
        return exits


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
