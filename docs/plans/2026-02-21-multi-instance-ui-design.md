# Multi-Instance UI Design — Tthol Reader

Date: 2026-02-21

## Overview

Support multiple simultaneously-running `tthola.dat` game windows. On startup the
tool auto-detects all running instances and creates one `CharacterPanel` tab per
window. Each tab connects independently using a manually-entered HP value (Phase 1).
A later phase will replace HP input with on-click OCR.

---

## Phased Rollout

| Phase | Scope |
|-------|-------|
| **1 (this doc)** | Auto-detect game windows → multi-tab architecture → manual HP input per tab |
| **2 (future)** | OCR replaces HP input: screenshot the specific window on button click → extract HP automatically |

Phase 2 security note: screenshot is taken **only** on explicit user click, never on a
timer, to avoid capturing login credentials.

---

## UI Layout

```
┌──────────────────────────────────────────────────────┐
│  Tthol Reader                                        │
├────────────┬────────────┬──────────────────────┬─────┤
│ 視窗 1     │ 小花  ●   │  視窗 3              │ [+] │
├────────────┴────────────┴──────────────────────┴─────┤
│  HP: [________]  [Connect]  ○ DISCONNECTED           │  ← op_bar
│  ─────────────────────────────────────────────────   │
│  LV: ---  HP: ---/---  MP: ---/---  POS: (---,---)   │  ← vitals strip
├──────────────┬─────────────┬────────────┬────────────┤
│   Status     │  Inventory  │  Warehouse │  道具總覽  │  ← inner tabs
├──────────────┴─────────────┴────────────┴────────────┤
│  (tab content)                                       │
└──────────────────────────────────────────────────────┘
```

### Outer Tab Labels

| Label | Meaning |
|-------|---------|
| `視窗 N` | Detected but not yet connected |
| `小花 ●` (green) | Connected — shows character name + LOCATED badge |
| `視窗 N ◐` (orange) | Connecting / Rescanning |
| `視窗 N ✕` (red) | READ_ERROR |
| `視窗 N ○` (gray) | Disconnected after prior connection |

### [+] Button

Placed after the last tab. Clicking re-runs `detect_game_windows()` and adds tabs
for any newly-started processes. Existing PID tabs are not duplicated.

### Phase 2 OCR Reservation

The op_bar will reserve space for an `[OCR]` button (hidden in Phase 1). In Phase 2
it becomes visible and clicking it screenshots the panel's specific window, OCR-reads
the HP value, and auto-fills the HP input field before connecting.

---

## Architecture

### File Changes

```
gui/
├── process_detector.py   ← NEW: enumerate tthola.dat PIDs + HWNDs
├── character_panel.py    ← NEW: per-character widget (extracted from main_window)
├── main_window.py        ← SIMPLIFIED: outer QTabWidget only
└── worker.py             ← MODIFIED: connect by PID instead of process name
```

### `process_detector.py`

```python
def detect_game_windows() -> list[tuple[int, int, str]]:
    """Return list of (pid, hwnd, label) for all tthola.dat processes."""
```

Uses `psutil` to enumerate PIDs and `win32gui.EnumWindows` to find the corresponding
HWND. Labels are "視窗 1", "視窗 2", etc. in discovery order.

### `character_panel.py`

Extracts everything currently inside `MainWindow.__init__` (op_bar + vitals strip +
inner `QTabWidget`) into a self-contained `QWidget`. Each panel owns:
- One `ReaderWorker` instance (bound to a specific PID)
- Its own `SnapshotDB` reference (shared from `MainWindow`)
- The `(pid, hwnd)` tuple for future OCR use

Emits a `tab_label_changed(str)` signal so `MainWindow` can update the outer tab text
when the worker transitions from `CONNECTING` → `LOCATED` (character name known).

### `main_window.py` (simplified)

```
MainWindow
├── outer_tabs: QTabWidget
│   ├── CharacterPanel(pid=1234, hwnd=0x1A2B)
│   ├── CharacterPanel(pid=5678, hwnd=0x3C4D)
│   └── ... (one per detected process)
└── [+] button (re-scan and add new panels)
```

Startup flow:
1. Call `detect_game_windows()`
2. For each result → create `CharacterPanel` + add to `outer_tabs`
3. If zero results → show placeholder tab: "請先開啟遊戲"

### `worker.py`

Add `pid: int` parameter to `ReaderWorker.__init__`. Replace:

```python
# Before
pymem.Pymem("tthola.dat")   # connects to first match

# After
pymem.Pymem(process_id=self._pid)   # connects to specific instance
```

---

## Design System (ui-ux-pro-max)

- **Style**: Dark Mode (OLED) — already implemented
- **Colors**: `#020617` bg, `#1E293B` surface, `#22C55E` LOCATED, `#F8FAFC` text
- **Typography**: Fira Code / Fira Sans (technical, data-precise)
- **UX rules applied**:
  - Scanning >300ms → show spinner in op_bar
  - Tab badge communicates state without opening the tab
  - [+] button hover feedback (150–300ms transition)
  - OCR button reserved but hidden — no layout shift when it appears in Phase 2

---

## Out of Scope (Phase 1)

- OCR screenshot and HP extraction
- Simultaneous side-by-side character comparison
- Drag-to-reorder tabs
