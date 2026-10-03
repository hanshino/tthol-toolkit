"""Read-only damage capture: record every hit the player lands, for formula research.

The client copies each attack-result packet (opcode 0x43, handled at
tthola.dat 0x4A5210) into a global script-parameter table at 0x787750 before
it shows the floating numbers. Only the latest packet survives, so the table is
polled about once per millisecond (measured 2026-10-03: 1.4% of one core, no
gaps seen over ~25 attacks). Nothing is written to the game.

Two layouts share the table (u32 words):

    normal attack   [rel, 4, attacker, target, ?, n, n x (type, value)]
    skill           [rel, F, F x (b0, b1, cast_effect, n, n x (type, value))]

`rel` is a relation code: 5 = the player hit something, 0xB = something hit the
player. A skill record carries neither the target nor the skill id; the skill
is recovered from `cast_effect` (magic.cast_effect, shared by a skill family)
intersected with the learned skills, and the target from the latest normal
attack. Segment types: 0 hit, 1 crit, 4 heal, 2/3/5/6 no damage.

Handles resolve through the sprite manager's table: obj = [[[0x787748]+8] +
(h & 0xFFFF)*4], valid while [obj+0x1C] == h. Monsters are CCharObjects:
+0x12E npc.id, +0x132 instance, +0x2C8 HP percent, +0x9C8 / +0x9CC debuff
count / status groups. Investigation: memory project_damage_capture and
tthol_data scripts/damage_capture_investigation.md.

Pure helpers (parse_record, infer_skill, summarize) are unit-tested; the
recorder thread is the only part that touches the game.
"""

from __future__ import annotations

import ctypes
import datetime
import hashlib
import json
import logging
import sqlite3
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import reader
from services._paths import bundled

log = logging.getLogger("tthol.damage")

SCHEMA = "tthol-damage/1"

RESULT_TABLE_ADDR = 0x00787750
RESULT_TABLE_SIZE = 0x1A0  # the script-parameter block runs to 0x7878F0
SPRITE_MANAGER_PTR = 0x00787748  # CSpriteManager, == CStageProc + 0xC
HANDLE_TABLE_OFFSET = 0x8  # [manager + 8] -> object pointer per handle index
HANDLE_INDEX_MASK = 0xFFFF

OBJ_HANDLE_OFFSET = 0x1C
OBJ_NPC_ID_OFFSET = 0x12E  # inside the 10-byte packet key at +0x12C
OBJ_INSTANCE_OFFSET = 0x132
OBJ_HP_PCT_OFFSET = 0x2C8  # other characters only carry a 0..100 percentage
MON_DEBUFF_COUNT_OFFSET = 0x9C8  # monsters; the player's own list is HP+0x4C4
MON_DEBUFF_ARRAY_OFFSET = 0x9CC
MON_DEBUFF_MAX = 16

# Class / sect masks the skill-requirement check (0x4211B0) compares against,
# relative to CCharObject. Raw values only: the writer was not traced, so which
# one is the primary and which the secondary sect is unconfirmed.
OBJ_SECT_MASK_OFFSETS = (0x4C, 0x54, 0x5C)

STAGE_PROC_VTABLE = 0x005FD5BC  # CStageProc
STAGE_PROC_MANAGER_OFFSET = 0xC
STAGE_PROC_SELF_HANDLE_OFFSET = 0x2104  # the player's handle; verified live 2026-10-03
# Selected-target handle (from static analysis). Live 2026-10-03 it named a
# different monster for each kill in a skill-only session, so skills use it as
# their target; not yet cross-checked against a packet's own target.
STAGE_PROC_SELECTED_OFFSET = 0x3A5C

REL_PLAYER_HITS = 5
NORMAL_MARK = 4  # word 1 of a normal-attack record
MAX_SEGMENTS = 32
MAX_FRAMES = 16
HANDLE_FLOOR = 0x10000  # handles carry a counter above the 16-bit index

SEG_HIT, SEG_CRIT, SEG_HEAL = 0, 1, 4
DAMAGE_TYPES = (SEG_HIT, SEG_CRIT)
MISS_TYPES = (2, 3)
# Seen live 2026-10-03 on 千瘡百孔: a type-5 result arrives as its own packet
# exactly when 卸冑 shows up on the target, type 6 when it does not. Read as
# "status landed / resisted" (inferred, not confirmed in the client script).
STATUS_TYPES = (5, 6)

POLL_SECONDS = 0.001
REFRESH_SECONDS = 2.0  # learned skills, CStageProc check, snapshot
SKILL_TARGET_WINDOW = 10.0  # a skill borrows the target of a normal attack this recent
DEFAULT_GAP_SECONDS = 5.0  # hits further apart than this are not combat time
MAX_EVENTS = 50_000
TARGET_SAMPLE_SECONDS = 0.03  # how often the selected / last-hit target is sampled
TARGET_SAMPLE_MAX_AGE = 1.0  # older samples are not attached to a hit


# ---------------------------------------------------------------- parsing


@dataclass(frozen=True)
class Record:
    """One attack result as copied into the global table."""

    path: str  # "normal" | "skill"
    rel: int
    segments: tuple[tuple[int, int], ...]  # (type, value)
    attacker: int | None = None  # normal path only
    target: int | None = None  # normal path only
    cast_effect: int | None = None  # skill path only
    frame_key: tuple[int, int] | None = None  # skill path: the two frame-table bytes
    span: bytes = b""  # the bytes this record was parsed from, for change detection

    @property
    def damage(self) -> int:
        return sum(v for t, v in self.segments if t in DAMAGE_TYPES)


def parse_record(raw: bytes) -> Record | None:
    """Parse the result table, or None when it does not look like a result."""
    n_words = len(raw) // 4
    if n_words < 6:
        return None
    w = struct.unpack_from(f"<{n_words}I", raw)
    rel = w[0]
    if w[1] == NORMAL_MARK and w[2] >= HANDLE_FLOOR and w[3] >= HANDLE_FLOOR:
        n = w[5]
        end = 6 + 2 * n
        if not 1 <= n <= MAX_SEGMENTS or end > n_words:
            return None
        segs = tuple((w[6 + 2 * i], w[7 + 2 * i]) for i in range(n))
        return Record(
            path="normal", rel=rel, segments=segs, attacker=w[2], target=w[3],
            span=bytes(raw[: end * 4]),
        )  # fmt: skip
    frames = w[1]
    if not 1 <= frames <= MAX_FRAMES:
        return None
    idx, segs, effect, key = 2, [], None, None
    for _ in range(frames):
        if idx + 4 > n_words:
            return None
        b0, b1, ce, n = w[idx : idx + 4]
        if b0 > 0xFF or b1 > 0xFF or ce > 0xFFFF or n > MAX_SEGMENTS:
            return None
        if idx + 4 + 2 * n > n_words:
            return None
        if effect is None:
            effect, key = ce, (b0, b1)
        segs.extend((w[idx + 4 + 2 * i], w[idx + 5 + 2 * i]) for i in range(n))
        idx += 4 + 2 * n
    if not segs:
        return None
    return Record(
        path="skill", rel=rel, segments=tuple(segs), cast_effect=effect, frame_key=key,
        span=bytes(raw[: idx * 4]),
    )  # fmt: skip


# ---------------------------------------------------------------- skill id


@dataclass(frozen=True)
class SkillGuess:
    magic_id: int | None
    level: int | None
    method: str  # "learned" | "ambiguous" | "unique" | "unknown"
    candidates: tuple[int, ...] = ()


def infer_skill(
    cast_effect: int,
    learned: list[tuple[int, int]],
    effects: dict[tuple[int, int], int],
) -> SkillGuess:
    """Which skill produced a cast effect.

    `effects` maps (magic id, level) -> cast_effect (it can differ by level).
    One learned skill with that effect identifies it; otherwise fall back to the
    effect being unique in the whole DB.
    """
    hits = [(mid, lv) for mid, lv in learned if effects.get((mid, lv)) == cast_effect]
    ids = tuple(sorted({mid for mid, _ in hits}))
    if len(ids) == 1:
        level = max(lv for mid, lv in hits if mid == ids[0])
        return SkillGuess(ids[0], level, "learned", ids)
    if len(ids) > 1:
        return SkillGuess(None, None, "ambiguous", ids)
    owners = tuple(sorted({mid for (mid, _lv), ce in effects.items() if ce == cast_effect}))
    if len(owners) == 1:
        return SkillGuess(owners[0], None, "unique", owners)
    return SkillGuess(None, None, "unknown", owners)


# ---------------------------------------------------------------- summary


def _debuffs_before(hit: dict) -> list[int]:
    """The target's debuffs as they were when the hit landed. The read made when
    the result shows up is too late on a killing blow (the debuffs are cleared
    with the death), so the sample taken just before wins when there is one."""
    t = hit.get("target") or {}
    before = t.get("debuffs_before")
    return before if before is not None else t.get("debuffs") or []


def combat_seconds(times: list[float], gap: float) -> float:
    """Time spent fighting: the sum of gaps between consecutive hits that are
    at most `gap` apart. Walking to the next monster is left out."""
    ts = sorted(times)
    return sum(b - a for a, b in zip(ts, ts[1:]) if b - a <= gap)


def summarize(hits: list[dict], elapsed: float, gap: float = DEFAULT_GAP_SECONDS) -> dict:
    """Totals over hit events (see DamageRecorder._hit_event for the shape)."""
    total = sum(h["damage"] for h in hits)
    combat = combat_seconds([h["t"] for h in hits], gap)
    normal = [s for h in hits if h["path"] == "normal" for s in h["segments"]]
    landed = [t for t, _v in normal if t in DAMAGE_TYPES]
    segs = [s for h in hits for s in h["segments"]]
    misses = [t for t, _v in segs if t in MISS_TYPES]
    debuffed = [h for h in hits if _debuffs_before(h)]
    return {
        "total_damage": total,
        "hits": len(hits),
        "segments": len(segs),
        "misses": len(misses),
        "miss_rate": len(misses) / len(segs) if segs else 0.0,
        "crit_rate": landed.count(SEG_CRIT) / len(landed) if landed else 0.0,
        "debuffed_share": len(debuffed) / len(hits) if hits else 0.0,
        "combat_seconds": combat,
        "elapsed_seconds": elapsed,
        "combat_dps": total / combat if combat > 0 else 0.0,
        "overall_dps": total / elapsed if elapsed > 0 else 0.0,
        "gap_seconds": gap,
    }


# ---------------------------------------------------------------- game reads


def _u32(pm, addr: int) -> int:
    return struct.unpack("<I", pm.read_bytes(addr, 4))[0]


def handle_table(pm) -> int | None:
    try:
        mgr = _u32(pm, SPRITE_MANAGER_PTR)
        table = _u32(pm, mgr + HANDLE_TABLE_OFFSET)
    except Exception:
        return None
    return table if table >= reader.HEAP_MIN_PTR else None


def resolve_object(pm, table: int, handle: int) -> int | None:
    """Object address for a handle, or None when it is gone or reused."""
    if not handle or table is None:
        return None
    try:
        obj = _u32(pm, table + (handle & HANDLE_INDEX_MASK) * 4)
        if obj < reader.HEAP_MIN_PTR or _u32(pm, obj + OBJ_HANDLE_OFFSET) != handle:
            return None
    except Exception:
        return None
    return obj


def read_target(pm, table: int, handle: int) -> dict | None:
    """npc id, instance, HP percent and debuff groups of a live object."""
    obj = resolve_object(pm, table, handle)
    if obj is None:
        return None
    try:
        npc_id, inst = struct.unpack("<II", pm.read_bytes(obj + OBJ_NPC_ID_OFFSET, 8))
        hp_pct = struct.unpack("<i", pm.read_bytes(obj + OBJ_HP_PCT_OFFSET, 4))[0]
        count = struct.unpack("<i", pm.read_bytes(obj + MON_DEBUFF_COUNT_OFFSET, 4))[0]
        debuffs: list[int] = []
        if 0 < count <= MON_DEBUFF_MAX:
            debuffs = list(
                struct.unpack(f"<{count}i", pm.read_bytes(obj + MON_DEBUFF_ARRAY_OFFSET, 4 * count))
            )
    except Exception:
        return None
    return {
        "handle": handle,
        "npc_id": npc_id,
        "instance": inst,
        "hp_pct": hp_pct if 0 <= hp_pct <= 100 else None,
        "debuffs": debuffs,
    }


def find_stage_proc(pm) -> int | None:
    """CStageProc address (one heap pass, ~1-2 s): the instance whose +0xC is
    the sprite manager the global points at."""
    try:
        mgr = _u32(pm, SPRITE_MANAGER_PTR)
    except Exception:
        return None
    vt = struct.pack("<I", STAGE_PROC_VTABLE)
    for base, size in reader.get_memory_regions(pm.process_handle):
        if base < reader.HEAP_MIN_PTR:
            continue
        try:
            buf = pm.read_bytes(base, size)
        except Exception:
            continue
        i = buf.find(vt)
        while i >= 0:
            if i % 4 == 0 and i + STAGE_PROC_MANAGER_OFFSET + 4 <= len(buf):
                if struct.unpack_from("<I", buf, i + STAGE_PROC_MANAGER_OFFSET)[0] == mgr:
                    return base + i
            i = buf.find(vt, i + 1)
    return None


# ---------------------------------------------------------------- DB lookups


class GameData:
    """npc / magic / item / status names from tthol.sqlite, loaded once."""

    def __init__(self, db_path=None) -> None:
        self._path = db_path or bundled("tthol.sqlite")
        self._lock = threading.Lock()
        self._loaded = False
        self.npcs: dict[int, dict] = {}
        self.effects: dict[tuple[int, int], int] = {}
        self.skill_names: dict[int, str] = {}
        self.items: dict[int, str] = {}
        self.statuses: dict[int, str] = {}

    def load(self) -> "GameData":
        with self._lock:
            if self._loaded:
                return self
            self._loaded = True
            try:
                con = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True)
            except sqlite3.Error as exc:
                log.error("game DB unavailable: %s", exc, extra={"cat": "damage"})
                return self
            try:
                con.text_factory = lambda b: b.decode("utf-8", errors="replace")
                for nid, name, lv, d, md in con.execute(
                    "SELECT id, name, level, extra_def, magic_def FROM npc"
                ):
                    self.npcs[nid] = {"name": name, "level": lv, "def": d, "mdef": md}
                for mid, lv, name, ce in con.execute(
                    "SELECT id, level, name, cast_effect FROM magic"
                ):
                    if ce is not None:
                        self.effects[(mid, lv)] = ce
                    self.skill_names.setdefault(mid, name)
                self.items = dict(con.execute("SELECT id, name FROM items"))
                for g, name in con.execute(
                    'SELECT "group", name FROM status ORDER BY "group", "order"'
                ):
                    self.statuses.setdefault(g, name)
            except sqlite3.Error as exc:
                log.error("game DB read failed: %s", exc, extra={"cat": "damage"})
            finally:
                con.close()
            return self


# ---------------------------------------------------------------- snapshot


WEAPON_SLOTS = ("HAND_R", "HAND_L")
PANEL_KEYS = ("atk", "matk", "def", "mdef", "hit", "dodge", "critical", "attack_speed")


def build_snapshot(raw, data: GameData, sect_masks: list[int] | None = None) -> dict:
    """The player's state that matters for damage, from stat_sim_export.read_character.
    The character name is left out on purpose."""
    weapons = []
    for slot in raw.equipment:
        if slot["slot"] in WEAPON_SLOTS and slot["item_id"] is not None:
            weapons.append(
                {
                    "slot": slot["slot"],
                    "item_id": slot["item_id"],
                    "name": data.items.get(slot["item_id"]),
                    "plus": slot["plus"],
                    "zhenjie": slot["zhenjie"],
                }
            )
    return {
        "level": raw.level,
        "sect": raw.sect,
        "sect_masks": list(sect_masks or []),
        "panel": {k: raw.panel[k] for k in PANEL_KEYS if k in raw.panel},
        "attrs": list(raw.panel_attrs),
        "weapons": weapons,
        "buffs": [
            {"group": g, "name": data.statuses.get(g)}
            for g, kind in raw.statuses
            if kind != "debuff"
        ],
    }


# ---------------------------------------------------------------- recorder


def _client_sha256(pid: int) -> str | None:
    try:
        import psutil

        path = psutil.Process(pid).exe()
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


class _TimerResolution:
    """timeBeginPeriod(1) for the life of a polling thread, so sleep(0.001)
    sleeps ~1 ms instead of a 15.6 ms scheduler tick."""

    def __enter__(self):
        try:
            ctypes.windll.winmm.timeBeginPeriod(1)
            self._on = True
        except Exception:
            self._on = False
        return self

    def __exit__(self, *exc):
        if self._on:
            ctypes.windll.winmm.timeEndPeriod(1)


@dataclass
class _State:
    started_at: float | None = None  # wall clock of the first start
    t0: float | None = None  # perf_counter origin
    paused_total: float = 0.0
    paused_at: float | None = None
    events: list[dict] = field(default_factory=list)
    seq: int = 0
    snapshot: dict | None = None
    status: str = "idle"  # idle | recording | paused | waiting
    note: str | None = None


class DamageRecorder:
    """One character's recording: a polling thread plus the event log."""

    def __init__(
        self,
        pid: int,
        live: Callable[[], tuple | None],
        read_locked: Callable[[Callable], object],
        data: GameData,
        gap: float = DEFAULT_GAP_SECONDS,
    ) -> None:
        self.pid = pid
        self._live = live
        self._read_locked = read_locked
        self._data = data
        self.gap = gap
        self._lock = threading.Lock()
        self._st = _State()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._client_sha: str | None = None
        self._ctx: dict = {}  # learned skills, self object, CStageProc (context thread)
        self._ctx_ready = threading.Event()  # first CStageProc lookup done
        self._samples: dict[int, tuple[float, dict]] = {}  # handle -> (perf_counter, target)

    # ---- control ----

    def start(self) -> None:
        with self._lock:
            now = time.perf_counter()
            if self._st.t0 is None:
                self._st.t0 = now
                self._st.started_at = time.time()
            elif self._st.paused_at is not None:
                self._st.paused_total += now - self._st.paused_at
            self._st.paused_at = None
            self._st.status = "recording"
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name=f"damage-{self.pid}", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        """Pause: the thread ends, the log stays."""
        self._stop.set()
        with self._lock:
            if self._st.paused_at is None and self._st.t0 is not None:
                self._st.paused_at = time.perf_counter()
            self._st.status = "paused" if self._st.t0 is not None else "idle"
            self._st.note = None
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=2.0)

    def clear(self) -> None:
        running = self._thread is not None and self._thread.is_alive() and not self._stop.is_set()
        with self._lock:
            snapshot = self._st.snapshot
            self._st = _State(snapshot=snapshot)
            if running:
                self._st.t0 = time.perf_counter()
                self._st.started_at = time.time()
                self._st.status = "recording"
        if running:
            self._snapshot_event(snapshot, force=True)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    # ---- clock ----

    def _elapsed(self, now: float | None = None) -> float:
        st = self._st
        if st.t0 is None:
            return 0.0
        end = st.paused_at if st.paused_at is not None else (now or time.perf_counter())
        return max(0.0, end - st.t0 - st.paused_total)

    # ---- events ----

    def _append(self, event: dict) -> None:
        with self._lock:
            self._st.seq += 1
            event["seq"] = self._st.seq
            self._st.events.append(event)
            if len(self._st.events) > MAX_EVENTS:
                del self._st.events[: len(self._st.events) - MAX_EVENTS]

    def _snapshot_event(self, snap: dict | None, force: bool = False) -> None:
        if snap is None:
            return
        with self._lock:
            if not force and snap == self._st.snapshot:
                return
            self._st.snapshot = snap
        self._append({"kind": "snapshot", "t": round(self._elapsed(), 3), "snapshot": snap})

    def _target_info(self, t: dict | None, source: str) -> dict | None:
        if t is None:
            return None
        npc = self._data.npcs.get(t["npc_id"], {})
        return {**t, "name": npc.get("name"), "level": npc.get("level"),
                "defense": npc.get("def"), "mdefense": npc.get("mdef"), "source": source}  # fmt: skip

    def _hit_event(self, rec: Record, t: float, poll_gap: float | None, target: dict | None,
                   selected: dict | None, skill: SkillGuess | None) -> dict:  # fmt: skip
        ev = {
            "kind": "hit",
            "t": round(t, 3),
            # Time between the read that saw this result and the read before it:
            # the result arrived somewhere inside that window.
            "poll_gap_ms": round(poll_gap * 1000, 2) if poll_gap is not None else None,
            "path": rec.path,
            "rel": rec.rel,
            "segments": [list(s) for s in rec.segments],
            "damage": rec.damage,
            "target": target,
            "selected": selected,
        }
        if rec.path == "skill":
            ev["skill"] = {
                "cast_effect": rec.cast_effect,
                "frame_key": list(rec.frame_key or ()),
                "magic_id": skill.magic_id if skill else None,
                "level": skill.level if skill else None,
                "name": self._data.skill_names.get(skill.magic_id)
                if skill and skill.magic_id
                else None,
                "method": skill.method if skill else "unknown",
                "candidates": list(skill.candidates) if skill else [],
                "candidate_names": [self._data.skill_names.get(m) for m in skill.candidates]
                if skill
                else [],
            }
            # rel == 5 is a heuristic; a cast effect none of the learned skills
            # has is more likely a party member's.
            ev["not_mine_suspect"] = skill is None or skill.method not in ("learned", "ambiguous")
            # When a cast procs a status the damage and status results arrive
            # back to back and the second overwrites the first before a poll
            # can see it. A status-only result therefore usually means a lost
            # damage result.
            ev["damage_lost_suspect"] = all(t in STATUS_TYPES for t, _v in rec.segments)
        return ev

    # ---- threads ----
    # The poll thread only reads the result table and resolves handles (a few
    # small reads per packet). Everything slow -- the snapshot, learned skills,
    # the CStageProc heap scan -- runs on a second thread, so it never delays a
    # poll.

    def _run(self) -> None:  # pragma: no cover -- runtime only
        # The worker's lock comes and goes (it re-locates after a failed
        # validation, sometimes every few seconds in combat). Recording must not
        # stop with it: the table, the handle table and CStageProc (which holds
        # the player's handle) need only the process handle, so keep the last
        # one and carry on. Only the snapshot waits for a lock.
        self._data.load()
        if self._client_sha is None:
            self._client_sha = _client_sha256(self.pid)
        ctx = threading.Thread(target=self._run_context, name=f"damage-ctx-{self.pid}", daemon=True)
        ctx.start()
        pm = None
        last_span: bytes | None = None
        last_attack: tuple[float, int] | None = None  # (elapsed, target handle)
        prev_read: float | None = None
        next_sample = 0.0
        with _TimerResolution():
            while not self._stop.is_set():
                live = self._live()
                if live is not None:
                    pm = live[0]
                    self._set_status("recording", None)
                elif pm is not None:
                    self._set_status("recording", "角色定位中：照常記錄，快照暫停更新")
                else:
                    self._set_status("waiting", "等待角色定位")
                if pm is None or not self._ctx_ready.is_set():
                    self._stop.wait(0.2)
                    continue
                try:
                    raw = pm.read_bytes(RESULT_TABLE_ADDR, RESULT_TABLE_SIZE)
                except Exception:
                    self._stop.wait(0.05)
                    continue
                read_at = time.perf_counter()
                poll_gap = read_at - prev_read if prev_read is not None else None
                prev_read = read_at
                rec = parse_record(raw)
                # The first table read only primes: it holds whatever packet
                # came before the recording started.
                if rec is not None and rec.span != last_span:
                    primed = last_span is not None
                    last_span = rec.span
                    if primed:
                        last_attack = self._on_record(pm, rec, last_attack, poll_gap)
                elif rec is None and last_span is None:
                    last_span = b""
                if read_at >= next_sample:
                    next_sample = read_at + TARGET_SAMPLE_SECONDS
                    self._sample_targets(pm, last_attack, read_at)
                time.sleep(POLL_SECONDS)
        ctx.join(timeout=2.0)

    def _run_context(self) -> None:  # pragma: no cover -- runtime only
        pm = None
        while not self._stop.is_set():
            live = self._live()
            if live is not None:
                pm, hp_addr = live
                learned = reader.read_skills(pm, hp_addr)
                with self._lock:
                    if learned is not None:
                        self._ctx["learned"] = learned
                    self._ctx["self_obj"] = hp_addr - reader.CHAR_OBJ_HP_OFFSET
                self._refresh_snapshot()
            if pm is not None:
                with self._lock:
                    stage_proc = self._ctx.get("stage_proc")
                if stage_proc is None or not self._is_stage_proc(pm, stage_proc):
                    stage_proc = find_stage_proc(pm)
                    with self._lock:
                        self._ctx["stage_proc"] = stage_proc
                self._ctx_ready.set()
            self._stop.wait(REFRESH_SECONDS if pm is not None else 0.2)

    def _self_handle(self, pm, stage_proc, self_obj) -> int | None:
        """The player's handle; it changes on every map change, so read it per record."""
        try:
            if stage_proc is not None and self._is_stage_proc(pm, stage_proc):
                return _u32(pm, stage_proc + STAGE_PROC_SELF_HANDLE_OFFSET)
            if self_obj is not None and _u32(pm, self_obj) == reader.CHAR_OBJ_VTABLE:
                return _u32(pm, self_obj + OBJ_HANDLE_OFFSET)
        except Exception:
            pass
        return None

    def _sample_targets(self, pm, last_attack, now: float) -> None:
        """Remember the selected and last-hit targets' state, so a hit can carry
        the state from just before it landed."""
        with self._lock:
            stage_proc = self._ctx.get("stage_proc")
        handles = {self._selected_handle(pm, stage_proc)}
        if last_attack is not None:
            handles.add(last_attack[1])
        table = handle_table(pm)
        for h in handles:
            if h:
                t = read_target(pm, table, h)
                if t is not None:
                    self._samples[h] = (now, t)
        if len(self._samples) > 64:
            cutoff = now - TARGET_SAMPLE_MAX_AGE
            self._samples = {h: v for h, v in self._samples.items() if v[0] >= cutoff}

    def _with_before(self, target: dict | None, now: float) -> dict | None:
        if target is None:
            return None
        got = self._samples.get(target["handle"])
        if got is not None and now - got[0] <= TARGET_SAMPLE_MAX_AGE:
            sampled_at, before = got
            target["debuffs_before"] = before["debuffs"]
            target["hp_pct_before"] = before["hp_pct"]
            target["before_age_ms"] = round((now - sampled_at) * 1000, 1)
        return target

    def _on_record(self, pm, rec, last_attack, poll_gap=None):
        t = self._elapsed()
        table = handle_table(pm)
        with self._lock:
            stage_proc = self._ctx.get("stage_proc")
            self_obj = self._ctx.get("self_obj")
            learned = self._ctx.get("learned") or []
            learned_known = "learned" in self._ctx
        selected_handle = self._selected_handle(pm, stage_proc)
        selected_t = read_target(pm, table, selected_handle) if selected_handle else None
        selected = (
            {"npc_id": selected_t["npc_id"], "instance": selected_t["instance"]}
            if selected_t
            else None
        )
        if rec.path == "normal":
            if rec.attacker != self._self_handle(pm, stage_proc, self_obj):
                return last_attack
            target = self._target_info(read_target(pm, table, rec.target), "packet")
            target = self._with_before(target, time.perf_counter())
            self._append(self._hit_event(rec, t, poll_gap, target, selected, None))
            return (t, rec.target)
        if rec.rel != REL_PLAYER_HITS:
            return last_attack
        if selected_t is not None:
            target = self._with_before(
                self._target_info(selected_t, "selected"), time.perf_counter()
            )
        elif last_attack is not None and t - last_attack[0] <= SKILL_TARGET_WINDOW:
            target = self._with_before(
                self._target_info(read_target(pm, table, last_attack[1]), "recent_attack"),
                time.perf_counter(),
            )
        else:
            target = None
        skill = infer_skill(rec.cast_effect, learned, self._data.effects)
        ev = self._hit_event(rec, t, poll_gap, target, selected, skill)
        if not learned_known:
            ev["not_mine_suspect"] = False  # cannot judge before the skill list is read
        self._append(ev)
        return last_attack

    def _selected_handle(self, pm, stage_proc) -> int | None:
        if stage_proc is None:
            return None
        try:
            return _u32(pm, stage_proc + STAGE_PROC_SELECTED_OFFSET) or None
        except Exception:
            return None

    @staticmethod
    def _is_stage_proc(pm, addr: int) -> bool:
        try:
            return _u32(pm, addr) == STAGE_PROC_VTABLE
        except Exception:
            return False

    def _refresh_snapshot(self) -> None:
        from services import stat_sim_export

        def read(pm, hp_addr, compat_mode):
            raw = stat_sim_export.read_character(pm, hp_addr, compat_mode)
            obj = hp_addr - reader.CHAR_OBJ_HP_OFFSET
            masks = [_u32(pm, obj + off) for off in OBJ_SECT_MASK_OFFSETS]
            return raw, masks

        try:
            got = self._read_locked(read)
        except stat_sim_export.NotReady:
            return
        except Exception as exc:
            log.debug("snapshot read failed: %s", exc, extra={"cat": "damage"})
            return
        if got is not None:
            raw, masks = got
            self._snapshot_event(build_snapshot(raw, self._data, masks))

    def _set_status(self, status: str, note: str | None) -> None:
        with self._lock:
            if self._stop.is_set():
                return
            self._st.status, self._st.note = status, note

    # ---- reads for the API ----

    def status(self, since: int = 0) -> dict:
        with self._lock:
            st = self._st
            events = [e for e in st.events if e["seq"] > since]
            hits = [e for e in st.events if e["kind"] == "hit" and not e.get("not_mine_suspect")]
            elapsed = self._elapsed()
            snapshot = st.snapshot
            status, note, seq = st.status, st.note, st.seq
        return {
            "status": status,
            "note": note,
            "elapsed": round(elapsed, 3),
            "seq": seq,
            "events": events,
            "summary": summarize(hits, elapsed, self.gap),
            "snapshot": snapshot,
        }

    def export_lines(self, app_version: str) -> list[str]:
        with self._lock:
            st = self._st
            events = list(st.events)
            started = st.started_at
            elapsed = self._elapsed()
        header = {
            "schema": SCHEMA,
            "app": app_version,
            "client_sha256": self._client_sha,
            "recorded_at": datetime.datetime.fromtimestamp(started or time.time())
            .astimezone()
            .isoformat(timespec="seconds"),
            "elapsed_seconds": round(elapsed, 3),
            "gap_seconds": self.gap,
            "notes": [
                "segments are [type, value]; type 0 hit, 1 crit, 2/3 miss, 4 heal, 5/6 status landed/resisted (inferred)",
                "damage_lost_suspect: a skill result with only status segments; its damage result was most likely overwritten before it could be read",
                "target.debuffs / hp_pct are read when the result is seen (after the hit; cleared on a kill); *_before come from a sample taken before_age_ms earlier",
                "skill target: target.source=selected is the selected target (CStageProc+0x3A5C); recent_attack = the latest normal attack's target when nothing is selected",
                "two identical consecutive results collapse into one (equal damage is common with fixed-damage hits); counts and DPS can be low",
                "not_mine_suspect: a skill whose cast effect no learned skill has; left out of the summary",
                "snapshot.sect_masks: raw CCharObject +0x4C/+0x54/+0x5C (sect masks, which is primary/secondary unverified)",
                "buffs list only contract/shield-class buffs; pure stat buffs show in panel values only",
            ],
        }
        lines = [json.dumps(header, ensure_ascii=False)]
        lines += [json.dumps(e, ensure_ascii=False) for e in events]
        return lines


class DamageRecorderManager:
    """Recorders per pid. Built in app.py with the WorkerManager's accessors."""

    def __init__(self, live: Callable[[int], tuple | None], read_locked: Callable,
                 data: GameData | None = None) -> None:  # fmt: skip
        self._live = live
        self._read_locked = read_locked
        self._data = data or GameData()
        self._recs: dict[int, DamageRecorder] = {}
        self._lock = threading.Lock()

    def _rec(self, pid: int) -> DamageRecorder:
        with self._lock:
            rec = self._recs.get(pid)
            if rec is None:
                rec = DamageRecorder(
                    pid,
                    live=lambda: self._live(pid),
                    read_locked=lambda read: self._read_locked(pid, read),
                    data=self._data,
                )
                self._recs[pid] = rec
            return rec

    def start(self, pid: int) -> None:
        self._rec(pid).start()

    def stop(self, pid: int) -> None:
        self._rec(pid).stop()

    def clear(self, pid: int) -> None:
        self._rec(pid).clear()

    def status(self, pid: int, since: int = 0) -> dict:
        return self._rec(pid).status(since)

    def export_lines(self, pid: int, app_version: str) -> list[str]:
        return self._rec(pid).export_lines(app_version)

    def shutdown(self) -> None:
        with self._lock:
            recs = list(self._recs.values())
        for rec in recs:
            rec.stop()
