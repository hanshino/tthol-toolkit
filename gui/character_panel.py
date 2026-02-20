"""
Per-character panel: op_bar + vitals strip + inner tabs.
One instance per detected game window.
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTabWidget, QFrame,
)
from PySide6.QtCore import Qt, Slot, Signal

from gui.worker import ReaderWorker
from gui.status_tab import StatusTab
from gui.inventory_tab import InventoryTab
from gui.warehouse_tab import WarehouseTab
from gui.snapshot_db import SnapshotDB
from gui.inventory_manager_tab import InventoryManagerTab
from gui.theme import badge_style, vital_html, fraction_html, GREEN, BLUE, AMBER


def _vsep() -> QFrame:
    f = QFrame()
    f.setObjectName("vitals_sep")
    f.setFrameShape(QFrame.Shape.VLine)
    f.setFixedWidth(1)
    f.setFixedHeight(20)
    return f


class CharacterPanel(QWidget):
    # Emitted when the character name becomes known — used to rename the outer tab.
    tab_label_changed = Signal(str)
    # Forwarded to MainWindow's status bar.
    status_message = Signal(str, int)   # message, timeout_ms

    def __init__(self, pid: int, hwnd: int, snapshot_db: SnapshotDB, parent=None):
        super().__init__(parent)
        self._pid = pid
        self._hwnd = hwnd          # reserved for Phase 2 OCR
        self._snapshot_db = snapshot_db
        self._pending_hp: int | None = None
        self._current_character: str = ""
        self._last_inventory: list[dict] = []
        self._last_warehouse: list[dict] = []

        self._worker = ReaderWorker(pid=pid, parent=self)
        self._worker.state_changed.connect(self._on_state_changed)
        self._worker.stats_updated.connect(self._on_stats_updated)
        self._worker.inventory_ready.connect(self._on_inventory_ready)
        self._worker.warehouse_ready.connect(self._on_warehouse_ready)
        self._worker.scan_error.connect(self._on_scan_error)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 6)
        root.setSpacing(6)

        # ── op_bar ────────────────────────────────────────────────────
        op_frame = QFrame()
        op_frame.setObjectName("op_bar")
        op_layout = QHBoxLayout(op_frame)
        op_layout.setContentsMargins(10, 6, 10, 6)
        op_layout.setSpacing(8)

        hp_lbl = QLabel("HP")
        hp_lbl.setStyleSheet(f"color: {GREEN}; font-weight: 600; font-size: 11px;")
        op_layout.addWidget(hp_lbl)

        self._hp_input = QLineEdit()
        self._hp_input.setPlaceholderText("current HP value")
        self._hp_input.setMaximumWidth(130)
        op_layout.addWidget(self._hp_input)

        self._connect_btn = QPushButton("Connect")
        self._connect_btn.setObjectName("primary_btn")
        self._connect_btn.clicked.connect(self._on_connect)
        op_layout.addWidget(self._connect_btn)

        self._state_indicator = QLabel("● DISCONNECTED")
        self._state_indicator.setStyleSheet(badge_style("DISCONNECTED"))
        op_layout.addWidget(self._state_indicator)

        op_layout.addStretch()

        self._relocate_btn = QPushButton("Relocate")
        self._relocate_btn.setEnabled(False)
        self._relocate_btn.clicked.connect(self._on_relocate)
        op_layout.addWidget(self._relocate_btn)

        root.addWidget(op_frame)

        # ── vitals strip ──────────────────────────────────────────────
        vitals_frame = QFrame()
        vitals_frame.setObjectName("vitals_strip")
        vitals_layout = QHBoxLayout(vitals_frame)
        vitals_layout.setContentsMargins(14, 6, 14, 6)
        vitals_layout.setSpacing(12)

        self._vitals_labels: dict[str, QLabel] = {}
        vitals_defs = ["Lv", "HP", "MP", "Weight", "Pos"]
        for i, key in enumerate(vitals_defs):
            lbl = QLabel(vital_html(key.upper(), "---"))
            lbl.setTextFormat(Qt.TextFormat.RichText)
            vitals_layout.addWidget(lbl)
            self._vitals_labels[key] = lbl
            if i < len(vitals_defs) - 1:
                vitals_layout.addWidget(_vsep())

        vitals_layout.addStretch()
        root.addWidget(vitals_frame)

        # ── inner tabs ────────────────────────────────────────────────
        self._tabs = QTabWidget()
        self._status_tab = StatusTab()
        self._inventory_tab = InventoryTab()
        self._warehouse_tab = WarehouseTab()
        self._manager_tab = InventoryManagerTab(self._snapshot_db)

        self._tabs.addTab(self._status_tab, "Status")
        self._tabs.addTab(self._inventory_tab, "Inventory")
        self._tabs.addTab(self._warehouse_tab, "Warehouse")
        self._tabs.addTab(self._manager_tab, "道具總覽")
        root.addWidget(self._tabs)

        self._inventory_tab.scan_requested.connect(self._on_inventory_scan)
        self._warehouse_tab.scan_requested.connect(self._on_warehouse_scan)
        self._inventory_tab.save_requested.connect(self._on_inventory_save)
        self._warehouse_tab.save_requested.connect(self._on_warehouse_save)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------
    @Slot()
    def _on_connect(self):
        hp_text = self._hp_input.text().strip()
        if not hp_text.isdigit():
            self.status_message.emit("Enter a valid HP value first", 3000)
            return
        self._connect_btn.setEnabled(False)
        self._worker.connect(int(hp_text))

    @Slot()
    def _on_relocate(self):
        hp_text = self._hp_input.text().strip()
        if not hp_text.isdigit():
            self.status_message.emit("Enter a valid HP value first", 3000)
            return
        self._pending_hp = int(hp_text)
        self._relocate_btn.setEnabled(False)
        self._worker.stop()

    @Slot(str)
    def _on_state_changed(self, state: str):
        self._state_indicator.setText(f"● {state}")
        self._state_indicator.setStyleSheet(badge_style(state))
        self._relocate_btn.setEnabled(state == "LOCATED")
        if state == "DISCONNECTED":
            if self._pending_hp is not None:
                hp = self._pending_hp
                self._pending_hp = None
                self._worker.connect(hp)
            else:
                self._connect_btn.setEnabled(True)

    @Slot(list)
    def _on_stats_updated(self, fields: list):
        data = dict(fields)
        # Only update character name on first occurrence (name is stable for a session).
        # Uses empty-string fallback so a transient missing field does not clear the stored name.
        name = data.get("角色名稱", "")
        if name and name != self._current_character:
            self._current_character = name
            self.tab_label_changed.emit(name)

        hp     = data.get("血量", "---")
        hp_max = data.get("最大血量", "---")
        mp     = data.get("真氣", "---")
        mp_max = data.get("最大真氣", "---")
        wt     = data.get("負重", "---")
        wt_max = data.get("最大負重", "---")
        lv     = data.get("等級", "---")
        x      = data.get("X座標", "---")
        y      = data.get("Y座標", "---")

        self._vitals_labels["Lv"].setText(vital_html("LV", lv))
        self._vitals_labels["HP"].setText(fraction_html("HP", hp, hp_max, GREEN))
        self._vitals_labels["MP"].setText(fraction_html("MP", mp, mp_max, BLUE))
        self._vitals_labels["Weight"].setText(fraction_html("WT", wt, wt_max, AMBER))
        self._vitals_labels["Pos"].setText(vital_html("POS", f"({x}, {y})"))

        self._status_tab.update_stats(fields)

    @Slot(list)
    def _on_inventory_ready(self, items: list):
        self._inventory_tab.populate(items)
        self._last_inventory = [{"item_id": iid, "qty": qty} for iid, qty, _ in items]

    @Slot(list)
    def _on_warehouse_ready(self, items: list):
        self._warehouse_tab.populate(items)
        self._last_warehouse = [{"item_id": iid, "qty": qty} for iid, qty, _ in items]

    @Slot(str)
    def _on_scan_error(self, msg: str):
        self.status_message.emit(f"[Error] {msg}", 5000)
        self._inventory_tab.set_scanning(False)
        self._warehouse_tab.set_scanning(False)

    @Slot()
    def _on_inventory_scan(self):
        self._inventory_tab.set_scanning(True)
        self._worker.request_inventory_scan()

    @Slot()
    def _on_warehouse_scan(self):
        self._warehouse_tab.set_scanning(True)
        self._worker.request_warehouse_scan()

    @Slot()
    def _on_inventory_save(self):
        if not self._current_character or not self._last_inventory:
            self.status_message.emit("No inventory data to save", 3000)
            return
        saved = self._snapshot_db.save_snapshot(
            self._current_character, "inventory", self._last_inventory
        )
        msg = "Snapshot saved" if saved else "No change detected, skipped"
        self.status_message.emit(msg, 3000)
        self._manager_tab.refresh()

    @Slot()
    def _on_warehouse_save(self):
        if not self._current_character or not self._last_warehouse:
            self.status_message.emit("No warehouse data to save", 3000)
            return
        saved = self._snapshot_db.save_snapshot(
            self._current_character, "warehouse", self._last_warehouse
        )
        msg = "Snapshot saved" if saved else "No change detected, skipped"
        self.status_message.emit(msg, 3000)
        self._manager_tab.refresh()

    def shutdown(self):
        """Stop the worker thread. Call before removing this panel."""
        self._worker.stop()
        if not self._worker.wait(5000):   # 5-second timeout
            self._worker.terminate()      # last resort if worker is stuck
