"""Standing guard: rules that only use items / skills and never move the character.

Runs while the user plays by hand. One thread per pid wakes on every own-HP/MP
packet (0x06, through services.hook_hub) and at least every EVENT_WAIT, and when
a rule fires sends commands through the hook's command pipe (services.hook_cmd).

Rule 1 is drinking, after the tower runs of 2026-10-04 (tthol-hook
local/scripts/tower.py): a drink is not waited on. Each sent `use` adds its
restore amount to a pending list for PENDING_WINDOW; the expected value is the
current value plus what is still pending, and the guard keeps drinking (up to
MAX_PER_PASS per pass) while that stays under the threshold. The deeper HP
falls, the more it drinks in one go, and the pending window is what stops it
from overshooting. The bag count dropping is only logged, never waited on.

Rule 2 is curing: a debuff group in HP+0x4C4 / +0x4C8 that a ticked cure item
clears gets that item used. Unlike a drink, a cure is confirmed by the group
leaving the array; it is retried after CURE_HOLD, at most CURE_TRIES times per
spell of the debuff.

Rule 3 keeps self buffs up: a ticked skill is cast on the character when its
buff is missing from the hook's buff list (services.buff_tracker) or ends
within BUFF_LEAD. One cast per pass, CAST_GAP apart; a cast whose buff never
shows up is retried CAST_TRIES times, then the skill rests for CAST_PAUSE.

Rule 4 keeps item buffs up (道具處置 "use_periodic"): every item whose buff is
missing from the hook's list is used in the same pass, like the battle puppet;
each item then holds and rests like a cast, with no gap between items.
Cure items (rule 2) are the ones set to "use_on_status" in the same table.

Settings are saved per character name in snapshots.db (character_settings:
"guard.potion", "guard.buff", "items"). The on/off switch is not saved: the
guard never starts by itself after a restart.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from reader import read_inventory, read_pet_inventory, read_skills, read_stage
from services._paths import app_root, bundled
from services.api_types import (
    BuffInfo,
    BuffSkillCandidate,
    GuardBuffRule,
    GuardConfig,
    GuardLogEntry,
    GuardPotionRule,
    GuardStartResult,
    GuardStatus,
    GuardVitals,
    ItemRule,
    ItemRuleCandidate,
    ItemRules,
    ItemRulesView,
    PotionCandidate,
)
from services.item_rules import (
    ITEMS_SECTION,
    USE_ON_STATUS,
    USE_PERIODIC,
    ItemFact,
    load_item_facts,
)
from services.hook_cmd import (
    CommandChannel,
    NoReply,
    PipeBusy,
    PipeGone,
    classify_error,
)

log = logging.getLogger("tthol.guard")

EVENT_WAIT = 0.05  # re-check this often even without a 0x06 packet
PENDING_WINDOW = 0.5  # a drink shows up in 0x06 after ~0.1-0.2 s; count it as pending this long
MAX_PER_PASS = 4  # drinks per resource per pass; the next 0x06 continues
EMPTY_SKIP = 5.0  # after "item not in bag", try the next potion for this long
LIVE_FRESH = 1.0  # a 0x06 value newer than this beats the memory read
CONFIRM_WINDOW = 2.0  # how long a drink may take to show up in the bag (log only)
LOG_KEEP = 100
GUARD_COMMANDS = ("use",)  # what the guard needs from the hook's `caps` manifest
BUFF_COMMANDS = ("cast", "status")  # what keeping buffs up needs on top
RESOURCE_LABEL = {"hp": "體力", "mp": "真氣"}
CURE_HOLD = 2.0  # after a cure is sent, give the debuff this long to go away
CURE_TRIES = 3  # cures per spell of a debuff before giving up on it
BUFF_LEAD = 3.0  # recast a buff that ends within this many seconds
CAST_GAP = 2.0  # at least this long between two casts
CAST_HOLD = 4.0  # after a cast, wait this long for its buff before casting it again
CAST_TRIES = 3  # casts in a row without the buff showing up before resting the skill
CAST_PAUSE = 60.0
SKILLS_EVERY = 30.0  # re-read the learned skills (levels go up) this often

# Own pose, from the broadcast 0x59 `59 <u16 pose> 00 <key 10B>`, and from
# the `pose` field of `status` (the action name, "Sit" when sitting). Skills cannot be cast
# sitting, so keeping buffs up waits while the character sits.
POSE_PACKET = 0x59
POSE_SIT = 0x0D
POSE_STAND = 0x09

# Own debuffs: count, then one status group per int32 (compacting array; the
# slots past count hold stale groups). Same layout in the compat layout.
DEBUFF_COUNT_OFF = 0x4C4
DEBUFF_ARRAY_OFF = 0x4C8
MAX_DEBUFFS = 32  # a larger count is a bad read, not a real character

# Backoff after a failure, in seconds.
WAIT_NOT_LOCATED = 1.0
WAIT_NO_PIPE = 2.0
WAIT_BUSY = 0.2
WAIT_NO_REPLY = 0.5
WAIT_DISPATCHER = 3.0
WAIT_NOT_LOADED = 10.0
WAIT_ERROR = 1.0


# ---- memory sample ---------------------------------------------------------


@dataclass(frozen=True)
class Sample:
    hp: int
    hp_max: int
    mp: int
    mp_max: int
    bag: dict[int, int]  # items.id -> total count over all stacks
    debuffs: tuple[int, ...] = ()  # status groups, in array order


def _bag_counts(items) -> dict[int, int]:
    counts: dict[int, int] = {}
    for item_id, qty in items or []:
        counts[item_id] = counts.get(item_id, 0) + max(qty, 0)
    return counts


def read_vitals(pm, hp_addr, compat_mode) -> tuple[int, int, int, int]:
    """(hp, hp_max, mp, mp_max), for WorkerManager.read_locked."""
    # Normal layout: cur HP, max HP, cur MP, max MP. The compat layout swaps each pair.
    a, b, c, d = struct.unpack("<4i", pm.read_bytes(hp_addr, 16))
    return (b, a, d, c) if compat_mode else (a, b, c, d)


def read_debuffs(pm, hp_addr) -> tuple[int, ...]:
    """Status groups of the debuffs on the character; () when the read looks wrong."""
    try:
        (count,) = struct.unpack("<i", pm.read_bytes(hp_addr + DEBUFF_COUNT_OFF, 4))
        if count <= 0 or count > MAX_DEBUFFS:
            return ()
        groups = struct.unpack(f"<{count}i", pm.read_bytes(hp_addr + DEBUFF_ARRAY_OFF, 4 * count))
    except Exception:
        return ()
    return tuple(g for g in groups if 0 < g < 1000)


def read_sample(pm, hp_addr, compat_mode) -> Sample | None:
    """HP / MP, bag counts and debuffs, for WorkerManager.read_locked."""
    hp, hp_max, mp, mp_max = read_vitals(pm, hp_addr, compat_mode)
    try:
        bag = read_inventory(pm, hp_addr)
    except ValueError:
        return None
    if bag is None:
        return None
    return Sample(hp, hp_max, mp, mp_max, _bag_counts(bag), read_debuffs(pm, hp_addr))


def read_holdings(pm, hp_addr, _compat_mode) -> tuple[dict[int, int], dict[int, int]] | None:
    try:
        bag = read_inventory(pm, hp_addr)
    except ValueError:
        return None  # the game is reallocating the bag; the picker asks again shortly
    if bag is None:
        return None
    try:
        pet = read_pet_inventory(pm, hp_addr) or []
    except ValueError:
        pet = []
    return _bag_counts(bag), _bag_counts(pet)


# ---- potions ---------------------------------------------------------------


# items.hp_flag / mp_flag, checked against the item texts: 0 restores points,
# 2 restores a share of the max; 1 / 3 raise the max (by points / percent) for a
# while. Those are buff items, not something to drink when low.
FLAG_POINTS, FLAG_SHARE = 0, 2
RESTORE_FLAGS = (FLAG_POINTS, FLAG_SHARE)


@dataclass(frozen=True)
class Potion:
    name: str
    hp: int = 0  # points, or percent of max when hp_share
    hp_share: bool = False
    mp: int = 0
    mp_share: bool = False

    @property
    def restores(self) -> str:
        return "both" if self.hp and self.mp else "hp" if self.hp else "mp"

    def amount(self, resource: str, cur_max: int) -> int:
        """Expected restore for one drink, from the DB values."""
        value, share = (self.hp, self.hp_share) if resource == "hp" else (self.mp, self.mp_share)
        return cur_max * value // 100 if share else value


def load_potions(db_path: Path | None = None) -> dict[int, Potion]:
    """items.id -> Potion for every potion that restores HP or MP."""
    path = db_path or bundled("tthol.sqlite")
    if not path.exists():
        return {}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.text_factory = lambda b: b.decode("utf-8", errors="replace")
    try:
        rows = con.execute(
            "SELECT id, name, hp, hp_flag, mp, mp_flag FROM items"
            " WHERE type_name = 'POTION' AND (hp > 0 OR mp > 0)"
        ).fetchall()
    finally:
        con.close()
    out = {}
    for item_id, name, hp, hp_flag, mp, mp_flag in rows:
        hp_ok = hp > 0 and hp_flag in RESTORE_FLAGS
        mp_ok = mp > 0 and mp_flag in RESTORE_FLAGS
        if hp_ok or mp_ok:
            out[item_id] = Potion(
                name,
                hp if hp_ok else 0,
                hp_flag == FLAG_SHARE,
                mp if mp_ok else 0,
                mp_flag == FLAG_SHARE,
            )
    return out


# ---- cure items ------------------------------------------------------------


@dataclass(frozen=True)
class Cure:
    name: str
    group: int  # the status group it clears
    status: str  # that group's name


def load_status_names(db_path: Path | None = None) -> dict[int, str]:
    """status group -> a name for it (the first status in the group)."""
    path = db_path or bundled("tthol.sqlite")
    if not path.exists():
        return {}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.text_factory = lambda b: b.decode("utf-8", errors="replace")
    try:
        rows = con.execute(
            'SELECT "group", name FROM status WHERE "group" > 0 ORDER BY "group", id'
        ).fetchall()
    finally:
        con.close()
    names: dict[int, str] = {}
    for group, name in rows:
        names.setdefault(group, name)
    return names


# ---- self-buff skills ----------------------------------------------------------


# Status groups that harm the one who has them; a skill giving one is not a self buff.
HOSTILE_GROUPS = frozenset({14, 15, 17, 18, 19, 20, 21, 22, 23, 38, 39, 65, 66, 68, 69, 70, 71, 72})
BUFF_TARGETS = {"TARGET_SELF": "self", "TARGET_ALLY": "ally", "TARGET_GROUP": "group"}


@dataclass(frozen=True)
class SelfBuff:
    name: str
    group: int
    status: str
    mp: int
    duration_ms: int
    target: str  # self / ally / group


def load_self_buffs(db_path: Path | None = None) -> dict[tuple[int, int], SelfBuff]:
    """(magic id, level) -> SelfBuff for every skill level that buffs its caster.

    TARGET_LOVE skills are left out: they need the partner.
    """
    path = db_path or bundled("tthol.sqlite")
    if not path.exists():
        return {}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.text_factory = lambda b: b.decode("utf-8", errors="replace")
    try:
        rows = con.execute(
            'SELECT m.id, m.level, m.name, m.target, m.spend_mp, m.time, s."group", s.name'
            " FROM magic m JOIN status s ON s.id = m.extra_status"
            " WHERE m.time > 0 AND m.target IN ('TARGET_SELF', 'TARGET_ALLY', 'TARGET_GROUP')"
        ).fetchall()
    finally:
        con.close()
    return {
        (mid, level): SelfBuff(name, group, status, mp or 0, ms, BUFF_TARGETS[target])
        for mid, level, name, target, mp, ms, group, status in rows
        if group not in HOSTILE_GROUPS
    }


def load_town_stages(db_path: Path | None = None) -> frozenset[int]:
    """Stage ids where fighting is off (STAGE_FLAG_NOFIGHT): towns, markets, halls.

    Like the battle puppet, this includes NOFIGHT maps that allow PK (成都城郊,
    迷路草原, 平行空間): the user chose to pause there too. Monster counts are no
    guide: about 125 fighting maps spawn theirs from map events only.
    """
    path = db_path or bundled("tthol.sqlite")
    if not path.exists():
        return frozenset()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.text_factory = lambda b: b.decode("utf-8", errors="replace")
    try:
        rows = con.execute("SELECT id, flag FROM stages").fetchall()
    finally:
        con.close()
    out = set()
    for stage_id, flag in rows:
        flags = set((flag or "").split(","))
        if "STAGE_FLAG_NOFIGHT" in flags:
            out.add(stage_id)
    return frozenset(out)


def read_stage_id(pm, _hp_addr, _compat_mode) -> tuple[int, str] | None:
    """(stage id, map name) for WorkerManager.read_locked."""
    return read_stage(pm)


def decode_pose(raw: bytes) -> tuple[int, bytes] | None:
    """(pose, key) of a 0x59 packet."""
    if len(raw) < 14 or raw[0] != POSE_PACKET:
        return None
    (pose,) = struct.unpack_from("<H", raw, 1)
    return pose, bytes(raw[4:14])


def read_learned(pm, hp_addr, _compat_mode) -> dict[int, int] | None:
    """magic id -> learned level, for WorkerManager.read_locked."""
    try:
        skills = read_skills(pm, hp_addr)
    except ValueError:
        return None
    return dict(skills) if skills is not None else None


# ---- rule: drinking (pure) -------------------------------------------------


@dataclass
class Outstanding:
    """Drinks of one item sent but not yet seen in the bag (for the log only)."""

    before: int
    sent: int
    at: float


@dataclass
class DrinkState:
    pending: dict[str, list[tuple[float, int]]] = field(
        default_factory=lambda: {"hp": [], "mp": []}
    )
    empty_until: dict[int, float] = field(default_factory=dict)
    outstanding: dict[int, Outstanding] = field(default_factory=dict)


def expected(state: DrinkState, resource: str, cur: int, now: float) -> int:
    """Current value plus restores still pending; drops expired entries."""
    live = [(t, a) for t, a in state.pending[resource] if t > now]
    state.pending[resource] = live
    return cur + sum(a for _, a in live)


def wants_drink(
    state: DrinkState, resource: str, cur: int, cur_max: int, pct: int, now: float
) -> bool:
    # cur <= 0: dead (or a bad read); a potion does nothing then.
    if cur <= 0 or cur_max <= 0:
        return False
    return expected(state, resource, cur, now) * 100 < pct * cur_max


def next_potion(items: list[int], bag: dict[int, int], state: DrinkState, now: float) -> int | None:
    """First whitelisted item the bag holds that is not resting after "item not in bag"."""
    for item_id in items:
        if bag.get(item_id, 0) > 0 and state.empty_until.get(item_id, 0.0) <= now:
            return item_id
    return None


def record_drink(
    state: DrinkState,
    item_id: int,
    potion: Potion | None,
    maxes: dict[str, int],
    bag_before: int,
    now: float,
) -> None:
    """A `use` went out (or may have): count its restore as pending on every resource it fills."""
    if potion is not None:
        for resource in ("hp", "mp"):
            amount = potion.amount(resource, maxes[resource])
            if amount > 0:
                state.pending[resource].append((now + PENDING_WINDOW, amount))
    o = state.outstanding.get(item_id)
    if o is None:
        state.outstanding[item_id] = Outstanding(bag_before, 1, now)
    else:
        o.sent += 1


def settle_bag(
    state: DrinkState, bag: dict[int, int], now: float
) -> list[tuple[int, int, int, bool]]:
    """(item, count before, count now, confirmed) for drinks the bag now shows, or that timed out."""
    out = []
    for item_id, o in list(state.outstanding.items()):
        count = bag.get(item_id, 0)
        if count > o.before:
            o.before = count  # refilled (e.g. from the pet bag): measure from here
        elif count < o.before:
            out.append((item_id, o.before, count, True))
            o.sent -= o.before - count
            o.before, o.at = count, now
            if o.sent <= 0:
                del state.outstanding[item_id]
            continue
        if now - o.at >= CONFIRM_WINDOW:
            out.append((item_id, o.before, count, False))
            del state.outstanding[item_id]
    return out


# ---- rule: curing (pure) --------------------------------------------------


@dataclass
class CureTry:
    """Cures sent for one spell of a debuff group."""

    tries: int = 0
    until: float = 0.0  # no new cure for this group before this
    gave_up: bool = False


@dataclass
class CureState:
    groups: dict[int, CureTry] = field(default_factory=dict)


def next_cure(
    debuffs: tuple[int, ...],
    items: list[int],
    cures: dict[int, Cure],
    bag: dict[int, int],
    state: CureState,
    empty_until: dict[int, float],
    now: float,
) -> tuple[int, int] | None:
    """(group, item) for the first debuff a ticked, held cure clears and is not on hold."""
    for group in dict.fromkeys(debuffs):
        t = state.groups.get(group)
        if t is not None and (t.tries >= CURE_TRIES or t.until > now):
            continue
        for item_id in items:
            cure = cures.get(item_id)
            if (
                cure is not None
                and cure.group == group
                and bag.get(item_id, 0) > 0
                and empty_until.get(item_id, 0.0) <= now
            ):
                return group, item_id
    return None


def record_cure(state: CureState, group: int, now: float) -> int:
    """A cure for `group` went out (or may have); returns the try number."""
    t = state.groups.setdefault(group, CureTry())
    t.tries += 1
    t.until = now + CURE_HOLD
    return t.tries


def settle_cures(state: CureState, debuffs: tuple[int, ...], now: float) -> list[tuple[int, bool]]:
    """(group, cleared) for spells that ended, or that ran out of tries."""
    out = []
    present = set(debuffs)
    for group, t in list(state.groups.items()):
        if group not in present:
            del state.groups[group]
            if not t.gave_up:
                out.append((group, True))
        elif not t.gave_up and t.tries >= CURE_TRIES and t.until <= now:
            t.gave_up = True
            out.append((group, False))
    return out


# ---- rule: keeping buffs up (pure) -----------------------------------------------


@dataclass
class SkillTry:
    tries: int = 0  # casts since the buff was last seen
    hold_until: float = 0.0
    paused_until: float = 0.0


@dataclass
class BuffState:
    next_cast: float = 0.0  # no cast at all before this
    skills: dict[int, SkillTry] = field(default_factory=dict)


def active_skills(buffs: list[BuffInfo]) -> dict[int, float | None]:
    """magic id -> expiry (unix s, None when unknown) for the skill buffs on the character."""
    out: dict[int, float | None] = {}
    for b in buffs:
        if b.code is not None and b.level is not None:  # level set = a skill code
            out[b.code // 100] = b.expires_at
    return out


def next_cast(
    ticked: list[int],
    learned: dict[int, int],
    defs: dict[tuple[int, int], SelfBuff],
    active: dict[int, float | None],
    mp: int,
    state: BuffState,
    now: float,
    wall: float,
) -> tuple[int, int] | None:
    """(magic id, level) of the first ticked buff to cast now, or None."""
    if now < state.next_cast:
        return None
    for mid in ticked:
        level = learned.get(mid)
        d = defs.get((mid, level)) if level else None
        if d is None or d.mp > mp:
            continue
        t = state.skills.get(mid)
        if t is not None and (t.hold_until > now or t.paused_until > now):
            continue
        if mid in active:
            expires = active[mid]
            if expires is None or expires - wall > BUFF_LEAD:
                continue
        return mid, level
    return None


def record_cast(state: BuffState, mid: int, now: float) -> bool:
    """A cast went out (or may have). True when the skill now rests (too many misses)."""
    state.next_cast = now + CAST_GAP
    t = state.skills.setdefault(mid, SkillTry())
    t.tries += 1
    t.hold_until = now + CAST_HOLD
    if t.tries > CAST_TRIES:
        t.tries = 0
        t.paused_until = now + CAST_PAUSE
        return True
    return False


def active_items(buffs: list[BuffInfo]) -> dict[int, float | None]:
    """items.id -> expiry for the item buffs on the character (level None = an item code)."""
    return {b.code: b.expires_at for b in buffs if b.code is not None and b.level is None}


def due_items(
    wanted: list[int],
    bag: dict[int, int],
    active: dict[int, float | None],
    state: BuffState,
    empty_until: dict[int, float],
    now: float,
    wall: float,
) -> list[int]:
    """Every use_periodic item to use now: held, not resting, its buff missing or ending.

    All of them go in one pass, like the battle puppet (several 0x29 in the
    same instant): an item has no cast animation, so there is no gap between
    different items, only the per-item hold and rest.
    """
    out = []
    for item_id in wanted:
        if bag.get(item_id, 0) <= 0 or empty_until.get(item_id, 0.0) > now:
            continue
        t = state.skills.get(item_id)
        if t is not None and (t.hold_until > now or t.paused_until > now):
            continue
        if item_id in active:
            expires = active[item_id]
            if expires is None or expires - wall > BUFF_LEAD:
                continue
        out.append(item_id)
    return out


def settle_casts(state: BuffState, active: dict[int, float | None], wall: float) -> list[int]:
    """Skills whose buff is on (and not about to end) again: reset their tries."""
    out = []
    for mid, t in state.skills.items():
        if t.tries and mid in active:
            expires = active[mid]
            if expires is None or expires - wall > BUFF_LEAD:
                t.tries = 0
                out.append(mid)
    return out


# ---- settings --------------------------------------------------------------


POTION_SECTION = "guard.potion"
BUFF_SECTION = "guard.buff"


def _legacy_store_path() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) / "御心鑒" if appdata else app_root()
    return base / "guard.json"


class MemorySettings:
    """get_setting / set_setting in memory: GuardStore's default and the tests' store."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], dict] = {}

    def get_setting(self, character: str, section: str) -> dict | None:
        data = self._data.get((character, section))
        return json.loads(json.dumps(data)) if data is not None else None

    def set_setting(self, character: str, section: str, data: dict) -> None:
        self._data[(character, section)] = json.loads(json.dumps(data))


class GuardStore:
    """Guard settings and 道具處置 per character name, in the settings table.

    `db` is anything with get_setting / set_setting (SnapshotDB). A section that
    does not validate reads as its defaults.
    """

    def __init__(self, db=None) -> None:
        self._db = db if db is not None else MemorySettings()

    def _section(self, name: str, section: str, model):
        raw = self._db.get_setting(name, section)
        try:
            return model.model_validate(raw) if raw else model()
        except ValueError:
            log.warning(
                "settings %s for %s are invalid; using defaults",
                section,
                name,
                extra={"cat": "guard"},
            )
            return model()

    def load(self, name: str) -> GuardConfig:
        return GuardConfig(
            potion=self._section(name, POTION_SECTION, GuardPotionRule),
            buff=self._section(name, BUFF_SECTION, GuardBuffRule),
        )

    def save(self, name: str, config: GuardConfig) -> None:
        self._db.set_setting(name, POTION_SECTION, config.potion.model_dump())
        self._db.set_setting(name, BUFF_SECTION, config.buff.model_dump())

    def load_items(self, name: str) -> ItemRules:
        return self._section(name, ITEMS_SECTION, ItemRules)

    def save_items(self, name: str, rules: ItemRules) -> None:
        self._db.set_setting(name, ITEMS_SECTION, rules.model_dump(mode="json"))


def migrate_legacy_store(db, path: Path | None = None) -> int:
    """Move %APPDATA%\\御心鑒\\guard.json (before 2026-10-05) into the settings table.

    Sections already in the table win. The old cure list becomes
    "use_on_status" rules. The file is renamed to guard.json.migrated, so this
    runs once. Returns how many characters were moved.
    """
    path = path or _legacy_store_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    moved = 0
    for name, cfg in (data if isinstance(data, dict) else {}).items():
        if not isinstance(cfg, dict):
            continue
        for key, section in (("potion", POTION_SECTION), ("buff", BUFF_SECTION)):
            if isinstance(cfg.get(key), dict) and db.get_setting(name, section) is None:
                db.set_setting(name, section, cfg[key])
        cure = (cfg.get("cure") or {}).get("items") or []
        if cure and db.get_setting(name, ITEMS_SECTION) is None:
            rules = ItemRules(items={int(i): ItemRule(action=USE_ON_STATUS) for i in cure})
            db.set_setting(name, ITEMS_SECTION, rules.model_dump(mode="json"))
        moved += 1
    try:
        os.replace(path, path.with_name(path.name + ".migrated"))
    except OSError:
        log.warning("could not rename %s after migrating it", path, extra={"cat": "guard"})
    log.info("guard settings migrated from %s: %d characters", path, moved, extra={"cat": "guard"})
    return moved


# ---- manager ---------------------------------------------------------------


def cmd_pipe_present(pid: int) -> bool:
    try:
        return f"tthol-cmd-{pid}" in os.listdir("\\\\.\\pipe\\")
    except OSError:
        return False


@dataclass
class _LogLine:
    """One guard log line. A drink line is updated in place as the bag confirms it."""

    id: int
    ts: float
    rule: str
    phase: str
    text: str
    # items.id -> [drinks not yet seen in the bag, count before, count now, timed out]
    bag: dict[int, list] = field(default_factory=dict)

    def refresh(self) -> None:
        if not self.bag:
            return
        if all(slot[0] <= 0 for slot in self.bag.values()):
            self.phase = "confirmed"
        elif any(slot[3] for slot in self.bag.values()):
            self.phase = "unconfirmed"

    def entry(self, name_of: Callable[[int], str]) -> GuardLogEntry:
        text = self.text
        moved = [(i, slot) for i, slot in self.bag.items() if slot[2] != slot[1]]
        if len(self.bag) == 1 and moved:
            _, slot = moved[0]
            text += f"（背包 {slot[1]} → {slot[2]}）"
        elif moved:
            text += (
                "（背包 "
                + "、".join(f"{name_of(i)} {slot[1]} → {slot[2]}" for i, slot in moved)
                + "）"
            )
        if self.phase == "unconfirmed":
            text += (
                f"（{CONFIRM_WINDOW:g} 秒內背包數量沒變）" if not moved else "（部分沒在背包看到）"
            )
        return GuardLogEntry(id=self.id, ts=self.ts, rule=self.rule, text=text, phase=self.phase)


class _Run:
    def __init__(self, name: str, config: GuardConfig, items: ItemRules | None = None) -> None:
        self.name = name
        self.config = config
        self.items = items or ItemRules()
        self.item_state = BuffState()  # 定期使用, timed like casts
        self.item_lines: dict[int, _LogLine] = {}  # items.id -> its open use line
        self.uses = 0
        self.stop = threading.Event()
        self.wake = threading.Event()  # set by every own 0x06
        self.thread: threading.Thread | None = None
        self.state = DrinkState()
        self.live: tuple[int, int, float] | None = None  # (hp, mp, clock) from the last 0x06
        self.log: deque[_LogLine] = deque(maxlen=LOG_KEEP)
        self.next_id = 0
        # items.id -> drink lines still waiting for the bag, oldest first
        self.awaiting: dict[int, deque[_LogLine]] = {}
        self.buff_state = BuffState()
        self.buff_lines: dict[int, _LogLine] = {}  # magic id -> its open cast line
        self.learned: dict[int, int] = {}
        self.learned_at = -SKILLS_EVERY
        self.buff_unsupported = False  # noted once that the hook cannot cast
        self.pose: int | None = None  # last own pose seen; None = not known (taken as standing)
        self.sit_noted = False
        self.town_noted: int | None = None  # stage id the "in town" note was made for
        self.casts = 0
        self.cure_state = CureState()
        self.cure_lines: dict[int, _LogLine] = {}  # status group -> its open cure line
        self.debuffs: tuple[int, ...] = ()
        self.problem: str | None = None
        self.drinks = 0
        self.cures = 0
        self.lock = threading.Lock()


class _Stop(Exception):
    """Ends a drinking pass early; carries how long the loop should back off."""

    def __init__(self, wait: float) -> None:
        self.wait = wait


class GuardManager:
    def __init__(
        self,
        read_locked: Callable[[int, Callable], object],
        character_name: Callable[[int], str | None],
        channel: CommandChannel | None = None,
        store: GuardStore | None = None,
        potions: Callable[[], dict[int, Potion]] = load_potions,
        item_facts: Callable[[int], ItemFact | None] | None = None,
        status_names: Callable[[], dict[int, str]] = load_status_names,
        self_buffs: Callable[[], dict[tuple[int, int], SelfBuff]] = load_self_buffs,
        towns: Callable[[], frozenset[int]] = load_town_stages,
        buffs: Callable[[int], list[BuffInfo] | None] = lambda _pid: None,
        skill_icon: Callable[[int, int], str | None] = lambda _m, _l: None,
        icon_url: Callable[[int], str | None] = lambda _id: None,
        pipe_present: Callable[[int], bool] = cmd_pipe_present,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self._read_locked = read_locked
        self._character_name = character_name
        self._channel = channel or CommandChannel()
        self._store = store or GuardStore()
        self._load_potions = potions
        self._potions: dict[int, Potion] | None = None
        self._facts = item_facts or load_item_facts()
        self._load_status_names = status_names
        self._status_names: dict[int, str] | None = None
        self._load_self_buffs = self_buffs
        self._self_buffs: dict[tuple[int, int], SelfBuff] | None = None
        self._load_towns = towns
        self._towns: frozenset[int] | None = None
        self._buffs = buffs
        self._skill_icon = skill_icon
        # pid -> the hook's command names from `caps`; read on start
        self._caps: dict[int, frozenset[str]] = {}
        self._icon_url = icon_url
        self._pipe_present = pipe_present
        self._clock = clock
        self._wall = wall
        self._runs: dict[int, _Run] = {}
        self._lock = threading.Lock()

    # -- API -----------------------------------------------------------------

    def start(self, pid: int) -> GuardStartResult:
        if os.environ.get("TTHOL_NO_HOOK") == "1":
            return GuardStartResult(ok=False, reason="hook 已被 TTHOL_NO_HOOK 停用")
        name = self._character_name(pid)
        if not name:
            return GuardStartResult(ok=False, reason="角色還沒定位")
        if not self._pipe_present(pid):
            return GuardStartResult(ok=False, reason="這個遊戲視窗沒有 hook 指令通道")
        reason = self._check_caps(pid)
        if reason:
            return GuardStartResult(ok=False, reason=reason)
        with self._lock:
            run = self._runs.get(pid)
            if run is not None and run.thread is not None and run.thread.is_alive():
                if not run.stop.is_set():
                    return GuardStartResult(ok=True)
                run.thread.join(timeout=2.0)  # a stop is still winding down: let it end first
            run = _Run(name, self._store.load(name), self._store.load_items(name))
            self._runs[pid] = run
            run.thread = threading.Thread(
                target=self._loop, args=(pid, run), daemon=True, name=f"guard-{pid}"
            )
            run.thread.start()
        log.info("guard started pid=%d", pid, extra={"cat": "guard"})
        return GuardStartResult(ok=True)

    def _check_caps(self, pid: int) -> str | None:
        """Read the hook's manifest; why the guard cannot run on it, or None."""
        try:
            reply = self._channel.send(pid, "caps")
        except PipeGone:
            return "找不到 hook 指令通道（遊戲關了，或 hook 沒有注入）"
        except PipeBusy:
            return "指令通道正被其他程式使用，請稍後再試"
        except NoReply:
            return "hook 沒有回應指令清單，請稍後再試"
        commands = reply.get("commands") if reply.get("ok") else None
        if not isinstance(commands, list):
            # An older hook answers `caps` with a usage error: it has no manifest.
            return "這個 hook 版本沒有指令清單（caps），請先更新 hook"
        names = frozenset(c.get("cmd") for c in commands if isinstance(c, dict))
        with self._lock:
            self._caps[pid] = names
        missing = [c for c in GUARD_COMMANDS if c not in names]
        if missing:
            return f"這個 hook 沒有守護要用的指令：{'、'.join(missing)}"
        return None

    def _has_commands(self, pid: int, commands: tuple[str, ...]) -> bool:
        with self._lock:
            names = self._caps.get(pid)
        return names is not None and all(c in names for c in commands)

    def _hook_ready(self, pid: int) -> bool:
        if not self._pipe_present(pid):
            return False
        with self._lock:
            names = self._caps.get(pid)
        return names is None or all(c in names for c in GUARD_COMMANDS)

    def stop(self, pid: int) -> None:
        with self._lock:
            run = self._runs.get(pid)
        if run is not None:
            run.stop.set()
            run.wake.set()

    def shutdown(self) -> None:
        with self._lock:
            runs = list(self._runs.values())
        for run in runs:
            run.stop.set()
            run.wake.set()

    def on_vitals(self, pid: int, hp: int, mp: int) -> None:
        """Own HP / MP from a 0x06 packet (HookHub listener): re-check right away."""
        with self._lock:
            run = self._runs.get(pid)
        if run is not None:
            run.live = (hp, mp, self._clock())
            run.wake.set()

    def on_pose_packet(self, pid: int, raw: bytes, _ts: float, own_key: bytes | None) -> None:
        """A 0x59 from the event pipe (HookHub packet listener): track the own pose."""
        decoded = decode_pose(raw)
        if decoded is None or own_key is None or decoded[1] != own_key:
            return
        with self._lock:
            run = self._runs.get(pid)
        if run is not None:
            run.pose = decoded[0]
            run.wake.set()

    def config(self, pid: int) -> GuardConfig | None:
        name = self._character_name(pid)
        return self._store.load(name) if name else None

    def set_config(self, pid: int, config: GuardConfig) -> GuardConfig | None:
        name = self._character_name(pid)
        if not name:
            return None
        self._store.save(name, config)
        with self._lock:
            run = self._runs.get(pid)
        if run is not None and run.name == name:
            run.config = config
        return config

    def items(self, pid: int) -> ItemRules | None:
        name = self._character_name(pid)
        return self._store.load_items(name) if name else None

    def set_items(self, pid: int, rules: ItemRules) -> ItemRules | None:
        name = self._character_name(pid)
        if not name:
            return None
        self._store.save_items(name, rules)
        with self._lock:
            run = self._runs.get(pid)
        if run is not None and run.name == name:
            run.items = rules
        return rules

    def reload_settings(self, name: str) -> None:
        """Settings for `name` changed outside this manager (copied): reload running guards."""
        with self._lock:
            runs = [r for r in self._runs.values() if r.name == name]
        for run in runs:
            run.config = self._store.load(name)
            run.items = self._store.load_items(name)

    def item_view(self, pid: int, extra: set[int] | None = None) -> ItemRulesView:
        """The 道具處置 table: held items (bag and pet bag), items with a rule, and `extra`."""
        name = self._character_name(pid)
        rules = self._store.load_items(name) if name else ItemRules()
        held = self._read_locked(pid, read_holdings) if name else None
        bag, pet = held if held is not None else ({}, {})
        active = active_items(self._buffs(pid) or [])
        out = []
        for item_id in sorted(set(bag) | set(pet) | set(rules.items) | (extra or set())):
            fact = self._facts(item_id)
            if fact is None:
                continue
            periodic = fact.periodic
            out.append(
                ItemRuleCandidate(
                    item_id=item_id,
                    name=fact.name,
                    bag=bag.get(item_id, 0),
                    pet=pet.get(item_id, 0),
                    actions=fact.actions,
                    effect=fact.effect,
                    active=(item_id in active) if periodic else None,
                    expires_at=active.get(item_id) if periodic else None,
                    icon_url=self._icon_url(item_id),
                )
            )
        return ItemRulesView(character=name, rules=rules, candidates=out)

    def status(self, pid: int) -> GuardStatus:
        name = self._character_name(pid)
        config = self._store.load(name) if name else GuardConfig()
        with self._lock:
            run = self._runs.get(pid)
        running = (
            run is not None
            and run.thread is not None
            and run.thread.is_alive()
            and not run.stop.is_set()
        )
        vitals = self._vitals(pid) if name else None
        hook_cmd = self._hook_ready(pid)
        if run is None:
            return GuardStatus(
                running=False,
                hook_cmd=hook_cmd,
                character=name,
                config=config,
                vitals=vitals,
            )
        with run.lock:
            entries = [line.entry(self._item_name) for line in run.log]
            problem, drinks, cures, debuffs = run.problem, run.drinks, run.cures, run.debuffs
            casts, uses = run.casts, run.uses
        return GuardStatus(
            running=running,
            hook_cmd=hook_cmd,
            character=name,
            problem=problem if running else None,
            drinks=drinks,
            cures=cures,
            casts=casts,
            uses=uses,
            debuffs=[self._status_name(g) for g in dict.fromkeys(debuffs)] if running else [],
            log=entries[::-1],
            config=config,
            vitals=vitals,
        )

    def _vitals(self, pid: int) -> GuardVitals | None:
        try:
            v = self._read_locked(pid, read_vitals)
            if not v:
                return None
            hp, hp_max, mp, mp_max = v
        except Exception:
            return None  # not located, or a read that raced a map change
        return GuardVitals(hp=hp, hp_max=hp_max, mp=mp, mp_max=mp_max)

    def potions(self, pid: int) -> list[PotionCandidate]:
        """Potions in the bag or pet bag that restore HP or MP."""
        held = self._read_locked(pid, read_holdings)
        if held is None:
            return []
        bag, pet = held
        catalog = self._catalog()
        out = []
        for item_id in sorted(set(bag) | set(pet)):
            potion = catalog.get(item_id)
            if potion is None:
                continue
            out.append(
                PotionCandidate(
                    item_id=item_id,
                    name=potion.name,
                    restores=potion.restores,
                    bag=bag.get(item_id, 0),
                    pet=pet.get(item_id, 0),
                    icon_url=self._icon_url(item_id),
                )
            )
        return out

    def buff_candidates(self, pid: int) -> list[BuffSkillCandidate]:
        """Learned skills that buff the character, with whether each is on now."""
        learned = self._read_locked(pid, read_learned)
        if not learned:
            return []
        defs = self._self_buff_defs()
        active = active_skills(self._buffs(pid) or [])
        out = []
        for mid, level in sorted(learned.items()):
            d = defs.get((mid, level))
            if d is None:
                continue
            out.append(
                BuffSkillCandidate(
                    magic_id=mid,
                    level=level,
                    name=d.name,
                    status=d.status,
                    group=d.group,
                    mp=d.mp,
                    duration_s=d.duration_ms // 1000,
                    target=d.target,
                    active=mid in active,
                    expires_at=active.get(mid),
                    icon_url=self._skill_icon(mid, level),
                )
            )
        return out

    # -- loop ----------------------------------------------------------------

    def _catalog(self) -> dict[int, Potion]:
        if self._potions is None:
            self._potions = self._load_potions()
        return self._potions

    def _self_buff_defs(self) -> dict[tuple[int, int], SelfBuff]:
        if self._self_buffs is None:
            self._self_buffs = self._load_self_buffs()
        return self._self_buffs

    def _cures(self, run: _Run) -> tuple[list[int], dict[int, Cure]]:
        """The run's use_on_status items and what each one clears."""
        items, cures = [], {}
        for item_id, rule in run.items.items.items():
            fact = self._facts(item_id) if rule.action == USE_ON_STATUS else None
            if fact is not None and fact.cure_group is not None:
                items.append(item_id)
                cures[item_id] = Cure(
                    fact.name, fact.cure_group, self._status_name(fact.cure_group)
                )
        return items, cures

    def _status_name(self, group: int) -> str:
        if self._status_names is None:
            self._status_names = self._load_status_names()
        return self._status_names.get(group, f"狀態 {group}")

    def _item_name(self, item_id: int) -> str:
        potion = self._catalog().get(item_id)
        if potion:
            return potion.name
        fact = self._facts(item_id)
        return fact.name if fact else f"#{item_id}"

    def _note(
        self,
        run: _Run,
        phase: str,
        text: str,
        rule: str = "potion",
        bag: dict[int, list] | None = None,
    ) -> _LogLine:
        with run.lock:
            run.next_id += 1
            line = _LogLine(run.next_id, self._wall(), rule, phase, text, bag or {})
            run.log.append(line)
            for item_id in line.bag:
                run.awaiting.setdefault(item_id, deque()).append(line)
            return line

    def _set_problem(self, run: _Run, problem: str | None) -> None:
        with run.lock:
            run.problem = problem

    def _loop(self, pid: int, run: _Run) -> None:
        self._note(run, "info", "守護開始", rule="guard")
        try:
            while not run.stop.is_set():
                wait = self._tick(pid, run)
                if wait > EVENT_WAIT:
                    run.stop.wait(wait)  # backing off: packets must not cut the wait short
                else:
                    run.wake.wait(wait)
                run.wake.clear()
        except Exception:  # pragma: no cover - keep the reason visible instead of a dead thread
            log.exception("guard loop crashed pid=%d", pid, extra={"cat": "guard"})
            self._note(run, "error", "守護發生錯誤而停止，詳見記錄檔", rule="guard")
        finally:
            run.stop.set()
            self._note(run, "info", "守護停止", rule="guard")
            log.info("guard stopped pid=%d", pid, extra={"cat": "guard"})

    def _tick(self, pid: int, run: _Run) -> float:
        """One pass; returns how long to wait before the next."""
        name = self._character_name(pid)
        if name != run.name:
            if name is None:
                self._set_problem(run, "角色還沒定位")
                return WAIT_NOT_LOCATED
            run.name, run.config, run.items, run.state, run.live = (
                name,
                self._store.load(name),
                self._store.load_items(name),
                DrinkState(),
                None,
            )
            run.item_state, run.item_lines = BuffState(), {}
            run.awaiting = {}
            run.cure_state, run.cure_lines = CureState(), {}
            run.buff_state, run.buff_lines, run.learned_at = BuffState(), {}, -SKILLS_EVERY
        try:
            sample = self._read_locked(pid, read_sample)
        except Exception:
            sample = None
        if sample is None:
            self._set_problem(run, "讀不到角色資料")
            return WAIT_NOT_LOCATED
        now = self._clock()
        hp, mp = sample.hp, sample.mp
        live = run.live
        if live is not None and now - live[2] < LIVE_FRESH:
            hp, mp = live[0], live[1]  # the packet is newer than what memory shows yet
        self._log_bag(run, sample, now)
        self._log_cures(run, sample.debuffs, now)
        with run.lock:
            run.debuffs = sample.debuffs

        rule = run.config.potion
        maxes = {"hp": sample.hp_max, "mp": sample.mp_max}
        bag = dict(sample.bag)
        try:
            if hp > 0:  # dead: nothing to drink for, nothing to cure
                # HP first (staying alive), then debuffs, then MP.
                self._drink_up(pid, run, "hp", hp, rule.hp_pct, rule.hp_items, maxes, bag, now)
                self._cure(pid, run, sample.debuffs, bag, now)
                self._drink_up(pid, run, "mp", mp, rule.mp_pct, rule.mp_items, maxes, bag, now)
                self._keep_buffs(pid, run, mp, now)
                self._keep_items(pid, run, bag, now)
        except _Stop as stop:
            return stop.wait
        self._set_problem(run, None)
        return EVENT_WAIT

    def _drink_up(
        self,
        pid: int,
        run: _Run,
        resource: str,
        cur: int,
        pct: int,
        items: list[int],
        maxes: dict[str, int],
        bag: dict[int, int],
        now: float,
    ) -> None:
        """Drink while the expected value stays under the threshold, up to MAX_PER_PASS."""
        cur_max = maxes[resource]
        drunk: dict[int, list] = {}  # items.id -> [drinks, bag count before the first]
        potions = self._catalog()
        share = cur * 100 // max(cur_max, 1)
        try:
            for _ in range(MAX_PER_PASS):
                if not wants_drink(run.state, resource, cur, cur_max, pct, now):
                    return
                item_id = next_potion(items, bag, run.state, now)
                if item_id is None:
                    return
                before = bag.get(item_id, 0)

                def sent(item_id=item_id, before=before) -> None:
                    record_drink(run.state, item_id, potions.get(item_id), maxes, before, now)
                    bag[item_id] = before - 1

                if self._use(
                    pid, run, item_id, f"喝 {self._item_name(item_id)}", "potion", sent, now
                ):
                    drunk.setdefault(item_id, [0, before])[0] += 1
                    with run.lock:
                        run.drinks += 1
        finally:
            if drunk:
                what = "、".join(f"{self._item_name(i)} ×{n}" for i, (n, _b) in drunk.items())
                self._note(
                    run,
                    "sent",
                    f"喝 {what}（{RESOURCE_LABEL[resource]} {share}%）",
                    bag={i: [n, b, b, False] for i, (n, b) in drunk.items()},
                )

    def _use(
        self,
        pid: int,
        run: _Run,
        item_id: int,
        what: str,
        rule: str,
        on_sent: Callable[[], None],
        now: float,
    ) -> bool:
        """Send one `use`. True when it went out; raises _Stop to end the pass.

        `on_sent` runs when the use went out, and also when it may have (no
        reply): it can still run on the game thread, so it is held like a sent
        one rather than repeated straight away.
        """
        try:
            reply = self._channel.send(pid, f"use {item_id}")
        except PipeGone:
            with self._lock:
                self._caps.pop(pid, None)  # the next hook may be another build
            self._set_problem(run, "找不到 hook 指令通道（遊戲關了，或 hook 沒有注入）")
            raise _Stop(WAIT_NO_PIPE)
        except PipeBusy:
            self._set_problem(run, "指令通道正被其他程式使用")
            raise _Stop(WAIT_BUSY)
        except NoReply as e:
            on_sent()
            self._note(run, "error", f"{what}：hook 沒有回應（{e}）", rule=rule)
            raise _Stop(WAIT_NO_REPLY)
        code = classify_error(reply)
        if code is None:
            on_sent()
            return True
        if code == "item_not_in_bag":
            run.state.empty_until[item_id] = now + EMPTY_SKIP
            self._note(
                run,
                "info",
                f"{self._item_name(item_id)}：遊戲說背包裡沒有，{EMPTY_SKIP:g} 秒內先不用它",
                rule=rule,
            )
            return False
        if code == "actions_not_loaded":
            self._set_problem(run, "hook 的動作模組還沒載入，請在 hook 端載入後再試")
            raise _Stop(WAIT_NOT_LOADED)
        if code == "dispatcher_timeout":
            # The hook took the command back unrun, so a retry cannot double-use.
            self._note(run, "error", f"{what}：遊戲沒有回應（視窗可能最小化）", rule=rule)
            raise _Stop(WAIT_DISPATCHER)
        self._note(run, "error", f"{what}：{reply.get('error', '未知錯誤')}", rule=rule)
        raise _Stop(WAIT_ERROR)

    def _cure(
        self,
        pid: int,
        run: _Run,
        debuffs: tuple[int, ...],
        bag: dict[int, int],
        now: float,
    ) -> None:
        """Use a use_on_status item for each debuff it clears, one try per group per pass."""
        items, cures = self._cures(run)
        if not items:
            return
        for _ in range(MAX_PER_PASS):
            pick = next_cure(debuffs, items, cures, bag, run.cure_state, run.state.empty_until, now)
            if pick is None:
                return
            group, item_id = pick
            name = self._status_name(group)
            before = bag.get(item_id, 0)
            tried: list[int] = []

            def sent(group=group, item_id=item_id, before=before) -> None:
                tried.append(record_cure(run.cure_state, group, now))
                bag[item_id] = before - 1

            used = self._use(
                pid, run, item_id, f"解{name}（{self._item_name(item_id)}）", "cure", sent, now
            )
            if not used:
                continue  # not in the bag after all: next_cure skips it now
            with run.lock:
                run.cures += 1
                line = run.cure_lines.get(group)
                if line is None:
                    run.next_id += 1
                    line = _LogLine(run.next_id, self._wall(), "cure", "sent", "")
                    run.log.append(line)
                    run.cure_lines[group] = line
                line.text = f"{name} → 用 {self._item_name(item_id)}" + (
                    f" ×{tried[0]}" if tried and tried[0] > 1 else ""
                )

    def _keep_buffs(self, pid: int, run: _Run, mp: int, now: float) -> None:
        """Cast one ticked self buff that is missing or about to end."""
        ticked = run.config.buff.skills
        if not ticked:
            return
        buffs = self._buffs(pid)
        if buffs is None:
            return  # the hook does not track buffs yet: nothing to judge by
        if not self._has_commands(pid, BUFF_COMMANDS):
            if not run.buff_unsupported:
                run.buff_unsupported = True
                self._note(
                    run, "error", "這個 hook 沒有 buff 維持要用的指令：cast、status", rule="buff"
                )
            return
        if run.pose == POSE_SIT:
            if not run.sit_noted:
                run.sit_noted = True
                self._note(run, "info", "坐著放不了技能，站起來後再補 buff", rule="buff")
            return
        run.sit_noted = False
        if self._in_town(pid, run):
            return
        wall = self._wall()
        active = active_skills(buffs)
        if now - run.learned_at >= SKILLS_EVERY:
            try:
                run.learned = self._read_locked(pid, read_learned) or run.learned
            except Exception:
                pass
            run.learned_at = now
        with run.lock:
            for mid in settle_casts(run.buff_state, active, wall):
                line = run.buff_lines.pop(mid, None)
                if line is not None:
                    line.phase = "confirmed"
                    line.text += "（已生效）"
        defs = self._self_buff_defs()
        pick = next_cast(ticked, run.learned, defs, active, mp, run.buff_state, now, wall)
        if pick is None:
            return
        mid, level = pick
        d = defs[(mid, level)]
        what = f"補 {d.name} Lv{level}"
        handle = self._own_handle(pid, run, what)
        if handle is None:
            return
        rested: list[bool] = []

        def sent() -> None:
            rested.append(record_cast(run.buff_state, mid, now))

        if not self._send_cast(pid, run, f"cast {mid * 100 + level} {handle}", what, sent):
            return
        with run.lock:
            run.casts += 1
            line = run.buff_lines.get(mid)
            if line is None:
                run.next_id += 1
                line = _LogLine(run.next_id, self._wall(), "buff", "sent", what)
                run.log.append(line)
                run.buff_lines[mid] = line
            tries = run.buff_state.skills[mid].tries
            if rested and rested[0]:
                run.buff_lines.pop(mid, None)
                line.phase = "unconfirmed"
                line.text = f"{what}（放了 {CAST_TRIES + 1} 次都沒生效，{CAST_PAUSE:g} 秒後再試）"
            elif tries > 1:
                line.text = f"{what} ×{tries}"

    def _in_town(self, pid: int, run: _Run) -> bool:
        """On a no-fight map (town, market, hall); noted once per map."""
        try:
            stage = self._read_locked(pid, read_stage_id)
        except Exception:
            stage = None
        if self._towns is None:
            self._towns = self._load_towns()
        if stage is not None and stage[0] in self._towns:
            if run.town_noted != stage[0]:
                run.town_noted = stage[0]
                self._note(
                    run,
                    "info",
                    f"在{stage[1]}（不能戰鬥的地圖），不補 buff、不用定期道具",
                    rule="buff",
                )
            return True
        run.town_noted = None
        return False

    def _keep_items(self, pid: int, run: _Run, bag: dict[int, int], now: float) -> None:
        """Use every use_periodic item whose buff is missing or about to end."""
        wanted = [i for i, r in run.items.items.items() if r.action == USE_PERIODIC]
        if not wanted:
            return
        buffs = self._buffs(pid)
        if buffs is None or self._in_town(pid, run):
            return
        wall = self._wall()
        active = active_items(buffs)
        with run.lock:
            for item_id in settle_casts(run.item_state, active, wall):
                line = run.item_lines.pop(item_id, None)
                if line is not None:
                    line.phase = "confirmed"
                    line.text += "（已生效）"
        for item_id in due_items(
            wanted, bag, active, run.item_state, run.state.empty_until, now, wall
        ):
            self._use_periodic(pid, run, item_id, bag, now)

    def _use_periodic(self, pid: int, run: _Run, item_id: int, bag: dict[int, int], now: float):
        name = self._item_name(item_id)
        before = bag.get(item_id, 0)
        rested: list[bool] = []

        def sent() -> None:
            rested.append(record_cast(run.item_state, item_id, now))
            bag[item_id] = before - 1

        if not self._use(pid, run, item_id, f"用 {name}", "item", sent, now):
            return
        with run.lock:
            run.uses += 1
            line = run.item_lines.get(item_id)
            if line is None:
                run.next_id += 1
                line = _LogLine(run.next_id, self._wall(), "item", "sent", f"用 {name}")
                run.log.append(line)
                run.item_lines[item_id] = line
            tries = run.item_state.skills[item_id].tries
            if rested and rested[0]:
                run.item_lines.pop(item_id, None)
                line.phase = "unconfirmed"
                line.text = (
                    f"用 {name}（用了 {CAST_TRIES + 1} 次都沒生效，{CAST_PAUSE:g} 秒後再試）"
                )
            elif tries > 1:
                line.text = f"用 {name} ×{tries}"

    def _own_handle(self, pid: int, run: _Run, what: str) -> int | None:
        """The character's own handle for `cast`, from `status` (changes on a map change)."""
        try:
            reply = self._channel.send(pid, "status")
        except PipeGone:
            self._set_problem(run, "找不到 hook 指令通道（遊戲關了，或 hook 沒有注入）")
            raise _Stop(WAIT_NO_PIPE)
        except (PipeBusy, NoReply):
            raise _Stop(WAIT_BUSY)
        handle = reply.get("self") if reply.get("ok") else None
        pose = reply.get("pose")  # the client's action name: "Wait", "Sit", ...
        if isinstance(pose, str):
            # A hook that reports the pose: trust it over the last 0x59.
            run.pose = POSE_SIT if pose == "Sit" else POSE_STAND
            if run.pose == POSE_SIT:
                return None
        if not isinstance(handle, int) or handle <= 0:
            # Mid map change the own character is not built yet.
            return None
        return handle

    def _send_cast(
        self, pid: int, run: _Run, line: str, what: str, on_sent: Callable[[], None]
    ) -> bool:
        try:
            reply = self._channel.send(pid, line)
        except PipeGone:
            self._set_problem(run, "找不到 hook 指令通道（遊戲關了，或 hook 沒有注入）")
            raise _Stop(WAIT_NO_PIPE)
        except PipeBusy:
            self._set_problem(run, "指令通道正被其他程式使用")
            raise _Stop(WAIT_BUSY)
        except NoReply as e:
            on_sent()  # it may still have run: hold the skill like a sent cast
            self._note(run, "error", f"{what}：hook 沒有回應（{e}）", rule="buff")
            raise _Stop(WAIT_NO_REPLY)
        code = classify_error(reply)
        if code is None:
            on_sent()
            return True
        if code == "actions_not_loaded":
            self._set_problem(run, "hook 的動作模組還沒載入，請在 hook 端載入後再試")
            raise _Stop(WAIT_NOT_LOADED)
        if code == "dispatcher_timeout":
            self._note(run, "error", f"{what}：遊戲沒有回應（視窗可能最小化）", rule="buff")
            raise _Stop(WAIT_DISPATCHER)
        # Refused (e.g. on cooldown): hold it like a miss so it is not hammered.
        on_sent()
        self._note(run, "error", f"{what}：{reply.get('error', '未知錯誤')}", rule="buff")
        return False

    def _log_cures(self, run: _Run, debuffs: tuple[int, ...], now: float) -> None:
        """Close the cure line of a debuff that went away, or ran out of tries."""
        settled = settle_cures(run.cure_state, debuffs, now)
        if not settled:
            return
        with run.lock:
            for group, cleared in settled:
                line = run.cure_lines.pop(group, None)
                if line is None:
                    continue
                if cleared:
                    line.phase = "confirmed"
                    line.text += "（已解除）"
                else:
                    line.phase = "unconfirmed"
                    line.text += f"（用了 {CURE_TRIES} 次還在，等它自己消失）"

    def _log_bag(self, run: _Run, sample: Sample, now: float) -> None:
        """Fold what the bag shows into the drink lines that sent it, oldest first."""
        settled = settle_bag(run.state, sample.bag, now)
        if not settled:
            return
        with run.lock:
            for item_id, before, count, ok in settled:
                queue = run.awaiting.get(item_id)
                if not queue:
                    continue
                if not ok:
                    for line in queue:
                        line.bag[item_id][3] = True
                        line.refresh()
                    del run.awaiting[item_id]
                    continue
                drop = before - count
                while drop > 0 and queue:
                    line = queue[0]
                    slot = line.bag[item_id]
                    k = min(drop, slot[0])
                    slot[0] -= k
                    slot[2] -= k
                    drop -= k
                    if slot[0] <= 0:
                        queue.popleft()
                    line.refresh()
                if not queue:
                    del run.awaiting[item_id]
