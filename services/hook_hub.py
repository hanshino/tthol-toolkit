"""Reader for the optional per-client hook event pipe (chat log).

A hooked game client serves JSON lines on \\\\.\\pipe\\tthol-hook-<pid>:

    {"t":"hello","v":<protocol>,...}
    {"t":"msg","seq":..,"ts_us":<unix us>,"type":900,"self":..,"self_key":"<hex>"|null,"raw":"<hex>"}
    {"t":"drop","n":..}
    {"t":"hb"}

The pipe takes one reader at a time, so while the app runs it is that reader
(set TTHOL_NO_HOOK=1 to leave the pipes alone). Only inbound chat (game packet
sub-type 0x0E) and system lines (0xFD) are decoded and kept, in memory; the own
HP / MP packet (0x06) is decoded and handed to listeners (the guard), not kept;
sub-types a packet listener asked for (0x29 own buffs, services.buff_tracker)
are handed over raw; every other message is dropped unread. Nothing here writes to the pipe.

World shouts and system lines carry an id into the client's template table
(reader.read_string_table, read from game memory through the worker). They are
filled in when the log is read, so a table that loads late still applies.
"""

from __future__ import annotations

import json
import logging
import os
import re
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from reader import read_string_table
from services.api_types import ChatLog, ChatMessage, HookInfo

log = logging.getLogger("tthol.hook_hub")

PIPE_DIR = "\\\\.\\pipe\\"
PIPE_PREFIX = "tthol-hook-"
GAME_PACKET = 900
CHAT = 0x0E
SYSTEM = 0xFD
VITALS = 0x06
CHAT_TEXT_OFFSET = 29
KEEP = 500
SCAN_INTERVAL = 1.0
STRINGS_RETRY = 10.0  # seconds between template table reads while it is unavailable

CHANNELS = {1: "normal", 2: "whisper", 3: "party", 4: "family", 5: "area", 200: "shout"}


@dataclass(frozen=True)
class ChatPacket:
    key: bytes  # sender key (u16 kind, u32 npc id, u32 instance); zero for family / shout
    channel: str
    echo: bool  # the sender's own copy; for a whisper the name is then the target
    name: str
    text: str


def _cstr(b: bytes) -> str:
    # Fixed-size field: the server leaves stale bytes after the NUL. cp950 also
    # covers the ETEN characters player names use.
    return b.split(b"\0", 1)[0].decode("cp950", errors="replace")


def decode_chat(raw: bytes) -> ChatPacket | None:
    """0E <sender key 10> <u8 channel> <u8 echo> <name[16]> <text> 00."""
    if len(raw) < CHAT_TEXT_OFFSET + 1 or raw[0] != CHAT:
        return None
    channel = CHANNELS.get(raw[11], str(raw[11]))
    return ChatPacket(
        raw[1:11], channel, bool(raw[12]), _cstr(raw[13:29]), _cstr(raw[CHAT_TEXT_OFFSET:])
    )


def decode_vitals(raw: bytes) -> tuple[bytes, int, int] | None:
    """06 <own key 10> <u32 HP> <u32 MP>: pushed on every change of the player's HP or MP."""
    if len(raw) < 19 or raw[0] != VITALS:
        return None
    hp, mp = struct.unpack_from("<II", raw, 11)
    return raw[1:11], hp, mp


def decode_system(raw: bytes) -> tuple[int, list[str]] | None:
    """FD <u16 template id> <cp950 arg> 00, e.g. 60077 + a family member's name.

    Only one-arg lines have been seen; several args are assumed NUL separated.
    """
    if len(raw) < 3 or raw[0] != SYSTEM:
        return None
    args = [a.decode("cp950", errors="replace") for a in raw[3:].split(b"\0")]
    while args and not args[-1]:
        args.pop()
    return struct.unpack_from("<H", raw, 1)[0], args


_SPEC = re.compile(r"%%|%[-+ 0#]*\d*(?:\.\d+)?[sdiuxXc]")
_SAYS = "%s 說> "


def fill(template: str, args: list[str]) -> str | None:
    """printf-style template with args in order; None when the counts differ."""
    if sum(1 for m in _SPEC.finditer(template) if m.group() != "%%") != len(args):
        return None
    it = iter(args)
    return _SPEC.sub(lambda m: "%" if m.group() == "%%" else next(it), template)


def render_shout(text: str, name: str, strings: dict[int, str]) -> str:
    """'6035 天芯 聖龍靴毛胚 蔚藍聖龍鞋' -> its template, minus the 'name 說>' lead the row already shows."""
    head, _, rest = text.partition(" ")
    if not head.isdigit() or int(head) not in strings:
        return text
    template, args = strings[int(head)], rest.split(" ") if rest else []
    # The last arg is free text (6014 "%s (%s) 大聲說> %s") and may hold spaces.
    n = sum(1 for m in _SPEC.finditer(template) if m.group() != "%%")
    if 0 < n < len(args):
        args = args[: n - 1] + [" ".join(args[n - 1 :])]
    if template.startswith(_SAYS) and args and args[0] == name:
        template, args = template[len(_SAYS) :], args[1:]
    return fill(template, args) or text


def render_system(template_id: int, args: list[str], strings: dict[int, str]) -> str:
    template = strings.get(template_id)
    text = fill(template, args) if template is not None else None
    return text if text is not None else " ".join([f"#{template_id}", *args])


def read_templates(pm, _hp_addr, _compat_mode) -> dict[int, str]:
    """WorkerManager.read_locked reader for the template table."""
    return read_string_table(pm)


def list_hook_pids() -> list[int]:
    try:
        names = os.listdir(PIPE_DIR)
    except OSError:
        return []
    return [
        int(n[len(PIPE_PREFIX) :])
        for n in names
        if n.startswith(PIPE_PREFIX) and n[len(PIPE_PREFIX) :].isdigit()
    ]


@dataclass
class _Line:
    seq: int
    ts: float
    channel: str
    echo: bool
    own: bool
    name: str
    text: str
    template: int | None = None  # system line: id into the template table
    args: list[str] = field(default_factory=list)


class _Feed:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.messages: deque[_Line] = deque(maxlen=KEEP)
        self.seq = 0
        self.proto: int | None = None  # set while a reader is connected
        self.reader: threading.Thread | None = None


class HookHub:
    def __init__(
        self,
        list_pids=list_hook_pids,
        strings: Callable[[int], dict[int, str] | None] | None = None,
    ) -> None:
        self._list_pids = list_pids
        self._load_strings = strings
        self._strings: dict[int, dict[int, str]] = {}
        self._strings_tried: dict[int, float] = {}
        self._feeds: dict[int, _Feed] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._scanner: threading.Thread | None = None
        self._vitals_listeners: list[Callable[[int, int, int], None]] = []
        self._link_listeners: list[Callable[[int], None]] = []
        self._packet_listeners: dict[
            int, list[Callable[[int, bytes, float, bytes | None], None]]
        ] = {}

    def start(self) -> None:
        if os.environ.get("TTHOL_NO_HOOK") == "1":
            log.info("hook pipes disabled by TTHOL_NO_HOOK", extra={"cat": "hook"})
            return
        self._scanner = threading.Thread(target=self._scan_loop, daemon=True, name="hook-scan")
        self._scanner.start()

    def add_vitals_listener(self, listener: Callable[[int, int, int], None]) -> None:
        """listener(pid, hp, mp) on every own 0x06, on the pipe reader thread: keep it quick."""
        self._vitals_listeners.append(listener)

    def add_link_listener(self, listener: Callable[[int], None]) -> None:
        """listener(pid) when a hook pipe says hello or closes (a new or gone hook build)."""
        self._link_listeners.append(listener)

    def _link_changed(self, pid: int) -> None:
        for listener in self._link_listeners:
            try:
                listener(pid)
            except Exception:  # pragma: no cover
                log.exception("hook link listener failed", extra={"cat": "hook"})

    def add_packet_listener(
        self, sub_type: int, listener: Callable[[int, bytes, float, bytes | None], None]
    ) -> None:
        """listener(pid, raw, unix ts, own key or None) per inbound packet of this sub-type.

        Runs on the pipe reader thread: keep it quick. The own key changes on
        every map change, so it comes with each packet.
        """
        self._packet_listeners.setdefault(sub_type, []).append(listener)

    def set_strings(self, strings: Callable[[int], dict[int, str] | None]) -> None:
        """Where the template table comes from: pid -> {id: text}, or None while unavailable."""
        self._load_strings = strings

    def shutdown(self) -> None:
        # Readers block in ReadFile; they are daemons and end with the process.
        self._stop.set()

    def connected_pids(self) -> list[int]:
        """Pids whose event pipe this hub is reading now."""
        with self._lock:
            return [pid for pid, feed in self._feeds.items() if feed.proto is not None]

    def status(self, pid: int) -> HookInfo | None:
        feed = self._feeds.get(pid)
        if feed is None or feed.proto is None:
            return None
        return HookInfo(proto=feed.proto)

    def chat(self, pid: int, after: int = 0) -> ChatLog:
        feed = self._feeds.get(pid)
        if feed is None:
            return ChatLog(connected=False, proto=None, last_seq=0, messages=[])
        with feed.lock:
            # A client that outlived a restart of the app holds seqs from the
            # old run: hand it everything.
            if after > feed.seq:
                after = 0
            lines = [m for m in feed.messages if m.seq > after]
            connected, proto, last_seq = feed.proto is not None, feed.proto, feed.seq
        templated = any(m.template is not None or m.channel == "shout" for m in lines)
        strings = self._templates(pid) if templated else {}
        return ChatLog(
            connected=connected,
            proto=proto,
            last_seq=last_seq,
            messages=[_render(m, strings) for m in lines],
        )

    def _templates(self, pid: int) -> dict[int, str]:
        """The client's template table, read once per process (fixed for a game build)."""
        if pid in self._strings or self._load_strings is None:
            return self._strings.get(pid, {})
        now = time.monotonic()
        if now - self._strings_tried.get(pid, -STRINGS_RETRY) < STRINGS_RETRY:
            return {}
        self._strings_tried[pid] = now
        try:
            table = self._load_strings(pid)
        except Exception:
            log.exception("template table read failed pid=%d", pid, extra={"cat": "hook"})
            table = None
        if table:
            self._strings[pid] = table
            log.info("template table loaded pid=%d n=%d", pid, len(table), extra={"cat": "hook"})
        return table or {}

    def _feed(self, pid: int) -> _Feed:
        with self._lock:
            feed = self._feeds.get(pid)
            if feed is None:
                feed = self._feeds[pid] = _Feed()
            return feed

    def _scan_loop(self) -> None:
        while not self._stop.is_set():
            try:
                for pid in self._list_pids():
                    feed = self._feed(pid)
                    if feed.reader is None or not feed.reader.is_alive():
                        feed.reader = threading.Thread(
                            target=self._read, args=(pid,), daemon=True, name=f"hook-pipe-{pid}"
                        )
                        feed.reader.start()
            except Exception:  # pragma: no cover
                log.exception("hook pipe scan failed", extra={"cat": "hook"})
            self._stop.wait(SCAN_INTERVAL)

    def _read(self, pid: int) -> None:
        try:
            # Busy (another reader such as hook_tail) or gone: try again on the next scan.
            pipe = open(f"{PIPE_DIR}{PIPE_PREFIX}{pid}", "rb", buffering=0)
        except OSError:
            return
        log.info("hook pipe connected pid=%d", pid, extra={"cat": "hook"})
        try:
            with pipe:
                buf = b""
                while not self._stop.is_set():
                    chunk = pipe.read(65536)
                    if not chunk:
                        break
                    buf += chunk
                    *lines, buf = buf.split(b"\n")
                    for line in lines:
                        if line:
                            self._ingest(pid, json.loads(line))
        except (OSError, ValueError):
            pass
        finally:
            self._feed(pid).proto = None
            self._link_changed(pid)
            log.info("hook pipe closed pid=%d", pid, extra={"cat": "hook"})

    def _ingest(self, pid: int, ev: dict) -> None:
        t = ev.get("t")
        if t == "hello":
            self._feed(pid).proto = int(ev.get("v") or 0)
            self._link_changed(pid)
            return
        if t != "msg" or ev.get("type") != GAME_PACKET:
            return
        raw_hex = ev.get("raw") or ""
        if raw_hex[:2] == "06":
            self._vitals(pid, ev, raw_hex)
            return
        listeners = self._packet_listeners.get(int(raw_hex[:2] or "0", 16)) if raw_hex else None
        if listeners:
            raw = bytes.fromhex(raw_hex)
            ts_us = ev.get("ts_us")
            ts = ts_us / 1e6 if ts_us else time.time()
            self_key = ev.get("self_key")
            own = bytes.fromhex(self_key) if self_key else None
            for listener in listeners:
                try:
                    listener(pid, raw, ts, own)
                except Exception:
                    log.exception("packet listener failed pid=%d", pid, extra={"cat": "hook"})
            return
        if raw_hex[:2].lower() not in ("0e", "fd"):
            return  # anything but chat and system lines is never decoded or kept
        raw = bytes.fromhex(raw_hex)
        ts_us = ev.get("ts_us")
        ts = ts_us / 1e6 if ts_us else time.time()
        if raw[0] == SYSTEM:
            system = decode_system(raw)
            if system is not None:
                template, args = system
                self._append(pid, _Line(0, ts, "system", False, False, "", "", template, args))
            return
        pkt = decode_chat(raw)
        if pkt is None:
            return
        self_key = ev.get("self_key")
        own = bool(self_key) and any(pkt.key) and pkt.key == bytes.fromhex(self_key)
        self._append(pid, _Line(0, ts, pkt.channel, pkt.echo, own, pkt.name, pkt.text))

    def _vitals(self, pid: int, ev: dict, raw_hex: str) -> None:
        if not self._vitals_listeners:
            return
        decoded = decode_vitals(bytes.fromhex(raw_hex))
        self_key = ev.get("self_key")
        # Only our own: the key changes on every map change, so compare per event.
        if decoded is None or not self_key or decoded[0] != bytes.fromhex(self_key):
            return
        _, hp, mp = decoded
        for listener in self._vitals_listeners:
            try:
                listener(pid, hp, mp)
            except Exception:
                log.exception("vitals listener failed pid=%d", pid, extra={"cat": "hook"})

    def _append(self, pid: int, line: _Line) -> None:
        feed = self._feed(pid)
        with feed.lock:
            feed.seq += 1
            line.seq = feed.seq
            feed.messages.append(line)


def _render(m: _Line, strings: dict[int, str]) -> ChatMessage:
    if m.template is not None:
        text = render_system(m.template, m.args, strings)
    elif m.channel == "shout":
        text = render_shout(m.text, m.name, strings)
    else:
        text = m.text
    return ChatMessage(
        seq=m.seq, ts=m.ts, channel=m.channel, echo=m.echo, own=m.own, name=m.name, text=text
    )
