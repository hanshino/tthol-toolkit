# Master-Detail Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dashboard → full-page detail drill-down with a persistent sidebar (global nav + character list) and a per-character workspace whose sticky header carries live vitals, errors and recovery actions. Also open the window at 1280×800 (clamped to the screen) and remember its geometry.

**Architecture:** `App.tsx` owns a `View` union plus a per-pid tab memory and renders `Sidebar` + one main view. The selected character's view is `CharWorkspace`, keyed by pid:
- `CharHeader`: driven by the live WS row
- a tab bar
- kept-alive tab panels fed by one shared `useCharacterDetail` poll

Window geometry is a pure Python function (`services/window_prefs.py`) that `app.py` feeds with `webview.screens` and a JSON file in `%APPDATA%\御心鑒`.

**Tech Stack:** React 18 + TypeScript + Vite (no frontend test runner; `npm run build` = `tsc && vite build` is the gate), Python 3 + pywebview 6.2.1 + pytest via `uv run`.

**Spec:** `docs/superpowers/specs/2026-10-01-master-detail-layout-design.md`

## Global Constraints

- All code output (comments, logs, identifiers) in English; user-facing UI copy in Traditional Chinese.
- Always `uv run` for Python (`uv run pytest`, `uv run app.py --dev`); never bare `python`.
- Read/write JSON and text with `encoding="utf-8"`.
- No backend API changes; no new npm dependencies; no frontend test framework.
- Keep the existing design tokens in `webui/src/styles.css` (`--tt-*`). Inline hex only where the spec's mockup used a value with no token (the `#3f6a5e` switch track).
- Defaults: window 1280×800; clamp to 90% of the primary screen; `min_size = (min(1024, w), min(700, h))`; sidebar 200px.
- Stylised names keep a small subtitle: 總覽·全角色一覽, 帳房·全角色道具, 留影·背包快照, 脈案·診斷, 行囊·道具, 根脈·屬性, 行止·地圖, 輔助·召喚商人.
- The default character tab is 行囊 (`'items'`). The app always launches on 總覽.
- Before touching UI code, run the ui-ux-pro-max skill (project rule, Task 3 Step 1).

## Review Focus

1. **Window minimized at close.** Windows reports x/y ≈ -32000; saving that would put the window off-screen next launch. Expect: geometry is not saved. Pinned by `test_remember_skips_minimized_window` (Task 1).
2. **Saved monitor no longer attached** (laptop undocked). Expect: default size, OS-placed. Pinned by `test_saved_rect_off_every_screen_uses_default` (Task 1).
3. **Game client closed while its workspace is open.** Expect a 「斷」 banner over greyed last data, never a blank main area. Manual check in Task 7 (no frontend test runner).
4. **Two accounts with the same character name in 帳房/留影.** Expect the name stays plain text, with no link to the wrong pid. Manual check in Task 7. The logic sits in `pidForName` (Task 2) so it is a single, readable function.
5. **Switching characters while a detail fetch is in flight.** Expect the old pid's response never to show in the new workspace. Guaranteed structurally: `CharWorkspace` is keyed by pid and the hook ignores late responses (`alive` flag, Task 4). Manual check in Task 7.

---

## File structure

| Path | Responsibility |
|---|---|
| `services/window_prefs.py` (new) | Load/save geometry JSON, compute the initial geometry, remember it on close |
| `tests/test_window_prefs.py` (new) | Unit tests for the above |
| `app.py` (modify) | Wire `window_prefs` into `create_window` and `events.closing` |
| `webui/src/nav.ts` (new) | `View`, `CharTab`, `GlobalView`, `OpenChar` types; `isUnlocated`, `pidForName` helpers |
| `webui/src/components/friendlyError.ts` (new) | `friendlyError()` (moved from Dashboard) |
| `webui/src/components/Sidebar.tsx` + `sidebar.css` (new) | Brand, global nav, character list, 脈案 footer + version |
| `webui/src/components/TopNav.tsx` (delete) | Replaced by Sidebar |
| `webui/src/App.tsx` (modify) | Shell layout, view state, tab memory, lastSeen |
| `webui/src/styles.css` (modify) | `.app-shell` / `.app-main` |
| `webui/src/pages/CharWorkspace/` (renamed from `CharDetail/`) | `index.tsx`, `CharHeader.tsx`, `useCharacterDetail.ts`, `useKeepActive.ts`, `workspace.css`, tabs |
| `webui/src/pages/CharWorkspace/KeepActiveTab.tsx` (delete) | Moved into the header toggle |
| `webui/src/pages/Dashboard.tsx` (rewrite) | 總覽: alert strip + full-width table |
| `webui/src/pages/Treasury.tsx`, `Snapshots.tsx` (modify) | Holder/snapshot name → open character |
| `webui/src/components/items/items.css` (modify) | `.tr-holder-link` |

---

### Task 1: Window geometry (`services/window_prefs.py` + `app.py`)

**Files:**
- Create: `services/window_prefs.py`
- Create: `tests/test_window_prefs.py`
- Modify: `app.py:1-30` (import), `app.py:135-136` (create_window)

**Interfaces:**
- Produces:
  - `Geometry(width: int, height: int, x: int | None, y: int | None, min_size: tuple[int, int])`
  - `default_prefs_path() -> Path`
  - `load_saved(path: Path) -> dict[str, int] | None`
  - `save(path: Path, width: int, height: int, x: int, y: int) -> None`
  - `compute_geometry(saved: dict | None, screens: Iterable[ScreenLike]) -> Geometry`
  - `remember(path: Path, window) -> None`

- [ ] **Step 1: Write the failing tests**

`tests/test_window_prefs.py`:

```python
from types import SimpleNamespace

from services import window_prefs as wp


def scr(x, y, w, h):
    return SimpleNamespace(x=x, y=y, width=w, height=h)


FHD = scr(0, 0, 1920, 1080)


def test_no_saved_uses_default_on_large_screen():
    g = wp.compute_geometry(None, [FHD])
    assert (g.width, g.height, g.x, g.y) == (1280, 800, None, None)
    assert g.min_size == (1024, 700)


def test_default_clamped_to_small_screen():
    g = wp.compute_geometry(None, [scr(0, 0, 1366, 768)])
    assert (g.width, g.height) == (1229, 691)
    assert g.min_size == (1024, 691)


def test_no_screens_falls_back_to_default():
    g = wp.compute_geometry(None, [])
    assert (g.width, g.height, g.x, g.y) == (1280, 800, None, None)


def test_primary_is_screen_at_origin():
    left = scr(-1366, 0, 1366, 768)
    g = wp.compute_geometry(None, [left, FHD])
    assert (g.width, g.height) == (1280, 800)


def test_saved_rect_on_screen_is_used():
    saved = {"width": 1500, "height": 900, "x": 100, "y": 50}
    g = wp.compute_geometry(saved, [FHD])
    assert (g.width, g.height, g.x, g.y) == (1500, 900, 100, 50)


def test_saved_rect_on_secondary_screen_is_used():
    saved = {"width": 1280, "height": 800, "x": -1300, "y": 0}
    g = wp.compute_geometry(saved, [FHD, scr(-1920, 0, 1920, 1080)])
    assert (g.x, g.y) == (-1300, 0)


def test_saved_rect_off_every_screen_uses_default():
    saved = {"width": 1280, "height": 800, "x": 5000, "y": 0}
    g = wp.compute_geometry(saved, [FHD])
    assert (g.width, g.height, g.x, g.y) == (1280, 800, None, None)


def test_saved_rect_barely_visible_uses_default():
    saved = {"width": 1280, "height": 800, "x": 1870, "y": 0}  # 50px visible
    g = wp.compute_geometry(saved, [FHD])
    assert g.x is None


def test_saved_smaller_than_min_is_bumped_up():
    saved = {"width": 600, "height": 400, "x": 0, "y": 0}
    g = wp.compute_geometry(saved, [FHD])
    assert (g.width, g.height) == (1024, 700)


def test_load_missing_file_returns_none(tmp_path):
    assert wp.load_saved(tmp_path / "window.json") is None


def test_load_corrupt_file_returns_none(tmp_path):
    p = tmp_path / "window.json"
    p.write_text("{not json", encoding="utf-8")
    assert wp.load_saved(p) is None


def test_load_missing_keys_returns_none(tmp_path):
    p = tmp_path / "window.json"
    p.write_text('{"width": 1280}', encoding="utf-8")
    assert wp.load_saved(p) is None


def test_save_then_load_round_trips(tmp_path):
    p = tmp_path / "sub" / "window.json"
    wp.save(p, 1400, 860, 10, 20)
    assert wp.load_saved(p) == {"width": 1400, "height": 860, "x": 10, "y": 20}


def test_remember_saves_window_geometry(tmp_path):
    p = tmp_path / "window.json"
    wp.remember(p, SimpleNamespace(width=1300, height=820, x=5, y=6))
    assert wp.load_saved(p) == {"width": 1300, "height": 820, "x": 5, "y": 6}


def test_remember_skips_minimized_window(tmp_path):
    p = tmp_path / "window.json"
    wp.remember(p, SimpleNamespace(width=160, height=28, x=-32000, y=-32000))
    assert not p.exists()


def test_remember_never_raises(tmp_path):
    class Broken:
        @property
        def width(self):
            raise RuntimeError("window gone")

    wp.remember(tmp_path / "window.json", Broken())  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_window_prefs.py -v`
Expected: FAIL with `ImportError: cannot import name 'window_prefs'`.

- [ ] **Step 3: Implement `services/window_prefs.py`**

```python
"""Window geometry: a default size clamped to the screen, and the last size remembered.

Geometry lives in %APPDATA%\\御心鑒\\window.json beside the snapshot DB so it
survives reinstalls. Everything here is best-effort: a missing or corrupt file
falls back to defaults and nothing raises into startup or shutdown.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

from services._paths import app_root

log = logging.getLogger("tthol.window_prefs")

DEFAULT_SIZE = (1280, 800)
MIN_SIZE = (1024, 700)
SCREEN_FRACTION = 0.9
# A saved rect must overlap some screen by at least this much on both axes,
# otherwise the window would open somewhere the user cannot grab it.
MIN_VISIBLE = 100
# Windows parks minimized windows near (-32000, -32000).
_MINIMIZED_COORD = -10000
_KEYS = ("width", "height", "x", "y")


class ScreenLike(Protocol):
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class Geometry:
    width: int
    height: int
    x: int | None
    y: int | None
    min_size: tuple[int, int]


def default_prefs_path() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) / "御心鑒" if appdata else app_root()
    return base / "window.json"


def load_saved(path: Path) -> dict[str, int] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        log.warning("window prefs unreadable, using defaults: %s", e)
        return None
    try:
        return {k: int(data[k]) for k in _KEYS}
    except (KeyError, TypeError, ValueError):
        log.warning("window prefs malformed, using defaults: %r", data)
        return None


def save(path: Path, width: int, height: int, x: int, y: int) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"width": int(width), "height": int(height), "x": int(x), "y": int(y)}
        path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError as e:
        log.warning("could not save window prefs: %s", e)


def _primary(screens: list[ScreenLike]) -> ScreenLike | None:
    for s in screens:
        if s.x == 0 and s.y == 0:
            return s
    return screens[0] if screens else None


def _visible_on(saved: dict[str, int], s: ScreenLike) -> bool:
    w = min(saved["x"] + saved["width"], s.x + s.width) - max(saved["x"], s.x)
    h = min(saved["y"] + saved["height"], s.y + s.height) - max(saved["y"], s.y)
    return w >= MIN_VISIBLE and h >= MIN_VISIBLE


def compute_geometry(saved: dict[str, int] | None, screens: Iterable[ScreenLike]) -> Geometry:
    screens = list(screens)
    primary = _primary(screens)
    w, h = DEFAULT_SIZE
    if primary is not None:
        w = min(w, int(primary.width * SCREEN_FRACTION))
        h = min(h, int(primary.height * SCREEN_FRACTION))
    min_size = (min(MIN_SIZE[0], w), min(MIN_SIZE[1], h))
    if saved and any(_visible_on(saved, s) for s in screens):
        return Geometry(
            max(saved["width"], min_size[0]),
            max(saved["height"], min_size[1]),
            saved["x"],
            saved["y"],
            min_size,
        )
    return Geometry(w, h, None, None, min_size)


def remember(path: Path, window) -> None:
    """Persist the window's current geometry; called from the closing event."""
    try:
        width, height, x, y = window.width, window.height, window.x, window.y
    except Exception as e:  # the window may already be torn down
        log.warning("could not read window geometry: %s", e)
        return
    if x <= _MINIMIZED_COORD or y <= _MINIMIZED_COORD or width <= 0 or height <= 0:
        return
    save(path, width, height, x, y)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_window_prefs.py -v`
Expected: all 16 PASS.

- [ ] **Step 5: Wire into `app.py`**

Add the import next to the other `services` imports:

```python
from services import window_prefs
```

Replace the single line `webview.create_window("御心鑒", target_url, width=1024, height=768)` with:

```python
    # Geometry is best-effort: a screen query failure or a bad prefs file
    # falls back to the clamped default instead of blocking startup.
    prefs_path = window_prefs.default_prefs_path()
    try:
        screens = list(webview.screens)
    except Exception:
        screens = []
    geo = window_prefs.compute_geometry(window_prefs.load_saved(prefs_path), screens)
    window = webview.create_window(
        "御心鑒", target_url,
        width=geo.width, height=geo.height, x=geo.x, y=geo.y, min_size=geo.min_size,
    )
    window.events.closing += lambda: window_prefs.remember(prefs_path, window)
```

- [ ] **Step 6: Run the full Python suite**

Run: `uv run pytest`
Expected: all PASS (no existing test touches `create_window`).

- [ ] **Step 7: Commit**

```bash
git add services/window_prefs.py tests/test_window_prefs.py app.py
git commit -m "feat(app): open at 1280x800 clamped to the screen and remember window geometry"
```

---

### Task 2: Shared nav types and `friendlyError`

**Files:**
- Create: `webui/src/nav.ts`
- Create: `webui/src/components/friendlyError.ts`
- Modify: `webui/src/pages/Dashboard.tsx` (delete its local `friendlyError`, import the shared one)

**Interfaces:**
- Produces (`nav.ts`):
  - `type CharTab = 'items' | 'body' | 'maps' | 'assist'`
  - `type GlobalView = 'overview' | 'treasury' | 'snapshots' | 'diagnostics'`
  - `type View = { kind: GlobalView } | { kind: 'char'; pid: number }`
  - `type OpenChar = (pid: number, tab?: CharTab) => void`
  - `isUnlocated(c: CharacterRow): boolean`
  - `pidForName(chars: CharacterRow[], name: string): number | null`
- Produces (`friendlyError.ts`): `friendlyError(e: ErrorInfo): string`

- [ ] **Step 1: Create `webui/src/nav.ts`**

```ts
import type { CharacterRow } from './api/types';

export type CharTab = 'items' | 'body' | 'maps' | 'assist';
export type GlobalView = 'overview' | 'treasury' | 'snapshots' | 'diagnostics';
export type View = { kind: GlobalView } | { kind: 'char'; pid: number };
export type OpenChar = (pid: number, tab?: CharTab) => void;

/**
 * The worker emits a placeholder row (name "(連線中)", level 0, empty vitals)
 * until a character has located once; see worker_manager._placeholder_row.
 */
export function isUnlocated(c: CharacterRow): boolean {
  return c.level === 0 && c.vitals.hp_max === 0;
}

/**
 * Treasury and snapshot rows carry a character name, not a pid. Link only when
 * exactly one live character has that name: two accounts can share a name and
 * a wrong jump is worse than no jump.
 */
export function pidForName(chars: CharacterRow[], name: string): number | null {
  const hits = chars.filter(c => c.name === name);
  return hits.length === 1 ? hits[0].pid : null;
}
```

- [ ] **Step 2: Create `webui/src/components/friendlyError.ts`**

This is the body moved from `Dashboard.tsx`, plus the 庫房 wording fix for `E_WH_NOT_FOUND`:

```ts
import type { ErrorInfo } from '../api/types';

export function friendlyError(e: ErrorInfo): string {
  switch (e.code) {
    case 'E_WH_NOT_FOUND':
      return '庫房尚未讀取 — 請先在遊戲中打開倉庫視窗';
    case 'E_INV_NOT_FOUND':
      return '找不到背包資料 — 可換張地圖後按「↻ 重偵」';
    case 'E_LOCATE_EXHAUSTED':
      // Not necessarily a login problem: when a game update invalidates the
      // pointer chain, 重偵 re-runs the same dead chain and can never succeed.
      // Manual HP is the fallback that still works, so lead with it.
      return '找不到角色 — 若已登入仍失敗，請於下方輸入目前血量定位';
    case 'E_PROC_GONE':
      return '無法連上遊戲程式 — 遊戲可能已關閉';
    case 'E_CHAIN_READ':
      return '尚未登入角色';
    default:
      return e.message;
  }
}
```

- [ ] **Step 3: Point Dashboard at it**

In `webui/src/pages/Dashboard.tsx`:
- Delete the whole `function friendlyError(...) { ... }` at the bottom of the file.
- Add `import { friendlyError } from '../components/friendlyError';`

- [ ] **Step 4: Build**

Run: `cd webui && npm run build`
Expected: exits 0, no TS errors.

- [ ] **Step 5: Commit**

```bash
git add webui/src/nav.ts webui/src/components/friendlyError.ts webui/src/pages/Dashboard.tsx
git commit -m "refactor(webui): extract nav types and friendlyError for the new shell"
```

---

### Task 3: Sidebar + app shell (TopNav removed)

**Files:**
- Create: `webui/src/components/Sidebar.tsx`, `webui/src/components/sidebar.css`
- Delete: `webui/src/components/TopNav.tsx`
- Modify: `webui/src/App.tsx` (whole file), `webui/src/styles.css` (append), `webui/src/pages/CharDetail/index.tsx` (make `onBack` optional, hide the back button when absent)

**Interfaces:**
- Consumes: `View`, `GlobalView`, `OpenChar`, `isUnlocated` (Task 2).
- Produces: `Sidebar({ view, chars, onNav, onOpenChar }: { view: View; chars: CharacterRow[]; onNav: (k: GlobalView) => void; onOpenChar: (pid: number) => void })`. `App` holds `view`, `tabByPid`, and `openChar: OpenChar`.

- [ ] **Step 1: Run the ui-ux-pro-max skill**

Invoke the `ui-ux-pro-max:ui-ux-pro-max` skill (project rule) with: "review a dark desktop sidebar navigation + character list for a 1280×800 pywebview tool; existing tokens in webui/src/styles.css". Note any accessibility or interaction findings that contradict this plan. Apply only findings that fit the spec, and list them in the Task 3 commit body.

- [ ] **Step 2: Create `webui/src/components/sidebar.css`**

```css
/* Sidebar: global nav + character list. Button rules are written as
   `button.x` so they outrank the global button styles in styles.css. */
.sb {
  width: 200px; flex: none; display: flex; flex-direction: column; min-height: 0;
  background: var(--tt-panel); border-right: 1px solid var(--tt-line);
}
.sb-brand { display: flex; align-items: center; gap: 10px; padding: 14px 16px; border-bottom: 1px solid var(--tt-line-soft); }
.sb-title { font-family: var(--tt-font-serif); font-size: 15px; font-weight: 600; letter-spacing: 4px; }
.sb-tag { font-size: 10px; color: var(--tt-dim); letter-spacing: 1px; }
.sb-nav { display: flex; flex-direction: column; gap: 2px; padding: 8px; }

button.sb-item, button.sb-char {
  display: flex; width: 100%; text-align: left; letter-spacing: 0;
  background: transparent; border: 1px solid transparent; color: var(--tt-text);
}
button.sb-item { align-items: baseline; gap: 8px; min-height: 36px; padding: 8px 10px; }
button.sb-char { align-items: center; gap: 10px; min-height: 44px; padding: 6px 10px; }
button.sb-item:hover:not(:disabled), button.sb-char:hover:not(:disabled) {
  background: var(--tt-raised); border-color: var(--tt-line-soft);
}
button.sb-item[aria-current="page"], button.sb-char[aria-current="page"] {
  background: var(--tt-raised); border-color: var(--tt-line);
}
button.sb-item[aria-current="page"] .sb-item-n,
button.sb-char[aria-current="page"] .sb-char-name { color: var(--tt-gold); }
button.sb-item:focus-visible, button.sb-char:focus-visible { outline: 1px solid var(--tt-gold); outline-offset: -1px; }
button.sb-char[data-lost] { opacity: 0.6; }

.sb-item-n { font-family: var(--tt-font-serif); font-size: 14px; letter-spacing: 3px; }
.sb-item-s { font-size: 10.5px; color: var(--tt-dim); }
.sb-sec {
  display: flex; justify-content: space-between; align-items: baseline;
  padding: 10px 16px 6px; border-top: 1px solid var(--tt-line-soft);
  font-size: 11px; color: var(--tt-dim); letter-spacing: 2px;
}
.sb-mono { font-family: var(--tt-font-mono); letter-spacing: 0; }
.sb-chars { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 2px; padding: 0 8px 8px; }
.sb-empty { padding: 12px 8px; font-size: 11px; color: var(--tt-dim); line-height: 1.6; }
.sb-char-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.sb-char-name { font-family: var(--tt-font-serif); font-size: 13px; letter-spacing: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sb-char-meta { font-size: 10.5px; color: var(--tt-dim); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sb-foot { display: flex; align-items: center; justify-content: space-between; gap: 6px; padding: 8px; border-top: 1px solid var(--tt-line-soft); }
button.sb-item.sb-diag { width: auto; min-height: 32px; align-items: center; }
.sb-ver { font-size: 10px; color: var(--tt-dim); }
```

- [ ] **Step 3: Create `webui/src/components/Sidebar.tsx`**

```tsx
import { useEffect, useState } from 'react';
import { get } from '../api/client';
import type { CharacterRow, DiagSummary } from '../api/types';
import { isUnlocated, type GlobalView, type View } from '../nav';
import { LinkDot, Seal } from '../primitives';
import './sidebar.css';

const GLOBAL_NAV: { k: GlobalView; n: string; s: string }[] = [
  { k: 'overview', n: '總覽', s: '全角色一覽' },
  { k: 'treasury', n: '帳房', s: '全角色道具' },
  { k: 'snapshots', n: '留影', s: '背包快照' },
];

function charMeta(c: CharacterRow): string {
  if (isUnlocated(c)) {
    return c.last_error?.code === 'E_LOCATE_EXHAUSTED' ? `pid ${c.pid} · 定位失敗` : `pid ${c.pid} · 連線中`;
  }
  if (c.link === 'lost') return `Lv ${c.level} · 已斷線`;
  return `Lv ${c.level} · ${c.position.map_name ?? '—'}`;
}

export function Sidebar({
  view, chars, onNav, onOpenChar,
}: {
  view: View; chars: CharacterRow[];
  onNav: (k: GlobalView) => void; onOpenChar: (pid: number) => void;
}) {
  // The version used to be hardcoded in the header and drifted five releases
  // behind, so users reported a version that no longer existed.
  const [version, setVersion] = useState('');
  useEffect(() => {
    get<DiagSummary>('/api/diagnostics/summary')
      .then(s => setVersion(String((s.environment as Record<string, unknown>).app_version ?? '')))
      .catch(() => { /* cosmetic only; 脈案 reports the real failure */ });
  }, []);
  const linked = chars.filter(c => c.link === 'ok').length;
  const current = (k: GlobalView) => (view.kind === k ? 'page' : undefined);

  return (
    <aside className="sb">
      <div className="sb-brand">
        <Seal size={30}>御</Seal>
        <div>
          <div className="sb-title">御心鑒</div>
          <div className="sb-tag">tthol memory reader</div>
        </div>
      </div>
      <nav className="sb-nav" aria-label="全域">
        {GLOBAL_NAV.map(n => (
          <button key={n.k} type="button" className="sb-item" aria-current={current(n.k)} onClick={() => onNav(n.k)}>
            <span className="sb-item-n">{n.n}</span>
            <span className="sb-item-s">{n.s}</span>
          </button>
        ))}
      </nav>
      <div className="sb-sec">
        <span>角色</span>
        <span className="sb-mono">{linked}/{chars.length} 已連</span>
      </div>
      <nav className="sb-chars" aria-label="角色">
        {chars.length === 0 && <div className="sb-empty">尚未偵測到遊戲程式 — 開啟遊戲後會自動出現在這裡</div>}
        {chars.map(c => (
          <button
            key={c.pid}
            type="button"
            className="sb-char"
            aria-current={view.kind === 'char' && view.pid === c.pid ? 'page' : undefined}
            data-lost={c.link === 'lost' || undefined}
            title={`${c.name} · pid ${c.pid}`}
            onClick={() => onOpenChar(c.pid)}
          >
            <LinkDot status={c.link} />
            <span className="sb-char-text">
              <span className="sb-char-name">{c.name}</span>
              <span className="sb-char-meta">{charMeta(c)}</span>
            </span>
          </button>
        ))}
      </nav>
      <div className="sb-foot">
        <button type="button" className="sb-item sb-diag" aria-current={current('diagnostics')} onClick={() => onNav('diagnostics')}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <circle cx="12" cy="12" r="3" />
            <path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1" />
          </svg>
          <span className="sb-item-n">脈案</span>
          <span className="sb-item-s">診斷</span>
        </button>
        <span className="sb-mono sb-ver">{version ? `v${version}` : ''}</span>
      </div>
    </aside>
  );
}
```

- [ ] **Step 4: Append the shell classes to `webui/src/styles.css`**

```css
/* ---- App shell ---------------------------------------------------- */
.app-shell { display: flex; height: 100vh; background: var(--tt-bg); color: var(--tt-text); }
.app-main { flex: 1; min-width: 0; overflow-y: auto; }
```

- [ ] **Step 5: Rewrite `webui/src/App.tsx` (interim: still uses CharDetail)**

```tsx
import { useCallback, useState } from 'react';
import { ErrorBoundary } from './components/ErrorBoundary';
import { Sidebar } from './components/Sidebar';
import { useLiveChars } from './hooks/useLiveChars';
import type { CharTab, GlobalView, OpenChar, View } from './nav';
import { Dashboard } from './pages/Dashboard';
import { Treasury } from './pages/Treasury';
import { Snapshots } from './pages/Snapshots';
import { Diagnostics } from './pages/Diagnostics';
import { CharDetail } from './pages/CharDetail';

export function App() {
  const [view, setView] = useState<View>({ kind: 'overview' });
  const [, setTabByPid] = useState<Record<number, CharTab>>({});
  const snap = useLiveChars();

  const nav = useCallback((k: GlobalView) => setView({ kind: k }), []);
  const openChar = useCallback<OpenChar>((pid, tab) => {
    if (tab) setTabByPid(m => ({ ...m, [pid]: tab }));
    setView({ kind: 'char', pid });
  }, []);

  const selected = view.kind === 'char' ? snap.chars.find(c => c.pid === view.pid) : undefined;
  // Keyed per view so navigating away from a crashed view clears the fallback.
  const boundaryKey = view.kind === 'char' ? `char-${view.pid}` : view.kind;

  return (
    <div className="app-shell">
      <Sidebar view={view} chars={snap.chars} onNav={nav} onOpenChar={openChar} />
      <main className="app-main">
        <ErrorBoundary key={boundaryKey} component={boundaryKey}>
          {view.kind === 'overview' && <Dashboard chars={snap.chars} onPick={c => openChar(c.pid)} />}
          {view.kind === 'treasury' && <Treasury />}
          {view.kind === 'snapshots' && <Snapshots />}
          {view.kind === 'diagnostics' && <Diagnostics />}
          {view.kind === 'char' && (selected
            ? <CharDetail char={selected} />
            : <Dashboard chars={snap.chars} onPick={c => openChar(c.pid)} />)}
        </ErrorBoundary>
      </main>
    </div>
  );
}
```

`tsconfig.json` has `noUnusedLocals`, and `tabByPid` is not read until Task 4. So in this task write that line as `const [, setTabByPid] = useState<Record<number, CharTab>>({});`; Task 4 Step 9 restores the name.

- [ ] **Step 6: Make CharDetail's back button optional**

In `webui/src/pages/CharDetail/index.tsx`:
- Change the signature to `{ char, onBack }: { char: CharacterRow; onBack?: () => void }`.
- Change the button line to `{onBack && <button className="is-ghost" onClick={onBack}>← 返回</button>}`.

- [ ] **Step 7: Delete TopNav**

Run: `git rm webui/src/components/TopNav.tsx`

- [ ] **Step 8: Build**

Run: `cd webui && npm run build`
Expected: exits 0.

- [ ] **Step 9: Commit**

```bash
git add -A webui/src
git commit -m "feat(webui): replace top nav with a persistent sidebar of global views and characters"
```

---

### Task 4: Character workspace (header, tabs kept alive, shared detail poll, lost state)

**Files:**
- Rename: `webui/src/pages/CharDetail/` → `webui/src/pages/CharWorkspace/`
- Create: `CharWorkspace/CharHeader.tsx`, `CharWorkspace/useCharacterDetail.ts`, `CharWorkspace/useKeepActive.ts`, `CharWorkspace/workspace.css`
- Rewrite: `CharWorkspace/index.tsx`, `CharWorkspace/BodyTab.tsx`
- Modify: `CharWorkspace/ItemsTab.tsx` (props instead of polling, 看留影 link), `webui/src/App.tsx`
- Delete: `CharWorkspace/KeepActiveTab.tsx`

**Interfaces:**
- Consumes: `CharTab`, `GlobalView`, `isUnlocated` (Task 2); `friendlyError` (Task 2); App's `tabByPid` and `openChar` (Task 3).
- Produces:
  - `CharWorkspace({ char, lostSince, tab, onTab, onNav }: { char: CharacterRow; lostSince: number | null; tab: CharTab; onTab: (t: CharTab) => void; onNav: (k: GlobalView) => void })`
  - `useCharacterDetail(pid: number, enabled: boolean): { detail: CharacterDetail | null; error: string | null }`
  - `useKeepActive(pid: number, enabled: boolean): { running: boolean; busy: boolean; toggle: () => void }`
  - `ItemsTab({ pid, detail, error, onOpenSnapshots })`
  - `BodyTab({ detail, error })`

- [ ] **Step 1: Rename the directory**

Run: `git mv webui/src/pages/CharDetail webui/src/pages/CharWorkspace && git rm webui/src/pages/CharWorkspace/KeepActiveTab.tsx`

- [ ] **Step 2: Create `CharWorkspace/useCharacterDetail.ts`**

```ts
import { useEffect, useState } from 'react';
import { get } from '../../api/client';
import { describeError, reportClientError } from '../../diag/report';
import type { CharacterDetail } from '../../api/types';

// The worker re-reads stats and containers every poll (~3 s).
const POLL_MS = 3000;

export type DetailState = { detail: CharacterDetail | null; error: string | null };

/** One detail poll per workspace, shared by every tab that needs it. */
export function useCharacterDetail(pid: number, enabled: boolean): DetailState {
  const [state, setState] = useState<DetailState>({ detail: null, error: null });
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    const fetchOnce = () =>
      get<CharacterDetail>(`/api/characters/${pid}`)
        .then(d => { if (alive) setState({ detail: d, error: null }); })
        .catch(e => {
          if (!alive) return;
          // Keep the last good detail on screen; only flag the failure.
          setState(s => ({ ...s, error: describeError(e) }));
          reportClientError(e, { component: 'useCharacterDetail' });
        });
    fetchOnce();
    const id = setInterval(fetchOnce, POLL_MS);
    return () => { alive = false; clearInterval(id); };
  }, [pid, enabled]);
  return state;
}
```

- [ ] **Step 3: Create `CharWorkspace/useKeepActive.ts`**

```ts
import { useCallback, useEffect, useState } from 'react';
import { get, post } from '../../api/client';
import { reportClientError } from '../../diag/report';

type Status = { running: boolean };

/** Keep-active state for the header switch; logic moved from KeepActiveTab. */
export function useKeepActive(pid: number, enabled: boolean) {
  const [running, setRunning] = useState(false);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const s = await get<Status>(`/api/characters/${pid}/keep-active/status`);
      setRunning(s.running);
    } catch (e) {
      // Manager may be absent off-Windows: intentionally not surfaced, but
      // still recorded so the timeline is complete.
      reportClientError(e, { component: 'useKeepActive.refresh', silent: true });
    }
  }, [pid]);

  useEffect(() => {
    if (!enabled) return;
    refresh();
    const t = window.setInterval(refresh, 2000);
    return () => window.clearInterval(t);
  }, [refresh, enabled]);

  const toggle = async () => {
    setBusy(true);
    try {
      await post(`/api/characters/${pid}/keep-active/${running ? 'stop' : 'start'}`);
      await refresh();
    } catch (e) {
      reportClientError(e, { component: 'useKeepActive.toggle' });
    } finally {
      setBusy(false);
    }
  };

  return { running, busy, toggle };
}
```

- [ ] **Step 4: Create `CharWorkspace/workspace.css`**

```css
/* Character workspace. Button rules are written as `button.x` (and selected
   tabs restate their underline on hover) so they outrank the global button
   styles in styles.css. */
.ws { display: flex; flex-direction: column; min-height: 100%; }
.ws-sticky { position: sticky; top: 0; z-index: 5; background: var(--tt-bg); }

.ws-head { padding: 12px 18px; background: var(--tt-panel); border-bottom: 1px solid var(--tt-line); display: grid; gap: 10px; }
.ws-id { display: flex; align-items: center; gap: 12px; min-width: 0; }
.ws-id-text { display: grid; gap: 2px; min-width: 0; }
.ws-name { display: flex; align-items: center; gap: 8px; font-family: var(--tt-font-serif); font-size: 18px; letter-spacing: 3px; min-width: 0; }
.ws-name > span:last-child { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ws-sub { font-size: 12px; color: var(--tt-dim); }
.ws-actions { margin-left: auto; display: flex; gap: 8px; align-items: center; flex: none; }

button.ws-btn, button.ws-toggle { font-size: 12px; letter-spacing: 1px; padding: 6px 12px; min-height: 32px; background: var(--tt-bg); color: var(--tt-dim); }
button.ws-btn[data-attn] { color: var(--tt-gold); }
button.ws-toggle { display: flex; align-items: center; gap: 8px; }
button.ws-toggle[aria-pressed="true"] { color: var(--tt-ok); border-color: var(--tt-ok); }
.ws-switch { width: 26px; height: 14px; border-radius: 7px; background: var(--tt-line-soft); display: flex; align-items: center; padding: 2px; box-sizing: border-box; transition: background 120ms ease; }
.ws-switch > span { width: 10px; height: 10px; border-radius: 50%; background: var(--tt-text); transition: transform 120ms ease; }
button.ws-toggle[aria-pressed="true"] .ws-switch { background: #3f6a5e; }
button.ws-toggle[aria-pressed="true"] .ws-switch > span { transform: translateX(12px); }

.ws-vitals { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; align-items: end; }
.ws-vital { display: grid; gap: 4px; min-width: 0; }
.ws-vrow { display: flex; justify-content: space-between; gap: 8px; }
.ws-vlabel { color: var(--tt-dim); letter-spacing: 2px; font-size: 11px; }
.ws-vnum { font-family: var(--tt-font-mono); color: var(--tt-dim); font-size: 11px; }
.ws-vnum[data-warn] { color: var(--tt-bad); }
.ws-ellipsis { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ws-buffs { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ws-auto { margin-left: auto; font-size: 11px; color: var(--tt-ok); letter-spacing: 1px; }
.ws-head[data-lost] .ws-dim-when-lost, .ws-body[data-lost] { opacity: 0.55; }

.ws-banner { padding: 8px 12px; font-size: 12px; line-height: 1.5; border: 1px solid var(--tt-line); display: grid; gap: 8px; }
.ws-banner.is-bad { border-color: var(--tt-bad); }
.ws-banner.is-warn { border-color: var(--tt-warn); }
.ws-badge { font-family: var(--tt-font-serif); margin-right: 6px; }
.ws-banner.is-bad .ws-badge { color: var(--tt-bad); }
.ws-banner.is-warn .ws-badge { color: var(--tt-warn); }
.ws-rescue { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ws-rescue input { width: 110px; padding: 5px 8px; font-size: 12px; }
button.ws-rescue-go { font-size: 12px; padding: 5px 12px; color: var(--tt-gold); }
.ws-rescue-hint { color: var(--tt-dim); font-size: 11px; }

.ws-tabs { display: flex; gap: 2px; padding: 0 12px; border-bottom: 1px solid var(--tt-line); background: var(--tt-bg); }
button.ws-tab { display: flex; align-items: baseline; gap: 6px; padding: 10px 16px; margin-bottom: -1px; background: transparent; border: 0; border-bottom: 2px solid transparent; color: var(--tt-dim); letter-spacing: 0; }
button.ws-tab:hover:not(:disabled) { background: transparent; color: var(--tt-text); border-color: transparent; }
button.ws-tab[aria-selected="true"],
button.ws-tab[aria-selected="true"]:hover:not(:disabled) { color: var(--tt-gold); border-bottom-color: var(--tt-gold); }
.ws-tab-n { font-family: var(--tt-font-serif); font-size: 14px; letter-spacing: 3px; }
.ws-tab-s { font-size: 10.5px; color: var(--tt-dim); }

.ws-body { padding: 14px 16px; }
.ws-empty { padding: 40px; text-align: center; color: var(--tt-dim); font-size: 13px; border: 1px dashed var(--tt-line); }
```

- [ ] **Step 5: Create `CharWorkspace/CharHeader.tsx`**

```tsx
import { useState } from 'react';
import { post } from '../../api/client';
import { describeError, reportClientError } from '../../diag/report';
import type { CharacterRow, ConnectResult, OkResponse } from '../../api/types';
import { friendlyError } from '../../components/friendlyError';
import { isUnlocated } from '../../nav';
import { Bar, BuffChips, LinkDot, Seal } from '../../primitives';
import { useKeepActive } from './useKeepActive';

const LOW_HP = 0.3;

function clock(ms: number) {
  return new Date(ms).toLocaleTimeString('zh-TW', { hour12: false });
}

export function CharHeader({ char, lostSince }: { char: CharacterRow; lostSince: number | null }) {
  const lost = char.link === 'lost';
  const unlocated = isUnlocated(char);
  const keep = useKeepActive(char.pid, !lost);
  const [busy, setBusy] = useState<'rescan' | 'relocate' | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [hpDraft, setHpDraft] = useState('');

  const rescan = async () => {
    setBusy('rescan');
    setActionError(null);
    try {
      await post<OkResponse>(`/api/characters/${char.pid}/rescan`);
    } catch (e) {
      // A failed 重偵 is the moment a user gives up and files a report, so it
      // must not vanish into a console nobody reads.
      setActionError(`重偵失敗：${describeError(e)}`);
      reportClientError(e, { component: 'CharHeader.rescan' });
    } finally {
      setBusy(null);
    }
  };

  const relocate = async () => {
    const hp = Number(hpDraft);
    if (!Number.isInteger(hp) || hp <= 0) {
      setActionError('請輸入目前血量（正整數）');
      return;
    }
    setBusy('relocate');
    setActionError(null);
    try {
      await post<ConnectResult>(`/api/characters/${char.pid}/relocate`, { hp });
      setHpDraft('');
    } catch (e) {
      setActionError(`定位失敗：${describeError(e)}`);
      reportClientError(e, { component: 'CharHeader.relocate' });
    } finally {
      setBusy(null);
    }
  };

  const { hp, hp_max, mp, mp_max, weight, weight_max } = char.vitals;
  const low = hp_max > 0 && hp / hp_max < LOW_HP;
  const err = char.last_error;
  // The lost banner already says the process is gone; don't say it twice.
  const showError = err && !(lost && err.code === 'E_PROC_GONE');
  const runtime = char.autoclick.runtime_seconds;

  return (
    <section className="ws-head" aria-label="角色狀態" data-lost={lost || undefined}>
      <div className="ws-id">
        <Seal size={34}>{unlocated ? '?' : char.name[0]}</Seal>
        <div className="ws-id-text">
          <div className="ws-name"><LinkDot status={char.link} /><span>{char.name}</span></div>
          <div className="ws-sub">
            {unlocated
              ? `pid ${char.pid} · 尚未定位`
              : `${char.sect ? `${char.sect} · ` : ''}Lv ${char.level} · pid ${char.pid}`}
          </div>
        </div>
        <div className="ws-actions">
          <button
            type="button" className="ws-toggle" aria-pressed={keep.running}
            disabled={keep.busy || lost} onClick={keep.toggle}
            title="切到別的視窗時，讓遊戲畫面持續更新"
          >
            <span className="ws-switch" aria-hidden="true"><span /></span>
            保持渲染
          </button>
          <button
            type="button" className="ws-btn" data-attn={lost || unlocated || undefined}
            disabled={busy !== null} onClick={rescan}
            title={lost ? '重新驅動角色偵測' : '強制重新定位（資料不對時用）'}
          >
            {busy === 'rescan' ? '偵測中…' : '↻ 重偵'}
          </button>
        </div>
      </div>

      {!unlocated && (
        <>
          <div className="ws-vitals ws-dim-when-lost">
            <Vital label="氣血" tone="hp" v={hp} m={hp_max} warn={low ? '偏低' : undefined} />
            <Vital label="內力" tone="mp" v={mp} m={mp_max} />
            <Vital label="負重" tone="weight" v={weight} m={weight_max} />
            <div className="ws-vital">
              <span className="ws-vlabel">方位</span>
              <span className="ws-vnum ws-ellipsis">
                {char.position.map_name ?? '—'} · {char.position.x},{char.position.y}
              </span>
            </div>
          </div>
          <div className="ws-buffs ws-dim-when-lost">
            <BuffChips buffs={char.buffs} emptyText="目前無增益狀態" />
            {char.autoclick.running && (
              <span className="ws-auto">● 召喚商人執行中{runtime != null ? ` · ${runtime}s` : ''}</span>
            )}
          </div>
        </>
      )}

      {lost && (
        <div className="ws-banner is-warn" role="status">
          <div>
            <span className="ws-badge">斷</span>
            無法連上遊戲程式，遊戲可能已關閉
            {lostSince ? ` — 以下是 ${clock(lostSince)} 的最後資料` : ''}
          </div>
        </div>
      )}

      {showError && err && (
        <div className="ws-banner is-bad" role="alert">
          <div><span className="ws-badge">錯</span>{friendlyError(err)}</div>
          {err.code === 'E_LOCATE_EXHAUSTED' && (
            <HpRescue
              pid={char.pid} value={hpDraft} busy={busy === 'relocate'}
              onChange={setHpDraft} onSubmit={relocate}
            />
          )}
        </div>
      )}

      {actionError && <div className="ws-banner is-bad" role="status">{actionError}</div>}
    </section>
  );
}

function Vital({ label, tone, v, m, warn }: {
  label: string; tone: 'hp' | 'mp' | 'weight'; v: number; m: number; warn?: string;
}) {
  return (
    <div className="ws-vital">
      <div className="ws-vrow">
        <span className="ws-vlabel">{label}</span>
        <span className="ws-vnum" data-warn={warn ? true : undefined}>
          {v.toLocaleString()} / {m.toLocaleString()}{warn ? ` ${warn}` : ''}
        </span>
      </div>
      <Bar value={v} max={m} tone={tone} />
    </div>
  );
}

function HpRescue({ pid, value, busy, onChange, onSubmit }: {
  pid: number; value: string; busy: boolean;
  onChange: (v: string) => void; onSubmit: () => void;
}) {
  const id = `hp-rescue-${pid}`;
  return (
    <div className="ws-rescue">
      <label htmlFor={id} style={{ color: 'var(--tt-dim)' }}>目前血量</label>
      <input
        id={id} type="number" inputMode="numeric" min={1} value={value} disabled={busy}
        onChange={e => onChange(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter') onSubmit(); }}
      />
      <button type="button" className="ws-rescue-go" onClick={onSubmit} disabled={busy || value.trim() === ''}>
        {busy ? '定位中…' : '用血量定位'}
      </button>
      <span className="ws-rescue-hint">在遊戲中查看角色目前血量，填入後即可掃描定位</span>
    </div>
  );
}
```

- [ ] **Step 6: Rewrite `CharWorkspace/BodyTab.tsx` (props, 狀態 panel dropped)**

```tsx
import type { CharacterDetail } from '../../api/types';
import { Panel, StatNum } from '../../primitives';

export function BodyTab({ detail, error }: { detail: CharacterDetail | null; error: string | null }) {
  if (!detail) {
    return error
      ? <div role="status" style={{ color: 'var(--tt-bad)', fontSize: 13 }}>讀取失敗：{error}</div>
      : <div style={{ color: 'var(--tt-dim)' }}>讀取中…</div>;
  }
  const s = detail.stats;
  const six = [
    ['外功', s.waigong], ['內力', s.neili], ['根骨', s.genggu],
    ['身法', s.shenfa], ['技巧', s.jiqiao], ['玄學', s.xuanxue],
  ] as const;
  const seven = [
    ['物攻', s.wugong], ['基礎', s.wugong_base], ['內勁', s.neijing],
    ['防禦', s.fangyu], ['護勁', s.huji], ['命中', s.mingzhong], ['閃躲', s.shanduo],
  ] as const;
  // Buffs live in the workspace header now, so this tab shows stats only.
  return (
    <div style={{ display: 'grid', gap: 16, gridTemplateColumns: '1fr 1fr' }}>
      <Panel title="六屬"><Grid pairs={six} /></Panel>
      <Panel title="七戰"><Grid pairs={seven} /></Panel>
    </div>
  );
}

function Grid({ pairs }: { pairs: readonly (readonly [string, number])[] }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
      {pairs.map(([k, v]) => (
        <div key={k} style={{ display: 'flex', justifyContent: 'space-between' }}>
          <span style={{ color: 'var(--tt-dim)', letterSpacing: 2 }}>{k}</span>
          <StatNum value={v} />
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 7: Change `CharWorkspace/ItemsTab.tsx` to take props**

Edits, in order:
1. Imports: change `import { get, post } from '../../api/client';` to `import { post } from '../../api/client';`.
2. Signature: replace `export function ItemsTab({ pid }: { pid: number }) {` with:
   ```tsx
   export function ItemsTab({ pid, detail, error, onOpenSnapshots }: {
     pid: number; detail: CharacterDetail | null; error: string | null; onOpenSnapshots: () => void;
   }) {
   ```
3. Delete the line `const [detail, setDetail] = useState<CharacterDetail | null>(null);`.
4. Delete the whole `useEffect(() => { let cancelled = false; ... }, [pid]);` block that polls `/api/characters/${pid}`.
5. Replace `{toast && <div className="inv-toast">{toast}</div>}` with:
   ```tsx
   {(toast ?? (error ? `讀取失敗：${error}` : null)) && (
     <div className="inv-toast">{toast ?? `讀取失敗：${error}`}</div>
   )}
   ```
6. In the `inv-foot` button group, after the `↧ 留影庫` button, add:
   ```tsx
   <button type="button" className="is-ghost" onClick={onOpenSnapshots} title="到留影頁查看已保存的快照">
     看留影 →
   </button>
   ```
7. Check that `useEffect` is still imported only if it is still used. If `tsc` reports it unused, drop it from the React import.

- [ ] **Step 8: Rewrite `CharWorkspace/index.tsx`**

```tsx
import { useRef } from 'react';
import type { CharacterRow } from '../../api/types';
import { isUnlocated, type CharTab, type GlobalView } from '../../nav';
import { AutoClickTab } from './AutoClickTab';
import { BodyTab } from './BodyTab';
import { CharHeader } from './CharHeader';
import { ItemsTab } from './ItemsTab';
import { MapAnalysis } from './MapAnalysis';
import { useCharacterDetail } from './useCharacterDetail';
import './workspace.css';

const TABS: { k: CharTab; n: string; s: string }[] = [
  { k: 'items', n: '行囊', s: '道具' },
  { k: 'body', n: '根脈', s: '屬性' },
  { k: 'maps', n: '行止', s: '地圖' },
  { k: 'assist', n: '輔助', s: '召喚商人' },
];

export function CharWorkspace({ char, lostSince, tab, onTab, onNav }: {
  char: CharacterRow; lostSince: number | null; tab: CharTab;
  onTab: (t: CharTab) => void; onNav: (k: GlobalView) => void;
}) {
  const lost = char.link === 'lost';
  const unlocated = isUnlocated(char);
  const { detail, error } = useCharacterDetail(char.pid, !unlocated);
  // Tabs mount on first visit and then stay mounted (hidden), so search text,
  // filters and selection survive a tab switch. Keyed by pid in App, so a
  // different character starts fresh.
  const visited = useRef(new Set<CharTab>());
  visited.current.add(tab);

  return (
    <div className="ws">
      <div className="ws-sticky">
        <CharHeader char={char} lostSince={lostSince} />
        <nav className="ws-tabs" role="tablist" aria-label="角色分頁">
          {TABS.map(t => (
            <button
              key={t.k} type="button" role="tab" className="ws-tab"
              aria-selected={tab === t.k} onClick={() => onTab(t.k)}
            >
              <span className="ws-tab-n">{t.n}</span>
              <span className="ws-tab-s">{t.s}</span>
            </button>
          ))}
        </nav>
      </div>
      <div className="ws-body" data-lost={lost || undefined}>
        {unlocated
          ? <div className="ws-empty">角色定位後，這裡會自動讀取行囊、屬性與地圖</div>
          : TABS.filter(t => visited.current.has(t.k)).map(t => (
            <div key={t.k} role="tabpanel" hidden={tab !== t.k}>
              {t.k === 'items' && (
                <ItemsTab pid={char.pid} detail={detail} error={error} onOpenSnapshots={() => onNav('snapshots')} />
              )}
              {t.k === 'body' && <BodyTab detail={detail} error={error} />}
              {t.k === 'maps' && <MapAnalysis char={char} />}
              {t.k === 'assist' && <AutoClickTab pid={char.pid} />}
            </div>
          ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 9: Update `webui/src/App.tsx` for the workspace and lost handling**

Edits:
1. Replace `import { CharDetail } from './pages/CharDetail';` with:
   ```tsx
   import { CharWorkspace } from './pages/CharWorkspace';
   import type { CharacterRow } from './api/types';
   ```
2. Change `import { useCallback, useState } from 'react';` to `import { useCallback, useRef, useState } from 'react';`.
3. Change `const [, setTabByPid]` (from Task 3) back to `const [tabByPid, setTabByPid]`.
4. Replace the `const selected = ...` line with:
   ```tsx
     // Last row seen per pid, and when it was last live. A character whose game
     // closed (link 'lost') or whose row vanished still renders from this, with
     // a lost banner, instead of a blank main area.
     const lastSeen = useRef(new Map<number, { row: CharacterRow; at: number }>());
     for (const c of snap.chars) {
       if (c.link !== 'lost' || !lastSeen.current.has(c.pid)) {
         lastSeen.current.set(c.pid, { row: c, at: Date.now() });
       }
     }
     let workspace: { row: CharacterRow; lostSince: number | null } | null = null;
     if (view.kind === 'char') {
       const live = snap.chars.find(c => c.pid === view.pid);
       const seen = lastSeen.current.get(view.pid);
       if (live && live.link !== 'lost') {
         workspace = { row: live, lostSince: null };
       } else if (seen) {
         workspace = {
           row: { ...seen.row, link: 'lost', last_error: live?.last_error ?? seen.row.last_error },
           lostSince: seen.at,
         };
       }
     }
   ```
5. Replace the char branch `{view.kind === 'char' && (selected ? <CharDetail char={selected} /> : <Dashboard ... />)}` with:
   ```tsx
             {view.kind === 'char' && (workspace
               ? (
                 <CharWorkspace
                   key={view.pid}
                   char={workspace.row}
                   lostSince={workspace.lostSince}
                   tab={tabByPid[view.pid] ?? 'items'}
                   onTab={t => setTabByPid(m => ({ ...m, [view.pid]: t }))}
                   onNav={nav}
                 />
               )
               : <Dashboard chars={snap.chars} onPick={c => openChar(c.pid)} />)}
   ```

- [ ] **Step 10: Build**

Run: `cd webui && npm run build`
Expected: exits 0. Fix any TS errors in the files this task touched; none are expected elsewhere.

- [ ] **Step 11: Smoke-run**

Run `uv run app.py --dev` (in a second terminal: `cd webui && npm run dev`) with at least one game client open. Check:
- clicking a character opens 行囊 with the vitals header
- switching to 根脈 and back keeps the 行囊 search text
- 保持渲染 toggles

- [ ] **Step 12: Commit**

```bash
git add -A webui/src
git commit -m "feat(webui): character workspace with live header, kept-alive tabs and lost state"
```

---

### Task 5: 總覽 (Dashboard) — alert strip and full-width table

**Files:**
- Rewrite: `webui/src/pages/Dashboard.tsx`
- Modify: `webui/src/App.tsx` (Dashboard props)

**Interfaces:**
- Consumes: `OpenChar`, `isUnlocated` (Task 2); `friendlyError` (Task 2).
- Produces: `Dashboard({ chars, onOpenChar }: { chars: CharacterRow[]; onOpenChar: OpenChar })`. The old `onPick` prop is removed.

- [ ] **Step 1: Rewrite `webui/src/pages/Dashboard.tsx`**

```tsx
import type { CharacterRow } from '../api/types';
import { friendlyError } from '../components/friendlyError';
import { isUnlocated, type OpenChar } from '../nav';
import { Bar, BuffChips, LinkDot, Panel, StatNum } from '../primitives';

// Name and bar columns flex so names stop truncating; recovery actions
// (重偵 / 用血量定位) live in the character workspace header now.
const COLS = '24px minmax(0, 1.4fr) 40px repeat(3, minmax(0, 1fr)) minmax(0, 1fr)';
const LOW_HP = 0.3;

type Alert = { pid: number; text: string; tone: 'bad' | 'warn' };

function label(c: CharacterRow): string {
  return isUnlocated(c) ? `pid ${c.pid}` : c.name;
}

function alertsFor(chars: CharacterRow[]): Alert[] {
  const out: Alert[] = [];
  for (const c of chars) {
    if (c.link === 'lost') out.push({ pid: c.pid, text: `${label(c)} 已斷線`, tone: 'warn' });
    else if (c.last_error) {
      const what = c.last_error.code === 'E_LOCATE_EXHAUSTED' ? '定位失敗' : '出錯';
      out.push({ pid: c.pid, text: `${label(c)} ${what}`, tone: 'bad' });
    } else if (c.vitals.hp_max > 0 && c.vitals.hp / c.vitals.hp_max < LOW_HP) {
      out.push({ pid: c.pid, text: `${c.name} 氣血偏低`, tone: 'bad' });
    }
  }
  return out;
}

export function Dashboard({ chars, onOpenChar }: { chars: CharacterRow[]; onOpenChar: OpenChar }) {
  const alerts = alertsFor(chars);
  const autoclicking = chars.filter(c => c.autoclick.running);
  return (
    <div style={{ display: 'grid', gap: 12, padding: 16 }}>
      <Panel>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span style={{ fontFamily: 'var(--tt-font-serif)', fontSize: 13, letterSpacing: 4, color: 'var(--tt-dim)' }}>警示</span>
          {alerts.length === 0 && <span style={{ color: 'var(--tt-dim)', fontSize: 12 }}>無</span>}
          {alerts.map(a => (
            <button
              key={`${a.pid}-${a.text}`} type="button" onClick={() => onOpenChar(a.pid)}
              style={{
                fontSize: 12, padding: '4px 10px', letterSpacing: 1,
                color: a.tone === 'bad' ? 'var(--tt-bad)' : 'var(--tt-warn)',
                borderColor: a.tone === 'bad' ? 'var(--tt-bad)' : 'var(--tt-warn)',
              }}
            >{a.text}</button>
          ))}
          <span style={{ marginLeft: 'auto', fontSize: 12, color: autoclicking.length ? 'var(--tt-ok)' : 'var(--tt-dim)' }}>
            {autoclicking.length ? `● 輔助執行中：${autoclicking.map(c => c.name).join('、')}` : '輔助未啟用'}
          </span>
        </div>
      </Panel>

      <Panel title="總覽">
        {chars.length === 0
          ? <div style={{ color: 'var(--tt-dim)', fontSize: 12, padding: 24, textAlign: 'center' }}>尚未偵測到遊戲程式 — 開啟遊戲後會自動出現在這裡</div>
          : (
            <div style={{ display: 'grid', gap: 4 }}>
              <Header />
              {chars.map(c => <Row key={c.pid} c={c} onOpen={() => onOpenChar(c.pid)} />)}
            </div>
          )}
      </Panel>
    </div>
  );
}

function Row({ c, onOpen }: { c: CharacterRow; onOpen: () => void }) {
  const unlocated = isUnlocated(c);
  return (
    <button
      type="button" onClick={onOpen}
      style={{
        display: 'flex', flexDirection: 'column', gap: 8, padding: 12, width: '100%',
        background: 'var(--tt-raised)', border: '1px solid var(--tt-line-soft)',
        color: 'var(--tt-text)', textAlign: 'left', letterSpacing: 0,
        opacity: c.link === 'lost' ? 0.65 : 1,
      }}
    >
      <span style={{ display: 'grid', gridTemplateColumns: COLS, gap: 10, alignItems: 'center', width: '100%' }}>
        <LinkDot status={c.link} />
        <span style={{ display: 'grid', gap: 2, minWidth: 0 }}>
          <span style={{ fontFamily: 'var(--tt-font-serif)', letterSpacing: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</span>
          <span style={{ color: 'var(--tt-dim)', fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {c.sect ? `${c.sect} · ` : ''}pid {c.pid}
          </span>
        </span>
        {unlocated ? <span style={{ color: 'var(--tt-dim)' }}>—</span> : <StatNum value={c.level} />}
        <VitalCell tone="hp" v={c.vitals.hp} m={c.vitals.hp_max} />
        <VitalCell tone="mp" v={c.vitals.mp} m={c.vitals.mp_max} />
        <VitalCell tone="weight" v={c.vitals.weight} m={c.vitals.weight_max} />
        <span style={{ fontSize: 11, color: 'var(--tt-dim)', fontFamily: 'var(--tt-font-mono)', display: 'grid', gap: 2, minWidth: 0 }}>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.position.map_name ?? '—'}</span>
          <span>{unlocated ? '' : `${c.position.x},${c.position.y}`}</span>
        </span>
      </span>
      <BuffChips buffs={c.buffs} />
      {c.last_error && (
        <span style={{ fontSize: 11, color: 'var(--tt-bad)', lineHeight: 1.5 }}>
          <span style={{ fontFamily: 'var(--tt-font-serif)', marginRight: 6 }}>錯</span>
          {friendlyError(c.last_error)} — 點進去處理
        </span>
      )}
    </button>
  );
}

function Header() {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: COLS, gap: 10, padding: '6px 12px',
      fontSize: 11, color: 'var(--tt-dim)', letterSpacing: 2, borderBottom: '1px solid var(--tt-line-soft)',
    }}>
      <span /> <span>名 · 門派</span> <span>等級</span>
      <span>氣血</span> <span>內力</span> <span>負重</span> <span>方位</span>
    </div>
  );
}

function VitalCell({ tone, v, m }: { tone: 'hp' | 'mp' | 'weight'; v: number; m: number }) {
  return (
    <span style={{ display: 'grid', gap: 2, minWidth: 0 }}>
      <Bar value={v} max={m} tone={tone} />
      <span style={{ fontSize: 10, color: 'var(--tt-dim)', fontFamily: 'var(--tt-font-mono)' }}>{v}/{m}</span>
    </span>
  );
}
```

`Panel` with no `title` renders no header (its `title` prop is optional). Global `button:hover` turns the border gold, which is the intended hover feedback for these rows.

- [ ] **Step 2: Update App's Dashboard usages**

In `webui/src/App.tsx`, replace both occurrences of `<Dashboard chars={snap.chars} onPick={c => openChar(c.pid)} />` with `<Dashboard chars={snap.chars} onOpenChar={openChar} />`.

- [ ] **Step 3: Build**

Run: `cd webui && npm run build`
Expected: exits 0.

- [ ] **Step 4: Commit**

```bash
git add webui/src/pages/Dashboard.tsx webui/src/App.tsx
git commit -m "feat(webui): overview with clickable alert strip and a full-width character table"
```

---

### Task 6: 帳房 / 留影 jump to a character

**Files:**
- Modify: `webui/src/pages/Treasury.tsx`, `webui/src/pages/Snapshots.tsx`, `webui/src/components/items/items.css`, `webui/src/App.tsx`

**Interfaces:**
- Consumes: `OpenChar`, `pidForName` (Task 2).
- Produces: `Treasury({ chars, onOpenChar })`, `Snapshots({ chars, onOpenChar })`, both `{ chars: CharacterRow[]; onOpenChar: OpenChar }`.

Links go in the **detail panels**, not in the list rows: the rows are `<button>`s already, and a button inside a button is invalid HTML.

- [ ] **Step 1: Add the link style to `items.css` (append)**

```css
/* Character name that jumps to that character's 行囊 (帳房 / 留影). */
button.tr-holder-link {
  padding: 0; background: transparent; border: 0; letter-spacing: 0;
  font: inherit; color: var(--tt-gold); text-align: left; cursor: pointer;
}
button.tr-holder-link:hover:not(:disabled) { background: transparent; text-decoration: underline; }
```

- [ ] **Step 2: Treasury**

In `webui/src/pages/Treasury.tsx`:
1. Imports: change the types import to `import type { CharacterRow, ItemMeta, TreasuryItem, TreasurySummary } from '../api/types';` and add `import { pidForName, type OpenChar } from '../nav';`.
2. Signature: `export function Treasury({ chars, onOpenChar }: { chars: CharacterRow[]; onOpenChar: OpenChar }) {`
3. Change `<HolderSection item={current} />` to `<HolderSection item={current} chars={chars} onOpenChar={onOpenChar} />`.
4. Replace `function HolderSection(...)` with:
   ```tsx
   function HolderSection({ item, chars, onOpenChar }: {
     item: TreasuryItem; chars: CharacterRow[]; onOpenChar: OpenChar;
   }) {
     const sorted = [...item.holders].sort((a, b) => b.qty - a.qty);
     return (
       <div className="inv-d-sec">
         <span className="inv-d-label">持有者</span>
         <div className="tr-holders">
           {sorted.map(h => {
             const pid = pidForName(chars, h.character);
             return (
               <div key={`${h.character}-${h.source}`} className="tr-holder">
                 {pid !== null
                   ? <button type="button" className="tr-holder-link" onClick={() => onOpenChar(pid, 'items')} title="開啟這個角色的行囊">{h.character}</button>
                   : <span>{h.character}</span>}
                 <span className="tr-holder-src">{h.source === 'warehouse' ? '庫房' : '隨身'}{h.account ? ` · ${h.account}` : ''}</span>
                 <span className="tr-holder-qty">{h.qty.toLocaleString()}</span>
               </div>
             );
           })}
         </div>
       </div>
     );
   }
   ```

- [ ] **Step 3: Snapshots**

In `webui/src/pages/Snapshots.tsx`:
1. Imports: change to `import type { BackupImportResult, CharacterRow, SnapshotRow } from '../api/types';` and add `import { pidForName, type OpenChar } from '../nav';`.
2. Signature: `export function Snapshots({ chars, onOpenChar }: { chars: CharacterRow[]; onOpenChar: OpenChar }) {`
3. Inside the 留影內容 panel, replace the `<span style={{ color: 'var(--tt-mute)', marginLeft: 8 }}>{selected.saved_at}</span>` line with:
   ```tsx
                   <span style={{ color: 'var(--tt-mute)', marginLeft: 8 }}>{selected.saved_at}</span>
                   {(() => {
                     const pid = pidForName(chars, selected.character_name);
                     return pid !== null && (
                       <button type="button" className="tr-holder-link" style={{ marginLeft: 12, fontSize: 12 }}
                         onClick={() => onOpenChar(pid, 'items')}>開啟角色 →</button>
                     );
                   })()}
   ```
4. Add `import '../components/items/items.css';` if it is not already imported (it is needed for `.tr-holder-link`).

- [ ] **Step 4: Pass props from App**

In `webui/src/App.tsx`:
- `{view.kind === 'treasury' && <Treasury chars={snap.chars} onOpenChar={openChar} />}`
- `{view.kind === 'snapshots' && <Snapshots chars={snap.chars} onOpenChar={openChar} />}`

- [ ] **Step 5: Build**

Run: `cd webui && npm run build`
Expected: exits 0.

- [ ] **Step 6: Commit**

```bash
git add webui/src
git commit -m "feat(webui): jump from 帳房 holders and 留影 rows to the character's 行囊"
```

---

### Task 7: Verification

**Files:** none (fix-ups only if checks fail)

- [ ] **Step 1: Placeholder / stub scan**

Run: `git diff main --stat` and `git grep -nE "TODO|FIXME|XXX|test\.skip|\.only\(" -- webui/src services/window_prefs.py tests/test_window_prefs.py app.py`
Expected: no new hits from this branch.

- [ ] **Step 2: Leftover references**

Run: `git grep -nE "TopNav|CharDetail|KeepActiveTab|onPick" -- webui/src`
Expected: no hits.

- [ ] **Step 3: Full test + build**

Run: `uv run pytest` and `cd webui && npm run build`
Expected: both exit 0.

- [ ] **Step 4: Manual checklist**

Run `uv run app.py --dev` with **two or more** game clients, then work through the list. Write down each result; any failure goes back to its task.
1. First launch after deleting `%APPDATA%\御心鑒\window.json` opens at 1280×800, or 90% of a smaller screen.
2. Resize and move the window, close it, reopen: the same size and place.
3. Minimize, then close from the taskbar, then reopen: the previous good geometry, not off-screen.
4. 總覽: names of 4+ glyphs are not truncated. Alert chips open the right character.
5. Sidebar: one click switches characters. Each character reopens on its last tab; the default is 行囊.
6. 行囊: type a search, switch to 根脈 and back, and the search is still there.
7. Header: HP/MP/weight/position/buffs update live. 保持渲染 toggles. ↻ 重偵 works.
8. Close one game client while viewing it: 「斷」 banner, greyed data, sidebar row dimmed and 已斷線. Never a blank main area.
9. A not-yet-located client shows `pid … · 定位失敗`/`連線中` in the sidebar. Once `E_LOCATE_EXHAUSTED` fires, entering HP in the header banner relocates it.
10. 帳房: select an item, click a holder name, and land on that character's 行囊. With two same-name characters online, that name is plain text.
11. 留影: select a row, then 「開啟角色 →」 appears only for a live, unambiguous name.
12. Switch characters rapidly: the header and tabs never show another character's data.

- [ ] **Step 5: Hand to review**

Use the `superpowers:requesting-code-review` skill for the whole-branch review before merge.
