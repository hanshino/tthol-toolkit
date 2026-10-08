import pytest

from services.api_types import CombatRule, GrindConfig, GuardConfig, GuardPotionRule
from services.combat import COMBAT_SECTION, AttackSkill
from services.grind import SECTION, GrindManager, _Done, _Run, fair_game
from services.guard import GuardStore, read_learned, read_stage_id
from services.nearby import read_nearby
from reader import NearbyObject

NPCS = {
    9001: ("●野狼", 30, True),
    9002: ("●山豬", 32, True),
    5594: ("毒氣", 45, False),  # hazard: not fair game
    6300: ("看守人", None, False),
    8062: ("●小水母", 20, True),  # a summon when it carries an owner tag
}
DEFS = {(751, 10): AttackSkill("劍盪千秋", 20, False, 400)}


def mob(h, nid, inst, tile, dead=0):
    return {
        "h": h,
        "kind": 11,
        "id": nid,
        "inst": inst,
        "x": tile[0] * 40,
        "y": tile[1] * 40,
        "dead": dead,
    }


class FakeGame:
    def __init__(self):
        self.sent = []
        self.tile = (50, 50)
        self.hp = 100
        self.stage = 12
        self.mobs = []
        self.followers = []  # NearbyObject with a tag
        self.caps = ["status", "near", "walk", "attack", "cast"]

    def send(self, pid, line, priority=0):
        self.sent.append(line)
        cmd = line.split()[0]
        if cmd == "caps":
            return {"ok": True, "commands": [{"cmd": c} for c in self.caps]}
        if cmd == "status":
            return {
                "ok": True,
                "self": 1,
                "hp": [self.hp, 100],
                "mp": [500, 500],
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
            return {"ok": True, "objects": [me, *self.mobs]}
        return {"ok": True, "path": True}


class FakeGuard:
    def __init__(self):
        self.started = False

    def running(self, pid):
        return self.started

    def start(self, pid):
        self.started = True
        return type("R", (), {"ok": True, "reason": None})()

    def cast_count(self, pid):
        return 0


def make(combat=None, config=None, hp_items=(24007,), busy=lambda pid: None):
    game = FakeGame()
    store = GuardStore()
    store.save("寒江孤影", GuardConfig(potion=GuardPotionRule(hp_items=list(hp_items))))
    store.save_section("寒江孤影", COMBAT_SECTION, combat or CombatRule(basic=True))
    store.save_section("寒江孤影", SECTION, config or GrindConfig(radius=5))
    clock = {"t": 0.0}

    def read_locked(pid, fn):
        if fn is read_stage_id:
            return (game.stage, "某地圖")
        if fn is read_learned:
            return {751: 10}
        if fn is read_nearby:
            return list(game.followers), game.tile
        raise AssertionError(fn)

    def wait(ev, secs):
        clock["t"] += secs
        return ev.is_set()

    guard = FakeGuard()
    mgr = GrindManager(
        guard=guard,
        read_locked=read_locked,
        character_name=lambda pid: "寒江孤影",
        channel=game,
        store=store,
        attack_skills=lambda: DEFS,
        npcs=lambda: NPCS,
        clock=lambda: clock["t"],
        wall=lambda: 1000.0 + clock["t"],
        wait=wait,
        busy=busy,
    )
    run = _Run(
        "寒江孤影",
        store.load_section("寒江孤影", COMBAT_SECTION, CombatRule),
        store.load_section("寒江孤影", SECTION, GrindConfig),
    )
    run.pid = 1
    with mgr._lock:
        mgr._runs[1] = run
    return mgr, run, game, guard, clock


def test_fair_game_skips_players_followers_hazards_dead_and_far():
    objs = [
        mob(2, 9001, 1, (52, 50)),
        mob(3, 9001, 2, (52, 50), dead=1),
        mob(4, 5594, 1, (51, 50)),
        mob(5, 60003, 1, (51, 50)),
        mob(6, 8062, 7, (51, 50)),
        mob(7, 9002, 1, (70, 50)),
        mob(8, 6300, 1, (50, 51)),
    ]
    live = fair_game(objs, NPCS, {(8062, 7)}, (50, 50), 5, [])
    assert [o["h"] for o in live] == [2]
    only = fair_game(
        [mob(2, 9001, 1, (52, 50)), mob(9, 9002, 1, (53, 50))], NPCS, set(), (50, 50), 5, [9002]
    )
    assert [o["h"] for o in only] == [9]


def test_start_refuses_without_an_attack_or_when_busy():
    mgr, *_ = make(combat=CombatRule(basic=False))
    mgr._runs.clear()
    assert mgr.start(1) == (False, "還沒設定攻擊方式：勾普攻，或選至少一個技能")
    mgr, *_ = make(busy=lambda pid: "這隻角色正在登塔")
    mgr._runs.clear()
    assert mgr.start(1) == (False, "這隻角色正在登塔")


def test_start_runs_without_potions_but_warns_and_starts_the_guard():
    mgr, _, _, guard, _ = make(hp_items=())
    mgr._runs.clear()
    mgr._sleep = lambda ev, secs: True  # the thread stops at its first wait
    ok, _ = mgr.start(1)
    assert ok and guard.started
    mgr._runs[1].thread.join(2)
    assert "白名單" in mgr.status(1).warning


def test_attacks_a_monster_in_range_and_counts_the_kill_once():
    mgr, run, game, _, _ = make()
    game.mobs = [mob(2, 9001, 1, (52, 50))]
    mgr._tick(1, run)
    assert "attack 2" in game.sent
    assert run.anchor == (50, 50)
    run.fighter.hits[(9001, 1)] = 0.0  # our 0x43 landed
    game.mobs = [mob(2, 9001, 1, (52, 50), dead=1)]
    mgr._tick(1, run)
    mgr._tick(1, run)
    assert run.kills == 1
    game.mobs = []  # the body faded; a respawn may reuse the key
    mgr._tick(1, run)
    assert run.counted == set()


def test_someone_elses_kill_does_not_count():
    mgr, run, game, _, _ = make()
    game.mobs = [mob(2, 9001, 1, (52, 50), dead=1)]
    mgr._tick(1, run)
    assert run.kills == 0


def test_a_summon_is_left_alone():
    mgr, run, game, _, _ = make()
    game.mobs = [mob(6, 8062, 7, (51, 50))]
    game.followers = [
        NearbyObject(6, 8062, 7, 51 * 40, 50 * 40, "～小水母～", 0, 100, "寒江孤影", False)
    ]
    mgr._tick(1, run)
    assert not [line for line in game.sent if line.startswith(("attack", "cast"))]


def test_walks_back_to_the_spot_when_idle_and_far():
    mgr, run, game, _, clock = make()
    mgr._tick(1, run)  # anchors at (50, 50)
    game.tile = (60, 50)
    mgr._tick(1, run)
    assert game.sent[-1] == "walk 2020 2020"
    mgr._tick(1, run)
    assert game.sent[-1] != "walk 2020 2020"  # not resent at once
    clock["t"] += 2.0
    mgr._tick(1, run)
    assert game.sent[-1] == "walk 2020 2020"


def test_stops_on_a_death_and_on_a_map_change():
    mgr, run, game, _, clock = make()
    mgr._tick(1, run)
    game.stage = 13
    with pytest.raises(_Done, match="換到別張地圖"):
        mgr._tick(1, run)
    mgr, run, game, _, clock = make()
    game.hp = 0
    mgr._tick(1, run)
    clock["t"] += 3.0
    with pytest.raises(_Done, match="角色死亡"):
        mgr._tick(1, run)
