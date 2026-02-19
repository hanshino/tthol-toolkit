# PySide6 GUI Design — Tthol Reader

Date: 2026-02-19

## Overview

A standalone PySide6 window for reading Tthol game memory. Displays character
stats, inventory, and warehouse contents. Read-only — never modifies game memory.

Phase 1 scope: manual HP input to connect. OCR auto-detection is deferred to a
later phase.

---

## Architecture

### File Structure

```
gui/
├── main_window.py      # QMainWindow: top bar + vitals strip + TabWidget
├── status_tab.py       # Character stats tab (HP/MP bars, attribute groups)
├── inventory_tab.py    # Inventory tab (table view + scan button)
├── warehouse_tab.py    # Warehouse tab (table view + scan button)
└── worker.py           # QThread background worker (scan / poll)
```

`reader.py` is imported as-is. No logic is duplicated in the GUI layer.

### Data Flow

```
[Connect button] → Worker Thread
                     └── locate_character()    ~0.4s, non-blocking
                     └── on success: read_all_fields() every 1s
                     └── emit signal → UI update

[Inventory tab]  → [Scan] button → Worker runs locate_inventory()
[Warehouse tab]  → [Scan] button → Worker runs warehouse scan
```

All blocking operations run in `QThread`. The main thread only renders.

---

## State Machine

```
DISCONNECTED → CONNECTING → LOCATED → READ_ERROR → RESCANNING
     ↑              │            │                       │
     └──────────────┴────────────┴───────────────────────┘
                (process lost at any point)
```

| State | Meaning | UI |
|---|---|---|
| `DISCONNECTED` | tthola.dat process not found | "未連線", button enabled |
| `CONNECTING` | Process found, scanning (~0.4s) | "定位中..." |
| `LOCATED` | Valid address, reading every 1s | Normal display |
| `READ_ERROR` | Read failed or validation failed | Triggers rescan |
| `RESCANNING` | Re-running locate_character | "重新定位..." |

### Rescan Triggers (only these three)

1. `read_int()` raises an exception (process exited or memory freed)
2. `verify_structure()` fails (struct was reallocated, address is stale)
3. User clicks "重新定位" manually

### Situations That Do NOT Trigger Rescan

- Character dies (HP → 0): value changes in-place, address stays valid
- Map change: coordinates update in-place
- Inventory / warehouse scan failure: isolated to that tab only

### Validation Thresholds

| Field | Valid Range | Invalid If |
|---|---|---|
| HP | `1 ≤ hp ≤ hp_max ≤ 999999` | hp > hp_max or hp_max = 0 |
| MP | `0 ≤ mp ≤ mp_max ≤ 999999` | mp_max = 0 |
| Level | `1 ≤ lv ≤ 200` | lv = 0 or lv > 200 |
| Coords | `-1 ≤ x, y ≤ 10000` | out of range |

3 consecutive failures required before triggering rescan (avoids false positives
from transient read noise).

---

## UI Layout

### Main Window

```
┌─────────────────────────────────────────────────┐
│  Tthol Reader                                   │
├─────────────────────────────────────────────────┤
│  HP: [________]  [連線]  ● LOCATED  [重新定位]  │
├─────────────────────────────────────────────────┤
│  Lv.192   HP 46277 / 46277   MP 5070 / 5070     │
│  Weight 31842 / 82498        Pos (54, 127)       │
├──────────┬──────────┬───────────────────────────┤
│   狀態   │   背包   │   倉庫                    │
├──────────┴──────────┴───────────────────────────┤
│  (tab content)                                  │
└─────────────────────────────────────────────────┘
```

Three rows above the tabs:
1. **Operation row** — HP input, connect button, status indicator, relocate button
2. **Vitals strip** — refreshes every 1s, always visible regardless of active tab
3. **Tab bar** — 狀態 / 背包 / 倉庫

### Status Tab

```
  ┌─ Basic ──────────────────────────────────────┐
  │  HP    46277 / 46277  [████████████████░░░]  │
  │  MP     5070 /  5070  [████████░░░░░░░░░░░]  │
  │  Weight 31842 / 82498 [████████░░░░░░░░░░░]  │
  └──────────────────────────────────────────────┘
  ┌─ Attributes ───┐  ┌─ Combat ─────────────────┐
  │  外功     190  │  │  物攻  2204  防禦  1050  │
  │  根骨      61  │  │  內勁   253  護勁   548  │
  │  技巧      85  │  │  命中   656  閃躲   309  │
  │  魅力   30454  │  └──────────────────────────┘
  └────────────────┘
```

HP / MP / Weight show progress bars. Other stats are plain numbers.

### Inventory Tab

```
  [掃描背包]  Last updated: 14:32:05

  #    Item ID    Qty    Name
  ──────────────────────────────
  1      26968      1    古劍
  2      55220     10    回血丹
  ...
  Total: 42 items
```

### Warehouse Tab

```
  [掃描倉庫]  ⚠ Open warehouse UI in-game first

  #    Item ID    Qty    Name
  ──────────────────────────────
  (results appear after scan)
```

---

## Out of Scope (Phase 1)

- OCR auto-detection of HP value
- Any write operations to game memory
- Battle state / combat monitoring
