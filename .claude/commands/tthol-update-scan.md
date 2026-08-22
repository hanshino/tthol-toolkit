# tthol-update-scan

Re-discover the static pointer chain after a game update.

## When to use

After `tthola.dat` is updated and `uv run reader.py` fails with pointer chain errors,
or the character data is clearly wrong (impossible stats).

## Prerequisites

- Cheat Engine is open and attached to `tthola.dat`
- CE Lua bridge is running (`dofile(...)` executed, `Server Listening` message visible)
- CE MCP tools are available (`mcp__cheatengine__ping` works)

## Steps

### 1. Confirm the pointer chain is broken

Run reader.py without arguments:
```
uv run reader.py
```
If it prints `[X] Pointer chain failed`, proceed. If it works, the chain is still valid — stop here.

### 2. Find the current HP address via fallback scan

Ask the user for their current HP value, then run:
```
uv run reader.py <hp_value>
```
This uses the full memory scan. Note the `hp_addr` from the output (e.g. `0x1A2B3C4D`).

### 3. Update deep_pointer_scan.py and run it

Edit line `target = 0x...` in `deep_pointer_scan.py` to the hp_addr found above.
Then run:
```
uv run deep_pointer_scan.py
```
Wait for output. Look for results under `tthola.dat+0x...` (static module entries only).
Ignore entries from `nvd3dum.dll`, `nvgpucomp32.dll`, `ucrtbase.dll`, or other DLLs.

### 4. Select the best chain

From the `tthola.dat` results, pick the chain with the cleanest (smallest, round-ish) offsets.
Use CE MCP to verify it returns the correct HP value:
```python
mcp__cheatengine__read_pointer_chain(base=<static_addr>, offsets=[<off1>, <off2>])
```
Confirm `final_value` matches the known HP.

### 5. Update reader.py constants

Edit the two constants near the top of `reader.py`:
```python
STATIC_BASE = <new_static_addr>       # tthola.dat + 0x...
STATIC_OFFSETS = [<off1>, <off2>]
```

### 6. Verify

Run without HP value to confirm instant locate:
```
uv run reader.py
```
Expected output: `[OK] Character located via pointer chain at 0x... (0.000s)`

### 7. Update MEMORY.md

Update the static pointer chain entry in MEMORY.md:
```
`[[<STATIC_BASE>]+<off1>]+<off2>` → 角色 struct HP base
```

## Notes

- The chain semantics: `read(STATIC_BASE)` → add off1 → read that → add off2 = HP base
- Offsets typically stay in range 0x00~0xFFF (struct member offsets)
- Large offsets like `0xE45` are valid but unusual — prefer chains with offsets < 0x200 if multiple options exist
- After updating, the GUI worker also picks up the change automatically (it calls `locate_via_pointer_chain`)
