"""
Main application window.

Layout:
  Row 1: HP input | Connect btn | Status indicator | Relocate btn
  Row 2: Vitals strip (Lv / HP / MP / Weight / Pos) — always visible
  Row 3: TabWidget (Status | Inventory | Warehouse)
"""
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTabWidget, QStatusBar,
)
from PySide6.QtCore import Qt, Slot

from gui.worker import ReaderWorker
from gui.status_tab import StatusTab
from gui.inventory_tab import InventoryTab
from gui.warehouse_tab import WarehouseTab

STATE_COLORS = {
    "DISCONNECTED": "gray",
    "CONNECTING":   "orange",
    "LOCATED":      "green",
    "READ_ERROR":   "red",
    "RESCANNING":   "orange",
}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tthol Reader")
        self.setMinimumWidth(560)

        self._worker = ReaderWorker(self)
        self._worker.state_changed.connect(self._on_state_changed)
        self._worker.stats_updated.connect(self._on_stats_updated)
        self._worker.inventory_ready.connect(self._on_inventory_ready)
        self._worker.warehouse_ready.connect(self._on_warehouse_ready)
        self._worker.scan_error.connect(self._on_scan_error)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 4)
        root.setSpacing(4)

        # --- Row 1: operation bar ---
        op_row = QHBoxLayout()
        op_row.addWidget(QLabel("HP:"))
        self._hp_input = QLineEdit()
        self._hp_input.setPlaceholderText("current HP value")
        self._hp_input.setMaximumWidth(120)
        op_row.addWidget(self._hp_input)

        self._connect_btn = QPushButton("Connect")
        self._connect_btn.clicked.connect(self._on_connect)
        op_row.addWidget(self._connect_btn)

        self._state_indicator = QLabel("● DISCONNECTED")
        self._state_indicator.setStyleSheet("color: gray; font-weight: bold;")
        op_row.addWidget(self._state_indicator)

        self._relocate_btn = QPushButton("Relocate")
        self._relocate_btn.setEnabled(False)
        self._relocate_btn.clicked.connect(self._on_relocate)
        op_row.addWidget(self._relocate_btn)
        op_row.addStretch()
        root.addLayout(op_row)

        # --- Row 2: vitals strip ---
        vitals_row = QHBoxLayout()
        self._vitals_labels = {}
        for key in ["Lv", "HP", "MP", "Weight", "Pos"]:
            lbl = QLabel(f"{key}: ---")
            vitals_row.addWidget(lbl)
            if key != "Pos":
                vitals_row.addWidget(QLabel("|"))
            self._vitals_labels[key] = lbl
        vitals_row.addStretch()
        root.addLayout(vitals_row)

        # --- Row 3: tabs ---
        tabs = QTabWidget()
        self._status_tab = StatusTab()
        self._inventory_tab = InventoryTab()
        self._warehouse_tab = WarehouseTab()

        tabs.addTab(self._status_tab, "Status")
        tabs.addTab(self._inventory_tab, "Inventory")
        tabs.addTab(self._warehouse_tab, "Warehouse")
        root.addWidget(tabs)

        # Wire tab scan buttons
        self._inventory_tab.scan_requested.connect(self._on_inventory_scan)
        self._warehouse_tab.scan_requested.connect(self._on_warehouse_scan)

        self.setStatusBar(QStatusBar())

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------
    @Slot()
    def _on_connect(self):
        hp_text = self._hp_input.text().strip()
        if not hp_text.isdigit():
            self.statusBar().showMessage("Enter a valid HP value first", 3000)
            return
        self._connect_btn.setEnabled(False)
        self._worker.connect(int(hp_text))

    @Slot()
    def _on_relocate(self):
        hp_text = self._hp_input.text().strip()
        if not hp_text.isdigit():
            self.statusBar().showMessage("Enter a valid HP value first", 3000)
            return
        self._worker.stop()
        self._worker.wait()
        self._worker.connect(int(hp_text))

    @Slot(str)
    def _on_state_changed(self, state: str):
        color = STATE_COLORS.get(state, "gray")
        self._state_indicator.setText(f"● {state}")
        self._state_indicator.setStyleSheet(f"color: {color}; font-weight: bold;")
        self._relocate_btn.setEnabled(state == "LOCATED")
        if state == "DISCONNECTED":
            self._connect_btn.setEnabled(True)

    @Slot(list)
    def _on_stats_updated(self, fields: list):
        data = {name: value for name, value in fields}

        hp = data.get("血量", "---")
        hp_max = data.get("最大血量", "---")
        mp = data.get("真氣", "---")
        mp_max = data.get("最大真氣", "---")
        wt = data.get("負重", "---")
        wt_max = data.get("最大負重", "---")
        lv = data.get("等級", "---")
        x = data.get("X座標", "---")
        y = data.get("Y座標", "---")

        self._vitals_labels["Lv"].setText(f"Lv: {lv}")
        self._vitals_labels["HP"].setText(f"HP: {hp}/{hp_max}")
        self._vitals_labels["MP"].setText(f"MP: {mp}/{mp_max}")
        self._vitals_labels["Weight"].setText(f"Weight: {wt}/{wt_max}")
        self._vitals_labels["Pos"].setText(f"Pos: ({x}, {y})")

        self._status_tab.update_stats(fields)

    @Slot(list)
    def _on_inventory_ready(self, items: list):
        self._inventory_tab.populate(items)

    @Slot(list)
    def _on_warehouse_ready(self, items: list):
        self._warehouse_tab.populate(items)

    @Slot(str)
    def _on_scan_error(self, msg: str):
        self.statusBar().showMessage(f"[Error] {msg}", 5000)

    @Slot()
    def _on_inventory_scan(self):
        self._inventory_tab.set_scanning(True)
        self._worker.request_inventory_scan()

    @Slot()
    def _on_warehouse_scan(self):
        self._warehouse_tab.set_scanning(True)
        self._worker.request_warehouse_scan()

    def closeEvent(self, event):
        self._worker.stop()
        self._worker.wait()
        event.accept()
