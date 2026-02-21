# Theme Toggle + Arrow Visibility Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add light/dark theme toggle to nav sidebar (issue #5) and fix arrow visibility in filter toggle (issue #4) by wiring all color references through a ThemeManager.

**Architecture:** Define `DARK_PALETTE`/`LIGHT_PALETTE` dicts in `theme.py`; generate QSS from the active palette; `ThemeManager` singleton applies theme and persists to `config.json`. Static inline Python styles are moved to QSS object-name rules so they update automatically on theme switch. Dynamic color functions (`badge_style`, `vital_html`, `fraction_html`) read from `ThemeManager.c()`.

**Tech Stack:** PySide6, pytest-qt, Python 3.12, uv

---

## Reference

Design doc: `docs/plans/2026-02-22-theme-arrows-design.md`

Run all tests: `uv run pytest`
Run GUI: `uv run gui_main.py`

---

### Task 1: config.py — read/write theme preference

**Files:**
- Create: `gui/config.py`
- Create: `tests/test_config.py`

**Step 1: Write failing tests**

Create `tests/test_config.py`:

```python
"""Tests for gui.config — theme preference persistence."""
import json
import pytest
from pathlib import Path
from gui.config import load_theme, save_theme


def test_load_theme_returns_dark_when_no_file(tmp_path):
    cfg = tmp_path / "config.json"
    assert load_theme(cfg) == "dark"


def test_load_theme_returns_saved_value(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"theme": "light"}), encoding="utf-8")
    assert load_theme(cfg) == "light"


def test_load_theme_returns_dark_on_corrupt_file(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text("not json", encoding="utf-8")
    assert load_theme(cfg) == "dark"


def test_save_theme_writes_json(tmp_path):
    cfg = tmp_path / "config.json"
    save_theme("light", cfg)
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["theme"] == "light"


def test_save_theme_overwrites_existing(tmp_path):
    cfg = tmp_path / "config.json"
    save_theme("light", cfg)
    save_theme("dark", cfg)
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["theme"] == "dark"
```

**Step 2: Run to verify failure**

```bash
uv run pytest tests/test_config.py -v
```
Expected: `ImportError: cannot import name 'load_theme' from 'gui.config'`

**Step 3: Implement `gui/config.py`**

```python
"""User preference persistence (config.json in project root)."""
import json
from pathlib import Path

_DEFAULT_PATH = Path(__file__).parent.parent / "config.json"


def load_theme(path: Path = _DEFAULT_PATH) -> str:
    """Return saved theme ('dark' or 'light'). Falls back to 'dark'."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("theme", "dark")
    except Exception:
        return "dark"


def save_theme(mode: str, path: Path = _DEFAULT_PATH) -> None:
    """Persist theme preference to config.json."""
    try:
        existing: dict = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        existing["theme"] = mode
        path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
```

**Step 4: Run to verify pass**

```bash
uv run pytest tests/test_config.py -v
```
Expected: 5 passed

**Step 5: Commit**

```bash
git add gui/config.py tests/test_config.py
git commit -m "feat: add config.py for theme preference persistence"
```

---

### Task 2: theme.py — palette dicts + ThemeManager + QSS generator

**Files:**
- Modify: `gui/theme.py` (full rewrite)
- Create: `tests/test_theme.py`

**Step 1: Write failing tests**

Create `tests/test_theme.py`:

```python
"""Tests for ThemeManager."""
import pytest
from unittest.mock import MagicMock, patch


def test_theme_manager_default_mode():
    from gui.theme import ThemeManager
    ThemeManager._mode = "dark"   # reset to known state
    assert ThemeManager.mode() == "dark"


def test_theme_manager_c_returns_dark_green():
    from gui.theme import ThemeManager
    ThemeManager._mode = "dark"
    ThemeManager._palette = __import__("gui.theme", fromlist=["DARK_PALETTE"]).DARK_PALETTE
    assert ThemeManager.c("GREEN") == "#22C55E"


def test_theme_manager_c_returns_light_bg():
    from gui.theme import ThemeManager, LIGHT_PALETTE
    ThemeManager._mode = "light"
    ThemeManager._palette = LIGHT_PALETTE
    assert ThemeManager.c("BG_BASE") == "#F8FAFC"


def test_theme_manager_c_dark_bg():
    from gui.theme import ThemeManager, DARK_PALETTE
    ThemeManager._mode = "dark"
    ThemeManager._palette = DARK_PALETTE
    assert ThemeManager.c("BG_BASE") == "#020617"


def test_badge_style_returns_string():
    from gui.theme import ThemeManager, DARK_PALETTE, badge_style
    ThemeManager._mode = "dark"
    ThemeManager._palette = DARK_PALETTE
    result = badge_style("LOCATED")
    assert "color" in result
    assert "#" in result


def test_vital_html_contains_key():
    from gui.theme import ThemeManager, DARK_PALETTE, vital_html
    ThemeManager._mode = "dark"
    ThemeManager._palette = DARK_PALETTE
    result = vital_html("HP", 100)
    assert "HP" in result


def test_theme_manager_toggle():
    from gui.theme import ThemeManager, DARK_PALETTE, LIGHT_PALETTE
    ThemeManager._mode = "dark"
    ThemeManager._palette = DARK_PALETTE
    ThemeManager._app = None
    with patch("gui.theme.save_theme"):
        ThemeManager.toggle()
    assert ThemeManager._mode == "light"
    assert ThemeManager._palette is LIGHT_PALETTE
```

**Step 2: Run to verify failure**

```bash
uv run pytest tests/test_theme.py -v
```
Expected: `ImportError` or `AttributeError` (ThemeManager, DARK_PALETTE not defined yet)

**Step 3: Rewrite `gui/theme.py`**

Replace the entire file content with:

```python
"""
Theme system for Tthol Reader.

Two palettes: DARK_PALETTE (slate-950 base) and LIGHT_PALETTE (slate-50 base).
QSS is generated from the active palette at runtime.
ThemeManager singleton applies theme and persists preference.

Shared accent colors (both modes):
    GREEN  = #22C55E
    BLUE   = #3B82F6
    AMBER  = #F59E0B
    RED    = #EF4444
    ORANGE = #F97316
"""

from gui.config import save_theme

# ── Shared accents ────────────────────────────────────────────────────────────
GREEN  = "#22C55E"
BLUE   = "#3B82F6"
AMBER  = "#F59E0B"
RED    = "#EF4444"
ORANGE = "#F97316"

# ── Palettes ──────────────────────────────────────────────────────────────────
DARK_PALETTE: dict[str, str] = {
    "BG_BASE":    "#020617",
    "BG_SURFACE": "#0F172A",
    "BG_CARD":    "#1E293B",
    "BORDER":     "#334155",
    "MUTED":      "#475569",
    "DIM":        "#94A3B8",
    "TEXT":       "#F8FAFC",
    "GREEN":      GREEN,
    "BLUE":       BLUE,
    "AMBER":      AMBER,
    "RED":        RED,
    "ORANGE":     ORANGE,
}

LIGHT_PALETTE: dict[str, str] = {
    "BG_BASE":    "#F8FAFC",
    "BG_SURFACE": "#F1F5F9",
    "BG_CARD":    "#E2E8F0",
    "BORDER":     "#CBD5E1",
    "MUTED":      "#64748B",
    "DIM":        "#475569",
    "TEXT":       "#0F172A",
    "GREEN":      GREEN,
    "BLUE":       BLUE,
    "AMBER":      AMBER,
    "RED":        RED,
    "ORANGE":     ORANGE,
}

# ── State badge configs ───────────────────────────────────────────────────────
_STATE_BADGE_DARK = {
    "DISCONNECTED": ("#151A23", "#334155", "#94A3B8"),
    "CONNECTING":   ("#231A0E", "#7C4A1A", ORANGE),
    "LOCATED":      ("#0D2417", "#1E5C2E", GREEN),
    "READ_ERROR":   ("#230E0E", "#7C1F1F", RED),
    "RESCANNING":   ("#231A0E", "#7C4A1A", ORANGE),
}

_STATE_BADGE_LIGHT = {
    "DISCONNECTED": ("#E2E8F0", "#CBD5E1", "#475569"),
    "CONNECTING":   ("#FEF3C7", "#F59E0B", "#92400E"),
    "LOCATED":      ("#DCFCE7", "#86EFAC", "#166534"),
    "READ_ERROR":   ("#FEE2E2", "#FCA5A5", "#991B1B"),
    "RESCANNING":   ("#FEF3C7", "#F59E0B", "#92400E"),
}


# ── ThemeManager ──────────────────────────────────────────────────────────────
class ThemeManager:
    """Singleton managing active theme palette and QSS application."""

    _mode: str = "dark"
    _palette: dict[str, str] = DARK_PALETTE
    _app = None

    @classmethod
    def apply(cls, app, mode: str) -> None:
        """Apply palette to app stylesheet and persist preference."""
        cls._app = app
        cls._mode = mode
        cls._palette = DARK_PALETTE if mode == "dark" else LIGHT_PALETTE
        app.setStyleSheet(_build_qss(cls._palette))
        save_theme(mode)

    @classmethod
    def toggle(cls) -> None:
        """Switch between dark and light modes."""
        new_mode = "light" if cls._mode == "dark" else "dark"
        new_palette = LIGHT_PALETTE if new_mode == "light" else DARK_PALETTE
        cls._mode = new_mode
        cls._palette = new_palette
        if cls._app is not None:
            cls._app.setStyleSheet(_build_qss(new_palette))
        save_theme(new_mode)

    @classmethod
    def c(cls, key: str) -> str:
        """Return current palette value for key."""
        return cls._palette.get(key, "#FF00FF")  # magenta = missing key

    @classmethod
    def mode(cls) -> str:
        return cls._mode


# ── Dynamic style helpers ─────────────────────────────────────────────────────
def badge_style(state: str) -> str:
    """Return inline QSS for the connection-state pill badge."""
    badges = _STATE_BADGE_DARK if ThemeManager._mode == "dark" else _STATE_BADGE_LIGHT
    bg, border, color = badges.get(state, badges["DISCONNECTED"])
    return (
        f"color: {color}; background-color: {bg}; "
        f"border: 1px solid {border}; border-radius: 10px; "
        "padding: 2px 10px; font-weight: 600; font-size: 11px;"
    )


def vital_html(key: str, val, val_color: str | None = None) -> str:
    """Render a simple vital field: dim key + bright value."""
    dim = ThemeManager.c("DIM")
    text = val_color or ThemeManager.c("TEXT")
    return (
        f'<span style="color:{dim};font-size:10px;letter-spacing:1px;">{key}</span>'
        f'&thinsp;<span style="color:{text};font-weight:700;">{val}</span>'
    )


def fraction_html(key: str, cur, mx, val_color: str = GREEN) -> str:
    """Render a cur/max vital: dim key + colored cur + muted /max."""
    dim = ThemeManager.c("DIM")
    muted = ThemeManager.c("MUTED")
    return (
        f'<span style="color:{dim};font-size:10px;letter-spacing:1px;">{key}</span>'
        f'&thinsp;<span style="color:{val_color};font-weight:700;">{cur}</span>'
        f'<span style="color:{muted};">/{mx}</span>'
    )


# ── QSS builder ───────────────────────────────────────────────────────────────
def _build_qss(p: dict[str, str]) -> str:
    """Generate full application QSS from palette dict p."""
    BG_BASE    = p["BG_BASE"]
    BG_SURFACE = p["BG_SURFACE"]
    BG_CARD    = p["BG_CARD"]
    BORDER     = p["BORDER"]
    MUTED      = p["MUTED"]
    DIM        = p["DIM"]
    TEXT       = p["TEXT"]
    _GREEN     = p["GREEN"]
    _BLUE      = p["BLUE"]
    _AMBER     = p["AMBER"]
    _RED       = p["RED"]

    # Table selection tint: green tint adapted per theme
    _SEL_BG  = "#122118" if BG_BASE == "#020617" else "#DCFCE7"
    _SEL_COL = _GREEN

    return f"""
/* ── Global ─────────────────────────────────────────────────────── */
QWidget {{
    background-color: {BG_BASE};
    color: {TEXT};
    font-family: "Noto Sans TC", "Microsoft JhengHei UI", "Segoe UI", sans-serif;
    font-size: 10pt;
    outline: 0;
}}
QLabel#mono {{
    font-family: "Cascadia Code", "Consolas", monospace;
}}
QMainWindow, QDialog {{
    background-color: {BG_BASE};
}}

/* ── Group Box ───────────────────────────────────────────────────── */
QGroupBox {{
    border: 1px solid {BG_CARD};
    border-radius: 8px;
    margin-top: 14px;
    padding: 12px 8px 10px 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    top: -1px;
    background-color: {BG_BASE};
    padding: 0 4px;
    color: {DIM};
    font-size: 8pt;
    font-weight: 600;
    letter-spacing: 1px;
}}

/* ── Buttons ─────────────────────────────────────────────────────── */
QPushButton {{
    background-color: {BG_CARD};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 16px;
    font-weight: 500;
    min-height: 28px;
}}
QPushButton:hover {{
    background-color: {BORDER};
    border-color: {_GREEN};
    color: {_GREEN};
}}
QPushButton:pressed {{
    background-color: {_GREEN};
    color: {BG_BASE};
    border-color: {_GREEN};
}}
QPushButton:disabled {{
    background-color: {BG_SURFACE};
    color: {MUTED};
    border-color: {BG_CARD};
}}

/* Primary green button */
QPushButton#primary_btn {{
    background-color: {_GREEN};
    color: {BG_BASE};
    font-weight: 700;
    border: none;
}}
QPushButton#primary_btn:hover {{
    background-color: #16A34A;
    color: {BG_BASE};
    border: none;
}}
QPushButton#primary_btn:pressed {{
    background-color: #15803D;
}}
QPushButton#primary_btn:disabled {{
    background-color: {BG_CARD};
    color: {MUTED};
    border: 1px solid {BG_CARD};
}}

/* Close tab button */
QPushButton#close_btn {{
    color: {MUTED};
    background: transparent;
    border: none;
    padding: 3px;
    font-size: 12px;
    min-height: 0;
    min-width: 0;
}}
QPushButton#close_btn:hover {{
    color: {_RED};
    background: rgba(239,68,68,0.15);
    border-radius: 4px;
}}

/* Refresh/add tab button */
QPushButton#refresh_btn {{
    color: {DIM};
    background: transparent;
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 2px 6px;
    font-size: 16px;
    font-weight: 700;
    min-height: 0;
    min-width: 0;
}}
QPushButton#refresh_btn:hover {{
    color: {_GREEN};
    border-color: {_GREEN};
    background: rgba(34,197,94,0.10);
}}
QPushButton#refresh_btn:pressed {{
    background: rgba(34,197,94,0.20);
}}

/* Filter toggle button */
QPushButton#filter_toggle_btn {{
    background: transparent;
    border: none;
    color: {TEXT};
    font-size: 10pt;
    font-weight: 500;
    min-height: 0;
    padding: 2px 6px;
}}
QPushButton#filter_toggle_btn:hover {{
    color: {_GREEN};
}}

/* ── Line Edit ───────────────────────────────────────────────────── */
QLineEdit {{
    background-color: {BG_SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 10px;
    min-height: 28px;
    selection-background-color: {_GREEN};
    selection-color: {BG_BASE};
}}
QLineEdit:focus {{
    border-color: {_GREEN};
}}

/* ── Tabs ────────────────────────────────────────────────────────── */
QTabWidget::pane {{
    border: 1px solid {BG_CARD};
    border-radius: 0 8px 8px 8px;
    background-color: {BG_SURFACE};
    top: -1px;
}}
QTabBar::tab {{
    background-color: {BG_BASE};
    color: {DIM};
    border: 1px solid {BG_CARD};
    border-bottom: none;
    padding: 6px 20px;
    margin-right: 2px;
    border-radius: 6px 6px 0 0;
    font-weight: 500;
    min-width: 72px;
}}
QTabBar::tab:selected {{
    background-color: {BG_SURFACE};
    color: {TEXT};
    border-top: 2px solid {_GREEN};
    border-left-color: {BG_CARD};
    border-right-color: {BG_CARD};
    border-bottom-color: {BG_SURFACE};
}}
QTabBar::tab:hover:!selected {{
    color: {TEXT};
    background-color: {BG_SURFACE};
}}

/* ── Progress Bars ───────────────────────────────────────────────── */
QProgressBar {{
    background-color: {BG_CARD};
    border: none;
    border-radius: 5px;
    min-height: 10px;
    max-height: 10px;
}}
QProgressBar::chunk {{
    border-radius: 5px;
    background-color: {_GREEN};
}}
QProgressBar#mp_bar::chunk {{
    background-color: {_BLUE};
}}
QProgressBar#weight_bar::chunk {{
    background-color: {_AMBER};
}}

/* ── Tables ──────────────────────────────────────────────────────── */
QTableWidget {{
    background-color: {BG_SURFACE};
    alternate-background-color: {BG_CARD};
    border: 1px solid {BG_CARD};
    border-radius: 6px;
    gridline-color: {BG_CARD};
    selection-background-color: {_SEL_BG};
    selection-color: {_SEL_COL};
    outline: 0;
}}
QTableWidget::item {{
    padding: 4px 8px;
    border: none;
}}
QTableWidget::item:selected {{
    background-color: {_SEL_BG};
    color: {_SEL_COL};
}}
QHeaderView {{
    background-color: {BG_BASE};
}}
QHeaderView::section {{
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
QHeaderView::section:last-child {{
    border-right: none;
}}
QHeaderView::section:hover {{
    background-color: {BG_SURFACE};
    color: {TEXT};
}}

/* ── Status Bar ──────────────────────────────────────────────────── */
QStatusBar {{
    background-color: {BG_BASE};
    color: {DIM};
    border-top: 1px solid {BG_CARD};
    font-size: 9pt;
    padding: 2px 6px;
}}

/* ── Scrollbars ──────────────────────────────────────────────────── */
QScrollBar:vertical {{
    background: {BG_SURFACE};
    width: 8px;
    border: none;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {DIM};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none; height: 0; width: 0;
}}
QScrollBar:horizontal {{
    background: {BG_SURFACE};
    height: 8px;
    border: none;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER};
    border-radius: 4px;
    min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {DIM};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: none; height: 0; width: 0;
}}
QAbstractScrollArea::corner {{
    background: {BG_SURFACE};
}}

/* ── Vitals strip ────────────────────────────────────────────────── */
QFrame#vitals_strip {{
    background-color: {BG_SURFACE};
    border: 1px solid {BG_CARD};
    border-radius: 8px;
}}
QFrame#vitals_sep {{
    background-color: {BG_CARD};
    border: none;
    max-width: 1px;
    min-width: 1px;
}}

/* ── Operation bar ───────────────────────────────────────────────── */
QFrame#op_bar {{
    background-color: {BG_SURFACE};
    border: 1px solid {BG_CARD};
    border-radius: 8px;
}}

/* ── Nav sidebar ─────────────────────────────────────────────────── */
QFrame#nav_sidebar {{
    background-color: {BG_SURFACE};
    border-right: 1px solid {BG_CARD};
}}
QPushButton#nav_btn {{
    background: transparent;
    border: none;
    border-left: 3px solid transparent;
    border-radius: 0;
    color: {DIM};
    font-size: 9pt;
    font-weight: 600;
    letter-spacing: 1px;
    padding: 12px 0;
    text-align: center;
    min-height: 48px;
}}
QPushButton#nav_btn:hover:!checked {{
    background: rgba(34,197,94,0.08);
    color: {TEXT};
}}
QPushButton#nav_btn:checked {{
    background: rgba(34,197,94,0.12);
    border-left-color: {_GREEN};
    color: {_GREEN};
}}

/* Theme toggle button in nav sidebar */
QPushButton#theme_toggle_btn {{
    background: transparent;
    border: none;
    border-left: 3px solid transparent;
    border-radius: 0;
    color: {MUTED};
    font-size: 9pt;
    font-weight: 600;
    letter-spacing: 1px;
    padding: 10px 0;
    text-align: center;
    min-height: 36px;
}}
QPushButton#theme_toggle_btn:hover {{
    background: rgba(34,197,94,0.08);
    color: {TEXT};
}}

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
    background-color: {_GREEN};
    color: {BG_BASE};
    border-color: {_GREEN};
    font-weight: 700;
}}
QPushButton#toggle_left:hover:!checked, QPushButton#toggle_right:hover:!checked {{
    background-color: {BORDER};
    border-color: {_GREEN};
    color: {_GREEN};
}}

/* ── Tree Widget ─────────────────────────────────────────────── */
QTreeWidget {{
    background-color: {BG_SURFACE};
    alternate-background-color: {BG_CARD};
    border: 1px solid {BG_CARD};
    border-radius: 6px;
    selection-background-color: {_SEL_BG};
    selection-color: {_SEL_COL};
    outline: 0;
}}
QTreeWidget::item {{
    padding: 4px 8px;
    border: none;
}}
QTreeWidget::item:selected {{
    background-color: {_SEL_BG};
    color: {_SEL_COL};
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

/* ── Data management left panel ─────────────────────────────── */
QFrame#mgmt_left_panel {{
    background-color: {BG_SURFACE};
    border-right: 1px solid {BG_CARD};
}}
QListWidget#mgmt_char_list {{
    background-color: {BG_SURFACE};
    border: none;
    outline: 0;
}}
QListWidget#mgmt_char_list::item {{
    padding: 8px 10px;
    border-bottom: 1px solid {BG_CARD};
    color: {DIM};
}}
QListWidget#mgmt_char_list::item:selected {{
    background-color: rgba(34,197,94,0.12);
    color: {_GREEN};
    border-left: 2px solid {_GREEN};
}}
QTreeWidget#mgmt_acct_tree {{
    background-color: {BG_SURFACE};
    border: 1px solid {BG_CARD};
    border-radius: 6px;
}}
QTreeWidget#mgmt_acct_tree::item {{
    padding: 4px 6px;
    color: {DIM};
}}

/* ── Delete button in snapshot table ────────────────────────── */
QPushButton#delete_btn {{
    padding: 4px 8px;
    min-height: 24px;
}}

/* ── HP / MP labels in op_bar ────────────────────────────────── */
QLabel#vital_hp_label {{
    color: {_GREEN};
    font-weight: 600;
    font-size: 11px;
    padding: 2px 6px;
}}
QLabel#vital_mp_label {{
    color: {_BLUE};
    font-weight: 600;
    font-size: 11px;
    padding: 2px 6px;
}}

/* ── Version label in status bar ─────────────────────────────── */
QLabel#version_label {{
    color: {MUTED};
    font-size: 11px;
    padding-right: 6px;
}}

/* ── Placeholder tab label ───────────────────────────────────── */
QLabel#placeholder_lbl {{
    color: {DIM};
    font-size: 14px;
}}

/* ── Character card ──────────────────────────────────────────── */
QFrame#char_card {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QFrame#char_card_header {{
    background-color: {BG_CARD};
    border-radius: 8px 8px 0 0;
    padding: 0 4px;
}}
"""


# Keep DARK_QSS as a convenience alias (used in gui_main.py startup before ThemeManager exists)
DARK_QSS = _build_qss(DARK_PALETTE)
```

**Step 4: Run tests**

```bash
uv run pytest tests/test_theme.py -v
```
Expected: 8 passed

**Step 5: Commit**

```bash
git add gui/theme.py tests/test_theme.py
git commit -m "feat: add ThemeManager with dark/light palettes and QSS generator"
```

---

### Task 3: gui_main.py — load config and apply initial theme

**Files:**
- Modify: `gui_main.py`

**Step 1: No new tests needed** — startup wiring, covered by existing `test_main_window.py`

**Step 2: Update `gui_main.py`**

Replace the entire file:

```python
"""Entry point for the PySide6 GUI."""
import sys
from PySide6.QtWidgets import QApplication
from gui.main_window import MainWindow
from gui.theme import ThemeManager
from gui.config import load_theme


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Tthol Reader")
    ThemeManager.apply(app, load_theme())
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
```

**Step 3: Run existing tests to confirm nothing broke**

```bash
uv run pytest tests/test_main_window.py -v
```
Expected: all pass

**Step 4: Commit**

```bash
git add gui_main.py
git commit -m "feat: load theme from config on startup via ThemeManager"
```

---

### Task 4: main_window.py — remove inline styles + add theme toggle button

**Files:**
- Modify: `gui/main_window.py`

**Step 1: Write new test**

Add to `tests/test_main_window.py`:

```python
def test_main_window_has_theme_toggle_button(main_window):
    assert hasattr(main_window, "_btn_theme")
    assert main_window._btn_theme is not None


def test_theme_toggle_button_label_dark(main_window):
    from gui.theme import ThemeManager
    ThemeManager._mode = "dark"
    # Force label refresh
    main_window._update_theme_btn_label()
    assert "亮色" in main_window._btn_theme.text()


def test_theme_toggle_button_label_light(main_window):
    from gui.theme import ThemeManager
    ThemeManager._mode = "light"
    main_window._update_theme_btn_label()
    assert "暗色" in main_window._btn_theme.text()
```

**Step 2: Run to verify failure**

```bash
uv run pytest tests/test_main_window.py::test_main_window_has_theme_toggle_button -v
```
Expected: `AttributeError: 'MainWindow' object has no attribute '_btn_theme'`

**Step 3: Update `gui/main_window.py`**

Key changes (do NOT remove existing logic, only update):

a) Remove module-level constants `_CLOSE_BTN_STYLE` and `_REFRESH_BTN_STYLE` entirely.

b) Update imports — remove `BORDER, DIM, GREEN, MUTED, RED` from theme import; keep only `badge_style` for CharacterPanel (which uses it internally). In `main_window.py`, no more direct color imports are needed:

```python
from gui.theme import ThemeManager
```

c) Update `_make_close_btn`: remove `btn.setStyleSheet(_CLOSE_BTN_STYLE)`, set object name instead:

```python
def _make_close_btn(self, panel: "CharacterPanel") -> QPushButton:
    btn = QPushButton("X")
    btn.setFixedSize(20, 20)
    btn.setObjectName("close_btn")
    btn.setToolTip(t("close_tab_tooltip"))
    btn.clicked.connect(lambda: self._close_panel(panel))
    return btn
```

d) Update refresh button — remove `setStyleSheet`, set object name:

```python
refresh_btn = QPushButton("+")
refresh_btn.setToolTip(t("refresh_tooltip"))
refresh_btn.setFixedSize(34, 28)
refresh_btn.setObjectName("refresh_btn")
refresh_btn.clicked.connect(self._on_refresh)
```

e) Update version label — remove `setStyleSheet`, set object name:

```python
version_label = QLabel(f"rev: {_get_version()}")
version_label.setObjectName("version_label")
version_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
self.statusBar().addPermanentWidget(version_label)
```

f) Add `_btn_theme` to nav sidebar after `nav_layout.addStretch()`:

```python
nav_layout.addStretch()

self._btn_theme = QPushButton()
self._btn_theme.setObjectName("theme_toggle_btn")
self._btn_theme.clicked.connect(self._on_toggle_theme)
self._update_theme_btn_label()
nav_layout.addWidget(self._btn_theme)
```

g) Add helper method and slot:

```python
def _update_theme_btn_label(self) -> None:
    """Set toggle button label to reflect the mode we will switch TO."""
    if ThemeManager.mode() == "dark":
        self._btn_theme.setText("◑ 亮色")
    else:
        self._btn_theme.setText("◑ 暗色")

@Slot()
def _on_toggle_theme(self):
    ThemeManager.toggle()
    self._update_theme_btn_label()
```

h) Update `_show_placeholder` — remove `setStyleSheet`, set object name:

```python
lbl = QLabel(t("placeholder_tab"))
lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
lbl.setObjectName("placeholder_lbl")
lbl.setProperty("is_placeholder", True)
```

**Step 4: Run tests**

```bash
uv run pytest tests/test_main_window.py -v
```
Expected: all pass including 3 new tests

**Step 5: Commit**

```bash
git add gui/main_window.py tests/test_main_window.py
git commit -m "feat: add theme toggle button to nav sidebar, remove inline styles from main_window"
```

---

### Task 5: character_panel.py — remove inline styles + object names

**Files:**
- Modify: `gui/character_panel.py`

**Step 1: No new tests** — visual-only changes; existing test coverage sufficient

**Step 2: Update `gui/character_panel.py`**

a) Update imports — remove direct color imports, keep only what's needed for CharacterPanel logic:

```python
from gui.theme import badge_style, vital_html, fraction_html, GREEN, BLUE, AMBER
```
(Keep `GREEN`, `BLUE`, `AMBER` — they're passed as `val_color` params to `vital_html`/`fraction_html`. These are accent constants that don't change between themes.)

b) Update `hp_lbl` — remove `setStyleSheet`, set object name:

```python
hp_lbl = QLabel(t("vital_hp"))
hp_lbl.setObjectName("vital_hp_label")
op_layout.addWidget(hp_lbl)
```

c) Update `mp_lbl` — remove `setStyleSheet`, set object name:

```python
mp_lbl = QLabel(t("mp_filter_label"))
mp_lbl.setObjectName("vital_mp_label")
filter_layout.addWidget(mp_lbl)
```

d) Update `_advanced_btn` — set object name `filter_toggle_btn`, remove `setFlat`:

```python
self._advanced_btn = QPushButton(t("filter_toggle_show"))
self._advanced_btn.setObjectName("filter_toggle_btn")
self._advanced_btn.clicked.connect(self._on_toggle_filter)
op_layout.addWidget(self._advanced_btn)
```

**Step 3: Run all tests**

```bash
uv run pytest -v
```
Expected: all pass

**Step 4: Commit**

```bash
git add gui/character_panel.py
git commit -m "feat: remove inline styles from character_panel, use QSS object names"
```

---

### Task 6: Manual smoke test + close issues

**Step 1: Launch GUI and verify dark theme**

```bash
uv run gui_main.py
```

Check:
- [ ] App launches in dark mode (if no config.json, or config.json has `"theme": "dark"`)
- [ ] Nav sidebar shows "◑ 亮色" toggle button at bottom
- [ ] Filter toggle button `▼ 篩選` is clearly visible (TEXT color, not DIM)
- [ ] Close tab X button and + button look correct

**Step 2: Switch to light theme**

Click "◑ 亮色" button.

Check:
- [ ] Background switches to slate-50 white
- [ ] All text readable (dark slate-900)
- [ ] `▼ 篩選` arrow clearly visible
- [ ] Badge pills readable (LOCATED = green tint, DISCONNECTED = gray)
- [ ] Button label changes to "◑ 暗色"
- [ ] `config.json` created/updated with `"theme": "light"`

**Step 3: Restart app**

```bash
uv run gui_main.py
```

Check:
- [ ] App launches in light theme (persisted from config.json)
- [ ] Toggle button shows "◑ 暗色"

**Step 4: Close GitHub issues**

```bash
gh issue close 4 --repo hanshino/tthol-toolkit --comment "Fixed: filter toggle arrow (▼ 篩選) now uses TEXT color in both themes, ensuring visibility. Root cause was dark-theme contrast. Resolved alongside #5."
gh issue close 5 --repo hanshino/tthol-toolkit --comment "Implemented: light/dark theme toggle (◑) in nav sidebar. Slate-50 base palette with slate-900 text. Preference persisted to config.json."
```

**Step 5: Final commit**

```bash
git add config.json  # if created during testing
git commit -m "chore: add default config.json" --allow-empty
```
(Only if config.json should be tracked — if not, add to .gitignore instead.)
