# Fast Player Position Implementation Plan

**Goal:** Show the player's position on the minimap at ~10 Hz instead of once every 3 s (worst case ~6 s stale today), with multi-boxing (many clients) costing the UI no more than a single character does. Also: fix the dot sitting on the click destination while walking, and cut the ~10 s minimap blackout after a map change to under 1 s.

**Status:** draft, reviewed by advisor 2026-10-01; revised after the Task 0 probe (same day).

## Why it is slow today (measured 2026-10-01 against a live client)

| Step | Cost |
|---|---|
| Read position (HP+416/+420 tiles) | 0.013 ms |
| `verify_structure` | 0.07 ms |
| Whole worker poll (stats, buffs, bag, pet bag, gear, warehouse) | ~1.7 ms (`locate_warehouse` ~1 ms) |

The memory reads are not the bottleneck. Latency comes from two independent 3 s timers stacked on top of each other:

1. `services/worker.py:55` sets `POLL_INTERVAL = 3.0`, so the worker re-reads everything every 3 s.
2. `WorkerManager.run_tick_loop(interval=3.0)` publishes a `WorldSnapshot` on `/ws/world`. The minimap takes its position from that row (`MapAnalysis char={char}`, then `char.position`).

Raising the existing tick rate is not an option. `world_snapshot()` enumerates processes (`find_tthol_processes()`) and serialises every row, and `useLiveChars` makes Dashboard, Sidebar and CharHeader re-render on every frame.

## Design

```
worker thread (per pid)                server loop                       webui
-----------------------                -----------                       -----
full poll every 3 s (unchanged)
  between polls, every 100 ms:
    read tiles + stage, verify,  ──►  CharSession.pos (+ seq)
    on change / on stage change            │
    0xDD sentinel → relocate now           │
                                       PositionHub flush every 100 ms:
                                       collect sessions whose seq moved
                                       ──► one frame /ws/pos ──────────►  positionStore (Map<pid,Pos>)
                                           {ts, pos:{pid:{...}}}          useLivePosition(pid)
                                           (no frame when idle)           └► Minimap player dot only
```

### Principles

- **One thread per pid stays the only reader of `hp_addr`.** The fast loop runs inside the worker's existing wait between polls, so it never races a relocate.
- **Batch on the server, not per worker.** Every 100 ms there is at most one frame, whatever the number of clients. Characters that are standing still send nothing.
- **Separate socket and separate store.** `/ws/world` and `useLiveChars` stay at 3 s. Only the components that need fast position subscribe to it, per pid.

## Tasks

### Task 0 — Probe the game's update rate (DONE 2026-10-01)

Probe: 60 s at 5 ms on a speed-12 character; walking, an in-map room teleport and two map changes. 160 change events. Findings:

1. **HP+636/+640 is the move target (click destination), not the live position.** It jumps to the destination before the tiles move, then stays fixed while the tiles walk there. `px - x*40` failed on 156/160 walking samples. It only equals the position after arrival. **Existing bug:** `Minimap.tsx:199` draws the player dot at `position.px/py`, so while walking the dot sits on the destination.
2. **The live position is tile-granular.** One step takes ~215 ms at speed 12. A diagonal step is two events (x, then y) ~27 ms apart. The game caps at speed 15, which is roughly 170 ms/step if speed is linear. There is no sub-tile live field in this struct.
3. **The struct is freed on map change.** Right after the teleport, all coords at the old address read `0xDDDDDDDD` (MSVC debug free fill) and stay that way. Today the worker needs `FAILURE_THRESHOLD` (3) x `POLL_INTERVAL` (3 s) before relocate even starts, so every map change leaves the minimap stale or dark for ~10 s.
4. **In-map room teleports are real jumps.** Example: 成都 rooms, (58,157) -> (15,13) -> back to (61,155) within ~1 s, same stage id. These are legitimate; no filter is needed. The dot just must not animate across them.
5. All 647 `map_images.tile_px` rows are 40, matching `DEFAULT_TILE_PX` in `services/api/maps.py`.

Decision: `POS_INTERVAL = 0.1` catches every step at speed 15 with <=100 ms lag. Smoothness comes from a CSS transition between tile centers (~200 ms), not from the data.

### Task 1 — Worker fast position loop (`services/worker.py`)

- Add `POS_INTERVAL = 0.1` and an `on_position` callback, defaulting to a no-op like the other optional callbacks.
- In the LOCATED loop, replace `self._wake_event.wait(POLL_INTERVAL)` with `_position_until(deadline)`:
  - each tick: read the two tile ints at HP+416/+420, then `read_stage(pm)` (0.014 ms) for the stage id and map name. `read_stage` does not depend on `hp_addr`, so it stays right even after the struct is freed.
  - the stage goes through the same `self._stage_names` filter as the 3 s loop. A stage that fails the filter is not sent; the frame keeps the last trusted stage.
  - accept the sample only if all of these hold:
    1. `verify_structure` (or `_shifted` in compat mode) scores >= 0.8. At 0.07 ms this is affordable, and it is what stops garbage reads after the struct moves.
    2. tiles are `-1,-1` (just changed map), or inside `[0, w_tiles) x [0, h_tiles)` for the current stage (`map_db.minimap_base`, cached per stage)
  - send only when `(stage_id, x, y)` changed since the last send
  - **stage changed** (stage id differs from the last sent one): send `{stage_id, map_name, unplaced}` at once, even though the tile sample is not trusted yet. The minimap swaps maps immediately and hides the dot instead of drawing it on the wrong map.
  - **definitive loss**: verify < 0.8 and both tile ints read `0xDDDDDDDD` means the struct was freed (map change). Leave the fast loop with a `lost` flag. The main loop then relocates **immediately**, skipping the `FAILURE_THRESHOLD` debounce. That debounce exists for transient glitches; this is not one. The relocate itself goes through `_locate_with_retries` unchanged, so `LOCATE_MAX_RETRIES` still bounds it. (User decision 2026-10-01.) The new struct may not exist yet during the loading screen. Consider a short first retry delay (e.g. 0.5 s) for this path only, so recovery is not held at `LOCATE_RETRY_INTERVAL` = 3 s.
  - any other failed sample is dropped silently (debug log only) and handled by the 3 s poll as today.
  - return early when `_wake_event` is set, so scan requests and stop stay responsive
- The fast path does not read any other fields. HP, buffs, bag and gear stay at 3 s.
- `TILE_PX`: move `DEFAULT_TILE_PX` somewhere both the worker and the API import (e.g. `services/map_db.py`).

### Task 2 — Session state (`services/char_session.py`)

- `_on_position(stage_id, map_name, x, y)`:
  - under `self._lock`, update `_latest_stats` x/y and the stage id and map name (so the 3 s row is never older than the fast stream, and the header's map name catches up at the next snapshot)
  - store `self._pos` (a `Position`, built through `_position()`)
- **Change `_position()`**: derive `px/py` from the tile center (`x*TILE_PX + TILE_PX//2`) instead of HP+636/+640. This fixes the walking-dot bug in the 3 s row too. It can ship first as its own small change. Keep the raw HP+636/+640 out of `Position`; it is the move target. If a destination marker is ever wanted, it gets its own field.
  - bump `self._pos_seq`
- Add `pos_snapshot() -> tuple[int, Position | None]` for the hub.

### Task 3 — PositionHub + `/ws/pos` (`services/events.py`, `services/worker_manager.py`, `services/api/position_ws.py`, `app.py`)

- `PositionStream`: the same drop-oldest subscriber queue as `WorldStream`. Generalise `WorldStream` to a generic class instead of copying it.
- `WorkerManager.run_position_loop(stream, interval=0.1)`:
  - keep `last_sent: dict[pid, seq]`
  - every tick, collect sessions whose seq advanced, and publish `PositionFrame{ts, pos: {pid: Position}}` only if that set is non-empty
  - prune pids that are no longer in `_sessions`
  - pull model, so worker threads never touch the event loop and need no `call_soon_threadsafe`
- `/ws/pos` mirrors `world_ws.py`. On connect it sends one full frame of every session's current position, so a new tab does not wait for movement.
- `app.py`: start the loop next to `_tick_runner`, on the same uvicorn loop (asyncio queues are loop-bound, the same constraint `run_tick_loop` documents).
- `api_types.py`: add `PositionFrame{pos: dict[int, Position]}`. No timestamp on `Position`: the row is never older than the fast stream (Task 2), so there is nothing to compare.
- Mock / `--dev` without a game: when `worker_manager` or the stream is missing, `/ws/pos` closes with 1011 like `world_ws.py`, and the loop is not started. `uv run app.py --dev` with no client must still boot.

### Task 4 — Frontend store (`webui/src/api/positionStore.ts`)

- Module-level `Map<number, Position>` plus a listener set per pid. One lazily opened socket with the same reconnect/backoff as `useLiveChars`, closed when the last subscriber leaves.
- `useLivePosition(pid): Position | null` via `useSyncExternalStore`. A frame re-renders only subscribers whose pid is in it.
- Merge rule: the consumer uses `useLivePosition(pid) ?? char.position`. The store clears all entries when the socket closes, so a dead socket falls back to the 3 s row instead of a frozen dot.

### Task 5 — Minimap (`webui/src/pages/CharWorkspace/Minimap.tsx`, `MapAnalysis.tsx`)

- Run the ui-ux-pro-max skill first (project rule).
- `MapAnalysis` passes `useLivePosition(char.pid) ?? char.position` into `Minimap`.
- Extract the player dot into a small memoised `PlayerDot`, so a position change does not re-render the warp, NPC and spawn layers.
- Smooth the movement between tile centers: `transition: transform ~200ms linear` (one step at speed 12) (if `at()` can move to `transform` cheaply; `left/top` is fine for one element otherwise). Turn it off (`data-jump`) when stage id or region changes, or when a move is larger than ~3 tiles (in-map room teleport, e.g. 成都 rooms), so the dot never slides across the map.
- When the fast frame says the stage changed and the position is unplaced, swap the map and hide the dot. Do not wait for the 3 s row.
- **Fix the region fetch loop.** The effect at `Minimap.tsx:69` depends on `position.x/y`. At 10 Hz, every step outside the current box cancels and restarts `/region`. Depend on `needRegion` and `stageId` only, and read x/y through a ref.
- Respect `prefers-reduced-motion`: no transition.
- Check whether `MapAnalysis` derives anything heavy from position, such as spawn distance lists. If it does, memoise it on tile x/y.

### Task 6 — Tests and verification

- `tests/test_worker_position.py`, with a fake pm in the style of `test_locate_failure_recovery.py`:
  - a changed sample is emitted once
  - an identical sample is not emitted
  - a sample with verify < 0.8 is dropped and does not count toward relocate
  - a tile outside the stage's bounds is dropped
  - a stage change emits `{stage_id, map_name, unplaced}` at once
  - all-`0xDDDDDDDD` plus failed verify leaves the fast loop and relocates without waiting `FAILURE_THRESHOLD` polls; relocate is still bounded by `LOCATE_MAX_RETRIES`
  - `_position()` px/py equal the tile center
  - `-1,-1` after a map change passes through with the `placed` rule applied
  - setting the wake event exits early
- `tests/test_position_stream.py`:
  - one frame batches several pids
  - no frame when nothing changed
  - a dead pid is pruned
  - a new subscriber gets a full frame
- `tests/test_position_ws.py`, mirroring `test_world_ws.py`.
- `uv run pytest`; `cd webui && npm run build`.
- Manual check, live: two clients, walk one, and watch the dot move smoothly at ~10 Hz while the other client's dashboard row stays at 3 s. While walking, the dot stays on the character, not the click destination. In-map room teleport: the dot jumps with no slide. Change map: the minimap swaps at once and the dot reappears within ~1 s (today: ~10 s). Idle: no `/ws/pos` frames (browser devtools, or a counter in the diag log).

## Review Focus

1. **Struct freed between polls (map change).** Expect no garbage dot, an instant map swap, and a relocate in under ~1 s. Guarded by per-tick verify plus the `0xDDDDDDDD` sentinel (Task 1).
2. **Map change.** The fast frame carries the stage id and map name and writes both into the session, so the header catches up at the next 3 s snapshot. Accepted.
3. **Multi-boxing N clients.** Expect at most 10 frames/s total, and a re-render only for the open workspace's dot. The cost is O(N) reads per second on the backend and O(1) on the frontend.
4. **Hidden kept-alive tabs** (`useKeepActive`). A memoised dot re-rendering 10x/s offscreen is noise. No unsubscribe logic.
5. **Socket drop.** Expect a fallback to the 3 s row position (the store clears on close), never a frozen dot.
6. **GIL / thread cost.** N worker threads each wake 10x/s for ~0.1 ms (N=8 is ~80 wakeups/s). Not expected to matter; measure once with N=2 in Task 6.
7. **Stage filter.** A fast-tick stage that fails `_stage_names` is never sent (Task 1).
8. **No game running (`--dev`, mock).** `/ws/pos` closes cleanly and the app boots (Task 3).
9. **Immediate relocate is a behaviour change** (user-approved). It must only trigger on the definitive sentinel, never on an ordinary verify dip, and must stay bounded by `LOCATE_MAX_RETRIES`.

## Non-goals

- Changing the 3 s rate of stats, buffs, bag, gear or warehouse.
- Faster position in Dashboard, Sidebar or CharHeader. It could be added later by subscribing them to `useLivePosition`.
- Movement prediction or extrapolation beyond a CSS transition.
- Sub-tile live position. That would need a diff-probe across the whole CCharObject while walking, which is a separate RE task.
- CLI tools (`reader.py --loop`).
