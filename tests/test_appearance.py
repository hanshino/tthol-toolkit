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
    from reader import ENHANCE_OFFSET, EQUIP_SLOTS, ITEM_ID_OFFSET, read_equipment

    pm = FakePm()
    _char(pm)
    cap_off, body_off = EQUIP_SLOTS[0][0], EQUIP_SLOTS[1][0]
    pm.u32(OBJ + cap_off, 0x28629F40)
    pm.write(0x28629F40 + ITEM_ID_OFFSET, struct.pack("<i", 50401))
    pm.write(0x28629F40 + ENHANCE_OFFSET, bytes([15]))  # stored as N + 10
    pm.u32(OBJ + body_off, 0x00000044)  # not a heap pointer
    got = read_equipment(pm, HP)
    assert got[0] == ("CAP", 50401, 5)
    assert got[1] == ("BODY", None, 0)
    assert [slot for slot, _, _ in got] == [slot for _, slot in EQUIP_SLOTS]
    assert all(iid is None for _, iid, _ in got[2:])
    assert read_equipment(FakePm(), HP) is None


def test_enhancement_raw_values_outside_n_plus_10_read_as_zero():
    from reader import ENHANCE_OFFSET, EQUIP_SLOTS, ITEM_ID_OFFSET, read_equipment

    for raw, want in ((0, 0), (10, 0), (11, 1), (30, 20), (31, 0), (0xFF, 0)):
        pm = FakePm()
        _char(pm)
        pm.u32(OBJ + EQUIP_SLOTS[0][0], 0x28629F40)
        pm.write(0x28629F40 + ITEM_ID_OFFSET, struct.pack("<i", 50401))
        pm.write(0x28629F40 + ENHANCE_OFFSET, bytes([raw]))
        assert read_equipment(pm, HP)[0][2] == want, raw
