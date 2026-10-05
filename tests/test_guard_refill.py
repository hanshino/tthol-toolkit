from services.api_types import GuardConfig, GuardPotionRule
from services.guard import (
    CAST_HOLD,
    CAST_PAUSE,
    CAST_TRIES,
    REFILL_STACK,
    BuffState,
    GuardManager,
    GuardStore,
    Sample,
    _Run,
    read_holdings,
    read_sample,
    refill_due,
)

JIN, BIG = 24007, 24034  # 金創藥 (HP), a MP potion
ALI = 22295  # 阿離, a pet
NAMES = {JIN: "金創藥", BIG: "大還丹", ALI: "阿離"}
SUMMON_CAPS = ("use", "pettake", "pet", "petsummon", "petdismiss")


# ---- pure ------------------------------------------------------------------------


def test_refill_due_takes_one_stack_of_the_first_low_potion():
    st = BuffState()
    assert refill_due([JIN, BIG], {JIN: 50, BIG: 3}, {JIN: 780, BIG: 600}, 20, st, 0.0) == (
        BIG,
        REFILL_STACK,
    )
    assert refill_due([JIN], {JIN: 0}, {JIN: 37}, 20, st, 0.0) == (JIN, 37)  # less than a stack
    assert refill_due([JIN], {JIN: 20}, {JIN: 780}, 20, st, 0.0) is None  # not below
    assert refill_due([JIN], {}, {}, 20, st, 0.0) is None  # pet bag has none
    assert refill_due([JIN], {JIN: 0}, {JIN: 780}, 20, st, 0.0, qty=50) == (JIN, 50)
    assert refill_due([JIN], {JIN: 0}, {JIN: 30}, 20, st, 0.0, qty=50) == (JIN, 30)


# ---- manager -----------------------------------------------------------------------


class FakeChannel:
    def __init__(self, reply=None, out=False):
        self.sent = []
        self.reply = reply or {"ok": True}
        self.out = out  # a pet is out

    def send(self, pid, line, priority=0):
        self.sent.append(line)
        if line == "pet":
            slot = {"item": ALI, "inst": 434} if self.out else None
            return {"ok": True, "index": 0, "slots": [slot, None, None, None, None, None]}
        return self.reply if line.startswith("pettake") else {"ok": True}


def make(
    refill=True, bag=None, pet=None, caps=("use", "pettake"), channel=None, hp=1000, summon=False
):
    clock = {"t": 100.0}
    store = GuardStore()
    store.save(
        "寒江孤影",
        GuardConfig(
            potion=GuardPotionRule(
                hp_items=[JIN],
                mp_items=[BIG],
                pet_refill=refill,
                refill_below=20,
                refill_summon=summon,
            )
        ),
    )
    state = {"bag": bag if bag is not None else {JIN: 5, BIG: 100}, "pet": pet or {JIN: 780}}

    def read_locked(pid, fn):
        if fn is read_holdings:
            return dict(state["bag"]), dict(state["pet"])
        assert fn is read_sample
        return Sample(hp, 1000, 500, 500, dict(state["bag"]))

    mgr = GuardManager(
        read_locked=read_locked,
        character_name=lambda pid: "寒江孤影",
        channel=channel or FakeChannel(),
        store=store,
        potions=lambda: {},
        item_facts=lambda i: None,
        pipe_present=lambda pid: True,
        pet_items=lambda: frozenset({ALI}),
        clock=lambda: clock["t"],
        wall=lambda: 1000.0 + clock["t"],
    )
    mgr._item_name = lambda i: NAMES.get(i, str(i))
    mgr._caps[1] = frozenset(caps)
    run = _Run("寒江孤影", mgr.config(1))
    with mgr._lock:
        mgr._runs[1] = run
    return mgr, run, clock, state


def takes(mgr):
    return [line for line in mgr._channel.sent if line.startswith("pettake")]


def test_takes_a_stack_when_low_then_confirms_from_the_bag():
    mgr, run, clock, state = make()
    mgr._tick(1, run)
    assert takes(mgr) == [f"pettake {JIN} 200"]
    (line,) = [e for e in run.log if e.rule == "pet"]
    assert line.text == "從寵物背包取 金創藥 ×200" and line.phase == "sent"
    state["bag"][JIN] = 205
    state["pet"][JIN] = 580
    clock["t"] += 0.5
    mgr._tick(1, run)
    assert takes(mgr) == [f"pettake {JIN} 200"]
    assert line.phase == "confirmed" and line.text.endswith("（已取到）")
    assert run.refills == 1 and mgr.status(1).refills == 1


def test_no_take_when_off_not_low_or_without_the_command():
    for kw in ({"refill": False}, {"bag": {JIN: 50, BIG: 100}}, {"pet": {BIG: 10}}):
        mgr, run, _, _ = make(**kw)
        mgr._tick(1, run)
        assert takes(mgr) == [], kw
    mgr, run, _, _ = make(caps=("use",))
    mgr._tick(1, run)
    mgr._tick(1, run)
    assert takes(mgr) == []
    assert [e.text for e in run.log if e.rule == "pet"] == [
        "這個 hook 沒有寵物取水要用的指令：pettake"
    ]


def test_no_take_when_dead():
    mgr, run, _, _ = make(hp=0)
    mgr._tick(1, run)
    assert takes(mgr) == []


def test_take_that_never_arrives_holds_then_rests():
    # The pet is not summoned: the hook says ok, the bag never grows.
    mgr, run, clock, _ = make()
    for _ in range(CAST_TRIES + 1):
        mgr._tick(1, run)
        clock["t"] += 0.5
        mgr._tick(1, run)  # held
        clock["t"] += CAST_HOLD
    assert len(takes(mgr)) == CAST_TRIES + 1
    (line,) = [e for e in run.log if e.rule == "pet"]
    assert line.phase == "unconfirmed" and "寵物可能沒召喚" in line.text
    mgr._tick(1, run)
    assert len(takes(mgr)) == CAST_TRIES + 1
    clock["t"] += CAST_PAUSE
    mgr._tick(1, run)
    assert len(takes(mgr)) == CAST_TRIES + 2


def test_refused_take_is_logged_and_held():
    mgr, run, clock, _ = make(channel=FakeChannel({"ok": False, "error": "bag full"}))
    mgr._tick(1, run)
    clock["t"] += 0.5
    mgr._tick(1, run)
    assert len(takes(mgr)) == 1
    assert any(e.rule == "pet" and "bag full" in e.text for e in run.log)


# ---- auto summon -------------------------------------------------------------------


def pet_lines(mgr):
    return [line for line in mgr._channel.sent if line.startswith("pet")]


def test_summons_a_pet_for_the_take_and_dismisses_it():
    mgr, run, clock, state = make(bag={JIN: 5, BIG: 100, ALI: 2}, caps=SUMMON_CAPS, summon=True)
    mgr._tick(1, run)
    assert pet_lines(mgr) == ["pet", f"petsummon {ALI}", f"pettake {JIN} 200", "petdismiss"]
    (line,) = [e for e in run.log if e.rule == "pet"]
    assert line.text == "從寵物背包取 金創藥 ×200（召喚 阿離，取完收回）"
    state["bag"][JIN] = 205
    clock["t"] += 0.5
    mgr._tick(1, run)
    assert line.phase == "confirmed"


def test_a_pet_already_out_is_used_and_left_out():
    ch = FakeChannel(out=True)
    mgr, run, _, _ = make(bag={JIN: 5, BIG: 100, ALI: 1}, caps=SUMMON_CAPS, channel=ch, summon=True)
    mgr._tick(1, run)
    assert pet_lines(mgr) == ["pet", f"pettake {JIN} 200"]


def test_no_summon_when_off_without_commands_or_without_a_pet():
    mgr, run, _, _ = make(bag={JIN: 5, BIG: 100, ALI: 1}, caps=SUMMON_CAPS, summon=False)
    mgr._tick(1, run)
    assert pet_lines(mgr) == [f"pettake {JIN} 200"]
    mgr, run, _, _ = make(
        bag={JIN: 5, BIG: 100, ALI: 1}, summon=True
    )  # hook without the pet commands
    mgr._tick(1, run)
    assert pet_lines(mgr) == [f"pettake {JIN} 200"]
    assert any("pet、petsummon、petdismiss" in e.text for e in run.log)
    mgr, run, clock, _ = make(
        bag={JIN: 5, BIG: 100}, caps=SUMMON_CAPS, summon=True
    )  # no pet in the bag
    mgr._tick(1, run)
    clock["t"] += CAST_HOLD
    mgr._tick(1, run)
    assert pet_lines(mgr) == ["pet", f"pettake {JIN} 200"] * 2
    notes = [e.text for e in run.log if e.rule == "pet" and e.phase == "error"]
    assert notes == ["背包裡沒有寵物可以召喚，寵物取水要先召喚寵物"]  # noted once


def test_hotkey_bar_redrawn_once_the_take_arrives():
    mgr, run, clock, state = make(caps=("use", "pettake", "hotkeyrefresh"))
    mgr._tick(1, run)
    assert "hotkeyrefresh" not in mgr._channel.sent  # not before the bag shows it
    state["bag"][JIN] = 205
    clock["t"] += 0.5
    mgr._tick(1, run)
    assert mgr._channel.sent.count("hotkeyrefresh") == 1
    mgr._tick(1, run)
    assert mgr._channel.sent.count("hotkeyrefresh") == 1


def test_no_hotkey_redraw_without_the_command():
    mgr, run, clock, state = make()
    mgr._tick(1, run)
    state["bag"][JIN] = 205
    mgr._tick(1, run)
    assert "hotkeyrefresh" not in mgr._channel.sent
