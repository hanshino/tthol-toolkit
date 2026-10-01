"""Tests for reading inventory / warehouse straight from the engine objects.

The HP struct sits inside a CCharObject that holds item containers as
(count, pointer-array) pairs, and the warehouse is a CCharData reached through
the open warehouse window in the window-manager list. These tests build that
layout in a fake byte-addressable process.
"""

import struct

import pytest

from reader import (
    CHAR_DATA_VTABLE,
    CHAR_OBJ_HP_OFFSET,
    CHAR_OBJ_VTABLE,
    INVENTORY_COUNT_OFFSET,
    ITEM_ID_OFFSET,
    ITEM_QTY_OFFSET,
    MONEY_OFFSET,
    PET_INVENTORY_COUNT_OFFSET,
    PLAYER_HP_CHAIN_BASE,
    WAREHOUSE_COUNT_OFFSET,
    WAREHOUSE_DATA_DELTA,
    WAREHOUSE_WND_DATA_OFFSET,
    WINDOW_MANAGER_OFFSET,
    locate_warehouse,
    read_inventory,
    read_item_container,
    read_money,
    read_pet_inventory,
    read_warehouse,
)


class FakePm:
    """pymem stand-in over a sparse byte map; unmapped bytes read as 0.

    Reads overlapping the half-open range `unmapped` raise, like a read that runs
    off the end of a committed region.
    """

    def __init__(self, unmapped=None):
        self.mem: dict[int, int] = {}
        self.unmapped = unmapped

    def write(self, addr, data):
        for i, b in enumerate(data):
            self.mem[addr + i] = b

    def u32(self, addr, value):
        self.write(addr, struct.pack("<I", value & 0xFFFFFFFF))

    def read_bytes(self, addr, n):
        if self.unmapped and addr < self.unmapped[1] and addr + n > self.unmapped[0]:
            raise OSError(f"unmapped read at 0x{addr:08X}")
        return bytes(self.mem.get(addr + i, 0) for i in range(n))

    def read_int(self, addr):
        return struct.unpack("<i", self.read_bytes(addr, 4))[0]


def _item(pm, addr, item_id, qty):
    # item_id is unaligned at +5 in the instance
    pm.write(addr + ITEM_ID_OFFSET, struct.pack("<i", item_id))
    pm.write(addr + ITEM_QTY_OFFSET, struct.pack("<i", qty))
    return addr


def _container(pm, count_addr, arr_addr, items):
    pm.u32(count_addr, len(items))
    pm.u32(count_addr + 4, arr_addr)
    for i, ptr in enumerate(items):
        pm.u32(arr_addr + 4 * i, ptr)


OBJ = 0x1B9556E0
HP = OBJ + CHAR_OBJ_HP_OFFSET


def _char(pm):
    pm.u32(OBJ, CHAR_OBJ_VTABLE)
    pm.u32(HP + MONEY_OFFSET, 277956)
    bag = [_item(pm, 0x21000000, 24034, 200), _item(pm, 0x21000400, 24007, 49)]
    _container(pm, HP + INVENTORY_COUNT_OFFSET, 0x21100000, bag)
    pet = [_item(pm, 0x21000800, 28154, 40)]
    _container(pm, HP + PET_INVENTORY_COUNT_OFFSET, 0x21100100, pet)


def test_reads_bag_pet_bag_and_money():
    pm = FakePm()
    _char(pm)
    assert read_inventory(pm, HP) == [(24034, 200), (24007, 49)]
    assert read_pet_inventory(pm, HP) == [(28154, 40)]
    assert read_money(pm, HP) == 277956


def test_rejects_hp_addr_outside_a_char_object():
    # A same-HP false positive, or a client patch that moved the vtable, must not
    # be followed into arbitrary pointers.
    pm = FakePm()
    _char(pm)
    pm.u32(OBJ, 0x12345678)
    assert read_inventory(pm, HP) is None
    assert read_pet_inventory(pm, HP) is None
    assert read_money(pm, HP) is None


def test_empty_container_reads_no_pointers():
    pm = FakePm()
    pm.u32(0x30000000, 0)
    pm.u32(0x30000004, 0xDEADBEEF)
    assert read_item_container(pm, 0x30000000) == []


def test_implausible_count_raises():
    pm = FakePm()
    pm.u32(0x30000000, 0xCDCDCDCD)
    with pytest.raises(ValueError):
        read_item_container(pm, 0x30000000)


WM = 0x04589C28
CHAR_DATA = 0x1B919018


def _window_manager(pm, windows):
    gamewnd = 0x04252BC0
    pm.u32(PLAYER_HP_CHAIN_BASE, gamewnd)
    pm.u32(gamewnd + WINDOW_MANAGER_OFFSET, WM)
    for i, wnd in enumerate(windows):
        pm.u32(WM + 0x24 + 4 * i, wnd)


def test_warehouse_found_anywhere_in_the_window_list():
    pm = FakePm()
    other, warehouse = 0x21D20090, 0x1B574008
    # An unrelated window whose +0x138 points somewhere without a CCharData.
    pm.u32(other + WAREHOUSE_WND_DATA_OFFSET, 0x22000000)
    pm.u32(warehouse + WAREHOUSE_WND_DATA_OFFSET, CHAR_DATA + WAREHOUSE_DATA_DELTA)
    pm.u32(CHAR_DATA, CHAR_DATA_VTABLE)
    _container(
        pm, CHAR_DATA + WAREHOUSE_COUNT_OFFSET, 0x21200000, [_item(pm, 0x21000C00, 26966, 6)]
    )
    _window_manager(pm, [other, other, warehouse])

    assert locate_warehouse(pm) == CHAR_DATA
    assert read_warehouse(pm) == [(26966, 6)]


def test_warehouse_closed_returns_none():
    pm = FakePm()
    other = 0x21D20090
    pm.u32(other + WAREHOUSE_WND_DATA_OFFSET, 0x22000000)
    _window_manager(pm, [other])
    assert locate_warehouse(pm) is None
    assert read_warehouse(pm) is None


def test_bad_entries_are_skipped_not_fatal():
    # The game can be reallocating the bag mid-read: a null slot, a pointer into
    # unmapped memory and a garbage id must each drop only that entry.
    pm = FakePm(unmapped=(0x60000000, 0x70000000))
    good = _item(pm, 0x21000000, 24034, 200)
    garbage = _item(pm, 0x21000400, -1, 7)
    _container(pm, 0x30000000, 0x21100000, [good, 0, 0x60000010, garbage])
    assert read_item_container(pm, 0x30000000) == [(24034, 200)]


def test_null_array_pointer_with_items_raises():
    pm = FakePm()
    pm.u32(0x30000000, 3)
    pm.u32(0x30000004, 0)
    with pytest.raises(ValueError):
        read_item_container(pm, 0x30000000)


def test_warehouse_not_logged_in_returns_none():
    # Chain root still 0 (login screen) must read as "closed", not raise.
    assert locate_warehouse(FakePm()) is None
    assert read_warehouse(FakePm()) is None


def test_window_list_stops_at_unmapped_memory():
    # The window manager sits near the end of its region: the list read must stop
    # there instead of failing, and still find a warehouse before the boundary.
    warehouse = 0x1B574008
    pm = FakePm(unmapped=(WM + 0x24 + 4 * 70, WM + 0x10000))
    pm.u32(warehouse + WAREHOUSE_WND_DATA_OFFSET, CHAR_DATA + WAREHOUSE_DATA_DELTA)
    pm.u32(CHAR_DATA, CHAR_DATA_VTABLE)
    _container(
        pm, CHAR_DATA + WAREHOUSE_COUNT_OFFSET, 0x21200000, [_item(pm, 0x21000C00, 26966, 6)]
    )
    _window_manager(pm, [0x00001234, warehouse])  # first entry is below the pointer floor
    assert read_warehouse(pm) == [(26966, 6)]


def test_worker_names_items_with_unknown_fallback(monkeypatch):
    import services.worker as W
    from services.worker import ReaderWorker

    got: list = []
    w = ReaderWorker(
        pid=1,
        on_state=lambda _s: None,
        on_stats=lambda _s: None,
        on_inventory=got.append,
        on_warehouse=lambda _w: None,
        on_error=lambda _m, **_kw: None,
    )
    w._item_db = {24034: "中行血藥"}
    monkeypatch.setattr(W, "read_inventory", lambda _pm, _hp: [(24034, 200), (12345, 1)])
    w._do_inventory_scan(pm=object(), hp_addr=HP)
    assert got == [[(24034, 200, "中行血藥"), (12345, 1, "???")]]


def _auto_worker(errors, got):
    from services.worker import ReaderWorker

    w = ReaderWorker(
        pid=1,
        on_state=lambda _s: None,
        on_stats=lambda _s: None,
        on_inventory=lambda i: got.setdefault("inv", []).append(i),
        on_warehouse=lambda i: got.setdefault("wh", []).append(i),
        on_error=lambda m, **kw: errors.append(m),
        on_warehouse_open=lambda o: got.setdefault("open", []).append(o),
    )
    w._item_db = {24034: "中行血藥", 26966: "八星覺醒符"}
    return w


def test_auto_read_pushes_bag_and_open_warehouse():
    pm = FakePm()
    _char(pm)
    pm.u32(0x1B574008 + WAREHOUSE_WND_DATA_OFFSET, CHAR_DATA + WAREHOUSE_DATA_DELTA)
    pm.u32(CHAR_DATA, CHAR_DATA_VTABLE)
    _container(
        pm, CHAR_DATA + WAREHOUSE_COUNT_OFFSET, 0x21200000, [_item(pm, 0x21000C00, 26966, 6)]
    )
    _window_manager(pm, [0x1B574008])
    errors, got = [], {}
    _auto_worker(errors, got)._auto_read_items(pm, HP)
    assert got["inv"][0][0] == (24034, 200, "中行血藥")
    assert got["open"] == [True]
    assert got["wh"] == [[(26966, 6, "八星覺醒符")]]
    assert errors == []


def test_auto_read_closed_warehouse_keeps_quiet():
    # Warehouse closed and bag unreadable are routine on every poll: no error
    # reports (they would flood last_error), and no warehouse push.
    pm = FakePm()
    _window_manager(pm, [])
    errors, got = [], {}
    _auto_worker(errors, got)._auto_read_items(pm, HP)
    assert "inv" not in got
    assert "wh" not in got
    assert got["open"] == [False]
    assert errors == []
