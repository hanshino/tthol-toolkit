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

# ── Shared accents (kept as module constants for val_color params) ─────────────
GREEN = "#22C55E"
BLUE = "#3B82F6"
AMBER = "#F59E0B"
RED = "#EF4444"
ORANGE = "#F97316"

# ── Backward-compatible dark-palette module constants ──────────────────────────
# These are used by gui modules that import them directly (e.g. BORDER, DIM).
# They reflect the dark palette defaults. Dynamic theming should use ThemeManager.c().
BG_BASE = "#020617"
BG_SURFACE = "#0F172A"
BG_CARD = "#1E293B"
BORDER = "#334155"
MUTED = "#475569"
DIM = "#94A3B8"
TEXT = "#F8FAFC"

# ── Palettes ──────────────────────────────────────────────────────────────────
DARK_PALETTE: dict[str, str] = {
    "BG_BASE": "#020617",
    "BG_SURFACE": "#0F172A",
    "BG_CARD": "#1E293B",
    "BORDER": "#334155",
    "MUTED": "#475569",
    "DIM": "#94A3B8",
    "TEXT": "#F8FAFC",
    "GREEN": GREEN,
    "GREEN_HOVER": "#16A34A",
    "GREEN_PRESS": "#15803D",
    "BLUE": BLUE,
    "AMBER": AMBER,
    "RED": RED,
    "ORANGE": ORANGE,
}

LIGHT_PALETTE: dict[str, str] = {
    "BG_BASE": "#F8FAFC",
    "BG_SURFACE": "#F1F5F9",
    "BG_CARD": "#E2E8F0",
    "BORDER": "#CBD5E1",
    "MUTED": "#64748B",
    "DIM": "#475569",
    "TEXT": "#0F172A",
    "GREEN": GREEN,
    "GREEN_HOVER": "#16A34A",
    "GREEN_PRESS": "#15803D",
    "BLUE": BLUE,
    "AMBER": AMBER,
    "RED": RED,
    "ORANGE": ORANGE,
}

# ── State badge configs ───────────────────────────────────────────────────────
_STATE_BADGE_DARK = {
    "DISCONNECTED": ("#151A23", "#334155", "#94A3B8"),
    "CONNECTING": ("#231A0E", "#7C4A1A", ORANGE),
    "LOCATED": ("#0D2417", "#1E5C2E", GREEN),
    "READ_ERROR": ("#230E0E", "#7C1F1F", RED),
    "RESCANNING": ("#231A0E", "#7C4A1A", ORANGE),
}

_STATE_BADGE_LIGHT = {
    "DISCONNECTED": ("#E2E8F0", "#CBD5E1", "#475569"),
    "CONNECTING": ("#FEF3C7", "#F59E0B", "#92400E"),
    "LOCATED": ("#DCFCE7", "#86EFAC", "#166534"),
    "READ_ERROR": ("#FEE2E2", "#FCA5A5", "#991B1B"),
    "RESCANNING": ("#FEF3C7", "#F59E0B", "#92400E"),
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
        if mode not in ("dark", "light"):
            mode = "dark"
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
        """Return current palette value for key. Returns magenta on missing key."""
        return cls._palette.get(key, "#FF00FF")

    @classmethod
    def mode(cls) -> str:
        """Return current mode string ('dark' or 'light')."""
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
        f"&thinsp;"
        f'<span style="color:{text};font-weight:700;">{val}</span>'
    )


def fraction_html(key: str, cur, mx, val_color: str = GREEN) -> str:
    """Render a cur/max vital: dim key + colored cur + muted /max."""
    dim = ThemeManager.c("DIM")
    muted = ThemeManager.c("MUTED")
    return (
        f'<span style="color:{dim};font-size:10px;letter-spacing:1px;">{key}</span>'
        f"&thinsp;"
        f'<span style="color:{val_color};font-weight:700;">{cur}</span>'
        f'<span style="color:{muted};">/{mx}</span>'
    )


# ── QSS builder ───────────────────────────────────────────────────────────────
def _build_qss(p: dict[str, str]) -> str:
    """Generate full application QSS from palette dict p."""
    BG_BASE = p["BG_BASE"]
    BG_SURFACE = p["BG_SURFACE"]
    BG_CARD = p["BG_CARD"]
    BORDER = p["BORDER"]
    MUTED = p["MUTED"]
    DIM = p["DIM"]
    TEXT = p["TEXT"]
    _GREEN = p["GREEN"]
    _GREEN_HOVER = p["GREEN_HOVER"]
    _GREEN_PRESS = p["GREEN_PRESS"]
    _BLUE = p["BLUE"]
    _AMBER = p["AMBER"]
    _RED = p["RED"]

    # Table/tree selection tint: green tint adapted per theme
    _SEL_BG = "#122118" if BG_BASE == "#020617" else "#DCFCE7"
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
    background-color: {_GREEN_HOVER};
    color: {BG_BASE};
    border: none;
}}
QPushButton#primary_btn:pressed {{
    background-color: {_GREEN_PRESS};
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
    background: none;
    height: 0;
    width: 0;
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
    background: none;
    height: 0;
    width: 0;
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


# Convenience alias — startup uses this before ThemeManager.apply() is called
DARK_QSS = _build_qss(DARK_PALETTE)
