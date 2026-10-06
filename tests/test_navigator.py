import math
import threading

import pytest

from services import route_plan as rp
from services._paths import bundled
from services.navigator import Navigator, _Replan, _Run, read_level

pytestmark = pytest.mark.skipif(
    not bundled("tthol.sqlite").exists(), reason="tthol.sqlite not pulled"
)

CHENGDU = 53
BANK_CLERK = (21, 17)  # 成都少城 錢莊伙計: a room behind a door


class FakeGame:
    """A client on one map: `walk` moves 2 tiles per `near`, zones teleport."""

    def __init__(self, stage, pos, zones, objects=(), touch_zones=None):
        self.stage, self.pos = stage, pos
        self.target = None
        self.zones = zones  # zone tile -> landing tile (step on)
        self.objects = list(objects)  # map objects for `objects`
        self.touch_zones = touch_zones or {}  # object h -> landing tile
        self.sent = []

    def send(self, pid, line):
        self.sent.append(line)
        cmd, *args = line.split()
        if cmd == "status":
            return {"ok": True, "self": 1, "hp": [100, 100], "tile": [-1, -1]}
        if cmd == "near":
            self._step()
            return {
                "ok": True,
                "objects": [{"h": 1, "id": 60004, "x": self.pos[0] * 40, "y": self.pos[1] * 40}],
            }
        if cmd == "walk":
            self.target = (int(args[0]) // 40, int(args[1]) // 40)
            return {"ok": True, "path": True}
        if cmd == "objects":
            return {"ok": True, "objects": self.objects}
        if cmd == "touch":
            self.pos, self.target = self.touch_zones[int(args[0])], None
            return {"ok": True}
        return {"ok": False}

    def _step(self):
        if self.target is None:
            return
        x, y = self.pos
        tx, ty = self.target
        self.pos = (x + max(-2, min(2, tx - x)), y + max(-2, min(2, ty - y)))
        for zone, land in self.zones.items():
            if max(abs(self.pos[0] - zone[0]), abs(self.pos[1] - zone[1])) <= 1:
                self.pos, self.target = land, None
                return


def nav_for(game, level=120, manor=1121):
    def read_locked(pid, fn):
        if fn is read_level:
            return level
        return (game.stage, "x")

    return Navigator(
        game, read_locked, lambda *a: None, manor=lambda pid: manor, sleep=lambda ev, s: False
    )


def door_into_bank():
    t = rp._tables()
    hops = rp.room_doors("stage", CHENGDU, (47, 168), BANK_CLERK)
    ((zone, _touch),) = hops
    return zone, t


def test_walks_through_the_bank_door_onto_the_goal():
    zone, _t = door_into_bank()
    game = FakeGame(CHENGDU, (47, 168), {zone: (12, 13)})
    result = nav_for(game).go(7, CHENGDU, BANK_CLERK)
    assert result.ok, result
    assert math.dist(result.tile, BANK_CLERK) <= 2
    assert any(line.startswith("walk") for line in game.sent)


def test_already_there_sends_no_walk():
    game = FakeGame(CHENGDU, BANK_CLERK, {})
    result = nav_for(game).go(7, CHENGDU, BANK_CLERK)
    assert result.ok
    assert not [line for line in game.sent if line.startswith("walk")]


def test_click_zone_uses_touch():
    # 清音瀑布: the crane statue (tag 258) is a click zone back to the 天靈道院 side.
    t = rp._tables()
    statue = t.middle(202, "arrival", 258)
    landing = t.middle(202, "trigger", 101)
    obj = {"h": 9, "id": 101, "inst": 257, "x": statue[0] * 40, "y": statue[1] * 40}
    game = FakeGame(202, (44, 96), {}, objects=[obj], touch_zones={9: landing})
    nav = nav_for(game)
    run = __import__("services.navigator", fromlist=["_Run"])._Run(nav, 7, None, lambda _t: None)
    assert run.touch_zone(statue, 202) is True
    assert "touch 9" in game.sent
    assert game.pos == landing


def test_stop_ends_the_walk():
    stop = threading.Event()
    stop.set()
    game = FakeGame(CHENGDU, (47, 168), {})
    nav = Navigator(
        game,
        lambda pid, fn: 120 if fn is read_level else (CHENGDU, "x"),
        lambda *a: None,
        manor=lambda pid: None,
        sleep=lambda ev, s: ev.is_set(),
    )
    result = nav.go(7, CHENGDU, BANK_CLERK, stop=stop)
    assert (result.ok, result.reason) == (False, "stopped")


def test_no_route_is_reported():
    game = FakeGame(CHENGDU, (47, 168), {})
    result = nav_for(game).go(7, 999_999)
    assert (result.ok, result.reason) == (False, "no-route")


def test_a_door_on_the_way_to_an_npc_replans():
    # Walking to the 成都車伕, a zone on the way puts us in the bank room.
    game = FakeGame(CHENGDU, (47, 168), {(45, 168): (12, 13)})
    run = _Run(nav_for(game), 7, None, lambda _t: None)
    with pytest.raises(_Replan):
        run.ride(6024, (25, 169), 1, CHENGDU)
    assert not [line for line in game.sent if line.startswith("talk")]


def test_a_warp_on_the_way_to_an_npc_replans():
    game = FakeGame(CHENGDU, (47, 168), {})
    orig = game._step

    def step():
        orig()
        if game.target:
            game.stage = 51  # stepped on an exit on the way

    game._step = step
    run = _Run(nav_for(game), 7, None, lambda _t: None)
    with pytest.raises(_Replan):
        run.ride(6024, (25, 169), 1, CHENGDU)


def test_inside_a_manor_without_family_info_rides_its_horse():
    # 地英莊 (1054), the family's 0x31 not in yet: the map tells the manor.
    from services.tower import LOBBY_STAGE

    game = FakeGame(1054, (36, 19), {})
    notes = []
    result = nav_for(game, level=150, manor=None).go(7, LOBBY_STAGE, note=notes.append)
    # The fake has no NPCs: getting as far as looking for the 家族馬夫 is the point.
    assert result.reason == "stuck" and "6347" in result.detail
    assert any("先送到探幽曲徑" in n for n in notes)


def test_the_target_is_clicked_again_every_few_seconds_while_walking():
    game = FakeGame(CHENGDU, (47, 168), {})
    clock = {"t": 0.0}

    def sleep(ev, secs):
        clock["t"] += secs
        return False

    class Rng:
        def uniform(self, a, b):
            return 3.0

    nav = Navigator(
        game,
        lambda pid, fn: 120 if fn is read_level else (CHENGDU, "x"),
        lambda *a: None,
        manor=lambda pid: None,
        clock=lambda: clock["t"],
        sleep=sleep,
        rng=Rng(),
    )
    run = _Run(nav, 7, None, lambda _t: None)
    assert run.walk_to((47, 140), CHENGDU) == "arrived"  # 28 tiles at 2 a poll: ~5.6 s
    walks = [line for line in game.sent if line.startswith("walk")]
    assert len(walks) >= 2 and len(set(walks)) == 1  # the same target, clicked again


class FastGame(FakeGame):
    """A character with a speed buff: 6 tiles a poll."""

    def _step(self):
        if self.target is None:
            return
        x, y = self.pos
        tx, ty = self.target
        self.pos = (x + max(-6, min(6, tx - x)), y + max(-6, min(6, ty - y)))


def test_a_fast_walk_is_not_taken_for_a_teleport():
    game = FastGame(CHENGDU, (47, 168), {})
    run = _Run(nav_for(game), 7, None, lambda _t: None)
    assert run.walk_to((47, 120), CHENGDU) == "arrived"


def _asking_nav(game, reply, arrives_after=None):
    """manor unknown until `arrives_after` seconds past the `family` ask."""
    clock = {"t": 0.0}
    asked = []

    def ask(pid):
        asked.append(clock["t"])
        return reply

    def manor(pid):
        if not asked or arrives_after is None or clock["t"] - asked[0] < arrives_after:
            return None
        return 1121

    def sleep(ev, secs):
        clock["t"] += secs
        return False

    nav = Navigator(
        game,
        lambda pid, fn: 120 if fn is read_level else (CHENGDU, "x"),
        lambda *a: None,
        manor=manor,
        clock=lambda: clock["t"],
        sleep=sleep,
        ask_family=ask,
    )
    return nav, asked, clock


def test_an_unknown_manor_is_asked_for_before_planning():
    game = FakeGame(CHENGDU, (47, 168), {})
    nav, asked, clock = _asking_nav(game, {"ok": True}, arrives_after=0.3)
    run = _Run(nav, 7, None, lambda _t: None)
    run.learn_manor()
    assert asked == [0.0] and run.manor == 1121 and run.script.manor == 1121


def test_no_family_reply_does_not_wait():
    game = FakeGame(CHENGDU, (47, 168), {})
    nav, asked, clock = _asking_nav(game, {"ok": False, "error": "no family"})
    run = _Run(nav, 7, None, lambda _t: None)
    run.learn_manor()
    assert asked and run.manor is None and clock["t"] == 0.0


def test_a_missing_0x31_gives_up_after_the_wait():
    game = FakeGame(CHENGDU, (47, 168), {})
    nav, asked, clock = _asking_nav(game, {"ok": True})
    run = _Run(nav, 7, None, lambda _t: None)
    run.learn_manor()
    assert run.manor is None and 2.0 <= clock["t"] < 2.5


class LoadBlipGame(FakeGame):
    """`status` reads HP 0 a few times, like a map load."""

    def __init__(self, *a, zeros=2, **kw):
        super().__init__(*a, **kw)
        self.zeros = zeros

    def send(self, pid, line):
        if line == "status" and self.zeros != 0:
            self.zeros -= 1
            self.sent.append(line)
            return {"ok": True, "self": 1, "hp": [0, 100], "tile": [-1, -1]}
        return super().send(pid, line)


def _clocked_nav(game):
    clock = {"t": 0.0}

    def sleep(ev, secs):
        clock["t"] += secs
        return False

    nav = Navigator(
        game,
        lambda pid, fn: 120 if fn is read_level else (CHENGDU, "x"),
        lambda *a: None,
        manor=lambda pid: 1121,
        clock=lambda: clock["t"],
        sleep=sleep,
    )
    return nav


def test_a_moment_of_zero_hp_is_not_a_death():
    game = LoadBlipGame(CHENGDU, (47, 168), {}, zeros=2)
    run = _Run(_clocked_nav(game), 7, None, lambda _t: None)
    assert run.walk_to((47, 150), CHENGDU) == "arrived"


def test_zero_hp_that_stays_is_a_death():
    from services.navigator import _Stop

    game = LoadBlipGame(CHENGDU, (47, 168), {}, zeros=-1)  # never comes back
    run = _Run(_clocked_nav(game), 7, None, lambda _t: None)
    with pytest.raises(_Stop) as e:
        run.walk_to((47, 150), CHENGDU)
    assert e.value.reason == "dead"
