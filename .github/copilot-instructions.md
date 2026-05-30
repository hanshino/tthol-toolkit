# Copilot Instructions

This repository is a **read-only** Windows memory reader for the Tthol game. It reads character, inventory, and warehouse data from the running `tthola.dat` process and should never write to game memory.

## Commands

Use `uv run` for Python commands in this repository.

```bash
# Install dependencies
uv sync

# Run the app (FastAPI + pywebview, serves built webui/dist)
uv run app.py

# Run the app in dev mode (window points at Vite dev server on :5173)
uv run app.py --dev
# In a second terminal, start the Vite dev server:
cd webui && npm run dev

# Build the webui bundle (required before `uv run app.py` without --dev)
cd webui && npm ci && npm run build

# Build a release artifact locally (PyInstaller onedir bundle)
pwsh scripts/build-release-local.ps1

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
uv run pytest tests/test_worker_manager.py -v

# Lint / format checks
uv run ruff check .
uv run ruff format --check .
```

## High-level architecture

- `knowledge.json` is the source of truth for the game memory layout. Character field offsets are defined relative to the HP base address, and inventory / warehouse slot structure metadata also lives there.
- `reader.py` is the core memory-access module. It owns memory-region enumeration, character location / validation, field reads, inventory scanning, item-name lookup, and the reusable helpers imported by other entry points.
- Character location is a two-step flow inside `services/worker.py`: `read_hp_from_player_chain()` reads the current HP from a stable pointer chain, then `locate_character()` scans memory for the flat struct. Manual HP entry is the fallback when the chain does not work.
- `auto_detect.py` is a separate slower scanner that does not need a known HP value. It pattern-matches a plausible character struct directly from memory using multiple field constraints.
- `app.py` is the only entry point: PyInstaller bundles it as `tthol-reader.exe`. It starts uvicorn in a daemon thread serving FastAPI from `services/api/`, then opens a pywebview window pointed at the local server. In dev mode (`--dev`) the window targets the Vite dev server on :5173 instead of the bundled `webui/dist`. There is no separate splash or update-check stage — double-clicking the exe goes straight into the app.
- `services/worker_manager.py` owns one `ReaderWorker` per detected `tthola.dat` PID, plus the tick loop that publishes a `WorldSnapshot` every 1.5s into the WebSocket pubsub at `/ws/world`. The frontend reconciles state from `GET /api/world` (initial) + `/ws/world` (live).
- `services/worker.py` is the per-character polling thread. It owns the connection / waiting / located / read-error / rescanning state machine and performs on-demand inventory + warehouse scans. Locate retries are bounded (`LOCATE_MAX_RETRIES = 10`); after the cap the worker exits and the React UI's "↻ 重偵" button calls `/api/characters/{pid}/rescan` to rebuild the session.
- Snapshot persistence is in `services/snapshot_db.py`, backed by `%APPDATA%\御心鑒\snapshots.db` (with one-shot migration from a legacy `tthol_inventory.db` in the install root). Both the Treasury page and the Snapshots page read from that DB.
- `warehouse_scan.py` reuses the inventory-slot pattern matcher but finds all candidate slot arrays, excludes the known inventory range, and treats the largest remaining array as the warehouse. Warehouse data only exists while the warehouse UI is open in game.
- `webui/` is a Vite + React + TypeScript SPA. Types in `webui/src/api/schema.ts` are generated from FastAPI's `/openapi.json` via `scripts/gen_openapi.py`; do not hand-edit them.

## Key conventions

- Keep repo commands on the `uv` workflow for development (`uv sync`, `uv run ...`). The end-user release is a PyInstaller `tthol-reader.exe` bundle — no `pip` / `git` / auto-update on the user side. Upgrades are manual: download a new zip from GitHub Releases and replace the install folder.
- Code artifacts stay in English: logs, print output, and comments should be English even though the user-facing UI text is Chinese.
- User-visible strings live in the React app (`webui/src/`); keep Chinese strings inside `.tsx` files, English everywhere in Python.
- Use `encoding="utf-8"` for JSON and text file I/O.
- Treat process addresses and pointers as **32-bit unsigned** values. `pymem.read_int()` is signed; when reading pointers, use `struct.unpack("<I", ...)`.
- The target process is 32-bit, so memory scans and pointer validation should stay within `0x00000000`-`0x7FFFFFFF`.
- Preserve the `ReaderWorker` state-machine pattern in `services/worker.py` instead of adding direct memory polling from API handlers.
- Snapshot dedup depends on canonical sorted JSON of `{"item_id", "qty"}` pairs plus a SHA-256 checksum. If you change snapshot persistence, keep that canonicalization / dedup behavior intact.
- `compat_mode` is a real code path: some character structs are treated as a 4-byte-shifted layout, so scan or validation changes need to account for both normal and shifted structure handling.
- For path resolution, use `services._paths.app_root()` (writable install root, e.g. for user data) vs `bundled(...)` (read-only data shipped inside `_internal/` when frozen). Do not hardcode paths relative to `__file__` from non-`services/` modules.
