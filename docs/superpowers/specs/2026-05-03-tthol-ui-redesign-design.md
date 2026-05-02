# Tthol UI Redesign — Design Spec

**Date:** 2026-05-03
**Status:** Draft, awaiting user review
**Source of visual design:** Claude Design bundle archived in `.tmp_design/tthol/`
**Codename:** 御心鑒 (yu xin jian)

---

## 1. Summary

Replace the existing PySide6/Qt UI (`gui/`, `gui_main.py`, `launcher.py`, `gui/launcher_window.py`) with a **pywebview + React + Vite + FastAPI** stack. Keep the entire Python memory layer unchanged. The redesign collapses three nav levels into two, renames technical surfaces with a wuxia-themed vocabulary, and introduces a multi-character live monitoring dashboard as the primary surface.

JS↔Python communication is **HTTP + WebSocket over localhost** (real IPC) — `pywebview` is used purely as the window host, not as a JS bridge.

**This is a destructive replacement** — the old `gui/` directory is deleted. Git is the only safety net (per user request).

---

## 2. Goals and Non-Goals

### Goals
1. **2-level IA**: top-nav (3 pages) + character detail panel. No more sidebar → outer tab → inner tab.
2. **Multi-character monitoring** as the primary screen — tabular row layout supporting 7+ concurrent characters.
3. **Web-design-thinking compatible**: real flexbox/grid, real CSS, modern component model.
4. **Hide technical vocabulary** behind a green/yellow/red status dot (no DISCONNECTED/RESCANNING/LOCATING text in the chrome).
5. **Wuxia thematic copy**: 御心鑒 / 江湖一覽 / 帳房 / 留影 / 行囊 / 庫房 / 氣血 / 內力 / 負重 / 輔助·召喚商人 / 根脈 / 行止.
6. **Keep all Python memory infrastructure intact** — `reader.py`, `auto_detect.py`, `warehouse_scan.py`, `deep_pointer_scan.py`, `knowledge.json`, `tthol.sqlite`, `gui/snapshot_db.py` (relocated), the worker state machine, the auto-click logic, the fake-active keeper.

### Non-Goals (explicit out-of-scope for v1)
- **行止 (map analysis) data wiring**: UI ships, but `行止` tab shows "即將推出". A new SQLite schema for maps/mobs is owed by user; integration is a follow-up.
- **留影 diff view**: snapshot list page exists, but the "increase/decrease vs previous snapshot" diff feature is deferred.
- **Reverse map analysis** ("which of my characters can fight on this map") — out of scope.
- **i18n**: ship Traditional Chinese only. Existing `gui/i18n.py` is dropped along with `gui/`.
- **CSV export polish**: keep parity with current behavior (detail + summary), not redesigned.

---

## 3. Architecture

### 3.1 Process model

```
┌──────────────────────────────────────────────┐
│ bootstrap.py (entry)                         │
│  - Tiny pywebview splash (HTML)              │
│  - git pull + uv sync                        │
│  - On success: spawn app.py and exit         │
│  - On failure: show error, allow run-anyway  │
└──────────────────────────────────────────────┘
                        ↓
┌──────────────────────────────────────────────────────┐
│ app.py (main process)                                │
│                                                      │
│  ┌────────────────────────────────────────────────┐  │
│  │ uvicorn (daemon thread, 127.0.0.1:<random>)    │  │
│  │  ┌──────────────────────────────────────────┐  │  │
│  │  │ FastAPI                                  │  │  │
│  │  │  - /             (static webui/dist)     │  │  │
│  │  │  - /api/*        (REST endpoints)        │  │  │
│  │  │  - /ws/world     (live snapshot push)    │  │  │
│  │  │  - /openapi.json (TS type generation)    │  │  │
│  │  └──────────────────────────────────────────┘  │  │
│  │                       ↕                          │  │
│  │  ┌──────────────────────────────────────────┐  │  │
│  │  │ services/                                │  │  │
│  │  │  - WorkerManager (per-PID)               │  │  │
│  │  │  - SnapshotDB                            │  │  │
│  │  │  - AutoClickManager                      │  │  │
│  │  │  - process_detector / fake_active        │  │  │
│  │  └──────────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────┘  │
│                                                      │
│  pywebview window (main thread)                      │
│    loads http://127.0.0.1:<random>/                  │
│    React fetches /api/* and subscribes to /ws/world  │
└──────────────────────────────────────────────────────┘
```

- **Single Python process**. uvicorn runs in a daemon thread; pywebview owns the main thread for its event loop.
- **Real IPC over localhost**: frontend uses `fetch('/api/...')` and `new WebSocket('/ws/world')`. Backend is a normal FastAPI app — testable with `curl`, `httpx`, OpenAPI viewers, no UI required.
- **Random port** via `socket.bind(('127.0.0.1', 0))` to avoid collisions; port is interpolated into the URL pywebview opens.
- **Bind to 127.0.0.1 only** — never accessible from network.
- **WebSocket `/ws/world`** pushes one `WorldSnapshot` per worker tick (~1.5s). Polling `GET /api/world` is kept as a fallback for first paint and reconnect.
- **Native OS calls** (focus game window via Win32, file dialogs) live in regular `/api/*` endpoints — Python has the full Win32 surface; pywebview is only the renderer host.

### 3.2 Repository layout (after migration)

```
E:/workspace/tthol-memory/
  bootstrap.py            ← NEW: replaces launcher.py (self-update + splash)
  app.py                  ← NEW: replaces gui_main.py (pywebview entry)
  reader.py               ← unchanged
  auto_detect.py          ← unchanged
  warehouse_scan.py       ← unchanged
  deep_pointer_scan.py    ← unchanged
  knowledge.json          ← unchanged
  tthol.sqlite            ← unchanged (new map/mob tables added later)
  pyproject.toml          ← deps changed: drop pyside6/pytest-qt; add pywebview,
                            fastapi, uvicorn[standard], websockets, httpx (test only)

  services/               ← NEW: extracted business logic
    __init__.py
    snapshot_db.py        ← moved from gui/snapshot_db.py
    worker.py             ← moved from gui/worker.py, decoupled from Qt signals
    auto_click.py         ← moved from gui/auto_click_tab.py (logic only)
    fake_active.py        ← moved from gui/fake_active.py
    process_detector.py   ← moved from gui/process_detector.py
    api/                  ← NEW: FastAPI routers
      __init__.py         ←   builds the FastAPI app, mounts /api + /ws + static
      characters.py       ←   /api/characters/*
      snapshots.py        ←   /api/snapshots/*
      accounts.py         ←   /api/accounts/*
      autoclick.py        ←   /api/characters/{pid}/autoclick/*
      export.py           ←   /api/export/*
      world_ws.py         ←   /ws/world WebSocket handler
    api_types.py          ← NEW: Pydantic models (single source of truth for shapes)
    events.py             ← NEW: in-process pubsub feeding /ws/world
    char_session.py       ← NEW: per-PID worker session state

  scripts/                ← NEW
    gen_types.sh          ← `openapi-typescript http://127.0.0.1:<port>/openapi.json
                            -o webui/src/api/types.ts` (run via npm script)

  webui/                  ← NEW: frontend source
    package.json
    vite.config.ts
    tsconfig.json
    index.html
    src/
      main.tsx
      App.tsx
      api/
        client.ts          ← thin fetch/WebSocket wrapper, base URL from window.location
        types.ts           ← generated from /openapi.json (do not hand-edit)
      theme/
        tokens.ts          ← from prototype themes.jsx
        ThemeProvider.tsx
      primitives/
        Bar.tsx
        LinkDot.tsx
        Seal.tsx
        CharChip.tsx
        Panel.tsx
        StatNum.tsx
        FrameCorners.tsx
      pages/
        Dashboard.tsx      ← 江湖一覽 (from variation-a.jsx A_Dashboard)
        Treasury.tsx       ← 帳房 (from treasury-pro.jsx)
        Snapshots.tsx      ← 留影 (from pages.jsx SnapshotsPage)
        CharDetail/
          index.tsx        ← 4-tab shell (from char-detail-pro.jsx)
          BodyTab.tsx      ← 根脈
          ItemsTab.tsx     ← 行囊
          AutoClickTab.tsx ← 輔助
          MapAnalysis.tsx  ← 行止 (placeholder, "coming soon")
      components/
        TopNav.tsx
        ToastStack.tsx
        AlertList.tsx
        RunningACList.tsx
      hooks/
        useLiveChars.ts    ← polls or subscribes for live data
        useAlerts.ts
      mock/
        chars.ts            ← dev-mode mock matching MOCK_CHARS

  webui/dist/             ← built output, loaded by pywebview at runtime

  tests/                  ← existing pytest tree
    test_snapshot_db.py   ← updated for relocated module path
    test_main_window.py   ← DELETED (Qt-specific)
    test_*.py             ← Qt-coupled tests deleted; pure-logic tests kept
    test_api.py           ← NEW: FastAPI route contracts via httpx.AsyncClient
    test_world_ws.py      ← NEW: /ws/world frame shape + tick delivery
    test_events.py        ← NEW: WorldStream pubsub backpressure
```

**Deleted:** entire `gui/` directory, `gui_main.py`, `launcher.py`, `requirements.txt` (uv only now).

---

## 4. HTTP / WebSocket API surface

Pydantic models in `services/api_types.py` are the single source of truth for request/response shapes. FastAPI auto-generates `/openapi.json`; `openapi-typescript` consumes it during `npm run build` to produce `webui/src/api/types.ts`. Drift between Python models and TS types is caught at frontend build time.

All endpoints under `/api/*`. WebSocket under `/ws/*`. Same uvicorn instance also serves `webui/dist/` at `/`.

### 4.1 Discovery / lifecycle
| Method + Path | Body | Response | Notes |
|---|---|---|---|
| `GET /api/characters` | — | `Character[]` | All known game windows; one per PID |
| `POST /api/characters/{pid}/connect` | `ConnectRequest` | `ConnectResult` | Manual reconnect; `hp` optional (auto-chain first) |
| `POST /api/characters/{pid}/disconnect` | — | `{ ok: bool }` | |
| `POST /api/characters/{pid}/relocate` | `{ hp: int }` | `ConnectResult` | Re-scan after game restart |
| `POST /api/characters/{pid}/focus` | — | `{ ok: bool }` | Bring game window forward (Win32) |

### 4.2 Live data
| Method + Path | Body | Response | Notes |
|---|---|---|---|
| `GET /api/world` | — | `WorldSnapshot` | First paint and WS-reconnect fallback |
| `GET /api/characters/{pid}` | — | `CharacterDetail` | Stats + last-known inventory + auto-click status |
| `WS /ws/world` | — | stream of `WorldSnapshot` | Server pushes one frame per worker tick (~1.5s) |

### 4.3 Inventory / warehouse / snapshots
| Method + Path | Body | Response | Notes |
|---|---|---|---|
| `POST /api/characters/{pid}/inventory/scan` | — | `Item[]` | Triggers worker scan, blocks ~1s |
| `POST /api/characters/{pid}/warehouse/scan` | — | `Item[]` | Same |
| `POST /api/snapshots` | `{ pid, source }` | `{ saved: bool, snapshot_id?: int }` | `saved=false` if no diff vs prev |
| `GET /api/snapshots` | query: `SnapshotFilter` | `SnapshotRow[]` | For 帳房 + 留影 |
| `DELETE /api/snapshots/{id}` | — | `{ ok: bool }` | |
| `DELETE /api/characters/by-name/{name}` | — | `{ ok: bool }` | |
| `GET /api/accounts` | — | `Account[]` | |
| `POST /api/accounts` | `{ name }` | `Account` | |
| `PUT /api/characters/by-name/{name}/account` | `{ account_id: int \| null }` | `{ ok: bool }` | |
| `POST /api/export/csv` | `{ mode: 'detail' \| 'summary' }` | `{ rows: int, path: str }` | Writes to `exports/<timestamp>.csv`; frontend opens it via Win32 `os.startfile` through a separate `POST /api/open-path` call |

### 4.4 Auto-click
| Method + Path | Body | Response | Notes |
|---|---|---|---|
| `POST /api/characters/{pid}/autoclick/start` | `AutoClickConfig` | `{ ok: bool }` | |
| `POST /api/characters/{pid}/autoclick/stop` | — | `{ ok: bool }` | |
| `POST /api/characters/{pid}/autoclick/test` | `{ merchant_idx: int }` | `{ ok: bool }` | Single test click |
| `GET /api/characters/{pid}/autoclick/status` | — | `AutoClickStatus` | Also embedded in `WorldSnapshot` |

### 4.5 Live push details (`/ws/world`)

- One connection per pywebview window (frontend opens it on mount, retries on close)
- Server side: a single `events.WorldStream` pubsub. Every WorkerManager tick emits one `WorldSnapshot`; the WebSocket handler forwards each frame to subscribed clients
- Backpressure: if a client falls behind, drop oldest frame (snapshots are idempotent — only "latest" matters)
- Client reconnect: on close, frontend issues `GET /api/world` once, then reopens WS

---

## 5. Frontend pages

### 5.1 江湖一覽 (Dashboard)
- Mirrors `variation-a.jsx::A_Dashboard` 1:1
- Two-column layout: main char table (left) + sidebar (right) with `警示` (alerts) and `輔助執行` (running auto-clicks)
- Char row: link-dot, char chip (avatar/text/number), name+sect+pid, level, HP bar+nums, MP bar+nums, weight bar+nums (compact), position (map+coords), auto-click badge
- Click row → navigate to detail
- Density toggle (compact/normal/comfy) drives row height

### 5.2 帳房 (Treasury)
- Mirrors `treasury-pro.jsx` (品階 column already removed in chat2)
- 4-stat header (種類數 / 件數總計 / 隨身可用 / 七日進出) + search input
- Two-pane: item list left, holders detail right
- Bar chart for holders per item

### 5.3 留影 (Snapshots) — v1 reduced
- List of snapshots (left), detail (right)
- Detail shows: title, when, by, account, item count, raw item list
- **Deferred**: prev-snapshot diff (shown in mock as "增 +28 / 減 -12"). v1 hides this section.

### 5.4 角色詳情 (Character Detail)
- Top: breadcrumb back, seal avatar, name+sect+pid, link-dot+lv+pos, mini HP/MP/weight bars
- 4 tabs: `根脈` / `行囊` / `輔助` / `行止`
  - **根脈**: 六屬 + 七戰 + 動靜 (recent autoclick log). From `char-detail-pro.jsx::BodyTab`.
  - **行囊**: combined inventory+warehouse, filter chips (all/身/庫). From `ItemsTab`.
  - **輔助**: auto-click panel + log. From `ACTab`.
  - **行止**: placeholder card "資料準備中". From `pages.jsx::MapAnalysis`, but data wiring deferred.

### 5.5 Tweaks panel (right-side floating)
- Theme toggle (暗紅 / 暗金 / 水墨青) — persists in localStorage
- Font (黑體 / 中文襯線)
- Density (compact / normal / comfy)
- Chip mode (avatar / text / number)

---

## 6. Connection state model

The state machine in current `gui/worker.py` survives, but its surface area to JS is reduced to a single `link` field per character: `'ok' | 'weak' | 'lost'`.

| Internal state | JS-visible `link` |
|----------------|-------------------|
| LOCATED + reading | `ok` |
| READING but recent error | `weak` |
| DISCONNECTED / READ_ERROR / RESCANNING | `lost` |
| LOCATING (transient) | `weak` |

When `link === 'lost'`, the dashboard row dims to `opacity: 0.45` and stat values render as `---` per the prototype. Manual reconnect surface is reachable via `角色詳情 → 根脈 → 連線狀態 (small section)`.

---

## 7. Bootstrap / self-update (`bootstrap.py`)

**Community-common pattern for pywebview apps**: a tiny separate splash window that handles updating, then spawns the main app.

### 7.1 Behavior
1. Open a 460×260 pywebview window with a static HTML splash (`bootstrap_splash.html`, no React)
2. JS calls `await window.pywebview.api.do_update()`
3. Python:
   - `git symbolic-ref HEAD` (fix detached HEAD if needed)
   - `git pull --ff-only` (capture stdout/stderr, stream to splash via `evaluate_js`)
   - `uv sync` (replaces `pip install -r requirements.txt`)
4. On success: close splash, `subprocess.Popen([sys.executable, "app.py"], ...)`, exit
5. On failure: show error + two buttons:
   - `繼續使用目前版本` → spawn `app.py` anyway
   - `關閉` → quit

### 7.2 Why this pattern
- `pywebview` startup is fast enough to render splash without flicker
- Self-update keeps the existing `git pull` workflow (which the user already validated)
- Keeps update logic out of the main app process — restart-on-update is clean
- Same shape as VS Code, Squirrel.Windows updaters: separate updater binary spawning the app

### 7.3 Replaces
- `launcher.py` (whole file, deleted)
- `gui/launcher_window.py` (whole file, deleted)

---

## 8. Build and packaging

### 8.1 Dev workflow
```
# Terminal 1 — backend (uvicorn auto-reload + pywebview window)
uv run app.py --dev
#  - uvicorn binds 127.0.0.1:<random> with reload=True
#  - pywebview window points to http://127.0.0.1:5173 (Vite)
#  - Vite proxies /api and /ws to the uvicorn port (vite.config.ts)

# Terminal 2 — frontend
cd webui
npm install          # one-time
npm run dev          # Vite dev server on http://localhost:5173 with HMR

# One-time after backend starts: regenerate TS types
cd webui && npm run gen-types
#  - hits http://127.0.0.1:<port>/openapi.json via openapi-typescript
#  - port is written to .omc/.dev-port by app.py for the script to read
```

### 8.2 Production
```
cd webui
npm run build        # outputs webui/dist/

uv run app.py
#  - uvicorn binds 127.0.0.1:<random>
#  - FastAPI mounts webui/dist/ at /
#  - pywebview opens http://127.0.0.1:<random>/
```

### 8.3 End-user distribution (deferred — out of v1 spec scope)
Current: end users `git pull` and run via `bootstrap.py`. PyInstaller bundle is a future task.

---

## 9. Migration plan

This is the high-level order; the detailed implementation plan (writing-plans skill) will break it down further.

1. **Set up `webui/`** with Vite + React + TypeScript scaffolding, port the prototype assets (themes, primitives, mock data) verbatim
2. **Build `services/api_types.py` + `services/api/`** FastAPI routers returning mock data (frontend dev unblocked); confirm `/openapi.json` shape and TS type generation pipeline (`npm run gen-types`)
3. **Move pure-logic services** out of `gui/` into `services/` (`snapshot_db`, `worker`, `auto_click`, `fake_active`, `process_detector`)
4. **Replace mocks with real data** in `services/api/*` routers — wire to relocated services; build `services/events.py` WorldStream and `/ws/world` handler
5. **Build `app.py`**: bind random localhost port, start uvicorn in daemon thread, open pywebview window pointed at `http://127.0.0.1:<port>/`, hook window-close to `server.should_exit = True`
6. **Build `bootstrap.py`** + splash HTML
7. **Delete `gui/`, `gui_main.py`, `launcher.py`, `requirements.txt`**
8. **Update `pyproject.toml`**: drop `pyside6` and `pytest-qt`; add `pywebview`, `fastapi`, `uvicorn[standard]`, `websockets`, and `httpx` (test only)
9. **Rewrite test suite** for new module paths; delete Qt-coupled tests; add FastAPI contract + WebSocket tests
10. **Verify**: 7+ char dashboard live updates over `/ws/world`, scan inventory/warehouse, save snapshot, auto-click start/stop, char detail pages, 留影 list

---

## 10. Testing strategy

### 10.1 What survives
- `test_snapshot_db.py` — updated import paths only (`gui.snapshot_db` → `services.snapshot_db`)
- Pure-logic tests for memory locator (if any) — keep
- `reader.py`'s logic — already mostly script-driven, untouched

### 10.2 What's deleted
- All `pytest-qt` tests (`test_main_window.py`, anything that imports `PySide6` or `pytestqt`)

### 10.3 What's added
- `test_api.py` — FastAPI contract tests using `httpx.AsyncClient` against the in-process app: each endpoint returns a Pydantic model from `services/api_types.py` and `model_dump()` is asserted against fixture JSON. No server boot needed (FastAPI's ASGI test client). The TypeScript types in `webui/src/api/types.ts` are generated from `/openapi.json` (see §4), so contract drift surfaces at frontend build time.
- `test_world_ws.py` — WebSocket test: connect, force a tick, assert one `WorldSnapshot` frame received and shape valid.
- `test_events.py` — pubsub backpressure: ensure slow subscribers drop oldest frames, fast subscribers receive every tick.

### 10.4 Frontend tests
- v1: none (Vite build success + manual smoke test). Vitest scaffolding kept available but no required tests in v1.
- Future: snapshot tests for the dashboard row layout

---

## 11. Risks and mitigations

| Risk | Mitigation |
|------|-----------|
| pywebview's WebView2 backend is missing on Win10 (rare in 2026) | Bootstrap detects, links user to MS download |
| Port collision on `127.0.0.1` | Use `socket.bind(('127.0.0.1', 0))` to grab a random ephemeral port; never hardcode |
| uvicorn thread keeps process alive after window close | pywebview `on_window_close` event triggers `server.should_exit = True`; daemon thread also dies on main exit |
| WebSocket disconnects on laptop sleep / VPN flap | Frontend retries with exponential backoff; falls back to `GET /api/world` poll until WS reopens |
| 7+ chars × 1.5s tick is too heavy for memory reads | Single `WorkerManager` builds one shared `WorldSnapshot` per tick; WS broadcasts the same frame to all subscribers |
| User's Big5 char names contain rare codepoints that JSON-serialize wrong | FastAPI's default `JSONResponse` uses `ensure_ascii=False` via `json.dumps` overrides — verified; existing `reader.py` already handles Big5→str |
| `auto_click` Win32 `PostMessageW` blocks on main thread | Keep in worker thread (current pattern); endpoints just enqueue start/stop |
| 留影 diff feature gets demanded mid-build | Out-of-scope; defer to v1.1 |
| 行止 SQLite schema changes after API is locked | Map/mob types live behind a single `services/maps.py` module; UI shows placeholder until schema is delivered |
| Antivirus / firewall flags localhost server | `127.0.0.1` bind (not `0.0.0.0`) avoids most heuristics; if flagged, README documents exception |

---

## 12. Open dependencies on user

1. **SQLite map/mob schema** for 行止 — owed by user, post-v1
2. **Account model parity check** — confirm new UI's account assignment flow matches current behavior (it should, but worth eyeballing)
3. **PyInstaller bundling decision** — deferred; current `git pull` flow remains valid

---

## 13. What stays exactly as-is

- `reader.py` (memory access core)
- `auto_detect.py`
- `warehouse_scan.py`
- `deep_pointer_scan.py`
- `knowledge.json`
- `tthol.sqlite` (schema unchanged for v1; new tables added later)
- The connection state machine logic (just renamed source path and stripped of Qt signals)
- Inventory snapshot canonicalization (sorted JSON + SHA-256)
- `compat_mode` 4-byte shifted struct handling

---

## 14. Glossary (UI ↔ technical)

| UI | Technical |
|----|-----------|
| 御心鑒 | tthol-memory-reader app name |
| 江湖一覽 | Dashboard / multi-character monitor |
| 帳房 | Cross-character inventory aggregator |
| 留影 | Snapshot history |
| 行囊 | Inventory (in-game on-person) |
| 庫房 | Warehouse |
| 氣血 / 內力 / 負重 | HP / MP / Weight |
| 根脈 | Body / stats tab |
| 行止 | Map analysis (deferred) |
| 輔助·召喚商人 | Auto-click hero summoning |
| 連線燈 (綠/黃/紅) | Connection state badge |
| 雪泥 | (rejected — too abstract; replaced with 留影) |

---

## End of spec
