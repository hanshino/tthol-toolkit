"""Warehouse tab: identical layout to inventory, with open-UI reminder."""
from datetime import datetime
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
)
from PySide6.QtCore import Qt, Signal


class WarehouseTab(QWidget):
    scan_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # Warning
        warn = QLabel("Open the warehouse UI in-game before scanning.")
        warn.setStyleSheet("color: orange;")
        layout.addWidget(warn)

        # Top bar
        top = QHBoxLayout()
        self._scan_btn = QPushButton("Scan Warehouse")
        self._scan_btn.clicked.connect(self.scan_requested)
        self._status_lbl = QLabel("Not scanned")
        top.addWidget(self._scan_btn)
        top.addWidget(self._status_lbl)
        top.addStretch()
        layout.addLayout(top)

        # Table
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["#", "Item ID", "Qty", "Name"])
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self._table)

        self._footer_lbl = QLabel("")
        layout.addWidget(self._footer_lbl)

    def set_scanning(self, scanning: bool):
        self._scan_btn.setEnabled(not scanning)
        if scanning:
            self._status_lbl.setText("Scanning...")
        else:
            # Reset label so it doesn't stay stuck at "Scanning..." after an error
            if self._status_lbl.text() == "Scanning...":
                self._status_lbl.setText("Ready")

    def populate(self, items: list):
        """items = list of (item_id, qty, name)"""
        self._table.setRowCount(len(items))
        for i, (item_id, qty, name) in enumerate(items):
            for col, val in enumerate([i + 1, item_id, qty, name]):
                cell = QTableWidgetItem(str(val))
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter if col < 3
                                      else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                self._table.setItem(i, col, cell)
        now = datetime.now().strftime("%H:%M:%S")
        self._status_lbl.setText(f"Last updated: {now}")
        self._footer_lbl.setText(f"Total: {len(items)} items")
        self._scan_btn.setEnabled(True)
