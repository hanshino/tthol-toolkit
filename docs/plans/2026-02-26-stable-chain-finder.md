# Stable Pointer Chain Finder

## Goal

Given multiple game processes (2+ windows) with their HP values, automatically discover cross-restart stable pointer chains (e.g. `[[[[0x007F6810]+0x128]+0x68]+0x140]`) and output constants for `reader.py`.

## Architecture

Single script `find_stable_chain.py`:

```
Input: multiple (PID, HP) pairs (at least 2 game windows)
         |
Step 1: For each process, scan for hp_addr using locate_character (from reader.py)
         |
Step 2: For each process, reverse BFS from hp_addr to find pointer chains ending at static bases
         |
Step 3: Intersect -- match chains by (module_name, module_offset, [offsets...]), ignore dynamic addrs
         |
Step 4: Verify survivors -- resolve each chain in every process, confirm correct HP value
         |
Output: Stable chain list with suggested reader.py constants
```

## Parameters

- `max_levels`: 5 (standard for 32-bit games)
- `search_range`: 2048 (max offset per level)
- Chain match key: `(module+offset, [per-level offsets])` -- dynamic intermediate addresses differ but offsets are identical

## Usage

```bash
uv run find_stable_chain.py <pid1>:<hp1> <pid2>:<hp2> [...]
```

## Implementation Notes

- Uses pymem directly (not CE MCP) -- CE can only attach to one process
- Process identification by PID (all share exe name `tthola.dat`)
- Reuses `get_memory_regions`, `get_static_ranges`, `is_static`, `find_pointers_to` from existing code
- Reuses `locate_character`, `load_knowledge` from reader.py
