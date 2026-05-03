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
from collections.abc import Callable

import pymem

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reader import (
    find_inventory_start,
    get_display_fields,
    load_item_db,
    load_knowledge,
    locate_character,
    locate_inventory,
    locate_map_name,
    read_all_fields,
    read_character_name,
    read_hp_from_player_chain,
    read_inventory,
    verify_structure,
    verify_structure_shifted,
)
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


class ReaderWorker(threading.Thread):
    """Per-PID worker thread. Calls callbacks instead of emitting Qt signals."""

    def __init__(
        self,
        pid: int,
        on_state: Callable[[str], None],
        on_stats: Callable[[list[tuple[str, int]]], None],
        on_inventory: Callable[[list[tuple[int, int, str]]], None],
        on_warehouse: Callable[[list[tuple[int, int, str]]], None],
        on_error: Callable[[str], None],
    ) -> None:
        super().__init__(daemon=True)
        self._pid = pid
        self._cb_state = on_state
        self._cb_stats = on_stats
        self._cb_inventory = on_inventory
        self._cb_warehouse = on_warehouse
        self._cb_error = on_error
        self._hp_value: int | None = None
        self._offset_filters = None
        self._compat_mode = False
        self._stop_event = threading.Event()
        self._scan_inventory = False
        self._scan_warehouse = False
        self._knowledge = load_knowledge()
        self._display_fields = get_display_fields(self._knowledge)
        self._item_db = load_item_db()
        try:
            self._stage_names = all_stage_names()
        except Exception:
            self._stage_names = None  # fall back to heuristic if DB unavailable

    # ------------------------------------------------------------------
    # Public API (called from main thread)
    # ------------------------------------------------------------------
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

    def request_warehouse(self) -> None:
        self._scan_warehouse = True

    def stop(self) -> None:
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Thread entry point
    # ------------------------------------------------------------------
    def run(self) -> None:  # pragma: no cover -- runtime only
        self._cb_state("CONNECTING")

        pm = self._connect_process()
        if pm is None:
            self._cb_state("DISCONNECTED")
            return

        hp_addr = None
        for attempt in range(LOCATE_MAX_RETRIES + 1):
            hp_addr = self._locate(pm, silent=True)
            if hp_addr is not None:
                break
            if self._stop_event.is_set():
                self._cb_state("DISCONNECTED")
                return
            if attempt == 0:
                self._cb_state("WAITING")
            self._stop_event.wait(LOCATE_RETRY_INTERVAL)

        if hp_addr is None:
            self._cb_state("DISCONNECTED")
            return

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
                fields = read_all_fields(pm, hp_addr, self._display_fields)
                if self._compat_mode:
                    score = verify_structure_shifted(pm, hp_addr, struct_fields)
                else:
                    score = verify_structure(pm, hp_addr, struct_fields)

                if score < 0.8:
                    failure_count += 1
                    if failure_count >= FAILURE_THRESHOLD:
                        self._cb_state("READ_ERROR")
                        self._cb_state("RESCANNING")
                        hp_addr = self._locate(pm)
                        if hp_addr is None:
                            self._cb_error(
                                "Character not found -- please enter the new character's HP value"
                            )
                            self._cb_state("DISCONNECTED")
                            return
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
                    self._cb_stats([("角色名稱", char_name), ("地圖名稱", map_name)] + fields)

            except Exception:
                failure_count += 1
                if failure_count >= FAILURE_THRESHOLD:
                    self._cb_state("READ_ERROR")
                    pm = self._connect_process()
                    if pm is None:
                        self._cb_state("DISCONNECTED")
                        return
                    self._cb_state("RESCANNING")
                    hp_addr = self._locate(pm)
                    if hp_addr is None:
                        self._cb_error(
                            "Character not found -- please enter the new character's HP value"
                        )
                        self._cb_state("DISCONNECTED")
                        return
                    self._cb_state("LOCATED")
                    char_name = read_character_name(pm, hp_addr)
                    failure_count = 0
                    map_name = ""
                    map_tick = 0

            self._stop_event.wait(POLL_INTERVAL)

        self._cb_state("DISCONNECTED")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _connect_process(self):
        try:
            return pymem.Pymem(self._pid)
        except Exception as e:
            self._cb_error(f"Cannot connect to PID {self._pid}: {e}")
            return None

    def _locate(self, pm, silent: bool = False):
        # Primary: read HP from stable pointer chain, then scan for flat struct
        try:
            hp_from_chain = read_hp_from_player_chain(pm)
            if hp_from_chain is not None:
                addr = locate_character(
                    pm,
                    hp_from_chain,
                    self._knowledge,
                    self._offset_filters,
                    compat_mode=self._compat_mode,
                )
                if addr is not None:
                    return addr
        except Exception:
            pass

        # Fallback: manual HP value provided by user
        if self._hp_value is None:
            if not silent:
                self._cb_error(
                    "Cannot locate character -- try entering your current HP value manually"
                )
            return None
        try:
            return locate_character(
                pm,
                self._hp_value,
                self._knowledge,
                self._offset_filters,
                compat_mode=self._compat_mode,
            )
        except Exception as e:
            if not silent:
                self._cb_error(f"Scan failed: {e}")
            return None

    def _do_inventory_scan(self, pm):
        try:
            inv_match = locate_inventory(pm)
            if inv_match is None:
                self._cb_error("Inventory not found in memory")
                return
            inv_start = find_inventory_start(pm, inv_match)
            items = read_inventory(pm, inv_start)
            named = [(item_id, qty, self._item_db.get(item_id, "???")) for item_id, qty in items]
            self._cb_inventory(named)
        except Exception as e:
            self._cb_error(f"Inventory scan error: {e}")

    def _do_warehouse_scan(self, pm):
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
                self._cb_error("Warehouse not found -- open warehouse UI in game first")
                return

            # Use the largest array (most items = warehouse)
            best = max(warehouse_arrays, key=lambda a: len(read_slot_array(pm, a)))
            raw = read_slot_array(pm, best)
            named = [(item_id, qty, self._item_db.get(item_id, "???")) for item_id, qty, _ in raw]
            self._cb_warehouse(named)
        except Exception as e:
            self._cb_error(f"Warehouse scan error: {e}")
