"""Tests for the head avatar: reading the doll sequences from the CCharObject
and turning them into image layers from the doll tables."""

import sqlite3
import struct

from reader import (
    CHAR_OBJ_HP_OFFSET,
    CHAR_OBJ_VTABLE,
    DOLL_CAP_SEQ_OFFSET,
    DOLL_EMPTY_SEQ,
    DOLL_HEAD_SEQ_OFFSET,
    HAIR_COLOR_OFFSET,
    HAIR_ITEM_OFFSET,
    read_appearance,
)
from services.doll_catalog import DollCatalog
from tests.test_direct_inventory import FakePm

OBJ = 0x287CE8C8
HP = OBJ + CHAR_OBJ_HP_OFFSET


def _char(pm, head=100005, cap=101018, color=1, hair=29005):
    pm.u32(OBJ, CHAR_OBJ_VTABLE)
    pm.write(HP + HAIR_ITEM_OFFSET, struct.pack("<i", hair))
    pm.write(HP + HAIR_COLOR_OFFSET, struct.pack("<i", color))
    pm.write(HP + DOLL_HEAD_SEQ_OFFSET, struct.pack("<i", head))
    pm.write(HP + DOLL_CAP_SEQ_OFFSET, struct.pack("<i", cap))


def test_reads_head_cap_and_dye():
    pm = FakePm()
    _char(pm)
    assert read_appearance(pm, HP) == {
        "gender": "m",
        "hair_item": 29005,
        "hair_color": 1,
        "head": 100005,
        "cap": 101018,
    }


def test_empty_cap_and_female_head():
    pm = FakePm()
    _char(pm, head=300008, cap=DOLL_EMPTY_SEQ, color=2, hair=29058)
    got = read_appearance(pm, HP)
    assert got["gender"] == "f" and got["head"] == 300008 and got["cap"] is None


def test_cap_of_other_gender_is_dropped():
    pm = FakePm()
    _char(pm, cap=301018)
    assert read_appearance(pm, HP)["cap"] is None


def test_out_of_range_dye_falls_back_to_zero():
    pm = FakePm()
    _char(pm, color=-842150451)
    assert read_appearance(pm, HP)["hair_color"] == 0


def test_bad_head_sequence_or_not_a_char_object():
    pm = FakePm()
    _char(pm, head=-842150451)
    assert read_appearance(pm, HP) is None
    pm = FakePm()
    assert read_appearance(pm, HP) is None


def _db(path):
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE doll_slot_rules (slot TEXT, dir INT, mirror_of INT, z_order INT,
            attach_to TEXT, attach_point INT);
        CREATE TABLE doll_frame_images (gender TEXT, slot TEXT, sequence INT, action TEXT,
            dir INT, url TEXT, width INT, height INT, anchor_x INT, anchor_y INT,
            color INT, points TEXT);
        INSERT INTO doll_slot_rules VALUES ('head', 6, 8, 4, 'body', 0);
        INSERT INTO doll_frame_images VALUES
            ('m', 'head', 100005, 'wait', 8, 'https://img/h0.png', 33, 31, 17, 27, 0, NULL),
            ('m', 'head', 100005, 'wait', 8, 'https://img/h1.png', 33, 31, 17, 27, 1, NULL),
            ('m', 'head', 100005, 'wait', 6, 'https://img/wrong.png', 1, 1, 0, 0, 1, NULL),
            ('m', 'cap', 101018, 'wait', 8, 'https://img/c0.png', 35, 26, 22, 34, 0, NULL);
        """
    )
    con.commit()
    con.close()


def test_avatar_layers_mirror_dir8_and_stack_head_under_cap(tmp_path):
    db = tmp_path / "t.sqlite"
    _db(db)
    cat = DollCatalog(db)
    av = cat.avatar(
        {"gender": "m", "hair_item": 29005, "hair_color": 1, "head": 100005, "cap": 101018}
    )
    assert av.mirror is True
    assert [layer.src for layer in av.layers] == [
        "/api/doll/m/head/100005.png?color=1",
        "/api/doll/m/cap/101018.png?color=0",
    ]
    assert cat.frame("m", "head", 100005, 1).url == "https://img/h1.png"
    # cap only ships color 0
    assert cat.frame("m", "cap", 101018, 1).url == "https://img/c0.png"


def test_avatar_none_without_head_art_or_doll_tables(tmp_path):
    db = tmp_path / "t.sqlite"
    _db(db)
    cat = DollCatalog(db)
    assert (
        cat.avatar({"gender": "m", "hair_item": 0, "hair_color": 0, "head": 100009, "cap": None})
        is None
    )
    empty = tmp_path / "empty.sqlite"
    sqlite3.connect(empty).close()
    assert (
        DollCatalog(empty).avatar(
            {"gender": "m", "hair_item": 0, "hair_color": 0, "head": 100005, "cap": None}
        )
        is None
    )


def test_reads_equipment_slots_and_skips_bad_pointers():
    from reader import (
        ENHANCE_OFFSET,
        EQUIP_SLOTS,
        ITEM_ID_OFFSET,
        INLAY_OFFSETS,
        ITEM_STATS_OFFSET,
        read_equipment,
    )

    pm = FakePm()
    _char(pm)
    cap_off, body_off = EQUIP_SLOTS[0][0], EQUIP_SLOTS[1][0]
    inst = 0x28629F40
    pm.u32(OBJ + cap_off, inst)
    pm.write(inst + ITEM_ID_OFFSET, struct.pack("<i", 50401))
    pm.write(inst + ENHANCE_OFFSET, bytes([15]))  # stored as N + 10
    # hp 950 flat (flag 1), mp 650 with a potion-only flag, def 89, mdef 35
    pm.write(inst + ITEM_STATS_OFFSET, struct.pack("<hhhh", 950, 1, 650, 2))
    pm.write(inst + ITEM_STATS_OFFSET + 0x24, struct.pack("<hh", 89, 35))
    # two 巨斧手小真元 sockets, filled from the back
    pm.write(inst + INLAY_OFFSETS[2], struct.pack("<H", 10737))
    pm.write(inst + INLAY_OFFSETS[3], struct.pack("<H", 10737))
    pm.u32(OBJ + body_off, 0x00000044)  # not a heap pointer
    got = read_equipment(pm, HP)
    assert got[0] == (
        "CAP",
        50401,
        5,
        {"hp": 950, "extra_def": 89, "magic_def": 35},
        [10737, 10737],
    )
    assert got[1] == ("BODY", None, 0, {}, [])
    assert [g[0] for g in got] == [slot for _, slot in EQUIP_SLOTS]
    assert all(g[1] is None for g in got[2:])
    assert read_equipment(FakePm(), HP) is None


def _strong_db(path):
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE items (id INT, strong_equipment INT);
        CREATE TABLE strong_equipment (id INT, name TEXT, list TEXT);
        CREATE TABLE strong_formula (id INT, bonus_type TEXT, bonus_value TEXT);
        INSERT INTO items VALUES (50401, 132), (21514, 0);
        INSERT INTO strong_formula VALUES
            (34614, 'ITEM_BONUS_DEF', '8'),
            (34615, 'ITEM_BONUS_DEF', '10'),
            (34641, 'ITEM_BONUS_MDEF', '24');
        """
    )
    con.execute(
        "INSERT INTO strong_equipment VALUES (132, '160~179', ?)",
        (
            '{"max":20,"data":[{"common":34614,"level":4},'
            '{"common":34615,"bonus":34641,"level":5}]}',
        ),
    )
    con.commit()
    con.close()


def test_enhance_bonus_is_the_current_level_row_not_a_running_sum(tmp_path):
    from services.equip_stats import EnhanceTable, to_stats

    db = tmp_path / "s.sqlite"
    _strong_db(db)
    table = EnhanceTable(db)
    # 160 cap +5 shows 防禦 89+10 in game: only the level-5 common row counts.
    assert table.bonus(50401, 5) == {"extra_def": 10}
    # ...and the +5 milestone row shows as 護勁 35(+24); not unlocked at +4.
    assert table.extra(50401, 5) == {"magic_def": 24}
    assert table.extra(50401, 4) == {}
    assert table.bonus(50401, 0) == {}
    assert table.bonus(21514, 5) == {}
    assert [(s.label, s.value) for s in to_stats({"extra_def": 10, "hp": 0})] == [("防禦", 10)]


def test_enhancement_raw_values_outside_n_plus_10_read_as_zero():
    from reader import ENHANCE_OFFSET, EQUIP_SLOTS, ITEM_ID_OFFSET, read_equipment

    for raw, want in ((0, 0), (10, 0), (11, 1), (30, 20), (31, 0), (0xFF, 0)):
        pm = FakePm()
        _char(pm)
        pm.u32(OBJ + EQUIP_SLOTS[0][0], 0x28629F40)
        pm.write(0x28629F40 + ITEM_ID_OFFSET, struct.pack("<i", 50401))
        pm.write(0x28629F40 + ENHANCE_OFFSET, bytes([raw]))
        assert read_equipment(pm, HP)[0][2] == want, raw


def test_inlays_group_by_stone_and_keep_the_first_effect_line(tmp_path):
    from services.equip_stats import InlayTable

    db = tmp_path / "c.sqlite"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE items (id INT, name TEXT);
        CREATE TABLE compounds (id INT, type TEXT, material_core_id INT, help TEXT);
        INSERT INTO items VALUES (25929, '巨斧手小真元'), (26651, '信風魂珠');
        INSERT INTO compounds VALUES
            (10737, 'ITEM_COMPOUND_EQUIPMENT', 25929, '防禦+27'),
            (10860, 'ITEM_COMPOUND_EQUIPMENT', 26651, '閃躲+25~70\\n\\n強化失敗將導致裝備毀損消失。'),
            (1, 'ITEM_COMPOUND_ITEM', 25929, 'not an inlay');
        """
    )
    con.commit()
    con.close()
    got = InlayTable(db).inlays([10737, 10860, 10737, 1, 999])
    assert [(i.item_id, i.name, i.count, i.effect) for i in got] == [
        (25929, "巨斧手小真元", 2, "防禦+27"),
        (26651, "信風魂珠", 1, "閃躲+25~70"),
    ]
