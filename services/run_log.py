"""Run record: what the modules did (guard, tower, navigator), on disk.

events.jsonl holds diagnostics (5 MB x 5 rotation). A tower run drinks some
2,000 times an hour per character, which would roll everything else out of it,
so these lines go to their own day files instead: logs/runs/YYYY-MM-DD.jsonl,
one event per line in the events.jsonl format. Opening a day's file sweeps the
folder: files older than KEEP_DAYS go, then the oldest until the folder is
under MAX_TOTAL_BYTES (the file being written is never removed).

HP trace: the guard feeds every HP / MP it sees into a per-pid ring of the
last TRACE_SECS. A death (HP 0 for DEATH_CONFIRM) or a close call (under
LOW_PCT) writes the ring out as one event, so the record shows how HP fell
(live 2026-10-07: a death on 第 50 層 that the in-memory log could not explain).
The same feed keeps per-floor numbers for the tower: the lowest HP and the
largest single drop.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from services.diag_events import event_from_record, event_to_json_line

log = logging.getLogger("tthol.run")

FOLDER_NAME = "runs"
KEEP_DAYS = 7
MAX_TOTAL_BYTES = 100_000_000
TRACE_SECS = 30.0
TRACE_KEEP = 1500  # samples; ~20 a second while HP moves fast
DEATH_CONFIRM = 3.0  # HP 0 this long is a death (a map load reads 0 for a moment)
LOW_PCT = 30  # HP under this share of max is a close call
LOW_GAP = 30.0  # at most one close call record this often per character
LOW_TRACE_SECS = 10.0
_DAY_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl$")


def sweep(folder: Path, keep: str, today: str, keep_days: int, max_bytes: int) -> list[Path]:
    """Delete day files older than `keep_days` before `today`, then the oldest
    until the folder is under `max_bytes`. `keep` (a file name) is never deleted.
    Returns what was deleted."""
    days = sorted(
        (m.group(1), p)
        for p in folder.glob("*.jsonl")
        if (m := _DAY_FILE.match(p.name)) and p.is_file()
    )
    cutoff = time.strftime(
        "%Y-%m-%d",
        time.localtime(time.mktime(time.strptime(today, "%Y-%m-%d")) - keep_days * 86400),
    )
    gone: list[Path] = []
    left: list[tuple[str, Path]] = []
    for day, p in days:
        if day <= cutoff and p.name != keep:
            gone.append(p)
        else:
            left.append((day, p))

    def size(p: Path) -> int:
        try:
            return p.stat().st_size
        except OSError:
            return 0

    total = sum(size(p) for _d, p in left)
    for _day, p in left:
        if total <= max_bytes:
            break
        if p.name == keep:
            continue
        total -= size(p)
        gone.append(p)
    deleted = []
    for p in gone:
        try:
            p.unlink()
            deleted.append(p)
        except OSError:
            pass
    return deleted


class DayFileHandler(logging.Handler):
    """One JSONL file per local day in `folder`; sweeps the folder on each new day."""

    def __init__(
        self, folder: Path, keep_days: int = KEEP_DAYS, max_bytes: int = MAX_TOTAL_BYTES
    ) -> None:
        super().__init__()
        self.folder = Path(folder)
        self.keep_days = keep_days
        self.max_bytes = max_bytes
        self._day: str | None = None
        self._stream = None

    def emit(self, record: logging.LogRecord) -> None:
        # Must never log: logging from inside emit() recurses into this handler.
        try:
            line = event_to_json_line(event_from_record(record))
            day = time.strftime("%Y-%m-%d", time.localtime(record.created))
            if day != self._day:
                self._open(day)
            self._stream.write(line + "\n")
            self._stream.flush()
        except Exception:
            pass

    def _open(self, day: str) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        self.folder.mkdir(parents=True, exist_ok=True)
        name = f"{day}.jsonl"
        self._stream = open(self.folder / name, "a", encoding="utf-8")
        self._day = day
        sweep(self.folder, name, day, self.keep_days, self.max_bytes)

    def close(self) -> None:
        with self.lock:
            if self._stream is not None:
                self._stream.close()
                self._stream = None
        super().close()


_installed: Path | None = None


def install(folder: Path) -> Path | None:
    """Send the run record to day files in `folder` (once). None when it cannot."""
    global _installed
    if _installed is not None:
        return _installed
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    log.addHandler(DayFileHandler(folder))
    log.setLevel(logging.INFO)
    log.propagate = False  # not in events.jsonl or the diagnostics buffer
    _installed = folder
    return folder


def folder() -> Path | None:
    return _installed


def note(source: str, pid: int | None, char: str | None, text: str, **detail) -> None:
    """One line of the run record. `source`: guard / tower / navigator / hp."""
    log.info(
        text,
        extra={"cat": source, "char_pid": pid, "char_name": char, "detail": detail or None},
    )


# ---- HP trace ---------------------------------------------------------------


@dataclass
class FloorStats:
    low_pct: int | None = None  # lowest HP share seen
    max_drop: int = 0  # largest fall between two samples
    max_drop_pct: int = 0


@dataclass
class _Track:
    samples: deque = field(default_factory=lambda: deque(maxlen=TRACE_KEEP))
    hp_max: int = 0
    mp_max: int = 0
    last: tuple[int, int] | None = None  # (hp, mp)
    zero_since: float | None = None
    dead_written: bool = False
    low_at: float = -LOW_GAP
    floor: FloorStats = field(default_factory=FloorStats)


_tracks: dict[int, _Track] = {}
_lock = threading.Lock()


def vitals(
    pid: int,
    hp: int,
    mp: int,
    hp_max: int | None = None,
    mp_max: int | None = None,
    src: str = "read",
    now: float | None = None,
) -> None:
    """One HP / MP seen (memory read or 0x06 packet). Cheap and no I/O: the
    packet reader thread calls it."""
    now = time.time() if now is None else now
    with _lock:
        t = _tracks.setdefault(pid, _Track())
        if hp_max:
            t.hp_max = hp_max
        if mp_max:
            t.mp_max = mp_max
        if t.last == (hp, mp):
            return
        prev = t.last
        t.last = (hp, mp)
        t.samples.append((now, hp, mp, src))
        if hp <= 0:
            if t.zero_since is None:
                t.zero_since = now  # when HP read 0, not when it was checked
        else:
            t.zero_since, t.dead_written = None, False
        if t.hp_max > 0:
            pct = max(hp, 0) * 100 // t.hp_max
            f = t.floor
            f.low_pct = pct if f.low_pct is None else min(f.low_pct, pct)
            if prev is not None and prev[0] > hp:
                drop = prev[0] - max(hp, 0)
                if drop > f.max_drop:
                    f.max_drop = drop
                    f.max_drop_pct = drop * 100 // t.hp_max


def _trace(t: _Track, now: float, secs: float) -> list:
    return [[round(ts - now, 2), hp, mp, src] for ts, hp, mp, src in t.samples if now - ts <= secs]


def check(pid: int, char: str | None, now: float | None = None) -> None:
    """Write a death or close call trace when one is due (the guard's tick)."""
    now = time.time() if now is None else now
    out: list[tuple[str, str, dict]] = []
    with _lock:
        t = _tracks.get(pid)
        if t is None or t.last is None:
            return
        hp = t.last[0]
        if t.zero_since is not None:
            if not t.dead_written and now - t.zero_since >= DEATH_CONFIRM:
                t.dead_written = True
                out.append(("hp", "death", _detail(t, now, TRACE_SECS)))
        elif t.hp_max > 0 and hp * 100 < LOW_PCT * t.hp_max and now - t.low_at >= LOW_GAP:
            t.low_at = now
            out.append(("hp", f"low hp {hp * 100 // t.hp_max}%", _detail(t, now, LOW_TRACE_SECS)))
    for source, text, detail in out:
        note(source, pid, char, text, **detail)


def _detail(t: _Track, now: float, secs: float) -> dict:
    return {
        "hp_max": t.hp_max,
        "mp_max": t.mp_max,
        "trace": _trace(t, now, secs),  # [seconds before now, hp, mp, read|packet]
    }


def floor_reset(pid: int) -> None:
    with _lock:
        t = _tracks.get(pid)
        if t is not None:
            t.floor = FloorStats()


def floor_stats(pid: int) -> FloorStats | None:
    with _lock:
        t = _tracks.get(pid)
        if t is None or t.floor.low_pct is None:
            return None
        f = t.floor
        return FloorStats(f.low_pct, f.max_drop, f.max_drop_pct)


def forget(pid: int) -> None:
    with _lock:
        _tracks.pop(pid, None)


def _reset_for_tests() -> None:
    global _installed
    for h in list(log.handlers):
        log.removeHandler(h)
        h.close()
    log.propagate = True
    _installed = None
    with _lock:
        _tracks.clear()
