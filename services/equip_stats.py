"""Stats of worn gear: the instance's own stats plus the enhancement bonuses.

The item instance in memory already holds the item's stats with 真元 inlays
applied (reader.read_item_stats). Enhancement (+N) is not baked in; the
tooltip shows it in two parts, both verified on a +5 160 cap:

* "+x": the `common` strong_formula row of the current level only, not a
  running sum (防禦 89+10, and 160帽+5 is 防禦+10).
* "(+x)": `bonus` rows that some levels add (160帽+5"1 護勁+24 -> 護勁 35(+24)).
  Taken as unlocked once reached, so every bonus row up to the current level
  counts. Only +5 has been checked in game.
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
        # item id -> {level: {"common" | "bonus": (column, value)}}
        self._levels: dict[int, dict[int, dict[str, tuple[str, int]]]] | None = None
        self._lock = threading.Lock()

    def _load(self) -> dict[int, dict[int, dict[str, tuple[str, int]]]]:
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

        per_list: dict[int, dict[int, dict[str, tuple[str, int]]]] = {}
        for list_id, raw in lists.items():
            levels: dict[int, dict[str, tuple[str, int]]] = {}
            try:
                data = json.loads(raw)["data"]
            except (TypeError, ValueError, KeyError):
                continue
            for step in data:
                for kind in ("common", "bonus"):
                    formula = formulas.get(step.get(kind))
                    if formula is None:
                        continue
                    col = BONUS_COLUMN.get(formula[0])
                    try:
                        value = int(formula[1])
                    except (TypeError, ValueError):
                        continue
                    if col:
                        levels.setdefault(step["level"], {})[kind] = (col, value)
            per_list[list_id] = levels
        return {item_id: per_list[lid] for item_id, lid in items if lid in per_list}

    def _steps(self, item_id: int) -> dict[int, dict[str, tuple[str, int]]]:
        with self._lock:
            if self._levels is None:
                self._levels = self._load()
            return self._levels.get(item_id, {})

    def bonus(self, item_id: int, level: int) -> dict[str, int]:
        """The "+x" part at +level: that level's common row, as {column: value}."""
        step = self._steps(item_id).get(level, {}).get("common") if level > 0 else None
        return {step[0]: step[1]} if step else {}

    def extra(self, item_id: int, level: int) -> dict[str, int]:
        """The "(+x)" part at +level: every bonus row up to it, summed."""
        total: dict[str, int] = {}
        for lv, kinds in self._steps(item_id).items():
            if lv <= level and "bonus" in kinds:
                col, value = kinds["bonus"]
                total[col] = total.get(col, 0) + value
        return total


_table = EnhanceTable()


def enhance_bonus(item_id: int, level: int) -> list[ItemStat]:
    return to_stats(_table.bonus(item_id, level))


def enhance_extra(item_id: int, level: int) -> list[ItemStat]:
    return to_stats(_table.extra(item_id, level))
