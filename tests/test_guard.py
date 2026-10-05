import pytest

from services.api_types import GuardConfig, GuardCureRule, GuardPotionRule
from services.guard import (
    CONFIRM_WINDOW,
    CURE_HOLD,
    CURE_TRIES,
    EMPTY_SKIP,
    MAX_PER_PASS,
    PENDING_WINDOW,
    Cure,
    CureState,
    DrinkState,
    GuardManager,
    GuardStore,
    Potion,
    Sample,
    _Run,
    expected,
    next_cure,
    next_potion,
    read_debuffs,
    read_sample,
    record_cure,
    record_drink,
    settle_bag,
    settle_cures,
    wants_drink,
)
from services.hook_cmd import NoReply, PipeGone

JIN, QIONG, ZHONG, YIQI = 24007, 24008, 24034, 24023
CATALOG = {
    JIN: Potion("金創藥", hp=10, hp_share=True),
    QIONG: Potion("瓊丹妙露", hp=20, hp_share=True),
    ZHONG: Potion("中行血藥", mp=70),
    YIQI: Potion("大益氣丹", hp=120, mp=50),
}
MAXES = {"hp": 1000, "mp": 500}
POISON, SLOW = 19, 20
JIEDU, JIANBU = 24206, 24207
CURES = {JIEDU: Cure("解毒劑", POISON, "中毒"), JIANBU: Cure("健步散", SLOW, "緩速")}


def sample(hp=1000, mp=500, bag=None, hp_max=1000, mp_max=500, debuffs=()):
    return Sample(
        hp,
        hp_max,
        mp,
        mp_max,
        bag if bag is not None else {JIN: 50, ZHONG: 50, JIEDU: 9},
        tuple(debuffs),
    )


def rule(**kw):
    base = dict(hp_pct=70, mp_pct=30, hp_items=[JIN], mp_items=[ZHONG])
    base.update(kw)
    return GuardPotionRule(**base)


# ---- potion amounts --------------------------------------------------------


def test_amount_share_and_points():
    assert CATALOG[JIN].amount("hp", 48877) == 4887
    assert CATALOG[ZHONG].amount("mp", 6170) == 70
    assert CATALOG[YIQI].amount("hp", 1000) == 120 and CATALOG[YIQI].amount("mp", 500) == 50
    assert CATALOG[JIN].amount("mp", 500) == 0
    assert CATALOG[YIQI].restores == "both"


# ---- expected value / pending ----------------------------------------------


def test_expected_adds_live_pending_and_drops_expired():
    st = DrinkState()
    record_drink(st, JIN, CATALOG[JIN], MAXES, 50, now=0.0)  # +100 hp until 0.5
    assert expected(st, "hp", 500, 0.1) == 600
    assert expected(st, "hp", 500, PENDING_WINDOW) == 500  # expired
    assert st.pending["hp"] == []


def test_both_potion_counts_on_both_resources():
    st = DrinkState()
    record_drink(st, YIQI, CATALOG[YIQI], MAXES, 5, now=0.0)
    assert expected(st, "hp", 0, 0.1) == 120
    assert expected(st, "mp", 0, 0.1) == 50


def test_wants_drink_threshold_and_dead():
    st = DrinkState()
    assert wants_drink(st, "hp", 699, 1000, 70, 0.0)
    assert not wants_drink(st, "hp", 700, 1000, 70, 0.0)
    assert not wants_drink(st, "hp", 0, 1000, 70, 0.0)  # dead
    assert not wants_drink(st, "hp", 10, 0, 70, 0.0)  # unknown max


def test_pending_stops_overshoot():
    st = DrinkState()
    record_drink(st, JIN, CATALOG[JIN], MAXES, 50, now=0.0)  # 650 + 100 >= 700
    assert not wants_drink(st, "hp", 650, 1000, 70, 0.1)


def test_next_potion_order_bag_and_empty_skip():
    st = DrinkState()
    items = [QIONG, JIN]
    assert next_potion(items, {JIN: 3}, st, 0.0) == JIN  # QIONG not held
    assert next_potion(items, {QIONG: 1, JIN: 3}, st, 0.0) == QIONG
    st.empty_until[QIONG] = 5.0
    assert next_potion(items, {QIONG: 1, JIN: 3}, st, 1.0) == JIN
    assert next_potion(items, {QIONG: 1, JIN: 3}, st, 5.0) == QIONG
    assert next_potion(items, {}, st, 0.0) is None


# ---- bag log ---------------------------------------------------------------


def test_settle_bag_confirms_partial_then_rest():
    st = DrinkState()
    for _ in range(3):
        record_drink(st, JIN, None, MAXES, 50, now=0.0)
    assert settle_bag(st, {JIN: 48}, 0.2) == [(JIN, 50, 48, True)]
    assert st.outstanding[JIN].sent == 1
    assert settle_bag(st, {JIN: 47}, 0.3) == [(JIN, 48, 47, True)]
    assert st.outstanding == {}


def test_settle_bag_times_out_and_ignores_refill():
    st = DrinkState()
    record_drink(st, JIN, None, MAXES, 50, now=0.0)
    assert settle_bag(st, {JIN: 150}, 0.2) == []  # refilled from the pet bag: re-baseline
    assert settle_bag(st, {JIN: 150}, CONFIRM_WINDOW) == [(JIN, 150, 150, False)]
    assert st.outstanding == {}


# ---- read_sample -----------------------------------------------------------


class FakePM:
    def __init__(self, ints):
        self.ints = ints

    def read_bytes(self, addr, n):
        import struct

        return struct.pack("<4i", *self.ints)


def test_read_sample_normal_and_compat(monkeypatch):
    monkeypatch.setattr(
        "services.guard.read_inventory", lambda pm, a: [(JIN, 3), (JIN, 4), (ZHONG, 1)]
    )
    s = read_sample(FakePM([10, 100, 20, 200]), 0x1000, False)
    assert (s.hp, s.hp_max, s.mp, s.mp_max) == (10, 100, 20, 200)
    assert s.bag == {JIN: 7, ZHONG: 1}
    c = read_sample(FakePM([100, 10, 200, 20]), 0x1000, True)
    assert (c.hp, c.hp_max, c.mp, c.mp_max) == (10, 100, 20, 200)


def test_read_sample_not_a_char_object(monkeypatch):
    monkeypatch.setattr("services.guard.read_inventory", lambda pm, a: None)
    assert read_sample(FakePM([1, 2, 3, 4]), 0, False) is None


# ---- potion catalog (real DB) ----------------------------------------------


def test_load_potions_keeps_restoring_items_only():
    from services._paths import bundled
    from services.guard import load_potions

    if not bundled("tthol.sqlite").exists():
        pytest.skip("tthol.sqlite not pulled")
    potions = load_potions()
    assert potions[24007].restores == "hp" and potions[24007].hp_share  # 金創藥, share (flag 2)
    assert potions[24034].restores == "mp" and not potions[24034].mp_share  # 中行血藥, 70 points
    assert potions[24034].mp == 70
    assert potions[24023].restores == "both"  # 大益氣丹
    # Max-raising buffs are not drinks: 九龍聚魂散 (flag 3), 賞善真氣丹 (flag 1).
    assert 24128 not in potions
    assert 28139 not in potions


# ---- store -----------------------------------------------------------------


def test_store_round_trip_and_defaults(tmp_path):
    store = GuardStore(tmp_path / "guard.json")
    assert store.load("寒江孤影") == GuardConfig()
    cfg = GuardConfig(potion=rule(hp_items=[QIONG, YIQI]))
    store.save("寒江孤影", cfg)
    assert GuardStore(tmp_path / "guard.json").load("寒江孤影") == cfg
    assert store.load("別人") == GuardConfig()


def test_store_survives_corrupt_file(tmp_path):
    path = tmp_path / "guard.json"
    path.write_text("{not json", encoding="utf-8")
    assert GuardStore(path).load("x") == GuardConfig()


# ---- manager tick ----------------------------------------------------------


class FakeChannel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def send(self, pid, line, priority=0):
        self.sent.append(line)
        r = self.replies.pop(0) if self.replies else {"ok": True}
        if isinstance(r, Exception):
            raise r
        return r


def make_manager(tmp_path, samples, replies=(), cfg=None, name="寒江孤影", cure_items=()):
    clock = {"t": 0.0}
    seq = list(samples)
    store = GuardStore(tmp_path / "guard.json")
    store.save(name, GuardConfig(potion=cfg or rule(), cure=GuardCureRule(items=list(cure_items))))
    mgr = GuardManager(
        read_locked=lambda pid, fn: seq.pop(0) if len(seq) > 1 else seq[0],
        character_name=lambda pid: name,
        channel=FakeChannel(replies),
        store=store,
        potions=lambda: CATALOG,
        cures=lambda: CURES,
        status_names=lambda: {POISON: "中毒", SLOW: "緩速"},
        pipe_present=lambda pid: True,
        clock=lambda: clock["t"],
        wall=lambda: 1000.0 + clock["t"],
    )
    run = _Run(name, mgr.config(1))
    with mgr._lock:
        mgr._runs[1] = run
    return mgr, run, clock


def test_tick_drinks_until_expected_reaches_threshold(tmp_path):
    # 400 / 1000 hp; each 金創藥 is +100: 400 -> 500 -> 600 -> 700 stops after 3.
    mgr, run, clock = make_manager(tmp_path, [sample(hp=400)])
    mgr._tick(1, run)
    assert mgr._channel.sent == [f"use {JIN}"] * 3
    assert [e.phase for e in run.log] == ["sent"]
    assert "×3" in run.log[-1].text
    assert run.drinks == 3


def test_tick_caps_drinks_per_pass(tmp_path):
    mgr, run, clock = make_manager(tmp_path, [sample(hp=10)])
    mgr._tick(1, run)
    assert len(mgr._channel.sent) == MAX_PER_PASS


def test_tick_does_not_redrink_inside_pending_window(tmp_path):
    mgr, run, clock = make_manager(tmp_path, [sample(hp=650)])
    mgr._tick(1, run)
    clock["t"] += 0.1
    mgr._tick(1, run)  # HP not refreshed yet, but 650 + 100 pending >= 700
    assert mgr._channel.sent == [f"use {JIN}"]
    clock["t"] += PENDING_WINDOW
    mgr._tick(1, run)  # pending expired and HP still 650: drink again
    assert mgr._channel.sent == [f"use {JIN}"] * 2


def test_live_packet_beats_memory(tmp_path):
    mgr, run, clock = make_manager(tmp_path, [sample(hp=1000)])
    mgr.on_vitals(1, 300, 500)
    assert run.wake.is_set()
    mgr._tick(1, run)
    assert len(mgr._channel.sent) == 4  # 300 + 4*100 = 700


def test_stale_live_packet_is_ignored(tmp_path):
    mgr, run, clock = make_manager(tmp_path, [sample(hp=1000)])
    mgr.on_vitals(1, 300, 500)
    clock["t"] += 5.0
    mgr._tick(1, run)
    assert mgr._channel.sent == []


def test_dead_drinks_nothing(tmp_path):
    mgr, run, clock = make_manager(tmp_path, [sample(hp=0, mp=0)])
    mgr._tick(1, run)
    assert mgr._channel.sent == []


def test_both_resources_in_one_pass(tmp_path):
    mgr, run, clock = make_manager(tmp_path, [sample(hp=650, mp=100)])
    mgr._tick(1, run)
    assert mgr._channel.sent == [f"use {JIN}", f"use {ZHONG}"]


def test_item_not_in_bag_falls_back_to_next(tmp_path):
    cfg = rule(hp_items=[QIONG, JIN])
    mgr, run, clock = make_manager(
        tmp_path,
        [sample(hp=650, bag={QIONG: 1, JIN: 5})],
        [{"ok": False, "error": "item not in bag"}],
        cfg=cfg,
    )
    mgr._tick(1, run)
    assert mgr._channel.sent == [f"use {QIONG}", f"use {JIN}"]
    assert run.state.empty_until[QIONG] == EMPTY_SKIP


def test_no_reply_counts_as_pending_and_backs_off(tmp_path):
    mgr, run, clock = make_manager(tmp_path, [sample(hp=650)], [NoReply("timeout")])
    wait = mgr._tick(1, run)
    assert wait > 0.05
    assert expected(run.state, "hp", 650, clock["t"] + 0.1) == 750
    assert [e.phase for e in run.log] == ["error"]


def test_tick_reports_missing_pipe(tmp_path):
    mgr, run, clock = make_manager(tmp_path, [sample(hp=10)], [PipeGone("x")])
    mgr._tick(1, run)
    assert run.problem and "hook" in run.problem
    assert run.state.pending["hp"] == []


def test_dispatcher_timeout_backs_off_without_pending(tmp_path):
    mgr, run, clock = make_manager(
        tmp_path, [sample(hp=10)], [{"ok": False, "error": "timeout: dispatcher did not run"}]
    )
    assert mgr._tick(1, run) >= 1.0
    assert run.state.pending["hp"] == []


def test_actions_not_loaded_sets_problem_and_never_reloads(tmp_path):
    mgr, run, clock = make_manager(
        tmp_path,
        [sample(hp=10)],
        [{"ok": False, "error": "actions not loaded: send reload <path>"}],
    )
    mgr._tick(1, run)
    assert "動作模組" in run.problem
    assert all(not line.startswith("reload") for line in mgr._channel.sent)


def _entries(mgr, run):
    return [line.entry(mgr._item_name) for line in run.log]


def test_bag_drop_updates_the_drink_line_in_place(tmp_path):
    mgr, run, clock = make_manager(
        tmp_path,
        [
            sample(hp=400, bag={JIN: 50}),
            sample(hp=600, bag={JIN: 48}),
            sample(hp=800, bag={JIN: 47}),
        ],
    )
    mgr._tick(1, run)  # 3 drinks
    (line,) = _entries(mgr, run)
    assert line.phase == "sent" and line.text == "喝 金創藥 ×3（體力 40%）"
    clock["t"] += 0.2
    mgr._tick(1, run)  # bag shows 2 of them
    (line,) = _entries(mgr, run)
    assert line.phase == "sent" and line.text.endswith("（背包 50 → 48）")
    clock["t"] += 0.2
    mgr._tick(1, run)  # and the third
    (line,) = _entries(mgr, run)
    assert line.phase == "confirmed" and line.text == "喝 金創藥 ×3（體力 40%）（背包 50 → 47）"
    assert run.awaiting == {}


def test_drink_line_never_seen_in_bag_turns_unconfirmed(tmp_path):
    mgr, run, clock = make_manager(tmp_path, [sample(hp=650, bag={JIN: 50})])
    mgr._tick(1, run)
    clock["t"] += CONFIRM_WINDOW
    mgr._tick(1, run)  # HP still 650 after the pending window: drinks again (a new line)
    first = _entries(mgr, run)[0]
    assert first.phase == "unconfirmed" and "背包數量沒變" in first.text
    assert len({e.id for e in _entries(mgr, run)}) == len(run.log)


def test_start_refuses_without_pipe_or_name(tmp_path, monkeypatch):
    monkeypatch.delenv("TTHOL_NO_HOOK", raising=False)
    mgr = GuardManager(
        read_locked=lambda pid, fn: None,
        character_name=lambda pid: None,
        store=GuardStore(tmp_path / "g.json"),
        pipe_present=lambda pid: True,
    )
    assert mgr.start(1).ok is False
    mgr2 = GuardManager(
        read_locked=lambda pid, fn: None,
        character_name=lambda pid: "a",
        store=GuardStore(tmp_path / "g.json"),
        pipe_present=lambda pid: False,
    )
    assert mgr2.start(1).ok is False
    monkeypatch.setenv("TTHOL_NO_HOOK", "1")
    assert mgr2.start(1).reason and "TTHOL_NO_HOOK" in mgr2.start(1).reason


def test_potions_lists_held_restoring_items(tmp_path):
    mgr = GuardManager(
        read_locked=lambda pid, fn: ({JIN: 87, 99999: 1}, {JIN: 300, ZHONG: 120}),
        character_name=lambda pid: "a",
        store=GuardStore(tmp_path / "g.json"),
        potions=lambda: CATALOG,
    )
    got = {(p.item_id, p.restores, p.bag, p.pet) for p in mgr.potions(1)}
    assert got == {(JIN, "hp", 87, 300), (ZHONG, "mp", 0, 120)}


# ---- debuff read -----------------------------------------------------------


class MemPM:
    """read_bytes over a sparse {address: int32} map; unknown words read as 0."""

    def __init__(self, words):
        self.words = words

    def read_bytes(self, addr, n):
        import struct

        return b"".join(struct.pack("<i", self.words.get(addr + i, 0)) for i in range(0, n, 4))


def test_read_debuffs_is_count_gated():
    base = 0x1000
    pm = MemPM({base + 0x4C4: 2, base + 0x4C8: POISON, base + 0x4CC: SLOW, base + 0x4D0: 23})
    assert read_debuffs(pm, base) == (POISON, SLOW)  # the stale third slot is ignored
    assert read_debuffs(MemPM({base + 0x4C4: 0}), base) == ()
    assert read_debuffs(MemPM({base + 0x4C4: 999999}), base) == ()  # bad read


def test_read_debuffs_swallows_read_errors():
    class Broken:
        def read_bytes(self, addr, n):
            raise OSError("gone")

    assert read_debuffs(Broken(), 0x1000) == ()


# ---- cure catalog (real DB) --------------------------------------------------


def test_load_cures_keeps_real_cures_only():
    from services._paths import bundled
    from services.guard import load_cures

    if not bundled("tthol.sqlite").exists():
        pytest.skip("tthol.sqlite not pulled")
    cures = load_cures()
    assert set(cures) == {24201, 24202, 24204, 24205, 24206, 24207, 24208, 24209, 24210}
    assert cures[24206].group == 19 and cures[24206].status == "中毒"
    # These point at a hostile group too, but inflict it or strip the user's own 隱形.
    assert 28034 not in cures  # 謎之藥水
    assert 24161 not in cures  # 烤壞的肉串
    assert 24203 not in cures  # 現形丹


# ---- rule: curing (pure) -------------------------------------------------------


def test_next_cure_matches_the_exact_group_held_and_ticked():
    st = CureState()
    bag = {JIEDU: 1, JIANBU: 1}
    assert next_cure((POISON,), [JIEDU], CURES, bag, st, {}, 0.0) == (POISON, JIEDU)
    assert next_cure((70,), [JIEDU], CURES, bag, st, {}, 0.0) is None  # the other 中毒 group
    assert next_cure((POISON,), [JIANBU], CURES, bag, st, {}, 0.0) is None  # not ticked for it
    assert next_cure((POISON,), [JIEDU], CURES, {}, st, {}, 0.0) is None  # not held
    assert next_cure((POISON,), [JIEDU], CURES, bag, st, {JIEDU: 5.0}, 1.0) is None  # resting
    assert next_cure((POISON, SLOW), [JIEDU, JIANBU], CURES, bag, st, {}, 0.0) == (POISON, JIEDU)


def test_cure_holds_then_retries_then_gives_up():
    st = CureState()
    bag = {JIEDU: 9}
    assert record_cure(st, POISON, 0.0) == 1
    assert next_cure((POISON,), [JIEDU], CURES, bag, st, {}, CURE_HOLD - 0.1) is None
    assert next_cure((POISON,), [JIEDU], CURES, bag, st, {}, CURE_HOLD) == (POISON, JIEDU)
    for i in range(2, CURE_TRIES + 1):
        assert record_cure(st, POISON, CURE_HOLD * i) == i
    end = CURE_HOLD * (CURE_TRIES + 1)
    assert next_cure((POISON,), [JIEDU], CURES, bag, st, {}, end) is None
    assert settle_cures(st, (POISON,), end) == [(POISON, False)]
    assert settle_cures(st, (POISON,), end + 1) == []  # reported once
    assert settle_cures(st, (), end + 2) == []  # gone after giving up: no "cleared"
    assert st.groups == {}


def test_settle_cures_reports_cleared():
    st = CureState()
    record_cure(st, POISON, 0.0)
    assert settle_cures(st, (POISON,), 0.5) == []
    assert settle_cures(st, (), 0.6) == [(POISON, True)]


# ---- manager: curing -----------------------------------------------------------


def test_tick_cures_once_and_confirms_when_the_debuff_leaves(tmp_path):
    mgr, run, clock = make_manager(
        tmp_path,
        [sample(debuffs=[POISON]), sample(debuffs=[POISON]), sample()],
        cure_items=[JIEDU],
    )
    mgr._tick(1, run)
    assert mgr._channel.sent == [f"use {JIEDU}"]
    clock["t"] = 0.5
    mgr._tick(1, run)  # still poisoned, inside the hold: no second use
    assert mgr._channel.sent == [f"use {JIEDU}"]
    clock["t"] = 0.7
    mgr._tick(1, run)
    (line,) = [e for e in _entries(mgr, run) if e.rule == "cure"]
    assert line.phase == "confirmed"
    assert line.text == "中毒 → 用 解毒劑（已解除）"
    assert run.cures == 1


def test_tick_cure_gives_up_after_tries_in_one_line(tmp_path):
    mgr, run, clock = make_manager(tmp_path, [sample(debuffs=[POISON])], cure_items=[JIEDU])
    for i in range(CURE_TRIES + 1):
        clock["t"] = CURE_HOLD * i
        mgr._tick(1, run)
    assert mgr._channel.sent == [f"use {JIEDU}"] * CURE_TRIES
    (line,) = [e for e in _entries(mgr, run) if e.rule == "cure"]
    assert line.phase == "unconfirmed"
    assert line.text.startswith(f"中毒 → 用 解毒劑 ×{CURE_TRIES}")
    assert "還在" in line.text


def test_tick_unticked_cure_is_never_used(tmp_path):
    mgr, run, _ = make_manager(tmp_path, [sample(debuffs=[POISON])])
    mgr._tick(1, run)
    assert mgr._channel.sent == []


def test_tick_hp_comes_before_cure_and_mp(tmp_path):
    mgr, run, _ = make_manager(
        tmp_path, [sample(hp=650, mp=100, debuffs=[POISON])], cure_items=[JIEDU]
    )
    mgr._tick(1, run)
    assert mgr._channel.sent == [f"use {JIN}", f"use {JIEDU}", f"use {ZHONG}"]


def test_tick_dead_cures_nothing(tmp_path):
    mgr, run, _ = make_manager(tmp_path, [sample(hp=0, debuffs=[POISON])], cure_items=[JIEDU])
    mgr._tick(1, run)
    assert mgr._channel.sent == []


def test_status_lists_current_debuffs_while_running(tmp_path):
    mgr, run, _ = make_manager(tmp_path, [sample(debuffs=[POISON, SLOW, POISON])])
    mgr._tick(1, run)
    run.thread = type("T", (), {"is_alive": lambda self: True})()
    assert mgr.status(1).debuffs == ["中毒", "緩速"]


# ---- caps ----------------------------------------------------------------------


def caps_reply(*names):
    return {"ok": True, "caps": 1, "commands": [{"cmd": n, "kind": "action"} for n in names]}


def _caps_manager(tmp_path, reply):
    return GuardManager(
        read_locked=lambda pid, fn: None,
        character_name=lambda pid: "a",
        channel=FakeChannel([reply]),
        store=GuardStore(tmp_path / "g.json"),
        pipe_present=lambda pid: True,
    )


def test_start_needs_use_in_the_manifest(tmp_path, monkeypatch):
    monkeypatch.delenv("TTHOL_NO_HOOK", raising=False)
    mgr = _caps_manager(tmp_path, caps_reply("pos", "cast"))
    r = mgr.start(1)
    assert r.ok is False and "use" in r.reason
    assert mgr._channel.sent == ["caps"]
    assert mgr.status(1).hook_cmd is False


def test_start_refuses_a_hook_without_manifest(tmp_path, monkeypatch):
    monkeypatch.delenv("TTHOL_NO_HOOK", raising=False)
    mgr = _caps_manager(tmp_path, {"ok": False, "error": "usage: ..."})
    r = mgr.start(1)
    assert r.ok is False and "caps" in r.reason


def test_start_with_use_in_the_manifest(tmp_path, monkeypatch):
    monkeypatch.delenv("TTHOL_NO_HOOK", raising=False)
    mgr = _caps_manager(tmp_path, caps_reply("use", "cast"))
    try:
        assert mgr.start(1).ok is True
        assert mgr.status(1).hook_cmd is True
    finally:
        mgr.shutdown()


def test_pipe_gone_forgets_the_manifest(tmp_path):
    mgr, run, _ = make_manager(tmp_path, [sample(hp=100)], replies=[PipeGone("x")])
    mgr._caps[1] = frozenset({"pos"})
    mgr._tick(1, run)
    assert 1 not in mgr._caps
