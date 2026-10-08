"""DungeonManager on a small made-up dungeon: an entrance map (100) and one
dungeon map (42) split into a left and a right area by a wall."""

from contextlib import contextmanager
from dataclasses import replace

import pytest

from services.api_types import (
    CombatRule,
    DungeonConfig,
    DungeonRecord,
    GuardConfig,
    GuardPotionRule,
)
from services.combat import COMBAT_SECTION
from services.dungeon import DungeonManager, _Done, _Moved, _Run
from services.dungeon_scripts import Area, Clear, Dungeon, Enter, Shortcut, Talk
from services.guard import GuardStore, read_holdings, read_learned, read_stage_id
from services.map_areas import MapAreas, Portal, _components
from services.nearby import read_nearby

CHEST = 27229
INVESTIGATOR = 7189
ACCEPT, REFUSE = 6653, 6621


def _map(stage, width, height, wall_cols=()):
    mask = "".join("0" if x in wall_cols else "1" for _row in range(height) for x in range(width))
    return MapAreas("stage", stage, width, height, _components(width, height, mask))


def build_maps():
    entrance = _map(100, 10, 10)
    entrance.npcs = {INVESTIGATOR: [(3, 3)]}
    entrance.landings = {1: [(6, 6)]}
    entrance.portals = {
        257: Portal(257, 0, ((5, 5),), frozenset(), frozenset(), True, 42, 1),
    }
    inside = _map(42, 20, 10, wall_cols=(9, 10))
    inside.landings = {1: [(2, 5)], 2: [(15, 5)]}
    left, right = inside.area_of((2, 5)), inside.area_of((15, 5))
    inside.group_area = {1: left, 2: right}
    inside.group_npcs = {1: frozenset({9001}), 2: frozenset({9002})}
    inside.group_tiles = {1: [(4, 5)], 2: [(16, 5)]}
    inside.group_count = {1: 2, 2: 1}
    inside.portals = {
        10: Portal(10, left, ((7, 5),), frozenset({1}), frozenset(), False, None, 2),
        20: Portal(20, right, ((18, 5),), frozenset({2}), frozenset(), True, 100, 1),
    }
    return {100: entrance, 42: inside}


SCRIPT = Dungeon(
    key="lingxiao",
    title="測試閣",
    cooldown_h=4,
    entry_stage=100,
    entry=(Talk(INVESTIGATOR, (ACCEPT,), mission=True, optional=True), Enter(257)),
    areas=(
        Area("左廳", 42, 1, (Shortcut(10), Clear((1,)), Enter(10)), floor="一層"),
        Area("右廳", 42, 2, (Clear((2,)), Enter(20)), floor="二層"),
    ),
    finish=(Talk(INVESTIGATOR, (), mission=True, optional=True),),
    last_exit=20,
    floors=("一層", "二層"),
    chests=(CHEST,),
)


class FakeGame:
    """Walks are instant; a walk onto a portal zone whose groups are dead teleports."""

    def __init__(self, maps):
        self.maps = maps
        self.stage = 100
        self.tile = (6, 6)
        self.hp = 100
        self.sent = []
        self.mobs = {}  # handle -> object
        self.dialog = None  # {"options": [...]} while open
        self.chose = []
        self.bag = {}
        self.fade = {}  # handle -> near calls left for a body

    def add_mob(self, h, npc, inst, tile):
        self.mobs[h] = {
            "h": h,
            "kind": 11,
            "id": npc,
            "inst": inst,
            "x": tile[0] * 40,
            "y": tile[1] * 40,
            "dead": 0,
        }

    def alive(self, group):
        ids = self.maps[42].group_npcs[group]
        return any(m["id"] in ids and not m["dead"] for m in self.mobs.values())

    def send(self, pid, line, priority=0):
        self.sent.append(line)
        assert len(self.sent) < 20000, "runaway loop"
        cmd, *args = line.split()
        if cmd == "caps":
            return {
                "ok": True,
                "commands": [
                    {"cmd": c}
                    for c in (
                        "status",
                        "near",
                        "walk",
                        "attack",
                        "cast",
                        "talk",
                        "dialog",
                        "option",
                        "next",
                    )
                ],
            }
        if cmd == "status":
            return {
                "ok": True,
                "self": 1,
                "hp": [self.hp, 100],
                "mp": [50, 50],
                "tile": list(self.tile),
            }
        if cmd == "near":
            me = {
                "h": 1,
                "kind": 10,
                "id": 60004,
                "inst": 1,
                "x": self.tile[0] * 40,
                "y": self.tile[1] * 40,
                "dead": 0,
            }
            objs = [me]
            if self.stage == 100:
                objs.append(
                    {
                        "h": 50,
                        "kind": 3,
                        "id": INVESTIGATOR,
                        "inst": 1,
                        "x": 3 * 40,
                        "y": 3 * 40,
                        "dead": 0,
                    }
                )
            if self.stage == 42:
                for h, m in list(self.mobs.items()):
                    objs.append(dict(m))
                    if m["dead"]:
                        self.fade[h] = self.fade.get(h, 2) - 1
                        if self.fade[h] <= 0:
                            del self.mobs[h]
            return {"ok": True, "objects": objs}
        if cmd == "walk":
            self.tile = (int(args[0]) // 40, int(args[1]) // 40)
            self._portal()
            return {"ok": True, "path": True}
        if cmd == "attack":
            m = self.mobs.get(int(args[0]))
            if m is not None:
                m["dead"] = 1
            return {"ok": True}
        if cmd == "talk":
            self.dialog = {"options": [ACCEPT, REFUSE]} if not self.chose else {"options": []}
            return {"ok": True}
        if cmd == "dialog":
            if self.dialog is None:
                return {"ok": True, "open": False}
            return {"ok": True, "open": True, "options": self.dialog["options"]}
        if cmd == "option":
            self.chose.append(self.dialog["options"][int(args[0])])
            self.dialog = None
            return {"ok": True}
        if cmd == "next":
            self.dialog = None
            return {"ok": True}
        return {"ok": True}

    def _portal(self):
        for p in self.maps[self.stage].portals.values():
            if self.tile in p.zone and not any(self.alive(g) for g in p.dead):
                to = p.to_stage or self.stage
                self.tile = self.maps[to].landings[p.to_tag][0]
                if p.event == 20:
                    self.bag[CHEST] = self.bag.get(CHEST, 0) + 1
                self.stage = to
                return


class FakeGuard:
    def running(self, pid):
        return True

    def cast_count(self, pid):
        return 0

    @contextmanager
    def quiet(self, pid):
        yield


class FakeNavigator:
    def __init__(self, game):
        self.game = game

    def go(self, pid, dest, goal=None, stop=None, note=None):
        self.game.stage, self.game.tile = dest, goal or (6, 6)
        return type("R", (), {"ok": True, "reason": ""})()


def make(game=None, combat=None, config=None, script=SCRIPT, maps=None):
    maps = maps or build_maps()
    game = game or FakeGame(maps)
    store = GuardStore()
    store.save("寒江孤影", GuardConfig(potion=GuardPotionRule(hp_items=[24007])))
    store.save_section("寒江孤影", COMBAT_SECTION, combat or CombatRule(basic=True))
    clock = {"t": 0.0}

    def read_locked(pid, fn):
        if fn is read_stage_id:
            return (game.stage, f"#{game.stage}")
        if fn is read_learned:
            return {}
        if fn is read_holdings:
            return dict(game.bag), {}
        if fn is read_nearby:
            return [], game.tile
        raise AssertionError(fn)

    def wait(ev, secs):
        clock["t"] += secs
        return ev.is_set()

    mgr = DungeonManager(
        script,
        guard=FakeGuard(),
        read_locked=read_locked,
        character_name=lambda pid: "寒江孤影",
        channel=game,
        store=store,
        navigator=FakeNavigator(game),
        attack_skills=lambda: {},
        areas=lambda kind, stage: maps.get(stage),
        clock=lambda: clock["t"],
        wall=lambda: 1_000_000.0 + clock["t"],
        wait=wait,
    )
    run = _Run("寒江孤影", combat or CombatRule(basic=True), config or DungeonConfig())
    run.pid = 1
    with mgr._lock:
        mgr._runs[1] = run
    return mgr, run, game, store


def record(store):
    return store.load_section("寒江孤影", "dungeon.lingxiao.record", DungeonRecord)


def test_a_full_run_from_another_map_to_the_report():
    mgr, run, game, store = make()
    game.stage, game.tile = 5, (1, 1)  # somewhere else
    game.add_mob(31, 9001, 1, (4, 5))
    game.add_mob(32, 9001, 2, (5, 6))
    game.add_mob(33, 9002, 1, (16, 5))
    mgr._loop(1, run)
    assert run.outcome == "done", run.ended
    assert game.chose[0] == ACCEPT  # took the mission on the way in
    assert {"attack 31", "attack 32", "attack 33"} <= set(game.sent)
    rec = record(store)
    assert rec.done and rec.chests == 1 and rec.entered_at is not None
    names = [line.text for line in run.log]
    assert "到了左廳" in names and "到了右廳" in names


def test_an_exit_someone_else_opened_is_taken_without_a_fight():
    mgr, run, game, _ = make()
    game.stage, game.tile = 42, (2, 5)  # left area, its group already dead
    game.add_mob(33, 9002, 1, (16, 5))
    with pytest.raises(_Moved):
        mgr._tick(1, run)  # the shortcut moved us
    assert game.tile == (15, 5)
    assert not [line for line in game.sent if line.startswith("attack")]


def test_without_the_mission_setting_the_investigator_is_skipped():
    mgr, run, game, _ = make(config=DungeonConfig(mission=False))
    game.add_mob(33, 9002, 1, (16, 5))
    mgr._loop(1, run)
    assert run.outcome == "done" and game.chose == []


def test_a_death_stops_the_run():
    mgr, run, game, _ = make()
    game.stage, game.tile, game.hp = 42, (2, 5), 0
    game.add_mob(31, 9001, 1, (4, 5))
    mgr._loop(1, run)
    assert run.outcome == "error" and run.ended == "角色死亡"


def test_leaving_the_dungeon_maps_midway_is_an_error():
    mgr, run, game, _ = make()
    run.inside = True
    game.stage = 7
    with pytest.raises(_Done, match="離開副本"):
        mgr._tick(1, run)


def test_start_waits_out_the_cooldown_only_outside():
    mgr, _, game, store = make()
    mgr._runs.clear()
    store.save_section(
        "寒江孤影", "dungeon.lingxiao.record", DungeonRecord(entered_at=1_000_000.0 - 600)
    )
    ok, reason = mgr.start(1)
    assert not ok and "冷卻" in reason
    game.stage = 42  # stopped mid-run: a restart is fine
    mgr._sleep = lambda ev, secs: True
    ok, _ = mgr.start(1)
    assert ok
    mgr._runs[1].thread.join(2)


def test_the_last_exit_through_a_shortcut_finishes_the_run():
    # The boss is already dead (someone else): the right hall's shortcut leaves.
    right = Area("右廳", 42, 2, (Shortcut(20), Clear((2,)), Enter(20)), floor="二層")
    script = replace(SCRIPT, areas=(SCRIPT.areas[0], right))
    maps = build_maps()
    # A map hazard (never dies) that the DB places in the right hall.
    maps[42].group_area[0] = maps[42].area_of((15, 5))
    maps[42].group_npcs[0] = frozenset({5594})
    mgr, run, game, store = make(script=script, maps=maps)
    game.stage, game.tile = 42, (15, 5)
    game.add_mob(90, 5594, 1, (13, 6))
    run.inside = True
    mgr._loop(1, run)
    assert run.outcome == "done", run.ended
    assert record(store).chests == 1
    assert not [line for line in game.sent if line.startswith("attack")]
    assert "walk 660 220" not in game.sent  # no sweep of the boss spawn: the shortcut went first


def test_a_restart_inside_keeps_the_entry_time():
    mgr, run, game, store = make()
    store.save_section("寒江孤影", "dungeon.lingxiao.record", DungeonRecord(entered_at=123.0))
    game.stage, game.tile = 42, (15, 5)  # started again in the right hall
    game.add_mob(33, 9002, 1, (16, 5))
    mgr._loop(1, run)
    assert run.outcome == "done", run.ended
    assert record(store).entered_at == 123.0
