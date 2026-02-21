# Snapshot Management & Account Grouping Design

Date: 2026-02-21

## Overview

Two features added to the Item Overview tab:

1. **Snapshot deletion** — users can delete individual historical snapshots or all records for a character.
2. **Account grouping** — users manually group characters that share the same game account so that shared warehouse data is deduplicated in the overview.

---

## Database Schema Changes (`tthol_inventory.db`)

Two new tables alongside the existing `snapshots` table:

```sql
CREATE TABLE IF NOT EXISTS accounts (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS character_accounts (
    character  TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE SET NULL
);
```

### New `SnapshotDB` methods

| Method | Description |
|--------|-------------|
| `delete_snapshot(id: int)` | Delete a single snapshot row |
| `delete_character(character: str)` | Delete all snapshots + account assignment for a character |
| `list_snapshots(character: str, source: str) -> list[dict]` | Return all snapshots for a character+source (id, scanned_at, item count) |
| `list_all_snapshots(character: str) -> list[dict]` | Return all snapshots across all sources for a character |
| `list_accounts() -> list[dict]` | Return all accounts (id, name) |
| `create_account(name: str) -> int` | Create a new account, return its id |
| `set_character_account(character: str, account_id: int)` | Assign character to account |
| `get_character_account(character: str) -> dict \| None` | Get account info for a character |

### Updated `load_latest_snapshots()` logic

1. Fetch the latest snapshot per `(character, source)` — existing logic.
2. Load `character_accounts` mapping.
3. Post-process warehouse snapshots: group by `account_id`, keep only the row with the latest `scanned_at` per account. Characters without an account are unaffected.
4. Append `account` field (account name or `None`) to each result row.

By Item view aggregation respects the same dedup: warehouse items from the same account are counted only once (from the newest snapshot).

---

## UI Architecture

### By Char View Refactor

Replace the existing flat `QTableWidget` with a **character card list** (`QScrollArea` containing one `QFrame` per character). This allows inline panel expansion.

Each card has:
- **Header row**: character name · account name (or "未設定") · latest snapshot time · **[管理] button**
- **Item rows**: same flat list as before

### Management Panel (inline, animated)

Clicking [管理] toggles an inline panel that expands below the card header using `QPropertyAnimation` (150 ms, `InOutSine`). Clicking again or clicking [關閉] collapses it.

Panel contents:

```
帳號歸屬
  [ Account Name ▾ ]   [ + 建立新帳號 ]

快照歷史
  背包  2026-02-21 14:30  87 items   [Delete]
  背包  2026-02-20 09:00  85 items   [Delete]
  倉庫  2026-02-19 21:15  54 items   [Delete]

[ 刪除此角色所有記錄 ]
```

### Deletion UX

- Single snapshot delete → `QMessageBox` warning confirmation → call `delete_snapshot(id)` → refresh → status bar message for 2 s.
- Delete all records → `QMessageBox` warning confirmation → call `delete_character(character)` → refresh → status bar message for 2 s.
- Delete buttons use red accent (`#EF4444`), hover darkens.

### Account Creation UX

- [+ 建立新帳號] → `QInputDialog` for account name → `create_account(name)` → auto-assign character → refresh.

### Warehouse Merge Display

In By Char view, when an account's warehouse is shown (merged from multiple characters), the header reads:
`天龍八部（倉庫）  最新由 大剤小調 於 2026-02-21 14:30 上傳`

---

## Visual Style

Extends the existing dark theme:

| Element | Spec |
|---------|------|
| Management panel background | One shade deeper than card: `#0F172A` |
| [管理] button hover | 150 ms border brightness transition |
| Delete button | `#EF4444`, hover `#DC2626` |
| Snapshot history row height | 32 px, alternating row color |
| Panel expand animation | `QPropertyAnimation` 150 ms `InOutSine` |

---

## Out of Scope

- Automatic account detection by comparing warehouse checksums.
- Snapshot diff/comparison UI.
- Export or import of snapshot data.
