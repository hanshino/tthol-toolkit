from services.api_types import CombatRule, GuardConfig, GuardPotionRule, TowerConfig
from services.combat import AttackSkill, Rotation, cast_sent, next_skill, retarget
from services.guard import GuardStore, read_holdings, read_learned, read_stage_id
from services.navigator import NavResult
from services.tower import LEAVE, LOBBY_STAGE, ROOMS, YAN, TowerStage
from services.tower_run import COMBAT_SECTION, TOWER_SECTION, TowerManager, _Done, _Run

import pytest

# ---- combat rotation (pure) ------------------------------------------------------

DEFS = {
    (751, 10): AttackSkill("劍盪千秋", 20, False, 400),
    (754, 15): AttackSkill("瞬影斬", 24, False, 0),
    (304, 3): AttackSkill("天責", 500, True, 0),
}
LEARNED = {751: 10, 754: 15, 304: 3}


def test_opener_once_then_rotation_skipping_unusable_slots():
    rule = CombatRule(opener=751, rotation=[754, 304, 999])
    rot = Rotation()
    retarget(rot, 7, 0.0)
    got = []
    t = 0.0
    for _ in range(4):
        pick = next_skill(rule, rot, LEARNED, DEFS, mp=100, now=t)
        got.append(pick[0] if pick else None)
        if pick:
            cast_sent(rot, pick[0], t)
        t += 1.0
    # 304 costs 500 MP and 999 is not learned: both skipped, 754 every turn
    assert got == [751, 754, 754, 754]
    retarget(rot, 8, t)  # a new target opens again
    assert next_skill(rule, rot, LEARNED, DEFS, 100, t)[0] == 751


def test_skill_gap_is_recharge_plus_stun_at_least_the_puppet_gap():
    rule = CombatRule(rotation=[751, 754])
    rot = Rotation()
    pick = next_skill(rule, rot, LEARNED, DEFS, 100, 0.0)
    cast_sent(rot, pick[0], 0.0)
    assert next_skill(rule, rot, LEARNED, DEFS, 100, 0.39) is None
    pick = next_skill(rule, rot, LEARNED, DEFS, 100, 0.45)  # 400 ms < 450 ms floor
    assert pick[0] == 754 and not rot.attacked


# ---- the runner against a fake game ---------------------------------------------------


def make_tower(stage_id=1704):
    starts = {r: (r * 100, 10) for r in range(1, ROOMS + 1)}
    exits = {r: [(r * 100 + 20, 10), (r * 100 + 21, 10), (r * 100 + 20, 11)] for r in starts}
    return TowerStage(
        stage_id=stage_id,
        starts=starts,
        exits=exits,
        monsters={r: frozenset({9000 + r}) for r in starts},
        expect={r: 1 for r in starts},
        spawns={r: [(r * 100 + 5, 12)] for r in starts},
        fox=(500, 500),
    )


class FakeGame:
    """Enough of the hook to clear rooms: a hit kills, bodies fade after 2 looks."""

    def __init__(self, stage=1704, room=1, exit_options=(LEAVE, 65876)):
        self.stage = stage
        self.tile = (room * 100, 10)
        self.room = room
        self.mob_dead = False
        self.fade = 2
        self.dialog_open = False
        self.exit_options = list(exit_options)
        self.sent = []
        self.hp = 100
        self.fox_visible = True
        self.stale = None  # status's tile after a teleport, until the next walk
        self.bag, self.petbag = {24007: 500}, {24007: 0}
        self.left = False
        self.caps = ["status", "near", "walk", "attack", "cast", "talk", "dialog", "option", "next"]
        self.fox_dialog = False

    def leave(self):
        self.left = True  # the Esc menu click: the own character is gone
        return True

    def mob(self):
        return {
            "h": 50 + self.room,
            "kind": 2,
            "id": 9000 + self.room,
            "inst": 1,
            "x": (self.room * 100 + 3) * 40,
            "y": 400,
            "dead": int(self.mob_dead),
        }

    def send(self, pid, line, priority=0):
        self.sent.append(line)
        cmd, *args = line.split()
        if cmd == "caps":
            return {
                "ok": True,
                "commands": [{"cmd": c} for c in self.caps],
            }
        if cmd == "status":
            if self.left:
                return {"ok": False, "error": "own character not found"}
            return {
                "ok": True,
                "self": 1,
                "hp": [self.hp, 100],
                "mp": [500, 500],
                "tile": list(self.stale or self.tile),
            }
        if cmd == "near":
            objs = [
                {
                    "h": 1,
                    "kind": 10,
                    "id": 60004,
                    "inst": 1,
                    "x": self.tile[0] * 40,
                    "y": self.tile[1] * 40,
                    "dead": 0,
                }
            ]
            if self.tile == (500, 500) and self.fox_visible:
                objs.append(
                    {"h": 77, "kind": 11, "id": 7603, "inst": 1, "x": 20000, "y": 20000, "dead": 0}
                )
            if self.stage == LOBBY_STAGE:
                objs.append({"h": 99, "kind": 3, "id": YAN, "inst": 1, "x": 0, "y": 0, "dead": 0})
            elif self.fade > 0:
                objs.append(self.mob())
                if self.mob_dead:
                    self.fade -= 1
            return {"ok": True, "objects": objs}
        if cmd in ("attack", "cast"):
            self.mob_dead = True
            return {"ok": True}
        if cmd == "walk":
            self.stale = None
            self.tile = (int(args[0]) // 40, int(args[1]) // 40)
            exits = make_tower().exits[self.room]
            if self.tile in exits and self.mob_dead and self.fade <= 0:
                self.dialog_open = True
            return {"ok": True, "path": True}
        if cmd == "talk":
            self.fox_dialog = args[0] == "77"
            return {"ok": True}
        if cmd == "dialog":
            if self.fox_dialog:
                return {"ok": True, "open": True, "waiting": False, "options": [65862, 65861]}
            if self.dialog_open:
                return {"ok": True, "open": True, "waiting": False, "options": self.exit_options}
            return {"ok": True, "open": False}
        if cmd == "option" and self.fox_dialog:
            assert [65862, 65861][int(args[0])] == 65861  # never the decline
            self.fox_dialog = False
            self.stale = self.tile  # like the client: status keeps the old tile
            self.room, self.tile = 1, (100, 10)
            return {"ok": True}
        if cmd == "option":
            choice = self.exit_options[int(args[0])]
            self.dialog_open = False
            if choice == LEAVE:
                self.stage = LOBBY_STAGE
            else:
                self.room += 1
                self.tile = (self.room * 100, 10)
                self.mob_dead, self.fade = False, 2
            return {"ok": True}
        return {"ok": True}


class FakeGuard:
    def __init__(self):
        self.started = False
        self.casts = 0
        self.pending = False

    def buffs_pending(self, pid):
        return self.pending

    def running(self, pid):
        return self.started

    def start(self, pid):
        self.started = True
        return type("R", (), {"ok": True, "reason": None})()

    def cast_count(self, pid):
        return self.casts


def make(game=None, combat=None, config=None, hp_items=(24007,)):
    game = game or FakeGame()
    store = GuardStore()
    store.save("寒江孤影", GuardConfig(potion=GuardPotionRule(hp_items=list(hp_items))))
    store.save_section("寒江孤影", COMBAT_SECTION, combat or CombatRule())
    store.save_section("寒江孤影", TOWER_SECTION, config or TowerConfig())
    clock = {"t": 0.0}

    def read_locked(pid, fn):
        if fn is read_stage_id:
            return (game.stage, "神武玄天塔" if game.stage != LOBBY_STAGE else "玄天之境")
        if fn is read_learned:
            return dict(LEARNED)
        if fn is read_holdings:
            return dict(game.bag), dict(game.petbag)
        raise AssertionError(fn)

    def wait(ev, secs):
        clock["t"] += secs
        return ev.is_set()

    guard = FakeGuard()
    mgr = TowerManager(
        guard=guard,
        read_locked=read_locked,
        character_name=lambda pid: "寒江孤影",
        channel=game,
        store=store,
        attack_skills=lambda: DEFS,
        tower=lambda sid: make_tower(sid) if sid != LOBBY_STAGE else None,
        clock=lambda: clock["t"],
        wall=lambda: 1000.0 + clock["t"],
        wait=wait,
        leave_game=lambda pid: game.leave(),
    )
    run = _Run(
        "寒江孤影",
        store.load_section("寒江孤影", COMBAT_SECTION, CombatRule),
        store.load_section("寒江孤影", TOWER_SECTION, TowerConfig),
    )
    with mgr._lock:
        mgr._runs[1] = run
    return mgr, run, game, guard


def ticks(mgr, run, n):
    for _ in range(n):
        mgr._tick(1, run)


def test_clears_a_room_and_continues_to_the_next():
    mgr, run, game, _ = make()
    for _ in range(20):
        mgr._tick(1, run)
        if run.floors:
            break
    assert any(line.startswith("attack 51") for line in game.sent)
    assert [f.floor for f in run.floors] == [1]
    assert game.room == 2
    ticks(mgr, run, 1)
    assert run.room == 2
    assert any("第 1 層通過" in line.text for line in run.log)


def test_skills_go_before_the_basic_attack():
    mgr, run, game, _ = make(combat=CombatRule(basic=True, opener=751))
    ticks(mgr, run, 2)
    fights = [line for line in game.sent if line.split()[0] in ("attack", "cast")]
    assert fights[0] == "cast 75110 51"


def test_stop_floor_leaves_through_the_exit():
    mgr, run, game, _ = make(config=TowerConfig(stop_floor=1))
    with pytest.raises(_Done, match="打到第 1 層"):
        ticks(mgr, run, 12)
    assert game.stage == LOBBY_STAGE


def test_back_in_the_lobby_after_a_clear_ends_the_run():
    mgr, run, game, _ = make()
    run.cleared = True
    game.stage = LOBBY_STAGE
    with pytest.raises(_Done, match="被送出塔"):
        mgr._tick(1, run)


def test_full_bag_at_the_exit_stops():
    mgr, run, game, _ = make(game=FakeGame(exit_options=()))

    def no_options(pid, line, priority=0, _send=game.send):
        r = _send(pid, line, priority)
        if line == "dialog" and game.dialog_open:
            game.dialog_open = False  # 65857: opens and closes with no options
            return {"ok": True, "open": True, "waiting": False, "options": []}
        return r

    game.send = no_options
    with pytest.raises(_Done, match="背包滿或超重"):
        ticks(mgr, run, 12)


def test_death_stops():
    mgr, run, game, _ = make()
    game.hp = 0
    with pytest.raises(_Done, match="角色死亡"):
        mgr._tick(1, run)


def test_guard_buff_cast_pauses_fighting():
    mgr, run, game, guard = make()
    guard.casts = 1
    mgr._tick(1, run)
    assert not [line for line in game.sent if line.startswith("attack")]


def test_start_needs_an_attack_and_potions_and_starts_the_guard():
    mgr, _, _, guard = make(combat=CombatRule(basic=False))
    assert mgr.start(1) == (False, "還沒設定攻擊方式：勾普攻，或選至少一個技能")
    mgr, _, _, guard = make(hp_items=())
    ok, reason = mgr.start(1)
    assert not ok and "白名單" in reason
    mgr, run, _, guard = make()
    mgr._runs.clear()
    mgr._sleep = lambda ev, secs: True  # the thread stops at its first wait
    ok, _ = mgr.start(1)
    assert ok and guard.started
    mgr._runs[1].thread.join(2)


def test_own_hits_are_noted_by_target_key():
    import struct

    mgr, run, _, _ = make()
    own = struct.pack("<HII", 10, 60004, 1)
    raw = b"\x43" + struct.pack("<HII", 2, 9001, 1) + own + b"\x00" * 7
    mgr.on_attack_packet(1, raw, 0.0, own)
    assert (9001, 1) in run.hits
    mgr.on_attack_packet(
        1, b"\x43" + struct.pack("<HII", 2, 9001, 2) + b"\x01" * 10 + b"\x00" * 7, 0.0, own
    )
    assert (9001, 2) not in run.hits  # someone else's hit


def test_view_without_a_hook_pipe_is_not_an_error():
    from services.hook_cmd import PipeGone

    mgr, _, game, _ = make()

    def gone(pid, line, priority=0):
        raise PipeGone("no pipe")

    game.send = gone
    assert mgr.view(1).hook_ready is False


def test_caps_are_cached_for_the_view():
    mgr, _, game, _ = make()
    mgr.view(1)
    mgr.view(1)
    assert game.sent.count("caps") == 1


def test_at_the_start_waits_for_buffs_then_talks_to_the_fox():
    game = FakeGame()
    game.tile = (500, 500)
    mgr, run, game, guard = make(game=game)
    guard.pending = True
    mgr._tick(1, run)
    assert "talk 77" not in game.sent and run.step.startswith("等守護補完")
    guard.pending = False
    mgr._tick(1, run)
    assert "talk 77" in game.sent and game.tile == (100, 10)
    mgr._tick(1, run)
    assert run.room == 1  # in room 1 now, not at the start


def test_buff_wait_has_a_limit():
    game = FakeGame()
    game.tile = (500, 500)
    mgr, run, game, guard = make(game=game)
    guard.pending = True
    for _ in range(80):  # 0.5 s each
        mgr._tick(1, run)
        mgr._wait(run, 0.5)
        if "talk 77" in game.sent:
            break
    assert "talk 77" in game.sent


def test_fox_not_listed_yet_walks_to_it():
    game = FakeGame()
    game.tile, game.fox_visible = (500, 500), False
    mgr, run, game, _ = make(game=game)
    mgr._tick(1, run)
    assert "walk 20020 20020" in game.sent and "talk 77" not in game.sent


def test_a_briefly_missing_pipe_is_retried():
    from services.hook_cmd import PipeGone

    mgr, run, game, _ = make()
    real, fails = game.send, {"n": 2}

    def flaky(pid, line, priority=0):
        if fails["n"]:
            fails["n"] -= 1
            raise PipeGone("loading")
        return real(pid, line, priority)

    game.send = flaky
    mgr._tick(1, run)  # no _Done
    assert run.room == 1


def test_room_holds_while_standing_by_its_exit():
    # By room 1's exit another room's start can be nearer (DB layout): the room
    # must not change until the exit goes through.
    mgr, run, game, _ = make()
    mgr._tick(1, run)
    assert run.room == 1
    tower = make_tower()
    tower.starts[5] = (125, 10)  # nearer room 1's exit than room 1's start
    mgr._towers[1704] = tower
    game.tile = (121, 10)
    mgr._tick(1, run)
    assert run.room == 1


class SurvivingGame(FakeGame):
    """Casts do not kill: the opener's fate is up to the 0x43 the test feeds."""

    def send(self, pid, line, priority=0):
        if line.startswith("cast"):
            self.sent.append(line)
            return {"ok": True}
        return super().send(pid, line, priority)


def own_hit(mgr, magic, level, target=(7, 9001, 1)):  # kind 7 != near's 2
    import struct

    own = struct.pack("<HII", 10, 60004, 1)
    raw = b"\x43" + struct.pack("<HII", *target) + own + struct.pack("<I", magic * 100 + level)
    mgr.on_attack_packet(1, raw + b"\x01\x00" + b"\x00\x01\x00\x00\x00", 0.0, own)


def casts(game):
    return [line.split()[1] for line in game.sent if line.startswith("cast")]


def test_a_dropped_opener_is_sent_again_after_the_puppet_gap():
    game = SurvivingGame()
    mgr, run, game, _ = make(game=game, combat=CombatRule(basic=False, opener=751, rotation=[754]))
    mgr._tick(1, run)
    assert casts(game) == ["75110"]
    mgr._wait(run, 0.3)
    mgr._tick(1, run)
    assert casts(game) == ["75110"]  # within 0.45 s: wait for the server's 0x10
    mgr._wait(run, 0.2)
    mgr._tick(1, run)  # no 0x10: dropped, the same pick goes again
    assert casts(game) == ["75110", "75110"]
    own_hit(mgr, 751, 10)  # a hit counts as taken too
    mgr._wait(run, 0.5)
    mgr._tick(1, run)
    assert casts(game) == ["75110", "75110", "75415"]


def test_a_pick_dropped_four_times_moves_on():
    game = SurvivingGame()
    mgr, run, game, _ = make(game=game, combat=CombatRule(basic=False, opener=751, rotation=[754]))
    for _ in range(12):
        mgr._tick(1, run)
        mgr._wait(run, 0.5)
    sent = casts(game)
    assert sent[:4] == ["75110"] * 4 and sent[4] == "75415"
    assert any("連續 4 次沒被伺服器接受" in line.text for line in run.log)


def own_cast_start(mgr, magic, level):
    import struct

    own = struct.pack("<HII", 10, 60004, 1)
    raw = (
        b"\x10"
        + own
        + struct.pack("<I", magic * 100 + level)
        + struct.pack("<HH", 3, 0)
        + struct.pack("<HII", 7, 9001, 1)
        + b"\x01"
    )
    mgr.on_cast_packet(1, raw, 0.0, own)


def test_cast_start_confirms_the_opener_before_any_hit():
    game = SurvivingGame()
    mgr, run, game, _ = make(game=game, combat=CombatRule(basic=False, opener=751, rotation=[754]))
    mgr._tick(1, run)
    own_cast_start(mgr, 751, 10)
    mgr._wait(run, 0.6)  # 450 ms floor + the 80 ms starting margin
    mgr._tick(1, run)
    assert casts(game) == ["75110", "75415"]


def test_next_skill_waits_from_when_the_server_took_the_last_one():
    from services.combat import MARGIN_START, Rotation, cast_taken

    rot = Rotation()
    cast_taken(rot, DEFS[(751, 10)], 2.0, 751)  # taken at 2.0 s, recharge + stun 400 ms
    assert rot.next_skill == pytest.approx(2.45 + MARGIN_START)  # 450 ms floor + margin
    cast_taken(rot, AttackSkill("x", 0, False, 900), 2.1, 9)
    assert rot.next_skill == pytest.approx(3.0 + MARGIN_START)


def test_margin_grows_on_a_drop_and_shrinks_on_a_first_try_take():
    from services.combat import MARGIN_DOWN, MARGIN_START, MARGIN_UP, Rotation, settle_cast

    rot = Rotation()
    cast_sent(rot, 714, 0.0)
    assert settle_cast(rot, None, 0.5) == "dropped"
    assert rot.margins[714] == pytest.approx(MARGIN_START + MARGIN_UP)
    cast_sent(rot, 714, 0.5)
    assert settle_cast(rot, 0.6, 0.7) is None  # taken, but not first try: no shrink
    assert rot.margins[714] == pytest.approx(MARGIN_START + MARGIN_UP)
    cast_sent(rot, 714, 2.0)
    settle_cast(rot, 2.1, 2.2)
    assert rot.margins[714] == pytest.approx(MARGIN_START + MARGIN_UP - MARGIN_DOWN)


def mob(h, npc, tx, ty):
    return {"h": h, "kind": 11, "id": npc, "inst": h, "x": tx * 40, "y": ty * 40, "dead": 0}


def test_pick_target_nearest_weakest_and_away_from_packs():
    from services.combat import pick_target

    pack = [mob(1, 8801, 5, 0), mob(2, 8801, 6, 0), mob(3, 8801, 5, 1)]
    elite = mob(4, 8802, 2, 0)
    straggler = mob(5, 8801, 9, 9)
    live = [*pack, elite, straggler]
    hp = {8801: (0, 8237), 8802: (1, 7604)}  # (elite, DB HP): the elite reads lower
    me = (0, 0)
    assert pick_target(live, me, CombatRule(), hp)["h"] == 4  # the elite is nearest
    assert pick_target(live, me, CombatRule(target="weakest"), hp)["h"] in (1, 3)
    weak_alone = CombatRule(target="weakest", avoid_packs=True)
    assert pick_target(live, me, weak_alone, hp)["h"] == 5  # the straggler, not into the pack
    assert pick_target(live, me, CombatRule(avoid_packs=True), hp)["h"] == 4


def test_pack_monster_walking_up_takes_over_from_an_elite():
    from services.combat import mobbed_by

    elite, far, near = mob(4, 8802, 2, 0), mob(1, 8801, 9, 0), mob(2, 8801, 1, 2)
    weak = CombatRule(target="weakest")
    elites = frozenset({8802})
    assert mobbed_by(elite, [elite, far], (0, 0), weak, elites) is None  # nobody close
    assert mobbed_by(elite, [elite, far, near], (0, 0), weak, elites)["h"] == 2
    assert mobbed_by(far, [elite, far, near], (0, 0), weak, elites) is None  # pack to pack: no
    assert mobbed_by(elite, [elite, near], (0, 0), CombatRule(), elites) is None  # nearest mode


def test_short_of_potions_at_an_exit_leaves_instead_of_continuing():
    game = FakeGame()
    game.bag, game.petbag = {24007: 30}, {24007: 10}  # 40 in all
    mgr, run, game, _ = make(game=game, config=TowerConfig(leave_hp_below=50))
    with pytest.raises(_Done, match="體力藥只剩 40，離開塔"):
        ticks(mgr, run, 20)
    assert game.stage == LOBBY_STAGE


def test_enough_potions_with_the_pet_bag_continues():
    game = FakeGame()
    game.bag, game.petbag = {24007: 30}, {24007: 300}
    mgr, run, game, _ = make(game=game, config=TowerConfig(leave_hp_below=50))
    for _ in range(20):
        mgr._tick(1, run)
        if run.floors:
            break
    assert game.room == 2 and game.stage != LOBBY_STAGE


def test_potions_used_up_mid_floor_logs_out():
    game = FakeGame()
    game.bag, game.petbag = {24007: 0}, {}
    mgr, run, game, _ = make(game=game, config=TowerConfig(logout=True))
    with pytest.raises(_Done, match="已登出遊戲"):
        mgr._tick(1, run)
    assert game.left


def test_logout_that_does_not_take_says_so():
    game = FakeGame()
    game.bag, game.petbag = {24007: 0}, {}
    game.leave = lambda: True  # clicked, but the character stays
    mgr, run, game, _ = make(game=game, config=TowerConfig(logout=True))
    with pytest.raises(_Done, match="角色還在遊戲裡"):
        mgr._tick(1, run)


def test_logout_off_by_default():
    game = FakeGame()
    game.bag, game.petbag = {24007: 0}, {}
    mgr, run, game, _ = make(game=game)
    mgr._tick(1, run)
    assert not game.left


def test_estimate_sets_the_stop_floor():
    from services.tower_run import read_hit_level

    mgr, _, game, guard = make()
    guard.missing_buffs = lambda pid: ["冰心靈訣"]
    real = mgr._read_locked
    mgr._read_locked = lambda pid, fn: (2000, 150) if fn is read_hit_level else real(pid, fn)
    mgr._gates = [0] * 70
    est = mgr.estimate(1)
    assert est.ok and est.hit == 2000 and est.max_floor == 70 and est.applied  # 7 關, no dodge
    assert est.missing_buffs == ["冰心靈訣"]
    assert mgr.view(1).config.stop_floor == 70


def outside(mgr, game):
    """Stand in 成都少城, which is not a tower map."""
    game.stage = 53
    load = mgr._load_tower
    mgr._load_tower = lambda sid: None if sid == 53 else load(sid)
    mgr._towers.clear()


class FakeNavigator:
    def __init__(self, game, ok=True, detail=""):
        self.game, self.ok, self.detail = game, ok, detail
        self.calls = []

    def go(self, pid, dest, goal=None, stop=None, note=None):
        self.calls.append(dest)
        if note:
            note("走出口到杭州城")
        if self.ok:
            self.game.stage = dest
        return NavResult(self.ok, "arrived" if self.ok else "stuck", self.detail)


def test_started_elsewhere_walks_to_the_lobby_first():
    mgr, run, game, _ = make()
    outside(mgr, game)
    nav = FakeNavigator(game)
    mgr._navigator = nav
    mgr._tick(1, run)
    assert nav.calls == [LOBBY_STAGE]
    assert game.stage == LOBBY_STAGE
    assert any("導航到玄天之境" in line.text for line in run.log)
    with pytest.raises(_Done, match="不在玄天之境或玄天塔裡"):
        mgr._go_to_lobby(1, run, "成都少城")  # once a run: thrown out later is not walked back
    assert nav.calls == [LOBBY_STAGE]


def test_navigation_that_fails_stops_with_its_reason():
    mgr, run, game, _ = make()
    outside(mgr, game)
    mgr._navigator = FakeNavigator(game, ok=False, detail="傳點 (48, 98) 沒有傳送")
    with pytest.raises(_Done, match="走不到玄天之境：傳點"):
        mgr._tick(1, run)


def test_outside_after_floors_is_not_walked_back():
    mgr, run, game, _ = make()
    run.floors.append(type("F", (), {"floor": 3})())
    outside(mgr, game)
    mgr._navigator = FakeNavigator(game)
    with pytest.raises(_Done, match="不在玄天之境或玄天塔裡"):
        mgr._tick(1, run)
