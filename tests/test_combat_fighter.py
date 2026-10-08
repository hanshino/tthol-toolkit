import struct

from services.api_types import CombatRule
from services.combat import NO_ATTACK, AttackSkill, Fighter, attack_problem, skill_candidates

DEFS = {
    (751, 10): AttackSkill("劍盪千秋", 20, False, 400),
    (754, 15): AttackSkill("瞬影斬", 24, False, 0),
}
OWN = struct.pack("<HII", 10, 60004, 1)
MOB = {"h": 5, "id": 9001, "inst": 1, "x": 400, "y": 400}


def fight(rule):
    f = Fighter(rule)
    f.learned = {751: 10, 754: 15}
    sent = []

    def tick(now, casts=0, live=(MOB,)):
        f.tick(
            list(live),
            (0, 0),
            {},
            frozenset(),
            DEFS,
            999,
            casts,
            now,
            send=lambda line: sent.append(line) or {"ok": True},
            note=lambda phase, text: None,
        )

    return f, sent, tick


def cast_start(f, magic, level, now):
    raw = b"\x10" + OWN + struct.pack("<I", magic * 100 + level) + b"\x00" * 15
    f.on_cast(raw, OWN, now)


def test_opener_once_then_the_rotation():
    f, sent, tick = fight(CombatRule(basic=True, opener=751, rotation=[754]))
    tick(0.0)
    cast_start(f, 751, 10, 0.1)
    tick(0.7)
    cast_start(f, 754, 15, 0.8)
    tick(1.5)
    assert sent == ["cast 75110 5", "cast 75415 5", "cast 75415 5"]


def test_basic_attack_only_once_per_target():
    _, sent, tick = fight(CombatRule(basic=True))
    tick(0.0)
    tick(0.1)
    assert sent == ["attack 5"]


def test_a_guard_buff_cast_pauses_the_fight():
    _, sent, tick = fight(CombatRule(basic=True))
    tick(0.0, casts=1)
    tick(0.5, casts=1)
    assert sent == []
    tick(1.1, casts=1)
    assert sent == ["attack 5"]


def test_only_our_own_hits_count():
    f = Fighter(CombatRule(basic=True))
    raw = b"\x43" + struct.pack("<HII", 2, 9001, 1) + OWN + b"\x00" * 7
    f.on_attack(raw, OWN, 1.0)
    other = b"\x43" + struct.pack("<HII", 2, 9001, 2) + b"\x01" * 10 + b"\x00" * 7
    f.on_attack(other, OWN, 1.0)
    assert f.hits == {(9001, 1): 1.0}


def test_reset_keeps_the_skill_margins():
    f = Fighter(CombatRule(basic=True))
    f.rot.margins[751] = 0.2
    f.hits[(1, 1)] = 1.0
    f.reset()
    assert f.rot.margins == {751: 0.2} and f.hits == {}


def test_attack_problem_and_candidates():
    assert attack_problem(CombatRule(basic=False)) == NO_ATTACK
    assert attack_problem(CombatRule(basic=False, rotation=[754])) is None
    got = skill_candidates({751: 10, 999: 1}, DEFS)
    assert [(c.magic_id, c.level, c.name) for c in got] == [(751, 10, "劍盪千秋")]
