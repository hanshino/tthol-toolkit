"""Stats of worn gear: the instance's own stats plus the enhancement bonus.

The item instance in memory already holds the item's stats with 真元 inlays
applied (reader.read_item_stats). Enhancement (+N) is not baked in: the
tooltip shows it as a separate "+x" next to each stat. Its bonus is the
strong_formula row of the item's current level -- one row per level, not a
running sum (a 160 cap at +5 shows 防禦 89+10, and 160帽+5 is 防禦+10).

Each level can also list a "bonus" row (e.g. 160帽+5"1 護勁+24). Whether the
game grants it is not verified, so it is left out.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path

from services._paths import bundled
from services.api_types import ItemStat
from services.item_catalog import STAT_COLUMNS

log = logging.getLogger("tthol.equip_stats")

DB_PATH = bundled("tthol.sqlite")

# (items column, label) in display order; matches item_catalog's item stats.
LABELS: tuple[tuple[str, str], ...] = (
    ("hp", "體力"),
    ("mp", "真氣"),
    *STAT_COLUMNS,
    ("uncanny_dodge", "拆招"),  # only ever an enhancement bonus
)

# strong_formula.bonus_type -> items column
BONUS_COLUMN: dict[str, str] = {
    "ITEM_BONUS_HP": "hp",
    "ITEM_BONUS_MP": "mp",
    "ITEM_BONUS_STR": "str",
    "ITEM_BONUS_POW": "pow",
    "ITEM_BONUS_VIT": "vit",
    "ITEM_BONUS_DEX": "dex",
    "ITEM_BONUS_AGI": "agi",
    "ITEM_BONUS_WIS": "wis",
    "ITEM_BONUS_ATK": "atk",
    "ITEM_BONUS_MATK": "matk",
    "ITEM_BONUS_DEF": "extra_def",
    "ITEM_BONUS_MDEF": "magic_def",
    "ITEM_BONUS_HIT": "hit",
    "ITEM_BONUS_DODGE": "dodge",
    "ITEM_BONUS_CRITICAL": "critical_hit",
    "ITEM_BONUS_UNCANNYDODGE": "uncanny_dodge",
}


def to_stats(cols: dict[str, int]) -> list[ItemStat]:
    """{column: value} as labelled stats in display order, zeros dropped."""
    return [ItemStat(label=label, value=cols[col]) for col, label in LABELS if cols.get(col)]


class EnhanceTable:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._db_path = db_path
        # item id -> {level: (column, value)}
        self._levels: dict[int, dict[int, tuple[str, int]]] | None = None
        self._lock = threading.Lock()

    def _load(self) -> dict[int, dict[int, tuple[str, int]]]:
        try:
            con = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            log.error("enhance table unavailable: %s", exc, extra={"cat": "items"})
            return {}
        try:
            formulas = {
                fid: (btype, bval)
                for fid, btype, bval in con.execute(
                    "SELECT id, bonus_type, bonus_value FROM strong_formula"
                )
            }
            lists = dict(con.execute("SELECT id, list FROM strong_equipment"))
            items = con.execute(
                "SELECT id, strong_equipment FROM items WHERE strong_equipment > 0"
            ).fetchall()
        except sqlite3.Error as exc:
            log.warning("enhance tables missing; gear shows no +N bonus: %s", exc)
            return {}
        finally:
            con.close()

        per_list: dict[int, dict[int, tuple[str, int]]] = {}
        for list_id, raw in lists.items():
            levels: dict[int, tuple[str, int]] = {}
            try:
                data = json.loads(raw)["data"]
            except (TypeError, ValueError, KeyError):
                continue
            for step in data:
                formula = formulas.get(step.get("common"))
                if formula is None:
                    continue
                col = BONUS_COLUMN.get(formula[0])
                try:
                    value = int(formula[1])
                except (TypeError, ValueError):
                    continue
                if col:
                    levels[step["level"]] = (col, value)
            per_list[list_id] = levels
        return {item_id: per_list[lid] for item_id, lid in items if lid in per_list}

    def bonus(self, item_id: int, level: int) -> dict[str, int]:
        """Enhancement bonus of an item at +level as {column: value}."""
        if level <= 0:
            return {}
        with self._lock:
            if self._levels is None:
                self._levels = self._load()
            step = self._levels.get(item_id, {}).get(level)
        return {step[0]: step[1]} if step else {}


_table = EnhanceTable()


def enhance_bonus(item_id: int, level: int) -> list[ItemStat]:
    return to_stats(_table.bonus(item_id, level))
