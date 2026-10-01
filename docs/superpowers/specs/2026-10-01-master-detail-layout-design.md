# Master-Detail Layout — Design Spec

**Date:** 2026-10-01
**Status:** Draft, awaiting user review
**Mockup:** https://claude.ai/artifact/3WALJ67s9fAF8e58baAUhs (artboards "A · …"; layout A chosen, B was the rejected comparison)

---

## 1. Summary

Replace the drill-down navigation (dashboard table → full-page character detail) with a persistent **single sidebar + workspace** layout built for multi-boxing. The sidebar holds global navigation *and* the character list at the same time, so switching characters is one click and never loses context. The selected character's live vitals, errors and recovery actions move into a sticky header at the top of its workspace.

Also enlarge the default window (1280×800, clamped to the screen) and remember the user's last window size and position.

Frontend-only apart from the window geometry in `app.py`. **No API changes.**

## 2. Problems this fixes

Found by reading the current webui (`App.tsx`, `TopNav.tsx`, `Dashboard.tsx`, `pages/CharDetail/*`):

1. **The detail page shows less than the list.** `CharDetail`'s header has only name, sect, Lv and link dot. HP/MP/weight/position, `last_error`, 重偵 and 用血量定位 exist only on the dashboard.
2. **The detail page replaces the whole page.** Switching characters means back → pick → the tab resets to 根脈 (`useState('body')`). Clicking a top-nav tab clears `selectedPid`. Tabs unmount on switch, so ItemsTab loses its search and selection, and each tab re-fetches and flashes 讀取中….
3. **The page goes blank when the selected character disappears.** `page === 'detail' && liveSelected` renders nothing (`App.tsx:33`).
4. **Global and per-character features share one nav level.** For example, 留影 is saved from the 行囊 footer but browsed from the top nav.
5. **Tab order ignores how often each tab is used.** The default tab, 根脈, holds the most static data. 輔助 bundles two unrelated tools.
6. **Character names get truncated.** In the dashboard row, the fixed 320px right column plus the fixed-width columns leave the name column about 45px at 1024px wide, which is roughly two glyphs.

## 3. Goals and non-goals

**Goals**
- Switching to any character takes one click from anywhere and remembers that character's last tab.
- Every character's link state, Lv and location is visible at all times.
- The selected character's vitals, buffs, errors and recovery actions are always on screen while you work in its tabs.
- No blank screens: a character whose worker stopped, or whose game closed, shows an explicit state.
- Scales to 7+ characters. The sidebar scrolls and fits about 10–12 rows at 800px tall.
- Larger default window that never exceeds the screen and is remembered between runs.

**Non-goals**
- No visual restyle: keep the existing tokens, fonts, seal and wuxia vocabulary in `styles.css`.
- No new features (丹爐, route planning and so on), no backend/API changes, no new frontend test framework.
- No collapsible sidebar, drag-to-reorder or pinning of characters (YAGNI for now).

## 4. Layout

```
┌──────────────┬───────────────────────────────────────────────┐
│ 御 御心鑒     │ CharHeader (sticky)                           │
│──────────────│  seal · name · sect·Lv·pid   [保持渲染] [↻ 重偵] │
│ 總覽 全角色一覽│  氣血 ▬▬  內力 ▬▬  負重 ▬▬  方位 map · x,y      │
│ 帳房 全角色道具│  buff chips …            ● 英雄培養執行中      │
│ 留影 背包快照 │  [error / lost banner when applicable]        │
│── 角色 6/7 ──│───────────────────────────────────────────────│
│ ● 月下獨酌    │ 行囊 道具 · 根脈 屬性 · 行止 地圖 · 輔助 英雄培養 │
│   Lv92 · 洛陽 │───────────────────────────────────────────────│
│ ● 青衫客 …    │ tab content (scrolls)                         │
│   (scrolls)   │                                               │
│──────────────│                                               │
│ ⚙ 脈案 診斷 v… │                                               │
└──────────────┴───────────────────────────────────────────────┘
```

- **Sidebar**: fixed 200px wide, full height, top to bottom:
  - brand block
  - global nav: 總覽 / 帳房 / 留影
  - a "角色 n/m 已連" label
  - the scrolling character list
  - footer with the 脈案 button and the app version
- **`TopNav` is removed.** Its version fetch moves to the sidebar footer, and its linked count moves to the 角色 label.
- **Main area** fills the rest. Exactly one sidebar item is highlighted at any time: a global page or a character.

### 4.1 Naming

Keep the stylised names and add a small plain-language subtitle next to each:

| Item | Label | Subtitle |
|---|---|---|
| global | 總覽 | 全角色一覽 |
| global | 帳房 | 全角色道具 |
| global | 留影 | 背包快照 |
| global (footer) | 脈案 | 診斷 |
| char tab | 行囊 | 道具 |
| char tab | 根脈 | 屬性 |
| char tab | 行止 | 地圖 |
| char tab | 輔助 | 英雄培養 |

Wording fix: user-facing text says 庫房 everywhere. The `E_WH_NOT_FOUND` message currently says 倉庫.

### 4.2 Sidebar character row

Each row is a `<button>` at least 44px tall. Per the user's choice it holds only a link dot, the name (serif, ellipsis as a last resort; about 150px ≈ 8–9 glyphs) and one meta line:

| State | Meta line | Row style |
|---|---|---|
| normal | `Lv {level} · {map_name}` | — |
| `last_error.code === 'E_LOCATE_EXHAUSTED'` / not yet located | `pid {pid} · 定位失敗` | dot `weak` |
| `link === 'lost'` (worker stopped, game still running) | `Lv {level} · 偵測已停止` | row at 0.6 opacity |

The order is the order of `snap.chars`, which is what the dashboard uses today.

## 5. Navigation state

`App.tsx` owns:

```ts
type View =
  | { kind: 'overview' } | { kind: 'treasury' } | { kind: 'snapshots' } | { kind: 'diagnostics' }
  | { kind: 'char'; pid: number };
const [view, setView] = useState<View>({ kind: 'overview' });
const [tabByPid, setTabByPid] = useState<Record<number, CharTab>>({});   // default 'items'
```

- `openChar(pid, tab?)` sets the view and, if `tab` is given, sets `tabByPid[pid]`. The sidebar, overview rows, overview alerts, treasury holders and snapshot rows all use it.
- Going to a global page does **not** forget `tabByPid`. Coming back to a character restores its tab.
- Not persisted across app restarts; the app always launches on 總覽.
- `ErrorBoundary` is keyed on `view.kind + pid`, which keeps today's "navigate away clears a crash" behaviour.

### 5.1 Selected character disappears

Two different conditions, confirmed against `services/worker_manager.py` / `char_session.py`:

- **Stopped**: the selected row is present with `link === 'lost'`. The worker gave up (locate retries exhausted, connect or read failure) but the game process still runs; 重偵 restarts it. Render normally, with the row's `last_error` banner, or a 「停」 banner when there is no error (§6.2). Tab content at 0.55 opacity.
- **Gone**: the selected pid is missing from `snap.chars`. `world_snapshot` drops rows whose process exited. Render from `lastSeen` with the 「斷」 banner and a 「回總覽」 button, and stop polling detail. Never render nothing.

`App` keeps a `lastSeen: Map<pid, { row, at }>` ref, updated on every snapshot.
- **Missing from both** (should not happen): fall back to 總覽.

## 6. Character workspace

`pages/CharDetail/` is renamed to `pages/CharWorkspace/`:

```
CharWorkspace/
  index.tsx        // header + tab bar + kept-alive tab panels
  CharHeader.tsx   // §6.1–6.2
  useCharacterDetail.ts  // §6.4
  BodyTab.tsx  ItemsTab.tsx  MapAnalysis.tsx  AutoClickTab.tsx
```

`CharWorkspace` is keyed by `pid`, so switching characters gives each one fresh tab internals. Only the tab choice is remembered across characters.

### 6.1 CharHeader (sticky, fed by the live WS `CharacterRow`)

- **Row 1**: seal (first glyph, `?` when unlocated), link dot + name, then `sect · Lv · pid`. Right-aligned:
  - **保持渲染 toggle**: a switch-style `<button aria-pressed>`. Moved here from the 輔助 tab. It owns the existing `/keep-active/{status,start,stop}` calls and 2s status poll, taken over from `KeepActiveTab`, which is deleted.
  - **↻ 重偵**: same `/rescan` call and error handling as the dashboard today. Gold when the worker is stopped or the character is unlocated; disabled when gone.
- **Row 2**: a 4-column grid with 氣血 / 內力 / 負重 (label, `v / max`, bar) and 方位 (`map · x,y`). HP below 30% turns the HP text `--tt-bad` and appends 偏低, so the warning does not rely on colour alone.
- **Row 3**: `BuffChips`. When `autoclick.running`, a right-aligned `● 英雄培養執行中 · {runtime}s` in `--tt-ok`.
- When the character is unlocated, rows 2–3 are hidden.

### 6.2 Banners (inside the header, below row 3)

- **`last_error` present**: the same `friendlyError()` text as today, moved out of `Dashboard.tsx`. For `E_LOCATE_EXHAUSTED` it includes the `HpRescue` input and 「用血量定位」 button (`/relocate`), also moved from `Dashboard.tsx`.
- **Gone**: 「斷」 badge with 「遊戲程式已關閉 — 以下是 {time} 的最後資料」 (time = last snapshot that contained the pid), plus a 「回總覽」 button.
- **Stopped, no `last_error`**: 「停」 badge with 「角色偵測已停止 — 按「↻ 重偵」重新偵測」.
- **Rescan or relocate failure**: shown inline in the banner area, replacing the dashboard-level `rescanError`.

### 6.3 Tabs

The order is 行囊 (default) / 根脈 / 行止 / 輔助.

- **Kept alive**: a tab panel mounts the first time it is visited and is then hidden with the `hidden` attribute instead of unmounted. Search text, category, view mode and selected item in 行囊 survive tab switches.
- **根脈**: drops its 狀態 panel, because the buffs already sit in the header.
- **輔助**: contains only `AutoClickTab`.
- **行囊**: the footer gains a 「看留影 →」 link next to the save buttons, which opens the 留影 view.

### 6.4 One detail fetch per character

`useCharacterDetail(pid)` polls `GET /api/characters/{pid}` every 3s, the same interval as today. It returns `{ detail, error }`, and `BodyTab` and `ItemsTab` receive the result as props instead of polling on their own.

This halves the requests and removes the 讀取中… flash on tab switches. Polling still runs while 行囊 is hidden, because 行囊 shows inventory freshness. `MapAnalysis` keeps its own map fetch.

## 7. Global pages

- **總覽** (`Dashboard.tsx`): the 320px right column is removed.
  - **Alert strip at the top**: one `<button>` chip per character, first match wins: `last_error` (定位失敗/出錯), then stopped (偵測已停止), then HP below 30%, each calling `openChar`. A right-aligned 「● 輔助執行中：names」.
  - **Character table**: full width. Rows call `openChar`. The name column becomes `minmax(0, 1.4fr)`, and the bar columns become flexible `minmax(0, 1fr)` instead of fixed 88px. An error row shows the friendly text plus 「點進去處理」.
  - 重偵 / HpRescue are no longer on this page; they live in the header (§6.2), so each action exists in one place.
- **帳房 / 留影**: content unchanged. A holder or snapshot `character` name becomes a link (`openChar(pid, 'items')`) **only when exactly one currently listed character has that name**; otherwise it stays plain text. The APIs carry names, not pids.
- **脈案**: unchanged and reached from the sidebar footer.

## 8. Window geometry (`app.py` + new `services/window_prefs.py`)

- `compute_geometry(saved, screens) -> (width, height, x, y)`, a pure function:
  - If `saved` exists and its rect intersects one of `screens` by at least 100×100, use it.
  - Otherwise use 1280×800, clamped to 90% of the primary screen's width/height (logical px), with `x/y = None` so the OS places the window.
- `min_size = (min(1024, w), min(700, h))`, using the clamped size, so the minimum can never exceed a small screen.
- **Storage**: `%APPDATA%\御心鑒\window.json`, `{"width", "height", "x", "y"}`, the same base directory as `snapshot_db.py` / `icon_cache.py`. Read with `encoding='utf-8'`. A missing or corrupt file means defaults; it never raises.
- **Saving**: on `window.events.closing`, read `window.width/height/x/y` and write the file; failures are logged and ignored. The pywebview 5.x docs (context7) confirm `create_window(width, height, x, y, min_size)`, `screen.width/height` and `window.width` are logical px. `window.x/y` and `events.closing` are assumed and get confirmed against the installed version before coding.
- Maximized state is not persisted. Closing while maximized or minimized keeps the previously saved normal rect, tracked via the `maximized`/`restored` events. The `closing` handler runs synchronously on the WinForms UI thread, and `get_size`/`get_position` read the form directly (pywebview 6.2.1), so reading geometry there cannot deadlock.

## 9. Files touched

| File | Change |
|---|---|
| `webui/src/App.tsx` | View state, `tabByPid`, `lastSeen`, sidebar layout |
| `webui/src/components/Sidebar.tsx` | **new**: brand, global nav, character list, footer |
| `webui/src/components/TopNav.tsx` | **deleted** |
| `webui/src/pages/Dashboard.tsx` | Alert strip, full-width table, rescue UI removed |
| `webui/src/pages/CharDetail/` → `CharWorkspace/` | Rename; `CharHeader`, `useCharacterDetail` added; `KeepActiveTab` deleted |
| `webui/src/pages/Treasury.tsx`, `Snapshots.tsx` | Name → `openChar` links |
| `webui/src/components/friendlyError.ts` | **new**: `friendlyError()` moved out of `Dashboard.tsx` (used by Dashboard and CharHeader); `HpRescue` moves into `CharHeader.tsx` |
| `app.py` | Use `window_prefs` for size, position, `min_size`; save on closing |
| `services/window_prefs.py` | **new** |
| `tests/test_window_prefs.py` | **new** |

## 10. Error handling

- Every existing `reportClientError` call keeps its `component` tag (renamed where the component moved, e.g. `CharHeader.rescan`).
- Loading geometry never blocks startup: any exception means defaults.

## 11. Testing and verification

- **`uv run pytest`**: the new `test_window_prefs.py` covers no saved file, a corrupt file, a saved rect on screen, a saved rect off every screen, clamping on a 1366×768 screen, and `min_size` never exceeding a small screen.
- **`npm run build`** in `webui/`: `tsc` type-check plus bundle.
- **Manual run** (`uv run app.py --dev`) with two or more clients, checking:
  - one-click switching keeps each character's tab
  - 行囊 search survives a tab switch
  - a closed game client shows the 斷 banner, not a blank page; an exhausted locate shows the error + HP rescue, not 斷
  - `E_LOCATE_EXHAUSTED` relocation works from the header
  - 保持渲染 toggles from the header
  - the window reopens at its last size
  - names are no longer truncated at the default size
- **UI pass**: run the ui-ux-pro-max skill on the changed components before implementation (project rule).
