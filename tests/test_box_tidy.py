import pytest

from services._paths import bundled
from services.box_tidy import BAG_SLOTS, BoxTidy, Loot, bag_view, load_loot, to_store

BOX_A, BOX_B = 31620, 31621  # 歲星寶箱, 鎮星寶箱
HELMET, BOOK, PILL, HEAL, CHARM = 23292, 50001, 28150, 24008, 26966
LOOT = Loot(
    boxes=(BOX_A, BOX_B),
    potions=frozenset({PILL, HEAL}),
    store=frozenset({HELMET, BOOK, CHARM}),
    names={BOX_A: "歲星寶箱", BOX_B: "鎮星寶箱", HELMET: "聖曦頭盔", PILL: "賞善寧心符"},
    collect=frozenset({HELMET}),
)


class FakeGame:
    """A bag of stacks; a box `use` turns it into the next loot in `drops`."""

    def __init__(self, stacks, drops=(), stuck=(), store_room=10**9):
        self.stacks = [list(s) for s in stacks]  # [item, count]
        self.drops = list(drops)
        self.stuck = set(stuck)  # items whose use does nothing
        self.store_room = store_room
        self.sent = []
        self.trips = []
        self.auto_calls = []

    def counts(self):
        out = {}
        for item, n in self.stacks:
            out[item] = out.get(item, 0) + n
        return out

    def take(self, item, n):
        for s in self.stacks:
            if s[0] == item:
                k = min(n, s[1])
                s[1] -= k
                n -= k
        self.stacks = [s for s in self.stacks if s[1] > 0]

    def give(self, item, n):
        for s in self.stacks:
            if s[0] == item and item in (PILL, HEAL):
                s[1] += n
                return
        self.stacks.append([item, n])

    def cmd(self, line):
        self.sent.append(line)
        if line.split()[0] in ("collection", "collectpage", "collect", "openpanel", "closepanel"):
            return {"ok": False, "error": "unknown command"}
        if line == "bag":
            return {"ok": True, "bag": [{"item": i, "inst": 0, "count": n} for i, n in self.stacks]}
        verb, item = line.split()[0], int(line.split()[1])
        if verb == "use" and item not in self.stuck and self.counts().get(item):
            self.take(item, 1)
            if item in LOOT.boxes and self.drops:
                self.give(*self.drops.pop(0))
        return {"ok": True}

    def set_auto(self, items):
        self.auto_calls.append(list(items))
        return [i for i in items if i != HEAL]  # HEAL: the user has a rule of their own

    def trip(self, want):
        self.trips.append(dict(want))
        stored = 0
        for item, n in want.items():
            k = min(n, self.store_room - stored)
            if k <= 0:
                break
            self.take(item, k)
            stored += k

        class R:
            ok = True
            detail = ""

        r = R()
        r.stored = stored
        return r


def tidy(game, supplies=frozenset(), store=True, auto=True):
    notes = []
    t = BoxTidy(
        cmd=game.cmd,
        wait=lambda s: None,
        clock=iter(range(10**6)).__next__,
        note=lambda phase, text: notes.append((phase, text)),
        step=lambda text: None,
        loot=LOOT,
        supplies=supplies,
        store_trip=game.trip if store else None,
        auto_use=game.set_auto if auto else None,
    )
    return t.run(), notes


# ---- the DB ------------------------------------------------------------------


@pytest.mark.skipif(not bundled("tthol.sqlite").exists(), reason="tthol.sqlite not pulled")
def test_the_db_sorts_box_loot_into_eat_and_store():
    loot = load_loot()
    assert loot.boxes == tuple(range(31617, 31624))  # 辰星 .. 颶星
    assert HELMET in loot.store  # 聖曦頭盔, opened live from a 歲星寶箱
    assert HELMET in loot.collect and not loot.collect - loot.store  # in the 蒐藏冊
    assert PILL in loot.potions
    assert 24852 not in loot.store and 24852 not in loot.potions  # 賞善蒼天庇祐: no_store
    assert loot.name(BOX_A) == "歲星寶箱"
    names = {loot.name(i) for i in loot.store}
    assert {"八星覺醒符", "三級禪宗棍法", "聖龍甲圖紙"} <= names
    assert not set(loot.boxes) & (loot.store | loot.potions)


# ---- pure parts --------------------------------------------------------------


def test_to_store_takes_only_box_loot():
    assert to_store({HELMET: 1, BOOK: 2, 99999: 5, PILL: 9}, LOOT) == {HELMET: 1, BOOK: 2}


def test_bag_view_counts_stacks_and_items():
    reply = {"ok": True, "bag": [{"item": 1, "count": 200}, {"item": 1, "count": 3}]}
    assert bag_view(reply) == (2, {1: 203})
    assert bag_view({"ok": False}) is None


# ---- a tidy ------------------------------------------------------------------


def test_opens_every_box_sets_potions_to_auto_use_and_stores_the_rest():
    game = FakeGame(
        [[BOX_A, 2], [BOX_B, 1], [PILL, 45], [HEAL, 9]],
        drops=[(HELMET, 1), (PILL, 10), (BOOK, 1)],
    )
    result, notes = tidy(game, supplies=frozenset())
    assert result.opened == {BOX_A: 2, BOX_B: 1}
    assert result.got == {HELMET: 1, PILL: 10, BOOK: 1}
    assert not any(line == f"use {PILL}" for line in game.sent)  # nothing eaten
    assert game.auto_calls == [[HEAL, PILL]] and result.auto == [PILL]
    assert ("confirmed", "行囊設成自動使用（守護戰鬥時用）：賞善寧心符") in notes
    assert game.trips == [{HELMET: 1, BOOK: 1}]
    assert result.stored == 2 and result.boxes_left == 0 and result.problem is None
    assert game.counts() == {PILL: 55, HEAL: 9}
    assert result.collected == {}  # no collection commands on this hook


def test_a_supply_potion_is_never_set_to_auto_use():
    game = FakeGame([[BOX_A, 1], [PILL, 5]], drops=[(HELMET, 1)])
    result, _notes = tidy(game, supplies=frozenset({PILL}))
    assert game.auto_calls == [] and result.auto == []


def test_a_full_bag_stores_and_opens_the_rest():
    filler = [[90000 + i, 1] for i in range(BAG_SLOTS - 2)]
    game = FakeGame(filler + [[BOX_A, 4]], drops=[(HELMET, 1)] * 4)
    result, notes = tidy(game)
    assert result.opened == {BOX_A: 4} and result.boxes_left == 0
    assert len(game.trips) >= 2  # stored between rounds
    assert result.problem is None
    assert any("背包滿了" in text for _p, text in notes)


def test_a_full_warehouse_stops_with_the_boxes_left():
    filler = [[90000 + i, 1] for i in range(BAG_SLOTS - 2)]
    game = FakeGame(filler + [[BOX_A, 3]], drops=[(HELMET, 1)] * 3, store_room=0)
    result, _notes = tidy(game)
    assert result.opened == {BOX_A: 1}  # the one free slot
    assert result.boxes_left == 2
    assert "還剩 2 個寶箱" in result.problem


def test_a_box_that_does_not_open_stops():
    game = FakeGame([[BOX_A, 2]], stuck={BOX_A})
    result, notes = tidy(game)
    assert result.opened == {} and result.boxes_left == 2
    assert "打不開" in result.problem
    assert ("error", "開歲星寶箱沒有反應") in notes


def test_without_a_supply_module_the_loot_stays():
    game = FakeGame([[BOX_A, 1]], drops=[(HELMET, 1)])
    result, notes = tidy(game, store=False)
    assert result.opened == {BOX_A: 1} and game.counts() == {HELMET: 1}
    assert ("error", "沒有補給模組，開出來的東西先留在背包") in notes


def test_even_the_last_box_waits_for_a_free_slot():
    filler = [[90000 + i, 1] for i in range(BAG_SLOTS - 1)]
    game = FakeGame(filler + [[BOX_A, 1]], drops=[(HELMET, 1)])
    result, _notes = tidy(game, store=False)
    assert "use 31620" not in game.sent
    assert result.opened == {} and result.boxes_left == 1
    assert "背包滿了" in result.problem


class LateLootGame(FakeGame):
    """The box count drops one bag read before its loot shows."""

    late = None
    reads = 0

    def cmd(self, line):
        if line.startswith("use") and int(line.split()[1]) in LOOT.boxes:
            self.sent.append(line)
            self.take(int(line.split()[1]), 1)
            self.late, self.reads = self.drops.pop(0), 0
            return {"ok": True}
        if line == "bag" and self.late:
            if self.reads >= 1:  # the second read after the use
                self.give(*self.late)
                self.late = None
            self.reads += 1
        return super().cmd(line)


def test_loot_that_lands_a_read_later_is_still_named():
    game = LateLootGame([[BOX_A, 1]], drops=[(HELMET, 1)])
    result, notes = tidy(game, store=False)
    assert result.got == {HELMET: 1}
    assert ("confirmed", "開歲星寶箱：聖曦頭盔 ×1") in notes


class BookGame(FakeGame):
    """FakeGame with the hook's 蒐藏冊 commands. `fresh`: just logged in, the
    read has the count but no ids until every group is asked for."""

    def __init__(self, *a, have=(), fresh=False, **kw):
        super().__init__(*a, **kw)
        self.have = set(have)
        self.asked = set()
        self.fresh = fresh
        self.window = False

    def cmd(self, line):
        verb, *args = line.split()
        if verb == "collection":
            self.sent.append(line)
            shown = self.have if not self.fresh or self.asked >= {100, 200} else set()
            return {
                "ok": True,
                "count": len(self.have),
                "groups": [100, 200],
                "collected": sorted(shown),
            }
        if verb == "collectpage":
            self.sent.append(line)
            self.asked.add(int(args[0]))
            return {"ok": True, "group": int(args[0]), "window": False}
        if verb == "openpanel":
            self.sent.append(line)
            self.window = True
            return {"ok": True, "opened": True, "open": True}
        if verb == "closepanel":
            self.sent.append(line)
            self.window = False
            return {"ok": True}
        if verb == "collect":
            self.sent.append(line)
            item = int(args[0])
            assert self.window, "collect with the window closed crashes the client"
            if item in self.have:
                return {"ok": False, "error": "already collected"}
            self.take(item, 1)
            self.have.add(item)
            return {"ok": True, "item": item}
        return super().cmd(line)


def test_box_gear_not_in_the_book_is_collected_once_and_the_rest_stored():
    game = BookGame([[BOX_A, 2]], drops=[(HELMET, 1), (HELMET, 1)])
    result, notes = tidy(game)
    assert result.collected == {HELMET: 1}
    assert game.trips == [{HELMET: 1}]  # the second one is stored
    assert ("confirmed", "蒐藏 聖曦頭盔") in notes
    assert game.sent.index("openpanel collection") < game.sent.index(f"collect {HELMET}")
    assert game.sent[game.sent.index(f"collect {HELMET}") + 1 :].count("closepanel collection") >= 1
    assert not game.window


def test_gear_already_in_the_book_is_just_stored():
    game = BookGame([[BOX_A, 1]], drops=[(HELMET, 1)], have={HELMET})
    result, _notes = tidy(game)
    assert result.collected == {} and game.trips == [{HELMET: 1}]
    assert "openpanel collection" not in game.sent


def test_right_after_login_every_group_is_asked_for_first():
    game = BookGame([[BOX_A, 1]], drops=[(HELMET, 1)], have={HELMET}, fresh=True)
    result, _notes = tidy(game)
    assert {"collectpage 100", "collectpage 200"} <= set(game.sent)
    assert result.collected == {}  # it was in the book after all
