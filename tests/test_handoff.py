import threading
import time
from contextlib import contextmanager

from services.api_types import HandoffConfig, HandoffItem
from services.handoff import (
    EV_INVITE,
    TRADE_STACKS,
    HandoffManager,
    Timing,
    batch,
    counts,
    plan,
    trade_cap,
)

A, B, C, D = 501, 502, 503, 504  # A: stored, B: kept, C: nobody wants it, D: untradable
NAMES = {A: "三級醉月劍法", B: "惡人谷書", C: "雜物", D: "綁定物"}
FAST = Timing(invite=2.0, open=1.0, step=1.0, seen=0.2, store=5.0, put_gap=0.0, poll=0.01)


class World:
    """Game windows that trade with each other: one bag, key and trade state per pid."""

    def __init__(self):
        self.chars = {}
        self.partner = {}
        self.offer = {}
        self.locked = set()
        self.confirmed = set()
        self.sent = []
        self.capacity = None
        self.stranger_once = set()  # pids whose next invite comes after a stranger's
        self.refuse_put = set()  # items tradeput refuses
        self.put_error = {}  # item -> any other tradeput error
        self.drop_invites = 0  # invites that never arrive
        self.mgr = None
        self.vaults = {}  # pid -> warehouse stacks

    def add(self, pid, name, stacks, tile=(10, 10)):
        self.chars[pid] = {"name": name, "stacks": [list(s) for s in stacks], "tile": tile}

    def key(self, pid):
        return (60001, 1000 + pid)

    def counts(self, pid):
        return counts([tuple(s) for s in self.chars[pid]["stacks"]])

    def push(self, pid, sub):
        self.mgr.events.push(pid, sub)

    def send(self, pid, line):
        self.sent.append((pid, line))
        verb, *args = line.split()
        me = self.chars[pid]
        if verb == "status":
            return {"ok": True, "self": 100 + pid, "tile": list(me["tile"]), "hp": [1, 1]}
        if verb == "near":
            return {
                "ok": True,
                "objects": [
                    {"h": 100 + p, "kind": 1, "id": self.key(p)[0], "inst": self.key(p)[1]}
                    for p in self.chars
                ],
            }
        if verb == "bag":
            return {
                "ok": True,
                "bag": [{"item": i, "inst": 0, "count": n} for i, n in me["stacks"]],
            }
        if verb == "closepanel":
            return {"ok": True}
        if verb == "warehouse":
            vault = self.vaults.get(pid, [])
            return {"ok": True, "open": False, "items": [{"item": i, "count": n} for i, n in vault]}
        if verb == "tradeinvite":
            target = int(args[0]) - 100
            if self.drop_invites:
                self.drop_invites -= 1
                return {"ok": True}  # sent, but the server never passes it on
            self.partner[target] = pid
            if target in self.stranger_once:
                self.stranger_once.discard(target)
                self.partner[target] = "stranger"
                self.push(target, EV_INVITE)
                self.partner[target] = pid
                self._stranger_read = target
            self.push(target, EV_INVITE)
            return {"ok": True}
        if verb == "trade":
            p = self.partner.get(pid)
            if getattr(self, "_stranger_read", None) == pid:
                self._stranger_read = None
                peer = {"kind": 1, "id": 60002, "inst": 9}
            else:
                peer = {"kind": 1, "id": self.key(p)[0], "inst": self.key(p)[1]} if p else {}
            reply = {
                "ok": True,
                "peer": peer,
                "offer": [{"item": i, "count": n} for i, n in self.offer.get(pid, [])],
            }
            if self.capacity is not None:
                reply["capacity"] = self.capacity
            return reply
        if verb == "tradeaccept":
            p = self.partner[pid]
            self.partner[p] = pid
            self.offer[pid], self.offer[p] = [], []
            self.locked -= {pid, p}
            self.confirmed -= {pid, p}
            self.push(pid, 2)
            self.push(p, 2)
            return {"ok": True}
        if verb == "tradeput":
            item = int(args[0])
            if item in self.refuse_put:
                return {"ok": False, "error": "item cannot be traded"}
            if item in self.put_error:
                return {"ok": False, "error": self.put_error[item]}
            stack = next(s for s in me["stacks"] if s[0] == item)
            me["stacks"].remove(stack)
            self.offer[pid].append(tuple(stack))
            self.push(self.partner[pid], 3)
            return {"ok": True}
        if verb == "tradelock":
            self.locked.add(pid)
            self.push(self.partner[pid], 5)
            return {"ok": True}
        if verb == "tradeconfirm":
            p = self.partner[pid]
            assert {pid, p} <= self.locked, "confirm before both locked"
            self.confirmed.add(pid)
            if p in self.confirmed:
                for giver, taker in ((pid, p), (p, pid)):
                    self.chars[taker]["stacks"] += [list(s) for s in self.offer[giver]]
                    self.offer[giver] = []
                self.push(pid, 8)
                self.push(p, 8)
            return {"ok": True}
        if verb == "tradecancel":
            p = self.partner.get(pid)
            for side in (pid, p):
                if side in self.offer:
                    self.chars[side]["stacks"] += [list(s) for s in self.offer[side]]
                    self.offer[side] = []
                    self.push(side, 6)
            return {"ok": True}
        raise AssertionError(line)


class Channel:
    def __init__(self, world):
        self.world = world
        self.lock = threading.Lock()

    def send(self, pid, line):
        with self.lock:
            return self.world.send(pid, line)


class Guard:
    @contextmanager
    def quiet(self, _pid):
        yield


class Store:
    def __init__(self):
        self.rows = {}

    def load_section(self, name, section, model):
        return self.rows.get((name, section)) or model()

    def save_section(self, name, section, value):
        self.rows[(name, section)] = value


class Supply:
    def __init__(self, world):
        self.world = world
        self.trips = []
        self.withdraws = []

    def add_host(self, _text):
        pass

    def run(self, pid, stop, note=None, host=None, store_only=None, withdraw=None, seated=False):
        assert seated  # 分身交貨 opens the warehouse sitting
        me = self.world.chars[pid]
        if withdraw is not None:
            wants, room = withdraw
            vault = self.world.vaults.setdefault(pid, [])
            stacks = [s for s in vault if s[0] in wants]
            take = stacks[:room]
            for s in take:
                vault.remove(s)
                me["stacks"].append(list(s))
            self.withdraws.append((pid, [tuple(s) for s in take]))

            class W:
                ok = True
                detail = ""
                withdrawn = sum(n for _i, n in take)
                left = len(stacks) - len(take)

            return W()
        self.trips.append((pid, dict(store_only)))
        for item, n in store_only.items():
            for s in [s for s in me["stacks"] if s[0] == item]:
                k = min(n, s[1])
                s[1] -= k
                n -= k
            me["stacks"] = [s for s in me["stacks"] if s[1] > 0]

        class R:
            ok = True
            detail = ""

        return R()


class Nav:
    def __init__(self, world):
        self.world = world
        self.walks = []

    def go(self, pid, dest, goal=None, stop=None, note=None):
        self.walks.append((pid, dest, goal))
        self.world.chars[pid]["tile"] = goal

        class R:
            ok = True
            reason = "arrived"
            detail = ""

        return R()


def setup(accounts=None):
    world = World()
    store = Store()
    supply = Supply(world)
    nav = Nav(world)
    accounts = accounts or {}
    mgr = HandoffManager(
        guard=Guard(),
        character_name=lambda pid: world.chars[pid]["name"] if pid in world.chars else None,
        channel=Channel(world),
        store=store,
        navigator=nav,
        supply=supply,
        read_stage=lambda _pid: 7,
        stage_name=lambda _sid: "杭州城",
        account_of=accounts.get,
        item_info=lambda i: (NAMES.get(i, f"#{i}"), i == D, False, "book"),
        timing=FAST,
    )
    world.mgr = mgr
    return world, store, supply, nav, mgr


def whitelist(store, name, items, slots=40):
    store.save_section(
        name,
        "handoff",
        HandoffConfig(items=[HandoffItem(item_id=i, store=s) for i, s in items], bag_slots=slots),
    )


def until(cond, secs=5.0):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def start_receiver(mgr, pid):
    assert mgr.start(pid, "receive") == (True, None)
    assert until(lambda: mgr.status(pid).step == "等送貨的人")


def finish(mgr, pid):
    assert until(lambda: not mgr.running(pid), 15.0), mgr.status(pid)
    return mgr.status(pid)


# ---- pure parts --------------------------------------------------------------


def test_plan_gives_an_item_to_the_first_receiver_that_wants_it():
    stacks = [(A, 1), (B, 2), (A, 3), (C, 1), (D, 1)]
    out = plan(stacks, [(1, {A}), (2, {A, B, D})], lambda i: i == D)
    assert out == {1: [(A, 1), (A, 3)], 2: [(B, 2)]}


def test_batch_takes_no_more_than_free_slots_or_the_cap():
    stacks = [(A, 1)] * 5
    assert batch(stacks, 9) == [(A, 1)] * TRADE_STACKS
    assert batch(stacks, 2) == [(A, 1)] * 2
    assert batch(stacks, 0) == []
    assert batch(stacks, 9, cap=5) == [(A, 1)] * 5


def test_trade_cap_reads_capacity_or_keeps_the_measured_three():
    assert trade_cap({"ok": True, "capacity": 12}) == 12
    assert trade_cap({"ok": True}) == TRADE_STACKS
    assert trade_cap({"ok": True, "capacity": 0}) == TRADE_STACKS
    assert trade_cap({"ok": False, "capacity": 12}) == TRADE_STACKS


# ---- two windows -------------------------------------------------------------


def test_rounds_move_every_whitelisted_stack_and_store_each_round():
    world, store, supply, nav, mgr = setup()
    world.add(1, "倉庫", [], tile=(30, 40))
    world.add(2, "送貨", [(A, 5), (A, 6), (B, 1), (C, 9), (A, 2), (D, 1)])
    whitelist(store, "倉庫", [(A, True), (B, False), (D, True)])
    start_receiver(mgr, 1)
    assert mgr.start(2, "send") == (True, None)
    sender = finish(mgr, 2)
    assert sender.ended == "全部交完"
    assert world.counts(2) == {C: 9, D: 1}  # unwanted and untradable stay
    assert world.counts(1) == {B: 1}  # A stored, B kept
    # Two rounds (3 stacks, then 1), each stored right after it came.
    assert supply.trips == [(1, {A: 11}), (1, {A: 2})]
    assert nav.walks == [(2, 7, (30, 40))]
    receiver = mgr.status(1)
    assert {c.item_id: (c.count, c.store) for c in receiver.moved} == {A: (13, True), B: (1, False)}
    mgr.stop(1)
    finish(mgr, 1)


def test_the_trade_capacity_limits_each_round():
    world, store, supply, _nav, mgr = setup()
    world.capacity = 1
    world.add(1, "倉庫", [])
    world.add(2, "送貨", [(A, 1), (A, 1)])
    whitelist(store, "倉庫", [(A, True)])
    start_receiver(mgr, 1)
    mgr.start(2, "send")
    assert finish(mgr, 2).ended == "全部交完"
    assert supply.trips == [(1, {A: 1}), (1, {A: 1})]
    mgr.stop(1)


def test_a_strangers_invite_is_ignored():
    world, store, _supply, _nav, mgr = setup()
    world.add(1, "倉庫", [])
    world.add(2, "送貨", [(A, 1)])
    world.stranger_once.add(1)
    whitelist(store, "倉庫", [(A, True)])
    start_receiver(mgr, 1)
    mgr.start(2, "send")
    assert finish(mgr, 2).ended == "全部交完"
    texts = [e.text for e in mgr.status(1).log]
    assert "有人邀請交易（不是送貨中的角色），不理" in texts
    mgr.stop(1)


def test_a_full_receiver_is_skipped():
    world, store, supply, _nav, mgr = setup()
    world.add(1, "倉庫", [(C, 1), (C, 1)])
    world.add(2, "送貨", [(A, 1)])
    whitelist(store, "倉庫", [(A, True)], slots=2)
    start_receiver(mgr, 1)
    mgr.start(2, "send")
    sender = finish(mgr, 2)
    assert sender.ended == "背包還有 1 格沒交出去"
    assert "倉庫 背包滿了，換下一個倉庫" in [e.text for e in sender.log]
    assert world.counts(2) == {A: 1} and supply.trips == []
    mgr.stop(1)


def test_a_receiver_on_the_same_account_is_not_offered():
    world, store, _supply, _nav, mgr = setup(accounts={"倉庫": 1, "送貨": 1})
    world.add(1, "倉庫", [])
    world.add(2, "送貨", [(A, 1)])
    whitelist(store, "倉庫", [(A, True)])
    start_receiver(mgr, 1)
    assert mgr.view(2).receivers[0].same_account
    mgr.start(2, "send")
    assert "沒有開著的倉庫" in finish(mgr, 2).ended
    assert not any(line.startswith("tradeinvite") for _p, line in world.sent)
    mgr.stop(1)


def test_two_receivers_in_the_order_they_started():
    world, store, supply, nav, mgr = setup()
    world.add(1, "倉庫一", [], tile=(1, 1))
    world.add(3, "倉庫二", [], tile=(5, 5))
    world.add(2, "送貨", [(A, 1), (B, 1)])
    whitelist(store, "倉庫一", [(A, True)])
    whitelist(store, "倉庫二", [(A, True), (B, True)])
    start_receiver(mgr, 1)
    start_receiver(mgr, 3)
    plan_rows = {r.item_id: r.receiver for r in mgr.view(2).plan}
    assert plan_rows == {A: "倉庫一", B: "倉庫二"}
    mgr.start(2, "send")
    assert finish(mgr, 2).ended == "全部交完"
    assert supply.trips == [(1, {A: 1}), (3, {B: 1})]
    assert [w[2] for w in nav.walks] == [(1, 1), (5, 5)]
    mgr.stop(1)
    mgr.stop(3)


def test_an_item_the_game_will_not_trade_is_skipped_and_the_rest_go():
    world, store, supply, _nav, mgr = setup()
    world.add(1, "倉庫", [])
    world.add(2, "送貨", [(B, 1), (A, 1)])
    world.refuse_put.add(B)
    whitelist(store, "倉庫", [(A, True), (B, True)])
    start_receiver(mgr, 1)
    mgr.start(2, "send")
    sender = finish(mgr, 2)
    assert sender.ended == "全部交完"
    assert world.counts(2) == {B: 1} and supply.trips == [(1, {A: 1})]
    assert "惡人谷書 不能交易，跳過" in [e.text for e in sender.log]
    mgr.stop(1)


def test_a_round_where_nothing_can_go_closes_the_empty_trade():
    world, store, supply, _nav, mgr = setup()
    world.add(1, "倉庫", [])
    world.add(2, "送貨", [(B, 1)])
    world.refuse_put.add(B)
    whitelist(store, "倉庫", [(B, True)])
    start_receiver(mgr, 1)
    mgr.start(2, "send")
    assert finish(mgr, 2).ended == "全部交完"
    assert (2, "tradecancel") in world.sent and supply.trips == []
    assert until(lambda: mgr.status(1).step == "等送貨的人")
    mgr.stop(1)


def test_a_refused_put_cancels_the_trade_on_both_sides():
    world, store, supply, _nav, mgr = setup()
    world.add(1, "倉庫", [])
    world.add(2, "送貨", [(A, 1), (B, 1)])
    world.put_error[B] = "bad quantity 0 (have 1)"
    whitelist(store, "倉庫", [(A, True), (B, True)])
    start_receiver(mgr, 1)
    mgr.start(2, "send")
    sender = finish(mgr, 2)
    assert (2, "tradecancel") in world.sent
    assert world.counts(2) == {A: 1, B: 1}  # the put A came back
    assert supply.trips == []
    assert any("放 惡人谷書 沒成功" in e.text for e in sender.log)
    # The receiver goes on waiting for the next sender.
    assert until(lambda: mgr.status(1).step == "等送貨的人")
    assert mgr.status(1).running
    mgr.stop(1)


def test_receiver_stop_waits_for_the_round_in_hand():
    world, store, _supply, _nav, mgr = setup()
    world.add(1, "倉庫", [])
    whitelist(store, "倉庫", [(A, True)])
    start_receiver(mgr, 1)
    mgr.stop(1)  # no round in hand: stops at once
    assert finish(mgr, 1).ended == "已停止"


def test_receiver_needs_a_whitelist():
    world, _store, _supply, _nav, mgr = setup()
    world.add(1, "倉庫", [])
    assert mgr.start(1, "receive") == (False, "還沒設定要收哪些東西")


def test_what_a_full_receiver_cannot_take_goes_to_the_next_one_that_wants_it():
    world, store, supply, _nav, mgr = setup()
    world.add(1, "倉庫一", [(C, 1)])
    world.add(3, "倉庫二", [])
    world.add(2, "送貨", [(A, 1)])
    whitelist(store, "倉庫一", [(A, True)], slots=1)  # full
    whitelist(store, "倉庫二", [(A, True)])
    start_receiver(mgr, 1)
    start_receiver(mgr, 3)
    mgr.start(2, "send")
    assert finish(mgr, 2).ended == "全部交完"
    assert supply.trips == [(3, {A: 1})]
    mgr.stop(1)
    mgr.stop(3)


def test_an_invite_that_never_arrives_times_out_and_both_go_on():
    world, store, supply, _nav, mgr = setup()
    world.add(1, "倉庫", [])
    world.add(2, "送貨", [(A, 1)])
    world.drop_invites = 1
    whitelist(store, "倉庫", [(A, True)])
    start_receiver(mgr, 1)
    mgr.start(2, "send")
    sender = finish(mgr, 2)
    assert sender.ended == "背包還有 1 格沒交出去"
    assert any("等太久" in e.text for e in sender.log)
    assert not any(line == "tradecancel" for _p, line in world.sent)  # no window was open
    assert until(lambda: mgr.status(1).step == "等送貨的人")
    assert mgr.status(1).running
    # A second sender run goes through.
    mgr.start(2, "send")
    assert finish(mgr, 2).ended == "全部交完"
    assert supply.trips == [(1, {A: 1})]
    mgr.stop(1)


def test_the_senders_warehouse_goes_too_a_bag_load_at_a_time():
    world, store, supply, nav, mgr = setup()
    world.add(1, "倉庫", [])
    world.add(2, "送貨", [(A, 1), (C, 1), (C, 1)])
    world.vaults[2] = [[A, 2], [B, 3], [A, 4], [C, 5]]
    whitelist(store, "倉庫", [(A, True), (B, False)])
    whitelist(store, "送貨", [], slots=4)  # two C stay: two free slots per load
    start_receiver(mgr, 1)
    mgr.start(2, "send")
    assert finish(mgr, 2).ended == "全部交完"
    assert supply.withdraws == [(2, [(A, 2), (B, 3)]), (2, [(A, 4)]), (2, [])]
    assert world.vaults[2] == [[C, 5]]  # never wanted
    assert world.counts(1) == {B: 3}  # A stored round by round
    assert world.counts(2) == {C: 2}
    mgr.stop(1)


def test_without_from_warehouse_only_the_bag_goes():
    world, store, supply, _nav, mgr = setup()
    world.add(1, "倉庫", [])
    world.add(2, "送貨", [(A, 1)])
    world.vaults[2] = [[A, 2]]
    whitelist(store, "倉庫", [(A, True)])
    store.save_section("送貨", "handoff", HandoffConfig(from_warehouse=False))
    start_receiver(mgr, 1)
    mgr.start(2, "send")
    assert finish(mgr, 2).ended == "全部交完"
    assert supply.withdraws == [] and world.vaults[2] == [[A, 2]]
    mgr.stop(1)


def test_the_view_lists_the_bag_and_the_last_seen_warehouse_for_the_pickers():
    world, _store, _supply, _nav, mgr = setup()
    world.add(1, "倉庫", [(A, 1), (A, 2), (C, 1)])
    world.vaults[1] = [[B, 3], [B, 4]]
    view = mgr.view(1)
    assert {(r.item_id, r.count, r.stacks) for r in view.bag} == {(A, 3, 2), (C, 1, 1)}
    assert [(r.item_id, r.count, r.stacks, r.category) for r in view.warehouse] == [
        (B, 7, 2, "book")
    ]
