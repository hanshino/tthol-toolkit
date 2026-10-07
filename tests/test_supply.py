"""補給: the pure rules and one trip against a fake game."""

from __future__ import annotations

import threading
from contextlib import contextmanager

import pytest

from services import supply as sp
from services.api_types import ItemRule, ItemRules, SupplyConfig, SupplyItem
from services.guard import GuardStore, read_holdings, read_stage_id
from services.item_rules import ItemFact
from services.navigator import NavResult
from services.shop_catalog import Buyable, ShopCatalog, ShopNpc
from services.supply_points import SupplyPoint

POTION, MP, SCROLL, JUNK, ORE, PET = 24007, 24021, 24037, 900, 901, 7000
SHOP, OTHER = 7, 27


def fact(name: str, sell: bool = True, store: bool = True) -> ItemFact:
    return ItemFact(name, sell, store, False, None, None)


FACTS = {JUNK: fact("狼皮"), ORE: fact("玄鐵礦"), POTION: fact("金創藥"), PET: fact("小狐")}


# ---- pure parts ----------------------------------------------------------------


def test_sell_list_keeps_and_skips_unsellable():
    rules = ItemRules(
        items={
            JUNK: ItemRule(action="sell", keep=2),
            ORE: ItemRule(action="store"),
            POTION: ItemRule(action="sell", keep=50),
        }
    )
    bag = {JUNK: 12, ORE: 30, POTION: 40}
    assert sp.sell_list(rules, bag, FACTS.get, "sell") == [(JUNK, 10, 2, 12)]
    assert sp.sell_list(rules, bag, FACTS.get, "store") == [(ORE, 30, 0, 30)]
    no_shop = {JUNK: fact("狼皮", sell=False)}
    assert sp.sell_list(rules, bag, no_shop.get, "sell") == []


def test_buy_needs_counts_bag_and_pet_targets():
    items = [SupplyItem(item_id=POTION, bag=200, pet=400), SupplyItem(item_id=SCROLL, bag=10)]
    assert sp.buy_needs(items, {POTION: 37, SCROLL: 10}, {POTION: 120}) == [(POTION, 163, 280)]


def test_pet_move_only_moves_what_is_over_the_bag_target():
    assert sp.pet_move(237, 200, 280) == 37
    assert sp.pet_move(150, 200, 280) == 0
    assert sp.pet_move(400, 200, 43) == 43


def test_affordable_keeps_the_gold_floor():
    assert sp.affordable(100_000 + 312 * 10, 100_000, 312, 200) == 10
    assert sp.affordable(50_000, 100_000, 312, 200) == 0
    assert sp.affordable(None, 0, 312, 5) == 0


def test_bank_action_brings_gold_back_to_target():
    cfg = SupplyConfig(gold_low=100_000, gold_high=2_000_000, gold_target=500_000)
    assert sp.bank_action(40_000, cfg) == ("withdraw", 460_000)
    assert sp.bank_action(3_000_000, cfg) == ("deposit", 2_500_000)
    assert sp.bank_action(800_000, cfg) is None
    assert sp.bank_action(40_000, SupplyConfig()) is None  # off by default
    assert sp.bank_text(("deposit", 2_500_000)) == "存進錢莊 2,500,000"


def test_estimate_load_after_selling_and_buying():
    bag = [(POTION, 37), (JUNK, 46), (SCROLL, 3)]
    pet = [(POTION, 120)]
    items = [SupplyItem(item_id=POTION, bag=200, pet=400), SupplyItem(item_id=SCROLL, bag=10)]
    weights = {POTION: 2, JUNK: 1, SCROLL: 1}
    load = sp.estimate_load(bag, pet, 1000, 3000, [(JUNK, 46, 0, 46)], items, weights)
    assert load.weight_after == 1000 - 46 + 163 * 2 + 7
    assert load.weight_peak == load.weight_after + 200 * 2  # a full stack waits for the pet
    assert (load.slots, load.slots_after) == (3, 2)  # 狼皮 gone, potions still one stack
    assert (load.pet_slots, load.pet_slots_after) == (1, 2)  # 400 = two stacks


def npc(npc_id: int, shop: int, stage: int = 23) -> ShopNpc:
    return ShopNpc(npc_id, f"商人{npc_id}", stage, (10, 10), ((1, shop),))


def test_pick_stop_prefers_coverage_then_cost():
    a, b, c = npc(1, SHOP), npc(2, OTHER), npc(3, SHOP, stage=30)
    sells = {SHOP: {POTION: 1, SCROLL: 1}, OTHER: {MP: 1}}
    costs = {1: 5.0, 2: 1.0, 3: 2.0}

    def sold_by(shops):
        return {i for s in shops for i in sells.get(s, {})}

    pick = sp.pick_stop(
        [a, b, c],
        lambda n: n.shop_ids,
        sold_by,
        {POTION, SCROLL, MP},
        lambda n: costs[n.npc_id],
        set(),
        False,
    )
    assert pick == (c, {POTION, SCROLL})
    # Unreachable shops are passed over.
    pick = sp.pick_stop(
        [a, b, c],
        lambda n: n.shop_ids,
        sold_by,
        {POTION},
        lambda n: None if n.npc_id == 3 else costs[n.npc_id],
        set(),
        False,
    )
    assert pick == (a, {POTION})
    # Only selling: any shop does, the nearest wins.
    pick = sp.pick_stop(
        [a, b, c], lambda n: n.shop_ids, sold_by, set(), lambda n: costs[n.npc_id], set(), True
    )
    assert pick == (b, set())


def test_pick_stop_prefers_a_shop_in_town_over_a_cheaper_ride():
    # 成都少城: a horse to 曼陀羅城 reckons a little cheaper than walking into
    # 成都市集, but the town's own shop wins (user, 2026-10-08).
    home, away, poor = npc(1, SHOP, stage=173), npc(2, SHOP, stage=264), npc(3, OTHER, stage=54)
    sells = {SHOP: {POTION: 1, SCROLL: 1}, OTHER: {POTION: 1}}
    costs = {1: 1.4, 2: 1.3, 3: 1.0}

    def sold_by(shops):
        return {i for s in shops for i in sells.get(s, {})}

    def pick(cost=lambda n: costs[n.npc_id]):
        return sp.pick_stop(
            [home, away, poor],
            lambda n: n.shop_ids,
            sold_by,
            {POTION, SCROLL},
            cost,
            set(),
            False,
            lambda n: n.stage in (53, 54, 173),
        )

    assert pick() == (home, {POTION, SCROLL})
    # Coverage still comes first: the town shop selling less does not win.
    assert pick()[0] is not poor
    # No way to the town's shop: the ride.
    assert pick(lambda n: None if n.npc_id == 1 else costs[n.npc_id]) == (away, {POTION, SCROLL})


# ---- a trip -------------------------------------------------------------------


class FakeGame:
    """The hook command pipe plus the memory reads a trip uses."""

    def __init__(self, bag, pet=None, gold=1_000_000, sells=None, pets_out=False) -> None:
        self.bag: dict[int, int] = dict(bag)
        self.pet: dict[int, int] = dict(pet or {})
        self.gold = gold
        self.sells = sells or {POTION: 312, SCROLL: 250}
        self.pets_out = pets_out
        self.shop_open = False
        self.shop_npc = 0
        self.sent: list[str] = []
        self.stage = 51
        self.refuse_buy = False
        self.summon_delay = 0  # `pet` polls before a summoned pet shows up
        self.drop_puts = 0  # puts the server ignores (sent before the pet is out)
        self.warehouse_open = False
        self.stuck_window = False
        self.vault: dict[int, int] = {}
        self.balance = 0
        self.pose = "Wait"

    def send(self, _pid, line: str) -> dict:
        self.sent.append(line)
        cmd, *args = line.split()
        if cmd == "status":
            return {"ok": True, "self": 1, "hp": [10, 10], "tile": [1, 1], "pose": self.pose}
        if cmd == "sit":
            self.pose = "Wait" if self.pose == "Sit" else "Sit"
            return {"ok": True}
        if cmd == "near":
            return {
                "ok": True,
                "objects": [
                    {"h": 1, "id": 0, "x": 400, "y": 400},
                    {"h": 9, "id": 6130, "inst": 1, "x": 440, "y": 400},
                    {"h": 8, "id": 6448, "inst": 2, "x": 480, "y": 400},
                ],
            }
        if cmd == "talk":
            if args[0] == "8":
                self.warehouse_open = True  # the 錢莊伙計 opens it on talk
            return {"ok": True}
        if cmd == "closepanel":
            if self.stuck_window:
                return {"ok": True}
            was = self.shop_open or self.warehouse_open
            self.shop_open = self.warehouse_open = False
            return {"ok": True} if was else {"ok": False, "error": "no panel open"}
        if cmd == "warehouse":
            items = [{"item": i, "inst": 0, "count": n} for i, n in self.vault.items() if n]
            return {"ok": True, "open": self.warehouse_open, "items": items}
        if cmd == "withdraw":
            item, qty = int(args[0]), int(args[1])
            if self.warehouse_open and self.vault.get(item, 0) >= qty:
                self.vault[item] -= qty
                self.bag[item] = self.bag.get(item, 0) + qty
            return {"ok": True, "item": item, "qty": qty}
        if cmd == "store":
            item, qty = int(args[0]), int(args[1])
            if self.warehouse_open:
                self.bag[item] -= qty
                self.vault[item] = self.vault.get(item, 0) + qty
            return {"ok": True, "item": item, "qty": qty}
        if cmd == "bank":
            return {"ok": True, "open": self.warehouse_open, "balance": self.balance}
        if cmd in ("bankin", "bankout"):
            amount = int(args[0])
            sign = 1 if cmd == "bankout" else -1
            self.gold += sign * amount
            self.balance -= sign * amount
            return {"ok": True, "amount": amount}
        if cmd == "dialog":
            if self.shop_open and self.shop_npc == 6130:
                return {"ok": True, "open": False}
            return {
                "ok": True,
                "open": True,
                "waiting": False,
                "npc": {"id": 6130},
                "options": [0, 2],
            }
        if cmd == "option":
            if args[0] == "1":
                self.shop_open, self.shop_npc = True, 6130
            return {"ok": True}
        if cmd == "shop":
            return {"ok": True, "open": self.shop_open, "mode": 0, "npc": {"id": self.shop_npc}}
        if cmd == "buy":
            item, qty = int(args[0]), int(args[1])
            if not self.refuse_buy and item in self.sells and self.shop_npc == 6130:
                self.bag[item] = self.bag.get(item, 0) + qty
                self.gold -= qty * self.sells[item]
            return {"ok": True}
        if cmd == "sell":
            item, qty = int(args[0]), int(args[1])
            self.bag[item] -= qty
            return {"ok": True}
        if cmd == "pet":
            if self.pets_out == "coming":
                self.summon_delay -= 1
                if self.summon_delay < 0:
                    self.pets_out = True
            return {"ok": True, "slots": [PET] if self.pets_out is True else []}
        if cmd == "petsummon":
            self.pets_out = "coming" if self.summon_delay else True
            return {"ok": True}
        if cmd == "petdismiss":
            self.pets_out = False
            return {"ok": True}
        if cmd == "petput":
            item, qty = int(args[0]), int(args[1])
            if self.drop_puts:
                self.drop_puts -= 1
            elif self.pets_out is True:
                self.bag[item] -= qty
                self.pet[item] = self.pet.get(item, 0) + qty
            return {"ok": True}
        return {"ok": False, "error": "unknown"}

    def read(self, _pid, fn):
        if fn is read_holdings:
            return dict(self.bag), dict(self.pet)
        if fn is read_stage_id:
            return self.stage, "洛陽外城"
        if fn is sp.read_gold:
            return self.gold
        if fn is sp.read_level:
            return 60
        raise AssertionError(fn)


class FakeChannel:
    def __init__(self, game: FakeGame) -> None:
        self.game = game

    def send(self, pid, line):
        return self.game.send(pid, line)


class FakeGuard:
    def __init__(self) -> None:
        self.held = []

    @contextmanager
    def quiet(self, pid):
        self.held.append("quiet")
        yield

    @contextmanager
    def hold_pets(self, pid):
        self.held.append("pets")
        yield


class FakeNav:
    def __init__(self, game: FakeGame) -> None:
        self.game = game
        self.went: list[tuple[int, tuple[int, int]]] = []

    def go(self, pid, dest, goal=None, stop=None, note=None):
        # A player never walks off with a shop / warehouse window open.
        assert not self.game.shop_open and not self.game.warehouse_open, "walked with a window open"
        self.went.append((dest, goal))
        self.game.stage = dest
        return NavResult(True, "arrived", "", dest, goal)


class FakeCaps:
    def __init__(self, names) -> None:
        self.names = frozenset(names)

    def get(self, pid, max_age=0.0):
        return self.names

    def cached(self, pid):
        return self.names


class FakeScript:
    """Option jump 1 opens SHOP; the family menus open FAMILY_SHOP with a manor."""

    def __init__(self, _t, _level, manor=None, _items=None) -> None:
        self.level, self.manor = 60, manor

    def shops_from_msg(self, msg):
        if msg in (25098, 25106):
            return [FAMILY_SHOP] if self.manor else []
        return {1: [SHOP if not self.manor else FAMILY_SHOP], 2: [SHOP]}.get(msg, [])


ALL = sp.SUPPLY_COMMANDS + ("buy", "sell") + sp.PET_COMMANDS + sp.SUMMON_COMMANDS
FAMILY_SHOP = 29
SHOPKEEPER = SupplyPoint("general", 6130, "檀泉道具商", 23, "檀泉別苑", (23, 15), shop=SHOP)
FAMILY_POINT = SupplyPoint("family", 6130, "家族道具商", 51, "洛陽外城", (70, 142))
KEEPER = SupplyPoint("warehouse", 6448, "錢莊伙計", 23, "檀泉別苑", (72, 14))


@pytest.fixture(autouse=True)
def no_db(monkeypatch):
    class Route:
        cost = 1.0

    monkeypatch.setattr(sp.rp, "_tables", lambda *a: type("T", (), {"stages": {23: "檀泉別苑"}})())
    monkeypatch.setattr(sp.rp, "_Script", FakeScript)
    monkeypatch.setattr(sp.rp, "cached_graph", lambda *a: None)
    monkeypatch.setattr(sp.rp, "plan", lambda *a: Route())


def make(
    game: FakeGame,
    cfg: SupplyConfig,
    rules: ItemRules | None = None,
    caps=ALL,
    manor=None,
    family_stock=None,
):
    store = GuardStore()
    store.save_section("晨曦", sp.SUPPLY_SECTION, cfg)
    if rules is not None:
        store.save_items("晨曦", rules)
    cat = ShopCatalog(
        sells={SHOP: dict(game.sells), FAMILY_SHOP: dict(family_stock or game.sells)},
        items={
            i: Buyable(i, FACTS[i].name if i in FACTS else f"#{i}", "POTION", p, 1)
            for i, p in game.sells.items()
        },
    )
    nav = FakeNav(game)
    guard = FakeGuard()
    clock = {"t": 0.0}

    def sleep(ev, secs):
        clock["t"] += secs
        return False

    mgr = sp.SupplyManager(
        guard=guard,
        read_locked=game.read,
        character_name=lambda pid: "晨曦",
        channel=FakeChannel(game),
        store=store,
        navigator=nav,
        hook_caps=FakeCaps(caps),
        facts=FACTS.get,
        catalog=lambda: cat,
        clock=lambda: clock["t"],
        wall=lambda: 0.0,
        sleep=sleep,
        pet_items=lambda: frozenset({PET}),
        manor=lambda pid: manor,
        points=(SHOPKEEPER, FAMILY_POINT, KEEPER),
    )
    return mgr, nav, guard


def test_trip_sells_buys_and_fills_the_pet_bag():
    game = FakeGame({POTION: 37, JUNK: 46, PET: 1}, {POTION: 120})
    cfg = SupplyConfig(items=[SupplyItem(item_id=POTION, bag=200, pet=400)], keep_gold=0)
    rules = ItemRules(items={JUNK: ItemRule(action="sell")})
    mgr, nav, guard = make(game, cfg, rules)
    result = mgr.run(1, threading.Event())
    assert result.ok and result.reason == "done", result
    assert nav.went == [(23, (23, 15))]
    assert game.bag[POTION] == 200 and game.pet[POTION] == 400
    assert JUNK not in game.bag or game.bag[JUNK] == 0
    assert (result.sold, result.bought, result.put) == (46, 443, 280)
    assert "option 1" in game.sent  # the option whose jump opens the shop
    assert all(int(line.split()[2]) <= sp.STACK for line in game.sent if line.startswith("buy"))
    assert game.sent[-1] == "petdismiss"  # the pet this trip summoned goes back
    assert guard.held == ["quiet", "pets"]


def test_puts_wait_for_the_summoned_pet_and_retry_once():
    game = FakeGame({POTION: 0, PET: 1})
    game.summon_delay, game.drop_puts = 3, 1
    cfg = SupplyConfig(items=[SupplyItem(item_id=POTION, bag=100, pet=100)], keep_gold=0)
    mgr, _, _ = make(game, cfg)
    result = mgr.run(1, threading.Event())
    assert game.bag[POTION] == 100 and game.pet[POTION] == 100, result
    summoned = game.sent.index("petsummon %d" % PET)
    first_buy = next(i for i, s in enumerate(game.sent) if s.startswith("buy"))
    # polled the pet bar until the pet showed (3 empty replies), before buying
    assert game.sent[summoned + 1 : first_buy].count("pet") == 4
    assert [s for s in game.sent if s.startswith("petput")][:2] == ["petput %d 100" % POTION] * 2
    summon = next(line for line in mgr.status(1).log if line.text.startswith("召喚"))
    assert summon.phase == "confirmed"


def test_a_pet_that_never_comes_out_leaves_the_pet_bag():
    game = FakeGame({POTION: 0, PET: 1})
    game.summon_delay = 10_000
    cfg = SupplyConfig(items=[SupplyItem(item_id=POTION, bag=50, pet=100)], keep_gold=0)
    mgr, _, _ = make(game, cfg)
    mgr.run(1, threading.Event())
    assert game.bag[POTION] == 50 and game.pet.get(POTION, 0) == 0
    assert not any(s.startswith("petput") for s in game.sent)
    assert any("寵物沒有出來" in line.text for line in mgr.status(1).log)


def test_nothing_to_do_skips_the_trip():
    game = FakeGame({POTION: 200})
    mgr, nav, _ = make(game, SupplyConfig(items=[SupplyItem(item_id=POTION, bag=200)]))
    result = mgr.run(1, threading.Event())
    assert result.reason == "nothing" and nav.went == []


def test_gold_floor_stops_buying_and_short_can_stop_the_module():
    game = FakeGame({POTION: 0}, gold=100_000 + 312 * 50)
    cfg = SupplyConfig(
        items=[SupplyItem(item_id=POTION, bag=200)], keep_gold=100_000, stop_when_short=True
    )
    mgr, _, _ = make(game, cfg)
    result = mgr.run(1, threading.Event())
    assert game.bag[POTION] == 50
    assert result.reason == "short" and not result.ok
    assert "金創藥" in result.detail
    assert any("銀兩不夠" in line.text for line in mgr.status(1).log)


def test_a_buy_that_never_lands_is_unconfirmed():
    game = FakeGame({POTION: 0})
    game.refuse_buy = True
    mgr, _, _ = make(game, SupplyConfig(items=[SupplyItem(item_id=POTION, bag=200)]))
    result = mgr.run(1, threading.Event())
    assert result.ok and "沒補齊" in result.detail
    assert [line.phase for line in mgr.status(1).log if line.text.startswith("買")] == [
        "unconfirmed"
    ]


def test_item_the_shop_does_not_sell_is_skipped_by_default():
    game = FakeGame({POTION: 0, MP: 0})
    cfg = SupplyConfig(items=[SupplyItem(item_id=POTION, bag=10), SupplyItem(item_id=MP, bag=10)])
    mgr, nav, _ = make(game, cfg)
    result = mgr.run(1, threading.Event())
    assert game.bag[POTION] == 10 and game.bag[MP] == 0
    assert len(nav.went) == 1
    assert "沒補齊" in result.detail


def test_missing_hook_commands_stop_before_walking():
    game = FakeGame({POTION: 0})
    mgr, nav, _ = make(
        game, SupplyConfig(items=[SupplyItem(item_id=POTION, bag=10)]), caps=sp.SUPPLY_COMMANDS
    )
    result = mgr.run(1, threading.Event())
    assert result.reason == "no-hook" and "buy" in result.detail and nav.went == []


def test_no_pet_summon_leaves_pet_targets_alone():
    game = FakeGame({POTION: 0, PET: 1})
    cfg = SupplyConfig(items=[SupplyItem(item_id=POTION, bag=50, pet=100)], pet_summon=False)
    mgr, _, _ = make(game, cfg)
    mgr.run(1, threading.Event())
    assert game.bag[POTION] == 50 and game.pet.get(POTION, 0) == 0
    assert "petsummon" not in " ".join(game.sent)


def test_store_and_bank_are_noted_until_the_hook_can():
    game = FakeGame({ORE: 30}, gold=40_000)
    cfg = SupplyConfig(gold_low=100_000, gold_target=500_000)
    mgr, nav, _ = make(game, cfg, ItemRules(items={ORE: ItemRule(action="store")}))
    result = mgr.run(1, threading.Event())
    texts = [line.text for line in mgr.status(1).log]
    assert any(t.startswith("存倉跳過") and "玄鐵礦 ×30" in t for t in texts)
    assert any(t.startswith("錢莊跳過") and "從錢莊領 460,000" in t for t in texts)
    assert result.ok and nav.went == []  # nothing to buy or sell: no walk


def test_a_shop_window_left_open_is_not_taken_for_this_one():
    game = FakeGame({POTION: 0})
    game.shop_open, game.shop_npc = True, 6121  # another shopkeeper's, never closed
    mgr, _, _ = make(game, SupplyConfig(items=[SupplyItem(item_id=POTION, bag=10)]))
    result = mgr.run(1, threading.Event())
    assert result.ok and game.bag[POTION] == 10
    first_buy = next(i for i, line in enumerate(game.sent) if line.startswith("buy"))
    assert game.sent.index("option 1") < first_buy
    assert not game.shop_open  # the stale window was closed before the walk


def test_a_second_trip_waits_for_the_first():
    game = FakeGame({POTION: 0})
    mgr, nav, _ = make(game, SupplyConfig(items=[SupplyItem(item_id=POTION, bag=10)]))
    mgr._hosts_running[1] = None  # the 現在補給 button's trip
    result = mgr.run(1, threading.Event(), host="神武玄天塔")
    assert result.reason == "busy" and nav.went == []


def test_store_only_needs_no_hook_commands():
    game = FakeGame({ORE: 30})
    mgr, nav, _ = make(
        game, SupplyConfig(), ItemRules(items={ORE: ItemRule(action="store")}), caps=()
    )
    result = mgr.run(1, threading.Event())
    assert result.ok and nav.went == []


def test_a_family_buys_only_at_the_family_shop():
    game = FakeGame({POTION: 0})
    mgr, nav, _ = make(game, SupplyConfig(items=[SupplyItem(item_id=POTION, bag=10)]), manor=1151)
    result = mgr.run(1, threading.Event())
    assert result.ok and game.bag[POTION] == 10
    assert nav.went == [(51, (70, 142))]  # the 家族道具商, not the nearer town shop
    assert any("家族商人・高級商店" in line.text for line in mgr.status(1).log)


def test_an_item_the_family_shop_does_not_sell_stops_before_walking():
    game = FakeGame({POTION: 0, SCROLL: 0})
    cfg = SupplyConfig(
        items=[SupplyItem(item_id=POTION, bag=10), SupplyItem(item_id=SCROLL, bag=5)]
    )
    mgr, nav, _ = make(game, cfg, manor=1151, family_stock={POTION: 300})
    result = mgr.run(1, threading.Event())
    assert result.reason == "unsold" and not result.ok
    assert "回城捲軸" in result.detail or f"#{SCROLL}" in result.detail
    assert nav.went == []


def test_no_family_uses_the_town_shopkeepers():
    game = FakeGame({POTION: 0})
    mgr, nav, _ = make(game, SupplyConfig(items=[SupplyItem(item_id=POTION, bag=10)]))
    mgr.run(1, threading.Event())
    assert nav.went == [(23, (23, 15))]


WAREHOUSE_CAPS = ALL + sp.STORE_COMMANDS + sp.BANK_COMMANDS


def test_warehouse_first_stores_and_withdraws_then_buys():
    game = FakeGame({ORE: 30, POTION: 0}, gold=40_000)
    game.balance = 1_000_000
    cfg = SupplyConfig(
        items=[SupplyItem(item_id=POTION, bag=10)],
        keep_gold=0,
        gold_low=100_000,
        gold_target=500_000,
    )
    rules = ItemRules(items={ORE: ItemRule(action="store")})
    mgr, nav, _ = make(game, cfg, rules, caps=WAREHOUSE_CAPS)
    result = mgr.run(1, threading.Event())
    assert result.ok, result
    assert [d for d, _g in nav.went] == [23, 23]  # the 錢莊伙計, then the shop
    assert game.vault == {ORE: 30} and game.bag[ORE] == 0
    assert game.gold == 500_000 - 312 * 10
    assert game.bag[POTION] == 10
    assert "存倉 30 個" in result.detail and "從錢莊領 460,000" in result.detail


def test_deposit_only_walks_to_the_warehouse_and_not_the_shop():
    game = FakeGame({}, gold=6_000_000)
    cfg = SupplyConfig(gold_high=5_000_000, gold_target=1_000_000)
    mgr, nav, _ = make(game, cfg, caps=WAREHOUSE_CAPS)
    result = mgr.run(1, threading.Event())
    assert result.ok and nav.went == [(23, (72, 14))]
    assert game.gold == 1_000_000 and game.balance == 5_000_000


def test_same_npc_checks_id_and_instance():
    assert sp.same_npc({"id": 6130, "instance": 1}, {"id": 6130, "inst": 1})
    assert not sp.same_npc({"id": 6130, "instance": 2}, {"id": 6130, "inst": 1})
    assert not sp.same_npc({"id": 6121}, {"id": 6130, "inst": 1})
    assert sp.same_npc({"id": 6130}, {"id": 6130, "inst": 1})  # no instance: id alone


def test_a_window_that_will_not_close_stops_before_walking():
    game = FakeGame({POTION: 0})
    game.shop_open, game.shop_npc = True, 6121
    game.stuck_window = True
    mgr, nav, _ = make(game, SupplyConfig(items=[SupplyItem(item_id=POTION, bag=10)]))
    result = mgr.run(1, threading.Event())
    assert result.reason == "error" and "關不掉" in result.detail
    assert nav.went == []


def test_windows_are_closed_when_the_trip_ends():
    game = FakeGame({POTION: 0})
    mgr, _, _ = make(game, SupplyConfig(items=[SupplyItem(item_id=POTION, bag=10)]))
    mgr.run(1, threading.Event())
    assert not game.shop_open
    assert game.sent.index("closepanel") > max(
        i for i, line in enumerate(game.sent) if line.startswith("buy")
    )


def test_store_only_stores_just_those_and_nothing_else():
    game = FakeGame({ORE: 30, JUNK: 46, POTION: 0}, gold=40_000)
    game.balance = 1_000_000
    cfg = SupplyConfig(items=[SupplyItem(item_id=POTION, bag=10)], keep_gold=0, gold_low=100_000)
    rules = ItemRules(items={JUNK: ItemRule(action="sell")})
    mgr, nav, _ = make(game, cfg, rules, caps=WAREHOUSE_CAPS)
    result = mgr.run(1, threading.Event(), store_only={ORE: 12, 99999: 5})
    assert result.ok and result.stored == 12, result
    assert [d for d, _g in nav.went] == [23]  # the 錢莊伙計 only: no shop
    assert game.vault == {ORE: 12} and game.bag[ORE] == 18
    assert game.bag[JUNK] == 46 and game.bag[POTION] == 0  # no sell, no buy
    assert game.gold == 40_000  # no 錢莊 move


def test_withdraw_takes_out_the_wanted_stacks_that_fit():
    game = FakeGame({JUNK: 1}, gold=40_000)
    game.vault = {ORE: 300, SCROLL: 2, POTION: 5}
    cfg = SupplyConfig(items=[SupplyItem(item_id=POTION, bag=10)], keep_gold=0, gold_low=100_000)
    mgr, nav, _ = make(game, cfg, ItemRules(), caps=WAREHOUSE_CAPS + ("withdraw",))
    result = mgr.run(1, threading.Event(), withdraw=(frozenset({ORE, SCROLL}), 1))
    assert result.ok and result.withdrawn == 300 and result.left == 1, result
    assert game.bag[ORE] == 300 and game.vault[SCROLL] == 2  # one slot: one stack
    assert [line for line in game.sent if line.startswith("withdraw")] == [
        f"withdraw {ORE} 255",
        f"withdraw {ORE} 45",
    ]
    assert [d for d, _g in nav.went] == [23]  # the 錢莊伙計 only
    assert game.bag.get(POTION, 0) == 0 and game.gold == 40_000  # no buy, no 錢莊 move


def test_withdraw_needs_the_hook_command():
    game = FakeGame({}, gold=40_000)
    mgr, _nav, _ = make(game, SupplyConfig(), ItemRules(), caps=WAREHOUSE_CAPS)
    result = mgr.run(1, threading.Event(), withdraw=(frozenset({ORE}), 3))
    assert not result.ok and "withdraw" in result.detail


SIT_CAPS = WAREHOUSE_CAPS + ("withdraw", "sit")


def test_a_seated_trip_sits_and_opens_the_warehouse_from_where_it_is():
    game = FakeGame({ORE: 30}, gold=40_000)
    game.stage = 23  # the 錢莊伙計's map, and he is on screen
    mgr, nav, _ = make(game, SupplyConfig(), ItemRules(), caps=SIT_CAPS)
    result = mgr.run(1, threading.Event(), store_only={ORE: 12}, seated=True)
    assert result.ok and result.stored == 12, result
    assert nav.went == []  # no walk
    assert game.sent.count("sit") == 2 and game.pose == "Wait"  # sat, then up again
    assert game.vault == {ORE: 12}


def test_a_seated_trip_on_another_map_walks_as_usual():
    game = FakeGame({ORE: 30}, gold=40_000)
    mgr, nav, _ = make(game, SupplyConfig(), ItemRules(), caps=SIT_CAPS)
    result = mgr.run(1, threading.Event(), store_only={ORE: 12}, seated=True)
    assert result.ok and [d for d, _g in nav.went] == [23]
    assert "sit" not in game.sent


def test_withdraw_waits_for_the_warehouse_list_to_fill():
    # Not opened since the game started: the list reads empty for a moment.
    game = FakeGame({}, gold=40_000)
    game.vault = {ORE: 5}
    late = {"reads": 0}
    send = game.send

    def slow(pid, line):
        if line == "warehouse" and game.warehouse_open:
            late["reads"] += 1
            if late["reads"] <= 3:
                return {"ok": True, "open": True, "items": []}
        return send(pid, line)

    game.send = slow
    mgr, _nav, _ = make(game, SupplyConfig(), ItemRules(), caps=WAREHOUSE_CAPS + ("withdraw",))
    result = mgr.run(1, threading.Event(), withdraw=(frozenset({ORE}), 3))
    assert result.ok and result.withdrawn == 5, result


def test_a_stuck_warehouse_flag_is_taken_as_closed():
    # The hook reads the warehouse open with nothing on screen, and closepanel
    # says no window open (live 2026-10-07): the trip goes on.
    game = FakeGame({ORE: 30}, gold=40_000)
    game.stuck_flag = True
    send = game.send

    def stuck(pid, line):
        if line == "warehouse":
            r = send(pid, line)
            return {**r, "open": True}
        if line == "closepanel" and not game.shop_open:
            send(pid, line)
            return {"ok": False, "error": "no window open"}
        return send(pid, line)

    game.send = stuck
    mgr, _nav, _ = make(game, SupplyConfig(), ItemRules(), caps=WAREHOUSE_CAPS)
    result = mgr.run(1, threading.Event(), store_only={ORE: 12})
    assert result.ok and result.stored == 12, result
