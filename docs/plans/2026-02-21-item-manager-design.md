# Item Manager Design — Tthol Reader

Date: 2026-02-21

## Overview

Add an **Item Overview Tab** to the existing PySide6 GUI. Users can see all
characters' latest inventory/warehouse snapshots in one searchable, filterable
table — without needing to open the game and hover over every item icon.

Snapshots are stored in a separate `tthol_inventory.db` SQLite database.
`tthol.sqlite` remains read-only (item name definitions only).

---

## Database Schema (`tthol_inventory.db`)

```sql
CREATE TABLE snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    character   TEXT NOT NULL,
    source      TEXT NOT NULL,       -- 'inventory' | 'warehouse'
    scanned_at  TEXT NOT NULL,       -- ISO8601, e.g. '2026-02-21T14:30:00'
    items       TEXT NOT NULL,       -- JSON: [{"item_id": 26968, "qty": 1}, ...]
    checksum    TEXT NOT NULL        -- SHA256 of canonical items JSON
);
```

**items** JSON is serialized with `item_id` sorted ascending before hashing,
ensuring identical contents always produce the same checksum.

**Dedup rule:** before writing, query the latest snapshot for the same
`character + source`. If `checksum` matches, skip saving and notify the user
("No change detected, skipped"). Otherwise write a new row (historical
snapshots are retained for future change analysis).

---

## Architecture

### New Files

```
gui/
├── inventory_manager_tab.py   # Item Overview Tab (QWidget)
└── snapshot_db.py             # All tthol_inventory.db read/write logic
```

### `snapshot_db.py`

- Auto-creates schema on first run (no manual migration needed).
- `save_snapshot(character: str, source: str, items: list[dict]) -> bool`
  Returns `True` if saved, `False` if deduped.
- `load_latest_snapshots() -> list[dict]`
  Returns the latest snapshot per `character+source`, with item names resolved
  from `tthol.sqlite` in Python (no cross-DB SQL JOIN).

### `inventory_manager_tab.py`

- `QTableWidget` with columns: Character / Item ID / Name / Qty / Source / Snapshot Time
- Sortable columns.
- Top toolbar: name search box + character dropdown + source dropdown.
  All filtering is done in-memory after loading; no extra DB queries on keypress.
- Exposes `refresh()` — reloads from DB and re-applies current filters.

### Modifications to Existing Tabs

**`inventory_tab.py`** and **`warehouse_tab.py`**:
- Add a **"存入快照" (Save Snapshot)** QPushButton next to the existing scan button.
- Button is disabled until a successful scan result is available in the current session.
- On click:
  1. Call `snapshot_db.save_snapshot(character, source, items)`.
  2. If saved → toast/status "Snapshot saved".
  3. If deduped → toast/status "No change, skipped".
  4. Call `inventory_manager_tab.refresh()`.

**`main_window.py`**:
- Add the new `InventoryManagerTab` as the fourth tab: 狀態 / 背包 / 倉庫 / **道具總覽**.
- Pass a reference to `InventoryManagerTab` into `InventoryTab` and `WarehouseTab`
  so they can call `refresh()` after saving.

---

## UI Layout

### Tab Bar

```
┌──────┬──────┬──────┬──────────┐
│ 狀態 │ 背包 │ 倉庫 │ 道具總覽 │
└──────┴──────┴──────┴──────────┘
```

### 道具總覽 Tab

```
[ Search item name... ]  [ Character: All ∨ ]  [ Source: All ∨ ]

 Character    ID     Name        Qty   Source   Snapshot Time
 ────────────────────────────────────────────────────────────
 大剤小調    26968  古劍          1    背包     02-21 14:30
 大剤小調    55220  回血丹       10    背包     02-21 14:30
 二大剤      33120  絕世奇功      1    倉庫     02-20 09:15
 ...
 87 items total
```

### Scan Tab Modification

```
[ 掃描背包 ]  [ 存入快照 ]   Last updated: 14:32:05
```

---

## Out of Scope

- Master/overview dashboard page (deferred, concept not yet defined).
- Automatic scanning on tab switch.
- Item comparison / diff view between snapshots (foundation is laid via
  checksum history, but UI for this is not in scope).
