"""
Dark gaming-style theme for Tthol Reader.

Color system (Slate-950 base + Green accent):
    BG_BASE    = #020617  slate-950  — main window background
    BG_SURFACE = #0F172A  slate-900  — tab pane, panels
    BG_CARD    = #1E293B  slate-800  — cards, group boxes
    BORDER     = #334155  slate-700  — interactive borders
    MUTED      = #475569  slate-600  — disabled / secondary text
    DIM        = #94A3B8  slate-400  — labels / captions
    TEXT       = #F8FAFC  slate-50   — primary text

    GREEN  = #22C55E  — HP, active, primary CTA
    BLUE   = #3B82F6  — MP / mana
    AMBER  = #F59E0B  — Weight / warning
    RED    = #EF4444  — error
    ORANGE = #F97316  — connecting / scanning
"""

GREEN  = "#22C55E"
BLUE   = "#3B82F6"
AMBER  = "#F59E0B"
RED    = "#EF4444"
ORANGE = "#F97316"

BG_BASE    = "#020617"
BG_SURFACE = "#0F172A"
BG_CARD    = "#1E293B"
BORDER     = "#334155"
MUTED      = "#475569"
DIM        = "#94A3B8"
TEXT       = "#F8FAFC"

# State badge (bg, border, text)
_STATE_BADGE = {
    "DISCONNECTED": ("#151A23", "#334155", DIM),
    "CONNECTING":   ("#231A0E", "#7C4A1A", ORANGE),
    "LOCATED":      ("#0D2417", "#1E5C2E", GREEN),
    "READ_ERROR":   ("#230E0E", "#7C1F1F", RED),
    "RESCANNING":   ("#231A0E", "#7C4A1A", ORANGE),
}


def badge_style(state: str) -> str:
    """Return inline QSS for the connection-state pill badge."""
    bg, border, color = _STATE_BADGE.get(state, _STATE_BADGE["DISCONNECTED"])
    return (
        f"color: {color}; background-color: {bg}; "
        f"border: 1px solid {border}; border-radius: 10px; "
        "padding: 2px 10px; font-weight: 600; font-size: 11px;"
    )


def vital_html(key: str, val, val_color: str = TEXT) -> str:
    """Render a simple vital field: dim key + bright value."""
    return (
        f'<span style="color:{DIM};font-size:10px;letter-spacing:1px;">{key}</span>'
        f'&thinsp;<span style="color:{val_color};font-weight:700;">{val}</span>'
    )


def fraction_html(key: str, cur, mx, val_color: str = GREEN) -> str:
    """Render a cur/max vital: dim key + colored cur + muted /max."""
    return (
        f'<span style="color:{DIM};font-size:10px;letter-spacing:1px;">{key}</span>'
        f'&thinsp;<span style="color:{val_color};font-weight:700;">{cur}</span>'
        f'<span style="color:{MUTED};">/{mx}</span>'
    )


DARK_QSS = f"""
/* ── Global ─────────────────────────────────────────────────────── */
QWidget {{
    background-color: {BG_BASE};
    color: {TEXT};
    font-family: "Consolas", "Cascadia Code", "Segoe UI", monospace;
    font-size: 13px;
    outline: 0;
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
    font-size: 10px;
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
    border-color: {GREEN};
    color: {GREEN};
}}
QPushButton:pressed {{
    background-color: {GREEN};
    color: {BG_BASE};
    border-color: {GREEN};
}}
QPushButton:disabled {{
    background-color: {BG_SURFACE};
    color: {MUTED};
    border-color: {BG_CARD};
}}

/* Primary green button (Connect) */
QPushButton#primary_btn {{
    background-color: {GREEN};
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

/* ── Line Edit ───────────────────────────────────────────────────── */
QLineEdit {{
    background-color: {BG_SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 10px;
    min-height: 28px;
    selection-background-color: {GREEN};
    selection-color: {BG_BASE};
}}
QLineEdit:focus {{
    border-color: {GREEN};
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
    border-top: 2px solid {GREEN};
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
    background-color: {GREEN};
}}
QProgressBar#mp_bar::chunk {{
    background-color: {BLUE};
}}
QProgressBar#weight_bar::chunk {{
    background-color: {AMBER};
}}

/* ── Tables ──────────────────────────────────────────────────────── */
QTableWidget {{
    background-color: {BG_SURFACE};
    alternate-background-color: {BG_CARD};
    border: 1px solid {BG_CARD};
    border-radius: 6px;
    gridline-color: {BG_CARD};
    selection-background-color: #122118;
    selection-color: {GREEN};
    outline: 0;
}}
QTableWidget::item {{
    padding: 4px 8px;
    border: none;
}}
QTableWidget::item:selected {{
    background-color: #122118;
    color: {GREEN};
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
    font-size: 10px;
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
    font-size: 11px;
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

/* ── Operation bar container ─────────────────────────────────────── */
QFrame#op_bar {{
    background-color: {BG_SURFACE};
    border: 1px solid {BG_CARD};
    border-radius: 8px;
}}
"""
