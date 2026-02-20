"""Item Overview tab: shows latest snapshots for all characters."""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView,
)
from PySide6.QtCore import Qt

from gui.snapshot_db import SnapshotDB


class InventoryManagerTab(QWidget):
    COLUMNS = ["Character", "Item ID", "Name", "Qty", "Source", "Snapshot Time"]

    def __init__(self, db: SnapshotDB, parent=None):
        super().__init__(parent)
        self._db = db
        self._all_rows: list[dict] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # ── Filter bar ──────────────────────────────────────────────────
        filter_bar = QHBoxLayout()

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search item name...")
        self._search.setMaximumWidth(220)
        self._search.textChanged.connect(self._apply_filter)
        filter_bar.addWidget(self._search)

        self._char_combo = QComboBox()
        self._char_combo.addItem("All Characters")
        self._char_combo.currentIndexChanged.connect(self._apply_filter)
        filter_bar.addWidget(self._char_combo)

        self._source_combo = QComboBox()
        self._source_combo.addItems(["All Sources", "inventory", "warehouse"])
        self._source_combo.currentIndexChanged.connect(self._apply_filter)
        filter_bar.addWidget(self._source_combo)

        filter_bar.addStretch()
        layout.addLayout(filter_bar)

        # ── Table ────────────────────────────────────────────────────────
        self._table = QTableWidget(0, len(self.COLUMNS))
        self._table.setHorizontalHeaderLabels(self.COLUMNS)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setSortingEnabled(True)
        layout.addWidget(self._table)

        self._footer = QLabel("")
        self._footer.setStyleSheet("color: #475569; font-size: 11px;")
        layout.addWidget(self._footer)

        self.refresh()

    # ------------------------------------------------------------------
    def refresh(self):
        """Reload from DB and re-apply current filters."""
        self._all_rows = self._db.load_latest_snapshots()
        self._rebuild_char_combo()
        self._apply_filter()

    def _rebuild_char_combo(self):
        chars = sorted({r["character"] for r in self._all_rows})
        current = self._char_combo.currentText()
        self._char_combo.blockSignals(True)
        self._char_combo.clear()
        self._char_combo.addItem("All Characters")
        for c in chars:
            self._char_combo.addItem(c)
        idx = self._char_combo.findText(current)
        self._char_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._char_combo.blockSignals(False)

    def _apply_filter(self):
        name_filter = self._search.text().strip().lower()
        char_filter = self._char_combo.currentText()
        source_filter = self._source_combo.currentText()

        visible = []
        for r in self._all_rows:
            if name_filter and name_filter not in r["name"].lower():
                continue
            if char_filter != "All Characters" and r["character"] != char_filter:
                continue
            if source_filter != "All Sources" and r["source"] != source_filter:
                continue
            visible.append(r)

        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(visible))
        for i, r in enumerate(visible):
            values = [
                r["character"],
                str(r["item_id"]),
                r["name"],
                str(r["qty"]),
                r["source"],
                r["scanned_at"],
            ]
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                if col in (1, 3):  # item_id, qty — right-align numbers
                    align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                item.setTextAlignment(align)
                self._table.setItem(i, col, item)
        self._table.setSortingEnabled(True)
        self._footer.setText(f"{len(visible)} items")
