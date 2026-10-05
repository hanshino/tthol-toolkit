"""Own buffs and debuffs from a hooked client: what each one is and when it ends.

Two hook sources, both by `code` (a skill code = magic.id * 100 + level, or an
items.id for item buffs):

- packet 0x29 (event pipe), sent for the own character only, carries no key:
  `29 <u32 code> <u8 1=on / 0=off> <u32 remaining ms>`. Adds, removals and the
  time left all arrive this way; on entering a map the server resends item and
  hero buffs with their current time left (skill buffs are dropped by the
  game on a map change and are not resent).
- the `buffs` read command (command pipe) lists what the client holds now. It
  fills in what was missed (the app started after a buff went on) and drops
  what a lost off-packet left behind. Its `group` field is unreliable (stale
  for item buffs, wrong for skill codes that collide with an items.id), so it
  is ignored.

A debuff an attack applies (not a skill) shows in neither; the character's
own debuff array in memory (HP+0x4C4) still has it, so the snapshot merges that
in (WorkerManager).
"""

from __future__ import annotations

import logging
import sqlite3
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from services._paths import bundled
from services.api_types import BuffInfo
from services.hook_cmd import CommandChannel, HookCmdError

log = logging.getLogger("tthol.buff_tracker")

BUFF_PACKET = 0x29
RECONCILE_EVERY = 5.0  # seconds between `buffs` reads per hooked client
FRESH = 2.0  # a buff this new may not be in a `buffs` reply yet: keep it
BACKOFF = 30.0  # after the hook refuses `buffs` (older build, or no actions loaded)

# Status groups that harm the one who has them. 16 現形 is left out: it strips
# the user's own 隱形 rather than being cast on them.
HOSTILE_GROUPS = frozenset({14, 15, 17, 18, 19, 20, 21, 22, 23, 38, 39, 65, 66, 68, 69, 70, 71, 72})
HERO_GROUPS = frozenset({48, 49})


def decode_buff(raw: bytes) -> tuple[int, bool, int] | None:
    """(code, on, remaining ms) of a 0x29 packet."""
    if len(raw) < 10 or raw[0] != BUFF_PACKET:
        return None
    code, on, ms = struct.unpack_from("<IBI", raw, 1)
    return code, bool(on), ms


@dataclass(frozen=True)
class BuffDef:
    name: str
    level: int | None  # skill level; None for an item buff
    group: int  # status group of its effect, 0 when the DB has none
    kind: str  # buff / debuff / hero


def _kind(group: int) -> str:
    if group in HOSTILE_GROUPS:
        return "debuff"
    if group in HERO_GROUPS:
        return "hero"
    return "buff"


def load_buff_defs(db_path: Path | None = None) -> Callable[[int], BuffDef | None]:
    """code -> BuffDef, looked up lazily in the game DB."""
    path = db_path or bundled("tthol.sqlite")
    cache: dict[int, BuffDef | None] = {}
    lock = threading.Lock()

    def lookup(code: int) -> BuffDef | None:
        with lock:
            if code in cache:
                return cache[code]
            found = _lookup(path, code) if path.exists() else None
            cache[code] = found
            return found

    return lookup


def _lookup(path: Path, code: int) -> BuffDef | None:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.text_factory = lambda b: b.decode("utf-8", errors="replace")
    try:
        group_of = lambda status_id: (  # noqa: E731
            (
                con.execute('SELECT "group" FROM status WHERE id = ?', (status_id,)).fetchone()
                or (0,)
            )[0]
            if status_id
            else 0
        )
        item = con.execute(
            "SELECT name, extra_status, item_time FROM items WHERE id = ?", (code,)
        ).fetchone()
        skill = con.execute(
            "SELECT name, level, extra_status FROM magic WHERE id = ? AND level = ?",
            (code // 100, code % 100),
        ).fetchone()
        # 585 skill codes equal some items.id. Those items never give a timed
        # effect, so an item only wins when it does (or when no skill matches).
        if item is not None and (item[2] or item[1] or skill is None):
            group = group_of(item[1]) or 0
            return BuffDef(item[0], None, group, _kind(group))
        if skill is not None:
            group = group_of(skill[2]) or 0
            return BuffDef(skill[0], skill[1], group, _kind(group))
        return None
    finally:
        con.close()


@dataclass
class _Entry:
    expires_at: float | None  # unix seconds; None when only seen in a `buffs` reply
    added: float  # monotonic, for FRESH


class _Pid:
    def __init__(self) -> None:
        self.entries: dict[int, _Entry] = {}
        self.synced = False  # one `buffs` reply or 0x29 seen since the hook connected
        self.next_reconcile = 0.0
        self.lock = threading.Lock()


class BuffTracker:
    def __init__(
        self,
        connected: Callable[[int], bool],
        pids: Callable[[], list[int]],
        channel: CommandChannel | None = None,
        defs: Callable[[int], BuffDef | None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self._connected = connected
        self._pids = pids
        self._channel = channel or CommandChannel()
        self._defs = defs or load_buff_defs()
        self._clock = clock
        self._wall = wall
        self._state: dict[int, _Pid] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="buff-tracker")
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()

    def _pid(self, pid: int) -> _Pid:
        with self._lock:
            st = self._state.get(pid)
            if st is None:
                st = self._state[pid] = _Pid()
            return st

    # -- sources -------------------------------------------------------------

    def on_packet(self, pid: int, raw: bytes, ts: float) -> None:
        """A 0x29 from the event pipe (HookHub listener; keep it quick)."""
        decoded = decode_buff(raw)
        if decoded is None:
            return
        code, on, ms = decoded
        st = self._pid(pid)
        with st.lock:
            st.synced = True
            if on:
                st.entries[code] = _Entry(ts + ms / 1000, self._clock())
            else:
                st.entries.pop(code, None)

    def reconcile(self, pid: int) -> None:
        """Bring the list in line with a `buffs` reply."""
        st = self._pid(pid)
        try:
            reply = self._channel.send(pid, "buffs")
        except HookCmdError:
            st.next_reconcile = self._clock() + RECONCILE_EVERY
            return
        now = self._clock()
        if not reply.get("ok") or not isinstance(reply.get("buffs"), list):
            error = str(reply.get("error", ""))
            # Mid map change the own character is not built yet: try again soon.
            transient = "not found" in error
            st.next_reconcile = now + (RECONCILE_EVERY if transient else BACKOFF)
            return
        codes = {b.get("code") for b in reply["buffs"] if isinstance(b, dict)}
        with st.lock:
            for code in codes:
                if isinstance(code, int) and code not in st.entries:
                    # Only a packet-added buff gets the FRESH grace.
                    st.entries[code] = _Entry(None, now - FRESH)
            for code, entry in list(st.entries.items()):
                if code not in codes and now - entry.added >= FRESH:
                    del st.entries[code]
            st.synced = True
        st.next_reconcile = now + RECONCILE_EVERY

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                live = set(self._pids())
                with self._lock:
                    for pid in list(self._state):
                        if pid not in live or not self._connected(pid):
                            del self._state[pid]  # hook gone: the list is no longer known
                for pid in live:
                    if self._connected(pid) and self._pid(pid).next_reconcile <= self._clock():
                        self.reconcile(pid)
            except Exception:  # pragma: no cover - keep tracking the other clients
                log.exception("buff tracker pass failed", extra={"cat": "hook"})
            self._stop.wait(1.0)

    # -- read ----------------------------------------------------------------

    def buffs(self, pid: int) -> list[BuffInfo] | None:
        """The own buffs from the hook, or None when the hook does not track them."""
        with self._lock:
            st = self._state.get(pid)
        if st is None or not self._connected(pid):
            return None
        wall = self._wall()
        with st.lock:
            if not st.synced:
                return None
            items = list(st.entries.items())
        out = []
        for code, entry in items:
            if entry.expires_at is not None and entry.expires_at < wall - FRESH:
                continue  # its off packet was lost; the next reconcile drops it
            d = self._defs(code)
            out.append(
                BuffInfo(
                    group=d.group if d else 0,
                    name=d.name if d else f"#{code}",
                    kind=d.kind if d else "buff",
                    code=code,
                    level=d.level if d else None,
                    expires_at=entry.expires_at,
                    source="hook",
                )
            )
        order = {"hero": 0, "buff": 1, "debuff": 2}
        out.sort(key=lambda b: (order[b.kind], b.expires_at or float("inf")))
        return out


def merge_buffs(hooked: list[BuffInfo] | None, memory: list[BuffInfo]) -> list[BuffInfo]:
    """The hook's list when it has one, plus memory debuffs it does not cover.

    An attack-applied debuff (not cast as a skill) is only in the memory array.
    """
    if hooked is None:
        return memory
    covered = {b.group for b in hooked if b.group}
    extra = [
        b.model_copy(update={"source": "memory"})
        for b in memory
        if b.kind == "debuff" and b.group not in covered
    ]
    return hooked + extra
