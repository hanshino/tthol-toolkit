"""
Main application window.

Layout:
  Outer QTabWidget — one tab per detected tthola.dat process.
    Each tab: CharacterPanel (op_bar + vitals + inner tabs)
  Corner widget: [+] button to re-scan for new game windows.
"""
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout,
    QLabel, QPushButton, QTabWidget, QStatusBar,
)
from PySide6.QtCore import Slot

from gui.character_panel import CharacterPanel
from gui.process_detector import detect_game_windows
from gui.snapshot_db import SnapshotDB
from gui.theme import DARK_QSS, DIM


_PLACEHOLDER_LABEL = "請先開啟遊戲"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tthol Reader")
        self.setMinimumWidth(580)
        self.setStyleSheet(DARK_QSS)

        self._snapshot_db = SnapshotDB()
        self._panels: dict[int, CharacterPanel] = {}   # pid → panel

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._outer_tabs = QTabWidget()
        self._outer_tabs.setTabsClosable(True)
        self._outer_tabs.tabCloseRequested.connect(self._on_close_tab)

        refresh_btn = QPushButton("+")
        refresh_btn.setToolTip("Scan for new game windows")
        refresh_btn.setFixedSize(28, 28)
        refresh_btn.clicked.connect(self._on_refresh)
        self._outer_tabs.setCornerWidget(refresh_btn)

        root.addWidget(self._outer_tabs)
        self.setStatusBar(QStatusBar())

        self._populate_tabs()

    # ------------------------------------------------------------------
    # Tab management
    # ------------------------------------------------------------------
    def _populate_tabs(self):
        """Detect game windows and add a tab for each new PID found."""
        windows = detect_game_windows()
        if not windows and not self._panels:
            self._show_placeholder()
            return
        for pid, hwnd, label in windows:
            if pid in self._panels:
                continue
            self._remove_placeholder()
            panel = CharacterPanel(pid=pid, hwnd=hwnd, snapshot_db=self._snapshot_db)
            panel.status_message.connect(self._on_status_message)
            idx = self._outer_tabs.addTab(panel, label)
            # Capture idx in closure so the lambda updates the correct tab.
            panel.tab_label_changed.connect(
                lambda name, i=idx: self._outer_tabs.setTabText(i, name)
            )
            self._panels[pid] = panel

    def _show_placeholder(self):
        """Show a single informational tab when no game window is found."""
        if self._outer_tabs.count() == 0:
            lbl = QLabel(_PLACEHOLDER_LABEL)
            lbl.setStyleSheet(f"color: {DIM}; font-size: 14px;")
            lbl.setProperty("is_placeholder", True)
            self._outer_tabs.addTab(lbl, _PLACEHOLDER_LABEL)
            self._outer_tabs.setTabsClosable(False)

    def _remove_placeholder(self):
        """Remove placeholder tab if it is present."""
        for i in range(self._outer_tabs.count()):
            w = self._outer_tabs.widget(i)
            if w and w.property("is_placeholder"):
                self._outer_tabs.removeTab(i)
                break
        self._outer_tabs.setTabsClosable(True)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------
    @Slot()
    def _on_refresh(self):
        self._populate_tabs()

    @Slot(int)
    def _on_close_tab(self, index: int):
        if self._outer_tabs.count() <= 1:
            return   # prevent closing the last tab
        widget = self._outer_tabs.widget(index)
        self._outer_tabs.removeTab(index)
        pid_to_remove = next(
            (pid for pid, panel in self._panels.items() if panel is widget), None
        )
        if pid_to_remove is not None:
            del self._panels[pid_to_remove]
            widget.shutdown()
            widget.deleteLater()

    @Slot(str, int)
    def _on_status_message(self, msg: str, timeout: int):
        self.statusBar().showMessage(msg, timeout)

    def closeEvent(self, event):
        for panel in list(self._panels.values()):
            panel.shutdown()
        self._snapshot_db.close()
        event.accept()
