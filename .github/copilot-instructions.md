# Copilot Instructions

This repository is a **read-only** Windows memory reader for the Tthol game. It reads character, inventory, and warehouse data from the running `tthola.dat` process and should never write to game memory.

## Commands

Use `uv run` for Python commands in this repository.

```bash
# Install dependencies
uv sync

# Run the GUI directly in development
uv run gui_main.py

# Fast character scan when current HP is known
uv run reader.py <current_hp>
uv run reader.py <current_hp> --loop
uv run reader.py <current_hp> --inventory --filter 等級=7 --filter 真氣=150

# Slower auto-detect path when HP is not known
uv run auto_detect.py
uv run auto_detect.py --loop

# Warehouse scan (warehouse UI must be open in game)
uv run warehouse_scan.py <current_hp>

# Full test suite
uv run pytest

# Run one test file
uv run pytest tests/test_snapshot_db.py -v

# Run one test
uv run pytest tests/test_main_window.py::test_switch_to_data_management_page -v

# Lint / format checks
uv run ruff check .
uv run ruff format --check .
```

## High-level architecture

- `knowledge.json` is the source of truth for the game memory layout. Character field offsets are defined relative to the HP base address, and inventory / warehouse slot structure metadata also lives there.
- `reader.py` is the core memory-access module. It owns memory-region enumeration, character location / validation, field reads, inventory scanning, item-name lookup, and the reusable helpers imported by other entry points.
- Character location is a two-step flow in the current code: `gui.worker.ReaderWorker` first tries `read_hp_from_player_chain()` to get the current HP from a stable chain, then uses `locate_character()` to find the flat struct in memory. Manual HP entry is the fallback when the chain does not work.
- `auto_detect.py` is a separate slower scanner that does not need a known HP value. It pattern-matches a plausible character struct directly from memory using multiple field constraints.
- The GUI is centered around one `CharacterPanel` per detected game process. `gui.main_window.MainWindow` detects all running `tthola.dat` windows, creates a tab per PID, and shares a single `SnapshotDB` instance across the app.
- `gui.worker.ReaderWorker` is the per-character polling thread. It owns the connection / waiting / located / read-error / rescanning state machine, emits stats updates, and performs on-demand inventory and warehouse scans for its panel.
- Snapshot persistence is centralized in `gui.snapshot_db.SnapshotDB`, backed by `tthol_inventory.db`. Both `InventoryManagerTab` and `DataManagementTab` read from that database, so changes to snapshot behavior affect multiple GUI surfaces.
- `warehouse_scan.py` reuses the inventory-slot pattern matcher but finds all candidate slot arrays, excludes the known inventory range, and treats the largest remaining array as the warehouse. Warehouse data only exists while the warehouse UI is open in game.
- `launcher.py` and `gui/launcher_window.py` are the release/update entry point, not the normal dev entry point. The launcher does `git pull` and `pip install -r requirements.txt`, then starts `gui_main.py`.

## Key conventions

- Keep repo commands on the `uv` workflow for development (`uv sync`, `uv run ...`), even though the end-user launcher uses `pip` internally during self-update.
- Code artifacts stay in English: logs, print output, and comments should be English even though the user-facing GUI text is Chinese.
- User-visible GUI strings should go through `gui/i18n.py` via `t(...)` instead of being scattered inline.
- Use `encoding="utf-8"` for JSON and text file I/O.
- Treat process addresses and pointers as **32-bit unsigned** values. `pymem.read_int()` is signed; when reading pointers, use `struct.unpack("<I", ...)`.
- The target process is 32-bit, so memory scans and pointer validation should stay within `0x00000000`-`0x7FFFFFFF`.
- Preserve the existing `ReaderWorker` state-machine pattern instead of adding direct memory polling in widgets.
- Snapshot dedup depends on canonical sorted JSON of `{"item_id", "qty"}` pairs plus a SHA-256 checksum. If you change snapshot persistence, keep that canonicalization / dedup behavior intact.
- `compat_mode` is a real code path: some character structs are treated as a 4-byte-shifted layout, so scan or validation changes need to account for both normal and shifted structure handling.
