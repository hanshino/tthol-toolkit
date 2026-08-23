"""Background worker thread for one PID. No Qt -- uses callbacks.

States:
    DISCONNECTED  - process not found
    CONNECTING    - process found, scanning for character struct
    WAITING       - process found but character not yet located (waiting for login)
    LOCATED       - polling every 1s from known address
    READ_ERROR    - validation failed 3x, triggers rescan
    RESCANNING    - re-running locate_character
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable

import pymem

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reader import (
    find_inventory_start,
    get_display_fields,
    load_item_db,
    load_knowledge,
    load_status_db,
    locate_character,
    locate_inventory,
    locate_map_name,
    read_active_statuses,
    read_all_fields,
    read_character_name,
    read_hp_from_player_chain,
    read_hp_pair_from_chain,
    read_inventory,
    verify_structure,
    verify_structure_shifted,
)
from services import diagnostics
from services.diag_events import ErrorCode
from services.map_db import all_stage_names
from warehouse_scan import (
    SLOT_SIZE,
    locate_all_slot_arrays,
    read_slot_array,
    walk_back_to_start,
)

POLL_INTERVAL = 3.0
FAILURE_THRESHOLD = 3
LOCATE_RETRY_INTERVAL = 3.0
LOCATE_MAX_RETRIES = 10
MAP_RESCAN_EVERY = 5  # locate_map_name walks the heap; cache between polls


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
    ) -> None:
        super().__init__(daemon=True)
        self._pid = pid
        self._cb_state = on_state
        self._cb_stats = on_stats
        self._cb_inventory = on_inventory
        self._cb_warehouse = on_warehouse
        self._cb_error = on_error
        self._cb_buffs = on_buffs or (lambda _b: None)
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
        self._item_db = load_item_db()
        self._status_db = load_status_db()
        try:
            self._stage_names = all_stage_names()
        except Exception:
            self._stage_names = None  # fall back to heuristic if DB unavailable

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
        char_name = read_character_name(pm, hp_addr)
        failure_count = 0
        struct_fields = self._knowledge["character_structure"]["fields"]
        map_name = ""
        map_tick = 0

        while not self._stop_event.is_set():
            if self._scan_inventory:
                self._scan_inventory = False
                self._do_inventory_scan(pm)

            if self._scan_warehouse:
                self._scan_warehouse = False
                self._do_warehouse_scan(pm)

            try:
                fields = read_all_fields(pm, hp_addr, self._display_fields, self._compat_mode)
                if self._compat_mode:
                    score = verify_structure_shifted(pm, hp_addr, struct_fields)
                else:
                    score = verify_structure(pm, hp_addr, struct_fields)

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
                        char_name = read_character_name(pm, hp_addr)
                        failure_count = 0
                        map_name = ""
                        map_tick = 0
                else:
                    failure_count = 0
                    if map_tick % MAP_RESCAN_EVERY == 0 or not map_name:
                        map_name = locate_map_name(pm, valid_names=self._stage_names)
                    map_tick += 1
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
                    self._cb_stats([("角色名稱", char_name), ("地圖名稱", map_name)] + fields)
                    statuses = read_active_statuses(pm, hp_addr, self._knowledge)
                    self._cb_buffs(
                        [(g, self._status_db.get(g, f"group {g}"), kind) for g, kind in statuses]
                    )

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
                    char_name = read_character_name(pm, hp_addr)
                    failure_count = 0
                    map_name = ""
                    map_tick = 0

            self._wake_event.wait(POLL_INTERVAL)
            self._wake_event.clear()

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

    def _locate_with_retries(self, pm, waiting_state: str):
        """Locate with bounded retries (~LOCATE_MAX_RETRIES x LOCATE_RETRY_INTERVAL).

        The character struct lives on the heap and is reallocated on events like
        map changes, so its address moves; a single locate attempt can land in
        the brief window where the old block is already freed (0xCDCDCDCD) and
        the new one is not yet valid. Retrying a bounded number of times lets a
        moved struct self-heal, without spinning forever for a genuinely
        logged-out character (recovery past the bound is via the UI 重偵 button).
        Emits `waiting_state` after the first miss. Returns the address or None.
        """
        for attempt in range(LOCATE_MAX_RETRIES + 1):
            addr = self._locate(pm, silent=True)
            if addr is not None:
                return addr
            if self._stop_event.is_set():
                return None
            if attempt == 0:
                self._cb_state(waiting_state)
            self._stop_event.wait(LOCATE_RETRY_INTERVAL)
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
                        self._compat_mode = compat
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
                    self._compat_mode = compat
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

    def _do_inventory_scan(self, pm):
        # Every exit path must call self._cb_inventory(...) so the session's
        # _inv_seq advances and the waiting API request returns promptly.
        # Otherwise a not-found / error path leaves the request blocked for the
        # full INVENTORY_SCAN_TIMEOUT before it 504s.
        try:
            inv_match = locate_inventory(pm)
            if inv_match is None:
                self._cb_error(
                    "Inventory not found in memory",
                    cat="inventory",
                    code=ErrorCode.E_INV_NOT_FOUND,
                    detail={"hp_addr": None, "scan_ms": None},
                )
                self._cb_inventory([])
                return
            inv_start = find_inventory_start(pm, inv_match)
            items = read_inventory(pm, inv_start)
            named = [(item_id, qty, self._item_db.get(item_id, "???")) for item_id, qty in items]
            self._cb_inventory(named)
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
            # Find inventory range for exclusion
            inv_match = locate_inventory(pm)
            if inv_match:
                inv_start = find_inventory_start(pm, inv_match)
                inv_end = inv_start + SLOT_SIZE * 60
            else:
                inv_start = inv_end = 0

            all_arrays = locate_all_slot_arrays(pm)
            warehouse_arrays = []
            for addr in all_arrays:
                arr_start = walk_back_to_start(pm, addr)
                if inv_start and inv_start <= arr_start < inv_end:
                    continue
                if inv_start and inv_start <= addr < inv_end:
                    continue
                warehouse_arrays.append(arr_start)
            warehouse_arrays = sorted(set(warehouse_arrays))

            if not warehouse_arrays:
                self._cb_error(
                    "Warehouse not found -- open warehouse UI in game first",
                    cat="warehouse",
                    code=ErrorCode.E_WH_NOT_FOUND,
                    detail={
                        "hp_addr": None,
                        "inv_range": [inv_start, inv_end],
                        "arrays_seen": len(all_arrays),
                    },
                )
                self._cb_warehouse([])
                return

            # Use the largest array (most items = warehouse)
            best = max(warehouse_arrays, key=lambda a: len(read_slot_array(pm, a)))
            raw = read_slot_array(pm, best)
            named = [(item_id, qty, self._item_db.get(item_id, "???")) for item_id, qty, _ in raw]
            self._cb_warehouse(named)
        except Exception as e:
            self._cb_error(
                f"Warehouse scan error: {e}",
                cat="warehouse",
                code=ErrorCode.E_SCAN_FAILED,
                detail={"exc": repr(e), "hp_value": self._hp_value, "compat_tried": []},
            )
            self._cb_warehouse([])
