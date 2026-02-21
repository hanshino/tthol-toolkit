"""Data Management tab: master-detail view for characters, snapshots, and accounts."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.i18n import t
from gui.snapshot_db import SnapshotDB
from gui.theme import BORDER, DIM


def _section_label(text: str) -> QLabel:
    """Create a small caps section header label."""
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color: {DIM}; font-size: 8pt; font-weight: 600; letter-spacing: 1px;")
    return lbl


class _CharDetailPanel(QWidget):
    """Detail panel showing account assignment, snapshot history, and delete options."""

    data_changed = Signal()
    status_message = Signal(str, int)

    def __init__(self, db: SnapshotDB, parent=None):
        super().__init__(parent)
        self._db = db
        self._character: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # ── Title ────────────────────────────────────────────────────────
        self._title_lbl = QLabel("")
        self._title_lbl.setStyleSheet("font-weight: bold; font-size: 13pt;")
        layout.addWidget(self._title_lbl)

        # ── Account section ───────────────────────────────────────────────
        layout.addWidget(_section_label(t("account_label")))

        acct_row = QHBoxLayout()
        self._acct_combo = QComboBox()
        self._acct_combo.setMinimumWidth(160)
        acct_row.addWidget(self._acct_combo)

        self._create_acct_btn = QPushButton(t("create_account"))
        acct_row.addWidget(self._create_acct_btn)
        acct_row.addStretch()
        layout.addLayout(acct_row)

        # ── Snapshot history section ──────────────────────────────────────
        layout.addWidget(_section_label(t("snapshot_history")))

        self._snap_table = QTableWidget(0, 4)
        self._snap_table.setHorizontalHeaderLabels(
            [t("mgr_col_source"), t("mgr_col_snapshot_time"), t("mgr_col_qty"), ""]
        )
        hdr = self._snap_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self._snap_table.setColumnWidth(3, 72)
        self._snap_table.verticalHeader().setVisible(False)
        self._snap_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._snap_table.setWordWrap(False)
        layout.addWidget(self._snap_table)

        # ── Separator ────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {BORDER}; border: none;")
        layout.addWidget(sep)

        # ── Delete character button ───────────────────────────────────────
        del_row = QHBoxLayout()
        self._delete_char_btn = QPushButton(t("delete_character"))
        self._delete_char_btn.setObjectName("delete_btn")
        del_row.addWidget(self._delete_char_btn)
        del_row.addStretch()
        layout.addLayout(del_row)

        layout.addStretch()

        # ── Connect signals ───────────────────────────────────────────────
        self._acct_combo.currentIndexChanged.connect(self._on_account_changed)
        self._create_acct_btn.clicked.connect(self._on_create_account)
        self._delete_char_btn.clicked.connect(self._on_delete_character)

    # ── Public API ────────────────────────────────────────────────────────

    def load(self, character: str) -> None:
        """Load data for the given character."""
        self._character = character
        self._title_lbl.setText(character)
        self._refresh_combo()
        self._refresh_table()

    # ── Private helpers ───────────────────────────────────────────────────

    def _refresh_combo(self) -> None:
        self._acct_combo.blockSignals(True)
        self._acct_combo.clear()
        self._acct_combo.addItem(t("no_account"), userData=None)

        accounts = self._db.list_accounts()
        for acct in accounts:
            self._acct_combo.addItem(acct["name"], userData=acct["id"])

        # Select current account
        current = self._db.get_character_account(self._character) if self._character else None
        if current is not None:
            for i in range(self._acct_combo.count()):
                if self._acct_combo.itemData(i) == current["id"]:
                    self._acct_combo.setCurrentIndex(i)
                    break
        else:
            self._acct_combo.setCurrentIndex(0)

        self._acct_combo.blockSignals(False)

    def _refresh_table(self) -> None:
        if self._character is None:
            return
        _src = {
            "inventory": t("source_inventory"),
            "warehouse": t("source_warehouse"),
        }
        snapshots = self._db.list_all_snapshots(self._character)
        self._snap_table.setRowCount(len(snapshots))
        for row, snap in enumerate(snapshots):
            src_label = _src.get(snap["source"], snap["source"])
            self._snap_table.setItem(row, 0, QTableWidgetItem(src_label))
            self._snap_table.setItem(row, 1, QTableWidgetItem(snap["scanned_at"]))
            self._snap_table.setItem(row, 2, QTableWidgetItem(str(snap["item_count"])))

            # Wrap button in a container widget so it doesn't bleed over cell borders
            del_btn = QPushButton(t("delete_snapshot"))
            del_btn.setObjectName("delete_btn")
            snap_id = snap["id"]
            del_btn.clicked.connect(
                lambda checked=False, sid=snap_id: self._on_delete_snapshot(sid)
            )
            cell_widget = QWidget()
            cell_layout = QHBoxLayout(cell_widget)
            cell_layout.setContentsMargins(4, 3, 4, 3)
            cell_layout.setSpacing(0)
            cell_layout.addWidget(del_btn)
            self._snap_table.setCellWidget(row, 3, cell_widget)

        self._snap_table.resizeRowsToContents()
        # resizeRowsToContents ignores cellWidgets; ensure each row is tall
        # enough to fully display the delete button (min-height 24 + padding).
        min_row_height = 60
        for row in range(self._snap_table.rowCount()):
            if self._snap_table.rowHeight(row) < min_row_height:
                self._snap_table.setRowHeight(row, min_row_height)

    # ── Slots ─────────────────────────────────────────────────────────────

    def _on_account_changed(self, index: int) -> None:
        if self._character is None:
            return
        account_id = self._acct_combo.itemData(index)
        if account_id is None:
            self._db.remove_character_account(self._character)
            self.status_message.emit(t("no_account"), 3000)
        else:
            self._db.set_character_account(self._character, account_id)
            name = self._acct_combo.itemText(index)
            self.status_message.emit(t("account_assigned", name=name), 3000)
        self.data_changed.emit()

    def _on_create_account(self) -> None:
        if self._character is None:
            return
        name, ok = QInputDialog.getText(
            self,
            t("enter_account_name"),
            t("new_account_placeholder"),
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        account_id = self._db.create_account(name)
        self._db.set_character_account(self._character, account_id)
        self._refresh_combo()
        self.status_message.emit(t("account_assigned", name=name), 3000)
        self.data_changed.emit()

    def _on_delete_snapshot(self, snapshot_id: int) -> None:
        reply = QMessageBox.warning(
            self,
            t("delete_snapshot"),
            t("confirm_delete_snapshot"),
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return
        self._db.delete_snapshot(snapshot_id)
        self._refresh_table()
        self.status_message.emit(t("deleted_snapshot"), 3000)
        self.data_changed.emit()

    def _on_delete_character(self) -> None:
        if self._character is None:
            return
        reply = QMessageBox.warning(
            self,
            t("delete_character"),
            t("confirm_delete_character", character=self._character),
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return
        character = self._character
        self._db.delete_character(character)
        self.status_message.emit(t("deleted_character", character=character), 4000)
        self.data_changed.emit()


class DataManagementTab(QWidget):
    """Master-detail tab for managing characters, snapshots, and accounts."""

    status_message = Signal(str, int)

    def __init__(self, db: SnapshotDB, parent=None):
        super().__init__(parent)
        self._db = db
        self._selected_character: str | None = None

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.addWidget(splitter)

        # ── Left panel ────────────────────────────────────────────────────
        left = QFrame()
        left.setObjectName("mgmt_left_panel")
        left.setFixedWidth(220)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 8, 0, 8)
        left_layout.setSpacing(4)

        char_header = QLabel(t("mgmt_characters_header"))
        char_header.setStyleSheet(
            "font-size: 8pt; font-weight: 700; letter-spacing: 1px; padding: 4px 10px 2px 10px;"
        )
        left_layout.addWidget(char_header)

        self._char_list = QListWidget()
        self._char_list.setObjectName("mgmt_char_list")
        left_layout.addWidget(self._char_list)

        acct_header = QLabel(t("mgmt_accounts_header"))
        acct_header.setStyleSheet(
            "font-size: 8pt; font-weight: 700; letter-spacing: 1px; padding: 8px 10px 2px 10px;"
        )
        left_layout.addWidget(acct_header)

        self._acct_tree = QTreeWidget()
        self._acct_tree.setObjectName("mgmt_acct_tree")
        self._acct_tree.setHeaderHidden(True)
        self._acct_tree.setFixedHeight(150)
        left_layout.addWidget(self._acct_tree)

        splitter.addWidget(left)

        # ── Right stack ───────────────────────────────────────────────────
        self._right_stack = QStackedWidget()

        # index 0: placeholder
        placeholder = QWidget()
        ph_layout = QVBoxLayout(placeholder)
        ph_lbl = QLabel(t("mgmt_select_character"))
        ph_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ph_layout.addWidget(ph_lbl)
        self._right_stack.addWidget(placeholder)

        # index 1: detail panel in scroll area
        self._detail_panel = _CharDetailPanel(self._db)
        scroll = QScrollArea()
        scroll.setWidget(self._detail_panel)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._right_stack.addWidget(scroll)

        splitter.addWidget(self._right_stack)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        # ── Connect signals ───────────────────────────────────────────────
        self._char_list.currentRowChanged.connect(self._on_char_selected)
        self._detail_panel.data_changed.connect(self.refresh)
        self._detail_panel.status_message.connect(self.status_message)

        # Initial load
        self.refresh()

    # ── Public API ────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Reload all data from DB. Re-selects previously selected character if still present."""
        self._rebuild_char_list()
        self._rebuild_acct_tree()

    # ── Private helpers ───────────────────────────────────────────────────

    def _rebuild_char_list(self) -> None:
        self._char_list.blockSignals(True)
        self._char_list.clear()

        characters = self._db.list_characters()
        reselect_row = -1
        for i, c in enumerate(characters):
            account_name = c["account_name"] or t("no_account")
            item = QListWidgetItem(f"{c['character']}  ·  {account_name}")
            item.setData(Qt.ItemDataRole.UserRole, c["character"])
            self._char_list.addItem(item)
            if c["character"] == self._selected_character:
                reselect_row = i

        self._char_list.blockSignals(False)

        # setCurrentRow is intentionally called after blockSignals(False) so that
        # _on_char_selected fires and reloads the detail panel with fresh data.
        if reselect_row >= 0:
            self._char_list.setCurrentRow(reselect_row)
            self._right_stack.setCurrentIndex(1)
        else:
            self._selected_character = None
            self._char_list.setCurrentRow(-1)
            self._right_stack.setCurrentIndex(0)

    def _rebuild_acct_tree(self) -> None:
        self._acct_tree.clear()
        accounts = self._db.list_accounts()
        characters = self._db.list_characters()

        # Group characters by account_id
        acct_chars: dict[int, list[str]] = {}
        for c in characters:
            if c["account_id"] is not None:
                acct_chars.setdefault(c["account_id"], []).append(c["character"])

        for acct in accounts:
            chars = acct_chars.get(acct["id"], [])
            root = QTreeWidgetItem([f"{acct['name']}  ({len(chars)})"])
            for char in chars:
                root.addChild(QTreeWidgetItem([char]))
            self._acct_tree.addTopLevelItem(root)

        self._acct_tree.expandAll()

    def _on_char_selected(self, row: int) -> None:
        if row < 0:
            self._selected_character = None
            self._right_stack.setCurrentIndex(0)
            return

        item = self._char_list.item(row)
        if item is None:
            self._selected_character = None
            self._right_stack.setCurrentIndex(0)
            return

        character = item.data(Qt.ItemDataRole.UserRole)
        self._selected_character = character
        self._detail_panel.load(character)
        self._right_stack.setCurrentIndex(1)
