"""The item type column must stay Chinese after the upstream schema change.

tthol_data replaced the `items.type` column (Chinese: 刀 / 衣 / 盾) with
type_code + type_name (English enums: BLADE / ARMOR / SHIELD). snapshot_db read
`type` inside `except sqlite3.OperationalError: pass`, so the rename did not
raise -- it silently emptied both the name and the type map and every item in
the treasury rendered as "???". These tests pin the recovery.
"""

import sqlite3

from services.item_types import ITEM_TYPE_LABELS, label_for
from services.snapshot_db import ITEM_NAME_DB, _load_item_maps


def test_every_type_in_the_bundled_db_resolves_or_is_known_blank():
    """The only types allowed to render blank are the ones that never had a
    Chinese label: base appearance (0), money (30), untyped (49). Anything else
    means the DB grew a type this table does not know."""
    con = sqlite3.connect(f"file:{ITEM_NAME_DB}?mode=ro", uri=True)
    rows = con.execute("SELECT DISTINCT type_code, COALESCE(equip_slot, '') FROM items").fetchall()
    con.close()
    unlabelled = {c for c, s in rows if not label_for(c, s)}
    assert unlabelled == {0, 30, 49}, f"unexpected unlabelled type codes: {unlabelled}"


def test_a_known_type_in_an_unseen_slot_falls_back_to_its_base_label():
    """Upstream attaching an existing type to a new equip slot must not blank
    the column -- 特效右手刀 is code 2 (BLADE) in HAND_R, a pair the exact table
    never saw, and it still has to read 刀."""
    assert (2, "HAND_R") not in ITEM_TYPE_LABELS
    assert label_for(2, "HAND_R") == "刀"


def test_known_types_resolve_to_chinese():
    assert label_for(2, "HAND_L,HAND_R") == "刀"
    assert label_for(18, "BODY") == "衣"
    assert label_for(18, "EXTRA_BODY") == "衣[外裝]"


def test_unknown_type_is_blank_not_an_exception():
    assert label_for(9999, "NOPE") == ""


def test_item_maps_resolve_real_names_and_types():
    """The end-to-end path snapshot_db actually uses."""
    _load_item_maps.cache_clear() if hasattr(_load_item_maps, "cache_clear") else None
    import services.snapshot_db as sdb

    sdb._ITEM_MAPS_CACHE = None
    names, types = sdb._load_item_maps()
    assert names, "item names must resolve against the bundled DB schema"
    assert names.get(20000) == "銀兩"
    assert types.get(20001) == "刀", "type must come back as Chinese, not BLADE"
