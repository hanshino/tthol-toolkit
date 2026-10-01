"""Tests for reading the current map straight from the CStage global."""

import struct

import reader
from reader import (
    STAGE_ID_OFFSET,
    STAGE_NAME_OFFSET,
    STAGE_PTR,
    STAGE_VTABLE,
    read_map_name,
    read_stage,
)

STAGE = 0x1B90BD28


class FakePm:
    """pymem stand-in over a sparse byte map; unmapped bytes read as 0."""

    def __init__(self):
        self.mem: dict[int, int] = {}

    def write(self, addr, data):
        for i, b in enumerate(data):
            self.mem[addr + i] = b

    def u32(self, addr, value):
        self.write(addr, struct.pack("<I", value & 0xFFFFFFFF))

    def read_bytes(self, addr, n):
        return bytes(self.mem.get(addr + i, 0) for i in range(n))

    def read_int(self, addr):
        return struct.unpack("<i", self.read_bytes(addr, 4))[0]


def make_stage(stage_id=48, name="龍虎山", vtable=STAGE_VTABLE):
    pm = FakePm()
    pm.u32(STAGE_PTR, STAGE)
    pm.u32(STAGE, vtable)
    pm.u32(STAGE + STAGE_ID_OFFSET, stage_id)
    pm.write(STAGE + STAGE_NAME_OFFSET, name.encode("big5") + b"\x00" + b"\xcd" * 8)
    return pm


def test_read_stage_returns_id_and_name():
    assert read_stage(make_stage(201, "仙水岩")) == (201, "仙水岩")


def test_read_stage_rejects_wrong_vtable():
    assert read_stage(make_stage(vtable=0x005F5DC4)) is None


def test_read_stage_rejects_null_global():
    assert read_stage(FakePm()) is None


def test_read_stage_rejects_empty_name():
    assert read_stage(make_stage(name="")) is None


def test_read_map_name_prefers_direct_read(monkeypatch):
    monkeypatch.setattr(reader, "locate_map_name", lambda *a, **k: "SCANNED")
    assert read_map_name(make_stage(), {"龍虎山"}) == "龍虎山"


def test_read_map_name_falls_back_to_scan(monkeypatch):
    monkeypatch.setattr(reader, "locate_map_name", lambda *a, **k: "SCANNED")
    assert read_map_name(FakePm(), {"龍虎山"}) == "SCANNED"
    # Name outside the DB whitelist is not trusted either
    assert read_map_name(make_stage(name="亂碼"), {"龍虎山"}) == "SCANNED"
