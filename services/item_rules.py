"""道具處置: what to do with each item a character holds, saved per character.

Each item has one action; an item without a rule is left alone ("keep").
The standing guard runs "use_periodic" (keep the item's buff up) and
"use_on_status" (use it when the debuff it clears shows up). "sell" and
"store" are saved now and carried out later by the daily bag tidy-up, which
leaves `keep` of the item in the bag.

Which actions an item allows comes from the game DB (ItemFacts): no_shop /
no_store forbid selling / storing, no_use forbids using, and only a cure can be
used on a status.

What can be kept up follows the battle puppet's "能力增加道具" filter (per a
static read of the client, not checked in game): a POTION with item_time > 0
that raises at least one stat (PERIODIC_STATS). On top of that, a timed POTION
whose only effect is a friendly status (food: 針灸 regen; 辟火符 and the like) is
allowed too. NORMAL_ITEM is not: the hero transform item 英雄無雙 is a feature of
its own.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from services._paths import bundled

ITEMS_SECTION = "items"

KEEP, USE_PERIODIC, USE_ON_STATUS, SELL, STORE = (
    "keep",
    "use_periodic",
    "use_on_status",
    "sell",
    "store",
)

# Status groups that harm the one who has them (same set as the guard's).
HOSTILE_GROUPS = frozenset({14, 15, 17, 18, 19, 20, 21, 22, 23, 38, 39, 65, 66, 68, 69, 70, 71, 72})
# Groups a cure item may clear; 16 現形 is left out (現形丹 strips the user's own 隱形).
CURABLE_GROUPS = frozenset({14, 15, 17, 18, 19, 20, 21, 22, 23})
USABLE_TYPES = ("POTION", "NORMAL_ITEM")
# Stat columns the puppet's filter checks (any > 0); resistances are not among them.
PERIODIC_STATS = (
    "hp",
    "mp",
    "str",
    "pow",
    "vit",
    "dex",
    "agi",
    "wis",
    "atk",
    "matk",
    "extra_def",
    "magic_def",
    "hit",
    "dodge",
    "attack_speed",
    "critical_hit",
    "uncanny_dodge",
    "walk_speed",
    "run_speed",
)


@dataclass(frozen=True)
class ItemFact:
    name: str
    can_sell: bool
    can_store: bool
    periodic: bool  # gives a timed, non-hostile effect: can be kept up
    cure_group: int | None  # the debuff group it clears, when it is a cure
    effect: str | None  # short description for the UI

    @property
    def actions(self) -> list[str]:
        out = [KEEP]
        if self.periodic:
            out.append(USE_PERIODIC)
        if self.cure_group is not None:
            out.append(USE_ON_STATUS)
        if self.can_sell:
            out.append(SELL)
        if self.can_store:
            out.append(STORE)
        return out


def _fact(row, status_of) -> ItemFact:
    name, type_name, no_shop, no_store, no_use, value, item_time, extra, summary, *stats = row
    group, status_name = status_of(extra) if extra else (0, None)
    usable = not no_use and type_name in USABLE_TYPES
    cure_group = (
        group if usable and group in CURABLE_GROUPS and (summary or "").startswith("解除") else None
    )
    raises_stat = any((v or 0) > 0 for v in stats)
    # 16 現形 strips the user's own 隱形; a hostile group harms the user
    # (烤壞的肉串 raises a stat but slows): neither is worth keeping up, even
    # where the puppet's stat-only filter would list it.
    harmful = bool(extra) and (group in HOSTILE_GROUPS or group == 16)
    friendly_status = bool(extra) and not harmful
    periodic = (
        usable
        and type_name == "POTION"
        and cure_group is None
        and not harmful
        and (item_time or 0) > 0
        and (raises_stat or friendly_status)
    )
    if cure_group is not None:
        effect = f"解{status_name}"
    elif periodic and item_time:
        effect = f"效果 {item_time // 60} 分鐘" if item_time >= 60 else f"效果 {item_time} 秒"
    elif periodic:
        effect = status_name
    else:
        effect = None
    return ItemFact(
        name=name,
        can_sell=not no_shop and (value or 0) > 0,
        can_store=not no_store,
        periodic=periodic,
        cure_group=cure_group,
        effect=effect,
    )


def load_item_facts(db_path: Path | None = None) -> Callable[[int], ItemFact | None]:
    """items.id -> ItemFact, looked up lazily in the game DB and cached."""
    path = db_path or bundled("tthol.sqlite")
    cache: dict[int, ItemFact | None] = {}
    lock = threading.Lock()

    def lookup(item_id: int) -> ItemFact | None:
        with lock:
            if item_id in cache:
                return cache[item_id]
            found = _lookup(path, item_id) if path.exists() else None
            cache[item_id] = found
            return found

    return lookup


def _lookup(path: Path, item_id: int) -> ItemFact | None:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.text_factory = lambda b: b.decode("utf-8", errors="replace")
    try:
        row = con.execute(
            "SELECT name, type_name, no_shop, no_store, no_use, value, item_time,"
            " extra_status, summary, " + ", ".join(PERIODIC_STATS) + " FROM items WHERE id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            return None

        def status_of(status_id: int) -> tuple[int, str | None]:
            r = con.execute(
                'SELECT "group", name FROM status WHERE id = ?', (status_id,)
            ).fetchone()
            return (r[0] or 0, r[1]) if r else (0, None)

        return _fact(row, status_of)
    finally:
        con.close()
