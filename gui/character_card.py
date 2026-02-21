"""Read-only character card widget for the By Char view in InventoryManagerTab."""

from PySide6.QtWidgets import (
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
)
from PySide6.QtCore import Qt

from gui.snapshot_db import SnapshotDB
from gui.i18n import t


class _NumericItem(QTableWidgetItem):
    """QTableWidgetItem that sorts numerically instead of lexicographically."""

    def __lt__(self, other: "QTableWidgetItem") -> bool:
        try:
            return float(self.text()) < float(other.text())
        except ValueError:
            return super().__lt__(other)


CARD_COLUMNS = [
    t("mgr_col_item_id"),
    t("mgr_col_name"),
    t("mgr_col_qty"),
    t("mgr_col_source"),
]


class CharacterCard(QFrame):
    """Read-only card for one character in the By Char view."""

    def __init__(self, character: str, rows: list[dict], db: SnapshotDB, parent=None):
        super().__init__(parent)
        self.setObjectName("char_card")
        self._character = character
        self._rows = rows
        self._db = db

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Header ──────────────────────────────────────────────────────
        header_frame = QFrame()
        header_frame.setObjectName("char_card_header")
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(16, 10, 12, 10)
        header_layout.setSpacing(8)

        self._name_lbl = QLabel(character)
        self._name_lbl.setStyleSheet(
            "color: #F8FAFC; font-weight: 700; padding: 2px 8px;"
        )
        header_layout.addWidget(self._name_lbl)

        self._acct_lbl = QLabel()
        self._acct_lbl.setStyleSheet("color: #475569; font-size: 9pt; padding: 0 6px;")
        header_layout.addWidget(self._acct_lbl)

        self._time_lbl = QLabel()
        self._time_lbl.setStyleSheet("color: #94A3B8; font-size: 9pt; padding: 0 6px;")
        header_layout.addWidget(self._time_lbl)

        header_layout.addStretch()

        outer.addWidget(header_frame)

        # ── Item table ───────────────────────────────────────────────────
        self._table = QTableWidget(0, len(CARD_COLUMNS))
        self._table.setHorizontalHeaderLabels(CARD_COLUMNS)
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)
        # Disable internal scrollbar — height will be fixed
        self._table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        outer.addWidget(self._table)

        self._populate(rows)

    def _populate(self, rows: list[dict]) -> None:
        """Fill account badge, time badge, and table rows."""
        # Account badge
        acct = self._db.get_character_account(self._character)
        acct_name = acct["name"] if acct else t("no_account")
        self._acct_lbl.setText(f"· {acct_name}")

        # Latest snapshot time
        latest = max((r["scanned_at"] for r in rows), default="")
        self._time_lbl.setText(f"· {latest[:16]}" if latest else "")

        # Fill table
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            values = [str(r["item_id"]), r["name"], str(r["qty"]), r["source"]]
            for col, val in enumerate(values):
                item = _NumericItem(val) if col in (0, 2) else QTableWidgetItem(val)
                align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                if col in (0, 2):
                    align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                item.setTextAlignment(align)
                self._table.setItem(i, col, item)
        self._table.setSortingEnabled(True)

        # Fix table height so no internal scrollbar is needed
        self._adjust_table_height()

    def _adjust_table_height(self) -> None:
        """Set table to exact content height (header + all rows)."""
        header_h = self._table.horizontalHeader().height()
        row_h = sum(self._table.rowHeight(i) for i in range(self._table.rowCount()))
        self._table.setFixedHeight(header_h + row_h + 2)

    def update_rows(self, rows: list[dict]) -> None:
        """Refresh card with new rows."""
        self._rows = rows
        self._populate(rows)
