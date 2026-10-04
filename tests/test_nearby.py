"""Tests for the live surroundings read from the sprite table (reader.scan_nearby,
services.nearby). The table and objects are built in a fake process."""

import struct

import pytest

from reader import (
    CHAR_NAME_OFFSET,
    CHAR_OBJ_HP_OFFSET,
    CHAR_OBJ_VTABLE,
    CHAR_STATE_OFFSET,
    CHAR_STATE_STALLING,
    OBJ_HANDLE_OFFSET,
    OBJ_KEY_OFFSET,
    OBJ_PX_X_OFFSET,
    OBJ_PX_Y_OFFSET,
    OBJ_TAG_OFFSET,
    SPRITE_MANAGER_PTR,
    SPRITE_TABLE_OFFSET,
    TILE_X_OFFSET,
    NearbyObject,
    scan_nearby,
)
from services import nearby


class FakePm:
    """pymem stand-in: a sparse byte map; reads that touch `unreadable` raise."""

    def __init__(self):
        self.mem: dict[int, int] = {}
        self.unreadable: set[int] = set()

    def write(self, addr, data):
        for i, b in enumerate(data):
            self.mem[addr + i] = b

    def u32(self, addr, value):
        self.write(addr, struct.pack("<I", value & 0xFFFFFFFF))

    def read_bytes(self, addr, n):
        if any(addr <= bad < addr + n for bad in self.unreadable):
            raise OSError(f"unreadable 0x{addr:08X}")
        return bytes(self.mem.get(addr + i, 0) for i in range(n))


MGR = 0x05000000
TABLE = 0x05100000


def _process():
    pm = FakePm()
    pm.u32(SPRITE_MANAGER_PTR, MGR)
    pm.u32(MGR + SPRITE_TABLE_OFFSET, TABLE)
    return pm


def _obj(
    pm, addr, index, npc_id, px, py, *, name=b"", tag=b"", state=9, hp=100, serial=0x1234, inst=7
):
    handle = (serial << 16) | index
    pm.u32(TABLE + index * 4, addr)
    pm.u32(addr, CHAR_OBJ_VTABLE)
    pm.u32(addr + OBJ_HANDLE_OFFSET, handle)
    pm.write(addr + OBJ_KEY_OFFSET, struct.pack("<HII", 10, npc_id, inst))
    pm.write(addr + OBJ_PX_X_OFFSET, struct.pack("<h", px))
    pm.write(addr + OBJ_PX_Y_OFFSET, struct.pack("<h", py))
    pm.write(addr + CHAR_NAME_OFFSET, name + b"\x00")
    pm.write(addr + OBJ_TAG_OFFSET, tag + b"\x00")
    pm.write(addr + CHAR_STATE_OFFSET, struct.pack("<i", state))
    pm.write(addr + CHAR_OBJ_HP_OFFSET, struct.pack("<i", hp))
    return handle


def test_scan_reads_each_live_object():
    pm = _process()
    h = _obj(pm, 0x06000000, 3, 5050, 1780, 3660, name="蠍子".encode("cp950"), hp=64)
    f = _obj(
        pm,
        0x06100000,
        4,
        5751,
        1820,
        3660,
        name="～絳雪～".encode("cp950"),
        tag="～翔奕～".encode("cp950"),
    )
    mob, follower = scan_nearby(pm)
    assert mob == NearbyObject(handle=h, npc_id=5050, instance=7, px=1780, py=3660,
                               name="蠍子", state=9, hp_pct=64, tag=None, is_self=False)  # fmt: skip
    assert (follower.handle, follower.name, follower.tag) == (f, "～絳雪～", "～翔奕～")


def test_scan_skips_garbage_freed_placeholder_and_stale_slots():
    pm = _process()
    pm.u32(TABLE + 0 * 4, 0xCDCDCDCD)  # garbage: above the user address range
    pm.u32(TABLE + 1 * 4, 0x06100000)  # unreadable pointer
    pm.unreadable.add(0x06100000)
    pm.u32(TABLE + 2 * 4, 0x06200000)  # readable, but not a CCharObject
    _obj(pm, 0x06300000, 4, 0, 100, 100)  # the client's npc.id 0 placeholders
    _obj(pm, 0x06400000, 5, 5050, 100, 100)
    pm.u32(0x06400000 + OBJ_HANDLE_OFFSET, (0x99 << 16) | 6)  # handle points at another slot
    keep = _obj(pm, 0x06500000, 9, 6150, 1140, 5179, name="曹大叔".encode("cp950"))
    assert [o.handle for o in scan_nearby(pm)] == [keep]


def test_scan_marks_the_viewer_and_strips_name_colour_codes():
    pm = _process()
    own = 0x06000000
    _obj(pm, own, 1, 60004, 1220, 4899, name=b"me", hp=33698)
    _obj(pm, 0x06100000, 2, 60004, 1500, 4619, name=b"/c#ff0000other")
    objs = scan_nearby(pm, own + CHAR_OBJ_HP_OFFSET)
    assert [(o.name, o.is_self) for o in objs] == [("me", True), ("other", False)]


NPCS = {
    5050: ("蠍子", 29, True),
    5751: ("●絳雪", 29, True),
    6150: ("曹大叔", 0, False),
    60004: ("武士男", 0, False),
}


def _raw(npc_id, px, py, *, handle=1, name=None, tag=None, state=9, hp=100, is_self=False):
    return NearbyObject(handle=handle, npc_id=npc_id, instance=1, px=px, py=py, name=name,
                        state=state, hp_pct=hp, tag=tag, is_self=is_self)  # fmt: skip


def test_build_classifies_places_and_sorts():
    objs = [
        _raw(6150, 1140, 5179, handle=1),  # NPC (28, 129)
        _raw(5050, 1220, 4980, handle=2, hp=140),  # monster (30, 124), hp clamped
        _raw(
            60004, 1500, 4619, handle=3, name="雷子雲", tag="鳳凰霸天", state=CHAR_STATE_STALLING
        ),  # (37, 115)
        _raw(
            5751, 1260, 4980, handle=6, name="～絳雪～", tag="雷子雲"
        ),  # is_monster, but a follower (31, 124)
        _raw(60004, 1260, 4619, handle=4, name="美中仙"),  # player (31, 115)
        _raw(60004, 1220, 4899, handle=5, name="me", is_self=True),
    ]
    out = nearby.build(objs, (30, 122), NPCS).entities
    assert [(e.kind, e.name, e.x, e.y, e.distance) for e in out] == [
        ("player", "雷子雲", 37, 115, 7),  # same distance: handle order
        ("player", "美中仙", 31, 115, 7),
        ("follower", "～絳雪～", 31, 124, 2),
        ("monster", "蠍子", 30, 124, 2),
        ("npc", "曹大叔", 28, 129, 7),
    ]
    stall, player, follower, monster, npc = out
    assert (stall.family, player.family, stall.owner) == ("鳳凰霸天", None, None)
    assert (follower.owner, follower.family, follower.level, follower.hp_pct) == (
        "雷子雲",
        None,
        29,
        None,
    )
    assert (player.stalling, stall.stalling) == (False, True)
    assert (monster.level, monster.hp_pct, player.hp_pct, player.level) == (29, 100, None, None)
    assert npc.level is None and npc.hp_pct is None


def test_build_without_own_tile_and_unknown_npc():
    [e] = nearby.build([_raw(9999, 80, 80, name="?")], None, NPCS).entities
    assert (e.kind, e.name, e.distance) == ("npc", "?", None)


def test_read_nearby_drops_the_unplaced_own_tile():
    pm = _process()
    own = 0x06000000
    _obj(pm, own, 1, 60004, 0, 0, name=b"me")
    hp_addr = own + CHAR_OBJ_HP_OFFSET
    pm.write(hp_addr + TILE_X_OFFSET, struct.pack("<ii", 44, 91))
    assert nearby.read_nearby(pm, hp_addr, False)[1] == (44, 91)
    pm.write(hp_addr + TILE_X_OFFSET, struct.pack("<ii", -1, -1))
    assert nearby.read_nearby(pm, hp_addr, False)[1] is None


class _FakeManager:
    def __init__(self, pm, hp_addr):
        self.pm, self.hp_addr = pm, hp_addr

    def read_locked(self, pid, read):
        return None if self.pm is None else read(self.pm, self.hp_addr, False)


async def _get(manager):
    from httpx import ASGITransport, AsyncClient

    from services.api import build_app

    app = build_app(services={"worker_manager": manager} if manager is not None else None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        return await ac.get("/api/characters/1001/nearby")


async def test_endpoint_lists_entities(monkeypatch):
    monkeypatch.setattr(nearby, "_npc_table", lambda: NPCS)
    pm = _process()
    own = 0x06000000
    _obj(pm, own, 1, 60004, 1220, 4899, name=b"me")
    _obj(pm, 0x06100000, 2, 5050, 1220, 4980, hp=40)
    hp_addr = own + CHAR_OBJ_HP_OFFSET
    pm.write(hp_addr + TILE_X_OFFSET, struct.pack("<ii", 30, 122))
    resp = await _get(_FakeManager(pm, hp_addr))
    assert resp.status_code == 200
    [e] = resp.json()["entities"]
    assert (e["kind"], e["name"], e["hp_pct"], e["x"], e["y"], e["distance"]) == (
        "monster",
        "蠍子",
        40,
        30,
        124,
        2,
    )


@pytest.mark.parametrize("manager, status", [(None, 503), (_FakeManager(None, 0), 409)])
async def test_endpoint_without_a_located_character(manager, status):
    assert (await _get(manager)).status_code == status
