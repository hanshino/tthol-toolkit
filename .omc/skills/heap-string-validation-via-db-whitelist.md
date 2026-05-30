---
name: heap-string-validation-via-db-whitelist
description: When scanning Tthol heap for strings that also exist in tthol.sqlite (map names, item names, NPC names), validate candidates against the DB whitelist instead of heap-padding heuristics — heuristics break on game updates, DB whitelist does not.
triggers:
  - locate_map_name returns empty string
  - "0xCDCDCDCD" heuristic fails after game update
  - heap pattern matching map name
  - Big5 string scan picks up wrong candidate
  - heap padding cdcd / fdfd assumption broken
  - struct trailing bytes changed after update
  - validate against tthol.sqlite stages
  - validate against items table
  - heap candidate filter rejects real value
---

# Heap String Validation via DB Whitelist

## The Insight

When Tthol heap memory contains a string (map name, item name, character name, NPC name)
**that also exists in `tthol.sqlite`**, do not trust heap-layout heuristics
(`0xCDCDCDCD` trailing padding, `cdcd/fdfd` preceding-bytes checks, struct prefix
markers). These ARE fragile across game versions — the allocator pads/aligns
differently, the surrounding struct grows new fields, etc.

Instead: scan loosely (e.g. "Big5 + 0x28 prefix"), then **filter the candidate set against
the canonical DB whitelist** (`SELECT name FROM stages` / `items` / `npc`).
Set membership is O(1) and version-proof — only real game data ever lands in the DB.

## Why This Matters

The 2026-05 game update silently inserted 2 bytes (`0xb0 0x00`) between a map
name's null terminator and the `0xCDCDCDCD` heap padding. The strict
`data[after:after+4] == b"\xcd\xcd\xcd\xcd"` filter at `reader.py:locate_map_name`
rejected every real map but kept passing the structural prefix check, so the
function returned `""` — and the frontend silently showed "尚未取得地圖位置"
forever. Symptom: character is detected (`link: ok`), but `position.map_name == ""`
in `/api/world` and the `行止` tab is dead.

Heap-padding heuristics also produce false positives — `0x21F78C20` was the
**player's character name** "阿克婭", not a map, but it had `cdcdcdcd` after the
null. The only thing keeping it out was a fragile "preceding 8 bytes don't have
cdcd" hack. Stack heuristics on top of heuristics → eventual breakage.

## Recognition Pattern

Suspect this skill when:
- A heap-scanning helper used to work and now silently returns `""` / `None` /
  empty list after a game update (no exception, just empty).
- The string the user expects to see DOES exist in `tthol.sqlite` (verifiable via
  `sqlite3` query).
- A diagnostic loop "find all candidates that pass *prefix* filter only" produces
  the right value among the candidates — proving the data IS in heap, just being
  filtered out.
- The current filter relies on bytes immediately after the string (padding,
  terminators, magic markers).

## The Approach

1. **Confirm the data is in heap** — write a one-shot diagnostic that finds all
   candidates passing only the loose prefix + valid-encoding filter, dump
   `(addr, decoded, surrounding bytes)`. If the expected value appears, the
   problem is over-filtering, not missing data.
2. **Look up the candidate space in `tthol.sqlite`** — `services/map_db.py`,
   `tthol.sqlite items`, etc. Encode candidates and compare. If the real value
   matches a DB row, the DB is your whitelist.
3. **Add a `valid_names: set[str] | None = None` parameter** to the locator
   function. When provided, take the first candidate that's a member; ignore the
   structural-padding checks entirely. Keep the legacy heuristic path for
   standalone CLI scripts that have no DB.
4. **Inject the whitelist from a layer that has DB access** (e.g.
   `services/worker.py` loads `all_stage_names()` once at init, passes it down
   into `reader.py`). Keep `reader.py` itself DB-free.

The mental model: **memory layout is the game's implementation detail and will
change. The DB is the contract**. Filter against the contract, not the
implementation detail.

## Example

`reader.py:locate_map_name(pm, valid_names=None)`:

```python
try:
    decoded = name_bytes.decode("big5")
except Exception:
    continue

if valid_names is not None:
    if decoded in valid_names:
        return decoded
    continue  # no DB match — skip; do not fall back to heuristic on this candidate

# Legacy heuristic path (kept for CLI without DB)
after = null_pos + 1
if data[after:after+4] != b"\xcd\xcd\xcd\xcd":
    continue
...
```

`services/worker.py` `__init__`:

```python
try:
    self._stage_names = all_stage_names()  # set[str] from tthol.sqlite
except Exception:
    self._stage_names = None  # fall back to heuristic if DB unavailable
```

Per-tick call:

```python
map_name = locate_map_name(pm, valid_names=self._stage_names)
```

## Generalization

This skill applies to **any** heap-extracted value where `tthol.sqlite` has the
canonical set:
- map names ↔ `stages.name` ✓ (already done)
- item names ↔ `items.name` (already partially done — items use ID lookup)
- NPC / monster names ↔ `npc.name` / `monsters.name`
- skill names ↔ relevant table

When you find yourself adding "the trailing bytes look like X" filters, stop and
check whether a DB whitelist exists. If it does, use it.
