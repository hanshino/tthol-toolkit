"""Background worker thread for one PID. No Qt -- uses callbacks.

States:
    DISCONNECTED  - process not found
    CONNECTING    - process found, scanning for character struct
    WAITING       - process found but character not yet located (waiting for login;
                    at the login / select screens it waits there without a limit)
    LOCATED       - polling every 3s from known address; position every 0.1s
    READ_ERROR    - validation failed 3x, triggers rescan
    RESCANNING    - re-running locate_character
"""

from __future__ import annotations

import os
import struct
import sys
import threading
import time
from collections.abc import Callable

import pymem

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reader import (
    WAREHOUSE_COUNT_OFFSET,
    get_display_fields,
    is_char_object,
    load_item_db,
    load_knowledge,
    load_status_db,
    locate_character,
    locate_map_name,
    locate_warehouse,
    read_active_statuses,
    read_all_fields,
    read_character_name,
    read_hp_from_player_chain,
    read_hp_pair_from_chain,
    read_inventory,
    read_item_container,
    read_appearance,
    read_equipment,
    read_money,
    read_pet_inventory,
    read_skills,
    read_stage,
    read_warehouse,
    verify_structure,
    verify_structure_shifted,
)
from services import diagnostics
from services.api_types import EquipSlot
from services.login_screen import PRE_GAME, screen_kind
from services.equip_stats import enhance_bonus, enhance_extra, inlays, to_stats
from services.diag_events import ErrorCode
from services.map_db import all_stage_names, minimap_base, stage_names_by_id

POLL_INTERVAL = 3.0
FAILURE_THRESHOLD = 3
LOCATE_RETRY_INTERVAL = 3.0
LOCATE_MAX_RETRIES = 10
MAP_RESCAN_EVERY = 5  # fallback locate_map_name walks the heap; cache between polls
# Between full polls the tile position is re-read on its own. One walking step
# takes ~215 ms at speed 12 (~170 ms at the cap of 15), so this sees every step.
POS_INTERVAL = 0.1
# Move target (click destination) in map pixels, x then y; equals the live
# position only once the character has arrived.
MOVE_TARGET_OFFSET = 636
# The struct is freed on a map change; the MSVC debug heap fills it with 0xDD.
FREED_FILL = struct.unpack("<i", b"\xdd" * 4)[0]
# A relocate after a map change runs while the new map loads, so its first
# retries come quickly; the attempt count (LOCATE_MAX_RETRIES) is unchanged.
QUICK_RETRY_INTERVAL = 0.5
QUICK_RETRIES = 4
# A lock outside a CCharObject is provisional (seen: a look-alike block found
# while the client was still logging in); look for the real one this often.
PROVISIONAL_RECHECK_EVERY = 5  # polls (~15 s)
# At the login / character select / protection password screens there is no
# character to find: watch the screen this often instead of scanning, without
# spending the locate retries.
SCREEN_POLL_INTERVAL = 1.0
# Right after a character switch the CCharObject shows up 5-15 s after the
# look-alike block, so the first polls of a provisional lock all look for it.
PROVISIONAL_FAST_POLLS = 6  # polls (~18 s)


def _provisional_recheck_due(map_tick: int) -> bool:
    """Whether this poll of a provisional lock looks for the CCharObject."""
    return map_tick <= PROVISIONAL_FAST_POLLS or map_tick % PROVISIONAL_RECHECK_EVERY == 0


class RelocateWindow:
    """Rate-limits repeated relocate reports inside a sliding window.

    The existing log shows `lost lock` / `re-acquired` cycling every ~9s during
    a bad session, which displaces the evidence. Frequency is itself the
    signal, so past the threshold the individual lines drop to DEBUG and the
    window closes with one summary count.
    """

    def __init__(self, window_seconds: float = 60.0, threshold: int = 2) -> None:
        self._window = window_seconds
        self._threshold = threshold
        self._start: float | None = None
        self._count = 0

    def should_log_at_info(self, now: float) -> bool:
        if self._start is None:
            self._start = now
        self._count += 1
        return self._count <= self._threshold

    def roll(self, now: float) -> int | None:
        """Close the window if it has elapsed; returns the count, or None."""
        if self._start is None or now - self._start < self._window:
            return None
        count = self._count
        self._start = None
        self._count = 0
        return count


class ReaderWorker(threading.Thread):
    """Per-PID worker thread. Calls callbacks instead of emitting Qt signals."""

    def __init__(
        self,
        pid: int,
        on_state: Callable[[str], None],
        on_stats: Callable[[list[tuple[str, int]]], None],
        on_inventory: Callable[[list[tuple[int, int, str]]], None],
        on_warehouse: Callable[[list[tuple[int, int, str]]], None],
        on_error: Callable[..., None],
        on_buffs: Callable[[list[tuple[int, str, str]]], None] | None = None,
        on_warehouse_open: Callable[[bool], None] | None = None,
        on_pet_inventory: Callable[[list[tuple[int, int, str]]], None] | None = None,
        on_money: Callable[[int], None] | None = None,
        on_appearance: Callable[[dict], None] | None = None,
        on_equipment: Callable[[list[EquipSlot]], None] | None = None,
        on_position: Callable[[int | None, str, int, int], None] | None = None,
        on_skills: Callable[[list[tuple[int, int]]], None] | None = None,
    ) -> None:
        super().__init__(daemon=True)
        self._pid = pid
        self._cb_state = on_state
        self._cb_stats = on_stats
        self._cb_inventory = on_inventory
        self._cb_warehouse = on_warehouse
        self._cb_error = on_error
        self._cb_buffs = on_buffs or (lambda _b: None)
        self._cb_warehouse_open = on_warehouse_open or (lambda _o: None)
        self._cb_pet_inventory = on_pet_inventory or (lambda _i: None)
        self._cb_money = on_money or (lambda _m: None)
        self._cb_appearance = on_appearance or (lambda _a: None)
        self._cb_equipment = on_equipment or (lambda _e: None)
        self._cb_position = on_position or (lambda _s, _n, _x, _y: None)
        self._cb_skills = on_skills or (lambda _s: None)
        self._hp_value: int | None = None
        self._offset_filters = None
        self._compat_mode = False
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._has_run = False
        self._scan_inventory = False
        self._scan_warehouse = False
        self._log = diagnostics.bind(pid)
        self._relocate_window = RelocateWindow()
        self._knowledge = load_knowledge()
        self._display_fields = get_display_fields(self._knowledge)
        offsets = {
            f["name"]: int(off)
            for off, f in self._knowledge["character_structure"]["fields"].items()
        }
        # Tile x and y are adjacent int32s, read together in one call.
        self._tile_offset = offsets["X座標"]
        assert offsets["Y座標"] == self._tile_offset + 4
        # Last position sent: (stage_id, map_name, x, y).
        self._pos_sent: tuple[int | None, str, int, int] | None = None
        # Tiles read when the stage changed; they belong to the old map until
        # they move, so samples equal to them are held back.
        self._stale_tiles: tuple[int, int] | None = None
        self._stage_bounds: dict[int, tuple[int, int] | None] = {}
        # Whether the current lock sits inside a CCharObject (set on each locate).
        self._lock_is_obj = False
        # (pm, hp_addr) of the lock the position loop is tracking, for walk_sample().
        self._live: tuple[object, int] | None = None
        self._item_db = load_item_db()
        self._status_db = load_status_db()
        try:
            self._stage_names = all_stage_names()
            self._stage_by_id = stage_names_by_id()
        except Exception:
            self._stage_names = None  # fall back to heuristic if DB unavailable
            self._stage_by_id = None

    # ------------------------------------------------------------------
    # Public API (called from main thread)
    # ------------------------------------------------------------------
    def start(self) -> None:
        self._has_run = True
        super().start()

    def has_run(self) -> bool:
        """True once start() has been called, alive or not.

        is_alive() reads False both before the first start and after the thread
        exits, but start() may only ever be called once -- owners need the two
        cases separated so they can rebuild instead of raising RuntimeError.
        """
        return self._has_run

    def connect(
        self,
        hp_value: int | None = None,
        offset_filters=None,
        compat_mode: bool = False,
    ) -> None:
        """Start the worker. hp_value is optional -- the stable pointer chain is tried first."""
        self._hp_value = hp_value
        self._offset_filters = offset_filters
        self._compat_mode = compat_mode
        self._stop_event.clear()
        if not self.is_alive():
            self.start()

    def request_inventory(self) -> None:
        self._scan_inventory = True
        self._wake_event.set()

    def request_warehouse(self) -> None:
        self._scan_warehouse = True
        self._wake_event.set()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        self._live = None

    def live_handle(self):
        """(pm, hp_addr) of the current lock for other readers (market survey), or None."""
        live = self._live
        if live is None or not self.is_alive():
            return None
        return live

    def read_locked(self, read):
        """read(pm, hp_addr, compat_mode) against the current lock, for one-off
        readers (stat-sim export). None while there is no lock, or when the lock
        moved during the read (the result may mix two structs)."""
        live = self._live
        if live is None or not self.is_alive():
            return None
        pm, hp_addr = live
        result = read(pm, hp_addr, self._compat_mode)
        # Compare by value: the position loop rebuilds the tuple every poll.
        if self._live != live:
            return None
        return result

    def walk_sample(self) -> tuple[int, int, int, int, int] | None:
        """(stage_id, x, y, target_px, target_py) read now, for the click-to-walk runner.

        Reads straight from the lock the position loop tracks, so it is as fresh
        as memory and independent of the 100 ms position stream. None while
        there is no lock or the stage cannot be trusted.
        """
        live = self._live
        if live is None or not self.is_alive():
            return None
        pm, hp_addr = live
        try:
            stage = read_stage(pm)
            if not self._trusted_stage(stage):
                return None
            x, y = struct.unpack("<ii", pm.read_bytes(hp_addr + self._tile_offset, 8))
            px, py = struct.unpack("<ii", pm.read_bytes(hp_addr + MOVE_TARGET_OFFSET, 8))
        except Exception:
            return None
        if FREED_FILL in (x, y, px, py) or self._live is not live:
            return None  # freed on a map change, or the lock moved while reading
        return stage[0], x, y, px, py

    # ------------------------------------------------------------------
    # Thread entry point
    # ------------------------------------------------------------------
    def run(self) -> None:  # pragma: no cover -- runtime only
        self._cb_state("CONNECTING")

        pm = self._connect_process()
        if pm is None:
            self._cb_state("DISCONNECTED")
            return

        hp_addr = self._locate_with_retries(pm, "WAITING")
        if hp_addr is None:
            self._cb_state("DISCONNECTED")
            return

        self._log.info("located character at 0x%08X (compat=%s)", hp_addr, self._compat_mode)
        self._cb_state("LOCATED")
        char_name = self._lock_name(pm, hp_addr)
        failure_count = 0
        map_name = ""
        stage_id = None
        map_tick = 0

        while not self._stop_event.is_set():
            if self._scan_inventory:
                self._scan_inventory = False
                self._do_inventory_scan(pm, hp_addr)

            if self._scan_warehouse:
                self._scan_warehouse = False
                self._do_warehouse_scan(pm)

            try:
                fields = read_all_fields(pm, hp_addr, self._display_fields, self._compat_mode)
                score = self._score(pm, hp_addr)

                if score < 0.8:
                    failure_count += 1
                    if failure_count >= FAILURE_THRESHOLD:
                        now = time.time()
                        lock_extra = {
                            "cat": "locate",
                            "code": ErrorCode.E_LOCK_LOST,
                            "detail": {"score": score, "hp_addr": hex(hp_addr)},
                        }
                        if self._relocate_window.should_log_at_info(now):
                            self._log.info(
                                "lost lock (validation score < 0.8 x%d); re-locating",
                                FAILURE_THRESHOLD,
                                extra=lock_extra,
                            )
                        else:
                            self._log.debug("lost lock (suppressed); re-locating", extra=lock_extra)
                        rolled = self._relocate_window.roll(now)
                        if rolled is not None:
                            self._log.info(
                                "relocated %d times in the last 60s",
                                rolled,
                                extra={"cat": "locate", "detail": {"relocates": rolled}},
                            )
                        self._cb_state("READ_ERROR")
                        hp_addr = self._locate_with_retries(pm, "RESCANNING")
                        if hp_addr is None:
                            self._cb_state("DISCONNECTED")
                            return
                        self._log.info(
                            "re-acquired at 0x%08X (compat=%s)", hp_addr, self._compat_mode
                        )
                        self._cb_state("LOCATED")
                        char_name = self._lock_name(pm, hp_addr)
                        failure_count = 0
                        map_name = ""
                        stage_id = None
                        map_tick = 0
                else:
                    failure_count = 0
                    stage = read_stage(pm)
                    if self._trusted_stage(stage):
                        stage_id, map_name = stage
                    elif map_tick % MAP_RESCAN_EVERY == 0 or not map_name:
                        # CStage global unreachable (e.g. moved by a client patch);
                        # the scan only yields the name, so the minimap goes dark.
                        stage_id = None
                        map_name = locate_map_name(pm, valid_names=self._stage_names)
                    map_tick += 1
                    if not self._lock_is_obj and _provisional_recheck_due(map_tick):
                        better = self._find_char_object(pm, hp_addr)
                        if better is not None:
                            hp_addr = better
                            char_name = self._lock_name(pm, hp_addr)
                            self._log.info(
                                "moved provisional lock to CCharObject at 0x%08X",
                                hp_addr,
                                extra={"cat": "locate"},
                            )
                            continue
                    if not char_name and map_tick > PROVISIONAL_FAST_POLLS:
                        # No CCharObject turned up: take the provisional lock as is.
                        char_name = read_character_name(pm, hp_addr)
                    # HP comes straight from the engine charobject pointer chain
                    # (no scan): authoritative and independent of the flat-struct
                    # lock, so it stays correct even if the scan locked a wrong
                    # same-HP candidate. Falls back to the flat struct's HP when
                    # the chain is unavailable.
                    hp_pair = read_hp_pair_from_chain(pm)
                    if hp_pair is not None:
                        cur, mx = hp_pair
                        fields = [
                            (n, cur if n == "血量" else mx if n == "最大血量" else v)
                            for n, v in fields
                        ]
                    self._cb_stats(
                        [("角色名稱", char_name), ("地圖名稱", map_name), ("地圖ID", stage_id)]
                        + fields
                    )
                    statuses = read_active_statuses(pm, hp_addr, self._knowledge)
                    self._cb_buffs(
                        [(g, self._status_db.get(g, f"group {g}"), kind) for g, kind in statuses]
                    )
                    # A provisional lock publishes no name, so the session still
                    # carries the previous character's: reads from the look-alike
                    # block would be filed under it.
                    if self._lock_is_obj or map_tick > PROVISIONAL_FAST_POLLS:
                        self._auto_read_items(pm, hp_addr)

            except Exception as exc:
                failure_count += 1
                if failure_count >= FAILURE_THRESHOLD:
                    self._log.info("read exception (%s); reconnecting + re-locating", exc)
                    self._cb_state("READ_ERROR")
                    pm = self._connect_process()
                    if pm is None:
                        self._log.warning("process gone; disconnecting")
                        self._cb_state("DISCONNECTED")
                        return
                    hp_addr = self._locate_with_retries(pm, "RESCANNING")
                    if hp_addr is None:
                        self._cb_state("DISCONNECTED")
                        return
                    self._log.info("re-acquired at 0x%08X (compat=%s)", hp_addr, self._compat_mode)
                    self._cb_state("LOCATED")
                    char_name = self._lock_name(pm, hp_addr)
                    failure_count = 0
                    map_name = ""
                    stage_id = None
                    map_tick = 0

            freed = self._track_position(pm, hp_addr, time.monotonic() + POLL_INTERVAL)
            self._wake_event.clear()
            if freed and not self._stop_event.is_set():
                # A map change, not a glitch: re-locate now rather than after
                # FAILURE_THRESHOLD failed polls (~10 s of a dark minimap).
                self._log.debug("character struct freed; re-locating", extra={"cat": "locate"})
                hp_addr = self._locate_with_retries(pm, "RESCANNING", quick=True)
                if hp_addr is None:
                    self._cb_state("DISCONNECTED")
                    return
                self._log.debug(
                    "re-acquired at 0x%08X (compat=%s)",
                    hp_addr,
                    self._compat_mode,
                    extra={"cat": "locate"},
                )
                self._cb_state("LOCATED")
                char_name = self._lock_name(pm, hp_addr)
                failure_count = 0
                map_name = ""
                stage_id = None
                map_tick = 0

        self._cb_state("DISCONNECTED")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _connect_process(self):
        try:
            return pymem.Pymem(self._pid)
        except Exception as e:
            self._cb_error(
                f"Cannot connect to PID {self._pid}: {e}",
                cat="locate",
                code=ErrorCode.E_PROC_GONE,
                detail={"pid": self._pid, "exc": repr(e)},
            )
            return None

    def _locate_with_retries(self, pm, waiting_state: str, quick: bool = False):
        self._live = None  # the old lock is gone; walk_sample must not read it
        return self._locate_with_retries_inner(pm, waiting_state, quick)

    def _locate_with_retries_inner(self, pm, waiting_state: str, quick: bool = False):
        """Locate with bounded retries (~LOCATE_MAX_RETRIES x LOCATE_RETRY_INTERVAL).

        The character struct lives on the heap and is reallocated on events like
        map changes, so its address moves; a single locate attempt can land in
        the brief window where the old block is already freed (0xDDDDDDDD) and
        the new one is not yet valid. Retrying a bounded number of times lets a
        moved struct self-heal, without spinning forever for a genuinely
        logged-out character (recovery past the bound is via the UI 重偵 button).
        At the login / select / protection screens nothing is scanned and no
        retry is spent: the screen is watched until the game shows, so logging
        in later needs no 重偵 (a disconnect box still reads as in game).
        Emits `waiting_state` after the first miss. Returns the address or None.
        `quick` shortens the first few waits, for a relocate known to be due to
        a map change (the new struct appears once the map has loaded).
        """
        attempt = 0
        pre_game = False
        while attempt <= LOCATE_MAX_RETRIES:
            kind = screen_kind(pm)
            if kind in PRE_GAME:
                if not pre_game:
                    pre_game = True
                    self._log.info(
                        "at the %s screen; waiting for the game", kind, extra={"cat": "locate"}
                    )
                    self._cb_state(waiting_state)
                attempt = 0  # the retries are for after entering the game
                if self._stop_event.wait(SCREEN_POLL_INTERVAL):
                    return None
                continue
            if pre_game:
                pre_game = False
                self._log.info("left the pre-game screens (%s)", kind, extra={"cat": "locate"})
            addr = self._locate(pm, silent=True)
            if addr is not None:
                self._lock_is_obj = is_char_object(pm, addr)
                if not self._lock_is_obj:
                    self._log.info(
                        "locked outside a CCharObject at 0x%08X; provisional",
                        addr,
                        extra={"cat": "locate"},
                    )
                return addr
            if self._stop_event.is_set():
                return None
            if attempt == 0:
                self._cb_state(waiting_state)
            quick_wait = quick and attempt < QUICK_RETRIES
            self._stop_event.wait(QUICK_RETRY_INTERVAL if quick_wait else LOCATE_RETRY_INTERVAL)
            attempt += 1
        self._report_locate_exhausted(pm)
        return None

    def _report_locate_exhausted(self, pm) -> None:
        """Single owner of the exhaustion report.

        Each of the three callers used to report (or forget to report) this for
        itself; the initial-locate caller forgot, which is precisely the one a
        user hits first. Reporting where the exhaustion happens makes that
        omission unrepresentable.
        """
        self._cb_error(
            "Character not found -- press 重偵 or enter the HP value",
            cat="locate",
            code=ErrorCode.E_LOCATE_EXHAUSTED,
            detail=diagnostics.snapshot_locate_failure(
                pm, knowledge=self._knowledge, hp_value=self._hp_value
            ),
        )

    def _track_position(self, pm, hp_addr: int, deadline: float) -> bool:
        """Re-read the tile position every POS_INTERVAL until `deadline` or a wake-up.

        Returns True when the character struct was freed under us (a map
        change), so the caller re-locates now. Every other bad sample is just
        skipped; the full poll keeps owning lock-loss detection.
        """
        self._live = (pm, hp_addr)
        while True:
            if self._sample_position(pm, hp_addr):
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self._wake_event.wait(min(POS_INTERVAL, remaining)):
                return False

    def _sample_position(self, pm, hp_addr: int) -> bool:
        """Send the position if it changed. Returns True when the struct was freed."""
        sent = self._pos_sent
        stage = read_stage(pm)  # independent of hp_addr, so right even mid map change
        if not self._trusted_stage(stage):
            stage = None
        try:
            x, y = struct.unpack("<ii", pm.read_bytes(hp_addr + self._tile_offset, 8))
        except Exception:
            return False
        if stage is None:
            if sent is None:
                return False
            stage = (sent[0], sent[1])
        elif sent is not None and stage[0] != sent[0]:
            # New map: show it at once with no dot. The tiles still hold the old
            # map's position until the character takes a step.
            self._stale_tiles = (x, y)
            self._send_position(stage[0], stage[1], -1, -1)
        if self._score(pm, hp_addr) < 0.8:
            return x == FREED_FILL and y == FREED_FILL
        if (x, y) == self._stale_tiles:
            return False
        self._stale_tiles = None
        if (x, y) != (-1, -1) and not self._tile_in_bounds(stage[0], x, y):
            return False
        self._send_position(stage[0], stage[1], x, y)
        return False

    def _score(self, pm, hp_addr: int) -> float:
        """Structure score of the lock; 0 once a CCharObject lock stops being one.

        verify_structure alone keeps passing a block whose memory was reused,
        so a lock taken inside a CCharObject must stay inside one.
        """
        if self._lock_is_obj and not is_char_object(pm, hp_addr):
            return 0.0
        fields = self._knowledge["character_structure"]["fields"]
        verify = verify_structure_shifted if self._compat_mode else verify_structure
        return verify(pm, hp_addr, fields)

    def _lock_name(self, pm, hp_addr: int) -> str:
        """The character name, or "" while the lock is provisional.

        The look-alike block a provisional lock sits in holds garbage where the
        name goes (";w9w", "2", live 2026-10-06/07). Publishing it reported a
        switch to that "character" and saved settings under it; with "" the
        session keeps the previous name until the CCharObject lock is found
        (or PROVISIONAL_FAST_POLLS pass without one).
        """
        return read_character_name(pm, hp_addr) if self._lock_is_obj else ""

    def _find_char_object(self, pm, current: int) -> int | None:
        """A CCharObject lock for the character, if one now exists and differs from `current`."""
        addr = self._locate(pm, silent=True)
        if addr is None or addr == current or not is_char_object(pm, addr):
            return None
        self._lock_is_obj = True
        return addr

    def _trusted_stage(self, stage: tuple[int, str] | None) -> bool:
        """The id and name must name the same DB stage. Mid map change CStage can
        be read torn -- the old id with the new name was seen live -- and a name
        check alone passes that."""
        if stage is None:
            return False
        if self._stage_by_id is None:
            return True
        return self._stage_by_id.get(stage[0]) == stage[1]

    def _send_position(self, stage_id: int | None, map_name: str, x: int, y: int) -> None:
        pos = (stage_id, map_name, x, y)
        if pos != self._pos_sent:
            self._pos_sent = pos
            self._cb_position(stage_id, map_name, x, y)

    def _tile_in_bounds(self, stage_id: int | None, x: int, y: int) -> bool:
        if x < 0 or y < 0:
            return False
        if stage_id is None:
            return True
        if stage_id not in self._stage_bounds:
            try:
                base = minimap_base(stage_id)
            except Exception:
                base = None
            w, h = (base or {}).get("w_tiles"), (base or {}).get("h_tiles")
            self._stage_bounds[stage_id] = (w, h) if w and h else None
        bounds = self._stage_bounds[stage_id]
        return bounds is None or (x < bounds[0] and y < bounds[1])

    def _layout_at(self, pm, addr: int) -> bool:
        """Whether the struct at `addr` is in the compat (shifted) layout.

        locate_character(compat_mode=True) scans the normal layout first and
        returns a normal match when it has one, so the mode it was called with
        does not tell which layout matched. Taking it as the answer locked a
        normal struct as compat after a map change (the first, normal-mode call
        ran before the new struct existed), and the shifted verifier then
        failed it on every poll: a relocate loop every ~10 s.
        """
        fields = self._knowledge["character_structure"]["fields"]
        try:
            if verify_structure(pm, addr, fields) >= 0.8:
                return False
            return verify_structure_shifted(pm, addr, fields) >= 0.8
        except Exception:
            return self._compat_mode

    def _locate(self, pm, silent: bool = False):
        # Try both the normal and the 4-byte-shifted (compat) layout, preferring
        # whichever is currently selected. Some characters only match in compat
        # layout (observed when HP is buffed so current HP > base max HP), so we
        # auto-fall back instead of staying unlocated. Whichever layout matches
        # is remembered in self._compat_mode so the polling loop validates with
        # the matching verifier.
        modes = (self._compat_mode, not self._compat_mode)

        # Primary: read HP from stable pointer chain, then scan for flat struct
        try:
            hp_from_chain = read_hp_from_player_chain(pm)
            if hp_from_chain is not None:
                for compat in modes:
                    addr = locate_character(
                        pm,
                        hp_from_chain,
                        self._knowledge,
                        self._offset_filters,
                        compat_mode=compat,
                    )
                    if addr is not None:
                        self._compat_mode = self._layout_at(pm, addr)
                        return addr
        except Exception as exc:
            # Debug, not warning: this fires on every retry and is expected
            # before login. The exhaustion report carries the durable signal.
            self._log.debug(
                "player HP chain read failed: %s",
                exc,
                extra={"cat": "locate", "detail": {"exc": repr(exc)}},
            )

        # Fallback: manual HP value provided by user
        if self._hp_value is None:
            if not silent:
                self._cb_error(
                    "Cannot locate character -- try entering your current HP value manually",
                    cat="locate",
                    code=ErrorCode.E_CHAIN_READ,
                    detail=diagnostics.snapshot_locate_failure(pm, knowledge=self._knowledge),
                )
            return None
        try:
            for compat in modes:
                addr = locate_character(
                    pm,
                    self._hp_value,
                    self._knowledge,
                    self._offset_filters,
                    compat_mode=compat,
                )
                if addr is not None:
                    self._compat_mode = self._layout_at(pm, addr)
                    return addr
            return None
        except Exception as e:
            if not silent:
                self._cb_error(
                    f"Scan failed: {e}",
                    cat="locate",
                    code=ErrorCode.E_SCAN_FAILED,
                    detail={
                        "exc": repr(e),
                        "hp_value": self._hp_value,
                        "compat_tried": [False, True],
                    },
                )
            return None

    def _do_inventory_scan(self, pm, hp_addr):
        # Every exit path must call self._cb_inventory(...) so the session's
        # _inv_seq advances and the waiting API request returns promptly.
        # Otherwise a not-found / error path leaves the request blocked for the
        # full INVENTORY_SCAN_TIMEOUT before it 504s.
        try:
            items = read_inventory(pm, hp_addr)
            if items is None:
                self._cb_error(
                    "Inventory not found in memory",
                    cat="inventory",
                    code=ErrorCode.E_INV_NOT_FOUND,
                    detail={"hp_addr": hex(hp_addr)},
                )
                self._cb_inventory([])
                return
            self._cb_inventory(self._name_items(items))
        except Exception as e:
            self._cb_error(
                f"Inventory scan error: {e}",
                cat="inventory",
                code=ErrorCode.E_SCAN_FAILED,
                detail={"exc": repr(e), "hp_value": self._hp_value, "compat_tried": []},
            )
            self._cb_inventory([])

    def _do_warehouse_scan(self, pm):
        # Every exit path must call self._cb_warehouse(...) so the session's
        # _wh_seq advances and the waiting API request returns promptly.
        # Otherwise a not-found / error path leaves the request blocked for the
        # full WAREHOUSE_SCAN_TIMEOUT (60s) before it 504s.
        try:
            items = read_warehouse(pm)
            if items is None:
                self._cb_error(
                    "Warehouse not found -- open warehouse UI in game first",
                    cat="warehouse",
                    code=ErrorCode.E_WH_NOT_FOUND,
                    detail={},
                )
                self._cb_warehouse([])
                return
            self._cb_warehouse(self._name_items(items))
        except Exception as e:
            self._cb_error(
                f"Warehouse scan error: {e}",
                cat="warehouse",
                code=ErrorCode.E_SCAN_FAILED,
                detail={"exc": repr(e), "hp_value": self._hp_value, "compat_tried": []},
            )
            self._cb_warehouse([])

    def _auto_read_items(self, pm, hp_addr):
        """Refresh bag, pet bag, money, appearance, equipment, skills and warehouse on every poll;
        all are direct reads (~1 ms).

        The warehouse is read whenever its window is open in game, and the last
        read is kept after it closes. Failures here are routine (warehouse
        closed, a container being reallocated mid-read) so they only log at
        debug; the explicit scan requests still report errors.
        """
        try:
            items = read_inventory(pm, hp_addr)
            if items is not None:
                self._cb_inventory(self._name_items(items))
        except Exception as exc:
            self._log.debug("auto inventory read failed: %s", exc, extra={"cat": "inventory"})
        try:
            items = read_pet_inventory(pm, hp_addr)
            if items is not None:
                self._cb_pet_inventory(self._name_items(items))
            money = read_money(pm, hp_addr)
            if money is not None:
                self._cb_money(money)
        except Exception as exc:
            self._log.debug("auto pet bag read failed: %s", exc, extra={"cat": "inventory"})
        try:
            appearance = read_appearance(pm, hp_addr)
            if appearance is not None:
                self._cb_appearance(appearance)
        except Exception as exc:
            self._log.debug("appearance read failed: %s", exc, extra={"cat": "inventory"})
        try:
            gear = read_equipment(pm, hp_addr)
            if gear is not None:
                self._cb_equipment(
                    [
                        EquipSlot(
                            slot=slot,
                            item_id=iid,
                            name=self._item_db.get(iid) if iid else None,
                            plus=plus,
                            stats=to_stats(stats),
                            enhance=enhance_bonus(iid, plus) if iid else [],
                            enhance_extra=enhance_extra(iid, plus) if iid else [],
                            inlays=inlays(sockets),
                        )
                        for slot, iid, plus, stats, sockets in gear
                    ]
                )
        except Exception as exc:
            self._log.debug("equipment read failed: %s", exc, extra={"cat": "inventory"})
        try:
            skills = read_skills(pm, hp_addr)
            if skills is not None:
                self._cb_skills(skills)
        except Exception as exc:
            self._log.debug("skill read failed: %s", exc, extra={"cat": "inventory"})
        try:
            data = locate_warehouse(pm)
            self._cb_warehouse_open(data is not None)
            if data is not None:
                items = read_item_container(pm, data + WAREHOUSE_COUNT_OFFSET)
                self._cb_warehouse(self._name_items(items))
        except Exception as exc:
            self._log.debug("auto warehouse read failed: %s", exc, extra={"cat": "warehouse"})

    def _name_items(self, items):
        return [(item_id, qty, self._item_db.get(item_id, "???")) for item_id, qty in items]
