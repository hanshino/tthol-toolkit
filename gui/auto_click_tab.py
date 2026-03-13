"""Auto-click tab for automated hero summoning."""

import ctypes
import time
from enum import Enum, auto

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QFormLayout,
    QPushButton,
    QRadioButton,
    QButtonGroup,
    QSpinBox,
    QDoubleSpinBox,
    QLabel,
    QFrame,
    QTextEdit,
)
from PySide6.QtCore import QTimer, QElapsedTimer, Slot, Signal

from gui.i18n import t

user32 = ctypes.windll.user32

WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001

# Game coordinates in 1200x900 DPI-scaled space (150% DPI)
# PySide6 marks the process as DPI-aware, so PostMessage needs physical pixels.
MERCHANT_COORDS = [
    (250, 500),  # Merchant 1
    (425, 500),  # Merchant 2
    (600, 500),  # Merchant 3
    (775, 500),  # Merchant 4
    (950, 500),  # Merchant 5
]
COLLECT_ALL_COORD = (968, 265)
DESTROY_ALL_COORD = (968, 314)

# Click spam interval (ms) — how fast clicks are sent during merchant phase
CLICK_SPAM_INTERVAL = 100


class ClickMode(Enum):
    COLLECT_DESTROY = auto()
    DESTROY_ONLY = auto()


class State(Enum):
    IDLE = auto()
    CLICKING_MERCHANT = auto()
    CLICKING_BUTTONS = auto()


def background_click(hwnd: int, x: int, y: int) -> None:
    """Single background click with 50ms delay between down/up."""
    if not hwnd:
        return
    lparam = (y << 16) | (x & 0xFFFF)
    user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
    time.sleep(0.05)
    user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lparam)


def background_double_click(hwnd: int, x: int, y: int) -> None:
    """Double click for UI buttons that need two click pairs to trigger."""
    background_click(hwnd, x, y)
    time.sleep(0.05)
    background_click(hwnd, x, y)


class AutoClickTab(QWidget):
    """Automated hero summoning click loop."""

    # Emitted when auto-click completes (max rounds reached) or stops.
    status_message = Signal(str, int)  # message, timeout_ms

    def __init__(self, hwnd: int = 0, parent=None):
        super().__init__(parent)
        self._hwnd = hwnd
        self._state = State.IDLE
        self._click_count = 0
        self._round_count = 0
        self._total_clicks = 0
        self._button_step = 0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)

        self._elapsed = QElapsedTimer()

        self._build_ui()
        self._update_status_labels()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # ── Target merchant selection ────────────────────────────────
        self._target_group = QGroupBox(t("ac_target"))
        target_layout = QHBoxLayout(self._target_group)
        target_layout.setSpacing(6)

        self._merchant_btn_group = QButtonGroup(self)
        self._merchant_btn_group.setExclusive(True)
        self._merchant_buttons: list[QPushButton] = []

        for i in range(5):
            btn = QPushButton(str(i + 1))
            btn.setCheckable(True)
            btn.setObjectName("merchant_btn")
            btn.setFixedSize(48, 36)
            self._merchant_btn_group.addButton(btn, i)
            self._merchant_buttons.append(btn)
            target_layout.addWidget(btn)

        self._merchant_buttons[0].setChecked(True)
        target_layout.addStretch()
        layout.addWidget(self._target_group)

        # ── Mode + Parameters side by side ───────────────────────────
        params_row = QHBoxLayout()
        params_row.setSpacing(10)

        # Mode group
        self._mode_group = QGroupBox(t("ac_mode"))
        mode_layout = QVBoxLayout(self._mode_group)
        self._mode_collect_destroy = QRadioButton(t("ac_mode_collect_destroy"))
        self._mode_destroy_only = QRadioButton(t("ac_mode_destroy_only"))
        self._mode_collect_destroy.setChecked(True)
        mode_layout.addWidget(self._mode_collect_destroy)
        mode_layout.addWidget(self._mode_destroy_only)
        params_row.addWidget(self._mode_group)

        # Parameters group
        self._params_group = QGroupBox(t("ac_parameters"))
        params_form = QFormLayout(self._params_group)
        params_form.setHorizontalSpacing(12)

        # Click duration (seconds)
        self._duration_spinbox = QDoubleSpinBox()
        self._duration_spinbox.setRange(1.0, 30.0)
        self._duration_spinbox.setValue(3.0)
        self._duration_spinbox.setSingleStep(0.5)
        self._duration_spinbox.setSuffix(" s")
        params_form.addRow(t("ac_click_duration"), self._duration_spinbox)

        # Max rounds
        self._rounds_spinbox = QSpinBox()
        self._rounds_spinbox.setRange(0, 9999)
        self._rounds_spinbox.setValue(0)
        self._rounds_spinbox.setSpecialValueText("∞")
        params_form.addRow(t("ac_max_rounds"), self._rounds_spinbox)

        params_row.addWidget(self._params_group)
        layout.addLayout(params_row)

        # ── Control buttons ──────────────────────────────────────────
        ctrl_layout = QHBoxLayout()
        ctrl_layout.setSpacing(8)

        self._start_btn = QPushButton(t("ac_start"))
        self._start_btn.setObjectName("ac_start_btn")
        self._start_btn.setEnabled(bool(self._hwnd))
        self._start_btn.clicked.connect(self._on_start)
        ctrl_layout.addWidget(self._start_btn)

        self._stop_btn = QPushButton(t("ac_stop"))
        self._stop_btn.setObjectName("ac_stop_btn")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop)
        ctrl_layout.addWidget(self._stop_btn)

        # Test click button for coordinate debugging
        self._test_btn = QPushButton(t("ac_test_click"))
        self._test_btn.setEnabled(bool(self._hwnd))
        self._test_btn.clicked.connect(self._on_test_click)
        ctrl_layout.addWidget(self._test_btn)

        ctrl_layout.addStretch()
        layout.addLayout(ctrl_layout)

        # ── Status display (vitals_strip style) ─────────────────────
        status_group = QGroupBox(t("ac_status"))
        status_layout = QHBoxLayout(status_group)
        status_layout.setContentsMargins(14, 6, 14, 6)
        status_layout.setSpacing(12)

        self._round_label = QLabel("---")
        self._round_label.setObjectName("ac_status_value")
        self._click_label = QLabel("---")
        self._click_label.setObjectName("ac_status_value")
        self._total_label = QLabel("---")
        self._total_label.setObjectName("ac_status_value")

        for label_text, value_label in [
            (t("ac_round"), self._round_label),
            (t("ac_clicks"), self._click_label),
            (t("ac_total"), self._total_label),
        ]:
            key_lbl = QLabel(label_text)
            key_lbl.setObjectName("ac_status_key")
            status_layout.addWidget(key_lbl)
            status_layout.addWidget(value_label)
            # Add separator except after last
            if value_label is not self._total_label:
                sep = QFrame()
                sep.setObjectName("vitals_sep")
                sep.setFrameShape(QFrame.Shape.VLine)
                sep.setFixedWidth(1)
                sep.setFixedHeight(20)
                status_layout.addWidget(sep)

        status_layout.addStretch()
        layout.addWidget(status_group)

        # ── Log area ────────────────────────────────────────────────
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(150)
        self._log.setObjectName("ac_log")
        layout.addWidget(self._log)

    # ------------------------------------------------------------------
    # Slot handlers
    # ------------------------------------------------------------------
    @Slot()
    def _on_start(self):
        if not self._hwnd:
            return
        self._state = State.CLICKING_MERCHANT
        self._click_count = 0
        self._round_count = 0
        self._total_clicks = 0
        self._button_step = 0

        self._set_running(True)
        self._elapsed.start()
        self._timer.start(CLICK_SPAM_INTERVAL)
        self._update_status_labels()
        duration = self._duration_spinbox.value()
        self._log_msg(
            f"Started: merchant={self._merchant_btn_group.checkedId() + 1}, "
            f"duration={duration}s, hwnd=0x{self._hwnd:08X}"
        )

    @Slot()
    def _on_stop(self):
        self._timer.stop()
        self._state = State.IDLE
        self._set_running(False)
        self._update_status_labels()
        self._log_msg("Stopped")

    @Slot()
    def _on_test_click(self):
        """Send a single test click to the selected merchant."""
        if not self._hwnd:
            return
        merchant_idx = self._merchant_btn_group.checkedId()
        if merchant_idx < 0:
            merchant_idx = 0
        x, y = MERCHANT_COORDS[merchant_idx]
        background_click(self._hwnd, x, y)
        self._log_msg(f"TEST click merchant {merchant_idx + 1} at ({x}, {y})")

    @Slot()
    def _on_tick(self):
        if self._state == State.CLICKING_MERCHANT:
            self._tick_merchant()
        elif self._state == State.CLICKING_BUTTONS:
            self._tick_buttons()

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------
    def _tick_merchant(self):
        merchant_idx = self._merchant_btn_group.checkedId()
        if merchant_idx < 0:
            merchant_idx = 0
        x, y = MERCHANT_COORDS[merchant_idx]
        background_click(self._hwnd, x, y)
        self._click_count += 1
        self._total_clicks += 1

        # Check if duration exceeded
        duration_ms = int(self._duration_spinbox.value() * 1000)
        elapsed = self._elapsed.elapsed()
        if elapsed >= duration_ms:
            self._log_msg(
                f"Clicked merchant {merchant_idx + 1} x{self._click_count} in {elapsed / 1000:.1f}s"
            )
            self._state = State.CLICKING_BUTTONS
            self._button_step = 0
            # Slow down timer for button phase
            self._timer.setInterval(500)
        elif self._click_count % 10 == 0:
            # Log every 10 clicks to avoid spam
            self._update_status_labels()

    def _tick_buttons(self):
        mode = self._get_mode()

        if mode == ClickMode.COLLECT_DESTROY:
            # Step 0: click Collect All
            # Step 1: wait
            # Step 2: click Destroy All
            # Step 3: wait -> round complete
            if self._button_step == 0:
                background_double_click(self._hwnd, *COLLECT_ALL_COORD)
                self._log_msg(f"Double-click Collect All at {COLLECT_ALL_COORD}")
            elif self._button_step == 2:
                background_double_click(self._hwnd, *DESTROY_ALL_COORD)
                self._log_msg(f"Double-click Destroy All at {DESTROY_ALL_COORD}")
            elif self._button_step >= 3:
                self._finish_round()
                return
        else:
            # Destroy only
            # Step 0: click Destroy All
            # Step 1: wait -> round complete
            if self._button_step == 0:
                background_double_click(self._hwnd, *DESTROY_ALL_COORD)
                self._log_msg(f"Double-click Destroy All at {DESTROY_ALL_COORD}")
            elif self._button_step >= 1:
                self._finish_round()
                return

        self._button_step += 1

    def _finish_round(self):
        self._round_count += 1
        self._click_count = 0
        self._update_status_labels()
        self._log_msg(f"Round {self._round_count} complete")

        max_rounds = self._rounds_spinbox.value()
        if max_rounds > 0 and self._round_count >= max_rounds:
            self._on_stop()
            self.status_message.emit(
                f"Auto-click completed: {self._round_count} rounds, {self._total_clicks} clicks",
                5000,
            )
            return

        # Reset for next round
        self._state = State.CLICKING_MERCHANT
        self._button_step = 0
        self._elapsed.restart()
        self._timer.setInterval(CLICK_SPAM_INTERVAL)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _log_msg(self, msg: str):
        """Append a timestamped message to the log area."""
        from datetime import datetime

        ts = datetime.now().strftime("%H:%M:%S")
        self._log.append(f"[{ts}] {msg}")
        # Auto-scroll to bottom
        self._log.verticalScrollBar().setValue(self._log.verticalScrollBar().maximum())

    def _get_mode(self) -> ClickMode:
        if self._mode_destroy_only.isChecked():
            return ClickMode.DESTROY_ONLY
        return ClickMode.COLLECT_DESTROY

    def _set_running(self, running: bool):
        self._start_btn.setEnabled(not running and bool(self._hwnd))
        self._stop_btn.setEnabled(running)
        self._test_btn.setEnabled(not running and bool(self._hwnd))
        self._target_group.setEnabled(not running)
        self._mode_group.setEnabled(not running)
        self._params_group.setEnabled(not running)

    def _update_status_labels(self):
        max_rounds = self._rounds_spinbox.value()
        rounds_str = str(max_rounds) if max_rounds > 0 else "∞"

        if self._state == State.IDLE and self._total_clicks == 0:
            self._round_label.setText("---")
            self._click_label.setText("---")
            self._total_label.setText("---")
        else:
            self._round_label.setText(f"{self._round_count} / {rounds_str}")
            elapsed_s = self._elapsed.elapsed() / 1000 if self._elapsed.isValid() else 0
            self._click_label.setText(f"{elapsed_s:.1f}s")
            self._total_label.setText(str(self._total_clicks))

    def shutdown(self):
        """Stop the timer and reset state. Call before removing this tab."""
        self._timer.stop()
        self._state = State.IDLE
        self._click_count = 0
        self._round_count = 0
        self._total_clicks = 0
        self._button_step = 0
