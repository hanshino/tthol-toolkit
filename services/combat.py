"""Fighting one target at a time, the way the battle puppet does (tthol-data's
static read of the client, 2026-10-05; not verified in game).

- The target stays until it dies; a new one is the nearest live monster.
- On a new target the opener goes first (once), then the rotation skills take
  turns; a slot that cannot be used now is skipped, never waited on.
- The basic attack is the engine's auto attack: sent on a new target and after
  every skill (a cast stops it), it repeats by itself.
- Skills are sent again each time (no client repeat). The puppet only keeps a
  fixed 450 ms gap and lets the server drop casts that come too early; here the
  next skill waits recharge_time + stun of the last one, at least 450 ms.
- A guard buff cast pauses fighting for 1 s, like the puppet.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from services._paths import bundled
from services.api_types import CombatRule

SKILL_GAP = 0.45  # the puppet's fixed gap between two skills
BUFF_PAUSE = 1.0  # after a guard buff cast
RELOCK_AFTER = 2.2  # no own hit on the target this long: send the attack again
# A cast counts only once the server takes it: our own cast start (0x10) comes
# 0.1-0.3 s after the send. One sent before the last one's recharge + stun is
# over (server time) is dropped silently: no 0x10, no MP spent. Like the
# puppet, a dropped cast is sent again SKILL_GAP after the send (measured
# 2026-10-05: 1 in 4 casts dropped when sent right at recharge + stun, and
# waiting a full gap again stalled the rotation for a second each time); the
# rotation slot only moves on once the cast was taken.
DROP_TRIES = 4  # the same pick dropped this often in a row: move on to the next slot
# The server wants a little more than recharge + stun (measured: ~40 % dropped at
# +0.03 s). Each skill keeps its own margin on top: up after a drop, down after a
# first-try take, so it settles where the server just takes it.
MARGIN_START = 0.08
MARGIN_UP = 0.03
MARGIN_DOWN = 0.01
MARGIN_MAX = 0.3


@dataclass(frozen=True)
class AttackSkill:
    name: str
    mp: int
    area: bool
    gap_ms: int
    # The skill's hit multiplier: the hit it lands with is the character's hit
    # times this (user, 2026-10-07; 幽冥刺擊 LV20 = 1.25, most skills 0.8-0.95).
    hit: float = 1.0


def hit_rate(func_hit: int | None, p1: int | None) -> float:
    """magic.func_hit 1 carries the hit percent in func_hit_p1 (its help text says
    the same: 追加20%命中 = 120). Multi-hit skills (52) and the rest carry none."""
    return p1 / 100 if func_hit == 1 and p1 else 1.0


def load_attack_skills(db_path: Path | None = None) -> dict[tuple[int, int], AttackSkill]:
    """(magic id, level) -> AttackSkill for every skill level aimed at an enemy."""
    path = db_path or bundled("tthol.sqlite")
    if not path.exists():
        return {}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.text_factory = lambda b: b.decode("utf-8", errors="replace")
    try:
        rows = con.execute(
            "SELECT id, level, name, target, spend_mp, recharge_time, stun, func_hit,"
            " func_hit_p1 FROM magic WHERE target LIKE 'TARGET_ENEMY%'"
        ).fetchall()
    finally:
        con.close()
    return {
        (mid, level): AttackSkill(
            name,
            mp or 0,
            target == "TARGET_ENEMYEX",
            (recharge or 0) + (stun or 0),
            hit_rate(func_hit, p1),
        )
        for mid, level, name, target, mp, recharge, stun, func_hit, p1 in rows
    }


@dataclass
class Rotation:
    """Where the rotation is for the current target."""

    target: int | None = None  # handle
    opened: bool = False  # the opener went out (or was skipped) for this target
    slot: int = 0  # next rotation slot to try
    next_skill: float = 0.0  # no skill before this (clock)
    paused_until: float = 0.0
    attacked: bool = False  # the basic attack is running on the target
    last_hit: float = 0.0  # own 0x43 on the target, or the last (re)lock
    before: tuple[int, bool] = (0, False)  # (slot, opened) before the last pick
    pending: tuple[int, float] | None = None  # (magic id, sent clock) not taken yet
    drops: int = 0  # the current pick dropped this often in a row
    # magic id -> extra wait (s); kept across targets and rooms
    margins: dict[int, float] = field(default_factory=dict)


def retarget(rot: Rotation, handle: int, now: float) -> None:
    """A new target: start over from the opener."""
    rot.target, rot.opened, rot.slot, rot.attacked, rot.last_hit = handle, False, 0, False, now
    rot.pending, rot.drops = None, 0


def next_skill(
    rule: CombatRule,
    rot: Rotation,
    learned: dict[int, int],
    defs: dict[tuple[int, int], AttackSkill],
    mp: int,
    now: float,
) -> tuple[int, int, AttackSkill] | None:
    """(magic id, level, def) to cast now, advancing the rotation; None for no skill now."""
    if now < rot.next_skill or now < rot.paused_until:
        return None
    rot.before = (rot.slot, rot.opened)

    def usable(mid: int | None):
        level = learned.get(mid) if mid else None
        d = defs.get((mid, level)) if level else None
        return (mid, level, d) if d is not None and d.mp <= mp else None

    if not rot.opened:
        rot.opened = True
        pick = usable(rule.opener)
        if pick:
            return pick
    n = len(rule.rotation)
    for i in range(n):
        slot = (rot.slot + i) % n
        pick = usable(rule.rotation[slot])
        if pick:
            rot.slot = (slot + 1) % n
            return pick
    return None


def cast_taken(rot: Rotation, skill: AttackSkill | None, at: float, mid: int = 0) -> None:
    """The server took a cast at `at` (own 0x10): the next one waits its
    recharge + stun (+ the skill's margin) from then, not from our send."""
    gap = max(SKILL_GAP, (skill.gap_ms if skill else 0) / 1000)
    rot.next_skill = max(rot.next_skill, at + gap + rot.margins.get(mid, MARGIN_START))


def cast_sent(rot: Rotation, mid: int, now: float) -> None:
    """A cast went out: retry it SKILL_GAP from now unless the server takes it."""
    rot.next_skill = now + SKILL_GAP
    rot.pending = (mid, now)
    rot.attacked = False  # a cast stops the auto attack: send it again
    rot.last_hit = max(rot.last_hit, now)


def settle_cast(rot: Rotation, taken_at: float | None, now: float) -> str | None:
    """Check the last cast: "dropped" (the same pick goes again), "gave_up"
    (dropped DROP_TRIES times: the rotation moves on), or None (taken, or
    still waiting). `taken_at`: our last 0x10 for that skill, if any."""
    if rot.pending is None:
        return None
    _mid, sent = rot.pending
    if taken_at is not None and taken_at >= sent:
        if rot.drops == 0:  # taken first try: the margin can shrink a little
            rot.margins[_mid] = max(0.0, rot.margins.get(_mid, MARGIN_START) - MARGIN_DOWN)
        rot.pending, rot.drops = None, 0
        return None
    if now < sent + SKILL_GAP:
        return None
    rot.pending = None
    rot.margins[_mid] = min(MARGIN_MAX, rot.margins.get(_mid, MARGIN_START) + MARGIN_UP)
    rot.drops += 1
    if rot.drops >= DROP_TRIES:
        rot.drops = 0
        return "gave_up"
    rot.slot, rot.opened = rot.before  # not taken: hand out the same pick again
    return "dropped"


MOBBED_RADIUS = 3  # tiles: a pack monster this close to us takes over from an elite target
PACK_RADIUS = 3  # tiles: other live monsters this close count as a pack around a target
PACK_COST = 4.0  # each of them weighs like this many tiles of extra distance
TILE_PX = 40


def pick_target(
    live: list[dict], me: tuple[int, int], rule: CombatRule, strength: dict[int, tuple]
) -> dict:
    """The new target among `live` (`near` entries, pixels), by the rule's choice.

    nearest: by distance; weakest: lowest `strength` (npc id -> sortable, e.g.
    (elite, base HP)) first, nearest among equals.
    avoid_packs adds a cost for every other live monster around a candidate, so
    stragglers at the edge go first.
    """

    def tiles(o):
        return o["x"] / TILE_PX, o["y"] / TILE_PX

    mx, my = me[0] / TILE_PX, me[1] / TILE_PX

    def cost(o):
        x, y = tiles(o)
        c = ((x - mx) ** 2 + (y - my) ** 2) ** 0.5
        if rule.avoid_packs:
            crowd = sum(
                1
                for p in live
                if p is not o
                and ((tiles(p)[0] - x) ** 2 + (tiles(p)[1] - y) ** 2) ** 0.5 <= PACK_RADIUS
            )
            c += crowd * PACK_COST
        return c

    if rule.target == "weakest":
        return min(live, key=lambda o: (strength.get(o["id"], ()), cost(o)))
    return min(live, key=cost)


def mobbed_by(
    target: dict, live: list[dict], me: tuple[int, int], rule: CombatRule, elites: frozenset[int]
) -> dict | None:
    """In weakest-first mode, a pack monster that walked up to us while we hit an
    elite: switch to it (the nearest such). Never from one pack monster to another."""
    if rule.target != "weakest" or target["id"] not in elites:
        return None
    mx, my = me[0] / TILE_PX, me[1] / TILE_PX
    close = [
        o
        for o in live
        if o["id"] not in elites
        and ((o["x"] / TILE_PX - mx) ** 2 + (o["y"] / TILE_PX - my) ** 2) ** 0.5 <= MOBBED_RADIUS
    ]
    if not close:
        return None
    return min(close, key=lambda o: (o["x"] / TILE_PX - mx) ** 2 + (o["y"] / TILE_PX - my) ** 2)
