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
| `E_LOCATE_EXHAUSTED` | Locate retried to its bound and gave up | the full snapshot: `chain_walk`, `chain_hp`, `compat_false`, `compat_true`, `bytes_hex`, `score` | See the decision tree below |
| `E_LOCK_LOST` | Validation score fell below 0.8 three times | `score`, `hp_addr` | The character struct moved (map change). Normal in ones and twos; a `relocated N times in the last 60s` line means something worse |
| `E_SCAN_FAILED` | A scan raised | `exc`, `hp_value`, `compat_tried` | Usually a read against freed memory |
| `E_INV_NOT_FOUND` | Inventory pattern not found | `hp_addr`, `scan_ms` | The scan returns an empty list on this path, so the UI shows "no items". An empty inventory and an unscannable one look identical without this code — that is the whole reason it exists |
| `E_WH_NOT_FOUND` | No warehouse slot array found | `hp_addr`, `inv_range`, `arrays_seen` | The warehouse UI was not open in game; the structure only exists while it is |
| `E_API_5XX` | An endpoint raised | `path`, `method`, `status`, `traceback` | A real backend bug — read the traceback |
| `E_CLIENT` | A browser-side error | `url`, `stack`, `component`, `ua` | Frontend bug; correlate by timestamp with backend events |

## 4. Decision tree for `E_LOCATE_EXHAUSTED`

**Start with `detail.chain_walk`** — the raw deref sequence from
`PLAYER_HP_CHAIN_BASE`, one entry per hop, formatted `<ptr>+<offset>`.

- First hop is not a plausible heap pointer (`0x1`, `0xffffffff`, `0x0`) →
  **the chain constant is stale; the game was updated.** `chain_hp` reads
  `null`, which on its own is indistinguishable from "not logged in" — the
  walk is what separates the two. Confirm in one step: `uv run auto_detect.py`.
  If it finds a character while the chain is dead, the constant has moved for
  certain. The fix is `/tthol-update-scan`; the user's stopgap is the 目前血量
  box on the dashboard error, which locates by scan instead.
- The walk reaches its last hop but `chain_hp` is `null` → the chain resolves
  and the HP failed its sanity bound. Suspect a changed offset rather than a
  changed base.
- The walk is empty, or its first entry reads `<...Error at 0x...>` → the base
  address itself is unreadable. Check the process really is `tthola.dat` and
  is still alive.

Then read `detail.bytes_hex` — the first 32 bytes at the last known address:

- Starts `cdcdcdcd` — the block was freed. The struct moved; normal during a
  map change, a problem if it repeats.
- Starts `fdfdfdfd` — the game was restarted. The session is stale; the
  UI's 重偵 button rebuilds it.
- `chain_hp` is an integer, but both `compat_false` and `compat_true` are
  `null` → the HP value is right but no candidate passed structure validation.
  Suspect `knowledge.json` drift; compare `knowledge_sha8` in the summary
  against the repo.
- `chain_hp` is a string starting `<` (a captured exception) → the chain read
  itself raised. Treat as `E_CHAIN_READ`.
- `chain_walk` is healthy and `hp_value` is `null` → no HP was supplied and the
  chain was momentarily unavailable. Expected before login.

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
- `uv run diag.py summary` distinguishes three states: `running`,
  `not running (exited cleanly)`, and
  `not running (CRASHED -- no clean-exit stamp)`. The last one means the
  process died without running its exit path — a finding in its own right,
  and worth correlating with the final events in the timeline.
- The pointer survives a clean exit (it gains an `exited_at` stamp), so
  post-mortem triage works after the user has closed the app. If the pointer
  is missing entirely, the CLI still tries
  `%LOCALAPPDATA%\tthol-reader\logs\events.jsonl` and says so on stderr.
- Locate exhaustion is reported once, by the retry loop itself
  (`ReaderWorker._report_locate_exhausted`), not by its three callers. If you
  add another caller, do not re-report — and do not add a bare log line beside
  it, which is exactly how the initial-locate path went blind.
- The ring buffer holds 1000 events; `events.jsonl` holds 5 MB × 5 rotations.
  For anything older than that, ask for a bundle taken closer to the incident.
