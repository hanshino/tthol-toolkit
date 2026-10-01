"""Tests for the item metadata catalog behind GET /api/items."""

import sqlite3

from fastapi.testclient import TestClient

from services.item_catalog import ItemCatalog, category_for, clean_description

_STAT_COLS = (
    "str pow vit dex agi wis atk matk extra_def magic_def hit dodge critical_hit run_speed"
).split()
_COLS = [
    "id",
    "name",
    "note",
    "summary",
    "type_code",
    "equip_slot",
    "base_lv",
    "no_trade",
    "no_store",
    "no_drop",
    "item_time",
    "hp",
    "hp_flag",
    "mp",
    "mp_flag",
    *_STAT_COLS,
]


def _db(tmp_path, rows, with_images=True):
    path = tmp_path / "items.sqlite"
    con = sqlite3.connect(path)
    con.execute(f"CREATE TABLE items ({', '.join(_COLS)})")
    for row in rows:
        full = {c: None for c in _COLS} | row
        con.execute(
            f"INSERT INTO items VALUES ({', '.join('?' * len(_COLS))})", list(full.values())
        )
    if with_images:
        con.execute("CREATE TABLE item_images (item_id, kind, url)")
        con.execute("INSERT INTO item_images VALUES (24007, 'icon', 'https://img/icon.png')")
        con.execute("INSERT INTO item_images VALUES (24007, 'gicon', 'https://img/ground.png')")
    con.commit()
    con.close()
    return path


POTION = {
    "id": 24007,
    "name": "金創藥",
    "note": "不明道具",
    "summary": "回復體力。\\n\\n\\n\\n無法交易",
    "type_code": 24,
    "equip_slot": "",
    "hp": 6,
    "hp_flag": 2,
}
ARMOR = {
    "id": 23649,
    "name": "幻羽護甲",
    "note": "100神兵衣",
    "type_code": 18,
    "equip_slot": "BODY",
    "base_lv": 107,
    "hp": 660,
    "hp_flag": 1,
    "extra_def": 170,
    "no_trade": 1,
}


def test_lookup_returns_icon_category_and_description(tmp_path):
    cat = ItemCatalog(_db(tmp_path, [POTION, ARMOR]))
    potion, armor = cat.lookup([24007, 23649])
    # Served same-origin through the local icon cache, not straight from the host.
    assert potion.icon_url == "/api/items/24007/icon"
    assert cat.icon_url(24007) == "https://img/icon.png"  # inventory icon, not the ground one
    assert armor_has_no_icon(cat)
    assert potion.category == "potion"
    assert potion.type_label == "藥品"
    assert potion.description == "回復體力。\n\n無法交易"
    # A restore effect (flag 2) is described in the text, not listed as a stat.
    assert potion.stats == []
    assert armor.category == "gear"
    assert armor.type_label == "衣"
    assert armor.level == 107
    assert armor.no_trade is True
    assert [(s.label, s.value) for s in armor.stats] == [("體力", 660), ("防禦", 170)]


def armor_has_no_icon(cat):
    (armor,) = cat.lookup([23649])
    return armor.icon_url is None and cat.icon_url(23649) is None


def test_lookup_skips_unknown_ids_and_duplicates(tmp_path):
    cat = ItemCatalog(_db(tmp_path, [POTION]))
    assert [m.item_id for m in cat.lookup([24007, 999999, 24007])] == [24007]


def test_db_without_item_images_still_loads(tmp_path):
    cat = ItemCatalog(_db(tmp_path, [POTION], with_images=False))
    (potion,) = cat.lookup([24007])
    assert potion.icon_url is None


def test_category_rules():
    assert category_for(24, "商城物品") == "potion"  # shop buff pills stay with potions
    assert category_for(2, "不明的刀") == "gear"
    assert category_for(37, "武功秘笈2") == "book"
    assert category_for(32, "不明的寵飾") == "pet"
    assert category_for(29, "雙倍exp30") == "event"
    assert category_for(37, "商城物品") == "event"
    assert category_for(47, "不明道具") == "misc"
    assert category_for(None, None) == "misc"


def test_clean_description_handles_none():
    assert clean_description(None) == ""


def test_items_endpoint(monkeypatch, tmp_path):
    from services import item_catalog
    from services.api import build_app

    monkeypatch.setattr(item_catalog, "_catalog", ItemCatalog(_db(tmp_path, [POTION, ARMOR])))
    client = TestClient(build_app())
    r = client.get("/api/items", params={"ids": "23649,24007,5"})
    assert r.status_code == 200
    assert [m["item_id"] for m in r.json()] == [23649, 24007]
    assert client.get("/api/items", params={"ids": "1,x"}).status_code == 422
