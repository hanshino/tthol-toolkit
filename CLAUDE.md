# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Read-only memory reader for the Tthol game (`tthola.dat`, 32-bit process). Reads character stats from game memory — never modifies game memory.

## Commands

```bash
# Install dependencies
uv sync

# Fetch the game DB (tthol.sqlite is not in git; db.lock.json pins the version)
uv run scripts/db_release.py pull

# After refreshing tthol.sqlite from tthol_data: upload it as a db-YYYY-MM-DD
# release and rewrite db.lock.json (needs a logged-in gh CLI), then commit the lock
uv run scripts/db_release.py publish

# Fast scan (requires known HP value)
uv run reader.py <current_hp> [--loop]

# Auto-detect (no known value needed, slower ~14s)
uv run auto_detect.py [--loop]

# Run the app (FastAPI + pywebview, serves bundled webui/dist)
uv run app.py

# Run the app in dev mode (points window at Vite dev server on :5173)
uv run app.py --dev
# In a second terminal:
cd webui && npm run dev

# Run tests
uv run pytest
```

> **Important:** Always use `uv run` for all Python commands in this project. Never use `python` or `python -m` directly.

## Architecture

The character struct lives on the heap and moves (`0xFDFDFDFD` after restart), so it is always located by scanning for an HP value and validating the structure around it. The HP value comes from one of three places:

1. **Player HP chain** — `reader.PLAYER_HP_CHAIN_BASE` / `PLAYER_HP_CHAIN_OFFSETS` resolve to the engine charobject and yield current HP with no user input; the app's worker then scans for that HP. Fixed for a given game build; a game patch moves the root — re-derive with `/tthol-update-scan`.
2. **`reader.py`** — Fast scan (~0.4s) for a known HP value (from the chain, or typed by the user), validated with structure scoring (>= 0.8 match). `auto_detect.py` imports shared utilities from here.
3. **`auto_detect.py`** — Auto-detect (~14s): pattern-matches the character struct by checking multiple field constraints (HP/MP ranges, level ratio, combat stat bounds) without any known value.

**`tthol.sqlite`** (item / map / status data from the tthol_data project) is kept out of git: it is a prerelease asset on this repo's GitHub Releases, pinned by `db.lock.json` (tag + sha256). `release.yml` pulls it before PyInstaller bundles it, so the release zip still ships it.

**`knowledge.json`** is the structure knowledge base defining field offsets relative to the HP address (offset 0) — the source of truth for the character struct layout.

All character fields are `int32` at 4-byte aligned offsets from the HP base address. See `knowledge.json` for the full offset table.

## Conventions

- **All code output (logs, print statements, comments) must be in English.** Communication with the user is in Chinese, but code artifacts use English to avoid Windows cp950 encoding issues.
- Always use `encoding='utf-8'` when reading/writing JSON or text files.
- `pymem.read_int()` returns signed int; use `struct.unpack('<I', ...)` for unsigned/pointer values.
- Target process is 32-bit: address space is `0x00000000`–`0x7FFFFFFF`.
