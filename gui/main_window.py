"""
Main application window.

Layout:
  QHBoxLayout
  ├── QFrame#nav_sidebar  — vertical nav buttons (角色 / 道具 / 資料)
  └── QStackedWidget
      ├── page 0: character area  (QTabWidget, one tab per process)
      ├── page 1: InventoryManagerTab  (shared, cross-character)
      └── page 2: DataManagementTab   (character/snapshot/account management)
"""

from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QTabBar,
    QStatusBar,
    QStackedWidget,
    QFrame,
    QButtonGroup,
)
import subprocess

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QIcon
from pathlib import Path

from gui.character_panel import CharacterPanel
from gui.data_management_tab import DataManagementTab
from gui.inventory_manager_tab import InventoryManagerTab
from gui.process_detector import detect_game_windows
from gui.snapshot_db import SnapshotDB
from gui.theme import ThemeManager
from gui.i18n import t


def _get_version() -> str:
    """Return tag name if current commit is tagged, otherwise short commit hash."""
    try:
        tag = subprocess.run(
            ["git", "describe", "--exact-match", "--tags", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if tag.returncode == 0 and tag.stdout.strip():
            return tag.stdout.strip()
        rev = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return rev.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(t("window_title"))
        _icon_path = Path(__file__).parent.parent / "icon.png"
        if _icon_path.exists():
            self.setWindowIcon(QIcon(str(_icon_path)))
        self.setMinimumWidth(682)
        self.resize(880, 660)
        self._snapshot_db = SnapshotDB()
        self._panels: dict[int, CharacterPanel] = {}  # pid → panel

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Left nav sidebar ──────────────────────────────────────────
        sidebar = QFrame()
        sidebar.setObjectName("nav_sidebar")
        sidebar.setFixedWidth(72)
        nav_layout = QVBoxLayout(sidebar)
        nav_layout.setContentsMargins(0, 8, 0, 8)
        nav_layout.setSpacing(2)

        self._btn_chars = QPushButton(t("nav_characters"))
        self._btn_chars.setObjectName("nav_btn")
        self._btn_chars.setCheckable(True)
        self._btn_chars.setChecked(True)
        self._btn_chars.clicked.connect(lambda: self._switch_page(0))

        self._btn_inventory = QPushButton(t("nav_inventory"))
        self._btn_inventory.setObjectName("nav_btn")
        self._btn_inventory.setCheckable(True)
        self._btn_inventory.clicked.connect(lambda: self._switch_page(1))

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._nav_group.addButton(self._btn_chars, 0)
        self._nav_group.addButton(self._btn_inventory, 1)

        nav_layout.addWidget(self._btn_chars)
        nav_layout.addWidget(self._btn_inventory)

        self._btn_data_mgmt = QPushButton(t("nav_data_mgmt"))
        self._btn_data_mgmt.setObjectName("nav_btn")
        self._btn_data_mgmt.setCheckable(True)
        self._btn_data_mgmt.clicked.connect(lambda: self._switch_page(2))
        self._nav_group.addButton(self._btn_data_mgmt, 2)
        nav_layout.addWidget(self._btn_data_mgmt)

        nav_layout.addStretch()

        self._btn_theme = QPushButton()
        self._btn_theme.setObjectName("theme_toggle_btn")
        self._btn_theme.clicked.connect(self._on_toggle_theme)
        self._update_theme_btn_label()
        nav_layout.addWidget(self._btn_theme)

        root.addWidget(sidebar)

        # ── Main stacked area ─────────────────────────────────────────
        self._stack = QStackedWidget()
        root.addWidget(self._stack)

        # page 0: character tabs
        char_area = QWidget()
        char_layout = QVBoxLayout(char_area)
        char_layout.setContentsMargins(0, 0, 0, 0)
        char_layout.setSpacing(0)

        self._outer_tabs = QTabWidget()
        self._outer_tabs.setTabsClosable(False)

        refresh_btn = QPushButton("+")
        refresh_btn.setToolTip(t("refresh_tooltip"))
        refresh_btn.setFixedSize(34, 28)
        refresh_btn.setObjectName("refresh_btn")
        refresh_btn.clicked.connect(self._on_refresh)
        self._outer_tabs.setCornerWidget(refresh_btn, Qt.Corner.TopRightCorner)

        char_layout.addWidget(self._outer_tabs)
        self._stack.addWidget(char_area)

        # page 1: shared InventoryManagerTab
        self._manager_tab = InventoryManagerTab(self._snapshot_db)
        self._stack.addWidget(self._manager_tab)

        # page 2: DataManagementTab
        self._data_mgmt_tab = DataManagementTab(self._snapshot_db)
        self._stack.addWidget(self._data_mgmt_tab)
        self._data_mgmt_tab.status_message.connect(self._on_status_message)

        self.setStatusBar(QStatusBar())

        version_label = QLabel(f"rev: {_get_version()}")
        version_label.setObjectName("version_label")
        version_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.statusBar().addPermanentWidget(version_label)

        self._populate_tabs()

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------
    @Slot()
    def _switch_page(self, index: int):
        self._stack.setCurrentIndex(index)
        if index == 1:
            self._manager_tab.refresh()
        elif index == 2:
            self._data_mgmt_tab.refresh()

    # ------------------------------------------------------------------
    # Tab management
    # ------------------------------------------------------------------
    def _make_close_btn(self, panel: "CharacterPanel") -> QPushButton:
        """Return a styled X button that closes the given panel's tab."""
        btn = QPushButton("X")
        btn.setFixedSize(20, 20)
        btn.setObjectName("close_btn")
        btn.setToolTip(t("close_tab_tooltip"))
        btn.clicked.connect(lambda: self._close_panel(panel))
        return btn

    def _attach_close_btn(self, index: int, panel: "CharacterPanel"):
        """Place a custom close button on the right side of the tab at index."""
        btn = self._make_close_btn(panel)
        self._outer_tabs.tabBar().setTabButton(index, QTabBar.ButtonPosition.RightSide, btn)

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
            panel.snapshot_saved.connect(self._on_snapshot_saved)
            idx = self._outer_tabs.addTab(panel, label)
            self._attach_close_btn(idx, panel)
            panel.tab_label_changed.connect(
                lambda name, p=panel: self._outer_tabs.setTabText(self._outer_tabs.indexOf(p), name)
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
            lbl.setObjectName("placeholder_lbl")
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
            return
        index = self._outer_tabs.indexOf(panel)
        if index == -1:
            return
        self._outer_tabs.removeTab(index)
        pid_to_remove = next((pid for pid, p in self._panels.items() if p is panel), None)
        if pid_to_remove is not None:
            del self._panels[pid_to_remove]
        panel.shutdown()
        panel.deleteLater()

    @Slot(str, int)
    def _on_status_message(self, msg: str, timeout: int):
        self.statusBar().showMessage(msg, timeout)

    @Slot()
    def _on_snapshot_saved(self):
        """Refresh inventory manager when any character saves a snapshot."""
        self._manager_tab.refresh()
        self._data_mgmt_tab.refresh()

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

    def closeEvent(self, event):
        for panel in list(self._panels.values()):
            panel.shutdown()
        self._snapshot_db.close()
        event.accept()
