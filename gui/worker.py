"""
Background worker thread: manages state machine and memory polling.

States:
    DISCONNECTED  - process not found
    CONNECTING    - process found, scanning for character struct
    LOCATED       - polling every 1s from known address
    READ_ERROR    - validation failed 3x, triggers rescan
    RESCANNING    - re-running locate_character
"""

import threading
import pymem
from PySide6.QtCore import QThread, Signal

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reader import (
    locate_character,
    read_all_fields,
    read_character_name,
    get_display_fields,
    load_knowledge,
    verify_structure,
    verify_structure_shifted,
    locate_inventory,
    find_inventory_start,
    read_inventory,
    load_item_db,
)
from warehouse_scan import (
    locate_all_slot_arrays,
    walk_back_to_start,
    read_slot_array,
    SLOT_SIZE,
)

POLL_INTERVAL = 1.0  # seconds between stat reads
FAILURE_THRESHOLD = 3  # consecutive failures before rescan


class ReaderWorker(QThread):
    state_changed = Signal(str)  # new state string
    stats_updated = Signal(list)  # list of (name, value) tuples
    inventory_ready = Signal(list)  # list of (item_id, qty, name)
    warehouse_ready = Signal(list)  # list of (item_id, qty, name)
    scan_error = Signal(str)  # human-readable error message

    def __init__(self, pid: int, parent=None):
        super().__init__(parent)
        self._pid = pid
        self._hp_value = None
        self._offset_filters = None
        self._compat_mode = False
        self._stop_event = threading.Event()
        self._scan_inventory = False
        self._scan_warehouse = False
        self._knowledge = load_knowledge()
        self._display_fields = get_display_fields(self._knowledge)
        self._item_db = load_item_db()

    # ------------------------------------------------------------------
    # Public API (called from main thread)
    # ------------------------------------------------------------------
    def connect(self, hp_value: int, offset_filters=None, compat_mode: bool = False):
        """Start the worker with a known HP value and optional offset filters."""
        self._hp_value = hp_value
        self._offset_filters = offset_filters
        self._compat_mode = compat_mode
        self._stop_event.clear()
        if not self.isRunning():
            self.start()

    def request_inventory_scan(self):
        self._scan_inventory = True

    def request_warehouse_scan(self):
        self._scan_warehouse = True

    def stop(self):
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Thread entry point
    # ------------------------------------------------------------------
    def run(self):
        if self._hp_value is None:
            self.scan_error.emit("HP value not set, call connect(hp_value) first")
            self.state_changed.emit("DISCONNECTED")
            return
        self.state_changed.emit("CONNECTING")

        pm = self._connect_process()
        if pm is None:
            self.state_changed.emit("DISCONNECTED")
            return

        hp_addr = self._locate(pm)
        if hp_addr is None:
            self.state_changed.emit("DISCONNECTED")
            return

        self.state_changed.emit("LOCATED")
        char_name = read_character_name(pm, hp_addr)
        failure_count = 0

        while not self._stop_event.is_set():
            # --- Handle inventory scan request ---
            if self._scan_inventory:
                self._scan_inventory = False
                self._do_inventory_scan(pm)

            # --- Handle warehouse scan request ---
            if self._scan_warehouse:
                self._scan_warehouse = False
                self._do_warehouse_scan(pm)

            # --- Poll character stats ---
            try:
                fields = read_all_fields(pm, hp_addr, self._display_fields)
                struct_fields = self._knowledge["character_structure"]["fields"]
                if self._compat_mode:
                    score = verify_structure_shifted(pm, hp_addr, struct_fields)
                else:
                    score = verify_structure(pm, hp_addr, struct_fields)

                if score < 0.8:
                    failure_count += 1
                    if failure_count >= FAILURE_THRESHOLD:
                        self.state_changed.emit("READ_ERROR")
                        self.state_changed.emit("RESCANNING")
                        hp_addr = self._locate(pm)
                        if hp_addr is None:
                            self.scan_error.emit(
                                "Character not found — please enter the new character's HP value"
                            )
                            self.state_changed.emit("DISCONNECTED")
                            return
                        self.state_changed.emit("LOCATED")
                        char_name = read_character_name(pm, hp_addr)
                        failure_count = 0
                else:
                    failure_count = 0
                    self.stats_updated.emit([("角色名稱", char_name)] + fields)

            except Exception:
                failure_count += 1
                if failure_count >= FAILURE_THRESHOLD:
                    # Try re-connecting process first
                    self.state_changed.emit("READ_ERROR")
                    pm = self._connect_process()
                    if pm is None:
                        self.state_changed.emit("DISCONNECTED")
                        return
                    self.state_changed.emit("RESCANNING")
                    hp_addr = self._locate(pm)
                    if hp_addr is None:
                        self.scan_error.emit(
                            "Character not found — please enter the new character's HP value"
                        )
                        self.state_changed.emit("DISCONNECTED")
                        return
                    self.state_changed.emit("LOCATED")
                    char_name = read_character_name(pm, hp_addr)
                    failure_count = 0

            self._stop_event.wait(POLL_INTERVAL)

        # Emit DISCONNECTED when loop exits cleanly (stop() was called)
        self.state_changed.emit("DISCONNECTED")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _connect_process(self):
        try:
            return pymem.Pymem(self._pid)
        except Exception as e:
            self.scan_error.emit(f"Cannot connect to PID {self._pid}: {e}")
            return None

    def _locate(self, pm):
        try:
            return locate_character(
                pm,
                self._hp_value,
                self._knowledge,
                self._offset_filters,
                compat_mode=self._compat_mode,
            )
        except Exception as e:
            self.scan_error.emit(f"Scan failed: {e}")
            return None

    def _do_inventory_scan(self, pm):
        try:
            inv_match = locate_inventory(pm)
            if inv_match is None:
                self.scan_error.emit("Inventory not found in memory")
                return
            inv_start = find_inventory_start(pm, inv_match)
            items = read_inventory(pm, inv_start)
            named = [(item_id, qty, self._item_db.get(item_id, "???")) for item_id, qty in items]
            self.inventory_ready.emit(named)
        except Exception as e:
            self.scan_error.emit(f"Inventory scan error: {e}")

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
                self.scan_error.emit("Warehouse not found — open warehouse UI in game first")
                return

            # Use the largest array (most items = warehouse)
            best = max(warehouse_arrays, key=lambda a: len(read_slot_array(pm, a)))
            raw = read_slot_array(pm, best)
            named = [(item_id, qty, self._item_db.get(item_id, "???")) for item_id, qty, _ in raw]
            self.warehouse_ready.emit(named)
        except Exception as e:
            self.scan_error.emit(f"Warehouse scan error: {e}")
