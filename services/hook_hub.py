"""Reader for the optional per-client hook event pipe (chat log).

A hooked game client serves JSON lines on \\\\.\\pipe\\tthol-hook-<pid>:

    {"t":"hello","v":<protocol>,...}
    {"t":"msg","seq":..,"ts_us":<unix us>,"type":900,"self":..,"self_key":"<hex>"|null,"raw":"<hex>"}
    {"t":"drop","n":..}
    {"t":"hb"}

The pipe takes one reader at a time, so while the app runs it is that reader
(set TTHOL_NO_HOOK=1 to leave the pipes alone). Only inbound chat (game packet
sub-type 0x0E) is decoded and kept, in memory; every other message is dropped
unread. Nothing here writes to the pipe or touches game memory.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass

from services.api_types import ChatLog, ChatMessage, HookInfo

log = logging.getLogger("tthol.hook_hub")

PIPE_DIR = "\\\\.\\pipe\\"
PIPE_PREFIX = "tthol-hook-"
GAME_PACKET = 900
CHAT = 0x0E
CHAT_TEXT_OFFSET = 29
KEEP = 500
SCAN_INTERVAL = 1.0

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


class _Feed:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.messages: deque[ChatMessage] = deque(maxlen=KEEP)
        self.seq = 0
        self.proto: int | None = None  # set while a reader is connected
        self.reader: threading.Thread | None = None


class HookHub:
    def __init__(self, list_pids=list_hook_pids) -> None:
        self._list_pids = list_pids
        self._feeds: dict[int, _Feed] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._scanner: threading.Thread | None = None

    def start(self) -> None:
        if os.environ.get("TTHOL_NO_HOOK") == "1":
            log.info("hook pipes disabled by TTHOL_NO_HOOK", extra={"cat": "hook"})
            return
        self._scanner = threading.Thread(target=self._scan_loop, daemon=True, name="hook-scan")
        self._scanner.start()

    def shutdown(self) -> None:
        # Readers block in ReadFile; they are daemons and end with the process.
        self._stop.set()

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
            msgs = [m for m in feed.messages if m.seq > after]
            return ChatLog(
                connected=feed.proto is not None, proto=feed.proto, last_seq=feed.seq, messages=msgs
            )

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
            log.info("hook pipe closed pid=%d", pid, extra={"cat": "hook"})

    def _ingest(self, pid: int, ev: dict) -> None:
        t = ev.get("t")
        if t == "hello":
            self._feed(pid).proto = int(ev.get("v") or 0)
            return
        if t != "msg" or ev.get("type") != GAME_PACKET:
            return
        raw_hex = ev.get("raw") or ""
        if raw_hex[:2].lower() != "0e":
            return  # anything but chat is never decoded or kept
        pkt = decode_chat(bytes.fromhex(raw_hex))
        if pkt is None:
            return
        self_key = ev.get("self_key")
        own = bool(self_key) and any(pkt.key) and pkt.key == bytes.fromhex(self_key)
        ts_us = ev.get("ts_us")
        feed = self._feed(pid)
        with feed.lock:
            feed.seq += 1
            feed.messages.append(
                ChatMessage(
                    seq=feed.seq,
                    ts=ts_us / 1e6 if ts_us else time.time(),
                    channel=pkt.channel,
                    echo=pkt.echo,
                    own=own,
                    name=pkt.name,
                    text=pkt.text,
                )
            )
