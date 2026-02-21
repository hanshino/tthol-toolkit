# GUI Filter Design — 真氣 Secondary Scan Filter

**Date:** 2026-02-21
**Status:** Approved

## Problem

When the character's HP value is small (e.g., HP=287 for a Lv.7 character), many unrelated
memory locations happen to contain that value. `verify_structure()` scoring alone is not strict
enough to reject all false positives.

The CLI already supports `--filter 真氣=150` to supply a second known value. The GUI needs the
same capability.

## Decision

Add a single dedicated **真氣 (MP) filter input** hidden behind a collapsible "Advanced" row in
`CharacterPanel`. A second matching value at the correct relative offset is sufficient to
uniquely identify the correct struct.

Scope is intentionally limited to 真氣 only (YAGNI). General-purpose multi-field filter UI is
not needed.

---

## UI Layout

### op_bar (unchanged except for new toggle button)

```
┌─ op_bar ──────────────────────────────────────────────────────────────────┐
│ HP  [___________]  [連線]  ● 未連線       <stretch>  [重新定位]  [+ 進階]  │
└───────────────────────────────────────────────────────────────────────────┘
```

- `+ 進階` is a `QPushButton` (flat/text style, no border) placed at the far right of `op_layout`.
- Clicking toggles the filter row and updates label: `+ 進階` ↔ `▲ 進階`.

### filter_row (hidden by default)

```
┌─ filter_row ──────────────────────────────────────────────────────────────┐
│ 真氣  [___________]   (optional — leave blank to skip)                    │
└───────────────────────────────────────────────────────────────────────────┘
```

- Implemented as a `QFrame` with `setVisible(False)` on construction.
- `真氣` is a `QLabel` (not placeholder-only) — consistent with the HP row's `QLabel("HP")`.
- The MP `QLineEdit` uses `QIntValidator(0, 2_147_483_647)` to restrict input to non-negative
  integers. Empty string = no filter applied.

---

## Data Flow

1. User fills HP (required). Optionally expands filter row and enters a 真氣 value.
2. `_on_connect()` / `_on_relocate()` read both inputs.
   - If MP input is non-empty and passes `QIntValidator`, build:
     `offset_filters = resolve_filters({"真氣": mp_val}, knowledge)`
   - Otherwise `offset_filters = None`.
3. `worker.connect(hp_value, offset_filters)` stores `_offset_filters` on the worker.
4. `worker._locate(pm)` calls:
   `locate_character(pm, self._hp_value, self._knowledge, self._offset_filters)`
5. On auto-rescan (score < 0.8 threshold), the stored `_offset_filters` are reused automatically
   — no re-entry required from the user.

---

## Files Changed

| File | Change |
|------|--------|
| `gui/character_panel.py` | Add `_advanced_btn` toggle; add `_filter_row` QFrame with `QLabel("真氣")` + `_mp_input` QLineEdit (QIntValidator); update `_on_connect` and `_on_relocate` to extract MP value and pass `offset_filters` to worker |
| `gui/worker.py` | `connect(hp_value, offset_filters=None)` stores `self._offset_filters`; `_locate(pm)` passes it to `locate_character` |
| `gui/i18n.py` | Add keys: `filter_toggle_show` (`"+ 進階"`), `filter_toggle_hide` (`"▲ 進階"`), `mp_filter_label` (`"真氣"`), `mp_filter_placeholder` (`"選填"`) |
| `reader.py` | No changes — `offset_filters` parameter already exists |

---

## UX Notes (from ui-ux-pro-max review)

- **Visible label required (High):** `真氣` must be a `QLabel`, not placeholder-only.
- **Input validation:** `QIntValidator` on MP field prevents non-numeric entry; mirrors existing
  `hp_text.isdigit()` guard on HP field.
- **Toggle feedback:** `+ 進階` / `▲ 進階` text swap provides clear expand/collapse state.
- **No animation required:** `QFrame.setVisible()` instant toggle is acceptable for a utility tool.

---

## Out of Scope

- General-purpose multi-field filter (YAGNI)
- Persistence of filter values across sessions
- Validation error UI beyond the existing status bar message pattern
- Auto-expand on failed connect (can be revisited if UX feedback requests it)
