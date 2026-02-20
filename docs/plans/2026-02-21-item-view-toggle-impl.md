# Item View Toggle Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a By Item aggregated view to the 道具總覽 tab, with a segmented toggle to switch between By Char (current flat table) and By Item (QTreeWidget with expand/collapse per item).

**Architecture:** `InventoryManagerTab` gains a `QStackedWidget` holding the existing `QTableWidget` (index 0, By Char) and a new `QTreeWidget` (index 1, By Item). A `_mode` state drives which widget is shown. `_apply_filter()` delegates to `_populate_table()` or `_populate_tree()` based on mode. The char/source combos are hidden in By Item mode. Theme gets new QSS for toggle buttons and tree widget.

**Tech Stack:** PySide6 `QTreeWidget`, `QStackedWidget`, `QPushButton` (checkable), existing `SnapshotDB`.

---

## Task 1: Rewrite `gui/inventory_manager_tab.py` with toggle + tree view

**Files:**
- Modify: `gui/inventory_manager_tab.py`

No new unit tests (pure Qt display layer). Manual verification in Task 2.

### Step 1: Replace the file with the full new implementation

Write `gui/inventory_manager_tab.py` with this exact content:

```python
"""Item Overview tab: shows latest snapshots for all characters."""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QTreeWidget, QTreeWidgetItem, QPushButton, QStackedWidget,
)
from PySide6.QtCore import Qt

from gui.snapshot_db import SnapshotDB

_MODE_BY_CHAR = "by_char"
_MODE_BY_ITEM = "by_item"


class _NumericItem(QTableWidgetItem):
    """QTableWidgetItem that sorts numerically instead of lexicographically."""
    def __lt__(self, other: "QTableWidgetItem") -> bool:
        try:
            return float(self.text()) < float(other.text())
        except ValueError:
            return super().__lt__(other)


class InventoryManagerTab(QWidget):
    CHAR_COLUMNS = ["Character", "Item ID", "Name", "Qty", "Source", "Snapshot Time"]
    ITEM_COLUMNS = ["Name", "Item ID", "Total Qty", "Details"]

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

        self._btn_by_char = QPushButton("By Char")
        self._btn_by_char.setObjectName("toggle_left")
        self._btn_by_char.setCheckable(True)
        self._btn_by_char.clicked.connect(lambda: self._set_mode(_MODE_BY_CHAR))
        filter_bar.addWidget(self._btn_by_char)

        self._btn_by_item = QPushButton("By Item")
        self._btn_by_item.setObjectName("toggle_right")
        self._btn_by_item.setCheckable(True)
        self._btn_by_item.clicked.connect(lambda: self._set_mode(_MODE_BY_ITEM))
        filter_bar.addWidget(self._btn_by_item)

        layout.addLayout(filter_bar)

        # ── Stacked widget ───────────────────────────────────────────────
        self._stack = QStackedWidget()

        # Index 0: By Char flat table
        self._table = QTableWidget(0, len(self.CHAR_COLUMNS))
        self._table.setHorizontalHeaderLabels(self.CHAR_COLUMNS)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setSortingEnabled(True)
        self._stack.addWidget(self._table)

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
        self._char_combo.addItem("All Characters")
        for c in chars:
            self._char_combo.addItem(c)
        idx = self._char_combo.findText(current)
        self._char_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._char_combo.blockSignals(False)

    def _apply_filter(self):
        if self._mode == _MODE_BY_ITEM:
            self._populate_tree()
        else:
            self._populate_table()

    def _populate_table(self):
        """Populate the By Char flat table (existing logic)."""
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
                item = _NumericItem(val) if col in (1, 3) else QTableWidgetItem(val)
                align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                if col in (1, 3):
                    align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                item.setTextAlignment(align)
                self._table.setItem(i, col, item)
        self._table.setSortingEnabled(True)
        self._footer.setText(f"{len(visible)} items")

    def _populate_tree(self):
        """Populate the By Item aggregated tree."""
        name_filter = self._search.text().strip().lower()

        # Aggregate by item_id
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
            parent.setText(3, f"{char_count} char(s)")
            parent.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            parent.setTextAlignment(2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            for r in sorted(item["rows"], key=lambda x: x["character"]):
                child = QTreeWidgetItem(parent)
                child.setText(0, r["character"])
                child.setText(1, "")
                child.setText(2, str(r["qty"]))
                child.setText(3, f"{r['source']} · {r['scanned_at']}")
                child.setTextAlignment(2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self._footer.setText(
            f"{len(items_sorted)} kinds · {total_qty} total"
        )
```

### Step 2: Verify import

```
uv run python -c "from gui.inventory_manager_tab import InventoryManagerTab; print('OK')"
```

Expected: `OK`

### Step 3: Commit

```bash
git add gui/inventory_manager_tab.py
git commit -m "feat: add By Item tree view with toggle to inventory manager tab"
```

---

## Task 2: Add QSS for toggle buttons and QTreeWidget to `gui/theme.py`

**Files:**
- Modify: `gui/theme.py`

### Step 1: Append new QSS sections to `DARK_QSS`

In `gui/theme.py`, find the closing `"""` of `DARK_QSS` (currently the last line after the scrollbar section). Insert these two new sections **before** that closing `"""`:

```css
/* ── View toggle segmented control ──────────────────────────── */
QPushButton#toggle_left, QPushButton#toggle_right {{
    padding: 4px 12px;
    min-height: 26px;
    font-weight: 500;
    border-radius: 0;
}}
QPushButton#toggle_left {{
    border-top-left-radius: 6px;
    border-bottom-left-radius: 6px;
    border-right: none;
}}
QPushButton#toggle_right {{
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
}}
QPushButton#toggle_left:checked, QPushButton#toggle_right:checked {{
    background-color: {GREEN};
    color: {BG_BASE};
    border-color: {GREEN};
    font-weight: 700;
}}
QPushButton#toggle_left:hover:!checked, QPushButton#toggle_right:hover:!checked {{
    background-color: {BORDER};
    border-color: {GREEN};
    color: {GREEN};
}}

/* ── Tree Widget ─────────────────────────────────────────────── */
QTreeWidget {{
    background-color: {BG_SURFACE};
    alternate-background-color: {BG_CARD};
    border: 1px solid {BG_CARD};
    border-radius: 6px;
    selection-background-color: #122118;
    selection-color: {GREEN};
    outline: 0;
}}
QTreeWidget::item {{
    padding: 4px 8px;
    border: none;
}}
QTreeWidget::item:selected {{
    background-color: #122118;
    color: {GREEN};
}}
QTreeWidget QHeaderView::section {{
    background-color: {BG_BASE};
    color: {DIM};
    border: none;
    border-bottom: 1px solid {BG_CARD};
    border-right: 1px solid {BG_CARD};
    padding: 6px 8px;
    font-weight: 600;
    font-size: 8pt;
    letter-spacing: 0.5px;
}}
QTreeWidget QHeaderView::section:last-child {{
    border-right: none;
}}
```

Note: use `{{` and `}}` (escaped braces) since `DARK_QSS` is an f-string.

### Step 2: Run existing tests

```
uv run pytest tests/ -v
```

Expected: 5/5 pass.

### Step 3: Commit

```bash
git add gui/theme.py
git commit -m "feat: add toggle button and tree widget QSS to dark theme"
```

---

## Task 3: Manual smoke test

Launch GUI and verify:

```
uv run python -m gui
```

1. 道具總覽 tab opens in **By Item** mode by default.
2. "By Item" button is highlighted green; char/source combos are hidden.
3. Items are shown as parent rows (Name / Item ID / Total Qty / N char(s)).
4. Clicking the ▶ arrow expands to show child rows (Character / — / Qty / source · time).
5. Search box filters parent rows by name (matching children collapse away).
6. Clicking **By Char** switches to the flat table; char/source combos reappear.
7. "By Char" button is highlighted; "By Item" is un-highlighted.
8. Toggle back to By Item — search state is preserved, filters re-applied.
9. Footer in By Item shows "N kinds · M total"; in By Char shows "N items".
10. No `QFont::setPointSize` warnings in console.
