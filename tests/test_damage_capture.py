"""Damage capture: result-table parsing, skill inference, summary, handle reads.

Byte fixtures are real 0x787750 tables captured live on 2026-10-03.
"""

import json
import struct

from reader import CHAR_OBJ_VTABLE
from services.damage_capture import (
    HANDLE_TABLE_OFFSET,
    MON_DEBUFF_ARRAY_OFFSET,
    MON_DEBUFF_COUNT_OFFSET,
    OBJ_HANDLE_OFFSET,
    OBJ_HP_PCT_OFFSET,
    OBJ_NPC_ID_OFFSET,
    RESULT_TABLE_SIZE,
    SPRITE_MANAGER_PTR,
    STAGE_PROC_SELECTED_OFFSET,
    STAGE_PROC_SELF_HANDLE_OFFSET,
    STAGE_PROC_VTABLE,
    DamageRecorder,
    build_snapshot,
    GameData,
    combat_seconds,
    infer_skill,
    parse_record,
    read_target,
    summarize,
)
from tests.test_direct_inventory import FakePm

# Own normal hit (type 0, 2279) on handle 0x4801060A by self 0x47D8062F.
NORMAL_HIT = bytes.fromhex(
    "05000000040000002f06d8470a060148000000000100000000000000e708000001000000bd160000"
)
# Own crit (type 1, 4626).
NORMAL_CRIT = bytes.fromhex(
    "05000000040000002f06d8470a0601480000000001000000010000001212000001000000bd160000"
)
# Monster 0x486E05EC misses the player (rel 0xB, type 3).
MONSTER_MISS = bytes.fromhex(
    "0b00000004000000ec056e482f06d8470000000001000000030000000000000001000000bd160000"
)
# 千瘡百孔: rel 5, one frame (5, 7, cast_effect 0x8F), one segment, 39321.
SKILL_HIT = bytes.fromhex(
    "050000000100000005000000070000008f00000001000000000000009999000001000000bd160000"
)
# Something casting a 0xC5 skill on the player: rel 0xB.
OTHER_SKILL = bytes.fromhex(
    "0b000000010000000500000007000000c500000001000000030000000000000001000000bd160000"
)


def _pad(b):
    return b + bytes(RESULT_TABLE_SIZE - len(b))


def test_parses_normal_hit():
    rec = parse_record(_pad(NORMAL_HIT))
    assert rec.path == "normal"
    assert rec.rel == 5
    assert rec.attacker == 0x47D8062F
    assert rec.target == 0x4801060A
    assert rec.segments == ((0, 2279),)
    assert rec.damage == 2279


def test_span_ignores_trailing_words():
    # Words after the last segment are other script parameters; they must not
    # make an unchanged result look new.
    a = parse_record(_pad(NORMAL_HIT))
    tail = bytearray(_pad(NORMAL_HIT))
    tail[0x30] ^= 0xFF
    assert parse_record(bytes(tail)).span == a.span
    assert parse_record(_pad(NORMAL_CRIT)).span != a.span


def test_parses_skill_hit():
    rec = parse_record(_pad(SKILL_HIT))
    assert rec.path == "skill"
    assert rec.cast_effect == 0x8F
    assert rec.frame_key == (5, 7)
    assert rec.segments == ((0, 0x9999),)
    assert rec.attacker is None


def test_parses_multi_frame_skill():
    words = [5, 2, 5, 7, 0x90, 2, 0, 100, 1, 200, 5, 7, 0x90, 1, 0, 300]
    rec = parse_record(_pad(struct.pack(f"<{len(words)}I", *words)))
    assert rec.segments == ((0, 100), (1, 200), (0, 300))
    assert rec.damage == 600
    assert len(rec.span) == len(words) * 4


def test_skill_with_four_frames_is_not_a_normal_attack():
    # w1 == 4 also happens for a 4-frame skill; the handles tell them apart.
    words = [5, 4] + [5, 7, 0x8F, 1, 0, 10] * 4
    rec = parse_record(_pad(struct.pack(f"<{len(words)}I", *words)))
    assert rec.path == "skill"
    assert len(rec.segments) == 4


def test_rejects_garbage():
    assert parse_record(bytes(RESULT_TABLE_SIZE)) is None
    assert parse_record(_pad(struct.pack("<6I", 5, 1, 999, 7, 0x8F, 1))) is None


# ---- skill inference


EFFECTS = {(702, 20): 143, (103, 5): 143, (343, 3): 143, (192, 10): 324, (900, 1): 777}


def test_learned_skill_identifies_effect():
    g = infer_skill(143, [(702, 20), (192, 10)], EFFECTS)
    assert (g.magic_id, g.level, g.method) == (702, 20, "learned")


def test_two_learned_skills_are_ambiguous():
    g = infer_skill(143, [(702, 20), (103, 5)], EFFECTS)
    assert g.method == "ambiguous"
    assert g.magic_id is None
    assert g.candidates == (103, 702)


def test_unlearned_unique_effect():
    g = infer_skill(777, [], EFFECTS)
    assert (g.magic_id, g.method) == (900, "unique")


def test_unlearned_shared_effect_is_unknown():
    assert infer_skill(143, [], EFFECTS).method == "unknown"


def test_effect_matched_on_learned_level():
    # cast_effect can change with level: only the learned level counts.
    effects = {(103, 1): 143, (103, 9): 145, (500, 1): 145}
    assert infer_skill(145, [(103, 1)], effects).method == "unknown"


# ---- summary


def _hit(t, segs, path="normal", debuffs=None):
    dmg = sum(v for ty, v in segs if ty in (0, 1))
    target = {"debuffs": debuffs or []}
    return {"t": t, "path": path, "segments": segs, "damage": dmg, "target": target}


def test_combat_time_skips_long_gaps():
    # 0..2 s fighting, 30 s walking, 32..33 s fighting.
    assert combat_seconds([0, 1, 2, 32, 33], gap=5) == 3


def test_summary_rates_and_dps():
    hits = [
        _hit(0, [[0, 1000]]),
        _hit(1, [[1, 2000]], debuffs=[15]),
        _hit(2, [[2, 0]]),
        _hit(2.5, [[0, 4000]], path="skill"),
        _hit(40, [[0, 1000]]),
    ]
    s = summarize(hits, elapsed=50, gap=5)
    assert s["total_damage"] == 8000
    assert s["combat_seconds"] == 2.5
    assert s["combat_dps"] == 8000 / 2.5
    assert s["overall_dps"] == 8000 / 50
    assert s["misses"] == 1
    assert s["crit_rate"] == 1 / 3  # normal-path landed segments only
    assert s["debuffed_share"] == 1 / 5


def test_summary_of_nothing():
    s = summarize([], 0)
    assert s["combat_dps"] == 0 and s["overall_dps"] == 0 and s["hits"] == 0


# ---- game reads (fake process)

MGR = 0x044AD658
TABLE = 0x044AE7D8
SELF_OBJ = 0x1C2E74A8
SELF_HANDLE = 0x47D8062F
MOB_OBJ = 0x220E7208
MOB_HANDLE = 0x4801060A
MOB2_OBJ = 0x220C6580
MOB2_HANDLE = 0x48020610
STAGE_PROC = 0x1B653970


def _world(pm, debuffs=(15,), hp_pct=72):
    pm.u32(SPRITE_MANAGER_PTR, MGR)
    pm.u32(MGR + HANDLE_TABLE_OFFSET, TABLE)
    for obj, h in ((SELF_OBJ, SELF_HANDLE), (MOB_OBJ, MOB_HANDLE), (MOB2_OBJ, MOB2_HANDLE)):
        pm.u32(TABLE + (h & 0xFFFF) * 4, obj)
        pm.u32(obj + OBJ_HANDLE_OFFSET, h)
    pm.u32(SELF_OBJ, CHAR_OBJ_VTABLE)
    pm.write(MOB_OBJ + OBJ_NPC_ID_OFFSET, struct.pack("<II", 5746, 6))
    pm.write(MOB2_OBJ + OBJ_NPC_ID_OFFSET, struct.pack("<II", 5746, 9))
    pm.u32(MOB_OBJ + OBJ_HP_PCT_OFFSET, hp_pct)
    pm.u32(MOB_OBJ + MON_DEBUFF_COUNT_OFFSET, len(debuffs))
    # A stale slot after the count must not be read.
    pm.write(MOB_OBJ + MON_DEBUFF_ARRAY_OFFSET, struct.pack("<3i", *debuffs, 99, 98)[:12])


def test_read_target_resolves_handle():
    pm = FakePm()
    _world(pm)
    t = read_target(pm, TABLE, MOB_HANDLE)
    assert t == {"handle": MOB_HANDLE, "npc_id": 5746, "instance": 6, "hp_pct": 72, "debuffs": [15]}


def test_read_target_rejects_reused_slot():
    pm = FakePm()
    _world(pm)
    # Same index, older counter: the object now belongs to another handle.
    assert read_target(pm, TABLE, (MOB_HANDLE & 0xFFFF) | 0x11110000) is None


def _recorder():
    data = GameData()
    data._loaded = True
    data.npcs = {5746: {"name": "▲惡道姑", "level": 94, "def": 134, "mdef": 50}}
    data.effects = {(702, 20): 143}
    data.skill_names = {702: "千瘡百孔"}
    rec = DamageRecorder(1, live=lambda: None, read_locked=lambda read: None, data=data)
    rec._st.t0 = 0.0  # elapsed() then runs off perf_counter; only order matters here
    rec._ctx = {"learned": [(702, 20)], "stage_proc": None, "self_obj": SELF_OBJ}
    return rec


def test_records_own_hits_and_borrows_skill_target():
    pm = FakePm()
    _world(pm)
    rec = _recorder()
    last = None
    for raw in (NORMAL_HIT, MONSTER_MISS, OTHER_SKILL, SKILL_HIT):
        last = rec._on_record(pm, parse_record(_pad(raw)), last)
    hits = [e for e in rec.status()["events"] if e["kind"] == "hit"]
    assert [h["path"] for h in hits] == ["normal", "skill"]  # monster hits dropped
    normal, skill = hits
    assert normal["target"]["name"] == "▲惡道姑"
    assert normal["target"]["source"] == "packet"
    assert normal["target"]["debuffs"] == [15]
    assert skill["target"]["source"] == "recent_attack"
    assert skill["target"]["instance"] == 6
    assert skill["skill"]["magic_id"] == 702
    assert skill["skill"]["method"] == "learned"
    assert skill["damage"] == 0x9999
    assert skill["not_mine_suspect"] is False
    assert rec.status()["summary"]["hits"] == 2


def test_skill_nobody_learned_is_suspect_and_left_out():
    pm = FakePm()
    _world(pm)
    rec = _recorder()
    rec._ctx["learned"] = [(192, 10)]  # does not have cast effect 0x8F
    rec._on_record(pm, parse_record(_pad(SKILL_HIT)), None, 0.0012)
    status = rec.status()
    (hit,) = status["events"]
    assert hit["not_mine_suspect"] is True
    assert hit["poll_gap_ms"] == 1.2
    assert status["summary"]["hits"] == 0


def test_ignores_normal_hits_by_someone_else():
    pm = FakePm()
    _world(pm)
    rec = _recorder()
    other = bytearray(_pad(NORMAL_HIT))
    other[8:12] = struct.pack("<I", 0x12340001)  # another attacker handle
    rec._on_record(pm, parse_record(bytes(other)), None)
    assert rec.status()["events"] == []


def test_export_header_and_lines():
    pm = FakePm()
    _world(pm)
    rec = _recorder()
    rec._on_record(pm, parse_record(_pad(NORMAL_HIT)), None)
    lines = rec.export_lines("1.3.0")
    header = json.loads(lines[0])
    assert header["schema"] == "tthol-damage/1"
    assert header["app"] == "1.3.0"
    assert json.loads(lines[1])["damage"] == 2279


def test_snapshot_leaves_out_the_character_name():
    from services.stat_sim_export import RawCharacter

    raw = RawCharacter(
        name="阿克婭", sect=2, level=96, bare=[1] * 6, remaining_points=0,
        equipment=[
            {"slot": "HAND_R", "item_id": 30412, "plus": 9, "zhenjie": 786},
            {"slot": "CAP", "item_id": 100, "plus": 0, "zhenjie": 0},
        ],
        skills=[], panel_attrs=[10] * 6,
        panel={"atk": 3412, "matk": 1086, "critical": 210, "hp": 9000},
        appearance=None, hair_item=0, hair_color=0,
        statuses=[(52, "buff"), (19, "debuff")],
    )  # fmt: skip
    data = GameData()
    data.items = {30412: "血飲雙刺"}
    data.statuses = {52: "八卦"}
    snap = build_snapshot(raw, data, [8, 0, 32])
    assert snap["sect_masks"] == [8, 0, 32]
    assert "阿克婭" not in json.dumps(snap, ensure_ascii=False)
    assert snap["panel"] == {"atk": 3412, "matk": 1086, "critical": 210}
    assert snap["weapons"] == [
        {"slot": "HAND_R", "item_id": 30412, "name": "血飲雙刺", "plus": 9, "zhenjie": 786}
    ]
    assert snap["buffs"] == [{"group": 52, "name": "八卦"}]


def _stage_proc(pm, self_handle, selected):
    pm.u32(STAGE_PROC, STAGE_PROC_VTABLE)
    pm.u32(STAGE_PROC + STAGE_PROC_SELF_HANDLE_OFFSET, self_handle)
    pm.u32(STAGE_PROC + STAGE_PROC_SELECTED_OFFSET, selected)


def test_skill_target_prefers_the_selected_target():
    pm = FakePm()
    _world(pm)
    _stage_proc(pm, SELF_HANDLE, MOB2_HANDLE)
    rec = _recorder()
    rec._ctx["stage_proc"] = STAGE_PROC
    last = rec._on_record(pm, parse_record(_pad(NORMAL_HIT)), None)  # hits instance 6
    rec._on_record(pm, parse_record(_pad(SKILL_HIT)), last)
    normal, skill = rec.status()["events"]
    assert normal["target"]["instance"] == 6 and normal["selected"]["instance"] == 9
    assert skill["target"]["instance"] == 9
    assert skill["target"]["source"] == "selected"


def test_self_handle_comes_from_stage_proc_without_a_lock():
    # The worker's lock (self_obj) can be gone mid-fight; CStageProc still
    # names the player.
    pm = FakePm()
    _world(pm)
    _stage_proc(pm, SELF_HANDLE, 0)
    rec = _recorder()
    rec._ctx.update(stage_proc=STAGE_PROC, self_obj=None)
    rec._on_record(pm, parse_record(_pad(NORMAL_HIT)), None)
    (hit,) = rec.status()["events"]
    assert hit["damage"] == 2279


# 千瘡百孔 procs 卸冑: a status-only result (type 5) of its own.
STATUS_ONLY = bytes.fromhex(
    "050000000100000005000000070000008f00000001000000050000000000000001000000bd160000"
)


def test_status_only_skill_result_is_flagged_not_a_miss():
    pm = FakePm()
    _world(pm)
    rec = _recorder()
    rec._on_record(pm, parse_record(_pad(STATUS_ONLY)), (0.0, MOB_HANDLE))
    status = rec.status()
    (hit,) = status["events"]
    assert hit["damage_lost_suspect"] is True
    assert status["summary"]["misses"] == 0


def test_damage_result_is_not_flagged_lost():
    pm = FakePm()
    _world(pm)
    rec = _recorder()
    rec._on_record(pm, parse_record(_pad(SKILL_HIT)), (0.0, MOB_HANDLE))
    assert rec.status()["events"][0]["damage_lost_suspect"] is False


def test_killing_blow_keeps_the_debuffs_from_before_the_hit():
    import time

    pm = FakePm()
    _world(pm, debuffs=(15,), hp_pct=40)
    rec = _recorder()
    rec._sample_targets(pm, (0.0, MOB_HANDLE), time.perf_counter())
    # The hit kills: by the time the result is read the debuff is gone.
    pm.u32(MOB_OBJ + MON_DEBUFF_COUNT_OFFSET, 0)
    pm.u32(MOB_OBJ + OBJ_HP_PCT_OFFSET, 0)
    rec._on_record(pm, parse_record(_pad(NORMAL_HIT)), None)
    (hit,) = rec.status()["events"]
    t = hit["target"]
    assert t["debuffs"] == [] and t["hp_pct"] == 0
    assert t["debuffs_before"] == [15] and t["hp_pct_before"] == 40
    assert rec.status()["summary"]["debuffed_share"] == 1.0
