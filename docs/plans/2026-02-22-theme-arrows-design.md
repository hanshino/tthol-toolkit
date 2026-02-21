# Design: Light/Dark Theme Toggle + Arrow Visibility (Issues #4, #5)

**Date:** 2026-02-22
**Issues:** #4 (arrow icon visibility), #5 (light theme)
**Root cause:** Issue #4 reports are caused by #5 — arrows exist but lack contrast on dark theme on some monitors.

---

## Goals

1. Add a light/dark theme toggle (issue #5) — Slate-50 base, retain Green/Blue/Amber accents.
2. Improve filter-toggle arrow visibility (issue #4) — resolved by proper per-theme contrast.
3. Persist theme preference across restarts via `config.json`.

---

## Architecture

### Files Changed

| File | Change |
|------|--------|
| `gui/theme.py` | Define `DARK_PALETTE` / `LIGHT_PALETTE` dicts; generate QSS from palette; add `ThemeManager` singleton |
| `gui/config.py` | New — read/write `config.json` (theme preference, future settings) |
| `gui/main_window.py` | Remove `_CLOSE_BTN_STYLE` / `_REFRESH_BTN_STYLE` module constants; add theme toggle button to nav sidebar bottom |
| `gui/character_panel.py` | Remove `hp_lbl` / `mp_lbl` inline `setStyleSheet`; use object names instead |
| `gui_main.py` | Load initial mode from config; call `ThemeManager.apply(app, mode)` |

**Not changed:** `status_tab.py`, `inventory_tab.py`, `warehouse_tab.py`, `data_management_tab.py`, `i18n.py`

---

## Palette Definitions

### Dark (existing)

```
BG_BASE    = #020617  (slate-950)
BG_SURFACE = #0F172A  (slate-900)
BG_CARD    = #1E293B  (slate-800)
BORDER     = #334155  (slate-700)
MUTED      = #475569  (slate-600)
DIM        = #94A3B8  (slate-400)
TEXT       = #F8FAFC  (slate-50)
```

### Light (new)

```
BG_BASE    = #F8FAFC  (slate-50)
BG_SURFACE = #F1F5F9  (slate-100)
BG_CARD    = #E2E8F0  (slate-200)
BORDER     = #CBD5E1  (slate-300)
MUTED      = #64748B  (slate-500)   contrast ~4.7:1 on BG_BASE ✓
DIM        = #475569  (slate-600)   contrast ~7:1   on BG_BASE ✓
TEXT       = #0F172A  (slate-900)   contrast ~19:1  on BG_BASE ✓
```

Accent colors unchanged in both modes: `GREEN=#22C55E`, `BLUE=#3B82F6`, `AMBER=#F59E0B`, `RED=#EF4444`, `ORANGE=#F97316`.

State badge backgrounds and borders get light-mode variants in `_STATE_BADGE_LIGHT`.

---

## ThemeManager Design

```python
class ThemeManager:
    _mode: str = "dark"           # "dark" | "light"
    _palette: dict = DARK_PALETTE
    _app = None

    @classmethod
    def apply(cls, app, mode: str) -> None:
        """Apply theme to app, update internal palette, persist to config."""

    @classmethod
    def toggle(cls) -> None:
        """Switch between dark and light."""

    @classmethod
    def c(cls, key: str) -> str:
        """Return current palette color. e.g. ThemeManager.c("GREEN")"""

    @classmethod
    def mode(cls) -> str:
        """Return current mode string."""
```

`badge_style()`, `vital_html()`, `fraction_html()` use `ThemeManager.c(...)` instead of module-level constants.

---

## Static Inline Styles → QSS Object Names

| Widget | Old approach | New approach |
|--------|-------------|--------------|
| Close tab button | `_CLOSE_BTN_STYLE` module constant | `QPushButton#close_btn { ... }` in QSS |
| Refresh/add button | `_REFRESH_BTN_STYLE` module constant | `QPushButton#refresh_btn { ... }` in QSS |
| HP label (green) | `setStyleSheet(f"color: {GREEN};...")` | `QLabel#vital_hp_label { color: ... }` |
| MP label (blue) | `setStyleSheet(f"color: {BLUE};...")` | `QLabel#vital_mp_label { color: ... }` |
| Version label | `setStyleSheet(f"color: {MUTED};...")` | `QLabel#version_label { ... }` |
| Placeholder label | `setStyleSheet(f"color: {DIM};...")` | `QLabel#placeholder_lbl { ... }` |
| Filter toggle btn | no dedicated style | `QPushButton#filter_toggle_btn { ... }` |
| HP flash (red border) | `setStyleSheet("border: 1px solid #EF4444;")` | **Keep as-is** — intentional fixed red |

---

## Issue #4: Arrow Visibility

`▼ 篩選` / `▲ 篩選` already exist in `i18n.py`. No i18n change needed.

Fix: Give `_advanced_btn` the object name `filter_toggle_btn` and define its QSS color explicitly in both palettes, ensuring sufficient contrast in dark mode (use `TEXT` instead of `DIM`) and clear visibility in light mode.

---

## Theme Toggle Button

- **Location:** Nav sidebar, below `addStretch()`
- **Label:** `"◑ 亮色"` (when dark) / `"◑ 暗色"` (when light) — Unicode half-circle, not emoji
- **Object name:** `nav_btn` (inherits sidebar button style) + `theme_toggle_btn` for additional overrides
- **Behavior:** `ThemeManager.toggle()` → `config.json` updated → `app.setStyleSheet(new_qss)`

---

## config.json Schema

```json
{
  "theme": "dark"
}
```

Path: project root `config.json`. Read at startup; written on every theme change.

---

## Contrast Audit (ui-ux-pro-max)

| Color | On BG_BASE (light) | Ratio | Pass 4.5:1? |
|-------|-------------------|-------|-------------|
| TEXT #0F172A | #F8FAFC | ~19:1 | ✓ |
| DIM #475569 | #F8FAFC | ~7:1 | ✓ |
| MUTED #64748B | #F8FAFC | ~4.7:1 | ✓ |
| GREEN #22C55E | #F8FAFC | ~2.9:1 | — (accent only, not body text) |

---

## Out of Scope

- Transition animation between themes (PySide6 QSS does not support CSS transitions)
- Per-widget theme override
- System theme detection (follow OS dark/light mode)
