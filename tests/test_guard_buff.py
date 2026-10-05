import pytest

from services.api_types import BuffInfo, GuardBuffRule, GuardConfig, GuardPotionRule
from services.guard import (
    BUFF_LEAD,
    CAST_GAP,
    CAST_HOLD,
    CAST_PAUSE,
    CAST_TRIES,
    HERO_CODE,
    BuffState,
    GuardManager,
    GuardStore,
    Sample,
    SelfBuff,
    _Run,
    active_skills,
    next_cast,
    read_learned,
    read_stage_id,
    record_cast,
    settle_casts,
)

ICE, BAGUA, SHIELD = 713, 762, 761
DEFS = {
    (ICE, 20): SelfBuff("冰心靈訣", 2, "冰心", 325, 600000, "self"),
    (BAGUA, 5): SelfBuff("八卦神武陣", 52, "八卦", 100, 240000, "self"),
    (SHIELD, 5): SelfBuff("冰霜烈炎盾", 51, "護體", 55, 300000, "self"),
}
LEARNED = {ICE: 20, BAGUA: 5, SHIELD: 5, 1: 1}
WALL0 = 1000.0


def skill_buff(mid, level, expires_at=None):
    return BuffInfo(
        group=0, name="x", code=mid * 100 + level, level=level, expires_at=expires_at, source="hook"
    )


# ---- catalog (real DB) ---------------------------------------------------------


def test_load_self_buffs_from_the_game_db():
    from services._paths import bundled
    from services.guard import load_self_buffs

    if not bundled("tthol.sqlite").exists():
        pytest.skip("tthol.sqlite not pulled")
    d = load_self_buffs()
    assert d[(713, 20)] == SelfBuff("冰心靈訣", 2, "冰心", 325, 600000, "self")
    assert d[(308, 7)].target == "ally"  # 炎胄靈甲
    assert d[(313, 10)].target == "group"  # 激攻符陣
    assert (461, 4) not in d  # 心有靈犀 needs the partner
    assert (305, 3) not in d  # 少淨神火 hits an enemy
    assert (181, 5) not in d  # 百八蟲毒 poisons


# ---- pure -----------------------------------------------------------------------


def test_active_skills_ignores_item_buffs():
    buffs = [
        skill_buff(ICE, 20, 1500.0),
        BuffInfo(group=0, name="賞善根骨丹", code=28146, source="hook"),  # item: level None
    ]
    assert active_skills(buffs) == {ICE: 1500.0}


def test_next_cast_picks_missing_or_ending_buffs_in_order():
    st = BuffState()
    ticked = [ICE, BAGUA]
    # both missing: the first ticked one
    assert next_cast(ticked, LEARNED, DEFS, {}, 9999, st, 0.0, WALL0) == (ICE, 20)
    # 冰心 on for a while, 八卦 missing
    assert next_cast(ticked, LEARNED, DEFS, {ICE: WALL0 + 60}, 9999, st, 0.0, WALL0) == (BAGUA, 5)
    # both on; time left unknown counts as on
    active = {ICE: None, BAGUA: WALL0 + 60}
    assert next_cast(ticked, LEARNED, DEFS, active, 9999, st, 0.0, WALL0) is None
    # 八卦 about to end
    active = {ICE: None, BAGUA: WALL0 + BUFF_LEAD - 0.1}
    assert next_cast(ticked, LEARNED, DEFS, active, 9999, st, 0.0, WALL0) == (BAGUA, 5)


def test_next_cast_skips_unlearned_unknown_and_short_of_mp():
    st = BuffState()
    assert next_cast([999], LEARNED, DEFS, {}, 9999, st, 0.0, WALL0) is None  # not learned
    assert next_cast([1], LEARNED, DEFS, {}, 9999, st, 0.0, WALL0) is None  # not a buff
    assert next_cast([ICE, SHIELD], LEARNED, DEFS, {}, 100, st, 0.0, WALL0) == (SHIELD, 5)


def test_cast_gap_hold_and_pause():
    st = BuffState()
    assert record_cast(st, ICE, 0.0) is False
    assert next_cast([ICE, BAGUA], LEARNED, DEFS, {}, 9999, st, CAST_GAP - 0.1, WALL0) is None
    # after the gap 冰心 is still held, 八卦 can go
    assert next_cast([ICE, BAGUA], LEARNED, DEFS, {}, 9999, st, CAST_GAP, WALL0) == (BAGUA, 5)
    assert next_cast([ICE], LEARNED, DEFS, {}, 9999, st, CAST_HOLD, WALL0) == (ICE, 20)
    t = CAST_HOLD
    for _ in range(CAST_TRIES - 1):
        assert record_cast(st, ICE, t) is False
        t += CAST_HOLD
    assert record_cast(st, ICE, t) is True  # one more miss: rest it
    assert next_cast([ICE], LEARNED, DEFS, {}, 9999, st, t + CAST_HOLD, WALL0) is None
    assert next_cast([ICE], LEARNED, DEFS, {}, 9999, st, t + CAST_PAUSE, WALL0) == (ICE, 20)


def test_settle_casts_resets_once_the_buff_is_on():
    st = BuffState()
    record_cast(st, ICE, 0.0)
    assert settle_casts(st, {}, WALL0) == []
    assert settle_casts(st, {ICE: WALL0 + 600}, WALL0) == [ICE]
    assert st.skills[ICE].tries == 0
    assert settle_casts(st, {ICE: WALL0 + 600}, WALL0) == []


def test_read_learned_wraps_read_skills(monkeypatch):
    monkeypatch.setattr("services.guard.read_skills", lambda pm, a: [(713, 20), (1, 1)])
    assert read_learned(None, 0, False) == {713: 20, 1: 1}
    monkeypatch.setattr("services.guard.read_skills", lambda pm, a: None)
    assert read_learned(None, 0, False) is None


# ---- manager ----------------------------------------------------------------------


class FakeChannel:
    def __init__(self, handle=27395721, cast_reply=None):
        self.sent = []
        self.handle = handle
        self.cast_reply = cast_reply or {"ok": True}

    def send(self, pid, line, priority=0):
        self.sent.append(line)
        if line == "status":
            return {"ok": True, "self": self.handle}
        if line.startswith("cast"):
            return self.cast_reply
        return {"ok": True}


def make(
    tmp_path, skills, buffs, mp=5000, channel=None, caps=("use", "cast", "status"), hero=False
):
    clock = {"t": 100.0}
    store = GuardStore()
    store.save(
        "寒江孤影",
        GuardConfig(
            potion=GuardPotionRule(hp_items=[], mp_items=[]),
            buff=GuardBuffRule(skills=skills, hero=hero),
        ),
    )
    state = {"buffs": buffs, "stage": (1, "莫愁谷入口")}

    def read_locked(pid, fn):
        if fn is read_learned:
            return dict(LEARNED)
        if fn is read_stage_id:
            return state["stage"]
        return Sample(1000, 1000, mp, 6000, {})

    mgr = GuardManager(
        read_locked=read_locked,
        character_name=lambda pid: "寒江孤影",
        channel=channel or FakeChannel(),
        store=store,
        potions=lambda: {},
        item_facts=lambda _i: None,
        self_buffs=lambda: DEFS,
        towns=lambda: frozenset({51}),
        buffs=lambda pid: state["buffs"],
        pipe_present=lambda pid: True,
        clock=lambda: clock["t"],
        wall=lambda: WALL0 + clock["t"],
    )
    mgr._caps[1] = frozenset(caps)
    run = _Run("寒江孤影", mgr.config(1))
    with mgr._lock:
        mgr._runs[1] = run
    return mgr, run, clock, state


def casts(mgr):
    return [line for line in mgr._channel.sent if line.startswith("cast")]


def test_casts_a_missing_buff_on_self_and_confirms(tmp_path):
    mgr, run, clock, state = make(tmp_path, [ICE], buffs=[])
    mgr._tick(1, run)
    assert casts(mgr) == ["cast 71320 27395721"]
    (line,) = [e for e in run.log if e.rule == "buff"]
    assert line.text == "補 冰心靈訣 Lv20" and line.phase == "sent"
    state["buffs"] = [skill_buff(ICE, 20, WALL0 + 700)]
    clock["t"] += 0.5
    mgr._tick(1, run)
    assert casts(mgr) == ["cast 71320 27395721"]
    assert line.phase == "confirmed" and line.text.endswith("（已生效）")
    assert run.casts == 1


def test_no_cast_without_hook_buff_list_or_cast_command(tmp_path):
    mgr, run, _, _ = make(tmp_path, [ICE], buffs=None)
    mgr._tick(1, run)
    assert casts(mgr) == []
    mgr, run, _, _ = make(tmp_path, [ICE], buffs=[], caps=("use",))
    mgr._tick(1, run)
    mgr._tick(1, run)
    assert casts(mgr) == []
    assert [e.text for e in run.log if e.rule == "buff"] == [
        "這個 hook 沒有 buff 維持要用的指令：cast、status"
    ]


def test_no_cast_when_short_of_mp_or_dead(tmp_path):
    mgr, run, _, _ = make(tmp_path, [ICE], buffs=[], mp=100)
    mgr._tick(1, run)
    assert casts(mgr) == []


def test_mid_map_change_waits_for_the_own_handle(tmp_path):
    mgr, run, _, _ = make(tmp_path, [ICE], buffs=[], channel=FakeChannel(handle=0))
    mgr._tick(1, run)
    assert casts(mgr) == []


def test_buff_that_never_shows_up_rests_in_one_line(tmp_path):
    mgr, run, clock, _ = make(tmp_path, [ICE], buffs=[])
    for _ in range(CAST_TRIES + 1):
        mgr._tick(1, run)
        clock["t"] += CAST_HOLD
    assert len(casts(mgr)) == CAST_TRIES + 1
    (line,) = [e for e in run.log if e.rule == "buff"]
    assert line.phase == "unconfirmed" and "都沒生效" in line.text
    mgr._tick(1, run)  # resting
    assert len(casts(mgr)) == CAST_TRIES + 1


def test_refused_cast_is_logged_and_held(tmp_path):
    ch = FakeChannel(cast_reply={"ok": False, "error": "skill not ready"})
    mgr, run, clock, _ = make(tmp_path, [ICE], buffs=[], channel=ch)
    mgr._tick(1, run)
    clock["t"] += 0.5
    mgr._tick(1, run)
    assert len(casts(mgr)) == 1
    assert any("skill not ready" in e.text for e in run.log)


def test_buff_candidates_lists_learned_self_buffs(tmp_path):
    mgr, _, _, _ = make(tmp_path, [], buffs=[skill_buff(ICE, 20, 1600.0)])
    got = {(c.magic_id, c.level, c.active, c.expires_at) for c in mgr.buff_candidates(1)}
    assert got == {(ICE, 20, True, 1600.0), (BAGUA, 5, False, None), (SHIELD, 5, False, None)}


# ---- pose ------------------------------------------------------------------------


KEY = bytes.fromhex("0100a0a10000e6000000")


def pose_pkt(pose, key=KEY):
    import struct

    return bytes([0x59]) + struct.pack("<HB", pose, 0) + key


def test_decode_pose():
    from services.guard import decode_pose

    assert decode_pose(pose_pkt(0x0D)) == (0x0D, KEY)
    assert decode_pose(b"\x59\x0d") is None


def test_sitting_pauses_casts_until_standing(tmp_path):
    mgr, run, clock, _ = make(tmp_path, [ICE], buffs=[])
    mgr.on_pose_packet(1, pose_pkt(0x0D, key=b"\x00" * 10), 0.0, KEY)  # someone else sat
    assert run.pose is None
    mgr.on_pose_packet(1, pose_pkt(0x0D), 0.0, KEY)
    mgr._tick(1, run)
    mgr._tick(1, run)
    assert casts(mgr) == []
    assert [e.text for e in run.log if e.rule == "buff"] == ["坐著放不了技能，站起來後再補 buff"]
    mgr.on_pose_packet(1, pose_pkt(0x09), 0.0, KEY)
    mgr._tick(1, run)
    assert casts(mgr) == ["cast 71320 27395721"]


def test_status_pose_field_wins(tmp_path):
    class SitChannel(FakeChannel):
        def send(self, pid, line, priority=0):
            if line == "status":
                self.sent.append(line)
                return {"ok": True, "self": self.handle, "pose": "Sit"}
            return super().send(pid, line, priority)

    mgr, run, _, _ = make(tmp_path, [ICE], buffs=[], channel=SitChannel())
    mgr._tick(1, run)
    assert casts(mgr) == [] and run.pose == 0x0D


# ---- towns -------------------------------------------------------------------------


def test_no_casts_in_town_noted_once_per_map(tmp_path):
    mgr, run, clock, state = make(tmp_path, [ICE], buffs=[])
    state["stage"] = (51, "洛陽外城")
    mgr._tick(1, run)
    mgr._tick(1, run)
    assert casts(mgr) == []
    assert [e.text for e in run.log if e.rule == "buff"] == [
        "在洛陽外城（不能戰鬥的地圖），不補 buff、不變身、不用定期道具"
    ]
    state["stage"] = (1, "莫愁谷入口")
    mgr._tick(1, run)
    assert casts(mgr) == ["cast 71320 27395721"]


def test_load_town_stages_from_the_game_db():
    from services._paths import bundled
    from services.guard import load_town_stages

    if not bundled("tthol.sqlite").exists():
        pytest.skip("tthol.sqlite not pulled")
    towns = load_town_stages()
    assert {2, 9, 44, 51, 52, 53, 54, 173, 174} <= towns  # towns and markets
    assert not {1, 3, 16, 202, 1721, 1936} & towns  # fighting maps
    assert 10 in towns  # 成都城郊: NOFIGHT + PK, paused like the puppet does


# ---- 自動變身 ------------------------------------------------------------------------

HERO_CAPS = ("use", "cast", "status", "hero")


def hero_buff(expires_at=None):
    return BuffInfo(
        group=4901, name="英雄無雙", code=HERO_CODE, expires_at=expires_at, source="hook"
    )


def heroes(mgr):
    return [line for line in mgr._channel.sent if line == "hero"]


def test_hero_pressed_when_not_transformed_then_confirmed(tmp_path):
    mgr, run, clock, state = make(tmp_path, [], buffs=[], caps=HERO_CAPS, hero=True)
    mgr._tick(1, run)
    assert heroes(mgr) == ["hero"]
    (line,) = [e for e in run.log if e.rule == "hero"]
    assert line.text == "變身" and line.phase == "sent"
    state["buffs"] = [hero_buff(WALL0 + 400)]
    clock["t"] += 0.5
    mgr._tick(1, run)
    assert line.phase == "confirmed" and line.text == "變身（已變身）"
    assert run.transforms == 1


def test_hero_not_pressed_while_transformed_even_when_ending(tmp_path):
    # No early refresh, like the puppet: wait for 英雄無雙 to drop off.
    mgr, run, clock, _ = make(
        tmp_path, [], buffs=[hero_buff(WALL0 + 100 + 1)], caps=HERO_CAPS, hero=True
    )
    mgr._tick(1, run)
    assert heroes(mgr) == []


def test_hero_off_by_default_and_needs_the_command_and_buff_list(tmp_path):
    mgr, run, _, _ = make(tmp_path, [], buffs=[], caps=HERO_CAPS)
    mgr._tick(1, run)
    assert heroes(mgr) == []
    mgr, run, _, _ = make(tmp_path, [], buffs=None, caps=HERO_CAPS, hero=True)
    mgr._tick(1, run)
    assert heroes(mgr) == []
    mgr, run, _, _ = make(tmp_path, [], buffs=[], hero=True)  # hook without `hero`
    mgr._tick(1, run)
    mgr._tick(1, run)
    assert heroes(mgr) == []
    assert [e.text for e in run.log if e.rule == "hero"] == [
        "這個 hook 沒有自動變身要用的指令：hero"
    ]


def test_hero_waits_in_town_and_while_sitting(tmp_path):
    mgr, run, _, state = make(tmp_path, [], buffs=[], caps=HERO_CAPS, hero=True)
    state["stage"] = (51, "洛陽外城")
    mgr._tick(1, run)
    assert heroes(mgr) == []
    state["stage"] = (1, "莫愁谷入口")
    mgr.on_pose_packet(1, pose_pkt(0x0D), 0.0, KEY)
    mgr._tick(1, run)
    assert heroes(mgr) == []
    mgr.on_pose_packet(1, pose_pkt(0x09), 0.0, KEY)
    mgr._tick(1, run)
    assert heroes(mgr) == ["hero"]


def test_hero_that_never_shows_up_holds_then_rests(tmp_path):
    mgr, run, clock, _ = make(tmp_path, [], buffs=[], caps=HERO_CAPS, hero=True)
    for _ in range(CAST_TRIES + 1):
        mgr._tick(1, run)
        clock["t"] += 0.5
        mgr._tick(1, run)  # held: no second press
        clock["t"] += CAST_HOLD
    assert len(heroes(mgr)) == CAST_TRIES + 1
    (line,) = [e for e in run.log if e.rule == "hero"]
    assert line.phase == "unconfirmed" and f"{CAST_PAUSE:g} 秒後再試" in line.text
    mgr._tick(1, run)
    assert len(heroes(mgr)) == CAST_TRIES + 1  # resting
    clock["t"] += CAST_PAUSE
    mgr._tick(1, run)
    assert len(heroes(mgr)) == CAST_TRIES + 2


def test_refused_hero_is_logged_and_held(tmp_path):
    class NoHero(FakeChannel):
        def send(self, pid, line, priority=0):
            if line == "hero":
                self.sent.append(line)
                return {"ok": False, "error": "no hero"}
            return super().send(pid, line, priority)

    mgr, run, clock, _ = make(tmp_path, [], buffs=[], channel=NoHero(), caps=HERO_CAPS, hero=True)
    mgr._tick(1, run)
    clock["t"] += 0.5
    mgr._tick(1, run)
    assert heroes(mgr) == ["hero"]
    assert any(e.rule == "hero" and "no hero" in e.text for e in run.log)


def test_buffs_pending_until_every_ticked_buff_is_on(tmp_path):
    mgr, run, clock, state = make(tmp_path, [ICE, BAGUA], buffs=[])
    run.learned = dict(LEARNED)
    assert mgr.buffs_pending(1)
    state["buffs"] = [skill_buff(ICE, 20, WALL0 + 700), skill_buff(BAGUA, 5, WALL0 + 700)]
    assert not mgr.buffs_pending(1)
    state["buffs"] = [skill_buff(ICE, 20, WALL0 + 700)]
    from services.guard import SkillTry

    run.buff_state.skills[BAGUA] = SkillTry(paused_until=clock["t"] + 60)  # resting: not waited on
    assert not mgr.buffs_pending(1)


def test_buffs_pending_counts_the_hero(tmp_path):
    mgr, run, _, state = make(tmp_path, [], buffs=[], caps=HERO_CAPS, hero=True)
    assert mgr.buffs_pending(1)
    state["buffs"] = [hero_buff(WALL0 + 300)]
    assert not mgr.buffs_pending(1)


def test_no_recast_past_the_estimated_end_while_still_listed():
    # 0x29's time runs early: past it the buff is still on until the off packet.
    st = BuffState()
    ending = {ICE: WALL0 + BUFF_LEAD - 1}
    assert next_cast([ICE], LEARNED, DEFS, ending, 5000, st, 0.0, WALL0) == (ICE, 20)
    past = {ICE: WALL0 - 2}
    assert next_cast([ICE], LEARNED, DEFS, past, 5000, st, 0.0, WALL0) is None


def test_a_dropped_manifest_is_read_again_instead_of_disabling_buffs(tmp_path):
    class CapsChannel(FakeChannel):
        def send(self, pid, line, priority=0):
            if line == "caps":
                self.sent.append(line)
                return {"ok": True, "commands": [{"cmd": c} for c in ("use", "cast", "status")]}
            return super().send(pid, line, priority)

    mgr, run, _, _ = make(tmp_path, [ICE], buffs=[], channel=CapsChannel())
    mgr._caps.pop(1)  # as after a PipeGone on a drink
    mgr._tick(1, run)
    assert casts(mgr) == ["cast 71320 27395721"]
    assert not any("沒有 buff 維持要用的指令" in e.text for e in run.log)
