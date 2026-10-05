import math
import threading

import pytest

from services import route_plan as rp
from services._paths import bundled
from services.navigator import Navigator, read_level

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
