"""Status tab: character stats grouped into BASIC / ATTRIBUTES / COMBAT."""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QProgressBar, QGridLayout,
)
from PySide6.QtCore import Qt


class StatusTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # ── BASIC: HP / MP / Weight with colored progress bars ────────
        basic_box = QGroupBox("BASIC")
        basic_grid = QGridLayout(basic_box)
        basic_grid.setHorizontalSpacing(10)
        basic_grid.setVerticalSpacing(8)

        self._bars = {}
        self._bar_labels = {}
        bar_defs = [
            ("HP",     "hp_bar"),
            ("MP",     "mp_bar"),
            ("Weight", "weight_bar"),
        ]
        for row, (key, obj_name) in enumerate(bar_defs):
            key_lbl = QLabel(key)
            bar = QProgressBar()
            bar.setObjectName(obj_name)
            bar.setTextVisible(False)
            bar.setFixedHeight(10)
            val_lbl = QLabel("---")
            val_lbl.setMinimumWidth(160)
            basic_grid.addWidget(key_lbl, row, 0)
            basic_grid.addWidget(bar,     row, 1)
            basic_grid.addWidget(val_lbl, row, 2)
            self._bars[key] = bar
            self._bar_labels[key] = val_lbl

        layout.addWidget(basic_box)

        # ── ATTRIBUTES + COMBAT side by side ──────────────────────────
        row_layout = QHBoxLayout()
        row_layout.setSpacing(10)

        attr_box = QGroupBox("ATTRIBUTES")
        attr_grid = QGridLayout(attr_box)
        attr_grid.setHorizontalSpacing(16)
        attr_grid.setVerticalSpacing(8)
        self._attr_labels = {}
        for r, name in enumerate(["外功", "根骨", "技巧", "魅力值"]):
            attr_grid.addWidget(QLabel(name), r, 0)
            val = QLabel("---")
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            attr_grid.addWidget(val, r, 1)
            self._attr_labels[name] = val
        row_layout.addWidget(attr_box)

        combat_box = QGroupBox("COMBAT")
        combat_grid = QGridLayout(combat_box)
        combat_grid.setHorizontalSpacing(16)
        combat_grid.setVerticalSpacing(8)
        self._combat_labels = {}
        combat_fields = ["物攻", "物攻(基礎?)", "內勁", "防禦", "護勁", "命中", "閃躲"]
        for i, name in enumerate(combat_fields):
            r, c = divmod(i, 2)
            combat_grid.addWidget(QLabel(name), r, c * 2)
            val = QLabel("---")
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            combat_grid.addWidget(val, r, c * 2 + 1)
            self._combat_labels[name] = val
        row_layout.addWidget(combat_box)

        layout.addLayout(row_layout)
        layout.addStretch()

    def update_stats(self, fields: list):
        """Update all displayed values. fields = list of (name, value)."""
        data = {name: value for name, value in fields}

        for key, cur_key, max_key in [
            ("HP",     "血量",   "最大血量"),
            ("MP",     "真氣",   "最大真氣"),
            ("Weight", "負重",   "最大負重"),
        ]:
            cur = data.get(cur_key, 0)
            mx  = data.get(max_key)
            if not isinstance(mx, int) or mx <= 0:
                mx = 1
            bar = self._bars[key]
            bar.setMaximum(mx)
            bar.setValue(max(0, cur) if isinstance(cur, int) else 0)
            self._bar_labels[key].setText(f"{cur} / {mx}")

        for name, lbl in self._attr_labels.items():
            lbl.setText(str(data.get(name, "---")))

        for name, lbl in self._combat_labels.items():
            lbl.setText(str(data.get(name, "---")))
