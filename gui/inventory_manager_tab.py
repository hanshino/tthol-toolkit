"""Item Overview tab: shows latest snapshots for all characters."""

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QHeaderView,
    QTreeWidget,
    QTreeWidgetItem,
    QPushButton,
    QStackedWidget,
    QButtonGroup,
    QScrollArea,
    QFrame,
)
from PySide6.QtCore import Qt

from gui.snapshot_db import SnapshotDB
from gui.i18n import t
from gui.character_card import CharacterCard

_MODE_BY_CHAR = "by_char"
_MODE_BY_ITEM = "by_item"


class InventoryManagerTab(QWidget):
    ITEM_COLUMNS = [
        t("mgr_col_name"),
        t("mgr_col_item_id"),
        t("mgr_col_total_qty"),
        t("mgr_col_details"),
    ]
    ITEM_COLUMNS = [
        t("mgr_col_name"),
        t("mgr_col_item_id"),
        t("mgr_col_total_qty"),
        t("mgr_col_details"),
    ]

    def __init__(self, db: SnapshotDB, parent=None):
        super().__init__(parent)
        self._db = db
        self._all_rows: list[dict] = []
        self._mode = _MODE_BY_ITEM

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # ── Filter bar ──────────────────────────────────────────────────
        filter_bar = QHBoxLayout()

        self._search = QLineEdit()
        self._search.setPlaceholderText(t("search_placeholder"))
        self._search.setMaximumWidth(220)
        self._search.textChanged.connect(self._apply_filter)
        filter_bar.addWidget(self._search)

        self._char_combo = QComboBox()
        self._char_combo.addItem(t("all_characters"))
        self._char_combo.currentIndexChanged.connect(self._apply_filter)
        filter_bar.addWidget(self._char_combo)

        self._source_combo = QComboBox()
        self._source_combo.addItems(
            [
                t("all_sources"),
                t("source_inventory"),
                t("source_warehouse"),
            ]
        )
        self._source_combo.currentIndexChanged.connect(self._apply_filter)
        filter_bar.addWidget(self._source_combo)

        filter_bar.addStretch()

        self._btn_by_char = QPushButton(t("by_char"))
        self._btn_by_char.setObjectName("toggle_left")
        self._btn_by_char.setCheckable(True)
        self._btn_by_char.clicked.connect(lambda: self._set_mode(_MODE_BY_CHAR))
        filter_bar.addWidget(self._btn_by_char)

        self._btn_by_item = QPushButton(t("by_item"))
        self._btn_by_item.setObjectName("toggle_right")
        self._btn_by_item.setCheckable(True)
        self._btn_by_item.clicked.connect(lambda: self._set_mode(_MODE_BY_ITEM))
        filter_bar.addWidget(self._btn_by_item)

        self._view_group = QButtonGroup(self)
        self._view_group.setExclusive(True)
        self._view_group.addButton(self._btn_by_char)
        self._view_group.addButton(self._btn_by_item)

        layout.addLayout(filter_bar)

        # ── Stacked widget ───────────────────────────────────────────────
        self._stack = QStackedWidget()

        # Index 0: By Char — scrollable card list
        self._char_scroll = QScrollArea()
        self._char_scroll.setWidgetResizable(True)
        self._char_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._char_container = QWidget()
        self._char_layout = QVBoxLayout(self._char_container)
        self._char_layout.setContentsMargins(0, 0, 0, 0)
        self._char_layout.setSpacing(8)
        self._char_layout.addStretch()
        self._char_scroll.setWidget(self._char_container)
        self._stack.addWidget(self._char_scroll)
        self._cards: dict[str, CharacterCard] = {}

        # Index 1: By Item tree
        self._tree = QTreeWidget()
        self._tree.setColumnCount(len(self.ITEM_COLUMNS))
        self._tree.setHeaderLabels(self.ITEM_COLUMNS)
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._tree.setEditTriggers(QTreeWidget.EditTrigger.NoEditTriggers)
        self._tree.setSelectionBehavior(QTreeWidget.SelectionBehavior.SelectRows)
        self._tree.setAlternatingRowColors(True)
        self._tree.setRootIsDecorated(True)
        self._stack.addWidget(self._tree)

        layout.addWidget(self._stack)

        self._footer = QLabel("")
        self._footer.setStyleSheet("color: #475569;")
        layout.addWidget(self._footer)

        self._set_mode(_MODE_BY_ITEM, initial=True)
        self.refresh()

    # ------------------------------------------------------------------
    def refresh(self):
        """Reload from DB and re-apply current filters."""
        self._all_rows = self._db.load_latest_snapshots()
        self._rebuild_char_combo()
        self._apply_filter()

    def _set_mode(self, mode: str, initial: bool = False):
        if mode == self._mode and not initial:
            return
        self._mode = mode
        is_by_item = mode == _MODE_BY_ITEM
        self._btn_by_char.setChecked(not is_by_item)
        self._btn_by_item.setChecked(is_by_item)
        self._char_combo.setVisible(not is_by_item)
        self._source_combo.setVisible(not is_by_item)
        self._stack.setCurrentIndex(1 if is_by_item else 0)
        if not initial:
            self._apply_filter()

    def _rebuild_char_combo(self):
        chars = sorted({r["character"] for r in self._all_rows})
        current = self._char_combo.currentText()
        self._char_combo.blockSignals(True)
        self._char_combo.clear()
        self._char_combo.addItem(t("all_characters"))
        for c in chars:
            self._char_combo.addItem(c)
        idx = self._char_combo.findText(current) if current else -1
        self._char_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._char_combo.blockSignals(False)

    def _apply_filter(self):
        if self._mode == _MODE_BY_ITEM:
            self._populate_tree()
        else:
            self._populate_cards()

    def _populate_cards(self):
        """Populate the By Char scrollable card list."""
        name_filter = self._search.text().strip().lower()
        char_filter = self._char_combo.currentText()
        source_filter = self._source_combo.currentText()

        _all_chars = t("all_characters")
        _all_sources = t("all_sources")
        _src_map = {
            t("source_inventory"): "inventory",
            t("source_warehouse"): "warehouse",
        }

        visible = []
        for r in self._all_rows:
            if name_filter and name_filter not in r["name"].lower():
                continue
            if char_filter != _all_chars and r["character"] != char_filter:
                continue
            if source_filter != _all_sources and r["source"] != _src_map.get(
                source_filter, source_filter
            ):
                continue
            visible.append(r)

        # Group by character
        by_char: dict[str, list[dict]] = {}
        for r in visible:
            by_char.setdefault(r["character"], []).append(r)

        # Remove cards for characters no longer in filtered set
        removed = [c for c in list(self._cards) if c not in by_char]
        for char in removed:
            card = self._cards.pop(char)
            self._char_layout.removeWidget(card)
            card.deleteLater()

        # Insert / update cards in alphabetical order
        sorted_chars = sorted(by_char.keys())
        for i, char in enumerate(sorted_chars):
            rows = by_char[char]
            if char in self._cards:
                card = self._cards[char]
                card.update_rows(rows)
                # Reposition if needed (stretch is last item, so layout count = cards + 1)
                current_pos = self._char_layout.indexOf(card)
                if current_pos != i:
                    self._char_layout.insertWidget(i, card)
            else:
                card = CharacterCard(char, rows, self._db)
                self._cards[char] = card
                self._char_layout.insertWidget(i, card)

        self._footer.setText(t("footer_items", n=len(visible)))

    def _populate_tree(self):
        """Populate the By Item aggregated tree."""
        name_filter = self._search.text().strip().lower()

        aggregated: dict[int, dict] = {}
        for r in self._all_rows:
            if name_filter and name_filter not in r["name"].lower():
                continue
            iid = r["item_id"]
            if iid not in aggregated:
                aggregated[iid] = {
                    "item_id": iid,
                    "name": r["name"],
                    "total_qty": 0,
                    "rows": [],
                }
            aggregated[iid]["total_qty"] += r["qty"]
            aggregated[iid]["rows"].append(r)

        items_sorted = sorted(aggregated.values(), key=lambda x: x["name"])

        self._tree.clear()
        total_qty = 0
        for item in items_sorted:
            total_qty += item["total_qty"]
            char_count = len({r["character"] for r in item["rows"]})

            parent = QTreeWidgetItem(self._tree)
            parent.setText(0, item["name"])
            parent.setText(1, str(item["item_id"]))
            parent.setText(2, str(item["total_qty"]))
            parent.setText(3, t("char_count", n=char_count))
            parent.setTextAlignment(
                1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            parent.setTextAlignment(
                2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )

            for r in sorted(item["rows"], key=lambda x: x["character"]):
                child = QTreeWidgetItem(parent)
                child.setText(0, r["character"])
                child.setText(1, "")
                child.setText(2, str(r["qty"]))
                child.setText(3, f"{r['source']} · {r['scanned_at']}")
                child.setTextAlignment(
                    2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )

        self._footer.setText(
            t("summary_kinds_total", kinds=len(items_sorted), total=total_qty)
        )
