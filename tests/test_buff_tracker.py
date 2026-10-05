import struct

import pytest

from services.api_types import BuffInfo
from services.buff_tracker import (
    FRESH,
    BuffDef,
    BuffTracker,
    decode_buff,
    load_buff_defs,
    merge_buffs,
)
from services.hook_cmd import NoReply

ICE, PILL, HERO, POISON = 71320, 28146, 30295, 18105
DEFS = {
    ICE: BuffDef("冰心靈訣", 20, 2, "buff"),
    PILL: BuffDef("賞善根骨丹", None, 0, "buff"),
    HERO: BuffDef("英雄無雙", None, 49, "hero"),
    POISON: BuffDef("百八蟲毒", 5, 19, "debuff"),
}


def pkt(code, on, ms):
    return bytes([0x29]) + struct.pack("<IBI", code, 1 if on else 0, ms)


def test_decode_buff_packet():
    # 28146 on, 600000 ms, as recorded on 2026-10-05.
    assert decode_buff(bytes.fromhex("29f26d000001c0270900")) == (PILL, True, 600000)
    assert decode_buff(pkt(HERO, False, 0)) == (HERO, False, 0)
    assert decode_buff(b"\x29\x00") is None
    assert decode_buff(b"\x06" + bytes(12)) is None


# ---- DB lookup ---------------------------------------------------------------


def test_load_buff_defs_from_the_game_db():
    from services._paths import bundled

    if not bundled("tthol.sqlite").exists():
        pytest.skip("tthol.sqlite not pulled")
    d = load_buff_defs()
    assert d(ICE) == BuffDef("冰心靈訣", 20, 2, "buff")
    assert d(PILL).name == "賞善根骨丹" and d(PILL).level is None
    assert d(HERO).kind == "hero"
    assert d(POISON).kind == "debuff" and d(POISON).name == "百八蟲毒"
    # Skill code that equals an items.id (中重擊赤魂石): the skill wins.
    assert d(26908).name == "疾風身法" and d(26908).level == 8
    assert d(1) is None


# ---- tracker -----------------------------------------------------------------


class FakeChannel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def send(self, pid, line, priority=0):
        self.sent.append(line)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def make(replies=(), connected=True):
    clock = {"t": 100.0, "wall": 1000.0}
    tr = BuffTracker(
        connected=lambda pid: connected,
        pids=lambda: [1],
        channel=FakeChannel(replies),
        defs=DEFS.get,
        clock=lambda: clock["t"],
        wall=lambda: clock["wall"],
    )
    return tr, clock


def names(tr):
    return [(b.name, b.expires_at) for b in tr.buffs(1)]


def test_nothing_known_until_the_first_packet_or_reply():
    tr, _ = make()
    assert tr.buffs(1) is None


def test_packets_add_and_remove_with_expiry():
    tr, _ = make()
    tr.on_packet(1, pkt(ICE, True, 600000), ts=1000.0)
    tr.on_packet(1, pkt(HERO, True, 295000), ts=1001.0)
    tr.on_packet(1, pkt(POISON, True, 30000), ts=1002.0)
    # hero first, then buffs, then debuffs
    assert names(tr) == [("英雄無雙", 1296.0), ("冰心靈訣", 1600.0), ("百八蟲毒", 1032.0)]
    b = tr.buffs(1)[1]
    assert (b.code, b.level, b.group, b.source) == (ICE, 20, 2, "hook")
    tr.on_packet(1, pkt(HERO, False, 0), ts=1100.0)
    assert [n for n, _ in names(tr)] == ["冰心靈訣", "百八蟲毒"]


def test_map_change_resend_refreshes_the_time_left():
    tr, _ = make()
    tr.on_packet(1, pkt(PILL, True, 600000), ts=1000.0)
    tr.on_packet(1, pkt(PILL, True, 582817), ts=1020.0)
    assert names(tr) == [("賞善根骨丹", pytest.approx(1602.817))]


def test_reconcile_fills_in_and_drops_stale():
    tr, clock = make(
        [
            {"ok": True, "buffs": [{"code": PILL, "group": 1101}, {"code": 76205, "group": 5205}]},
            {"ok": True, "buffs": [{"code": PILL, "group": 1101}]},
        ]
    )
    tr.on_packet(1, pkt(ICE, True, 600000), ts=1000.0)
    clock["t"] += FRESH  # 冰心 is no longer brand new
    tr.reconcile(1)
    got = {b.code: b for b in tr.buffs(1)}
    assert set(got) == {PILL, 76205}  # 冰心 was cleared (e.g. a map change)
    assert got[PILL].expires_at is None  # only seen in the list: no time left known
    assert got[76205].name == "#76205"  # not in the defs
    clock["t"] += 1
    tr.reconcile(1)
    assert [b.code for b in tr.buffs(1)] == [PILL]


def test_reconcile_keeps_a_buff_newer_than_the_reply():
    tr, _ = make([{"ok": True, "buffs": []}])
    tr.on_packet(1, pkt(ICE, True, 600000), ts=1000.0)
    tr.reconcile(1)  # same instant: the list may not show it yet
    assert [b.code for b in tr.buffs(1)] == [ICE]


def test_reconcile_failures_keep_the_list():
    tr, _ = make([NoReply("x"), {"ok": False, "error": "own character not found"}])
    tr.on_packet(1, pkt(ICE, True, 600000), ts=1000.0)
    tr.reconcile(1)
    tr.reconcile(1)
    assert [b.code for b in tr.buffs(1)] == [ICE]


def test_long_expired_entry_is_hidden():
    tr, clock = make()
    tr.on_packet(1, pkt(POISON, True, 30000), ts=1000.0)
    clock["wall"] = 1030.0 + FRESH + 0.1  # its off packet never came
    assert tr.buffs(1) == []


def test_hook_gone_means_unknown():
    tr, _ = make(connected=False)
    tr.on_packet(1, pkt(ICE, True, 600000), ts=1000.0)
    assert tr.buffs(1) is None


# ---- merge with the memory arrays ----------------------------------------------


def test_merge_adds_memory_debuffs_the_hook_does_not_have():
    hooked = [BuffInfo(group=2, name="冰心靈訣", kind="buff", code=ICE, source="hook")]
    memory = [
        BuffInfo(group=2, name="冰心", kind="buff"),
        BuffInfo(group=19, name="中毒", kind="debuff"),
    ]
    merged = merge_buffs(hooked, memory)
    assert [(b.name, b.source) for b in merged] == [("冰心靈訣", "hook"), ("中毒", "memory")]
    # A skill poison already in the hook list is not doubled.
    hooked.append(BuffInfo(group=19, name="百八蟲毒", kind="debuff", code=POISON, source="hook"))
    assert [b.name for b in merge_buffs(hooked, memory)] == ["冰心靈訣", "百八蟲毒"]
    assert merge_buffs(None, memory) == memory


# ---- HookHub hands 0x29 over -----------------------------------------------------


def test_hook_hub_routes_packets_to_listeners():
    from services.hook_hub import HookHub

    hub = HookHub(list_pids=lambda: [])
    got = []
    hub.add_packet_listener(0x29, lambda pid, raw, ts: got.append((pid, raw, ts)))
    raw = pkt(ICE, True, 600000)
    hub._ingest(7, {"t": "msg", "type": 900, "ts_us": 1_000_000, "raw": raw.hex()})
    hub._ingest(7, {"t": "msg", "type": 900, "raw": "0a00"})  # not asked for
    assert got == [(7, raw, 1.0)]


def test_worker_manager_swaps_in_the_hook_list():
    from services.worker_manager import WorkerManager

    class Tracker:
        def __init__(self, out):
            self.out = out

        def buffs(self, pid):
            return self.out

    memory = [BuffInfo(group=19, name="中毒", kind="debuff")]

    from services.api_types import CharacterDetail

    detail = CharacterDetail.model_construct(buffs=memory)
    hooked = [BuffInfo(group=2, name="冰心靈訣", code=ICE, source="hook")]
    wm = WorkerManager(buff_tracker=Tracker(hooked))
    assert [b.name for b in wm._with_hook_buffs(1, detail).buffs] == ["冰心靈訣", "中毒"]
    wm = WorkerManager(buff_tracker=Tracker(None))
    assert wm._with_hook_buffs(1, detail).buffs == memory
