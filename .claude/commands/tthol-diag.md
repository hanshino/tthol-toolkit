---
description: Triage a tthol-reader failure report from its diagnostic events
---

# tthol-diag

Diagnose a reported failure using the app's own event record. Works against a
running app, a closed one, or a bundle zip a user sent.

## 1. Orient

```bash
uv run diag.py summary
```

This prints whether the app is running, its version, the `events.jsonl` path,
the pointer-chain constants, and event counts by level. If it reports
`no runtime.json found`, the app has never been started on this machine — ask
for a bundle instead and use `uv run diag.py inspect <bundle.zip>`.

**Check the pointer chain first.** If `player_hp_chain_base` differs from the
value in `reader.py`, the game has been updated and the constants are stale.
Stop here and run `/tthol-update-scan` — no amount of event reading will fix
that. (`static_base` reads `None` on current builds: the session static chain
was removed from `reader.py` and only the player HP chain remains. That is
expected, not a fault.)

## 2. Find the failure

```bash
uv run diag.py events --level ERROR --since 1h --json
```

Every failure carries a stable `code`. Match on `code`, never on `message` —
message text is prose and changes between versions.

## 3. Read the code

| Code | Meaning | Read in `detail` | Usual cause |
|---|---|---|---|
| `E_PROC_GONE` | Cannot attach to the game process | `pid`, `exc` | Game closed, or the reader lacks rights to open the process |
| `E_CHAIN_READ` | The player HP pointer chain did not resolve | the full snapshot | Not logged into a character yet, or the chain constants are stale after a game update |
| `E_LOCATE_EXHAUSTED` | Locate retried to its bound and gave up | the full snapshot: `chain_hp`, `compat_false`, `compat_true`, `bytes_hex`, `score` | See the decision tree below |
| `E_LOCK_LOST` | Validation score fell below 0.8 three times | `score`, `hp_addr` | The character struct moved (map change). Normal in ones and twos; a `relocated N times in the last 60s` line means something worse |
| `E_SCAN_FAILED` | A scan raised | `exc`, `hp_value`, `compat_tried` | Usually a read against freed memory |
| `E_INV_NOT_FOUND` | Inventory pattern not found | `hp_addr`, `scan_ms` | The scan returns an empty list on this path, so the UI shows "no items". An empty inventory and an unscannable one look identical without this code — that is the whole reason it exists |
| `E_WH_NOT_FOUND` | No warehouse slot array found | `hp_addr`, `inv_range`, `arrays_seen` | The warehouse UI was not open in game; the structure only exists while it is |
| `E_API_5XX` | An endpoint raised | `path`, `method`, `status`, `traceback` | A real backend bug — read the traceback |
| `E_CLIENT` | A browser-side error | `url`, `stack`, `component`, `ua` | Frontend bug; correlate by timestamp with backend events |

## 4. Decision tree for `E_LOCATE_EXHAUSTED`

Read `detail.bytes_hex` — the first 32 bytes at the last known address:

- Starts `cdcdcdcd` — the block was freed. The struct moved; normal during a
  map change, a problem if it repeats.
- Starts `fdfdfdfd` — the game was restarted. The session is stale; the
  UI's 重偵 button rebuilds it.
- Anything else — read `detail.chain_hp`:
  - An integer, but both `compat_false` and `compat_true` are `null` → the HP
    value is right but no candidate passed structure validation. Suspect
    `knowledge.json` drift; compare `knowledge_sha8` in the summary against
    the repo.
  - A string starting `<` (a captured exception) → the chain read itself
    failed. Treat as `E_CHAIN_READ`.
  - `null` and `hp_value` is also `null` → the user never supplied an HP value
    and the chain was unavailable. Expected before login.

## 5. Correlate frontend and backend

Both land on one timeline, so a client error and its backend cause sit
adjacent:

```bash
uv run diag.py events --since 10m
```

## 6. Get more detail

If the events are too sparse, have the user turn on 詳細記錄 on the 脈案 page
(or `PUT /api/diagnostics/verbose`), reproduce, and export again. Verbose mode
raises only the `tthol` logger to DEBUG and resets to INFO on restart.

## Notes

- Every command runs through `uv run`. Never bare `python`.
- `runtime.json` lives at `%LOCALAPPDATA%\tthol-reader\runtime.json` and always
  at that path, whatever the fallback chose for `events.jsonl`.
- A `runtime.json` whose `pid` is not alive is stale — the app crashed rather
  than exiting cleanly, which is itself a finding.
- The ring buffer holds 1000 events; `events.jsonl` holds 5 MB × 5 rotations.
  For anything older than that, ask for a bundle taken closer to the incident.
