"""
Market survey: records player stalls as the player opens them in game.

One background thread polls every located character. While a character is on
a 市集 map (or recording is forced on) it watches for the shop window, waits
for the stall's items to settle, guards against the stale buffer of the
previous stall, and diffs each settled read into MarketDB.

See docs/plans/2026-10-02-market-survey.md.
"""

import logging
import struct
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import reader
from services.market_db import MarketDB, RecordResult, classify_price, fingerprint

log = logging.getLogger("tthol.market")

# stages.id of the 市集 maps: 莫愁谷 / 藏海村 / 成都 / 洛陽
MARKET_STAGE_IDS = frozenset({44, 172, 173, 174})

POLL_S = 0.1
STABLE_POLLS = 3  # identical reads in a row before a stall read counts
SELLER_TIMEOUT_S = 1.5  # no stalling seller label by then: an NPC shop
STALE_TIMEOUT_S = 3.0  # buffer still holds another seller's stall: give up
# Content that changes inside a window already recorded may be the next stall
# arriving before its seller label: give the label this long to catch up.
MIDWINDOW_SETTLE_S = 0.8
OWNER_CACHE = 1000  # fingerprints remembered for the stale-buffer guard
STALLERS_REFRESH_S = 15.0  # how often the stalls-in-view list is rescanned
LOG_SIZE = 40
# A pid briefly leaves the worker manager on 重偵 / relocate; keep its state.
GONE_GRACE_S = 30.0

Mode = Literal["auto", "on", "off"]


# ------------------------------------------------------------------
# Window-to-record state machine (pure: no memory access)
# ------------------------------------------------------------------
@dataclass
class Settled:
    seller: str
    items: tuple
    opened_at: float
    fingerprint: str


class StallTracker:
    """Turns per-poll observations into stall events.

    observe() returns a list of (kind, payload): 'opened' (seller or None),
    'closed', 'npc', 'stale' (seller) and 'settled' (Settled). After recording a
    Settled read the caller must call mark_recorded().
    """

    def __init__(self):
        self._owner: OrderedDict[str, str] = (
            OrderedDict()
        )  # fingerprint -> seller it was recorded for
        self.reset()

    def reset(self):
        """Forget the window state; keep which seller each recorded read belongs to."""
        self.wnd = None
        self.seller = None
        self.opened_at = 0.0
        self._started = False
        self._closed_content = None
        self._last = None
        self._stable = 0
        self._changed_at = 0.0
        self._done = False
        self._recorded = None  # fingerprint recorded in the current window
        self._recorded_content = None

    def observe(self, now, wnd, seller, content):
        events = []
        if not self._started:
            # Data can land before the window shows, so the baseline for the
            # next window is what the buffer held when the last one closed.
            self._closed_content = content
            self._started = True
        prev = self._last
        self._stable = self._stable + 1 if content is not None and content == prev else 0
        if content != prev:
            self._changed_at = now
        self._last = content
        if wnd is None:
            if self.wnd is not None:
                events.append(("closed", self.seller))
                self._closed_content = content
            self.wnd = self.seller = None
            return events
        if wnd != self.wnd or (seller and self.seller and seller != self.seller):
            if self.wnd is not None:
                # Switched stalls without closing: the previous stall's data is
                # what was recorded for it (the buffer may already hold the next).
                self._closed_content = (
                    self._recorded_content if self._recorded_content is not None else prev
                )
            self.wnd, self.seller, self.opened_at = wnd, seller, now
            self._done, self._recorded, self._recorded_content = False, None, None
            events.append(("opened", seller))
        elif self.seller is None and seller:
            self.seller = seller
        if self._done:
            return events
        waited = now - self.opened_at
        if self.seller is None:
            if waited > SELLER_TIMEOUT_S:
                self._done = True
                events.append(("npc", None))
            return events
        # An empty read is a buffer mid-refresh far more often than an empty
        # stall, and recording it would end every listing the seller has.
        if self._stable < STABLE_POLLS or not content:
            return events
        fp = fingerprint(content)
        if fp == self._recorded:
            return events
        if self._recorded is not None and now - self._changed_at < MIDWINDOW_SETTLE_S:
            return events
        if content == self._closed_content:
            owner = self._owner.get(fp)
            if owner is None:
                # Nobody owns this buffer (it predates recording). Unchanged
                # this long, it is the same stall re-opened: fresh data for a
                # different stall arrives well within the timeout.
                if waited <= STALE_TIMEOUT_S:
                    return events
            elif owner != self.seller:
                if waited > STALE_TIMEOUT_S:
                    self._done = True
                    events.append(("stale", self.seller))
                return events
        events.append(("settled", Settled(self.seller, content, self.opened_at, fp)))
        return events

    def mark_recorded(self, settled: Settled):
        # Keep watching while the window stays open: a re-opened stall settles
        # at once on the old buffer and fresh server data may replace it later.
        self._owner[settled.fingerprint] = settled.seller
        self._owner.move_to_end(settled.fingerprint)
        while len(self._owner) > OWNER_CACHE:
            self._owner.popitem(last=False)
        self._recorded = settled.fingerprint
        self._recorded_content = settled.items


# ------------------------------------------------------------------
# Per-character state + manager
# ------------------------------------------------------------------
@dataclass
class _Char:
    tracker: StallTracker = field(default_factory=StallTracker)
    active: bool = False
    reason: str = "waiting"  # recording | not_market | off | no_character
    stage_id: int | None = None
    map_name: str | None = None
    stallers: dict[str, reader.Staller] = field(default_factory=dict)
    viewer: tuple[int, int] | None = None  # the character's own tile, read every poll
    stallers_at: float = 0.0
    rescanned_wnd: int | None = None
    current: dict | None = None
    log: deque = field(default_factory=lambda: deque(maxlen=LOG_SIZE))
    session_sellers: set[str] = field(default_factory=set)
    session_new: int = 0
    session_reads: int = 0


class MarketSurveyManager:
    def __init__(
        self,
        live: Callable[[int], tuple | None],
        pids: Callable[[], list[int]],
        db: MarketDB,
        clock: Callable[[], float] = time.time,
    ):
        self._live = live
        self._pids = pids
        self._db = db
        self._clock = clock
        self._chars: dict[int, _Char] = {}
        # Kept apart from _Char so a user's choice outlives the character state.
        self._modes: dict[int, Mode] = {}
        self._absent_since: dict[int, float] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------
    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="market-survey", daemon=True)
            self._thread.start()

    def shutdown(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self):
        while not self._stop.wait(POLL_S):
            live = set(self._pids())
            for pid in live:
                try:
                    self.step(pid)
                except Exception:
                    log.exception("market survey step failed for pid %s", pid)
            self._forget_gone(live)

    def _forget_gone(self, live: set[int]):
        now = self._clock()
        with self._lock:
            for pid in live:
                self._absent_since.pop(pid, None)
            for pid in set(self._chars) - live:
                since = self._absent_since.setdefault(pid, now)
                if now - since > GONE_GRACE_S:
                    del self._chars[pid]
                    self._modes.pop(pid, None)
                    del self._absent_since[pid]

    # -- control -----------------------------------------------------
    def set_mode(self, pid: int, mode: Mode):
        with self._lock:
            self._modes[pid] = mode

    def _char(self, pid: int) -> _Char:
        with self._lock:
            return self._chars.setdefault(pid, _Char())

    # -- one poll ----------------------------------------------------
    def step(self, pid: int):
        ch = self._char(pid)
        handle = self._live(pid)
        if handle is None:
            self._pause(ch, "no_character")
            return
        pm, hp_addr = handle
        stage = reader.read_stage(pm)
        if stage is not None:
            ch.stage_id, ch.map_name = stage
        on_market = stage is not None and stage[0] in MARKET_STAGE_IDS
        mode = self._modes.get(pid, "auto")
        if mode == "off" or (mode == "auto" and not on_market):
            self._pause(ch, "off" if mode == "off" else "not_market")
            return
        if not ch.active:
            ch.active, ch.reason = True, "recording"
            self._log(ch, "start", f"開始記錄（{ch.map_name or '未知地圖'}）")
        scan_at = self._clock()
        if scan_at - ch.stallers_at > STALLERS_REFRESH_S:
            ch.stallers, ch.stallers_at = reader.scan_stallers(pm, hp_addr), scan_at
        windows = reader._window_list(pm)
        wnd = reader.find_shop_window(pm, windows)
        seller = None
        if wnd is not None:
            labels = reader.read_shop_labels(pm, windows, wnd)
            seller = next((t for t in labels if t in ch.stallers), None)
            if seller is None and labels and ch.rescanned_wnd != wnd:
                # The seller may have walked into view after the last scan.
                ch.stallers, ch.stallers_at, ch.rescanned_wnd = (
                    reader.scan_stallers(pm, hp_addr),
                    scan_at,
                    wnd,
                )
                seller = next((t for t in labels if t in ch.stallers), None)
        content = reader.read_viewed_stall(pm, hp_addr)
        ch.viewer = _own_tile(pm, hp_addr) or ch.viewer
        # Taken after the heap scans (~1 s each), so a scan does not eat into
        # the tracker's seller / stale timeouts.
        now = self._clock()
        for kind, payload in ch.tracker.observe(now, wnd, seller, content):
            self._handle(ch, kind, payload, now)

    def _pause(self, ch: _Char, reason: str):
        if ch.active:
            self._log(ch, "stop", "離開市集，暫停記錄" if reason == "not_market" else "停止記錄")
        ch.active, ch.reason = False, reason
        ch.tracker.reset()
        if ch.current is not None:
            ch.current["open"] = False

    def _handle(self, ch: _Char, kind: str, payload, now: float):
        if kind == "opened":
            ch.current = None
        elif kind == "closed":
            if ch.current is not None:
                ch.current["open"] = False
        elif kind == "npc":
            self._log(ch, "npc", "不是玩家攤位，略過")
        elif kind == "stale":
            self._log(ch, "stale", "讀到的是上一攤的資料，這次略過", payload)
        elif kind == "settled":
            self._record(ch, payload, now)

    def _record(self, ch: _Char, s: Settled, now: float):
        staller = ch.stallers.get(s.seller)
        sign = staller.sign if staller else ""
        pos = (staller.x, staller.y) if staller and staller.x is not None else None
        # Marked first: a failing write must not be retried every poll.
        ch.tracker.mark_recorded(s)
        try:
            result = self._db.record(
                s.seller,
                sign,
                ch.stage_id,
                ch.map_name,
                s.opened_at,
                s.items,
                now=now,
                pos=pos,
                viewer=ch.viewer,
            )
        except Exception:
            log.exception("market survey: recording %s failed", s.seller)
            self._log(ch, "error", "寫入紀錄失敗，詳情見診斷", s.seller)
            return
        prior = (
            ch.current
            if ch.current and ch.current["seller"] == s.seller and ch.current["open"]
            else None
        )
        ch.current = {
            "seller": s.seller,
            "sign": sign,
            "x": pos[0] if pos else None,
            "y": pos[1] if pos else None,
            "viewer_x": ch.viewer[0] if ch.viewer else None,
            "viewer_y": ch.viewer[1] if ch.viewer else None,
            "open": True,
            "opened_at": s.opened_at,
            "recorded_at": now,
            "settle_s": round(now - s.opened_at, 2),
            "rows": result.rows,
            "gone": result.gone,
            "new": result.new,
            "unchanged": result.unchanged,
            "changed": len(result.changed),
        }
        ch.session_sellers.add(s.seller)
        ch.session_new += result.new
        ch.session_reads += 1
        self._log(ch, "recorded", _summary(result), s.seller, refresh=prior is not None)

    def _log(self, ch: _Char, kind: str, text: str, seller: str | None = None, refresh=False):
        ch.log.appendleft(
            {"t": self._clock(), "kind": kind, "seller": seller, "text": text, "refresh": refresh}
        )

    # -- read side ---------------------------------------------------
    def status(self, pid: int) -> dict:
        ch = self._char(pid)
        recorded = self._db.last_recorded()
        stalls = [
            {
                "seller": name,
                "sign": st.sign,
                "x": st.x,
                "y": st.y,
                "last_recorded": recorded.get(name),
            }
            for name, st in sorted(ch.stallers.items())
        ]
        return {
            "mode": self._modes.get(pid, "auto"),
            "active": ch.active,
            "reason": ch.reason,
            "stage_id": ch.stage_id,
            "map_name": ch.map_name,
            "stalls": stalls,
            "current": ch.current,
            "log": list(ch.log),
            "session": {
                "stalls": len(ch.session_sellers),
                "new": ch.session_new,
                "reads": ch.session_reads,
            },
        }


def _own_tile(pm, hp_addr) -> tuple[int, int] | None:
    """The character's own tile; None right after a map change (reads -1)."""
    try:
        x, y = struct.unpack("<ii", pm.read_bytes(hp_addr + reader.TILE_X_OFFSET, 8))
    except Exception:
        return None
    return (x, y) if 0 <= x < 10_000 and 0 <= y < 10_000 else None


def _summary(r: RecordResult) -> str:
    parts = []
    if r.new:
        parts.append(f"新上架 {r.new}")
    if r.unchanged:
        parts.append(f"未變 {r.unchanged}")
    if r.changed:
        parts.append(f"數量變化 {len(r.changed)}")
    if r.gone:
        parts.append(f"已不在 {len(r.gone)}")
    negotiate = sum(classify_price(row["price"])[0] == "negotiate" for row in r.rows)
    if negotiate:
        parts.append(f"其中議價 {negotiate}")
    return " · ".join(parts) or "空攤位"
