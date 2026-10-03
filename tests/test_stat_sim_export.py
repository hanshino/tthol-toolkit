"""Tests for the genbu stat-sim export: the reader additions it needs (bare
attributes, positional sockets, item extras) and the TTHOL1 payload / codec.
Built in a fake byte-addressable process like the inventory tests."""

import base64
import datetime
import struct
import zlib

import pytest

import reader
from reader import (
    CHAR_OBJ_HP_OFFSET,
    CHAR_OBJ_VTABLE,
    DOLL_CAP_SEQ_OFFSET,
    DOLL_HEAD_SEQ_OFFSET,
    ENHANCE_OFFSET,
    EQUIP_SLOTS,
    HAIR_ITEM_OFFSET,
    INLAY_OFFSETS,
    ITEM_ID_OFFSET,
    ITEM_STATS_OFFSET,
    NAME_OFFSET,
    REFINE_LEFT_OFFSET,
    SKILL_COUNT_OFFSET,
    SKILL_IDS_OFFSET,
    SKILL_LEVELS_OFFSET,
    ZHENJIE_OFFSET,
)
from services import stat_sim_export as ex
from tests.test_direct_inventory import FakePm

OBJ = 0x287CE8C8
HP = OBJ + CHAR_OBJ_HP_OFFSET
CAP_INST = 0x28629F40
WEAPON_INST = 0x2862A400
SKILL_IDS = 0x1BC2CFF0
SKILL_LEVELS = 0x1BC2DB60
SLOT_OFFSET = {slot: off for off, slot in EQUIP_SLOTS}
AT = datetime.datetime(2026, 10, 3, 14, 5, tzinfo=datetime.timezone.utc)


def _i32(pm, addr, *values):
    pm.write(addr, struct.pack(f"<{len(values)}i", *values))


def _item(pm, slot, inst, item_id, enhance_raw=0):
    pm.u32(OBJ + SLOT_OFFSET[slot], inst)
    pm.write(inst + ITEM_ID_OFFSET, struct.pack("<i", item_id))
    pm.write(inst + ENHANCE_OFFSET, bytes([enhance_raw]))


def _character(pm, *, compat=False):
    """止戰詩園-like character: 天外天 Lv190, cap + weapon, three skills."""
    pm.u32(OBJ, CHAR_OBJ_VTABLE)
    pm.write(HP + NAME_OFFSET, "止戰詩園".encode("big5") + b"\x00")
    _i32(pm, HP + ex.SECT_OFFSET, 8)
    _i32(pm, HP + ex.LEVEL_OFFSET, 190)
    _i32(pm, HP + reader.BARE_ATTRS_OFFSET, 59, 1, 1, 144, 21, 40)
    _i32(pm, HP + reader.REMAINING_POINTS_OFFSET, 11)
    _i32(pm, HP + ex.PANEL_ATTRS_OFFSET, 69, 1, 1, 148, 21, 40)
    # current HP 30000 / max 32442, current MP 4000 / max 4530
    if compat:
        _i32(pm, HP, 32442, 30000, 4530, 4000)
    else:
        _i32(pm, HP, 30000, 32442, 4000, 4530)
    _i32(pm, HP + 28, 69300)
    _i32(pm, HP + 60, 17, 13)  # run speed, attack speed
    _i32(pm, HP + 72, 753, 753, 153, 573, 491, 372, 699, 42, 10)
    _i32(pm, HP + HAIR_ITEM_OFFSET, 29005, 1)
    _i32(pm, HP + DOLL_HEAD_SEQ_OFFSET, 100005, 101018)
    # buffs 31, 32; debuff 19
    _i32(pm, HP + reader.BUFF_COUNT_OFFSET, 2, 31, 32)
    _i32(pm, HP + 0x4C4, 1, 19)
    pm.u32(HP + SKILL_COUNT_OFFSET, 3)
    pm.u32(HP + SKILL_IDS_OFFSET, SKILL_IDS)
    pm.u32(HP + SKILL_LEVELS_OFFSET, SKILL_LEVELS)
    pm.write(SKILL_IDS, struct.pack("<3H", 21, 24, 1151))
    pm.write(SKILL_LEVELS, bytes([10, 6, 2]))

    _item(pm, "CAP", CAP_INST, 50401, enhance_raw=15)
    # hp 950 flat (flag 1), mp 650 flat (flag 0), def 89, mdef 35, hit 30
    pm.write(CAP_INST + ITEM_STATS_OFFSET, struct.pack("<hhhh", 950, 1, 650, 0))
    pm.write(CAP_INST + ITEM_STATS_OFFSET + 0x24, struct.pack("<hhh", 89, 35, 30))
    pm.write(CAP_INST + INLAY_OFFSETS[2], struct.pack("<H", 10737))
    pm.write(CAP_INST + INLAY_OFFSETS[3], struct.pack("<H", 10737))

    _item(pm, "HAND_R", WEAPON_INST, 55008, enhance_raw=15)
    pm.write(WEAPON_INST + ITEM_STATS_OFFSET + 0x08, struct.pack("<h", 6))  # str
    pm.write(WEAPON_INST + ITEM_STATS_OFFSET + 0x20, struct.pack("<h", 131))  # atk
    pm.write(WEAPON_INST + ITEM_STATS_OFFSET + 0x30, struct.pack("<h", 26))  # critical
    pm.write(WEAPON_INST + ITEM_STATS_OFFSET + 0x38, struct.pack("<hh", 1168, 1222))
    pm.write(WEAPON_INST + ZHENJIE_OFFSET, struct.pack("<I", 1912602754))
    pm.write(WEAPON_INST + REFINE_LEFT_OFFSET, bytes([2]))
    pm.write(WEAPON_INST + INLAY_OFFSETS[3], struct.pack("<H", 11111))
    return pm


# ---------------------------------------------------------------- reader


def test_bare_attrs_and_remaining_points():
    pm = _character(FakePm())
    assert reader.read_bare_attrs(pm, HP) == [59, 1, 1, 144, 21, 40]
    assert reader.read_remaining_points(pm, HP) == 11


def test_inlay_slots_keep_empty_sockets_in_place():
    pm = _character(FakePm())
    assert reader.read_item_inlay_slots(pm, CAP_INST) == [0, 0, 10737, 10737]
    assert reader.read_item_inlays(pm, CAP_INST) == [10737, 10737]


def test_equipment_detail_reads_sockets_and_extras():
    pm = _character(FakePm())
    detail = {e["slot"]: e for e in reader.read_equipment_detail(pm, HP)}
    weapon = detail["HAND_R"]
    assert weapon["item_id"] == 55008 and weapon["plus"] == 5
    assert weapon["inlays"] == [0, 0, 0, 11111]
    assert (weapon["zhenjie"], weapon["refine_left"]) == (1912602754, 2)
    assert detail["BODY"] == {
        "slot": "BODY",
        "item_id": None,
        "plus": 0,
        "stats": {},
        "inlays": [0, 0, 0, 0],
        "zhenjie": 0,
        "refine_left": 0,
    }
    # read_equipment keeps its tuple shape, filled sockets only.
    assert reader.read_equipment(pm, HP)[0] == (
        "CAP",
        50401,
        5,
        {"hp": 950, "mp": 650, "extra_def": 89, "magic_def": 35, "hit": 30},
        [10737, 10737],
    )


def test_equipment_detail_blanks_a_slot_with_a_bad_id():
    pm = _character(FakePm())
    pm.write(CAP_INST + ITEM_ID_OFFSET, struct.pack("<i", 0))
    cap = reader.read_equipment_detail(pm, HP)[0]
    assert cap["item_id"] is None and cap["inlays"] == [0, 0, 0, 0] and cap["stats"] == {}


# ---------------------------------------------------------------- payload


def test_payload_matches_the_tthol1_contract():
    payload = ex.build_payload(ex.read_character(_character(FakePm()), HP), "1.3.0", AT)
    assert payload["v"] == 1 and payload["app"] == "1.3.0"
    assert payload["at"] == "2026-10-03T14:05:00Z"
    assert (payload["name"], payload["sect"], payload["level"]) == ("止戰詩園", 8, 190)
    assert payload["bare"] == {"str": 59, "pow": 1, "vit": 1, "agi": 144, "dex": 21, "wis": 40}
    assert payload["remainingPoints"] == 11
    assert list(payload["equipment"]) == [
        "cap", "body", "foot", "right", "left", "wing", "horse",
        "ornament1", "ornament2", "ornament3",
    ]  # fmt: skip
    assert payload["equipment"]["body"] is None
    assert payload["equipment"]["cap"] == {
        "id": 50401,
        "plus": 5,
        "inlays": [0, 0, 10737, 10737],
        "stats": {"hp": 950, "mp": 650, "def": 89, "mdef": 35, "hit": 30},
        "refineLeft": 0,
    }
    right = payload["equipment"]["right"]
    # damage stays out of stats (genbu rejects unknown stats keys)
    assert right["stats"] == {"str": 6, "atk": 131, "critical": 26}
    assert right["damage"] == {"min": 1168, "max": 1222}
    assert right["zhenjie"] == 1912602754 and right["refineLeft"] == 2
    assert payload["skills"] == {"21": 10, "24": 6, "1151": 2}
    assert payload["panel"] == {
        "attributes": {"str": 69, "pow": 1, "vit": 1, "agi": 148, "dex": 21, "wis": 40},
        "hp": 32442, "mp": 4530, "atk": 753, "matk": 153, "def": 573, "mdef": 491,
        "hit": 372, "dodge": 699, "critical": 42, "uncanny_dodge": 10,
        "attack_speed": 13, "run_speed": 17, "weight_cap": 69300,
    }  # fmt: skip
    assert payload["appearance"] == {
        "gender": "m",
        "hairItem": 29005,
        "hairColor": 1,
        "head": 100005,
        "cap": 101018,
    }
    assert payload["statuses"] == {"buffs": [31, 32], "debuffs": [19]}


def test_every_stats_key_is_one_genbu_accepts():
    pm = _character(FakePm())
    # every int16 stat column set, damage and run_speed included
    pm.write(CAP_INST + ITEM_STATS_OFFSET, struct.pack("<35h", *([1] * 35)))
    payload = ex.build_payload(ex.read_character(pm, HP), "t", AT)
    stats = payload["equipment"]["cap"]["stats"]
    assert set(stats) <= ex.IMPORT_STAT_KEYS
    assert {"def", "mdef", "critical", "run_speed"} <= set(stats)


def test_compat_layout_reports_the_max_values():
    raw = ex.read_character(_character(FakePm(), compat=True), HP, compat_mode=True)
    assert (raw.panel["hp"], raw.panel["mp"]) == (32442, 4530)


def test_appearance_falls_back_to_the_hairstyle_for_gender():
    pm = _character(FakePm())
    _i32(pm, HP + DOLL_HEAD_SEQ_OFFSET, 0)  # head layer unreadable
    _i32(pm, HP + HAIR_ITEM_OFFSET, 29058, 77)
    payload = ex.build_payload(ex.read_character(pm, HP), "t", AT)
    assert payload["appearance"] == {"gender": "f", "hairItem": 29058, "hairColor": 0}
    _i32(pm, HP + HAIR_ITEM_OFFSET, 0)
    assert "appearance" not in ex.build_payload(ex.read_character(pm, HP), "t", AT)


def test_cap_is_left_out_when_none_is_drawn():
    pm = _character(FakePm())
    _i32(pm, HP + DOLL_CAP_SEQ_OFFSET, reader.DOLL_EMPTY_SEQ)
    assert "cap" not in ex.build_payload(ex.read_character(pm, HP), "t", AT)["appearance"]


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda pm: pm.u32(OBJ, 0x12345678),  # not a CCharObject
        lambda pm: _i32(pm, HP + ex.LEVEL_OFFSET, reader.FREED_FILL),  # freed on a map change
        lambda pm: _i32(pm, HP + reader.BARE_ATTRS_OFFSET, 0),  # bare attr below 1
        lambda pm: _i32(pm, HP + reader.REMAINING_POINTS_OFFSET, -1),
        lambda pm: pm.write(HP + NAME_OFFSET, b"\x00"),
        lambda pm: pm.u32(HP + SKILL_COUNT_OFFSET, 100000),  # read_skills raises
    ],
)
def test_incomplete_characters_are_not_exported(corrupt):
    pm = _character(FakePm())
    corrupt(pm)
    with pytest.raises(ex.NotReady):
        ex.read_character(pm, HP)


def test_unreadable_memory_is_not_ready():
    pm = _character(
        FakePm(unmapped=(HP + reader.BARE_ATTRS_OFFSET, HP + reader.BARE_ATTRS_OFFSET + 4))
    )
    with pytest.raises(ex.NotReady):
        ex.read_character(pm, HP)


# ---------------------------------------------------------------- codec


def test_encode_is_unpadded_base64url_of_raw_deflate():
    payload = ex.build_payload(ex.read_character(_character(FakePm()), HP), "t", AT)
    code = ex.encode(payload)
    prefix, body = code.split(".", 1)
    assert prefix == "TTHOL1"
    assert "=" not in body and set(body) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    )
    raw = zlib.decompress(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)), -15)
    assert raw.decode("utf-8").startswith('{"v":1,')
    assert ex.decode(code) == payload
    assert ex.decode(f"  {ex.import_url(code).split('#import=')[1]}\n") == payload


def test_decode_rejects_other_prefixes():
    with pytest.raises(ValueError):
        ex.decode("TTHOL2.abc")


# ---------------------------------------------------------------- API


class _FakeManager:
    """WorkerManager stand-in: runs the reader against a fake process."""

    def __init__(self, pm):
        self.pm = pm

    def read_locked(self, pid, read):
        return None if self.pm is None else read(self.pm, HP, False)


async def _get(manager):
    from httpx import ASGITransport, AsyncClient

    from services.api import build_app

    services = {"worker_manager": manager} if manager is not None else None
    app = build_app(services=services)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        return await ac.get("/api/characters/1001/stat-sim-export")


async def test_export_endpoint_returns_code_and_link():
    resp = await _get(_FakeManager(_character(FakePm())))
    assert resp.status_code == 200
    body = resp.json()
    assert body["url"] == ex.IMPORT_URL + body["code"]
    payload = ex.decode(body["code"])
    assert payload["name"] == "止戰詩園"
    from services.backup import APP_VERSION

    assert payload["app"] == APP_VERSION


async def test_export_endpoint_refuses_a_character_that_is_not_ready():
    assert (await _get(_FakeManager(None))).status_code == 409  # no lock
    pm = _character(FakePm())
    pm.u32(OBJ, 0)
    assert (await _get(_FakeManager(pm))).status_code == 409
    assert (await _get(None)).status_code == 503  # mock mode
