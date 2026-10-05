import pytest

from services.api_types import BuffInfo, GuardConfig, GuardPotionRule, ItemRule, ItemRules
from services.guard import (
    BUFF_LEAD,
    CAST_HOLD,
    BuffState,
    GuardManager,
    GuardStore,
    Sample,
    _Run,
    active_items,
    due_items,
    read_stage_id,
)
from services.item_rules import ItemFact, load_item_facts
from services.snapshot_db import SnapshotDB

ROUFO, GENGU, JIN = 24114, 28146, 24007
FACTS = {
    ROUFO: ItemFact("千年肉佛", True, True, True, None, "效果 3 分鐘"),
    GENGU: ItemFact("賞善根骨丹", False, False, True, None, "效果 10 分鐘"),
    JIN: ItemFact("金創藥", True, True, False, None, None),
}
WALL0 = 1000.0


def item_buff(item_id, expires_at=None):
    return BuffInfo(group=0, name="x", code=item_id, expires_at=expires_at, source="hook")


# ---- facts (real DB) ---------------------------------------------------------------


def test_item_facts_actions_from_the_game_db():
    from services._paths import bundled

    if not bundled("tthol.sqlite").exists():
        pytest.skip("tthol.sqlite not pulled")
    f = load_item_facts()
    assert f(24007).actions == ["keep", "sell", "store"]  # 金創藥: a plain potion
    assert f(24114).actions == ["keep", "use_periodic", "sell", "store"]  # 千年肉佛
    assert f(28146).actions == ["keep", "use_periodic"]  # 根骨丹: no_shop, no_store
    assert f(24206).actions == ["keep", "use_on_status", "sell", "store"]  # 解毒劑
    assert f(22063).actions == ["keep"]  # 冥龍靴: bound gear
    # The puppet's filter: timed POTION raising a stat, max-raising ones included.
    assert f(24103).periodic and f(24128).periodic  # 強命符, 九龍聚魂散
    assert f(27916).periodic  # 鮮蝦脆餅: timed friendly status (added on top of the puppet)
    assert not f(30295).periodic  # 英雄無雙: NORMAL_ITEM, a feature of its own
    assert not f(24007).periodic  # 金創藥: no item_time
    assert f(1) is None or f(1).name


# ---- pure ------------------------------------------------------------------------------


def test_active_items_ignores_skill_buffs():
    buffs = [
        item_buff(GENGU, 1600.0),
        BuffInfo(group=2, name="冰心靈訣", code=71320, level=20, source="hook"),
    ]
    assert active_items(buffs) == {GENGU: 1600.0}


def test_due_items_needs_it_held_and_its_buff_missing_or_ending():
    st = BuffState()
    bag = {ROUFO: 3, GENGU: 1}
    assert due_items([ROUFO, GENGU], bag, {}, st, {}, 0.0, WALL0) == [ROUFO, GENGU]
    assert due_items([ROUFO, GENGU], bag, {ROUFO: WALL0 + 60}, st, {}, 0.0, WALL0) == [GENGU]
    assert due_items([ROUFO], bag, {ROUFO: None}, st, {}, 0.0, WALL0) == []  # on, time unknown
    assert due_items([ROUFO], bag, {ROUFO: WALL0 + BUFF_LEAD - 1}, st, {}, 0.0, WALL0) == [ROUFO]
    assert due_items([ROUFO], {}, {}, st, {}, 0.0, WALL0) == []  # none left
    assert due_items([ROUFO], bag, {}, st, {ROUFO: 5.0}, 1.0, WALL0) == []  # "not in bag" rest


# ---- manager -----------------------------------------------------------------------------


class FakeChannel:
    def __init__(self):
        self.sent = []

    def send(self, pid, line, priority=0):
        self.sent.append(line)
        return {"ok": True}


def make(rules, buffs, bag=None, stage=(1, "莫愁谷入口")):
    clock = {"t": 100.0}
    store = GuardStore()
    store.save("寒江孤影", GuardConfig(potion=GuardPotionRule(hp_items=[], mp_items=[])))
    store.save_items("寒江孤影", ItemRules(items=rules))
    state = {"buffs": buffs, "bag": bag if bag is not None else {ROUFO: 5, GENGU: 2, JIN: 9}}

    def read_locked(pid, fn):
        if fn is read_stage_id:
            return stage
        return Sample(1000, 1000, 500, 500, dict(state["bag"]))

    mgr = GuardManager(
        read_locked=read_locked,
        character_name=lambda pid: "寒江孤影",
        channel=FakeChannel(),
        store=store,
        potions=lambda: {},
        item_facts=FACTS.get,
        buffs=lambda pid: state["buffs"],
        towns=lambda: frozenset({51}),
        pipe_present=lambda pid: True,
        clock=lambda: clock["t"],
        wall=lambda: WALL0 + clock["t"],
    )
    run = _Run("寒江孤影", mgr.config(1), store.load_items("寒江孤影"))
    with mgr._lock:
        mgr._runs[1] = run
    return mgr, run, clock, state


def uses(mgr):
    return [line for line in mgr._channel.sent if line.startswith("use")]


def test_periodic_item_used_when_its_buff_is_missing_then_confirmed(tmp_path):
    mgr, run, clock, state = make({ROUFO: ItemRule(action="use_periodic")}, buffs=[])
    mgr._tick(1, run)
    assert uses(mgr) == [f"use {ROUFO}"]
    (line,) = [e for e in run.log if e.rule == "item"]
    assert line.text == "用 千年肉佛"
    state["buffs"] = [item_buff(ROUFO, WALL0 + 280)]
    clock["t"] += 0.5
    mgr._tick(1, run)
    assert uses(mgr) == [f"use {ROUFO}"]
    assert line.phase == "confirmed" and line.text.endswith("（已生效）")
    assert run.uses == 1


def test_items_without_periodic_rule_are_never_used(tmp_path):
    rules = {JIN: ItemRule(action="sell", keep=10), GENGU: ItemRule(action="keep")}
    mgr, run, _, _ = make(rules, buffs=[])
    mgr._tick(1, run)
    assert uses(mgr) == []


def test_periodic_items_wait_for_the_hook_list_and_skip_towns(tmp_path):
    mgr, run, _, _ = make({ROUFO: ItemRule(action="use_periodic")}, buffs=None)
    mgr._tick(1, run)
    assert uses(mgr) == []
    mgr, run, _, _ = make(
        {ROUFO: ItemRule(action="use_periodic")}, buffs=[], stage=(51, "洛陽外城")
    )
    mgr._tick(1, run)
    assert uses(mgr) == []


def test_periodic_items_all_go_in_one_pass_then_hold(tmp_path):
    # Like the battle puppet: every missing item buff in the same pass.
    rules = {ROUFO: ItemRule(action="use_periodic"), GENGU: ItemRule(action="use_periodic")}
    mgr, run, clock, _ = make(rules, buffs=[])
    mgr._tick(1, run)
    assert uses(mgr) == [f"use {ROUFO}", f"use {GENGU}"]
    clock["t"] += CAST_HOLD - 0.1
    mgr._tick(1, run)  # each one holds for its buff
    assert len(uses(mgr)) == 2
    clock["t"] += 0.1
    mgr._tick(1, run)  # neither buff showed up: both again
    assert uses(mgr)[2:] == [f"use {ROUFO}", f"use {GENGU}"]
    assert len([e for e in run.log if e.rule == "item"]) == 2  # one line per item


def test_set_items_reaches_a_running_guard(tmp_path):
    mgr, run, _, _ = make({}, buffs=[])
    mgr.set_items(1, ItemRules(items={ROUFO: ItemRule(action="use_periodic")}))
    mgr._tick(1, run)
    assert uses(mgr) == [f"use {ROUFO}"]


def test_item_view_lists_held_and_ruled_items(tmp_path):
    rules = {GENGU: ItemRule(action="use_periodic")}
    mgr, _, _, _ = make(rules, buffs=[item_buff(GENGU, 1600.0)])
    mgr._read_locked = lambda pid, fn: ({ROUFO: 5, 99999: 1}, {JIN: 30})
    view = mgr.item_view(1)
    got = {c.item_id: (c.bag, c.pet, c.active, c.expires_at) for c in view.candidates}
    # 99999 is not in the DB facts: left out
    assert got == {
        ROUFO: (5, 0, False, None),
        GENGU: (0, 0, True, 1600.0),  # not held, but it has a rule
        JIN: (0, 30, None, None),  # cannot be kept up: no buff state
    }
    assert view.rules.items == rules


# ---- settings table ----------------------------------------------------------------------


def test_settings_copy_and_listing(tmp_path):
    db = SnapshotDB(str(tmp_path / "s.db"))
    db.set_setting("甲", "guard.potion", {"hp_pct": 60})
    db.set_setting("甲", "items", {"items": {"24007": {"action": "sell", "keep": 5}}})
    assert db.copy_settings("甲", "乙", ["items", "guard.buff"]) == 1  # 甲 has no guard.buff
    assert db.get_setting("乙", "items") == db.get_setting("甲", "items")
    assert db.get_setting("乙", "guard.potion") is None
    assert db.settings_characters() == [
        {"character": "乙", "sections": ["items"]},
        {"character": "甲", "sections": ["guard.potion", "items"]},
    ]


def test_backup_carries_settings_and_import_keeps_existing(tmp_path):
    src = SnapshotDB(str(tmp_path / "a.db"))
    src.set_setting("甲", "guard.potion", {"hp_pct": 60})
    src.set_setting("乙", "items", {"items": {}})
    data = src.export_all()
    assert {(e["character"], e["section"]) for e in data["character_settings"]} == {
        ("甲", "guard.potion"),
        ("乙", "items"),
    }
    dst = SnapshotDB(str(tmp_path / "b.db"))
    dst.set_setting("甲", "guard.potion", {"hp_pct": 80})  # newer here: kept
    summary = dst.import_merge(data)
    assert summary["settings_added"] == 1 and summary["settings_conflicts"] == 1
    assert dst.get_setting("甲", "guard.potion") == {"hp_pct": 80}
    assert dst.get_setting("乙", "items") == {"items": {}}


def test_item_view_describes_extra_ids(tmp_path):
    mgr, _, _, _ = make({}, buffs=[])
    mgr._read_locked = lambda pid, fn: ({}, {})
    assert [c.item_id for c in mgr.item_view(1, extra={JIN}).candidates] == [JIN]
