import pytest

from services._paths import bundled
from services.tower import FIRST_STAGE, LEAVE, load_tower, pick_option

needs_db = pytest.mark.skipif(
    not bundled("tthol.sqlite").exists(), reason="tthol.sqlite not pulled"
)


@needs_db
def test_load_tower_rooms_from_the_game_db():
    t = load_tower(FIRST_STAGE)
    assert t is not None
    assert t.starts[1] == (9, 11) and t.starts[10] == (100, 41)
    assert sorted(t.exits[1]) == [(29, 19), (30, 20), (31, 19)]
    assert t.expect[1] == 9 and t.expect[10] == 4  # 8 + elite; room 10: 3 + 1
    assert 8801 in t.monsters[1] and 8802 in t.monsters[1]
    assert t.floor(1) == 1 and load_tower(1705).floor(4) == 14
    assert load_tower(357) is None  # 玄天之境 is no tower floor
    assert t.fox == (112, 93)
    assert t.elites >= {8802} and 8801 not in t.elites
    assert t.strength()[8801] < t.strength()[8802]  # the pack first, though its DB HP is higher
    assert t.at_start((108, 94)) and not t.at_start((10, 12))


@needs_db
def test_room_of_and_staging():
    t = load_tower(FIRST_STAGE)
    assert t.room_of((10, 12)) == 1 and t.room_of((55, 70)) == 7
    # By region: every exit tile is its own room's, even where the next start is near.
    assert t.area is not None
    for room in range(1, 11):
        assert {t.room_of(x) for x in t.exits[room]} == {room}
    assert load_tower(1707).room_of((9, 36)) == 2  # floor 32's start (live 2026-10-06)
    sx, sy = t.staging(1)
    zx, zy = t.exits[1][len(t.exits[1]) // 2]  # the zone's middle tile
    assert 3 <= ((sx - zx) ** 2 + (sy - zy) ** 2) ** 0.5 <= 4.5  # rounded to a tile
    assert sx < zx  # toward the room start (west of the exit)
    assert t.exit_tiles(1)[0] in t.exits[1]


def test_pick_option_by_jump_id():
    assert pick_option([65852, 65846], (65846, 65847), frozenset({65852})) == 1
    assert pick_option([LEAVE, 65876], (), frozenset({LEAVE})) == 1  # any "continue"
    assert pick_option([LEAVE], (), frozenset({LEAVE})) is None
    assert pick_option([0, 65877], (), frozenset()) == 1  # 0 = no jump


@needs_db
def test_reach_matches_the_users_run_of_2026_10_05():
    from services.tower import FOX_STAGE, estimate_reach, floor_gates

    stages = [load_tower(s) for s in sorted(set(FOX_STAGE.values()))]
    gates = floor_gates()
    assert len(gates) == 70 and gates[:5] == [70, 70, 70, 70, 72]
    reach = estimate_reach(1089, 192, stages, gates)  # cleared 65, stalled on 66
    assert reach.max_floor == 65 and "傀變狂暴邪猴" in reach.blocker
    low = estimate_reach(9999, 150, stages, gates)  # hit is no issue: the level gate stops it
    assert low.blocker.endswith(f"LV{gates[low.max_floor - 1]}")


def test_reach_stops_at_the_first_floor_out_of_reach():
    from services.tower import ROOMS, TowerStage, estimate_reach

    def stage(sid, dodges):
        rooms = range(1, ROOMS + 1)
        return TowerStage(
            stage_id=sid,
            starts={r: (r, 0) for r in rooms},
            exits={r: [(r, 1)] for r in rooms},
            monsters={r: frozenset({sid * 100 + r}) for r in rooms},
            expect={r: 1 for r in rooms},
            npc_dodge={sid * 100 + r: d for r, d in zip(rooms, dodges)},
            npc_name={sid * 100 + r: f"怪{r}" for r in rooms},
        )

    s = stage(1704, [100, 100, 100, 500, 100, 100, 100, 100, 100, 100])
    r = estimate_reach(300, 200, [s], [0] * 70)
    assert r.max_floor == 3 and r.blocker == "第 4 層 怪4 迴避 500，命中不夠"
    assert estimate_reach(600, 200, [s], [0] * 70).max_floor == 10


def test_the_best_hitting_attack_carries_the_climb():
    from services.tower import ROOMS, TowerStage, attack_reach

    rooms = range(1, ROOMS + 1)
    dodges = [100, 100, 100, 500, 100, 100, 100, 100, 100, 100]
    s = TowerStage(
        stage_id=1704,
        starts={r: (r, 0) for r in rooms},
        exits={r: [(r, 1)] for r in rooms},
        monsters={r: frozenset({170400 + r}) for r in rooms},
        expect={r: 1 for r in rooms},
        npc_dodge={170400 + r: d for r, d in zip(rooms, dodges)},
        npc_name={170400 + r: f"怪{r}" for r in rooms},
    )
    # hit 420: 普攻 stops at floor 4 (dodge 500); 幽冥刺擊 x1.25 lands 525.
    reach, each = attack_reach(420, 200, [s], [0] * 70, [("普攻", 1.0), ("幽冥刺擊", 1.25)])
    assert reach.max_floor == 10 and reach.blocker is None
    by = {a.name: a for a in each}
    assert by["幽冥刺擊"].hit == 525 and by["普攻"].max_floor == 3
    # A 0.9 skill alone does worse than the bare hit would: hit 540 -> 486.
    low, _ = attack_reach(540, 200, [s], [0] * 70, [("穿雲箭", 0.9)])
    assert low.max_floor == 3 and "迴避 500" in low.blocker
    # No attack set: the bare hit, as before.
    bare, each = attack_reach(540, 200, [s], [0] * 70, [])
    assert bare.max_floor == 10 and each[0].rate == 1.0


def test_hit_rate_from_the_magic_columns():
    from services.combat import hit_rate

    assert hit_rate(1, 125) == 1.25
    assert hit_rate(1, 95) == 0.95
    assert hit_rate(52, None) == 1.0  # multi-hit (落英繽紛): no multiplier
    assert hit_rate(None, None) == 1.0


@needs_db
def test_skill_hit_rates_load_from_the_db():
    from services.combat import load_attack_skills

    skills = load_attack_skills()
    assert skills[(722, 20)].hit == 1.25 and skills[(722, 1)].hit == 1.2  # 幽冥刺擊
