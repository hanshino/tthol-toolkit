"""Chinese labels for item types.

The bundled DB carries `type_code` + `type_name` (English enums: BLADE, ARMOR)
but no Chinese label -- upstream tthol_data dropped the `items.type` column
that used to hold one. These tables reproduce it: derived from the last DB that
carried both columns, verified there as a bijection over all 13316 items the
two shared.

Two levels, because equip_slot matters but should not be load-bearing:

* ITEM_TYPE_LABELS is keyed on (type_code, equip_slot). type_code alone is too
  coarse -- code 22 covers 中飾 / 右飾 / 左飾 / 飾品[外裝], and code 18 does not
  separate 衣 from 衣[外裝]. type_name is left out of the key: it is 1:1 with
  type_code, so it would add nothing.
* BASE_TYPE_LABELS is keyed on type_code alone and catches a known type worn in
  a slot this file has not seen. Without it, upstream attaching an existing
  type to a new slot silently blanks the column; with it the item still reads
  刀 rather than nothing.

Codes 0 / 30 / 49 (base appearance, money, untyped) never carried a Chinese
label even in the old DB, so blank is the correct answer for them and callers
must not report them as gaps.
"""

from __future__ import annotations

ITEM_TYPE_LABELS: dict[tuple[int, str], str] = {
    (1, "HAND_L,HAND_R"): "劍",  # SWORD
    (2, "HAND_L,HAND_R"): "刀",  # BLADE
    (4, "HANDS"): "雙劍",  # HAMMER
    (6, "HANDS"): "棍",  # ROD
    (7, "HANDS"): "法杖",  # STAFF
    (8, "HANDS"): "拂塵",  # WHISK
    (9, "HAND_L"): "暗器",  # HIDDEN_WEAPON
    (10, "HAND_R"): "手甲",  # BOW
    (11, "HANDS"): "雙手刀",  # GREAT_SWORD
    (12, "EXTRA_HAND_L"): "盾[外裝]",  # SHIELD
    (12, "HAND_L"): "盾",  # SHIELD
    (13, "HAND_L,HAND_R"): "匕首",  # STING
    (14, "EXTRA_HAND_L"): "左武器[外裝]",  # CLAW
    (14, "EXTRA_HAND_R"): "右武器[外裝]",  # CLAW
    (14, "HANDS"): "扇",  # CLAW
    (15, "HANDS"): "手套",  # PUNCHER
    (16, "HANDS"): "拳刃",  # BOXING
    (17, "CAP"): "帽",  # HELMET
    (17, "EXTRA_CAP"): "帽[外裝]",  # HELMET
    (18, "BODY"): "衣",  # ARMOR
    (18, "EXTRA_BODY"): "衣[外裝]",  # ARMOR
    (19, "EXTRA_WING"): "背飾[外裝]",  # WING
    (19, "WING"): "背飾",  # WING
    (20, "EXTRA_FOOT"): "鞋[外裝]",  # BOOT
    (20, "FOOT"): "鞋",  # BOOT
    (21, "EXTRA_HORSE"): "座騎[外裝]",  # HORSE
    (21, "HORSE"): "座騎",  # HORSE
    (22, "EXTRA_ORNAMENT_1"): "飾品[外裝]",  # ORNAMENT
    (22, "EXTRA_ORNAMENT_2"): "飾品[外裝]",  # ORNAMENT
    (22, "ORNAMENT_1"): "左飾",  # ORNAMENT
    (22, "ORNAMENT_2"): "中飾",  # ORNAMENT
    (22, "ORNAMENT_3"): "右飾",  # ORNAMENT
    (24, ""): "藥品",  # POTION
    (29, ""): "寶箱",  # BONUS
    (32, "ORNAMENT_PET"): "娃娃",  # ITEM_PET
    (33, "BOT_ORNAMENT_1"): "木寵飾",  # PET_ORNAMENT
    (33, "PET_ORNAMENT_1"): "火寵飾",  # PET_ORNAMENT
    (33, "PET_ORNAMENT_2"): "水寵飾",  # PET_ORNAMENT
    (33, "PET_WING"): "雷寵飾",  # PET_ORNAMENT
    (37, ""): "未知1",  # NORMAL_ITEM
    (38, ""): "真元/魂石",  # SCARCE_ITEM
    (39, ""): "未知2",  # EVENT_ITEM
    (46, "ORNAMENT_PET"): "機關人",  # ITEM_BOT
    (47, ""): "卷軸",  # RETURN_SCROLL
    (48, ""): "未知3",  # CASTLE_ITEM
}

BASE_TYPE_LABELS: dict[int, str] = {
    1: "劍",  # SWORD
    2: "刀",  # BLADE
    4: "雙劍",  # HAMMER
    6: "棍",  # ROD
    7: "法杖",  # STAFF
    8: "拂塵",  # WHISK
    9: "暗器",  # HIDDEN_WEAPON
    10: "手甲",  # BOW
    11: "雙手刀",  # GREAT_SWORD
    12: "盾",  # SHIELD
    13: "匕首",  # STING
    14: "扇",  # CLAW
    15: "手套",  # PUNCHER
    16: "拳刃",  # BOXING
    17: "帽",  # HELMET
    18: "衣",  # ARMOR
    19: "背飾",  # WING
    20: "鞋",  # BOOT
    21: "座騎",  # HORSE
    22: "左飾",  # ORNAMENT
    24: "藥品",  # POTION
    29: "寶箱",  # BONUS
    32: "娃娃",  # ITEM_PET
    33: "木寵飾",  # PET_ORNAMENT
    37: "未知1",  # NORMAL_ITEM
    38: "真元/魂石",  # SCARCE_ITEM
    39: "未知2",  # EVENT_ITEM
    46: "機關人",  # ITEM_BOT
    47: "卷軸",  # RETURN_SCROLL
    48: "未知3",  # CASTLE_ITEM
}


def label_for(type_code: int, equip_slot: str | None) -> str:
    """Chinese label for an item type, or "" when the type is unknown."""
    exact = ITEM_TYPE_LABELS.get((type_code, equip_slot or ""))
    if exact is not None:
        return exact
    return BASE_TYPE_LABELS.get(type_code, "")
