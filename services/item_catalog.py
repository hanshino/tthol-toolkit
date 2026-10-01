"""Item metadata for the items page: icon, category, description, flags, stats.

Read once from the bundled tthol.sqlite and kept in memory (~13k rows). The
worker only knows item ids and quantities; the UI asks for metadata of the ids
it has not seen yet through GET /api/items.

`item_images` arrived with the 2026-10 DB refresh. An older DB without it still
works: every icon_url is just None.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path

from services._paths import bundled
from services.api_types import ItemMeta, ItemStat
from services.item_types import label_for

log = logging.getLogger("tthol.item_catalog")

DB_PATH = bundled("tthol.sqlite")

# type_code ranges; see services.item_types for the full code table.
_GEAR_CODES = frozenset(range(1, 23))  # weapons, armour, wings, mounts, ornaments
_POTION_CODES = frozenset({24})
_PET_CODES = frozenset({32, 33, 46})  # pet dolls, pet ornaments, mechanical companions
_EVENT_CODES = frozenset({29, 39})  # BONUS boxes, EVENT_ITEM

# (column, label). hp / mp are only shown as flat bonuses (flag 1): the other
# flags are restore or max-percent effects that the description already spells out.
STAT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("str", "外功"),
    ("pow", "內力"),
    ("vit", "根骨"),
    ("dex", "技巧"),
    ("agi", "身法"),
    ("wis", "玄學"),
    ("atk", "物攻"),
    ("matk", "內勁"),
    ("extra_def", "防禦"),
    ("magic_def", "護勁"),
    ("hit", "命中"),
    ("dodge", "閃躲"),
    ("critical_hit", "重擊"),
    ("run_speed", "移動速度"),
)
_FLAT_FLAG = 1


def category_for(type_code: int | None, tag: str | None) -> str:
    """Coarse grouping for the items page.

    `tag` is the items.note column: a short internal label (不明道具, 商城物品,
    武功秘笈2). Skill books are NORMAL_ITEMs tagged 武功秘笈*, since the DB has
    no dedicated type for them. Potions win over the shop check so shop buff
    pills still group with other potions.
    """
    code = type_code or 0
    tag = tag or ""
    if code in _POTION_CODES:
        return "potion"
    if code in _GEAR_CODES:
        return "gear"
    if tag.startswith("武功秘笈"):
        return "book"
    if code in _PET_CODES:
        return "pet"
    if code in _EVENT_CODES or "商城" in tag:
        return "event"
    return "misc"


def clean_description(text: str | None) -> str:
    """The DB stores line breaks as a literal backslash-n."""
    text = (text or "").replace("\\n", "\n").strip()
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text


def _stats(row: sqlite3.Row) -> list[ItemStat]:
    out: list[ItemStat] = []
    for col, label in (("hp", "體力"), ("mp", "真氣")):
        if row[col] and row[f"{col}_flag"] == _FLAT_FLAG:
            out.append(ItemStat(label=label, value=row[col]))
    out.extend(ItemStat(label=label, value=row[col]) for col, label in STAT_COLUMNS if row[col])
    return out


def icon_path(item_id: int) -> str:
    """Same-origin URL the UI loads an icon from; served via services.icon_cache."""
    return f"/api/items/{item_id}/icon"


def _meta(row: sqlite3.Row, has_icon: bool) -> ItemMeta:
    return ItemMeta(
        item_id=row["id"],
        name=row["name"] or "",
        type_label=label_for(row["type_code"] or 0, row["equip_slot"]),
        category=category_for(row["type_code"], row["note"]),
        level=row["base_lv"] or 0,
        # items.summary is the in-game description; items.note is only a tag.
        description=clean_description(row["summary"]),
        icon_url=icon_path(row["id"]) if has_icon else None,
        no_trade=bool(row["no_trade"]),
        no_store=bool(row["no_store"]),
        no_drop=bool(row["no_drop"]),
        effect_seconds=row["item_time"] or 0,
        stats=_stats(row),
    )


class ItemCatalog:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._db_path = db_path
        self._items: dict[int, ItemMeta] | None = None
        # item id -> remote icon URL on img.hanshino.dev
        self._icon_urls: dict[int, str] = {}
        self._lock = threading.Lock()

    def _load(self) -> tuple[dict[int, ItemMeta], dict[int, str]]:
        items: dict[int, ItemMeta] = {}
        try:
            con = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            log.error("item catalog unavailable: %s", exc, extra={"cat": "items"})
            return {}, {}
        try:
            con.row_factory = sqlite3.Row
            con.text_factory = lambda b: b.decode("utf-8", errors="replace")
            icons: dict[int, str] = {}
            try:
                icons = dict(
                    con.execute("SELECT item_id, url FROM item_images WHERE kind = 'icon'")
                )
            except sqlite3.OperationalError:
                log.warning("item_images missing; items render without icons")
            for row in con.execute("SELECT * FROM items"):
                items[row["id"]] = _meta(row, row["id"] in icons)
        except sqlite3.Error as exc:
            # Leave the cache empty so the next call retries.
            log.error("item catalog load failed: %s", exc, extra={"cat": "items"})
            return {}, {}
        finally:
            con.close()
        return items, icons

    def _ensure(self) -> dict[int, ItemMeta]:
        with self._lock:
            if not self._items:
                self._items, self._icon_urls = self._load()
            return self._items

    def lookup(self, ids: list[int]) -> list[ItemMeta]:
        items = self._ensure()
        return [items[i] for i in dict.fromkeys(ids) if i in items]

    def icon_url(self, item_id: int) -> str | None:
        """Remote URL of an item's inventory icon, or None if it has none."""
        self._ensure()
        return self._icon_urls.get(item_id)


_catalog = ItemCatalog()


def lookup(ids: list[int]) -> list[ItemMeta]:
    return _catalog.lookup(ids)


def icon_url(item_id: int) -> str | None:
    return _catalog.icon_url(item_id)
