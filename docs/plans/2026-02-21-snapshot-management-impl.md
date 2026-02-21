# Snapshot Management & Account Grouping Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add snapshot deletion and account grouping to the Item Overview tab via inline character cards with expandable management panels.

**Architecture:** Extend `SnapshotDB` with two new tables (`accounts`, `character_accounts`) and deletion methods. Refactor the By Char view in `InventoryManagerTab` from a flat `QTableWidget` into a `QScrollArea` of character-card widgets, each with an inline animated management panel.

**Tech Stack:** PySide6, SQLite (via `sqlite3`), existing `gui/snapshot_db.py`, `gui/inventory_manager_tab.py`, `gui/theme.py`, `gui/i18n.py`

---

## Task 1: Extend SnapshotDB — schema + account methods

**Files:**
- Modify: `gui/snapshot_db.py`

**Step 1: Add schema constants for new tables**

At line 19 (after `SCHEMA = """`), replace the SCHEMA string so it creates all three tables:

```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    character   TEXT NOT NULL,
    source      TEXT NOT NULL,
    scanned_at  TEXT NOT NULL,
    items       TEXT NOT NULL,
    checksum    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS character_accounts (
    character  TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE SET NULL
);
"""
```

**Step 2: Add deletion and account methods to `SnapshotDB`**

Add these methods after `load_latest_snapshots()`:

```python
def delete_snapshot(self, snapshot_id: int) -> None:
    """Delete a single snapshot row by id."""
    self._con.execute("DELETE FROM snapshots WHERE id=?", (snapshot_id,))
    self._con.commit()

def delete_character(self, character: str) -> None:
    """Delete all snapshots and account assignment for a character."""
    self._con.execute("DELETE FROM snapshots WHERE character=?", (character,))
    self._con.execute("DELETE FROM character_accounts WHERE character=?", (character,))
    self._con.commit()

def list_all_snapshots(self, character: str) -> list[dict]:
    """
    Return all snapshots for a character, newest first.
    Each dict: {id, source, scanned_at, item_count}
    """
    rows = self._con.execute(
        "SELECT id, source, scanned_at, items FROM snapshots "
        "WHERE character=? ORDER BY id DESC",
        (character,),
    ).fetchall()
    result = []
    for r in rows:
        items = json.loads(r["items"])
        result.append({
            "id": r["id"],
            "source": r["source"],
            "scanned_at": r["scanned_at"],
            "item_count": len(items),
        })
    return result

def list_accounts(self) -> list[dict]:
    """Return all accounts as list of {id, name}."""
    rows = self._con.execute("SELECT id, name FROM accounts ORDER BY name").fetchall()
    return [{"id": r["id"], "name": r["name"]} for r in rows]

def create_account(self, name: str) -> int:
    """Create a new account, return its id."""
    cur = self._con.execute("INSERT INTO accounts (name) VALUES (?)", (name,))
    self._con.commit()
    return cur.lastrowid

def set_character_account(self, character: str, account_id: int) -> None:
    """Assign a character to an account (upsert)."""
    self._con.execute(
        "INSERT INTO character_accounts (character, account_id) VALUES (?, ?) "
        "ON CONFLICT(character) DO UPDATE SET account_id=excluded.account_id",
        (character, account_id),
    )
    self._con.commit()

def get_character_account(self, character: str) -> dict | None:
    """Return {id, name} for the character's account, or None."""
    row = self._con.execute(
        "SELECT a.id, a.name FROM accounts a "
        "JOIN character_accounts ca ON ca.account_id=a.id "
        "WHERE ca.character=?",
        (character,),
    ).fetchone()
    return {"id": row["id"], "name": row["name"]} if row else None
```

**Step 3: Update `load_latest_snapshots()` to post-process warehouse dedup**

Replace the final `return result` block with:

```python
        # Load account assignments
        acct_rows = self._con.execute(
            "SELECT ca.character, a.name FROM character_accounts ca "
            "JOIN accounts a ON a.id=ca.account_id"
        ).fetchall()
        char_to_account: dict[str, str] = {r[0]: r[1] for r in acct_rows}

        result = []
        for snap in snapshot_rows:
            items = json.loads(snap["items"])
            acct = char_to_account.get(snap["character"])
            for item in items:
                result.append({
                    "character": snap["character"],
                    "source": snap["source"],
                    "scanned_at": snap["scanned_at"],
                    "item_id": item["item_id"],
                    "qty": item["qty"],
                    "name": name_map.get(item["item_id"], "???"),
                    "account": acct,
                })

        # Dedup warehouse rows: same account → keep only newest scanned_at
        seen_warehouse_accounts: dict[str, str] = {}  # account -> winning character
        warehouse_winners: set[str] = set()
        for r in result:
            if r["source"] != "warehouse" or r["account"] is None:
                continue
            acct = r["account"]
            if acct not in seen_warehouse_accounts:
                seen_warehouse_accounts[acct] = r["character"]
                warehouse_winners.add(r["character"])

        filtered = []
        for r in result:
            if r["source"] == "warehouse" and r["account"] is not None:
                if r["character"] not in warehouse_winners:
                    continue
            filtered.append(r)

        return filtered
```

**Step 4: Manual smoke test**

Open a Python shell:
```python
from gui.snapshot_db import SnapshotDB
db = SnapshotDB("test_tmp.db")
aid = db.create_account("TestAccount")
db.set_character_account("Alice", aid)
print(db.get_character_account("Alice"))   # {'id': 1, 'name': 'TestAccount'}
print(db.list_accounts())                  # [{'id': 1, 'name': 'TestAccount'}]
db.save_snapshot("Alice", "inventory", [{"item_id": 1, "qty": 2}])
snaps = db.list_all_snapshots("Alice")
print(snaps)   # one row, item_count=1
db.delete_snapshot(snaps[0]["id"])
print(db.list_all_snapshots("Alice"))  # []
db.delete_character("Alice")
print(db.get_character_account("Alice"))  # None
import os; os.remove("test_tmp.db")
```

**Step 5: Commit**

```bash
git add gui/snapshot_db.py
git commit -m "feat: extend SnapshotDB with account tables and deletion methods"
```

---

## Task 2: Add i18n strings for management panel

**Files:**
- Modify: `gui/i18n.py`

**Step 1: Add strings**

In `_STRINGS`, after the `"footer_items"` entry, add:

```python
    # ── Snapshot management panel ─────────────────────────────────────────
    "manage": "管理",
    "close_panel": "關閉",
    "account_label": "帳號歸屬",
    "no_account": "未設定",
    "create_account": "+ 建立新帳號",
    "snapshot_history": "快照歷史",
    "delete_snapshot": "刪除",
    "delete_character": "刪除此角色所有記錄",
    "confirm_delete_snapshot": "確定刪除此快照？此操作無法還原。",
    "confirm_delete_character": "確定刪除「{character}」的所有快照與帳號設定？此操作無法還原。",
    "deleted_snapshot": "已刪除快照",
    "deleted_character": "已刪除角色「{character}」的所有記錄",
    "account_created": "已建立帳號「{name}」",
    "account_assigned": "已將角色歸入帳號「{name}」",
    "enter_account_name": "輸入帳號名稱",
    "new_account_placeholder": "帳號名稱",
    "snap_row": "{source}  {scanned_at}  {item_count} 項",
    "warehouse_merged_from": "倉庫（由 {character} 於 {scanned_at} 上傳）",
```

**Step 2: Commit**

```bash
git add gui/i18n.py
git commit -m "feat: add i18n strings for snapshot management panel"
```

---

## Task 3: Add theme styles for character cards and management panel

**Files:**
- Modify: `gui/theme.py`

**Step 1: Add QSS at the bottom of `DARK_QSS`**

Before the closing `"""`, add:

```python
/* ── Character card (By Char view) ───────────────────────────── */
QFrame#char_card {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QFrame#char_card_header {{
    background-color: {BG_CARD};
    border-radius: 8px 8px 0 0;
}}

/* ── Management panel (inline, inside char_card) ─────────────── */
QFrame#mgmt_panel {{
    background-color: {BG_SURFACE};
    border-top: 1px solid {BORDER};
}}

/* ── Delete button (red accent) ──────────────────────────────── */
QPushButton#delete_btn {{
    background-color: transparent;
    color: {RED};
    border: 1px solid {RED};
    border-radius: 4px;
    padding: 2px 10px;
    font-size: 9pt;
    min-height: 24px;
}}
QPushButton#delete_btn:hover {{
    background-color: rgba(239,68,68,0.15);
    border-color: #DC2626;
    color: #DC2626;
}}
QPushButton#delete_btn:pressed {{
    background-color: rgba(239,68,68,0.30);
}}

/* ── Manage toggle button ─────────────────────────────────────── */
QPushButton#manage_btn {{
    background-color: transparent;
    color: {DIM};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 2px 10px;
    font-size: 9pt;
    min-height: 24px;
}}
QPushButton#manage_btn:hover {{
    color: {TEXT};
    border-color: {DIM};
}}
QPushButton#manage_btn:checked {{
    color: {GREEN};
    border-color: {GREEN};
    background-color: rgba(34,197,94,0.10);
}}
```

**Step 2: Commit**

```bash
git add gui/theme.py
git commit -m "feat: add QSS styles for character cards and management panel"
```

---

## Task 4: Create `CharacterCard` widget

**Files:**
- Create: `gui/character_card.py`

**Overview:** A `QFrame` that shows one character's items plus an inline collapsible management panel.

**Step 1: Write the widget**

```python
"""
CharacterCard — one card per character in the By Char view.

Shows:
  • Header row: character name, account badge, latest snapshot time, [manage] toggle
  • Item table: flat list of items (QTableWidget, read-only)
  • Management panel (collapsible): account assignment + snapshot history + delete
"""
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QComboBox, QMessageBox, QInputDialog, QWidget,
)
from PySide6.QtCore import Qt, Signal, QPropertyAnimation, QEasingCurve

from gui.snapshot_db import SnapshotDB
from gui.theme import DIM, TEXT, MUTED, BG_SURFACE, RED
from gui.i18n import t


class CharacterCard(QFrame):
    """Card widget for a single character in the By Char view."""

    # Emitted when any data-modifying action completes (delete, account change)
    data_changed = Signal()

    def __init__(self, character: str, rows: list[dict], db: SnapshotDB, parent=None):
        super().__init__(parent)
        self.setObjectName("char_card")
        self._character = character
        self._rows = rows
        self._db = db
        self._panel_open = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header ────────────────────────────────────────────────────
        header = QFrame()
        header.setObjectName("char_card_header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 8, 8, 8)
        header_layout.setSpacing(8)

        self._name_lbl = QLabel(character)
        self._name_lbl.setStyleSheet(f"color: {TEXT}; font-weight: 700;")
        header_layout.addWidget(self._name_lbl)

        self._acct_lbl = QLabel()
        self._acct_lbl.setStyleSheet(f"color: {MUTED}; font-size: 9pt;")
        header_layout.addWidget(self._acct_lbl)

        self._time_lbl = QLabel()
        self._time_lbl.setStyleSheet(f"color: {DIM}; font-size: 9pt;")
        header_layout.addWidget(self._time_lbl)

        header_layout.addStretch()

        self._manage_btn = QPushButton(t("manage"))
        self._manage_btn.setObjectName("manage_btn")
        self._manage_btn.setCheckable(True)
        self._manage_btn.setFixedHeight(26)
        self._manage_btn.clicked.connect(self._toggle_panel)
        header_layout.addWidget(self._manage_btn)

        layout.addWidget(header)

        # ── Item table ─────────────────────────────────────────────────
        COLUMNS = [t("mgr_col_item_id"), t("mgr_col_name"), t("mgr_col_qty"), t("mgr_col_source")]
        self._table = QTableWidget(0, len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setSortingEnabled(True)
        layout.addWidget(self._table)

        # ── Management panel (starts hidden) ───────────────────────────
        self._panel = QFrame()
        self._panel.setObjectName("mgmt_panel")
        self._panel.setMaximumHeight(0)
        self._panel.setMinimumHeight(0)
        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(12, 10, 12, 10)
        panel_layout.setSpacing(8)

        # Account row
        acct_row = QHBoxLayout()
        acct_row.addWidget(QLabel(t("account_label")))
        self._acct_combo = QComboBox()
        self._acct_combo.setMinimumWidth(140)
        acct_row.addWidget(self._acct_combo)
        create_btn = QPushButton(t("create_account"))
        create_btn.clicked.connect(self._on_create_account)
        acct_row.addWidget(create_btn)
        acct_row.addStretch()
        panel_layout.addLayout(acct_row)

        # Snapshot history label
        panel_layout.addWidget(QLabel(t("snapshot_history")))

        # Snapshot history table
        SNAP_COLS = [t("mgr_col_source"), t("mgr_col_snapshot_time"), "項數", ""]
        self._snap_table = QTableWidget(0, len(SNAP_COLS))
        self._snap_table.setHorizontalHeaderLabels(SNAP_COLS)
        self._snap_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._snap_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._snap_table.verticalHeader().setVisible(False)
        self._snap_table.setMaximumHeight(160)
        panel_layout.addWidget(self._snap_table)

        # Delete all button
        del_all_btn = QPushButton(t("delete_character"))
        del_all_btn.setObjectName("delete_btn")
        del_all_btn.clicked.connect(self._on_delete_character)
        panel_layout.addWidget(del_all_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        layout.addWidget(self._panel)

        # Animate panel expand/collapse
        self._anim = QPropertyAnimation(self._panel, b"maximumHeight")
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutSine)

        self._populate(rows)

    # ──────────────────────────────────────────────────────────────────
    def update_rows(self, rows: list[dict]):
        """Refresh item table with new rows (same character)."""
        self._rows = rows
        self._populate(rows)

    def _populate(self, rows: list[dict]):
        # Update header labels
        acct_info = self._db.get_character_account(self._character)
        acct_name = acct_info["name"] if acct_info else t("no_account")
        self._acct_lbl.setText(f"· {acct_name}")
        latest_time = max((r["scanned_at"] for r in rows), default="")
        self._time_lbl.setText(f"· {latest_time[:16]}" if latest_time else "")

        # Item table
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self._table.setItem(i, 0, QTableWidgetItem(str(r["item_id"])))
            self._table.setItem(i, 1, QTableWidgetItem(r["name"]))
            self._table.setItem(i, 2, QTableWidgetItem(str(r["qty"])))
            self._table.setItem(i, 3, QTableWidgetItem(r["source"]))
        self._table.setSortingEnabled(True)

        # Resize table to content (no scroll, expand to fit)
        row_h = self._table.rowHeight(0) if rows else 28
        header_h = self._table.horizontalHeader().height()
        self._table.setFixedHeight(header_h + row_h * len(rows) + 2)

    def _toggle_panel(self):
        self._panel_open = self._manage_btn.isChecked()
        if self._panel_open:
            self._refresh_panel()
            target = self._panel.sizeHint().height()
            self._anim.setStartValue(0)
            self._anim.setEndValue(max(target, 220))
        else:
            self._anim.setStartValue(self._panel.maximumHeight())
            self._anim.setEndValue(0)
        self._anim.start()

    def _refresh_panel(self):
        """Reload account combo and snapshot history."""
        accounts = self._db.list_accounts()
        current_acct = self._db.get_character_account(self._character)

        self._acct_combo.blockSignals(True)
        self._acct_combo.clear()
        self._acct_combo.addItem(t("no_account"), userData=None)
        for a in accounts:
            self._acct_combo.addItem(a["name"], userData=a["id"])
        if current_acct:
            idx = self._acct_combo.findData(current_acct["id"])
            if idx >= 0:
                self._acct_combo.setCurrentIndex(idx)
        self._acct_combo.blockSignals(False)
        self._acct_combo.currentIndexChanged.connect(self._on_account_changed)

        # Snapshot history
        snaps = self._db.list_all_snapshots(self._character)
        self._snap_table.setRowCount(len(snaps))
        for i, s in enumerate(snaps):
            self._snap_table.setItem(i, 0, QTableWidgetItem(s["source"]))
            self._snap_table.setItem(i, 1, QTableWidgetItem(s["scanned_at"]))
            self._snap_table.setItem(i, 2, QTableWidgetItem(str(s["item_count"])))
            del_btn = QPushButton(t("delete_snapshot"))
            del_btn.setObjectName("delete_btn")
            del_btn.clicked.connect(lambda checked, sid=s["id"]: self._on_delete_snapshot(sid))
            self._snap_table.setCellWidget(i, 3, del_btn)

    def _on_account_changed(self, index: int):
        account_id = self._acct_combo.itemData(index)
        if account_id is None:
            return
        self._db.set_character_account(self._character, account_id)
        name = self._acct_combo.itemText(index)
        self._acct_lbl.setText(f"· {name}")
        self.data_changed.emit()

    def _on_create_account(self):
        name, ok = QInputDialog.getText(
            self, t("create_account"), t("enter_account_name"),
            placeholderText=t("new_account_placeholder"),
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        account_id = self._db.create_account(name)
        self._db.set_character_account(self._character, account_id)
        self._refresh_panel()
        self._acct_lbl.setText(f"· {name}")
        self.data_changed.emit()

    def _on_delete_snapshot(self, snapshot_id: int):
        reply = QMessageBox.warning(
            self, t("delete_snapshot"),
            t("confirm_delete_snapshot"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._db.delete_snapshot(snapshot_id)
        self._refresh_panel()
        self.data_changed.emit()

    def _on_delete_character(self):
        reply = QMessageBox.warning(
            self, t("delete_character"),
            t("confirm_delete_character", character=self._character),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._db.delete_character(self._character)
        self.data_changed.emit()
```

**Step 2: Commit**

```bash
git add gui/character_card.py
git commit -m "feat: add CharacterCard widget with inline management panel"
```

---

## Task 5: Refactor InventoryManagerTab — replace By Char flat table with card list

**Files:**
- Modify: `gui/inventory_manager_tab.py`

**Overview:**
- Remove the `QTableWidget` (index 0 of the stack).
- Replace it with a `QScrollArea` + `QWidget` container that holds `CharacterCard` instances.
- Wire `data_changed` signals from each card to `refresh()`.

**Step 1: Update imports at the top of the file**

Add to imports:
```python
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QComboBox,
    QTreeWidget, QTreeWidgetItem, QPushButton, QStackedWidget,
    QButtonGroup, QScrollArea,
)
```
Remove: `QTableWidget, QTableWidgetItem, QHeaderView` (no longer needed for By Char view).
Add new import:
```python
from gui.character_card import CharacterCard
```

**Step 2: Replace the By Char stack widget (index 0)**

In `__init__`, replace the `QTableWidget` block (index 0 of `self._stack`) with:

```python
# Index 0: By Char — scrollable card list
self._char_scroll = QScrollArea()
self._char_scroll.setWidgetResizable(True)
self._char_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
self._char_container = QWidget()
self._char_layout = QVBoxLayout(self._char_container)
self._char_layout.setContentsMargins(0, 0, 0, 0)
self._char_layout.setSpacing(8)
self._char_layout.addStretch()
self._char_scroll.setWidget(self._char_container)
self._stack.addWidget(self._char_scroll)
self._cards: dict[str, CharacterCard] = {}  # character -> card
```

**Step 3: Replace `_populate_table()` with `_populate_cards()`**

Remove the `_populate_table()` method entirely. Add:

```python
def _populate_cards(self):
    """Rebuild character cards from filtered rows."""
    name_filter = self._search.text().strip().lower()
    char_filter = self._char_combo.currentText()
    source_filter = self._source_combo.currentText()

    _all_chars = t("all_characters")
    _all_sources = t("all_sources")
    _src_map = {t("source_inventory"): "inventory", t("source_warehouse"): "warehouse"}

    # Group filtered rows by character
    grouped: dict[str, list[dict]] = {}
    for r in self._all_rows:
        if name_filter and name_filter not in r["name"].lower():
            continue
        if char_filter != _all_chars and r["character"] != char_filter:
            continue
        if source_filter != _all_sources and r["source"] != _src_map.get(source_filter, source_filter):
            continue
        grouped.setdefault(r["character"], []).append(r)

    # Remove cards for characters no longer in filtered set
    for char in list(self._cards.keys()):
        if char not in grouped:
            card = self._cards.pop(char)
            self._char_layout.removeWidget(card)
            card.deleteLater()

    # Add/update cards
    for char in sorted(grouped.keys()):
        rows = grouped[char]
        if char in self._cards:
            self._cards[char].update_rows(rows)
        else:
            card = CharacterCard(char, rows, self._db)
            card.data_changed.connect(self.refresh)
            # Insert before the trailing stretch
            insert_pos = self._char_layout.count() - 1
            self._char_layout.insertWidget(insert_pos, card)
            self._cards[char] = card

    total = sum(len(v) for v in grouped.values())
    self._footer.setText(t("footer_items", n=total))
```

**Step 4: Update `_apply_filter()` to call `_populate_cards()`**

```python
def _apply_filter(self):
    if self._mode == _MODE_BY_ITEM:
        self._populate_tree()
    else:
        self._populate_cards()
```

**Step 5: Remove CHAR_COLUMNS class attribute and `_NumericItem` class**

They are no longer used.

**Step 6: Commit**

```bash
git add gui/inventory_manager_tab.py
git commit -m "refactor: replace By Char flat table with CharacterCard list in InventoryManagerTab"
```

---

## Task 6: Manual end-to-end verification

**Step 1: Launch the app**
```bash
uv run python -m gui.app
```
(or however the app is launched — check `pyproject.toml` for the entry point)

**Step 2: Verify By Char view**
- Switch to 道具總管 → By Char view.
- Confirm character cards appear, each with a [管理] button.
- Confirm item rows are listed inside each card.

**Step 3: Verify management panel**
- Click [管理] on a card → panel animates open.
- Confirm account combo shows "未設定" if no account assigned.
- Click [+ 建立新帳號], enter a name → confirm account is created and label updates.
- Re-open panel → account name appears in combo.

**Step 4: Verify snapshot deletion**
- In panel, click [刪除] on a snapshot → confirm dialog appears → confirm → row disappears.
- Click [刪除此角色所有記錄] → confirm → card disappears from view.

**Step 5: Verify warehouse dedup**
- Assign two characters to the same account.
- Ensure both have warehouse snapshots.
- Switch to By Item view → confirm warehouse items not double-counted.

**Step 6: Final commit if any fixes applied**
```bash
git add -p
git commit -m "fix: <describe what was fixed>"
```

---

## Task 7: Status bar feedback wiring

**Files:**
- Modify: `gui/character_card.py`
- Modify: `gui/inventory_manager_tab.py`

**Context:** After delete or account operations, a 2-second status bar message should appear. The status bar lives in `MainWindow`. The cleanest path is a signal chain: `CharacterCard` → `InventoryManagerTab` → `MainWindow`.

**Step 1: Add `status_message` signal to `CharacterCard`**

```python
status_message = Signal(str, int)  # message, timeout_ms
```

Emit it in each action method, e.g.:
```python
# After delete_snapshot:
self.status_message.emit(t("deleted_snapshot"), 2000)

# After delete_character:
self.status_message.emit(t("deleted_character", character=self._character), 2000)

# After account assigned:
self.status_message.emit(t("account_assigned", name=name), 2000)
```

**Step 2: Bubble signal through `InventoryManagerTab`**

Add to `InventoryManagerTab`:
```python
status_message = Signal(str, int)
```

In `_populate_cards()`, when connecting card signals:
```python
card.status_message.connect(self.status_message)
```

**Step 3: Connect in `MainWindow`**

In `MainWindow.__init__`, after creating `self._manager_tab`:
```python
self._manager_tab.status_message.connect(self._on_status_message)
```

**Step 4: Commit**

```bash
git add gui/character_card.py gui/inventory_manager_tab.py gui/main_window.py
git commit -m "feat: bubble status bar messages from CharacterCard through InventoryManagerTab"
```
