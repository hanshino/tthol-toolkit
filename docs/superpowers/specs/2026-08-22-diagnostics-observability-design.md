# Diagnostics & Observability

## Overview

A user reported "獲取資料失敗" (data fetch failed) and the app left no trace that could
explain it. The app is a distributed desktop binary — there is no way to attach a debugger
to a user's machine, so every diagnosis has to be reconstructed from whatever the app
recorded on its own.

Today it records almost nothing. This design closes the gap in three moves:

1. Make the log pipeline complete and impossible to silence.
2. Route every error — backend and frontend alike — through one bus (Python `logging`),
   with an in-memory ring buffer as the second sink.
3. Give the user a "脈案" (diagnostics) page and a one-click bundle export so a bug report
   arrives as a zip instead of a sentence.

The app stays read-only with respect to game memory. Nothing here writes to the game.

## Goals

- A user-reported failure can be diagnosed from a single exported zip, without a follow-up
  round trip asking them to find a log file.
- Every failure path in the worker records **why** it failed, not just **that** it failed.
- Log lines are attributable to a specific PID / character (multi-boxing is a core feature).
- The frontend stops swallowing errors; what the user saw and what the backend did land on
  one timeline.
- Logging never silently disappears because of an unwritable install directory.

## Non-Goals

- No telemetry, no network upload, no crash reporting service. The user chooses to send the
  bundle; the app never phones home.
- No structured-logging library (structlog etc.). Stdlib `logging` plus `extra=` is enough
  and keeps the dependency surface flat for PyInstaller.
- No virtual-list dependency for the event view. A "load more" button is sufficient at the
  1000-event buffer cap.
- No frontend test framework. None exists today; introducing one is out of scope.

## Current-State Findings (the basis for this design)

All verified by reading the code at commit `a91618b`.

**F1 — Worker errors are discarded.** `services/char_session.py:71` passes
`on_error=lambda _msg: None`. Every specific reason the worker produces —
`Inventory not found in memory`, `Warehouse not found -- open warehouse UI in game first`,
`Scan failed: {e}`, `Cannot connect to PID {pid}: {e}` (`services/worker.py:245, 319, 330, 367`) — is destroyed at the moment it is produced. It reaches neither the log nor
the UI.

**F2 — Logging exists in exactly one module.** Nine `log.*` calls, all in
`services/worker.py`. Zero in the API layer, `reader.py`, `snapshot_db`, `backup`,
`treasury`. `app.py:81` sets uvicorn `log_level="warning"`, so there is no access log
either: a 504 or 500 leaves no server-side record of which endpoint produced it.

**F3 — Log lines carry no PID or character name.** `log = logging.getLogger("tthol.worker")`
is module-level and shared across every worker thread. With several game clients open, the
`lost lock` / `re-acquired` lines interleave in one file with no way to attribute them.

**F4 — A release build may log nowhere at all.** `tthol-reader.spec:101` is
`console=_DEBUG`, so release builds are windowed and `sys.stderr` is unavailable — the
`StreamHandler` is dead weight. The only real sink is the `RotatingFileHandler`, wrapped in
`except Exception: pass` (`app.py:47`), writing to `app_root()` (next to the exe). Installed
under `C:\Program Files` or any non-writable location, the app logs **nothing**, silently.

**F5 — The tick loop's errors evaporate.** `services/worker_manager.py:173` uses `print()`.
A windowed PyInstaller build has no stdout.

**F6 — The frontend swallows errors.** `.catch(() => {})` at `BodyTab.tsx:20`,
`ItemsTab.tsx:23`, `Snapshots.tsx:16`. Where errors are shown, they are `String(e)` —
i.e. `Error: /api/xxx: 500`, which carries no information for either the user or the
developer, because `client.ts` discards the response body.

**F7 — No version or environment header.** A log file arriving from a user cannot be tied
to an app version, a game version, or the state of the pointer-chain constants.

**F8 — Interesting log segments get rotated away.** The existing log shows `lost lock` →
`re-acquired` cycling every ~9 seconds during one session. At `maxBytes=1MB, backupCount=2`
that noise displaces the evidence.

**F9 — The UI reports the wrong version.** `webui/src/components/TopNav.tsx:23` hardcodes
`v0.7.2`; the app is `1.2.1` (`pyproject.toml:3`, `services/backup.py:17`,
`webui/package.json:4`). A user reading the version off the UI reports a version that has
not existed for five releases.

**F10 — The most likely cause of the actual report.** `worker._do_inventory_scan` calls
`_cb_error("Inventory not found in memory")` — destroyed per F1 — and then
`_cb_inventory([])`. That advances `_inv_seq`, so `POST /api/characters/{pid}/inventory/scan`
does **not** time out; it returns an empty array. The frontend renders "no items". The one
sentence explaining why was thrown away. `_do_warehouse_scan` has the identical shape.

F10 is a hypothesis, not a confirmed diagnosis. It is the design's acceptance test: once
F1 is fixed, the reporting user can reproduce and export, and `report.md` will say whether
this was it.

## Architecture

**`logging` is the bus.** Events are born in exactly one place — a `log.*` call. A
`DiagnosticsHandler` on the root logger feeds an in-memory ring buffer; the rotating file
handler writes to disk. The file log and the UI event list cannot disagree, because they
are two sinks on one stream. Third-party loggers (`pymem`, `uvicorn`) are captured for free.

Frontend errors POST to an endpoint whose only job is to call `log.error(...)` under the
`tthol.client` logger name, so they join the same stream and the same timeline.

Structured fields ride on `extra=`; a `LoggerAdapter` bound per session makes that free at
the call site.

Alternatives considered and rejected:

- **Explicit event hub** (`hub.emit(Event(...))` as the primary API, logging as one sink).
  Fully structured by construction, but leaves two parallel APIs forever, and every existing
  `log.*` call plus all third-party output stays invisible to the UI unless duplicated.
- **File-only** (page and export both read the log file). Least code, but requires parsing
  back text we formatted, cannot hold the structured failure snapshot, and returns nothing
  at all in the F4 unwritable-directory case.

## Section 1 — Log Infrastructure

Extract `app.py::_setup_logging` into `services/logsetup.py` so tests can exercise it.

### Landing path: three-tier fallback, never silent

1. `%LOCALAPPDATA%\tthol-reader\logs\tthol-reader.log` — primary, guaranteed writable
2. `app_root()/tthol-reader.log` — preserves today's behaviour for portable installs
3. `tempfile.gettempdir()/tthol-reader.log` — last resort

The first handler that constructs successfully wins. If all three fail, the ring buffer
still collects events and the diagnostics page and bundle export still work — that
resilience is the direct payoff of the logging-as-bus choice. The path actually in use is
written as the first ring-buffer event and shown on the diagnostics page.

### Rotation

`maxBytes=5_000_000, backupCount=5` (25 MB ceiling). The export includes every backup.

### Format

```
%(asctime)s %(levelname)-7s %(name)s [pid=%(char_pid)s char=%(char_name)s] %(message)s
```

A `logging.Filter` supplies `-` defaults for `char_pid` / `char_name` on records that lack
them. **This is mandatory, not cosmetic**: without it the first `pymem` or `uvicorn` record
raises `KeyError` inside the formatter.

### Startup header

Written once, immediately after the file handler opens:

- App version (`services/backup.APP_VERSION`)
- `frozen?`, exe path, Python version, OS build
- The log path actually in use
- `knowledge.json` mtime + first 8 chars of its sha256
- `tthol.sqlite` presence and `items` row count
- `reader.py`'s current `STATIC_BASE`, `STATIC_OFFSETS`, `PLAYER_HP_CHAIN_BASE`,
  `PLAYER_HP_CHAIN_OFFSETS`

The last item matters most: a game update invalidates these constants, and the header lets
that be confirmed or ruled out at a glance.

### Noise suppression

Per-PID 60-second sliding window. Within a window, `lost lock` occurrences past the second
drop to DEBUG; at window close, one summary line: `relocated 14 times in the last 60s`.
This preserves the file (F8) and improves the signal — relocation *frequency* is itself
diagnostic, and a rate line conveys it better than 14 identical lines.

### HTTP layer

uvicorn stays at `log_level="warning"` (no access log). Instead, one middleware logs only
non-2xx responses and requests exceeding 1s, with path, method, status, elapsed time, and
any `pid` path parameter. Higher signal density than a full access log, and the 15s / 60s
scan timeouts surface on their own.

## Section 2 — Diagnostics Core (`services/diagnostics.py`)

```python
@dataclass(frozen=True)
class DiagEvent:
    ts: float
    level: str            # INFO / WARNING / ERROR
    logger: str           # tthol.worker / tthol.api / tthol.client / pymem
    pid: int | None
    char: str | None
    cat: str              # locate / read / inventory / warehouse / api / client / startup
    message: str
    detail: dict | None   # structured failure snapshot
```

- **`DiagnosticsBuffer`** — `collections.deque(maxlen=1000)` guarded by a `threading.Lock`.
  At the 3s poll cadence this spans tens of minutes of activity.
- **`DiagnosticsHandler(logging.Handler)`** — attached to the root logger. `emit()` reads
  `char_pid` / `char_name` / `cat` / `detail` from `record.__dict__`, defaulting to `None`.
  The whole body is wrapped in `try/except` and **must never log**: a handler that logs
  from inside `emit` recurses into itself.
- **`bind(pid, name) -> LoggerAdapter`** — held by `CharSession` and handed to the worker,
  so every worker line carries pid and character name without a hand-written `extra=` at
  each call site. This is what makes "structured by discipline" cost nothing in practice.
- **`set_verbose(bool)`** — adjusts the level of `logging.getLogger("tthol")` only, never
  the root logger; raising root to DEBUG would drown everything in `pymem` output. Not
  persisted: a restart returns to INFO, so a user who forgets to switch it off does not
  grow the log indefinitely.
- **`snapshot_locate_failure(pm, ...) -> dict`** — called on every worker failure path.
  Collects:
  - `read_hp_from_player_chain` return value, or the exception it raised
  - `locate_character` result for **both** `compat=False` and `compat=True`
  - The first 32 bytes at the current `hp_addr`, hex — distinguishes `0xCDCDCDCD` (freed)
    from `0xFDFDFDFD` (game restarted) from anything else
  - The last validation score and which fields failed
  - Whether the process is alive, and module base addresses

  The dict goes into `detail`; the log message itself stays a human-readable single line.
  This is what turns `re-locate failed after retries` into a readable scene.

## Section 3 — Error Propagation Repair

### 3.1 Connect `on_error` (fixes F1)

`services/char_session.py:71`:

```python
def _on_error(self, msg: str, *, cat: str = "worker", detail: dict | None = None) -> None:
    with self._lock:
        self._last_error = ErrorInfo(ts=time.time(), message=msg, cat=cat)
    self._log.error(msg, extra={"cat": cat, "detail": detail})
```

`ErrorInfo` is a new Pydantic model in `services/api_types.py`:

```python
class ErrorInfo(BaseModel):
    ts: float
    message: str
    cat: str
```

`CharacterRow` and `CharacterDetail` gain an optional `last_error: ErrorInfo | None`,
pushed to the Dashboard over `/ws/world`. The user sees self-serviceable messages
("open the warehouse UI in game first") without opening the diagnostics page. Adding an
optional Pydantic field is backward-compatible for any older frontend. The mirrored
TypeScript type goes in `webui/src/api/types.ts` alongside the existing row/detail types.

The worker's `on_error` signature widens from `Callable[[str], None]` to accept keyword
`cat` / `detail`. Nine call sites: `worker.py:176, 223, 245, 300, 319, 330, 338, 367, 377`.

### 3.2 Bind the worker's logger (fixes F3)

Replace module-level `log = logging.getLogger("tthol.worker")` with
`self._log = diagnostics.bind(pid, char_name)`, re-bound after `read_character_name`
resolves. All nine existing log calls gain pid and character attribution with no other
change.

### 3.3 Replace the tick-loop `print` (fixes F5)

`services/worker_manager.py:173` → `log.exception(...)`.

### 3.4 Global FastAPI exception handlers (fixes F2)

`build_app` registers handlers for `Exception` and `HTTPException`: 4xx at WARNING, 5xx at
ERROR, each carrying path, method, pid, and detail. The `Exception` handler returns a 500
JSON body shaped like `HTTPException`'s (`{"detail": ...}`) so the frontend has one parse
path.

### 3.5 Disambiguate empty vs. unscannable (addresses F10)

With 3.1 in place, `_do_inventory_scan` and `_do_warehouse_scan` still return `[]`, but the
reason is now recorded and reaches the UI through `last_error`. The frontend distinguishes
"the container is empty" from "the scan could not find the container" by checking
`last_error` alongside an empty list.

## Section 4 — Diagnostics API and Bundle Export

New router `services/api/diagnostics.py`, mounted in `build_app`.

| Endpoint | Purpose |
|---|---|
| `GET /api/diagnostics/events?since=&level=&pid=&cat=` | Ring-buffer events, newest first |
| `GET /api/diagnostics/summary` | Environment header, per-PID state, per-level counts |
| `GET` / `PUT /api/diagnostics/verbose` | Verbose-mode toggle |
| `POST /api/diagnostics/client-error` | Frontend entry point → `log.error(extra={"cat": "client"})`; identical messages within 5s are deduplicated so a render loop cannot flood the buffer |
| `GET /api/diagnostics/bundle` | Builds and returns the zip |

### Bundle contents — `tthol-diag-YYYYMMDD-HHMMSS.zip`

- `report.md` — human-readable summary: last 20 ERROR/WARNING entries, environment
  highlights, event timeline. **Reading this alone should usually be enough.**
- `report.json` — environment header, per-session state, the full ring buffer including
  `detail` failure snapshots
- `logs/` — the current log file plus every rotation backup

Served as `StreamingResponse` with `Content-Disposition: attachment`.
`webview.settings["ALLOW_DOWNLOADS"]` is already `True` (`app.py:132`), so WebView2's
native download flyout handles it.

`tthol.sqlite` is excluded — 22 MB and shipped with the app; only its row count and hash
appear in the header.

### Privacy

The bundle contains character names, map names, coordinates, item lists, PIDs, and the exe
path (which may include the Windows username). This is accepted: it is what makes the
bundle diagnostic. The export button lists the contents inline so the user decides before
sending. Nothing is transmitted by the app itself.

## Section 5 — Frontend

New tab **「脈案」** — the traditional term for a diagnostic case record. Two characters,
consistent with 江湖一覽 / 帳房 / 留影.

### Layout (fixed 1024×768; ~1024×700 below TopNav)

- Top row: `Panel「環境」` (app version, log path in use, `knowledge.json` hash, pointer-chain
  constants, per-PID state) and `Panel「操作」` (verbose toggle, export button)
- Below: `Panel「事件」` — filter bar (level / pid / category / text) over the event timeline

Reuses the existing design language: dark palette from `styles.css` tokens, the `Panel`
primitive, inline styles, serif nav labels, mono for data. No new design system.

### UX requirements (from the UX guideline search)

1. **Level must not be conveyed by colour alone** (Color Only, High). Each row is prefixed
   with a text marker — `錯` / `警` / `訊` / `詳` — with colour as reinforcement only.
2. **4.5:1 contrast** (High). Measured against the existing tokens: `--tt-mute #5a6a74` on
   `--tt-panel #141a20` is **3.3:1** and must not carry event body text; `--tt-dim #8a9aa4`
   is **6.3:1** and is fine. Body text uses `--tt-text`, secondary fields use `--tt-dim`.
3. **Errors must be announceable** (High). The export result toast uses `role="status"`.
   The event stream deliberately gets **no** `aria-live` — a 3s cadence would flood a screen
   reader. Unread ERROR count appears as a badge on the tab instead.

### File changes

- `pages/Diagnostics.tsx` — new page
- `api/client.ts` — add `ApiError extends Error { status; detail?; path }`; all four helpers
  throw it, so the backend `detail` finally reaches the UI (fixes F6's second half)
- `diag/report.ts` — `reportClientError(err, ctx)`, with client-side 5s dedup before sending
- `components/ErrorBoundary.tsx` — wraps `<main>`; turns a white screen into a readable
  fallback and reports automatically
- `main.tsx` — `window.onerror` and `unhandledrejection` hooks
- Swallowed catches: `BodyTab.tsx:20`, `ItemsTab.tsx:23`, `Snapshots.tsx:16` become
  display + report. `AutoClickTab.tsx:28` and `KeepActiveTab.tsx:20` are **intentional**
  (the worker may be gone; the manager is absent off-Windows) — they become
  `reportClientError(e, {silent: true})`, reported but not surfaced, preserving current UX.
- `Dashboard.tsx` — surface `last_error` on the character card
- `TopNav.tsx:23` — replace the hardcoded `v0.7.2` with the value from the API (fixes F9)

### Performance

Rendering 1000 rows at once stutters in WebView2. Default to the most recent 200 with a
"load more" control; no virtual-list dependency for a single diagnostics page. While the
page is open, poll `GET /api/diagnostics/events?since=<last_ts>` every 2s for the delta;
stop on navigation away.

## Section 6 — Testing

TDD, using the existing `tests/` pytest layout.

| Test | Covers |
|---|---|
| `test_diagnostics_buffer.py` | Ring-buffer cap, thread safety, `since` filtering, handler mapping `extra` → `DiagEvent` |
| `test_logsetup.py` | Three-tier fallback (primary made unwritable → falls to secondary); **all three failing still leaves the ring buffer collecting**; the formatter survives a third-party record with no `extra` |
| `test_worker_session_errors.py` | Regression for F1/F10: `locate_inventory` returning `None` produces a non-null `session.last_error` and a buffer event |
| `test_diagnostics_router.py` | Event filtering, verbose toggle, client-error dedup, bundle zip contains `report.md` / `report.json` / `logs/` |
| `test_api_error_logging.py` | A raising endpoint produces a logged 500 carrying path and pid |

No frontend test framework is introduced; the frontend relies on types plus manual
verification.

## Acceptance

The design's real acceptance test is external: after implementation, ask the reporting user
to reproduce and export a bundle. `report.md` either confirms F10 or names something else.
Either outcome validates the pipeline — the failure mode being fixed is "the app said
nothing", and any concrete answer proves that is no longer true.
