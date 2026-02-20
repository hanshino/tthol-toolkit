# Multi-Instance UI (Phase 1) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Auto-detect all running tthola.dat processes on startup, create one `CharacterPanel` tab per process, each connecting independently via manual HP input.

**Architecture:** Extract all per-character UI from `MainWindow` into a new `CharacterPanel` widget. `MainWindow` becomes a thin outer `QTabWidget` manager that calls `process_detector.detect_game_windows()` on startup and whenever the user clicks `[+]`. `ReaderWorker` gains a `pid` parameter and connects to a specific process instead of the first matching name.

**Tech Stack:** PySide6, psutil, pywin32 (win32gui, win32process), pymem

---

### Task 1: `gui/process_detector.py`

**Files:**
- Create: `gui/process_detector.py`
- Create: `tests/test_process_detector.py`

**Step 1: Write the failing test**

```python
# tests/test_process_detector.py
from unittest.mock import patch, MagicMock
from gui.process_detector import detect_game_windows

def test_returns_empty_when_no_game_running():
    with patch("gui.process_detector.psutil.process_iter", return_value=[]):
        result = detect_game_windows()
    assert result == []

def test_labels_windows_in_pid_order():
    proc1 = MagicMock(); proc1.info = {"pid": 100, "name": "tthola.dat"}
    proc2 = MagicMock(); proc2.info = {"pid": 200, "name": "tthola.dat"}
    with patch("gui.process_detector.psutil.process_iter", return_value=[proc1, proc2]):
        with patch("gui.process_detector._hwnd_for_pid", side_effect=[0x1000, 0x2000]):
            result = detect_game_windows()
    assert result == [(100, 0x1000, "視窗 1"), (200, 0x2000, "視窗 2")]

def test_ignores_other_processes():
    proc = MagicMock(); proc.info = {"pid": 999, "name": "notepad.exe"}
    with patch("gui.process_detector.psutil.process_iter", return_value=[proc]):
        result = detect_game_windows()
    assert result == []
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_process_detector.py -v
```
Expected: `ImportError` or `ModuleNotFoundError` — module does not exist yet.

**Step 3: Implement `gui/process_detector.py`**

```python
"""Enumerate running tthola.dat processes and their window handles."""
import psutil
import win32gui
import win32process

PROCESS_NAME = "tthola.dat"


def _hwnd_for_pid(target_pid: int) -> int:
    """Return the first visible top-level HWND belonging to target_pid, or 0."""
    found = 0

    def _callback(hwnd, _):
        nonlocal found
        if not win32gui.IsWindowVisible(hwnd):
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid == target_pid:
            found = hwnd
            return False   # stop enumeration
        return True

    win32gui.EnumWindows(_callback, None)
    return found


def detect_game_windows() -> list[tuple[int, int, str]]:
    """
    Return list of (pid, hwnd, label) for all running tthola.dat processes,
    sorted by pid. Labels are "視窗 1", "視窗 2", etc.
    """
    pids = sorted(
        proc.info["pid"]
        for proc in psutil.process_iter(["pid", "name"])
        if proc.info["name"] == PROCESS_NAME
    )
    result = []
    for i, pid in enumerate(pids, start=1):
        hwnd = _hwnd_for_pid(pid)
        result.append((pid, hwnd, f"視窗 {i}"))
    return result
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_process_detector.py -v
```
Expected: all 3 tests PASS.

**Step 5: Commit**

```bash
git add gui/process_detector.py tests/test_process_detector.py
git commit -m "feat: add process_detector to enumerate tthola.dat windows"
```

---

### Task 2: Modify `ReaderWorker` to connect by PID

**Files:**
- Modify: `gui/worker.py`

**Step 1: Change `__init__` signature**

Replace the existing `__init__` (line 51–59) with:

```python
def __init__(self, pid: int, parent=None):
    super().__init__(parent)
    self._pid = pid
    self._hp_value = None
    self._stop_event = threading.Event()
    self._scan_inventory = False
    self._scan_warehouse = False
    self._knowledge = load_knowledge()
    self._display_fields = get_display_fields(self._knowledge)
    self._item_db = load_item_db()
```

**Step 2: Replace `_connect_process`**

Replace the existing `_connect_process` method:

```python
def _connect_process(self):
    try:
        return pymem.Pymem(process_id=self._pid)
    except Exception as e:
        self.scan_error.emit(f"Cannot connect to PID {self._pid}: {e}")
        return None
```

Remove the `PROCESS_NAME = "tthola.dat"` constant at the top of the file — it is no longer used.

**Step 3: Verify import**

```bash
uv run python -c "from gui.worker import ReaderWorker; print('OK')"
```
Expected: `OK`.

**Step 4: Commit**

```bash
git add gui/worker.py
git commit -m "feat: worker connects to specific PID instead of first matching process name"
```

---

### Task 3: Create `gui/character_panel.py`

**Files:**
- Create: `gui/character_panel.py`

This widget contains everything that was previously inside `MainWindow.__init__`: op_bar + vitals strip + inner `QTabWidget`. It owns one `ReaderWorker` and emits signals upward for tab label updates and status bar messages.

**Step 1: Create `gui/character_panel.py`**

```python
"""
Per-character panel: op_bar + vitals strip + inner tabs.
One instance per detected game window.
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTabWidget, QFrame,
)
from PySide6.QtCore import Qt, Slot, Signal

from gui.worker import ReaderWorker
from gui.status_tab import StatusTab
from gui.inventory_tab import InventoryTab
from gui.warehouse_tab import WarehouseTab
from gui.snapshot_db import SnapshotDB
from gui.inventory_manager_tab import InventoryManagerTab
from gui.theme import badge_style, vital_html, fraction_html, GREEN, BLUE, AMBER, DIM


def _vsep() -> QFrame:
    f = QFrame()
    f.setObjectName("vitals_sep")
    f.setFrameShape(QFrame.Shape.VLine)
    f.setFixedWidth(1)
    f.setFixedHeight(20)
    return f


class CharacterPanel(QWidget):
    # Emitted when the character name becomes known — used to rename the outer tab.
    tab_label_changed = Signal(str)
    # Forwarded to MainWindow's status bar.
    status_message = Signal(str, int)   # message, timeout_ms

    def __init__(self, pid: int, hwnd: int, snapshot_db: SnapshotDB, parent=None):
        super().__init__(parent)
        self._pid = pid
        self._hwnd = hwnd          # reserved for Phase 2 OCR
        self._snapshot_db = snapshot_db
        self._pending_hp: int | None = None
        self._current_character: str = ""
        self._last_inventory: list[dict] = []
        self._last_warehouse: list[dict] = []

        self._worker = ReaderWorker(pid=pid, parent=self)
        self._worker.state_changed.connect(self._on_state_changed)
        self._worker.stats_updated.connect(self._on_stats_updated)
        self._worker.inventory_ready.connect(self._on_inventory_ready)
        self._worker.warehouse_ready.connect(self._on_warehouse_ready)
        self._worker.scan_error.connect(self._on_scan_error)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 6)
        root.setSpacing(6)

        # ── op_bar ────────────────────────────────────────────────────
        op_frame = QFrame()
        op_frame.setObjectName("op_bar")
        op_layout = QHBoxLayout(op_frame)
        op_layout.setContentsMargins(10, 6, 10, 6)
        op_layout.setSpacing(8)

        hp_lbl = QLabel("HP")
        hp_lbl.setStyleSheet(f"color: {GREEN}; font-weight: 600; font-size: 11px;")
        op_layout.addWidget(hp_lbl)

        self._hp_input = QLineEdit()
        self._hp_input.setPlaceholderText("current HP value")
        self._hp_input.setMaximumWidth(130)
        op_layout.addWidget(self._hp_input)

        self._connect_btn = QPushButton("Connect")
        self._connect_btn.setObjectName("primary_btn")
        self._connect_btn.clicked.connect(self._on_connect)
        op_layout.addWidget(self._connect_btn)

        self._state_indicator = QLabel("● DISCONNECTED")
        self._state_indicator.setStyleSheet(badge_style("DISCONNECTED"))
        op_layout.addWidget(self._state_indicator)

        op_layout.addStretch()

        self._relocate_btn = QPushButton("Relocate")
        self._relocate_btn.setEnabled(False)
        self._relocate_btn.clicked.connect(self._on_relocate)
        op_layout.addWidget(self._relocate_btn)

        root.addWidget(op_frame)

        # ── vitals strip ──────────────────────────────────────────────
        vitals_frame = QFrame()
        vitals_frame.setObjectName("vitals_strip")
        vitals_layout = QHBoxLayout(vitals_frame)
        vitals_layout.setContentsMargins(14, 6, 14, 6)
        vitals_layout.setSpacing(12)

        self._vitals_labels: dict[str, QLabel] = {}
        vitals_defs = ["Lv", "HP", "MP", "Weight", "Pos"]
        for i, key in enumerate(vitals_defs):
            lbl = QLabel(vital_html(key.upper(), "---"))
            lbl.setTextFormat(Qt.TextFormat.RichText)
            vitals_layout.addWidget(lbl)
            self._vitals_labels[key] = lbl
            if i < len(vitals_defs) - 1:
                vitals_layout.addWidget(_vsep())

        vitals_layout.addStretch()
        root.addWidget(vitals_frame)

        # ── inner tabs ────────────────────────────────────────────────
        tabs = QTabWidget()
        self._status_tab = StatusTab()
        self._inventory_tab = InventoryTab()
        self._warehouse_tab = WarehouseTab()
        self._manager_tab = InventoryManagerTab(self._snapshot_db)

        tabs.addTab(self._status_tab, "Status")
        tabs.addTab(self._inventory_tab, "Inventory")
        tabs.addTab(self._warehouse_tab, "Warehouse")
        tabs.addTab(self._manager_tab, "道具總覽")
        root.addWidget(tabs)

        self._inventory_tab.scan_requested.connect(self._on_inventory_scan)
        self._warehouse_tab.scan_requested.connect(self._on_warehouse_scan)
        self._inventory_tab.save_requested.connect(self._on_inventory_save)
        self._warehouse_tab.save_requested.connect(self._on_warehouse_save)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------
    @Slot()
    def _on_connect(self):
        hp_text = self._hp_input.text().strip()
        if not hp_text.isdigit():
            self.status_message.emit("Enter a valid HP value first", 3000)
            return
        self._connect_btn.setEnabled(False)
        self._worker.connect(int(hp_text))

    @Slot()
    def _on_relocate(self):
        hp_text = self._hp_input.text().strip()
        if not hp_text.isdigit():
            self.status_message.emit("Enter a valid HP value first", 3000)
            return
        self._pending_hp = int(hp_text)
        self._relocate_btn.setEnabled(False)
        self._worker.stop()

    @Slot(str)
    def _on_state_changed(self, state: str):
        self._state_indicator.setText(f"● {state}")
        self._state_indicator.setStyleSheet(badge_style(state))
        self._relocate_btn.setEnabled(state == "LOCATED")
        if state == "DISCONNECTED":
            if self._pending_hp is not None:
                hp = self._pending_hp
                self._pending_hp = None
                self._worker.connect(hp)
            else:
                self._connect_btn.setEnabled(True)

    @Slot(list)
    def _on_stats_updated(self, fields: list):
        data = dict(fields)
        name = data.get("角色名稱", "")
        if name and name != self._current_character:
            self._current_character = name
            self.tab_label_changed.emit(name)

        hp     = data.get("血量", "---")
        hp_max = data.get("最大血量", "---")
        mp     = data.get("真氣", "---")
        mp_max = data.get("最大真氣", "---")
        wt     = data.get("負重", "---")
        wt_max = data.get("最大負重", "---")
        lv     = data.get("等級", "---")
        x      = data.get("X座標", "---")
        y      = data.get("Y座標", "---")

        self._vitals_labels["Lv"].setText(vital_html("LV", lv))
        self._vitals_labels["HP"].setText(fraction_html("HP", hp, hp_max, GREEN))
        self._vitals_labels["MP"].setText(fraction_html("MP", mp, mp_max, BLUE))
        self._vitals_labels["Weight"].setText(fraction_html("WT", wt, wt_max, AMBER))
        self._vitals_labels["Pos"].setText(vital_html("POS", f"({x}, {y})"))

        self._status_tab.update_stats(fields)

    @Slot(list)
    def _on_inventory_ready(self, items: list):
        self._inventory_tab.populate(items)
        self._last_inventory = [{"item_id": iid, "qty": qty} for iid, qty, _ in items]

    @Slot(list)
    def _on_warehouse_ready(self, items: list):
        self._warehouse_tab.populate(items)
        self._last_warehouse = [{"item_id": iid, "qty": qty} for iid, qty, _ in items]

    @Slot(str)
    def _on_scan_error(self, msg: str):
        self.status_message.emit(f"[Error] {msg}", 5000)
        self._inventory_tab.set_scanning(False)
        self._warehouse_tab.set_scanning(False)

    @Slot()
    def _on_inventory_scan(self):
        self._inventory_tab.set_scanning(True)
        self._worker.request_inventory_scan()

    @Slot()
    def _on_warehouse_scan(self):
        self._warehouse_tab.set_scanning(True)
        self._worker.request_warehouse_scan()

    @Slot()
    def _on_inventory_save(self):
        if not self._current_character or not self._last_inventory:
            self.status_message.emit("No inventory data to save", 3000)
            return
        saved = self._snapshot_db.save_snapshot(
            self._current_character, "inventory", self._last_inventory
        )
        msg = "Snapshot saved" if saved else "No change detected, skipped"
        self.status_message.emit(msg, 3000)
        self._manager_tab.refresh()

    @Slot()
    def _on_warehouse_save(self):
        if not self._current_character or not self._last_warehouse:
            self.status_message.emit("No warehouse data to save", 3000)
            return
        saved = self._snapshot_db.save_snapshot(
            self._current_character, "warehouse", self._last_warehouse
        )
        msg = "Snapshot saved" if saved else "No change detected, skipped"
        self.status_message.emit(msg, 3000)
        self._manager_tab.refresh()

    def shutdown(self):
        """Stop the worker thread. Call before removing this panel."""
        self._worker.stop()
        self._worker.wait()
```

**Step 2: Verify import**

```bash
uv run python -c "from gui.character_panel import CharacterPanel; print('OK')"
```
Expected: `OK`.

**Step 3: Commit**

```bash
git add gui/character_panel.py
git commit -m "feat: add CharacterPanel with worker lifecycle and tab_label_changed signal"
```

---

### Task 4: Refactor `gui/main_window.py`

**Files:**
- Modify: `gui/main_window.py`

Replace the entire body of `MainWindow` with a thin outer `QTabWidget` manager.

**Step 1: Rewrite `gui/main_window.py`**

```python
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
```

**Step 2: Verify import**

```bash
uv run python -c "from gui.main_window import MainWindow; print('OK')"
```
Expected: `OK`.

**Step 3: Manual smoke test**

```bash
uv run gui_main.py
```

Test matrix:

| Scenario | Expected |
|---|---|
| 0 tthola.dat running | Single tab "請先開啟遊戲", no close button |
| 1 tthola.dat running | Single tab "視窗 1", close button hidden (count=1) |
| 2 tthola.dat running | Two tabs "視窗 1" / "視窗 2", each independently connectable |
| Connect one, enter HP | Tab label changes to character name after LOCATED |
| Click [+] after starting new game | New tab appears, existing tabs unaffected |
| Close a tab (2+ open) | Tab removed, worker stopped cleanly |
| Close last tab | Nothing happens (close button has no effect) |

**Step 4: Commit**

```bash
git add gui/main_window.py
git commit -m "refactor: MainWindow manages multiple CharacterPanel tabs via process_detector"
```

---

## Out of Scope

- OCR screenshot flow (Phase 2)
- Tab reorder by drag
- Persisting which PIDs were open across restarts
