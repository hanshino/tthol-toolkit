"""What the hook in each game client can do: its `caps` manifest, read once per
pid and shared by every module, and the toolkit features that manifest allows.

FEATURES is the one place that says which hook commands a feature needs. The
world snapshot carries the allowed feature names per character, and the UI
shows a hook feature only when its name is there, so a hook injected after the
app started, or an older hook build without a command, needs no special case.

A manifest is dropped whenever a hook pipe connects (hello) or closes: the next
hook may be another build.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from services.hook_cmd import CommandChannel, NoReply, PipeBusy, PipeGone

log = logging.getLogger("tthol.hook")

# feature -> the hook commands it needs. () = only the event pipe (chat packets).
FEATURES: dict[str, tuple[str, ...]] = {
    "chat": (),
    "guard": ("use",),
    # Ask the server for the family summary (0x31): the manor id is in no login packet.
    "family": ("family",),
    # 補給 trips; buy / sell / petput are checked per trip (only what it needs).
    "supply": ("status", "near", "walk", "talk", "dialog", "option", "next", "shop"),
    "daily.tower": (
        "status",
        "near",
        "walk",
        "attack",
        "cast",
        "talk",
        "dialog",
        "option",
        "next",
    ),
}

FRESH = 60.0  # a manifest older than this is re-read in the background
RETRY = 5.0  # after a failed background read (pipe busy, map load)


class _Entry:
    def __init__(self, at: float, names: frozenset[str] | None) -> None:
        self.at = at
        self.names = names  # None: the hook answered without a manifest (old build)


class HookCaps:
    def __init__(
        self,
        channel: CommandChannel | None = None,
        clock: Callable[[], float] = time.monotonic,
        connected: Callable[[int], bool] = lambda pid: True,
        background: bool = True,
    ) -> None:
        self._channel = channel or CommandChannel()
        self._clock = clock
        self._connected = connected
        self._background = background
        self._entries: dict[int, _Entry] = {}
        self._tried: dict[int, float] = {}  # pid -> clock of the last failed background read
        self._reading: set[int] = set()
        self._lock = threading.Lock()

    def read(self, pid: int) -> frozenset[str] | None:
        """Ask the hook now. None: it has no manifest (an older build).

        Raises PipeGone / PipeBusy / NoReply when the hook cannot be asked.
        """
        reply = self._channel.send(pid, "caps")
        if str(reply.get("error") or "").startswith("not in game"):
            # v5 at the login screens: nothing to learn yet, read again later.
            raise NoReply("not in game")
        commands = reply.get("commands") if reply.get("ok") else None
        names = (
            frozenset(c.get("cmd") for c in commands if isinstance(c, dict))
            if isinstance(commands, list)
            else None
        )
        with self._lock:
            self._entries[pid] = _Entry(self._clock(), names)
        return names

    def get(self, pid: int, max_age: float = 0.0) -> frozenset[str] | None:
        """The manifest, read again when older than `max_age` (0 = always)."""
        with self._lock:
            entry = self._entries.get(pid)
        if entry is not None and self._clock() - entry.at < max_age:
            return entry.names
        return self.read(pid)

    def cached(self, pid: int) -> frozenset[str] | None:
        """The last manifest read, of any age; None when there is none."""
        with self._lock:
            entry = self._entries.get(pid)
        return entry.names if entry is not None else None

    def put(self, pid: int, names: frozenset[str] | None) -> None:
        """Store a manifest as just read (tests, and a reply read elsewhere)."""
        with self._lock:
            self._entries[pid] = _Entry(self._clock(), names)

    def invalidate(self, pid: int) -> None:
        with self._lock:
            self._entries.pop(pid, None)
            self._tried.pop(pid, None)

    def features(self, pid: int) -> list[str]:
        """Features the hook in this client allows now. Never blocks: a missing
        or old manifest is read in the background for the next call."""
        if not self._connected(pid):
            return []
        with self._lock:
            entry = self._entries.get(pid)
        now = self._clock()
        if entry is None or now - entry.at >= FRESH:
            self._refresh(pid, now)
            with self._lock:
                entry = self._entries.get(pid)  # a foreground read (tests) is in already
        names = entry.names if entry is not None else None
        return [f for f, need in FEATURES.items() if not need or (names and set(need) <= names)]

    def _refresh(self, pid: int, now: float) -> None:
        with self._lock:
            if pid in self._reading or now - self._tried.get(pid, -RETRY) < RETRY:
                return
            self._reading.add(pid)
        if self._background:
            threading.Thread(
                target=self._read_quietly, args=(pid,), daemon=True, name=f"hook-caps-{pid}"
            ).start()
        else:
            self._read_quietly(pid)

    def _read_quietly(self, pid: int) -> None:
        try:
            self.read(pid)
        except (PipeGone, PipeBusy, NoReply):
            with self._lock:
                self._tried[pid] = self._clock()
        except Exception:
            log.exception("hook caps read failed pid=%d", pid, extra={"cat": "hook"})
            with self._lock:
                self._tried[pid] = self._clock()
        finally:
            with self._lock:
                self._reading.discard(pid)
