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

`Fighter` runs this for every module that fights (神武玄天塔, dungeons): the
module decides which monsters are fair game, the fighter picks among them and
sends the attacks. The settings are one `CombatRule` per character
(COMBAT_SECTION), shared by every module.
"""

from __future__ import annotations

import logging
import sqlite3
import struct
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from services._paths import bundled
from services.api_types import AttackSkillCandidate, CombatRule

COMBAT_SECTION = "combat"  # the character's combat settings, shared by every module
NO_ATTACK = "還沒設定攻擊方式：勾普攻，或選至少一個技能"

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


def attack_problem(rule: CombatRule) -> str | None:
    """Why `rule` cannot fight at all, or None."""
    if not rule.basic and not rule.opener and not rule.rotation:
        return NO_ATTACK
    return None


def skill_candidates(
    learned: dict[int, int], defs: dict[tuple[int, int], AttackSkill]
) -> list[AttackSkillCandidate]:
    """The learned skills that hit an enemy, for the combat pickers."""
    out = []
    for mid, level in sorted(learned.items()):
        d = defs.get((mid, level))
        if d is not None:
            out.append(
                AttackSkillCandidate(
                    magic_id=mid, level=level, name=d.name, mp=d.mp, area=d.area, gap_ms=d.gap_ms
                )
            )
    return out


class Fighter:
    """One character's fighting: the target, the rotation, and what the server
    said about our attacks (0x43 hits, 0x10 cast starts). The packet handlers
    run on the hook listener's thread, `tick` on the module's run thread."""

    def __init__(
        self,
        rule: CombatRule,
        logger: logging.Logger | None = None,
        cat: str = "combat",
    ) -> None:
        self.rule = rule
        self._log = logger or logging.getLogger("tthol.combat")
        self._cat = cat
        self.lock = threading.Lock()
        self.rot = Rotation()
        self.learned: dict[int, int] = {}
        self.casts_seen = 0
        self.hits: dict[tuple[int, int], float] = {}  # target key -> clock of our last hit
        # target key -> magic id -> clock of our last hit with it (0 = basic attack)
        self.hit_skills: dict[tuple[int, int], dict[int, float]] = {}
        self.taken: dict[int, float] = {}  # magic id -> clock of our last cast start (0x10)
        self.last_taken: tuple[float, int] | None = None  # (clock, skill code), newest
        self.taken_seen: float = -1.0  # last_taken already folded into the rotation

    def reset(self) -> None:
        """A new fight area (room, floor): start over, keeping the per-skill margins."""
        self.rot = Rotation(margins=self.rot.margins)  # margins are per skill, not per area
        with self.lock:
            self.hits, self.hit_skills = {}, {}

    def on_attack(self, raw: bytes, own_key: bytes | None, now: float) -> None:
        """0x43 (an attack landing): note our own hits on each target."""
        if own_key is None or len(raw) < 0x1B or raw[11:21] != own_key:
            return
        # Keyed by (npc id, instance): the kind word differs between `near` and
        # 0x43 (near lists the tower's monsters as kind 11).
        target = struct.unpack_from("<II", raw, 3)
        magic = struct.unpack_from("<I", raw, 0x15)[0] // 100  # magic * 100 + level
        with self.lock:
            self.hits[target] = now
            self.hit_skills.setdefault(target, {})[magic] = now

    def on_cast(self, raw: bytes, own_key: bytes | None, now: float) -> None:
        """0x10 (a cast starting): the server took one of our casts."""
        if own_key is None or len(raw) < 15 or raw[1:11] != own_key:
            return
        code = struct.unpack_from("<I", raw, 11)[0]
        with self.lock:
            self.taken[code // 100] = now
            self.last_taken = (now, code)
        self._log.debug(
            "%s cast taken code=%d t=%.3f", self._cat, code, now, extra={"cat": self._cat}
        )

    def tick(
        self,
        live: list[dict],
        me: tuple[int, int],
        strength: dict[int, tuple],
        elites: frozenset[int],
        defs: dict[tuple[int, int], AttackSkill],
        mp: int,
        casts: int,
        now: float,
        send: Callable[[str], dict],
        note: Callable[[str, str], None],
        state=None,
    ) -> None:
        """One fight decision against `live` (`near` entries, non-empty): keep or
        pick the target, then send the next skill or the basic attack.

        me: own position (pixels); strength / elites: see pick_target / mobbed_by;
        defs: load_attack_skills();
        casts: the guard's buff cast count (a change pauses fighting); send: a hook
        command line -> its reply; note: (phase, text) for the module's log;
        state: the character state, for the cast log only.
        """
        rule, rot = self.rule, self.rot
        mx, my = me
        target = next((o for o in live if o["h"] == rot.target), None)
        if target is not None:
            swap = mobbed_by(target, live, me, rule, elites)
            if swap is not None:
                note("info", "小怪圍過來了，先打小怪再回頭打菁英")
                target = swap
                retarget(rot, target["h"], now)
        if target is None:
            target = pick_target(live, me, rule, strength)
            retarget(rot, target["h"], now)
            self._log.debug(
                "%s target new=%s npc=%s t=%.3f",
                self._cat,
                target["h"],
                target["id"],
                now,
                extra={"cat": self._cat},
            )
        if casts != self.casts_seen:
            self.casts_seen = casts
            rot.paused_until, rot.attacked = now + BUFF_PAUSE, False
        if now < rot.paused_until:
            return
        key = (target["id"], target["inst"])
        with self.lock:
            hit = self.hits.get(key)
            by_skill = dict(self.hit_skills.get(key, {}))
            taken, last_taken = dict(self.taken), self.last_taken
        if hit is not None:
            rot.last_hit = max(rot.last_hit, hit)
        if last_taken is not None and last_taken[0] > self.taken_seen:
            self.taken_seen = last_taken[0]
            code = last_taken[1]
            cast_taken(rot, defs.get((code // 100, code % 100)), last_taken[0], code // 100)
        if rot.pending is not None:
            mid = rot.pending[0]
            seen = max(taken.get(mid, -1.0), by_skill.get(mid, -1.0))
            if settle_cast(rot, seen if seen >= 0 else None, now) == "gave_up":
                name = next((d.name for (m, _l), d in defs.items() if m == mid), mid)
                note("unconfirmed", f"{name} 連續 4 次沒被伺服器接受，先放下一招")
        if now - rot.last_hit > RELOCK_AFTER:
            rot.attacked, rot.last_hit = False, now  # nothing landing: lock on again
        pick = next_skill(rule, rot, self.learned, defs, mp, now)
        if pick is not None:
            mid, level, _d = pick
            r = send(f"cast {mid * 100 + level} {target['h']}")
            self._log.debug(
                "%s cast sent code=%d t=%.3f ok=%s target=%s dist=%.1f state=%s retry=%d",
                self._cat,
                mid * 100 + level,
                now,
                r.get("ok"),
                target["h"],
                (((target["x"] - mx) ** 2 + (target["y"] - my) ** 2) ** 0.5) / TILE_PX,
                state,
                rot.drops,
                extra={"cat": self._cat},
            )
            if r.get("ok"):
                cast_sent(rot, mid, now)
            return
        if rule.basic and not rot.attacked:
            if send(f"attack {target['h']}").get("ok"):
                rot.attacked = True
