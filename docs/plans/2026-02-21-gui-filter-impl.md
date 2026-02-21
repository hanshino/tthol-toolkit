# GUI Filter Implementation Plan — 真氣 Secondary Scan Filter

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a collapsible 真氣 (MP) filter row to CharacterPanel so users can supply a second
known value to eliminate false-positive struct matches when HP alone is ambiguous.

**Architecture:** `locate_character` in `reader.py` already accepts `offset_filters`. This plan
wires an optional MP input in the GUI through `CharacterPanel` → `ReaderWorker` → `locate_character`.
No changes to `reader.py` are needed. Three files change: `i18n.py` (strings), `worker.py`
(accept + store `offset_filters`), `character_panel.py` (UI + read inputs).

**Tech Stack:** PySide6 (QFrame, QLabel, QLineEdit, QPushButton, QIntValidator), existing
`resolve_filters` from `reader.py`.

---

### Task 1: Add i18n strings for the filter row

**Files:**
- Modify: `gui/i18n.py`

**Step 1: Add the four new keys**

In `_STRINGS`, after the `"enter_valid_hp"` entry add:

```python
    "filter_toggle_show": "+ 進階",
    "filter_toggle_hide": "▲ 進階",
    "mp_filter_label": "真氣",
    "mp_filter_placeholder": "選填",
    "enter_valid_mp": "真氣篩選值必須為正整數",
```

**Step 2: Run existing tests to confirm nothing broke**

```bash
uv run pytest -v
```
Expected: all PASSED (no tests touch i18n directly, but this guards regressions)

**Step 3: Commit**

```bash
git add gui/i18n.py
git commit -m "feat: add i18n strings for MP filter row"
```

---

### Task 2: Extend ReaderWorker to accept and store offset_filters

**Files:**
- Modify: `gui/worker.py`
- Test: `tests/test_worker_filter.py` (create)

**Context:** `ReaderWorker.connect(hp_value)` is a public method called from the main thread.
`_locate(pm)` is the private helper that calls `locate_character`. Both need to be updated.

> **Warning:** `connect` is also a method name on `QObject`. The existing code already has this
> name and it works at runtime (it shadows the Qt method). Do NOT rename it — just add the
> `offset_filters` parameter with a default of `None`.

**Step 1: Write the failing test**

```python
# tests/test_worker_filter.py
import pytest
from unittest.mock import MagicMock, patch


def test_worker_stores_offset_filters():
    """connect() with offset_filters stores them for use in _locate."""
    from gui.worker import ReaderWorker

    worker = ReaderWorker(pid=9999)
    worker.connect(hp_value=287, offset_filters={-36: 7})
    assert worker._offset_filters == {-36: 7}
    worker.terminate()


def test_worker_stores_none_when_no_filters():
    """connect() without offset_filters defaults to None."""
    from gui.worker import ReaderWorker

    worker = ReaderWorker(pid=9999)
    worker.connect(hp_value=287)
    assert worker._offset_filters is None
    worker.terminate()


def test_worker_locate_passes_filters_to_locate_character():
    """_locate() forwards _offset_filters to locate_character."""
    from gui.worker import ReaderWorker

    worker = ReaderWorker(pid=9999)
    worker._hp_value = 287
    worker._offset_filters = {-36: 7}

    mock_pm = MagicMock()
    with patch("gui.worker.locate_character", return_value=0x1000) as mock_lc:
        result = worker._locate(mock_pm)

    mock_lc.assert_called_once_with(
        mock_pm, 287, worker._knowledge, {-36: 7}
    )
    assert result == 0x1000
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_worker_filter.py -v
```
Expected: FAIL — `connect() got unexpected keyword argument 'offset_filters'`

**Step 3: Update `worker.py`**

In `__init__`, add after `self._hp_value = None`:
```python
        self._offset_filters = None
```

Change `connect` signature:
```python
    def connect(self, hp_value: int, offset_filters=None):
        """Start the worker with a known HP value and optional offset filters."""
        self._hp_value = hp_value
        self._offset_filters = offset_filters
        self._stop_event.clear()
        if not self.isRunning():
            self.start()
```

Change `_locate`:
```python
    def _locate(self, pm):
        try:
            return locate_character(pm, self._hp_value, self._knowledge, self._offset_filters)
        except Exception as e:
            self.scan_error.emit(f"Scan failed: {e}")
            return None
```

**Step 4: Run tests**

```bash
uv run pytest tests/test_worker_filter.py -v
```
Expected: 3 PASSED

**Step 5: Run full suite**

```bash
uv run pytest -v
```
Expected: all PASSED

**Step 6: Commit**

```bash
git add gui/worker.py tests/test_worker_filter.py
git commit -m "feat: ReaderWorker.connect accepts offset_filters, passes to locate_character"
```

---

### Task 3: Add filter row UI to CharacterPanel

**Files:**
- Modify: `gui/character_panel.py`

**Context:** The op_bar is a `QHBoxLayout` inside `op_frame`. Current right side is:
`<stretch> [重新定位]`. We insert `[+ 進階]` after `[重新定位]`.

Below `op_frame` and above `vitals_frame` we insert a `filter_frame` that is hidden by default.

**Step 1: No new test for pure widget construction** — PySide6 widget tests are brittle without
a running QApplication. The existing connect/relocate flow is tested indirectly via worker tests.
Run the existing suite first:

```bash
uv run pytest -v
```
Expected: all PASSED

**Step 2: Add imports at top of `character_panel.py`**

Add `QIntValidator` to the PySide6 import line:
```python
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTabWidget, QFrame, QIntValidator,
)
```

Add `resolve_filters` import after the `from gui.i18n import t` line:
```python
from reader import resolve_filters
```

**Step 3: Add `_advanced_btn` and `_filter_row` to `__init__`**

In the op_bar section, *replace* this block:
```python
        op_layout.addStretch()

        self._relocate_btn = QPushButton(t("relocate"))
        self._relocate_btn.setEnabled(False)
        self._relocate_btn.clicked.connect(self._on_relocate)
        op_layout.addWidget(self._relocate_btn)

        root.addWidget(op_frame)
```

With:
```python
        op_layout.addStretch()

        self._relocate_btn = QPushButton(t("relocate"))
        self._relocate_btn.setEnabled(False)
        self._relocate_btn.clicked.connect(self._on_relocate)
        op_layout.addWidget(self._relocate_btn)

        self._advanced_btn = QPushButton(t("filter_toggle_show"))
        self._advanced_btn.setFlat(True)
        self._advanced_btn.clicked.connect(self._on_toggle_filter)
        op_layout.addWidget(self._advanced_btn)

        root.addWidget(op_frame)

        # ── filter row (hidden by default) ───────────────────────────
        filter_frame = QFrame()
        filter_frame.setObjectName("filter_row")
        filter_layout = QHBoxLayout(filter_frame)
        filter_layout.setContentsMargins(10, 4, 10, 4)
        filter_layout.setSpacing(8)

        mp_lbl = QLabel(t("mp_filter_label"))
        mp_lbl.setStyleSheet(f"color: {BLUE}; font-weight: 600; font-size: 11px;")
        filter_layout.addWidget(mp_lbl)

        self._mp_input = QLineEdit()
        self._mp_input.setPlaceholderText(t("mp_filter_placeholder"))
        self._mp_input.setMaximumWidth(130)
        self._mp_input.setValidator(QIntValidator(0, 2_147_483_647))
        filter_layout.addWidget(self._mp_input)

        filter_layout.addStretch()

        self._filter_frame = filter_frame
        self._filter_frame.setVisible(False)
        root.addWidget(self._filter_frame)
```

**Step 4: Add `_on_toggle_filter` slot**

Add after `_on_connect`:
```python
    @Slot()
    def _on_toggle_filter(self):
        visible = not self._filter_frame.isVisible()
        self._filter_frame.setVisible(visible)
        self._advanced_btn.setText(
            t("filter_toggle_hide") if visible else t("filter_toggle_show")
        )
```

**Step 5: Add `_build_offset_filters` helper**

Add as a private method:
```python
    def _build_offset_filters(self):
        """Read MP input and return resolved offset_filters dict, or None."""
        mp_text = self._mp_input.text().strip()
        if not mp_text:
            return None
        try:
            mp_val = int(mp_text)
        except ValueError:
            self.status_message.emit(t("enter_valid_mp"), 3000)
            return None
        knowledge = self._worker._knowledge
        return resolve_filters({"真氣": mp_val}, knowledge)
```

**Step 6: Update `_on_connect` to pass offset_filters**

Replace the last line of `_on_connect`:
```python
        self._worker.connect(int(hp_text))
```
With:
```python
        self._worker.connect(int(hp_text), offset_filters=self._build_offset_filters())
```

**Step 7: Update `_on_relocate` to pass offset_filters**

In `_on_relocate`, replace:
```python
        self._pending_hp = int(hp_text)
```
With:
```python
        self._pending_hp = (int(hp_text), self._build_offset_filters())
```

And in `_on_state_changed`, replace the reconnect block:
```python
            if self._pending_hp is not None:
                hp = self._pending_hp
                self._pending_hp = None
                self._worker.connect(hp)
```
With:
```python
            if self._pending_hp is not None:
                hp, offset_filters = self._pending_hp
                self._pending_hp = None
                self._worker.connect(hp, offset_filters=offset_filters)
```

Also update the type annotation on `_pending_hp` in `__init__`:
```python
        self._pending_hp: tuple[int, dict | None] | None = None
```

**Step 8: Run full suite**

```bash
uv run pytest -v
```
Expected: all PASSED

**Step 9: Commit**

```bash
git add gui/character_panel.py
git commit -m "feat: add collapsible MP filter row to CharacterPanel op_bar"
```

---

## Summary

After these 3 tasks the GUI supports:

1. User enters HP, optionally expands `+ 進階` row and enters 真氣 value
2. Clicking 連線 or 重新定位 passes `offset_filters={8: mp_val}` to the worker
3. Worker forwards it to `locate_character` — false positives with wrong MP are rejected
4. Auto-rescan reuses the same stored filters without user re-entry
5. Leaving the 真氣 field blank behaves identically to before (no regression)

**Test commands:**
```bash
uv run pytest -v                        # full suite
uv run pytest tests/test_worker_filter.py -v   # worker filter tests only
```
