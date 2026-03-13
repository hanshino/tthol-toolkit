# Auto-Click Hero Summoning Feature

## Overview

Automated clicking feature for the hero summoning shop in Tthol game. Players select a merchant, choose a collection mode, and the system repeatedly clicks to summon heroes, then handles the results via "Collect All" / "Destroy All" buttons.

## Architecture

### Placement

New `AutoClickTab` as a 4th inner tab in `CharacterPanel`, alongside StatusTab, InventoryTab, WarehouseTab. Receives `hwnd` from CharacterPanel for background clicking.

### Click Mechanism

Background clicks via `PostMessageW` (`WM_LBUTTONDOWN` / `WM_LBUTTONUP`) to the game window. Coordinates use 800x600 client area (the game's native resolution; DPI scaling only affects screenshot capture, not PostMessage).

**Important:** A 50ms delay is required between WM_LBUTTONDOWN and WM_LBUTTONUP for the game to register the click. Additionally, UI buttons (Collect All, Destroy All) require **two consecutive click pairs** to trigger — merchant clicks only need one.

**hwnd guard:** `background_click()` must validate `hwnd != 0` before calling PostMessageW. The Start button is disabled when `hwnd == 0`.

### Timing

`QTimer` in main thread drives the click loop. `PostMessage` is non-blocking so no separate worker thread is needed.

### Lifecycle

AutoClickTab exposes a `shutdown()` method that stops the QTimer and resets state to IDLE. `CharacterPanel.shutdown()` must call it when the tab/panel is destroyed (e.g., user closes the character tab).

## Game Coordinates

Coordinates are in **800x600 client space** (game native resolution).

Screenshot coordinates (1200x900, DPI 150%) divide by 1.5 to get client coords.

| Element | 800x600 Coord | Click Type | Notes |
|---------|---------------|------------|-------|
| Merchant 1 | (167, 333) | Single | Leftmost |
| Merchant 2 | (283, 333) | Single | +117 offset |
| Merchant 3 | (400, 333) | Single | +117 offset |
| Merchant 4 | (517, 333) | Single | +117 offset |
| Merchant 5 | (633, 333) | Single | Rightmost |
| Collect All | (645, 177) | Double | Two click pairs needed |
| Destroy All | (645, 209) | Double | Two click pairs needed |

## State Machine

```
IDLE --> CLICKING_MERCHANT --> CLICKING_BUTTONS --> CLICKING_MERCHANT (loop)
                                                         |
                                                   (max rounds reached)
                                                         |
                                                        IDLE
```

### CLICKING_MERCHANT

- QTimer fires at user-configured interval (0.3~2.0s, default 0.5s)
- PostMessage click to selected merchant coordinate
- Count increments per tick
- Transitions to CLICKING_BUTTONS when count reaches clicks_per_round

### CLICKING_BUTTONS

Uses a step counter within the QTimer tick handler to sequence actions across multiple timer fires (no `time.sleep`):

**Mode A (Collect + Destroy):**
1. Step 0: click "Collect All" (928, 254)
2. Step 1: wait (timer fires, no action)
3. Step 2: click "Destroy All" (928, 306)
4. Step 3: wait (timer fires, no action) -> round complete

**Mode B (Destroy Only):**
1. Step 0: click "Destroy All" (928, 306)
2. Step 1: wait (timer fires, no action) -> round complete

Each step uses the same QTimer interval. After completing:
- Increment round counter
- If round < max_rounds (or max_rounds=0 for unlimited): transition to CLICKING_MERCHANT
- Else: transition to IDLE, emit status message to CharacterPanel status bar

### PostMessage Implementation

```python
import ctypes
user32 = ctypes.windll.user32

WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001

def background_click(hwnd, x, y):
    """Single click. 50ms delay between down/up required by this game."""
    if not hwnd:
        return
    lparam = (y << 16) | (x & 0xFFFF)
    user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
    time.sleep(0.05)
    user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lparam)

def background_double_click(hwnd, x, y):
    """Double click for UI buttons that need two click pairs to trigger."""
    background_click(hwnd, x, y)
    time.sleep(0.05)
    background_click(hwnd, x, y)
```

## GUI Layout

```
AutoClickTab (QWidget)
└── QVBoxLayout (margins=10, spacing=10)
    ├── Target (QGroupBox)
    │   └── QHBoxLayout
    │       └── 5x QPushButton (checkable, QButtonGroup exclusive)
    │           Selected = GREEN highlight (matches existing checked style)
    │
    ├── QHBoxLayout (side by side)
    │   ├── Mode (QGroupBox)
    │   │   └── QVBoxLayout
    │   │       ├── QRadioButton "Collect + Destroy"
    │   │       └── QRadioButton "Destroy Only"
    │   │
    │   └── Parameters (QGroupBox)
    │       └── QFormLayout
    │           ├── Interval: QSlider + QLabel "0.5s"
    │           ├── Clicks/Round: QSpinBox (1~50, default 15)
    │           └── Max Rounds: QSpinBox (0~9999, 0=unlimited)
    │
    ├── Controls (QHBoxLayout)
    │   ├── Start btn (GREEN)
    │   └── Stop btn (RED, disabled by default)
    │
    └── Status (QGroupBox, vitals_strip style)
        └── QHBoxLayout
            ├── Round badge + "0 / ∞"
            ├── | separator
            ├── Clicks badge + "0 / 15"
            ├── | separator
            └── Total badge + "0"
```

### UX Behavior

- Running state: Target + Parameters groups disabled (prevent accidental changes)
- Start button disabled when already running; Stop disabled when idle
- Status labels update in real-time
- All UI text in English (avoid cp950 encoding issues)
- Follows existing theme system (ThemeManager palette)

## Configuration

| Parameter | Type | Range | Default |
|-----------|------|-------|---------|
| Target Merchant | int | 1-5 | 1 |
| Mode | enum | collect_destroy, destroy_only | collect_destroy |
| Click Interval | float | 0.3-2.0s | 0.5s |
| Clicks per Round | int | 1-50 | 15 |
| Max Rounds | int | 0-9999 (0=unlimited) | 0 |

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `gui/auto_click_tab.py` | Create | AutoClickTab widget + click logic |
| `gui/character_panel.py` | Modify | Add AutoClickTab as 4th inner tab, call shutdown() |
| `gui/i18n.py` | Modify | Add translation strings (tab_auto_click, etc.) |
| `gui/theme.py` | Modify | Add QSS for QSlider, QRadioButton, QSpinBox |
