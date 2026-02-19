# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Read-only memory reader for the Tthol game (`tthola.dat`, 32-bit process). Reads character stats from game memory — never modifies game memory.

## Commands

```bash
# Install dependencies
uv sync

# Fast scan (requires known HP value)
uv run reader.py <current_hp> [--loop]

# Auto-detect (no known value needed, slower ~14s)
uv run auto_detect.py [--loop]
```

## Architecture

**No stable static pointer chain exists.** Heap pointers are ephemeral (`0xFDFDFDFD` after restart). Two approaches locate character data:

1. **`reader.py`** — Fast scan (~0.4s): scans all memory for a known HP value, validates with structure scoring (>= 0.8 match). `auto_detect.py` imports shared utilities from here.
2. **`auto_detect.py`** — Auto-detect (~14s): pattern-matches the character struct by checking multiple field constraints (HP/MP ranges, level ratio, combat stat bounds) without any known value.
3. **`knowledge.json`** — Structure knowledge base defining field offsets relative to HP address (offset 0). Source of truth for the character struct layout.

All character fields are `int32` at 4-byte aligned offsets from the HP base address. See `knowledge.json` for the full offset table.

## Conventions

- **All code output (logs, print statements, comments) must be in English.** Communication with the user is in Chinese, but code artifacts use English to avoid Windows cp950 encoding issues.
- Always use `encoding='utf-8'` when reading/writing JSON or text files.
- `pymem.read_int()` returns signed int; use `struct.unpack('<I', ...)` for unsigned/pointer values.
- Target process is 32-bit: address space is `0x00000000`–`0x7FFFFFFF`.
