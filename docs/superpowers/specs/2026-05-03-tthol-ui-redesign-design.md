# Tthol UI Redesign — Design Spec

**Date:** 2026-05-03
**Status:** Draft, awaiting user review
**Source of visual design:** Claude Design bundle archived in `.tmp_design/tthol/`
**Codename:** 御心鑒 (yu xin jian)

---

## 1. Summary

Replace the existing PySide6/Qt UI (`gui/`, `gui_main.py`, `launcher.py`, `gui/launcher_window.py`) with a **pywebview + React + Vite** stack. Keep the entire Python memory layer unchanged. The redesign collapses three nav levels into two, renames technical surfaces with a wuxia-themed vocabulary, and introduces a multi-character live monitoring dashboard as the primary surface.

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
┌──────────────────────────────────────────────┐
│ app.py (main process)                        │
│                                              │
│  ┌──────────────────┐    ┌────────────────┐ │
│  │ Python services  │ ←→ │ pywebview API  │ │
│  │  - PymemSession  │    │  (js_api class)│ │
│  │  - WorkerManager │    │                │ │
│  │  - SnapshotDB    │    │                │ │
│  │  - AutoClickMgr  │    │                │ │
│  └──────────────────┘    └────────────────┘ │
│           ↑                       ↓          │
│           └───── pywebview window ───────┐   │
│                  loads frontend dist/     │  │
│                  React renders UI         │  │
└──────────────────────────────────────────┘
```

- **Single Python process** owns memory access + a pywebview window.
- **No HTTP / no IPC protocol** — frontend calls `await window.pywebview.api.<method>()` directly. Returns are JSON-serializable.
- **Push from Python → JS** via `window.evaluate_js("window._tthol.push(...)")` for live updates (alternative: JS polls every 1.5s — see §5).

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
  pyproject.toml          ← deps changed: drop pyside6/pytest-qt, add pywebview

  services/               ← NEW: extracted business logic
    __init__.py
    snapshot_db.py        ← moved from gui/snapshot_db.py
    worker.py             ← moved from gui/worker.py, decoupled from Qt signals
    auto_click.py         ← moved from gui/auto_click_tab.py (logic only)
    fake_active.py        ← moved from gui/fake_active.py
    process_detector.py   ← moved from gui/process_detector.py
    api.py                ← NEW: pywebview js_api facade
    api_types.py          ← NEW: Pydantic models (single source of truth for shapes)
    events.py             ← NEW: pub/sub for live updates → JS (deferred)
    char_session.py       ← NEW: per-PID worker session state

  scripts/                ← NEW
    gen_types.py          ← Pydantic models → webui/src/api/types.ts

  webui/                  ← NEW: frontend source
    package.json
    vite.config.ts
    tsconfig.json
    index.html
    src/
      main.tsx
      App.tsx
      api/
        client.ts          ← thin wrapper over window.pywebview.api
        types.ts           ← TypeScript types matching Python dataclasses
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
    test_api.py           ← NEW: js_api method shape contracts
```

**Deleted:** entire `gui/` directory, `gui_main.py`, `launcher.py`, `requirements.txt` (uv only now).

---

## 4. JS-Python API surface (`services/api.py`)

The pywebview `js_api` class. All methods return JSON-serializable structures matching `webui/src/api/types.ts`.

**Types are co-defined**: Pydantic models in `services/api_types.py` are the single source of truth. A small `scripts/gen_types.py` runs during `npm run build` to emit `webui/src/api/types.ts` from those models so the JS side is type-checked. Tables below reference the Pydantic model names; the exact field shapes are produced during the writing-plans phase, not pre-decided here.

### 4.1 Discovery / lifecycle
| Method | Returns | Notes |
|--------|---------|-------|
| `list_characters()` | `Character[]` | All known game windows; one per PID |
| `connect(pid: int, hp: int \| None, options: ConnectOptions)` | `{ ok: bool, error?: str }` | Manual reconnect; `hp` optional (auto-chain first) |
| `disconnect(pid: int)` | `{ ok: bool }` | |
| `relocate(pid: int, hp: int)` | `{ ok: bool, error?: str }` | Re-scan after game restart |
| `focus_window(pid: int)` | `{ ok: bool }` | Bring game window forward (Win32) |

### 4.2 Live data
| Method | Returns | Notes |
|--------|---------|-------|
| `snapshot_state()` | `WorldSnapshot` | All chars + their latest stats. Frontend polls 1.5s. |
| `get_character_detail(pid: int)` | `CharacterDetail` | Stats + last-known inventory + auto-click status |

### 4.3 Inventory / warehouse / snapshots
| Method | Returns | Notes |
|--------|---------|-------|
| `scan_inventory(pid: int)` | `Item[]` | Triggers worker scan, blocks ~1s |
| `scan_warehouse(pid: int)` | `Item[]` | Same |
| `save_snapshot(pid: int, source: 'inventory' \| 'warehouse')` | `{ saved: bool }` | False if no diff |
| `list_snapshots(filter?: SnapshotFilter)` | `SnapshotRow[]` | For 帳房 + 留影 |
| `delete_snapshot(snapshot_id: int)` | `{ ok: bool }` | |
| `delete_character(name: str)` | `{ ok: bool }` | |
| `list_accounts()` | `Account[]` | |
| `set_character_account(name: str, account_id: int \| null)` | `{ ok: bool }` | |
| `create_account(name: str)` | `Account` | |
| `export_csv(mode: 'detail' \| 'summary', path: str)` | `{ rows: int }` | Path picked via pywebview file dialog (separate call) |

### 4.4 Auto-click
| Method | Returns | Notes |
|--------|---------|-------|
| `autoclick_start(pid: int, config: AutoClickConfig)` | `{ ok: bool }` | |
| `autoclick_stop(pid: int)` | `{ ok: bool }` | |
| `autoclick_test(pid: int, merchant_idx: int)` | `{ ok: bool }` | Single test click |
| `autoclick_status(pid: int)` | `AutoClickStatus` | Embedded in `WorldSnapshot` too |

### 4.5 Push-to-JS (optional, deferred behind a flag)

Default v1: **JS polls** `snapshot_state()` every 1.5s. Simpler, no event subscription bookkeeping.

If polling proves too laggy, add `app.window.evaluate_js("window._tthol.onTick(...)")` from a Python `events.py` pubsub. Defer until measured.

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
# Frontend
cd webui
npm install          # one-time
npm run dev          # Vite dev server on http://localhost:5173

# In another terminal:
uv run app.py --dev  # pywebview window points to http://localhost:5173 with hot reload
```

### 8.2 Production
```
cd webui
npm run build        # outputs webui/dist/

uv run app.py        # pywebview loads webui/dist/index.html via file://
```

### 8.3 End-user distribution (deferred — out of v1 spec scope)
Current: end users `git pull` and run via `bootstrap.py`. PyInstaller bundle is a future task.

---

## 9. Migration plan

This is the high-level order; the detailed implementation plan (writing-plans skill) will break it down further.

1. **Set up `webui/`** with Vite + React + TypeScript scaffolding, port the prototype assets (themes, primitives, mock data) verbatim
2. **Build `services/api.py`** with all js_api methods returning mock data (frontend dev unblocked)
3. **Move pure-logic services** out of `gui/` into `services/` (`snapshot_db`, `worker`, `auto_click`, `fake_active`, `process_detector`)
4. **Replace mock with real data** in `services/api.py` — wire to relocated services
5. **Build `app.py`** that opens pywebview window, instantiates `Api`, loads `webui/dist/` (or dev server)
6. **Build `bootstrap.py`** + splash HTML
7. **Delete `gui/`, `gui_main.py`, `launcher.py`, `requirements.txt`**
8. **Update `pyproject.toml`** (drop pyside6/pytest-qt, add pywebview)
9. **Rewrite test suite** for new module paths; delete Qt-coupled tests
10. **Verify**: 7+ char dashboard live updates, scan inventory/warehouse, save snapshot, auto-click start/stop, char detail pages, 留影 list

---

## 10. Testing strategy

### 10.1 What survives
- `test_snapshot_db.py` — updated import paths only (`gui.snapshot_db` → `services.snapshot_db`)
- Pure-logic tests for memory locator (if any) — keep
- `reader.py`'s logic — already mostly script-driven, untouched

### 10.2 What's deleted
- All `pytest-qt` tests (`test_main_window.py`, anything that imports `PySide6` or `pytestqt`)

### 10.3 What's added
- `test_api.py` — contract tests on `services/api.py`: each method returns a Pydantic model from `services/api_types.py` and `model_dump()` is asserted against fixture JSON. The TypeScript types in `webui/src/api/types.ts` are generated from the same Pydantic models (see §4) so drift is caught at build time, not runtime.
- `test_event_dispatch.py` — if push-to-JS is added, validate ordering and dedup

### 10.4 Frontend tests
- v1: none (Vite build success + manual smoke test). Vitest scaffolding kept available but no required tests in v1.
- Future: snapshot tests for the dashboard row layout

---

## 11. Risks and mitigations

| Risk | Mitigation |
|------|-----------|
| pywebview's WebView2 backend is missing on Win10 (rare in 2026) | Bootstrap detects, links user to MS download |
| 7+ chars × 1.5s polling is too heavy for memory reads | If profiling shows >100ms per snapshot, switch to push-from-Python with shared snapshot built once per tick |
| User's Big5 char names contain rare codepoints that JSON-serialize wrong | Force `ensure_ascii=False, encoding='utf-8'` everywhere; existing `reader.py` already handles Big5→str |
| `auto_click` Win32 `PostMessageW` runs on the main thread → blocks UI | Keep in worker thread (current pattern); only the public start/stop crosses to API |
| Frontend file:// origin can't `import()` from CDN | Bundle React/Babel into Vite's output; no runtime CDN |
| 留影 diff feature gets demanded mid-build | Out-of-scope; defer to v1.1 |
| 行止 SQLite schema changes after API is locked | Map/mob types live behind a single `services/maps.py` module; UI shows placeholder until schema is delivered |

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
