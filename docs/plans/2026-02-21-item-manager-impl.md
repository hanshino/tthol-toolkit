# Item Manager Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a 道具總覽 (Item Overview) tab to the existing PySide6 GUI that shows all characters' latest inventory/warehouse snapshots from a local SQLite database.

**Architecture:** `snapshot_db.py` owns all DB I/O (schema creation, dedup via SHA256, read/write). `inventory_manager_tab.py` is a pure display widget that calls `snapshot_db.load_latest_snapshots()` and renders results. Existing tabs get a "Save Snapshot" button; `MainWindow` wires signals to the DB layer (it already holds the current character name from `stats_updated`).

**Tech Stack:** PySide6, Python `sqlite3`, `hashlib` (stdlib), existing `tthol.sqlite` for item name lookups.

---

## Task 1: Create `gui/snapshot_db.py`

**Files:**
- Create: `gui/snapshot_db.py`
- Create: `tests/test_snapshot_db.py`

**Step 1: Write the failing tests**

```python
# tests/test_snapshot_db.py
import os
import pytest
from gui.snapshot_db import SnapshotDB


@pytest.fixture
def db(tmp_path):
    return SnapshotDB(str(tmp_path / "test.db"))


def test_save_and_load(db):
    items = [{"item_id": 100, "qty": 3}, {"item_id": 200, "qty": 1}]
    saved = db.save_snapshot("Hero", "inventory", items)
    assert saved is True
    rows = db.load_latest_snapshots()
    assert len(rows) == 2
    assert rows[0]["character"] == "Hero"
    assert rows[0]["source"] == "inventory"
    assert {r["item_id"] for r in rows} == {100, 200}


def test_dedup_skips_identical(db):
    items = [{"item_id": 100, "qty": 3}]
    db.save_snapshot("Hero", "inventory", items)
    saved = db.save_snapshot("Hero", "inventory", items)
    assert saved is False


def test_dedup_saves_when_changed(db):
    db.save_snapshot("Hero", "inventory", [{"item_id": 100, "qty": 3}])
    saved = db.save_snapshot("Hero", "inventory", [{"item_id": 100, "qty": 5}])
    assert saved is True


def test_load_returns_only_latest_per_character_source(db):
    db.save_snapshot("Hero", "inventory", [{"item_id": 100, "qty": 1}])
    db.save_snapshot("Hero", "inventory", [{"item_id": 100, "qty": 2}])
    rows = db.load_latest_snapshots()
    assert len(rows) == 1
    assert rows[0]["qty"] == 2


def test_multiple_characters(db):
    db.save_snapshot("Hero", "inventory", [{"item_id": 1, "qty": 1}])
    db.save_snapshot("Alt", "inventory", [{"item_id": 2, "qty": 5}])
    rows = db.load_latest_snapshots()
    chars = {r["character"] for r in rows}
    assert chars == {"Hero", "Alt"}
```

**Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_snapshot_db.py -v
```
Expected: `ModuleNotFoundError: No module named 'gui.snapshot_db'`

**Step 3: Implement `gui/snapshot_db.py`**

```python
"""
Snapshot database for tthol_inventory.db.

Schema:
    snapshots(id, character, source, scanned_at, items TEXT, checksum TEXT)

items is a JSON array sorted by item_id: [{"item_id": N, "qty": N}, ...]
checksum is SHA256 of the canonical items JSON string.
"""
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

ITEM_NAME_DB = Path(__file__).parent.parent / "tthol.sqlite"
DEFAULT_DB = Path(__file__).parent.parent / "tthol_inventory.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    character   TEXT NOT NULL,
    source      TEXT NOT NULL,
    scanned_at  TEXT NOT NULL,
    items       TEXT NOT NULL,
    checksum    TEXT NOT NULL
);
"""


def _canonical(items: list[dict]) -> str:
    """Return canonical JSON string for hashing (sorted by item_id)."""
    sorted_items = sorted(items, key=lambda x: x["item_id"])
    return json.dumps(sorted_items, separators=(",", ":"), ensure_ascii=False)


def _checksum(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SnapshotDB:
    def __init__(self, path: str | None = None):
        db_path = path or str(DEFAULT_DB)
        self._con = sqlite3.connect(db_path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.executescript(SCHEMA)
        self._con.commit()

    def save_snapshot(self, character: str, source: str, items: list[dict]) -> bool:
        """
        Save a snapshot. Returns True if saved, False if identical to last snapshot.
        items: list of {"item_id": int, "qty": int}
        """
        canonical = _canonical(items)
        chk = _checksum(canonical)

        # Dedup: check last snapshot for this character+source
        row = self._con.execute(
            "SELECT checksum FROM snapshots "
            "WHERE character=? AND source=? ORDER BY id DESC LIMIT 1",
            (character, source),
        ).fetchone()
        if row and row["checksum"] == chk:
            return False

        now = datetime.now().isoformat(timespec="seconds")
        self._con.execute(
            "INSERT INTO snapshots (character, source, scanned_at, items, checksum) "
            "VALUES (?, ?, ?, ?, ?)",
            (character, source, now, canonical, chk),
        )
        self._con.commit()
        return True

    def load_latest_snapshots(self) -> list[dict]:
        """
        Return rows for the latest snapshot per (character, source).
        Each row: {character, source, item_id, qty, name, scanned_at}
        Item names are resolved from tthol.sqlite.
        """
        # Fetch latest snapshot id per character+source
        snapshot_rows = self._con.execute(
            "SELECT id, character, source, scanned_at, items "
            "FROM snapshots "
            "WHERE id IN ("
            "  SELECT MAX(id) FROM snapshots GROUP BY character, source"
            ")"
        ).fetchall()

        # Build item_id → name map from tthol.sqlite
        name_map: dict[int, str] = {}
        if ITEM_NAME_DB.exists():
            with sqlite3.connect(str(ITEM_NAME_DB)) as name_con:
                for r in name_con.execute("SELECT id, name FROM items"):
                    name_map[r[0]] = r[1]

        result = []
        for snap in snapshot_rows:
            items = json.loads(snap["items"])
            for item in items:
                result.append({
                    "character": snap["character"],
                    "source": snap["source"],
                    "scanned_at": snap["scanned_at"],
                    "item_id": item["item_id"],
                    "qty": item["qty"],
                    "name": name_map.get(item["item_id"], "???"),
                })
        return result
```

**Step 4: Run tests to verify they pass**

```
uv run pytest tests/test_snapshot_db.py -v
```
Expected: all 5 tests PASS.

**Step 5: Commit**

```bash
git add gui/snapshot_db.py tests/test_snapshot_db.py
git commit -m "feat: add snapshot_db with SHA256 dedup"
```

---

## Task 2: Create `gui/inventory_manager_tab.py`

**Files:**
- Create: `gui/inventory_manager_tab.py`

No unit tests for this tab (pure Qt display widget — verify manually).

**Step 1: Implement the tab**

```python
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
```

**Step 2: Commit**

```bash
git add gui/inventory_manager_tab.py
git commit -m "feat: add InventoryManagerTab with search/filter"
```

---

## Task 3: Add "Save Snapshot" signal to `InventoryTab` and `WarehouseTab`

**Files:**
- Modify: `gui/inventory_tab.py`
- Modify: `gui/warehouse_tab.py`

Both tabs need a `save_requested = Signal()` and a disabled "Save Snapshot" button that enables after `populate()` is called.

**Step 1: Modify `gui/inventory_tab.py`**

After line 11 (`scan_requested = Signal()`), add:
```python
    save_requested = Signal()
```

In `__init__`, after `top.addWidget(self._scan_btn)`:
```python
        self._save_btn = QPushButton("Save Snapshot")
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self.save_requested)
        top.addWidget(self._save_btn)
```

At the end of `populate()`, add:
```python
        self._save_btn.setEnabled(True)
```

**Step 2: Modify `gui/warehouse_tab.py`**

Apply identical changes: add `save_requested = Signal()`, add `self._save_btn`, enable in `populate()`.

**Step 3: Commit**

```bash
git add gui/inventory_tab.py gui/warehouse_tab.py
git commit -m "feat: add Save Snapshot button to inventory and warehouse tabs"
```

---

## Task 4: Wire everything in `gui/main_window.py`

**Files:**
- Modify: `gui/main_window.py`

**Step 1: Import new modules**

Add to the existing imports block at the top:
```python
from gui.snapshot_db import SnapshotDB
from gui.inventory_manager_tab import InventoryManagerTab
```

**Step 2: Store DB, character name, and last scan results**

In `__init__`, after `self._pending_hp = None`, add:
```python
        self._snapshot_db = SnapshotDB()
        self._current_character: str = ""
        self._last_inventory: list[dict] = []
        self._last_warehouse: list[dict] = []
```

**Step 3: Add the 4th tab**

After `self._warehouse_tab = WarehouseTab()`, add:
```python
        self._manager_tab = InventoryManagerTab(self._snapshot_db)
```

After `tabs.addTab(self._warehouse_tab, "Warehouse")`, add:
```python
        tabs.addTab(self._manager_tab, "道具總覽")
```

**Step 4: Connect save signals**

After the existing signal connections at the end of `__init__`:
```python
        self._inventory_tab.save_requested.connect(self._on_inventory_save)
        self._warehouse_tab.save_requested.connect(self._on_warehouse_save)
```

**Step 5: Track character name and last scan results**

In `_on_stats_updated`, after `data = {name: value for name, value in fields}`:
```python
        self._current_character = data.get("角色名稱", self._current_character)
```

In `_on_inventory_ready`, after `self._inventory_tab.populate(items)`:
```python
        self._last_inventory = [{"item_id": iid, "qty": qty} for iid, qty, _ in items]
```

In `_on_warehouse_ready`, after `self._warehouse_tab.populate(items)`:
```python
        self._last_warehouse = [{"item_id": iid, "qty": qty} for iid, qty, _ in items]
```

**Step 6: Add save slots**

```python
    @Slot()
    def _on_inventory_save(self):
        if not self._current_character or not self._last_inventory:
            self.statusBar().showMessage("No inventory data to save", 3000)
            return
        saved = self._snapshot_db.save_snapshot(
            self._current_character, "inventory", self._last_inventory
        )
        if saved:
            self.statusBar().showMessage("Snapshot saved", 3000)
        else:
            self.statusBar().showMessage("No change detected, skipped", 3000)
        self._manager_tab.refresh()

    @Slot()
    def _on_warehouse_save(self):
        if not self._current_character or not self._last_warehouse:
            self.statusBar().showMessage("No warehouse data to save", 3000)
            return
        saved = self._snapshot_db.save_snapshot(
            self._current_character, "warehouse", self._last_warehouse
        )
        if saved:
            self.statusBar().showMessage("Snapshot saved", 3000)
        else:
            self.statusBar().showMessage("No change detected, skipped", 3000)
        self._manager_tab.refresh()
```

**Step 7: Commit**

```bash
git add gui/main_window.py
git commit -m "feat: wire snapshot DB and 道具總覽 tab into MainWindow"
```

---

## Task 5: Manual Smoke Test

Launch the GUI and verify:

1. `uv run python -m gui` opens without errors.
2. Connect with a known HP value → character name appears in vitals.
3. Click "Scan Inventory" → items appear. "Save Snapshot" button becomes enabled.
4. Click "Save Snapshot" → status bar shows "Snapshot saved".
5. Click "Save Snapshot" again (no rescan) → status bar shows "No change detected, skipped".
6. Switch to 道具總覽 tab → items appear with character name, source "inventory", timestamp.
7. Search box and dropdowns filter the table correctly.
8. Scan Warehouse (with warehouse open in-game) → same save flow, source "warehouse".
9. 道具總覽 shows rows from both sources.

---

## Notes

- `tthol_inventory.db` is created automatically beside `tthol.sqlite` on first run.
- `_current_character` is set from `stats_updated` which only fires when LOCATED. If the user saves before connecting, the save is blocked with a status message.
- Historical snapshots accumulate in the DB (all rows kept); `load_latest_snapshots()` queries only the MAX(id) per character+source, so old history is available for future analytics but not shown in the UI.
