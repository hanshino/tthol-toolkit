"""NPC shops for 補給: who sells what, and where they stand.

The game DB has no NPC -> shop column. The link is built from the dialogue:
a message whose trigger runs A5 (route_plan.OPEN_SHOP, a0 = shops.id) is
spoken by the shopkeeper, `messages.name_id` is that speaker's display name
(npc_strings, not npc.id), and the placed NPC with the same name is the one
to talk to. Names repeat across npc ids (one per town), so every placement of
the name counts, each on its own map.

Only gold shops (shops.style0 = 2) on no-fight maps (towns, markets) are used:
exchange shops take items as currency, and a cost-only pick must not send the
character into a fighting map for a 江湖商人.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

from services._paths import bundled
from services.guard import load_town_stages
from services.route_plan import OPEN_SHOP

GOLD = 2  # shops.style0 of a shop that takes 銀兩

Tile = tuple[int, int]


@dataclass(frozen=True)
class ShopNpc:
    npc_id: int
    name: str
    stage: int
    tile: Tile
    # (message, shops.id): the A5 messages this NPC speaks. Which of them a
    # character gets depends on its triggers (route_plan._Script.shops_from_msg).
    opens: tuple[tuple[int, int], ...]

    @property
    def shop_ids(self) -> frozenset[int]:
        return frozenset(s for _m, s in self.opens)


@dataclass(frozen=True)
class Buyable:
    item_id: int
    name: str
    type_name: str
    price: int  # the cheapest gold shop's price
    shops: int  # how many town shops sell it


@dataclass
class ShopCatalog:
    npcs: list[ShopNpc] = field(default_factory=list)
    sells: dict[int, dict[int, int]] = field(default_factory=dict)  # shop -> item -> price
    items: dict[int, Buyable] = field(default_factory=dict)

    def price(self, shop_ids, item_id: int) -> int | None:
        prices = [self.sells.get(s, {}).get(item_id) for s in shop_ids]
        prices = [p for p in prices if p is not None]
        return min(prices) if prices else None

    def sold_by(self, shop_ids) -> set[int]:
        out: set[int] = set()
        for s in shop_ids:
            out |= set(self.sells.get(s, {}))
        return out


def load_catalog(db_path: Path | None = None) -> ShopCatalog:
    path = db_path or bundled("tthol.sqlite")
    if not path.exists():
        return ShopCatalog()
    towns = load_town_stages(path)
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.text_factory = lambda b: b.decode("utf-8", errors="replace")
    try:
        gold = {r[0] for r in con.execute("SELECT id FROM shops WHERE style0 = ?", (GOLD,))}
        speakers: dict[str, set[tuple[int, int]]] = {}
        for msg, shop, name in con.execute(
            "SELECT t.msg_id, t.a0, s.name FROM trigger_ops t"
            " JOIN messages m ON m.file_no = t.file_no AND m.msg_id = t.msg_id"
            " JOIN npc_strings s ON s.id = m.name_id"
            " WHERE t.kind = 'A' AND t.op = ?",
            (OPEN_SHOP,),
        ):
            if shop in gold and name:
                speakers.setdefault(name, set()).add((msg, shop))
        npcs: dict[tuple[int, int], ShopNpc] = {}
        for npc_id, name, stage, x, y in con.execute(
            "SELECT n.id, n.name, p.stage_id, p.tile_x, p.tile_y FROM npc n"
            " JOIN map_placements p ON p.npc_id = n.id"
            " WHERE p.stage_kind = 'stage' AND p.category = 'npc' AND p.in_bounds = 1"
            " ORDER BY n.id, p.stage_id, p.id"
        ):
            opens = speakers.get(name)
            if not opens or stage not in towns or (npc_id, stage) in npcs:
                continue
            npcs[(npc_id, stage)] = ShopNpc(npc_id, name, stage, (x, y), tuple(sorted(opens)))
        used = {s for n in npcs.values() for s in n.shop_ids}
        sells: dict[int, dict[int, int]] = {}
        for shop, item, price in con.execute("SELECT shop_id, item_id, real_price FROM shop_sells"):
            if shop in used and price and price > 0:
                prices = sells.setdefault(shop, {})
                prices[item] = min(price, prices.get(item, price))
        counts: dict[int, int] = {}
        cheapest: dict[int, int] = {}
        for prices in sells.values():
            for item, price in prices.items():
                counts[item] = counts.get(item, 0) + 1
                cheapest[item] = min(price, cheapest.get(item, price))
        items: dict[int, Buyable] = {}
        if counts:
            marks = ",".join("?" * len(counts))
            for item_id, name, type_name in con.execute(
                f"SELECT id, name, type_name FROM items WHERE id IN ({marks})", tuple(counts)
            ):
                items[item_id] = Buyable(
                    item_id,
                    name or f"#{item_id}",
                    type_name or "",
                    cheapest[item_id],
                    counts[item_id],
                )
    finally:
        con.close()
    return ShopCatalog(list(npcs.values()), sells, items)


_cache: ShopCatalog | None = None
_lock = threading.Lock()


def catalog() -> ShopCatalog:
    global _cache
    with _lock:
        if _cache is None:
            _cache = load_catalog()
        return _cache


_weights: dict[int, int] | None = None


def item_weights(db_path: Path | None = None) -> dict[int, int]:
    """items.id -> items.weight, every item (sold or not), loaded once."""
    global _weights
    with _lock:
        if _weights is not None and db_path is None:
            return _weights
    path = db_path or bundled("tthol.sqlite")
    out: dict[int, int] = {}
    if path.exists():
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            out = {i: w or 0 for i, w in con.execute("SELECT id, weight FROM items")}
        finally:
            con.close()
    if db_path is None:
        with _lock:
            _weights = out
    return out
