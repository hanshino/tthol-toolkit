---
description: Re-discover the player HP pointer chain after a game update
---

# tthol-update-scan

Re-derive `PLAYER_HP_CHAIN_BASE` / `PLAYER_HP_CHAIN_OFFSETS` after a game
update moves them. Until that is done every automatic locate fails and users
are stuck on the manual-HP fallback.

## When to use

`/tthol-diag` reports `E_LOCATE_EXHAUSTED` whose `detail.chain_walk` first hop
is not a plausible heap pointer (`0x1`, `0xffffffff`, `0x0`) while a scan still
finds the character. That combination means the constants moved, not that the
user is logged out.

## What you need

- **Two game clients running at the same time**, each logged in to a
  character. This is not optional: the discovery scan returns hundreds of
  chains that resolve correctly *right now*, and only intersecting two
  independently-started processes tells you which of them survive a restart.
  Picking the "cleanest looking" offsets from a single process is how you get
  a chain that breaks again on the next launch.
- Cheat Engine is **optional** — used only to double-check a candidate. The
  discovery itself is pure `pymem`.

## Steps

### 1. Confirm the chain really is dead

```bash
uv run python -c "
import pymem, struct, reader
pm = pymem.Pymem(<pid>)
print(hex(struct.unpack('<I', pm.read_bytes(reader.PLAYER_HP_CHAIN_BASE, 4))[0]))
"
```
A heap pointer means the chain is fine and the problem is elsewhere — stop.

### 2. Find each character's HP address

Ask each client's current HP, then locate. **Try both layouts** — a compat
character (`+0`=maxHP, `+4`=currentHP) fails `compat_mode=False` silently, and
`auto_detect.py` is compat-blind so it reports zero candidates rather than an
error:

```bash
uv run python -c "
import pymem, reader
pm = pymem.Pymem(<pid>); kb = reader.load_knowledge()
for compat in (False, True):
    a = reader.locate_character(pm, <hp>, kb, None, compat_mode=compat)
    if a: print(hex(a), compat, reader.read_character_name(pm, a))
"
```

### 3. Run the cross-process intersection

```bash
uv run find_stable_chain.py <pid1>:<hp1> <pid2>:<hp2>
```

Takes a few minutes per process (a 5-level reverse BFS over all memory). It
scans for every address holding the HP value, walks pointers back toward the
loaded modules, then keeps only the chains present in **both** processes.
Ignore anything not in `tthola.dat` — `nvd3dum.dll` and friends produce
plausible-looking chains that are pure coincidence.

### 4. Verify a candidate independently

Cheat Engine is a second opinion from outside your own code. The MCP tools are
only present if CE's Lua bridge was running *before* Claude Code started; if
they are missing, talk to the named pipe directly:

```python
# \\.\pipe\CE_MCP_Bridge_v99 -- 4-byte LE length prefix, then JSON-RPC.
# Open it with ctypes.windll.kernel32.CreateFileW; pywin32's CreateFile
# fails against this pipe with ERROR_PATH_NOT_FOUND.
call("read_pointer_chain", {"base": "tthola.dat+<rva>", "offsets": [...]})
```
`offsets` are decimal in the JSON. Confirm the final value equals the known HP
in both processes.

### 5. Update reader.py

```python
PLAYER_HP_CHAIN_BASE = 0x<module_base + rva>
PLAYER_HP_CHAIN_OFFSETS = [<off1>, <off2>, ...]
```
Note the base is the **absolute** address (`tthola.dat` loads at `0x400000`),
not the RVA the scan prints.

### 6. Verify end to end

```bash
uv run pytest tests/test_hp_chain.py -q
uv run python -c "
import pymem, reader
print(reader.read_hp_from_player_chain(pymem.Pymem(<pid>)))
"
```
Then restart the app and confirm a character locates with no manual HP:
`uv run diag.py summary` should show the new `player_hp_chain_base`, and the
dashboard should reach `link: ok` on its own.

### 7. Update MEMORY.md

Replace the 層二 entry's constants and clear the staleness note.

## Notes

- `deep_pointer_scan.py` no longer exists; `find_stable_chain.py` replaced it.
  `STATIC_BASE` / `STATIC_OFFSETS` (層一, the session-only chain) were removed
  from `reader.py` — do not reintroduce them.
- Offsets are struct member offsets, normally `0x00`–`0xFFF`.
- The chain resolves to the engine charobject, **not** the flat display struct
  the scan locates. HP sits at the last offset; `read_hp_pair_from_chain` uses
  the same chain minus its final hop.
- Until this is done, users recover through the 目前血量 box on the dashboard
  error, which locates by scan. Say so when triaging rather than telling them
  to press 重偵 — with a dead chain 重偵 re-runs the same dead chain.
