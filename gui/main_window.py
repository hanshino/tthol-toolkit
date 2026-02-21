"""
Main application window.

Layout:
  Outer QTabWidget — one tab per detected tthola.dat process.
    Each tab: CharacterPanel (op_bar + vitals + inner tabs)
  Refresh button embedded in the tab bar (RightSide) to re-scan for new windows.
"""
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout,
    QLabel, QPushButton, QTabWidget, QTabBar, QStatusBar,
)
from PySide6.QtCore import Qt, Slot

from gui.character_panel import CharacterPanel
from gui.process_detector import detect_game_windows
from gui.snapshot_db import SnapshotDB
from gui.theme import BORDER, DIM, GREEN, MUTED, RED
from gui.i18n import t

_CLOSE_BTN_STYLE = (
    f"QPushButton {{ color: {MUTED}; background: transparent; border: none; "
    f"padding: 3px; font-size: 12px; min-height: 0; min-width: 0; }}"
    f"QPushButton:hover {{ color: {RED}; background: rgba(239,68,68,0.15); "
    f"border-radius: 4px; }}"
)

_REFRESH_BTN_STYLE = (
    f"QPushButton {{ color: {DIM}; background: transparent; "
    f"border: 1px solid {BORDER}; border-radius: 6px; "
    f"padding: 2px 6px; font-size: 16px; font-weight: 700; "
    f"min-height: 0; min-width: 0; }}"
    f"QPushButton:hover {{ color: {GREEN}; border-color: {GREEN}; "
    f"background: rgba(34,197,94,0.10); }}"
    f"QPushButton:pressed {{ background: rgba(34,197,94,0.20); }}"
)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(t("window_title"))
        self.setMinimumWidth(580)
        self._snapshot_db = SnapshotDB()
        self._panels: dict[int, CharacterPanel] = {}   # pid → panel

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._outer_tabs = QTabWidget()
        # Custom close buttons are added per-tab via _attach_close_btn().
        # setTabsClosable(False) prevents the native platform close button.
        self._outer_tabs.setTabsClosable(False)

        refresh_btn = QPushButton("+")
        refresh_btn.setToolTip(t("refresh_tooltip"))
        refresh_btn.setFixedSize(34, 28)
        refresh_btn.setStyleSheet(_REFRESH_BTN_STYLE)
        refresh_btn.clicked.connect(self._on_refresh)
        self._outer_tabs.setCornerWidget(refresh_btn, Qt.Corner.TopRightCorner)

        root.addWidget(self._outer_tabs)
        self.setStatusBar(QStatusBar())

        self._populate_tabs()

    # ------------------------------------------------------------------
    # Tab management
    # ------------------------------------------------------------------
    def _make_close_btn(self, panel: "CharacterPanel") -> QPushButton:
        """Return a styled ✕ button that closes the given panel's tab."""
        btn = QPushButton("✕")
        btn.setFixedSize(20, 20)
        btn.setStyleSheet(_CLOSE_BTN_STYLE)
        btn.setToolTip(t("close_tab_tooltip"))
        btn.clicked.connect(lambda: self._close_panel(panel))
        return btn

    def _attach_close_btn(self, index: int, panel: "CharacterPanel"):
        """Place a custom close button on the right side of the tab at index."""
        btn = self._make_close_btn(panel)
        self._outer_tabs.tabBar().setTabButton(
            index, QTabBar.ButtonPosition.RightSide, btn
        )

    def _populate_tabs(self):
        """Detect game windows and add a tab for each new PID found."""
        windows = detect_game_windows()
        if not windows and not self._panels:
            self._show_placeholder()
            return
        added = 0
        for pid, hwnd, label in windows:
            if pid in self._panels:
                continue
            self._remove_placeholder()
            panel = CharacterPanel(pid=pid, hwnd=hwnd, snapshot_db=self._snapshot_db)
            panel.status_message.connect(self._on_status_message)
            idx = self._outer_tabs.addTab(panel, label)
            self._attach_close_btn(idx, panel)
            # Use indexOf(panel) so the rename targets the correct tab even
            # after lower-indexed tabs have been closed.
            panel.tab_label_changed.connect(
                lambda name, p=panel: self._outer_tabs.setTabText(
                    self._outer_tabs.indexOf(p), name
                )
            )
            self._panels[pid] = panel
            added += 1
        if windows and added == 0:
            self.statusBar().showMessage(t("no_new_windows"), 2000)

    def _show_placeholder(self):
        """Show a single informational tab when no game window is found."""
        if self._outer_tabs.count() == 0:
            lbl = QLabel(t("placeholder_tab"))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(f"color: {DIM}; font-size: 14px;")
            lbl.setProperty("is_placeholder", True)
            self._outer_tabs.addTab(lbl, t("placeholder_tab"))

    def _remove_placeholder(self):
        """Remove placeholder tab if it is present."""
        for i in range(self._outer_tabs.count()):
            w = self._outer_tabs.widget(i)
            if w and w.property("is_placeholder"):
                self._outer_tabs.removeTab(i)
                break

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------
    @Slot()
    def _on_refresh(self):
        self._populate_tabs()

    def _close_panel(self, panel: "CharacterPanel"):
        """Close the tab containing panel (minimum 1 tab enforced)."""
        if self._outer_tabs.count() <= 1:
            return   # prevent closing the last tab
        index = self._outer_tabs.indexOf(panel)
        if index == -1:
            return
        self._outer_tabs.removeTab(index)
        pid_to_remove = next(
            (pid for pid, p in self._panels.items() if p is panel), None
        )
        if pid_to_remove is not None:
            del self._panels[pid_to_remove]
        panel.shutdown()
        panel.deleteLater()

    @Slot(str, int)
    def _on_status_message(self, msg: str, timeout: int):
        self.statusBar().showMessage(msg, timeout)

    def closeEvent(self, event):
        for panel in list(self._panels.values()):
            panel.shutdown()
        self._snapshot_db.close()
        event.accept()
