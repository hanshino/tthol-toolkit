# Tthol UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the PySide6 GUI with a pywebview window driving a React/Vite frontend that talks to a FastAPI backend over localhost HTTP + WebSocket.

**Architecture:** Single Python process. `uvicorn` runs FastAPI in a daemon thread on a random `127.0.0.1` port; `pywebview` opens a window pointed at that URL. The React app calls REST endpoints under `/api/*` and subscribes to `/ws/world` for live snapshots. Pydantic models are the single source of truth — TypeScript types are generated from FastAPI's `/openapi.json`. The Python memory layer (`reader.py`, `auto_detect.py`, `warehouse_scan.py`, `knowledge.json`, `tthol.sqlite`) is unchanged.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, websockets, pywebview, pymem, Pydantic v2, pytest, httpx (test). Frontend: Vite, React 18, TypeScript, openapi-typescript.

**Spec reference:** `docs/superpowers/specs/2026-05-03-tthol-ui-redesign-design.md`

---

## Phase 0 — Foundation

### Task 1: Update dependencies and scaffold directories

**Files:**
- Modify: `pyproject.toml`
- Create: `services/__init__.py`
- Create: `services/api/__init__.py`
- Create: `services/api_types.py` (empty stub)
- Create: `webui/.gitkeep`
- Create: `scripts/.gitkeep`
- Create: `exports/.gitkeep`

- [ ] **Step 1: Replace `pyproject.toml` dependency list**

```toml
[project]
name = "tthol-memory"
version = "0.1.0"
description = "Read-only memory reader UI for tthol"
requires-python = ">=3.11.9"
dependencies = [
    "psutil>=7.2.2",
    "pymem>=1.14.0",
    "pywin32>=311",
    "pywebview>=5.3",
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "websockets>=13.1",
    "pydantic>=2.9",
]

[dependency-groups]
dev = [
    "ruff>=0.15.2",
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "httpx>=0.27",
]

[tool.ruff]
line-length = 100

[tool.ruff.format]
quote-style = "double"
indent-style = "space"

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 2: Create empty package files**

```bash
mkdir -p services/api webui scripts exports
echo "" > services/__init__.py
echo "" > services/api/__init__.py
echo "" > services/api_types.py
touch webui/.gitkeep scripts/.gitkeep exports/.gitkeep
```

- [ ] **Step 3: Sync env**

```bash
uv sync
```
Expected: installs new deps, removes pyside6/pytest-qt.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock services/ webui/.gitkeep scripts/.gitkeep exports/.gitkeep
git commit -m "chore: swap PySide6 deps for FastAPI/pywebview/pydantic stack"
```

---

## Phase 1 — Pydantic models + FastAPI skeleton

### Task 2: Define Pydantic models in `api_types.py`

**Files:**
- Modify: `services/api_types.py`
- Create: `tests/test_api_types.py`

- [ ] **Step 1: Write failing test for `WorldSnapshot` shape**

```python
# tests/test_api_types.py
from services.api_types import WorldSnapshot, CharacterRow, Vitals, Position, AutoClickStatus


def test_world_snapshot_round_trip():
    snap = WorldSnapshot(
        chars=[
            CharacterRow(
                pid=1234,
                name="無名",
                sect="少林",
                link="ok",
                level=20,
                vitals=Vitals(hp=100, hp_max=120, mp=80, mp_max=80, weight=50, weight_max=200),
                position=Position(map_name="洛陽", x=100, y=200),
                autoclick=AutoClickStatus(running=False, started_at=None, runtime_seconds=None, last_click_at=None),
            )
        ],
        server_ts=1714723200.0,
    )
    dumped = snap.model_dump()
    assert dumped["chars"][0]["link"] == "ok"
    assert dumped["chars"][0]["vitals"]["hp"] == 100
    WorldSnapshot.model_validate(dumped)  # round-trip
```

- [ ] **Step 2: Run test (fails — models not defined)**

```bash
uv run pytest tests/test_api_types.py -v
```
Expected: FAIL — `ImportError: cannot import name 'WorldSnapshot'`.

- [ ] **Step 3: Implement models**

```python
# services/api_types.py
"""Pydantic models — single source of truth for HTTP/WS payload shapes.

These models are also fed to openapi-typescript at frontend build time
to produce webui/src/api/types.ts. Do not hand-edit the TS file.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---- Vitals / position --------------------------------------------------

class Vitals(_Base):
    hp: int
    hp_max: int
    mp: int
    mp_max: int
    weight: int
    weight_max: int


class Position(_Base):
    map_name: str | None = None
    x: int
    y: int


class AutoClickStatus(_Base):
    running: bool
    started_at: float | None = None
    runtime_seconds: int | None = None
    last_click_at: float | None = None


# ---- Stats (六屬 + 七戰) -----------------------------------------------

class CharacterStats(_Base):
    level: int
    waigong: int       # 外功
    neili: int         # 內力
    genggu: int        # 根骨
    shenfa: int        # 身法
    jiqiao: int        # 技巧
    xuanxue: int       # 玄學
    wugong: int        # 物攻
    wugong_base: int   # 物攻(基礎)
    neijing: int       # 內勁
    fangyu: int        # 防禦
    huji: int          # 護勁
    mingzhong: int     # 命中
    shanduo: int       # 閃躲


# ---- Items ---------------------------------------------------------------

class Item(_Base):
    item_id: int
    name: str
    quantity: int
    source: Literal["inventory", "warehouse"]


# ---- Character views -----------------------------------------------------

class Character(_Base):
    """Lightweight row used by GET /api/characters."""
    pid: int
    name: str | None = None
    sect: str | None = None
    level: int | None = None
    link: Literal["ok", "weak", "lost"]


class CharacterRow(_Base):
    """Used inside WorldSnapshot — stats summary per char."""
    pid: int
    name: str
    sect: str
    link: Literal["ok", "weak", "lost"]
    level: int
    vitals: Vitals
    position: Position
    autoclick: AutoClickStatus


class CharacterDetail(_Base):
    pid: int
    name: str
    sect: str
    link: Literal["ok", "weak", "lost"]
    stats: CharacterStats
    vitals: Vitals
    position: Position
    autoclick: AutoClickStatus
    inventory: list[Item] | None = None
    warehouse: list[Item] | None = None


class WorldSnapshot(_Base):
    chars: list[CharacterRow]
    server_ts: float


# ---- Connect / lifecycle -------------------------------------------------

class ConnectOptions(_Base):
    compat_mode: bool = False
    auto_chain: bool = True


class ConnectRequest(_Base):
    hp: int | None = None
    options: ConnectOptions = ConnectOptions()


class ConnectResult(_Base):
    ok: bool
    error: str | None = None
    hp_addr: int | None = None


# ---- Snapshots / accounts ------------------------------------------------

class SaveSnapshotRequest(_Base):
    pid: int
    source: Literal["inventory", "warehouse"]


class SaveSnapshotResult(_Base):
    saved: bool
    snapshot_id: int | None = None


class SnapshotFilter(_Base):
    account_id: int | None = None
    character_name: str | None = None
    source: Literal["inventory", "warehouse"] | None = None
    days: int | None = None


class SnapshotRow(_Base):
    snapshot_id: int
    character_name: str
    account_id: int | None = None
    source: Literal["inventory", "warehouse"]
    saved_at: str  # ISO 8601
    item_count: int


class Account(_Base):
    account_id: int
    name: str
    character_count: int = 0


class CreateAccountRequest(_Base):
    name: str


class SetCharacterAccountRequest(_Base):
    account_id: int | None


# ---- Auto-click ----------------------------------------------------------

class AutoClickConfig(_Base):
    interval_seconds: int
    merchant_idx: int


class AutoClickTestRequest(_Base):
    merchant_idx: int


# ---- Export --------------------------------------------------------------

class ExportCsvRequest(_Base):
    mode: Literal["detail", "summary"]


class ExportCsvResult(_Base):
    rows: int
    path: str


# ---- Generic -------------------------------------------------------------

class OkResponse(_Base):
    ok: bool
    error: str | None = None
```

- [ ] **Step 4: Run test (passes)**

```bash
uv run pytest tests/test_api_types.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/api_types.py tests/test_api_types.py
git commit -m "feat(api): add Pydantic models for HTTP/WS payloads"
```

---

### Task 3: Build FastAPI app skeleton

**Files:**
- Modify: `services/api/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_api_app.py`

- [ ] **Step 1: Write failing test for app and `/health`**

```python
# tests/test_api_app.py
import pytest
from httpx import ASGITransport, AsyncClient

from services.api import build_app


@pytest.fixture
async def client():
    app = build_app(services=None)  # services not yet wired
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_health(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


async def test_openapi_schema_serves(client):
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert schema["info"]["title"] == "tthol-memory"
```

- [ ] **Step 2: Add minimal conftest**

```python
# tests/conftest.py
import sys
from pathlib import Path

# Make repo root importable so `from services...` works in tests.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

- [ ] **Step 3: Run test (fails)**

```bash
uv run pytest tests/test_api_app.py -v
```
Expected: FAIL — `ImportError: cannot import name 'build_app'`.

- [ ] **Step 4: Implement `build_app`**

```python
# services/api/__init__.py
"""FastAPI app builder.

`build_app(services)` returns a FastAPI instance. `services` is a dict
of singletons (worker_manager, snapshot_db, autoclick_manager, ...);
None during tests/dev to allow stub routers to short-circuit.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI


def build_app(services: dict[str, Any] | None = None) -> FastAPI:
    app = FastAPI(title="tthol-memory", version="0.7.2")
    app.state.services = services or {}

    @app.get("/api/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    return app
```

- [ ] **Step 5: Run test (passes)**

```bash
uv run pytest tests/test_api_app.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/api/__init__.py tests/conftest.py tests/test_api_app.py
git commit -m "feat(api): scaffold FastAPI app with /api/health"
```

---

## Phase 2 — Mock backend routers (unblock frontend)

### Task 4: Characters router with mock data

**Files:**
- Create: `services/api/characters.py`
- Modify: `services/api/__init__.py`
- Create: `tests/test_characters_router.py`
- Create: `services/_mock.py`

- [ ] **Step 1: Add mock fixtures module**

```python
# services/_mock.py
"""Mock data for routers when no live services are wired.
Removed in Phase 5 once real worker manager is plumbed in.
"""
from services.api_types import (
    AutoClickStatus,
    Character,
    CharacterRow,
    Position,
    Vitals,
    WorldSnapshot,
)


def mock_chars() -> list[Character]:
    return [
        Character(pid=1001, name="無塵", sect="少林", level=20, link="ok"),
        Character(pid=1002, name="風清揚", sect="華山", level=25, link="ok"),
        Character(pid=1003, name="令狐沖", sect="華山", level=22, link="weak"),
    ]


def mock_world() -> WorldSnapshot:
    base = AutoClickStatus(running=False)
    return WorldSnapshot(
        server_ts=0.0,
        chars=[
            CharacterRow(
                pid=1001, name="無塵", sect="少林", link="ok", level=20,
                vitals=Vitals(hp=120, hp_max=150, mp=90, mp_max=100, weight=40, weight_max=200),
                position=Position(map_name="少林寺", x=100, y=200),
                autoclick=base,
            ),
            CharacterRow(
                pid=1002, name="風清揚", sect="華山", link="ok", level=25,
                vitals=Vitals(hp=180, hp_max=200, mp=150, mp_max=160, weight=80, weight_max=250),
                position=Position(map_name="華山絕頂", x=50, y=80),
                autoclick=base,
            ),
        ],
    )
```

- [ ] **Step 2: Write failing test**

```python
# tests/test_characters_router.py
import pytest
from httpx import ASGITransport, AsyncClient

from services.api import build_app


@pytest.fixture
async def client():
    app = build_app(services=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_list_characters_returns_mocks(client):
    resp = await client.get("/api/characters")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 3
    assert rows[0]["pid"] == 1001
    assert rows[0]["link"] == "ok"


async def test_world_snapshot(client):
    resp = await client.get("/api/world")
    assert resp.status_code == 200
    body = resp.json()
    assert "chars" in body and "server_ts" in body


async def test_connect_returns_ok(client):
    resp = await client.post("/api/characters/1001/connect", json={"hp": 120})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
```

- [ ] **Step 3: Run test (fails)**

```bash
uv run pytest tests/test_characters_router.py -v
```
Expected: FAIL — 404 on all routes.

- [ ] **Step 4: Implement characters router**

```python
# services/api/characters.py
import time

from fastapi import APIRouter, Request

from services._mock import mock_chars, mock_world
from services.api_types import (
    Character,
    CharacterDetail,
    ConnectRequest,
    ConnectResult,
    OkResponse,
    WorldSnapshot,
)

router = APIRouter(prefix="/api", tags=["characters"])


@router.get("/characters", response_model=list[Character])
async def list_characters(request: Request) -> list[Character]:
    wm = request.app.state.services.get("worker_manager")
    if wm is None:
        return mock_chars()
    return wm.list_characters()


@router.get("/world", response_model=WorldSnapshot)
async def world_snapshot(request: Request) -> WorldSnapshot:
    wm = request.app.state.services.get("worker_manager")
    if wm is None:
        snap = mock_world()
        snap.server_ts = time.time()
        return snap
    return wm.world_snapshot()


@router.get("/characters/{pid}", response_model=CharacterDetail)
async def character_detail(pid: int, request: Request) -> CharacterDetail:
    wm = request.app.state.services.get("worker_manager")
    if wm is None:
        from services.api_types import AutoClickStatus, CharacterStats, Position, Vitals
        return CharacterDetail(
            pid=pid, name="無塵", sect="少林", link="ok",
            stats=CharacterStats(
                level=20, waigong=100, neili=80, genggu=70, shenfa=60, jiqiao=50, xuanxue=40,
                wugong=200, wugong_base=180, neijing=150, fangyu=120, huji=90, mingzhong=85, shanduo=75,
            ),
            vitals=Vitals(hp=120, hp_max=150, mp=90, mp_max=100, weight=40, weight_max=200),
            position=Position(map_name="少林寺", x=100, y=200),
            autoclick=AutoClickStatus(running=False),
        )
    return wm.character_detail(pid)


@router.post("/characters/{pid}/connect", response_model=ConnectResult)
async def connect_char(pid: int, body: ConnectRequest, request: Request) -> ConnectResult:
    wm = request.app.state.services.get("worker_manager")
    if wm is None:
        return ConnectResult(ok=True, hp_addr=0xDEADBEEF)
    return wm.connect(pid, body)


@router.post("/characters/{pid}/disconnect", response_model=OkResponse)
async def disconnect_char(pid: int, request: Request) -> OkResponse:
    wm = request.app.state.services.get("worker_manager")
    if wm is None:
        return OkResponse(ok=True)
    wm.disconnect(pid)
    return OkResponse(ok=True)


@router.post("/characters/{pid}/relocate", response_model=ConnectResult)
async def relocate_char(pid: int, body: dict, request: Request) -> ConnectResult:
    wm = request.app.state.services.get("worker_manager")
    if wm is None:
        return ConnectResult(ok=True)
    return wm.relocate(pid, body.get("hp"))


@router.post("/characters/{pid}/focus", response_model=OkResponse)
async def focus_window(pid: int, request: Request) -> OkResponse:
    wm = request.app.state.services.get("worker_manager")
    if wm is None:
        return OkResponse(ok=True)
    wm.focus(pid)
    return OkResponse(ok=True)
```

- [ ] **Step 5: Mount the router in `build_app`**

```python
# services/api/__init__.py — replace body of build_app
from fastapi import FastAPI

from services.api import characters as characters_module


def build_app(services=None):
    app = FastAPI(title="tthol-memory", version="0.7.2")
    app.state.services = services or {}

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    app.include_router(characters_module.router)
    return app
```

- [ ] **Step 6: Run all api tests (pass)**

```bash
uv run pytest tests/test_characters_router.py tests/test_api_app.py -v
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add services/_mock.py services/api/characters.py services/api/__init__.py tests/test_characters_router.py
git commit -m "feat(api): add /api/characters routes (mocked)"
```

---

### Task 5: Snapshots + Accounts routers (mocked)

**Files:**
- Create: `services/api/snapshots.py`
- Create: `services/api/accounts.py`
- Modify: `services/api/__init__.py`
- Modify: `services/_mock.py`
- Create: `tests/test_snapshots_router.py`

- [ ] **Step 1: Extend mock with snapshots/accounts**

```python
# services/_mock.py — add at bottom
from services.api_types import Account, SnapshotRow


def mock_snapshots() -> list[SnapshotRow]:
    return [
        SnapshotRow(
            snapshot_id=1, character_name="無塵", account_id=1,
            source="inventory", saved_at="2026-04-01T10:00:00", item_count=24,
        ),
        SnapshotRow(
            snapshot_id=2, character_name="風清揚", account_id=2,
            source="warehouse", saved_at="2026-04-02T11:00:00", item_count=42,
        ),
    ]


def mock_accounts() -> list[Account]:
    return [
        Account(account_id=1, name="主帳", character_count=3),
        Account(account_id=2, name="練功號", character_count=4),
    ]
```

- [ ] **Step 2: Write failing test**

```python
# tests/test_snapshots_router.py
import pytest
from httpx import ASGITransport, AsyncClient

from services.api import build_app


@pytest.fixture
async def client():
    app = build_app(services=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_list_snapshots(client):
    resp = await client.get("/api/snapshots")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) >= 1
    assert "snapshot_id" in rows[0]


async def test_save_snapshot_returns_saved_flag(client):
    resp = await client.post("/api/snapshots", json={"pid": 1001, "source": "inventory"})
    assert resp.status_code == 200
    assert "saved" in resp.json()


async def test_list_accounts(client):
    resp = await client.get("/api/accounts")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1
```

- [ ] **Step 3: Run test (fails — 404s)**

```bash
uv run pytest tests/test_snapshots_router.py -v
```
Expected: FAIL.

- [ ] **Step 4: Implement snapshots router**

```python
# services/api/snapshots.py
from fastapi import APIRouter, Request

from services._mock import mock_snapshots
from services.api_types import (
    OkResponse,
    SaveSnapshotRequest,
    SaveSnapshotResult,
    SnapshotRow,
)

router = APIRouter(prefix="/api", tags=["snapshots"])


@router.get("/snapshots", response_model=list[SnapshotRow])
async def list_snapshots(
    request: Request,
    account_id: int | None = None,
    character_name: str | None = None,
    source: str | None = None,
    days: int | None = None,
) -> list[SnapshotRow]:
    db = request.app.state.services.get("snapshot_db")
    if db is None:
        return mock_snapshots()
    return db.list_snapshots(
        account_id=account_id, character_name=character_name, source=source, days=days,
    )


@router.post("/snapshots", response_model=SaveSnapshotResult)
async def save_snapshot(body: SaveSnapshotRequest, request: Request) -> SaveSnapshotResult:
    wm = request.app.state.services.get("worker_manager")
    db = request.app.state.services.get("snapshot_db")
    if wm is None or db is None:
        return SaveSnapshotResult(saved=False)
    return wm.save_snapshot(body.pid, body.source)


@router.delete("/snapshots/{snapshot_id}", response_model=OkResponse)
async def delete_snapshot(snapshot_id: int, request: Request) -> OkResponse:
    db = request.app.state.services.get("snapshot_db")
    if db is None:
        return OkResponse(ok=True)
    db.delete_snapshot(snapshot_id)
    return OkResponse(ok=True)


@router.delete("/characters/by-name/{name}", response_model=OkResponse)
async def delete_character(name: str, request: Request) -> OkResponse:
    db = request.app.state.services.get("snapshot_db")
    if db is None:
        return OkResponse(ok=True)
    db.delete_character(name)
    return OkResponse(ok=True)
```

- [ ] **Step 5: Implement accounts router**

```python
# services/api/accounts.py
from fastapi import APIRouter, Request

from services._mock import mock_accounts
from services.api_types import (
    Account,
    CreateAccountRequest,
    OkResponse,
    SetCharacterAccountRequest,
)

router = APIRouter(prefix="/api", tags=["accounts"])


@router.get("/accounts", response_model=list[Account])
async def list_accounts(request: Request) -> list[Account]:
    db = request.app.state.services.get("snapshot_db")
    if db is None:
        return mock_accounts()
    return db.list_accounts()


@router.post("/accounts", response_model=Account)
async def create_account(body: CreateAccountRequest, request: Request) -> Account:
    db = request.app.state.services.get("snapshot_db")
    if db is None:
        return Account(account_id=99, name=body.name, character_count=0)
    return db.create_account(body.name)


@router.put("/characters/by-name/{name}/account", response_model=OkResponse)
async def set_character_account(
    name: str, body: SetCharacterAccountRequest, request: Request,
) -> OkResponse:
    db = request.app.state.services.get("snapshot_db")
    if db is None:
        return OkResponse(ok=True)
    db.set_character_account(name, body.account_id)
    return OkResponse(ok=True)
```

- [ ] **Step 6: Mount routers**

```python
# services/api/__init__.py — append imports + include_router calls
from services.api import accounts as accounts_module
from services.api import characters as characters_module
from services.api import snapshots as snapshots_module

# inside build_app, after characters:
    app.include_router(characters_module.router)
    app.include_router(snapshots_module.router)
    app.include_router(accounts_module.router)
```

- [ ] **Step 7: Run tests (pass)**

```bash
uv run pytest tests/test_snapshots_router.py -v
```
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add services/api/snapshots.py services/api/accounts.py services/api/__init__.py services/_mock.py tests/test_snapshots_router.py
git commit -m "feat(api): add /api/snapshots and /api/accounts (mocked)"
```

---

### Task 6: Auto-click + Export routers (mocked)

**Files:**
- Create: `services/api/autoclick.py`
- Create: `services/api/export.py`
- Modify: `services/api/__init__.py`
- Create: `tests/test_autoclick_router.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_autoclick_router.py
import pytest
from httpx import ASGITransport, AsyncClient

from services.api import build_app


@pytest.fixture
async def client():
    app = build_app(services=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_autoclick_start(client):
    resp = await client.post(
        "/api/characters/1001/autoclick/start",
        json={"interval_seconds": 60, "merchant_idx": 0},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


async def test_autoclick_status(client):
    resp = await client.get("/api/characters/1001/autoclick/status")
    assert resp.status_code == 200
    assert "running" in resp.json()


async def test_export_csv_returns_path(client):
    resp = await client.post("/api/export/csv", json={"mode": "summary"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["path"].endswith(".csv")
```

- [ ] **Step 2: Run test (fails)**

```bash
uv run pytest tests/test_autoclick_router.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implement autoclick router**

```python
# services/api/autoclick.py
from fastapi import APIRouter, Request

from services.api_types import (
    AutoClickConfig,
    AutoClickStatus,
    AutoClickTestRequest,
    OkResponse,
)

router = APIRouter(prefix="/api/characters/{pid}/autoclick", tags=["autoclick"])


@router.post("/start", response_model=OkResponse)
async def start(pid: int, config: AutoClickConfig, request: Request) -> OkResponse:
    mgr = request.app.state.services.get("autoclick_manager")
    if mgr is None:
        return OkResponse(ok=True)
    mgr.start(pid, config)
    return OkResponse(ok=True)


@router.post("/stop", response_model=OkResponse)
async def stop(pid: int, request: Request) -> OkResponse:
    mgr = request.app.state.services.get("autoclick_manager")
    if mgr is None:
        return OkResponse(ok=True)
    mgr.stop(pid)
    return OkResponse(ok=True)


@router.post("/test", response_model=OkResponse)
async def test_click(pid: int, body: AutoClickTestRequest, request: Request) -> OkResponse:
    mgr = request.app.state.services.get("autoclick_manager")
    if mgr is None:
        return OkResponse(ok=True)
    mgr.test_click(pid, body.merchant_idx)
    return OkResponse(ok=True)


@router.get("/status", response_model=AutoClickStatus)
async def status(pid: int, request: Request) -> AutoClickStatus:
    mgr = request.app.state.services.get("autoclick_manager")
    if mgr is None:
        return AutoClickStatus(running=False)
    return mgr.status(pid)
```

- [ ] **Step 4: Implement export router**

```python
# services/api/export.py
import time
from pathlib import Path

from fastapi import APIRouter, Request

from services.api_types import ExportCsvRequest, ExportCsvResult

router = APIRouter(prefix="/api/export", tags=["export"])


@router.post("/csv", response_model=ExportCsvResult)
async def export_csv(body: ExportCsvRequest, request: Request) -> ExportCsvResult:
    exporter = request.app.state.services.get("exporter")
    out_path = Path("exports") / f"tthol_{body.mode}_{int(time.time())}.csv"
    out_path.parent.mkdir(exist_ok=True)
    if exporter is None:
        out_path.write_text("character,item_id,name,quantity\n", encoding="utf-8")
        return ExportCsvResult(rows=0, path=str(out_path.resolve()))
    rows = exporter.export(mode=body.mode, out_path=out_path)
    return ExportCsvResult(rows=rows, path=str(out_path.resolve()))
```

- [ ] **Step 5: Mount both routers**

```python
# services/api/__init__.py — add to imports + include_router
from services.api import autoclick as autoclick_module
from services.api import export as export_module

# inside build_app:
    app.include_router(autoclick_module.router)
    app.include_router(export_module.router)
```

- [ ] **Step 6: Run tests (pass)**

```bash
uv run pytest tests/test_autoclick_router.py -v
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add services/api/autoclick.py services/api/export.py services/api/__init__.py tests/test_autoclick_router.py
git commit -m "feat(api): add autoclick and export routers (mocked)"
```

---

### Task 7: WebSocket `/ws/world` with mock tick

**Files:**
- Create: `services/events.py`
- Create: `services/api/world_ws.py`
- Modify: `services/api/__init__.py`
- Create: `tests/test_events.py`
- Create: `tests/test_world_ws.py`

- [ ] **Step 1: Write failing test for `WorldStream` pubsub**

```python
# tests/test_events.py
import asyncio
import pytest

from services.api_types import WorldSnapshot
from services.events import WorldStream


def _empty_snap() -> WorldSnapshot:
    return WorldSnapshot(chars=[], server_ts=0.0)


async def test_subscribe_receives_published_frame():
    stream = WorldStream()
    sub = stream.subscribe()
    await stream.publish(_empty_snap())
    snap = await asyncio.wait_for(sub.get(), timeout=0.5)
    assert snap.server_ts == 0.0


async def test_slow_subscriber_drops_oldest():
    stream = WorldStream(maxsize=2)
    sub = stream.subscribe()
    for i in range(5):
        snap = _empty_snap()
        snap.server_ts = float(i)
        await stream.publish(snap)
    # subscriber lagged — only the latest 2 frames should remain
    received = []
    while True:
        try:
            received.append(await asyncio.wait_for(sub.get(), timeout=0.1))
        except asyncio.TimeoutError:
            break
    assert len(received) == 2
    assert received[-1].server_ts == 4.0
```

- [ ] **Step 2: Run test (fails)**

```bash
uv run pytest tests/test_events.py -v
```
Expected: FAIL — `services.events` not found.

- [ ] **Step 3: Implement `WorldStream`**

```python
# services/events.py
"""In-process pub/sub for live WorldSnapshot frames feeding /ws/world.

Bounded per-subscriber queues with drop-oldest backpressure. Snapshots
are idempotent — only the latest frame matters, so dropping older
frames during slowdowns is correct.
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterator

from services.api_types import WorldSnapshot


class WorldStream:
    def __init__(self, maxsize: int = 4) -> None:
        self._maxsize = maxsize
        self._subscribers: list[asyncio.Queue[WorldSnapshot]] = []
        self._lock = asyncio.Lock()

    def subscribe(self) -> asyncio.Queue[WorldSnapshot]:
        q: asyncio.Queue[WorldSnapshot] = asyncio.Queue(maxsize=self._maxsize)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[WorldSnapshot]) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    async def publish(self, snap: WorldSnapshot) -> None:
        async with self._lock:
            for q in list(self._subscribers):
                while q.full():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                q.put_nowait(snap)

    def __iter__(self) -> Iterator[asyncio.Queue[WorldSnapshot]]:
        return iter(self._subscribers)
```

- [ ] **Step 4: Run events test (pass)**

```bash
uv run pytest tests/test_events.py -v
```
Expected: PASS.

- [ ] **Step 5: Write failing WS test**

```python
# tests/test_world_ws.py
import asyncio
import pytest
from fastapi.testclient import TestClient

from services.api import build_app
from services.api_types import WorldSnapshot


def test_world_ws_receives_frame():
    app = build_app(services=None)
    stream = app.state.services["world_stream"]  # set up by build_app when services=None

    async def push():
        await asyncio.sleep(0.05)
        await stream.publish(WorldSnapshot(chars=[], server_ts=42.0))

    client = TestClient(app)
    with client.websocket_connect("/ws/world") as ws:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(push())
        msg = ws.receive_json()
        assert msg["server_ts"] == 42.0
```

- [ ] **Step 6: Run test (fails)**

```bash
uv run pytest tests/test_world_ws.py -v
```
Expected: FAIL — `/ws/world` not mounted, `world_stream` not in services.

- [ ] **Step 7: Implement WS router**

```python
# services/api/world_ws.py
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws/world")
async def world_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    stream = websocket.app.state.services.get("world_stream")
    if stream is None:
        await websocket.close(code=1011, reason="world_stream not configured")
        return
    queue = stream.subscribe()
    try:
        while True:
            snap = await queue.get()
            await websocket.send_json(snap.model_dump())
    except WebSocketDisconnect:
        pass
    finally:
        stream.unsubscribe(queue)
```

- [ ] **Step 8: Auto-create `WorldStream` in `build_app` when missing**

```python
# services/api/__init__.py — replace build_app body
from fastapi import FastAPI

from services.api import (
    accounts as accounts_module,
    autoclick as autoclick_module,
    characters as characters_module,
    export as export_module,
    snapshots as snapshots_module,
    world_ws as world_ws_module,
)
from services.events import WorldStream


def build_app(services=None):
    app = FastAPI(title="tthol-memory", version="0.7.2")
    services = dict(services or {})
    services.setdefault("world_stream", WorldStream())
    app.state.services = services

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    app.include_router(characters_module.router)
    app.include_router(snapshots_module.router)
    app.include_router(accounts_module.router)
    app.include_router(autoclick_module.router)
    app.include_router(export_module.router)
    app.include_router(world_ws_module.router)
    return app
```

- [ ] **Step 9: Run WS test (pass)**

```bash
uv run pytest tests/test_world_ws.py -v
```
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add services/events.py services/api/world_ws.py services/api/__init__.py tests/test_events.py tests/test_world_ws.py
git commit -m "feat(api): add /ws/world WebSocket with WorldStream pubsub"
```

---

## Phase 3 — Frontend scaffold

### Task 8: Vite + React + TS scaffolding

**Files:**
- Create: `webui/package.json`
- Create: `webui/vite.config.ts`
- Create: `webui/tsconfig.json`
- Create: `webui/index.html`
- Create: `webui/src/main.tsx`
- Create: `webui/src/App.tsx`
- Create: `webui/src/api/client.ts`

- [ ] **Step 1: Create `webui/package.json`**

```json
{
  "name": "tthol-webui",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview",
    "gen-types": "openapi-typescript http://127.0.0.1:$(cat ../.omc/.dev-port)/openapi.json -o src/api/types.ts"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@types/react": "^18.3.5",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "openapi-typescript": "^7.4.0",
    "typescript": "^5.5.4",
    "vite": "^5.4.5"
  }
}
```

- [ ] **Step 2: Create `webui/vite.config.ts`**

```ts
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';
import { readFileSync } from 'node:fs';

let backendPort = '5173';
try {
  backendPort = readFileSync('../.omc/.dev-port', 'utf-8').trim();
} catch {}
const backend = `http://127.0.0.1:${backendPort}`;

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: backend, changeOrigin: true },
      '/ws': { target: backend, ws: true, changeOrigin: true },
    },
  },
  build: { outDir: 'dist' },
});
```

- [ ] **Step 3: Create `webui/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "isolatedModules": true,
    "resolveJsonModule": true
  },
  "include": ["src"]
}
```

- [ ] **Step 4: Create `webui/index.html`**

```html
<!doctype html>
<html lang="zh-Hant">
  <head>
    <meta charset="utf-8" />
    <title>御心鑒</title>
    <meta name="viewport" content="width=device-width,initial-scale=1" />
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link
      href="https://fonts.googleapis.com/css2?family=Noto+Serif+TC:wght@400;500;600;700&family=Noto+Sans+TC:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap"
      rel="stylesheet"
    />
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 5: Create `webui/src/main.tsx`**

```tsx
import React from 'react';
import ReactDOM from 'react-dom/client';
import { App } from './App';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

- [ ] **Step 6: Create `webui/src/App.tsx` (placeholder)**

```tsx
export function App() {
  return (
    <div style={{ padding: 24, color: '#e8dcc8', background: '#14100e', minHeight: '100vh' }}>
      <h1 style={{ fontFamily: 'Noto Serif TC, serif', letterSpacing: 4 }}>御心鑒</h1>
      <p>UI scaffolding online.</p>
    </div>
  );
}
```

- [ ] **Step 7: Create `webui/src/styles.css`**

```css
html, body, #root { margin: 0; height: 100%; background: #0c0a08; color: #e8dcc8; }
* { box-sizing: border-box; }
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: rgba(255,255,255,.06); }
::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,.12); }
input::placeholder { color: #6b5e4e; }
```

- [ ] **Step 8: Create `webui/src/api/client.ts`**

```ts
const base = '';  // same-origin via Vite proxy or pywebview

export async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${base}${path}`);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json() as Promise<T>;
}

export async function post<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${base}${path}`, {
    method: 'POST',
    headers: body !== undefined ? { 'content-type': 'application/json' } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json() as Promise<T>;
}

export async function del<T>(path: string): Promise<T> {
  const r = await fetch(`${base}${path}`, { method: 'DELETE' });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json() as Promise<T>;
}

export function openWorldSocket(onFrame: (snap: unknown) => void): WebSocket {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${proto}//${location.host}/ws/world`);
  ws.onmessage = (e) => onFrame(JSON.parse(e.data));
  return ws;
}
```

- [ ] **Step 9: Install + verify dev server boots**

```bash
cd webui
npm install
npm run dev
# Open http://localhost:5173 and confirm "御心鑒 — UI scaffolding online" renders
# Ctrl-C to stop
cd ..
```

- [ ] **Step 10: Add `.gitignore` entries for `webui/node_modules` and `webui/dist`**

```bash
printf "\nwebui/node_modules/\nwebui/dist/\n.omc/.dev-port\nexports/*.csv\n" >> .gitignore
```

- [ ] **Step 11: Commit**

```bash
git add webui/package.json webui/package-lock.json webui/vite.config.ts webui/tsconfig.json webui/index.html webui/src/main.tsx webui/src/App.tsx webui/src/styles.css webui/src/api/client.ts .gitignore
git commit -m "feat(webui): scaffold Vite + React + TypeScript"
```

---

### Task 9: Theme tokens + ThemeProvider

**Files:**
- Create: `webui/src/theme/tokens.ts`
- Create: `webui/src/theme/ThemeProvider.tsx`
- Modify: `webui/src/App.tsx`

- [ ] **Step 1: Port theme tokens from `.tmp_design/tthol/project/themes.jsx`**

```ts
// webui/src/theme/tokens.ts
export type ThemeName = '暗紅' | '暗金' | '水墨青';
export type FontName = '黑體' | '中文襯線';

export interface ThemeTokens {
  bg: string; bgPanel: string; bgRaised: string;
  line: string; lineSoft: string;
  text: string; textDim: string; textMute: string;
  accent: string; accentDim: string;
  gold: string; ok: string; warn: string; bad: string; seal: string; grid: string;
}

export const THEMES: Record<ThemeName, ThemeTokens> = {
  '暗紅': {
    bg: '#14100e', bgPanel: '#1c1815', bgRaised: '#241e1a',
    line: '#3a2f28', lineSoft: '#2a221d',
    text: '#e8dcc8', textDim: '#a89880', textMute: '#6b5e4e',
    accent: '#c83838', accentDim: '#8a2828',
    gold: '#c9a866', ok: '#7ca858', warn: '#d4a142', bad: '#c83838',
    seal: '#a02828', grid: 'rgba(200,56,56,.05)',
  },
  '暗金': {
    bg: '#0f0d0a', bgPanel: '#181410', bgRaised: '#221c16',
    line: '#3d3528', lineSoft: '#28221a',
    text: '#e8d9b0', textDim: '#9c8a68', textMute: '#6b5e44',
    accent: '#c9a866', accentDim: '#8a7440',
    gold: '#e8c878', ok: '#a8b888', warn: '#d4a142', bad: '#b85838',
    seal: '#8a2828', grid: 'rgba(201,168,102,.05)',
  },
  '水墨青': {
    bg: '#0c1014', bgPanel: '#141a20', bgRaised: '#1c242c',
    line: '#2c3a44', lineSoft: '#1f2830',
    text: '#d8e2e8', textDim: '#8a9aa4', textMute: '#5a6a74',
    accent: '#5a8898', accentDim: '#3a5868',
    gold: '#c9b884', ok: '#7ca898', warn: '#d4b072', bad: '#c87878',
    seal: '#a04848', grid: 'rgba(90,136,152,.05)',
  },
};

export const FONT_STACKS: Record<FontName, string> = {
  '中文襯線':
    '"Noto Serif TC", "Source Han Serif TC", "Songti TC", "PMingLiU", serif',
  '黑體':
    '"Noto Sans TC", "Source Han Sans TC", "PingFang TC", "Microsoft JhengHei", system-ui, sans-serif',
};
```

- [ ] **Step 2: Implement `ThemeProvider`**

```tsx
// webui/src/theme/ThemeProvider.tsx
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { THEMES, FONT_STACKS, type ThemeName, type FontName } from './tokens';

export type Density = 'compact' | 'normal' | 'comfy';
export type ChipMode = 'avatar' | 'text' | 'number';

interface ThemeState {
  theme: ThemeName;
  font: FontName;
  density: Density;
  chipMode: ChipMode;
  setTheme: (t: ThemeName) => void;
  setFont: (f: FontName) => void;
  setDensity: (d: Density) => void;
  setChipMode: (c: ChipMode) => void;
}

const Ctx = createContext<ThemeState | null>(null);

const KEY = 'tthol-tweaks';

function loadTweaks() {
  try {
    return JSON.parse(localStorage.getItem(KEY) || '{}');
  } catch {
    return {};
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const initial = loadTweaks();
  const [theme, setTheme] = useState<ThemeName>(initial.theme ?? '暗紅');
  const [font, setFont] = useState<FontName>(initial.font ?? '黑體');
  const [density, setDensity] = useState<Density>(initial.density ?? 'normal');
  const [chipMode, setChipMode] = useState<ChipMode>(initial.chipMode ?? 'avatar');

  useEffect(() => {
    localStorage.setItem(KEY, JSON.stringify({ theme, font, density, chipMode }));
    const t = THEMES[theme];
    const root = document.documentElement;
    root.style.setProperty('--tt-bg', t.bg);
    root.style.setProperty('--tt-panel', t.bgPanel);
    root.style.setProperty('--tt-raised', t.bgRaised);
    root.style.setProperty('--tt-line', t.line);
    root.style.setProperty('--tt-line-soft', t.lineSoft);
    root.style.setProperty('--tt-text', t.text);
    root.style.setProperty('--tt-dim', t.textDim);
    root.style.setProperty('--tt-mute', t.textMute);
    root.style.setProperty('--tt-accent', t.accent);
    root.style.setProperty('--tt-accent-dim', t.accentDim);
    root.style.setProperty('--tt-gold', t.gold);
    root.style.setProperty('--tt-ok', t.ok);
    root.style.setProperty('--tt-warn', t.warn);
    root.style.setProperty('--tt-bad', t.bad);
    root.style.setProperty('--tt-seal', t.seal);
    root.style.setProperty('--tt-font', FONT_STACKS[font]);
    root.style.setProperty('--tt-font-serif', FONT_STACKS['中文襯線']);
    root.style.setProperty('--tt-font-mono',
      '"JetBrains Mono", "IBM Plex Mono", ui-monospace, monospace');
    document.body.style.fontFamily = FONT_STACKS[font];
  }, [theme, font, density, chipMode]);

  const value = useMemo(
    () => ({ theme, font, density, chipMode, setTheme, setFont, setDensity, setChipMode }),
    [theme, font, density, chipMode],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useTheme() {
  const v = useContext(Ctx);
  if (!v) throw new Error('useTheme requires <ThemeProvider>');
  return v;
}
```

- [ ] **Step 3: Wrap App in ThemeProvider**

```tsx
// webui/src/App.tsx
import { ThemeProvider } from './theme/ThemeProvider';

export function App() {
  return (
    <ThemeProvider>
      <div style={{ padding: 24, color: 'var(--tt-text)', background: 'var(--tt-bg)', minHeight: '100vh', fontFamily: 'var(--tt-font)' }}>
        <h1 style={{ fontFamily: 'var(--tt-font-serif)', letterSpacing: 4 }}>御心鑒</h1>
        <p>Theme tokens loaded.</p>
      </div>
    </ThemeProvider>
  );
}
```

- [ ] **Step 4: Verify dev server**

```bash
cd webui && npm run dev
# Confirm 暗紅 theme colors apply (warm dark background, 駝色 text)
# Ctrl-C
cd ..
```

- [ ] **Step 5: Commit**

```bash
git add webui/src/theme/ webui/src/App.tsx
git commit -m "feat(webui): add theme tokens and ThemeProvider"
```

---

### Task 10: Visual primitives

**Files:**
- Create: `webui/src/primitives/Bar.tsx`
- Create: `webui/src/primitives/LinkDot.tsx`
- Create: `webui/src/primitives/Seal.tsx`
- Create: `webui/src/primitives/CharChip.tsx`
- Create: `webui/src/primitives/Panel.tsx`
- Create: `webui/src/primitives/StatNum.tsx`
- Create: `webui/src/primitives/FrameCorners.tsx`
- Create: `webui/src/primitives/index.ts`

Port these from `.tmp_design/tthol/project/primitives.jsx` but as TS components.

- [ ] **Step 1: Read prototype primitives**

```bash
cat .tmp_design/tthol/project/primitives.jsx
```

- [ ] **Step 2: Implement `Bar`**

```tsx
// webui/src/primitives/Bar.tsx
type Tone = 'hp' | 'mp' | 'weight' | 'plain';

const TONE_VARS: Record<Tone, string> = {
  hp: 'var(--tt-bad)',
  mp: 'var(--tt-accent)',
  weight: 'var(--tt-gold)',
  plain: 'var(--tt-dim)',
};

export function Bar({
  value, max, tone = 'plain', height = 6,
}: { value: number; max: number; tone?: Tone; height?: number }) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div style={{ background: 'var(--tt-line-soft)', height, borderRadius: 1, overflow: 'hidden' }}>
      <div style={{ width: `${pct}%`, height: '100%', background: TONE_VARS[tone], transition: 'width .25s' }} />
    </div>
  );
}
```

- [ ] **Step 3: Implement `LinkDot`**

```tsx
// webui/src/primitives/LinkDot.tsx
export type LinkStatus = 'ok' | 'weak' | 'lost';

const STATUS_COLOR: Record<LinkStatus, string> = {
  ok: 'var(--tt-ok)',
  weak: 'var(--tt-warn)',
  lost: 'var(--tt-bad)',
};

export function LinkDot({ status, size = 8 }: { status: LinkStatus; size?: number }) {
  return (
    <span
      style={{
        display: 'inline-block', width: size, height: size, borderRadius: '50%',
        background: STATUS_COLOR[status],
        boxShadow: status === 'ok' ? `0 0 ${size}px ${STATUS_COLOR[status]}` : undefined,
      }}
    />
  );
}
```

- [ ] **Step 4: Implement `Seal`, `CharChip`, `Panel`, `StatNum`, `FrameCorners`**

```tsx
// webui/src/primitives/Seal.tsx
import type { ReactNode } from 'react';

export function Seal({ size = 36, children }: { size?: number; children: ReactNode }) {
  return (
    <span
      style={{
        width: size, height: size, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        background: 'var(--tt-seal)', color: '#fff', fontFamily: 'var(--tt-font-serif)',
        fontWeight: 600, fontSize: size * 0.5, letterSpacing: 0,
      }}
    >
      {children}
    </span>
  );
}
```

```tsx
// webui/src/primitives/CharChip.tsx
import type { ChipMode } from '../theme/ThemeProvider';

export function CharChip({
  mode, name, idx, size = 24,
}: { mode: ChipMode; name: string; idx: number; size?: number }) {
  if (mode === 'avatar') {
    return (
      <span style={{
        width: size, height: size, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        background: 'var(--tt-raised)', border: '1px solid var(--tt-line)',
        fontFamily: 'var(--tt-font-serif)', fontWeight: 600,
      }}>{name[0]}</span>
    );
  }
  if (mode === 'number') {
    return (
      <span style={{
        width: size, height: size, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        background: 'var(--tt-raised)', border: '1px solid var(--tt-line)',
        fontFamily: 'var(--tt-font-mono)', fontSize: 11,
      }}>{String(idx + 1).padStart(2, '0')}</span>
    );
  }
  return <span style={{ color: 'var(--tt-dim)', fontSize: 12 }}>{name}</span>;
}
```

```tsx
// webui/src/primitives/Panel.tsx
import type { CSSProperties, ReactNode } from 'react';

export function Panel({
  title, children, style,
}: { title?: ReactNode; children: ReactNode; style?: CSSProperties }) {
  return (
    <section style={{
      background: 'var(--tt-panel)', border: '1px solid var(--tt-line)', padding: 14, ...style,
    }}>
      {title && (
        <header style={{
          fontFamily: 'var(--tt-font-serif)', fontSize: 13, letterSpacing: 4, fontWeight: 600,
          color: 'var(--tt-dim)', marginBottom: 10,
        }}>
          {title}
        </header>
      )}
      {children}
    </section>
  );
}
```

```tsx
// webui/src/primitives/StatNum.tsx
export function StatNum({ value, max, dim }: { value: number; max?: number; dim?: boolean }) {
  return (
    <span style={{
      fontFamily: 'var(--tt-font-mono)',
      color: dim ? 'var(--tt-mute)' : 'var(--tt-text)',
      fontVariantNumeric: 'tabular-nums',
    }}>
      {value}{max !== undefined && <span style={{ color: 'var(--tt-mute)' }}>/{max}</span>}
    </span>
  );
}
```

```tsx
// webui/src/primitives/FrameCorners.tsx
export function FrameCorners({ size = 10 }: { size?: number }) {
  const c: React.CSSProperties = {
    position: 'absolute', width: size, height: size, borderColor: 'var(--tt-accent)',
    borderStyle: 'solid', borderWidth: 0,
  };
  return (
    <>
      <span style={{ ...c, top: 0, left: 0, borderTopWidth: 1, borderLeftWidth: 1 }} />
      <span style={{ ...c, top: 0, right: 0, borderTopWidth: 1, borderRightWidth: 1 }} />
      <span style={{ ...c, bottom: 0, left: 0, borderBottomWidth: 1, borderLeftWidth: 1 }} />
      <span style={{ ...c, bottom: 0, right: 0, borderBottomWidth: 1, borderRightWidth: 1 }} />
    </>
  );
}
```

- [ ] **Step 5: Barrel export**

```ts
// webui/src/primitives/index.ts
export { Bar } from './Bar';
export { LinkDot, type LinkStatus } from './LinkDot';
export { Seal } from './Seal';
export { CharChip } from './CharChip';
export { Panel } from './Panel';
export { StatNum } from './StatNum';
export { FrameCorners } from './FrameCorners';
```

- [ ] **Step 6: Smoke-test in App**

```tsx
// webui/src/App.tsx
import { Bar, LinkDot, Panel, Seal, StatNum } from './primitives';
import { ThemeProvider } from './theme/ThemeProvider';

export function App() {
  return (
    <ThemeProvider>
      <div style={{ padding: 24, background: 'var(--tt-bg)', minHeight: '100vh', color: 'var(--tt-text)' }}>
        <h1 style={{ fontFamily: 'var(--tt-font-serif)', letterSpacing: 4 }}>
          <Seal>御</Seal> 御心鑒
        </h1>
        <Panel title="primitives demo" style={{ maxWidth: 360 }}>
          <div style={{ display: 'grid', gap: 8 }}>
            <div><LinkDot status="ok" /> 已連</div>
            <div><LinkDot status="weak" /> 校驗中</div>
            <div><LinkDot status="lost" /> 斷線</div>
            <Bar value={120} max={150} tone="hp" />
            <StatNum value={120} max={150} />
          </div>
        </Panel>
      </div>
    </ThemeProvider>
  );
}
```

- [ ] **Step 7: Verify dev server renders, then commit**

```bash
cd webui && npm run dev   # eyeball, Ctrl-C
cd ..
git add webui/src/primitives/ webui/src/App.tsx
git commit -m "feat(webui): add visual primitives (Bar/LinkDot/Seal/CharChip/Panel/StatNum/FrameCorners)"
```

---

## Phase 4 — Frontend pages

### Task 11: TopNav + page routing shell

**Files:**
- Create: `webui/src/components/TopNav.tsx`
- Create: `webui/src/components/ToastStack.tsx`
- Modify: `webui/src/App.tsx`

- [ ] **Step 1: Implement TopNav**

```tsx
// webui/src/components/TopNav.tsx
import { LinkDot, Seal } from '../primitives';

export type PageKey = 'dashboard' | 'treasury' | 'snapshots' | 'detail';

export function TopNav({
  page, onNav, linkedCount, totalCount,
}: { page: PageKey; onNav: (k: PageKey) => void; linkedCount: number; totalCount: number }) {
  const tabs: { k: PageKey; n: string }[] = [
    { k: 'dashboard', n: '江湖一覽' },
    { k: 'treasury',  n: '帳房' },
    { k: 'snapshots', n: '留影' },
  ];
  const ts = new Date().toLocaleTimeString('zh-TW', { hour12: false });
  return (
    <header style={{
      display: 'grid', gridTemplateColumns: '1fr auto 1fr', alignItems: 'center',
      padding: '10px 18px', borderBottom: '1px solid var(--tt-line)', background: 'var(--tt-panel)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
        <Seal size={32}>御</Seal>
        <div>
          <div style={{ fontFamily: 'var(--tt-font-serif)', fontSize: 16, fontWeight: 600, letterSpacing: 4 }}>御心鑒</div>
          <div style={{ fontSize: 10, color: 'var(--tt-mute)', letterSpacing: 2 }}>tthol memory reader · v0.7.2</div>
        </div>
      </div>
      <nav style={{ display: 'flex' }}>
        {tabs.map(t => {
          const active = page === t.k || (page === 'detail' && t.k === 'dashboard');
          return (
            <button key={t.k} onClick={() => onNav(t.k)} style={{
              padding: '8px 22px', fontFamily: 'var(--tt-font-serif)', fontSize: 14,
              letterSpacing: 4, fontWeight: 600,
              background: active ? 'var(--tt-bg)' : 'transparent',
              color: active ? 'var(--tt-text)' : 'var(--tt-dim)',
              border: '1px solid ' + (active ? 'var(--tt-line)' : 'transparent'),
              borderBottom: 'none', cursor: 'pointer',
            }}>{t.n}</button>
          );
        })}
      </nav>
      <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 14, fontSize: 11, color: 'var(--tt-dim)', fontFamily: 'var(--tt-font-mono)' }}>
        <span><LinkDot status="ok" size={6} /> {linkedCount}/{totalCount} 已連</span>
        <span style={{ color: 'var(--tt-mute)' }}>{ts}</span>
      </div>
    </header>
  );
}
```

- [ ] **Step 2: Stub `ToastStack`**

```tsx
// webui/src/components/ToastStack.tsx
export interface Toast { id: string; tone: 'ok' | 'warn' | 'bad'; text: string; }

export function ToastStack({ toasts }: { toasts: Toast[] }) {
  return (
    <div style={{ position: 'fixed', right: 16, bottom: 16, display: 'grid', gap: 8, zIndex: 100 }}>
      {toasts.map(t => (
        <div key={t.id} style={{
          background: 'var(--tt-panel)', border: `1px solid var(--tt-${t.tone})`,
          padding: '8px 12px', fontSize: 12, color: 'var(--tt-text)',
        }}>{t.text}</div>
      ))}
    </div>
  );
}
```

- [ ] **Step 3: Wire routing in `App.tsx`**

```tsx
// webui/src/App.tsx
import { useState } from 'react';
import { TopNav, type PageKey } from './components/TopNav';
import { ThemeProvider } from './theme/ThemeProvider';

export function App() {
  const [page, setPage] = useState<PageKey>('dashboard');
  return (
    <ThemeProvider>
      <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100vh', background: 'var(--tt-bg)', color: 'var(--tt-text)' }}>
        <TopNav page={page} onNav={setPage} linkedCount={0} totalCount={0} />
        <main style={{ flex: 1, padding: 24 }}>
          {page === 'dashboard' && <div>Dashboard placeholder</div>}
          {page === 'treasury'  && <div>Treasury placeholder</div>}
          {page === 'snapshots' && <div>Snapshots placeholder</div>}
        </main>
      </div>
    </ThemeProvider>
  );
}
```

- [ ] **Step 4: Verify and commit**

```bash
cd webui && npm run dev   # eyeball nav clicks
cd ..
git add webui/src/components/ webui/src/App.tsx
git commit -m "feat(webui): TopNav + page routing shell"
```

---

### Task 12: `useLiveChars` hook + Dashboard page

**Files:**
- Create: `webui/src/api/types.ts` (manual placeholder until openapi-typescript runs)
- Create: `webui/src/hooks/useLiveChars.ts`
- Create: `webui/src/pages/Dashboard.tsx`
- Modify: `webui/src/App.tsx`

- [ ] **Step 1: Create manual type stub**

```ts
// webui/src/api/types.ts
// THIS FILE IS REGENERATED by `npm run gen-types` from /openapi.json once
// the dev backend is running. Manual content here is a temporary stub.
export type LinkStatus = 'ok' | 'weak' | 'lost';

export interface Vitals { hp: number; hp_max: number; mp: number; mp_max: number; weight: number; weight_max: number; }
export interface Position { map_name: string | null; x: number; y: number; }
export interface AutoClickStatus { running: boolean; started_at: number | null; runtime_seconds: number | null; last_click_at: number | null; }
export interface CharacterRow { pid: number; name: string; sect: string; link: LinkStatus; level: number; vitals: Vitals; position: Position; autoclick: AutoClickStatus; }
export interface WorldSnapshot { chars: CharacterRow[]; server_ts: number; }
```

- [ ] **Step 2: Implement `useLiveChars`**

```ts
// webui/src/hooks/useLiveChars.ts
import { useEffect, useState } from 'react';
import { get, openWorldSocket } from '../api/client';
import type { WorldSnapshot } from '../api/types';

export function useLiveChars(): WorldSnapshot {
  const [snap, setSnap] = useState<WorldSnapshot>({ chars: [], server_ts: 0 });

  useEffect(() => {
    let cancelled = false;
    let ws: WebSocket | null = null;
    let backoff = 1000;

    const connect = async () => {
      try {
        const initial = await get<WorldSnapshot>('/api/world');
        if (!cancelled) setSnap(initial);
      } catch (e) {
        console.warn('initial /api/world failed', e);
      }

      ws = openWorldSocket((frame) => setSnap(frame as WorldSnapshot));
      ws.onclose = () => {
        if (cancelled) return;
        setTimeout(() => { if (!cancelled) connect(); }, backoff);
        backoff = Math.min(backoff * 2, 30_000);
      };
      ws.onopen = () => { backoff = 1000; };
      ws.onerror = () => ws?.close();
    };
    connect();
    return () => { cancelled = true; ws?.close(); };
  }, []);

  return snap;
}
```

- [ ] **Step 3: Implement Dashboard page**

```tsx
// webui/src/pages/Dashboard.tsx
import type { CharacterRow } from '../api/types';
import { Bar, LinkDot, Panel, StatNum } from '../primitives';
import { useTheme } from '../theme/ThemeProvider';

export function Dashboard({
  chars, onPick,
}: { chars: CharacterRow[]; onPick: (c: CharacterRow) => void }) {
  const { density } = useTheme();
  const rowPad = density === 'compact' ? 8 : density === 'comfy' ? 16 : 12;

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 320px', gap: 16, padding: 16 }}>
      <Panel title="江湖一覽">
        <div style={{ display: 'grid', gap: 4 }}>
          <Header />
          {chars.map(c => (
            <button
              key={c.pid}
              onClick={() => onPick(c)}
              style={{
                display: 'grid',
                gridTemplateColumns: '24px 80px 1fr 60px 100px 100px 100px 80px',
                gap: 12, alignItems: 'center', padding: rowPad,
                background: 'var(--tt-raised)', border: '1px solid var(--tt-line-soft)',
                color: 'var(--tt-text)', cursor: 'pointer', textAlign: 'left',
                opacity: c.link === 'lost' ? 0.45 : 1,
              }}
            >
              <LinkDot status={c.link} />
              <span style={{ fontFamily: 'var(--tt-font-serif)', letterSpacing: 2 }}>{c.name}</span>
              <span style={{ color: 'var(--tt-dim)', fontSize: 12 }}>{c.sect} · pid {c.pid}</span>
              <StatNum value={c.level} />
              <VitalCell tone="hp" v={c.vitals.hp} m={c.vitals.hp_max} />
              <VitalCell tone="mp" v={c.vitals.mp} m={c.vitals.mp_max} />
              <VitalCell tone="weight" v={c.vitals.weight} m={c.vitals.weight_max} />
              <span style={{ fontSize: 11, color: 'var(--tt-mute)', fontFamily: 'var(--tt-font-mono)' }}>
                {c.position.map_name ?? '—'} {c.position.x},{c.position.y}
              </span>
            </button>
          ))}
        </div>
      </Panel>
      <div style={{ display: 'grid', gap: 16, gridTemplateRows: 'auto auto' }}>
        <Panel title="警示">
          {chars.filter(c => c.vitals.hp / c.vitals.hp_max < 0.3).length === 0
            ? <span style={{ color: 'var(--tt-mute)', fontSize: 12 }}>無</span>
            : chars.filter(c => c.vitals.hp / c.vitals.hp_max < 0.3).map(c => (
                <div key={c.pid} style={{ fontSize: 12, color: 'var(--tt-bad)' }}>
                  {c.name} 氣血偏低
                </div>
              ))}
        </Panel>
        <Panel title="輔助執行">
          {chars.filter(c => c.autoclick.running).length === 0
            ? <span style={{ color: 'var(--tt-mute)', fontSize: 12 }}>未啟用</span>
            : chars.filter(c => c.autoclick.running).map(c => (
                <div key={c.pid} style={{ fontSize: 12 }}>{c.name}</div>
              ))}
        </Panel>
      </div>
    </div>
  );
}

function Header() {
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '24px 80px 1fr 60px 100px 100px 100px 80px',
      gap: 12, padding: '6px 12px', fontSize: 11, color: 'var(--tt-mute)',
      letterSpacing: 2, borderBottom: '1px solid var(--tt-line-soft)',
    }}>
      <span /> <span>名</span> <span>門派</span> <span>等級</span>
      <span>氣血</span> <span>內力</span> <span>負重</span> <span>方位</span>
    </div>
  );
}

function VitalCell({ tone, v, m }: { tone: 'hp' | 'mp' | 'weight'; v: number; m: number }) {
  return (
    <div style={{ display: 'grid', gap: 2 }}>
      <Bar value={v} max={m} tone={tone} />
      <span style={{ fontSize: 10, color: 'var(--tt-mute)', fontFamily: 'var(--tt-font-mono)' }}>
        {v}/{m}
      </span>
    </div>
  );
}
```

- [ ] **Step 4: Wire Dashboard into App**

```tsx
// webui/src/App.tsx
import { useState } from 'react';
import { TopNav, type PageKey } from './components/TopNav';
import { useLiveChars } from './hooks/useLiveChars';
import { Dashboard } from './pages/Dashboard';
import { ThemeProvider } from './theme/ThemeProvider';
import type { CharacterRow } from './api/types';

export function App() {
  const [page, setPage] = useState<PageKey>('dashboard');
  const [, setSelected] = useState<CharacterRow | null>(null);
  const snap = useLiveChars();
  const linked = snap.chars.filter(c => c.link === 'ok').length;

  return (
    <ThemeProvider>
      <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100vh', background: 'var(--tt-bg)', color: 'var(--tt-text)' }}>
        <TopNav page={page} onNav={(k) => { setPage(k); setSelected(null); }} linkedCount={linked} totalCount={snap.chars.length} />
        <main style={{ flex: 1 }}>
          {page === 'dashboard' && (
            <Dashboard chars={snap.chars} onPick={(c) => { setSelected(c); setPage('detail'); }} />
          )}
          {page === 'treasury'  && <div style={{ padding: 24 }}>Treasury placeholder</div>}
          {page === 'snapshots' && <div style={{ padding: 24 }}>Snapshots placeholder</div>}
          {page === 'detail'    && <div style={{ padding: 24 }}>CharDetail placeholder</div>}
        </main>
      </div>
    </ThemeProvider>
  );
}
```

- [ ] **Step 5: Run backend + frontend together**

```bash
# Terminal 1
uv run python -c "import socket, uvicorn; from services.api import build_app; \
s=socket.socket(); s.bind(('127.0.0.1',0)); p=s.getsockname()[1]; s.close(); \
print(p); open('.omc/.dev-port','w').write(str(p)); \
uvicorn.run(build_app(), host='127.0.0.1', port=p)"
```

```bash
# Terminal 2
cd webui && npm run dev
# Open http://localhost:5173 — dashboard should show 2 mock characters via Vite proxy
```

- [ ] **Step 6: Commit**

```bash
git add webui/src/api/types.ts webui/src/hooks/ webui/src/pages/Dashboard.tsx webui/src/App.tsx
git commit -m "feat(webui): Dashboard page with /api/world + /ws/world live updates"
```

---

### Task 13: Treasury page (帳房)

**Files:**
- Create: `webui/src/pages/Treasury.tsx`
- Modify: `webui/src/App.tsx`

- [ ] **Step 1: Read prototype**

```bash
cat .tmp_design/tthol/project/treasury-pro.jsx
```

- [ ] **Step 2: Implement Treasury (mock-driven for now)**

```tsx
// webui/src/pages/Treasury.tsx
import { useEffect, useState } from 'react';
import { get } from '../api/client';
import { Panel, StatNum } from '../primitives';

interface TreasurySummary { total_kinds: number; total_qty: number; on_person: number; week_delta: number; }

export function Treasury() {
  const [summary, setSummary] = useState<TreasurySummary>({ total_kinds: 0, total_qty: 0, on_person: 0, week_delta: 0 });
  const [search, setSearch] = useState('');

  useEffect(() => {
    // Until real /api/treasury/summary exists, derive from /api/snapshots count
    get<unknown[]>('/api/snapshots').then(rows => {
      setSummary({
        total_kinds: rows.length, total_qty: 0, on_person: 0, week_delta: 0,
      });
    }).catch(() => {});
  }, []);

  return (
    <div style={{ padding: 16 }}>
      <Panel title="帳房">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr) auto', gap: 24, alignItems: 'center', marginBottom: 16 }}>
          <Stat label="種類數" value={summary.total_kinds} />
          <Stat label="件數總計" value={summary.total_qty} />
          <Stat label="隨身可用" value={summary.on_person} />
          <Stat label="七日進出" value={summary.week_delta} />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜尋道具…"
            style={{
              background: 'var(--tt-bg)', color: 'var(--tt-text)',
              border: '1px solid var(--tt-line)', padding: '6px 10px',
              fontFamily: 'var(--tt-font-mono)',
            }}
          />
        </div>
        <div style={{ color: 'var(--tt-mute)', fontSize: 12 }}>
          道具列表 / 持有者明細 — 連接真實資料後填入。
        </div>
      </Panel>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div style={{ fontSize: 11, color: 'var(--tt-mute)', letterSpacing: 2 }}>{label}</div>
      <div style={{ fontSize: 24 }}><StatNum value={value} /></div>
    </div>
  );
}
```

- [ ] **Step 3: Wire into App**

```tsx
// webui/src/App.tsx — replace treasury placeholder
import { Treasury } from './pages/Treasury';
// ...
{page === 'treasury' && <Treasury />}
```

- [ ] **Step 4: Verify and commit**

```bash
cd webui && npm run dev   # eyeball treasury tab
cd ..
git add webui/src/pages/Treasury.tsx webui/src/App.tsx
git commit -m "feat(webui): Treasury page with summary header"
```

---

### Task 14: Snapshots page (留影)

**Files:**
- Create: `webui/src/pages/Snapshots.tsx`
- Modify: `webui/src/App.tsx`

- [ ] **Step 1: Implement Snapshots (no diff in v1)**

```tsx
// webui/src/pages/Snapshots.tsx
import { useEffect, useState } from 'react';
import { get } from '../api/client';
import { Panel } from '../primitives';

interface SnapshotRow {
  snapshot_id: number; character_name: string; account_id: number | null;
  source: 'inventory' | 'warehouse'; saved_at: string; item_count: number;
}

export function Snapshots() {
  const [rows, setRows] = useState<SnapshotRow[]>([]);
  const [selected, setSelected] = useState<SnapshotRow | null>(null);

  useEffect(() => { get<SnapshotRow[]>('/api/snapshots').then(setRows).catch(() => {}); }, []);

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: 16, padding: 16 }}>
      <Panel title="留影列表">
        <div style={{ display: 'grid', gap: 4 }}>
          {rows.map(r => (
            <button
              key={r.snapshot_id}
              onClick={() => setSelected(r)}
              style={{
                textAlign: 'left', padding: 8, background: selected?.snapshot_id === r.snapshot_id ? 'var(--tt-raised)' : 'transparent',
                border: '1px solid var(--tt-line-soft)', color: 'var(--tt-text)', cursor: 'pointer',
              }}
            >
              <div style={{ fontFamily: 'var(--tt-font-serif)', letterSpacing: 2 }}>{r.character_name}</div>
              <div style={{ fontSize: 11, color: 'var(--tt-mute)', fontFamily: 'var(--tt-font-mono)' }}>
                {r.saved_at} · {r.source} · {r.item_count} 件
              </div>
            </button>
          ))}
        </div>
      </Panel>
      <Panel title="留影內容">
        {selected ? (
          <div>
            <div style={{ marginBottom: 12 }}>
              <strong style={{ fontFamily: 'var(--tt-font-serif)' }}>{selected.character_name}</strong>
              <span style={{ color: 'var(--tt-mute)', marginLeft: 8 }}>{selected.saved_at}</span>
            </div>
            <div style={{ color: 'var(--tt-mute)', fontSize: 12 }}>
              {selected.item_count} 件道具（道具明細 v1.1 接入；diff 已延後）
            </div>
          </div>
        ) : (
          <div style={{ color: 'var(--tt-mute)', fontSize: 12 }}>選擇一筆留影查看內容</div>
        )}
      </Panel>
    </div>
  );
}
```

- [ ] **Step 2: Wire into App**

```tsx
// webui/src/App.tsx — replace snapshots placeholder
import { Snapshots } from './pages/Snapshots';
// ...
{page === 'snapshots' && <Snapshots />}
```

- [ ] **Step 3: Verify and commit**

```bash
cd webui && npm run dev   # eyeball
cd ..
git add webui/src/pages/Snapshots.tsx webui/src/App.tsx
git commit -m "feat(webui): Snapshots page (留影) without diff"
```

---

### Task 15: Character Detail page with 4 tabs

**Files:**
- Create: `webui/src/pages/CharDetail/index.tsx`
- Create: `webui/src/pages/CharDetail/BodyTab.tsx`
- Create: `webui/src/pages/CharDetail/ItemsTab.tsx`
- Create: `webui/src/pages/CharDetail/AutoClickTab.tsx`
- Create: `webui/src/pages/CharDetail/MapAnalysis.tsx`
- Modify: `webui/src/App.tsx`

- [ ] **Step 1: Implement tabs (BodyTab, ItemsTab, AutoClickTab, MapAnalysis placeholder)**

```tsx
// webui/src/pages/CharDetail/BodyTab.tsx
import { useEffect, useState } from 'react';
import { get } from '../../api/client';
import { Panel, StatNum } from '../../primitives';

interface Detail {
  pid: number; name: string;
  stats: {
    waigong: number; neili: number; genggu: number; shenfa: number; jiqiao: number; xuanxue: number;
    wugong: number; wugong_base: number; neijing: number; fangyu: number; huji: number; mingzhong: number; shanduo: number;
  };
}

export function BodyTab({ pid }: { pid: number }) {
  const [d, setD] = useState<Detail | null>(null);
  useEffect(() => { get<Detail>(`/api/characters/${pid}`).then(setD).catch(() => {}); }, [pid]);
  if (!d) return <div style={{ color: 'var(--tt-mute)' }}>讀取中…</div>;
  const six = [
    ['外功', d.stats.waigong], ['內力', d.stats.neili], ['根骨', d.stats.genggu],
    ['身法', d.stats.shenfa], ['技巧', d.stats.jiqiao], ['玄學', d.stats.xuanxue],
  ] as const;
  const seven = [
    ['物攻', d.stats.wugong], ['基礎', d.stats.wugong_base], ['內勁', d.stats.neijing],
    ['防禦', d.stats.fangyu], ['護勁', d.stats.huji], ['命中', d.stats.mingzhong], ['閃躲', d.stats.shanduo],
  ] as const;
  return (
    <div style={{ display: 'grid', gap: 16, gridTemplateColumns: '1fr 1fr' }}>
      <Panel title="六屬">
        <Grid pairs={six} />
      </Panel>
      <Panel title="七戰">
        <Grid pairs={seven} />
      </Panel>
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

```tsx
// webui/src/pages/CharDetail/ItemsTab.tsx
import { useState } from 'react';
import { post } from '../../api/client';
import { Panel } from '../../primitives';

interface Item { item_id: number; name: string; quantity: number; source: 'inventory' | 'warehouse'; }

export function ItemsTab({ pid }: { pid: number }) {
  const [items, setItems] = useState<Item[]>([]);
  const [filter, setFilter] = useState<'all' | 'inventory' | 'warehouse'>('all');

  const scanInventory = () => post<Item[]>(`/api/characters/${pid}/inventory/scan`).then(items => setItems(prev => [...prev.filter(i => i.source !== 'inventory'), ...items]));
  const scanWarehouse = () => post<Item[]>(`/api/characters/${pid}/warehouse/scan`).then(items => setItems(prev => [...prev.filter(i => i.source !== 'warehouse'), ...items]));

  const visible = items.filter(i => filter === 'all' || i.source === filter);
  return (
    <Panel title="行囊 / 庫房">
      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        <button onClick={scanInventory}>掃描行囊</button>
        <button onClick={scanWarehouse}>掃描庫房</button>
        {(['all', 'inventory', 'warehouse'] as const).map(f => (
          <button key={f} onClick={() => setFilter(f)}
            style={{ background: filter === f ? 'var(--tt-raised)' : 'transparent', color: 'var(--tt-text)' }}>
            {f === 'all' ? '全部' : f === 'inventory' ? '身' : '庫'}
          </button>
        ))}
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {visible.map(i => (
          <div key={`${i.source}-${i.item_id}`} style={{ display: 'flex', justifyContent: 'space-between', padding: 6, borderBottom: '1px solid var(--tt-line-soft)' }}>
            <span>{i.name}</span>
            <span style={{ fontFamily: 'var(--tt-font-mono)', color: 'var(--tt-dim)' }}>×{i.quantity}</span>
          </div>
        ))}
      </div>
    </Panel>
  );
}
```

```tsx
// webui/src/pages/CharDetail/AutoClickTab.tsx
import { useState } from 'react';
import { post } from '../../api/client';
import { Panel } from '../../primitives';

export function AutoClickTab({ pid }: { pid: number }) {
  const [running, setRunning] = useState(false);

  const start = async () => {
    await post(`/api/characters/${pid}/autoclick/start`, { interval_seconds: 60, merchant_idx: 0 });
    setRunning(true);
  };
  const stop = async () => { await post(`/api/characters/${pid}/autoclick/stop`); setRunning(false); };

  return (
    <Panel title="輔助·召喚商人">
      <div style={{ display: 'flex', gap: 8 }}>
        <button onClick={start} disabled={running}>啟動</button>
        <button onClick={stop} disabled={!running}>停止</button>
        <span style={{ alignSelf: 'center', color: running ? 'var(--tt-ok)' : 'var(--tt-mute)' }}>
          {running ? '執行中' : '未啟用'}
        </span>
      </div>
    </Panel>
  );
}
```

```tsx
// webui/src/pages/CharDetail/MapAnalysis.tsx
import { Panel } from '../../primitives';

export function MapAnalysis() {
  return (
    <Panel title="行止">
      <div style={{ color: 'var(--tt-mute)', textAlign: 'center', padding: 32 }}>
        資料準備中
      </div>
    </Panel>
  );
}
```

- [ ] **Step 2: Implement tab shell**

```tsx
// webui/src/pages/CharDetail/index.tsx
import { useState } from 'react';
import type { CharacterRow } from '../../api/types';
import { LinkDot, Seal } from '../../primitives';
import { BodyTab } from './BodyTab';
import { ItemsTab } from './ItemsTab';
import { AutoClickTab } from './AutoClickTab';
import { MapAnalysis } from './MapAnalysis';

const TABS = [
  { k: 'body', n: '根脈' },
  { k: 'items', n: '行囊' },
  { k: 'autoclick', n: '輔助' },
  { k: 'maps', n: '行止' },
] as const;

type TabKey = typeof TABS[number]['k'];

export function CharDetail({ char, onBack }: { char: CharacterRow; onBack: () => void }) {
  const [tab, setTab] = useState<TabKey>('body');
  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 16 }}>
        <button onClick={onBack} style={{ background: 'transparent', color: 'var(--tt-dim)' }}>← 返回</button>
        <Seal>{char.name[0]}</Seal>
        <div>
          <div style={{ fontFamily: 'var(--tt-font-serif)', fontSize: 18 }}>{char.name}</div>
          <div style={{ fontSize: 12, color: 'var(--tt-dim)' }}>{char.sect} · pid {char.pid}</div>
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
          <LinkDot status={char.link} /> Lv {char.level}
        </div>
      </div>
      <nav style={{ display: 'flex', gap: 0, marginBottom: 12 }}>
        {TABS.map(t => (
          <button key={t.k} onClick={() => setTab(t.k)} style={{
            padding: '6px 18px', background: tab === t.k ? 'var(--tt-raised)' : 'transparent',
            color: 'var(--tt-text)', border: '1px solid var(--tt-line)', borderBottom: 'none',
            fontFamily: 'var(--tt-font-serif)', letterSpacing: 4, cursor: 'pointer',
          }}>{t.n}</button>
        ))}
      </nav>
      {tab === 'body' && <BodyTab pid={char.pid} />}
      {tab === 'items' && <ItemsTab pid={char.pid} />}
      {tab === 'autoclick' && <AutoClickTab pid={char.pid} />}
      {tab === 'maps' && <MapAnalysis />}
    </div>
  );
}
```

- [ ] **Step 3: Wire into App**

```tsx
// webui/src/App.tsx — replace detail placeholder
import { CharDetail } from './pages/CharDetail';
// ...
const [selected, setSelected] = useState<CharacterRow | null>(null);
// ...
{page === 'detail' && selected && (
  <CharDetail char={selected} onBack={() => setPage('dashboard')} />
)}
```

- [ ] **Step 4: Verify dev flow + commit**

```bash
cd webui && npm run dev   # click a row → enter detail, switch tabs
cd ..
git add webui/src/pages/CharDetail/ webui/src/App.tsx
git commit -m "feat(webui): CharDetail page with 4 tabs (根脈/行囊/輔助/行止)"
```

---

## Phase 5 — Service relocation (gui/ → services/)

### Task 16: Move snapshot_db to services/

**Files:**
- Create: `services/snapshot_db.py` (copy of `gui/snapshot_db.py`)
- Modify: `tests/test_snapshot_db.py`
- Delete (later): `gui/snapshot_db.py`

- [ ] **Step 1: Copy file as-is**

```bash
cp gui/snapshot_db.py services/snapshot_db.py
```

- [ ] **Step 2: Update test imports**

```python
# tests/test_snapshot_db.py — change first import
from services.snapshot_db import SnapshotDB  # was: from gui.snapshot_db import SnapshotDB
```

(Apply identical sed-style replacement everywhere `gui.snapshot_db` appears in this test.)

- [ ] **Step 3: Run snapshot tests**

```bash
uv run pytest tests/test_snapshot_db.py -v
```
Expected: PASS — pure SQLite, no Qt deps.

- [ ] **Step 4: Commit**

```bash
git add services/snapshot_db.py tests/test_snapshot_db.py
git commit -m "refactor: relocate snapshot_db to services/"
```

---

### Task 17: Decouple worker from Qt and move

**Files:**
- Create: `services/worker.py` (rewritten without `QThread`/`Signal`)
- Create: `services/char_session.py`
- Create: `tests/test_worker_session.py`
- Delete (later): `gui/worker.py`, `tests/test_worker.py`, `tests/test_worker_filter.py`

- [ ] **Step 1: Implement non-Qt `ReaderWorker`**

```python
# services/worker.py
"""Background worker thread for one PID. No Qt — uses callbacks."""
from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable

import pymem

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reader import (
    get_display_fields,
    load_item_db,
    load_knowledge,
    locate_character,
    locate_inventory,
    locate_map_name,
    read_all_fields,
    read_character_name,
    read_hp_from_player_chain,
    read_inventory,
    verify_structure,
    verify_structure_shifted,
)
from warehouse_scan import (
    SLOT_SIZE,
    locate_all_slot_arrays,
    read_slot_array,
    walk_back_to_start,
)

POLL_INTERVAL = 1.0
FAILURE_THRESHOLD = 3
LOCATE_RETRY_INTERVAL = 3.0
LOCATE_MAX_RETRIES = 10


class ReaderWorker(threading.Thread):
    """Per-PID worker thread. Calls callbacks instead of emitting Qt signals."""

    def __init__(
        self,
        pid: int,
        on_state: Callable[[str], None],
        on_stats: Callable[[list[tuple[str, int]]], None],
        on_inventory: Callable[[list[tuple[int, int, str]]], None],
        on_warehouse: Callable[[list[tuple[int, int, str]]], None],
        on_error: Callable[[str], None],
    ) -> None:
        super().__init__(daemon=True)
        self._pid = pid
        self._cb_state = on_state
        self._cb_stats = on_stats
        self._cb_inventory = on_inventory
        self._cb_warehouse = on_warehouse
        self._cb_error = on_error
        self._hp_value: int | None = None
        self._offset_filters = None
        self._compat_mode = False
        self._stop = threading.Event()
        self._scan_inv = False
        self._scan_wh = False
        self._knowledge = load_knowledge()
        self._display_fields = get_display_fields(self._knowledge)
        self._item_db = load_item_db()

    def request_inventory(self) -> None:
        self._scan_inv = True

    def request_warehouse(self) -> None:
        self._scan_wh = True

    def stop(self) -> None:
        self._stop.set()

    # The full state-machine body is ported verbatim from gui/worker.py
    # (DISCONNECTED → CONNECTING → LOCATED → READ_ERROR/RESCANNING),
    # but every `self.state_changed.emit(x)` becomes `self._cb_state(x)`,
    # `self.stats_updated.emit(rows)` becomes `self._cb_stats(rows)`, etc.
    def run(self) -> None:  # pragma: no cover — runtime only
        # Port logic verbatim from gui/worker.py::ReaderWorker.run
        # replacing `.emit(...)` calls with self._cb_*(...) callbacks.
        raise NotImplementedError("port body from gui/worker.py")
```

- [ ] **Step 2: Port the full `run()` body from `gui/worker.py`**

Open `gui/worker.py`, copy lines from `def run(self):` through end of class. Paste over the `raise NotImplementedError` line. Replace every signal emission:
- `self.state_changed.emit(s)` → `self._cb_state(s)`
- `self.stats_updated.emit(rows)` → `self._cb_stats(rows)`
- `self.inventory_ready.emit(items)` → `self._cb_inventory(items)`
- `self.warehouse_ready.emit(items)` → `self._cb_warehouse(items)`
- `self.scan_error.emit(msg)` → `self._cb_error(msg)`

- [ ] **Step 3: Implement `CharSession` adapter**

```python
# services/char_session.py
"""One CharSession per PID. Owns the worker thread and the latest snapshot."""
from __future__ import annotations

import threading
import time
from typing import Literal

from services.api_types import (
    AutoClickStatus,
    CharacterDetail,
    CharacterRow,
    CharacterStats,
    Item,
    Position,
    Vitals,
)
from services.worker import ReaderWorker


class CharSession:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.name: str | None = None
        self.sect: str | None = None
        self._state: str = "DISCONNECTED"
        self._latest_stats: dict[str, int] = {}
        self._latest_inv: list[Item] = []
        self._latest_wh: list[Item] = []
        self._lock = threading.Lock()
        self._worker = ReaderWorker(
            pid=pid,
            on_state=self._on_state,
            on_stats=self._on_stats,
            on_inventory=self._on_inv,
            on_warehouse=self._on_wh,
            on_error=lambda _msg: None,
        )

    def start(self, hp: int | None = None, compat_mode: bool = False) -> None:
        self._worker._hp_value = hp
        self._worker._compat_mode = compat_mode
        if not self._worker.is_alive():
            self._worker.start()

    def stop(self) -> None:
        self._worker.stop()

    def request_inventory(self) -> None:
        self._worker.request_inventory()

    def request_warehouse(self) -> None:
        self._worker.request_warehouse()

    @property
    def link(self) -> Literal["ok", "weak", "lost"]:
        if self._state == "LOCATED":
            return "ok"
        if self._state in ("CONNECTING", "RESCANNING", "READ_ERROR"):
            return "weak"
        return "lost"

    def row(self) -> CharacterRow | None:
        with self._lock:
            if not self.name:
                return None
            s = self._latest_stats
            return CharacterRow(
                pid=self.pid, name=self.name, sect=self.sect or "", link=self.link,
                level=s.get("level", 0),
                vitals=Vitals(
                    hp=s.get("hp", 0), hp_max=s.get("hp_max", 0),
                    mp=s.get("mp", 0), mp_max=s.get("mp_max", 0),
                    weight=s.get("weight", 0), weight_max=s.get("weight_max", 0),
                ),
                position=Position(map_name=s.get("map_name"), x=s.get("x", 0), y=s.get("y", 0)),
                autoclick=AutoClickStatus(running=False),
            )

    def detail(self) -> CharacterDetail:
        with self._lock:
            s = self._latest_stats
            return CharacterDetail(
                pid=self.pid, name=self.name or "", sect=self.sect or "", link=self.link,
                stats=CharacterStats(
                    level=s.get("level", 0),
                    waigong=s.get("waigong", 0), neili=s.get("neili", 0), genggu=s.get("genggu", 0),
                    shenfa=s.get("shenfa", 0), jiqiao=s.get("jiqiao", 0), xuanxue=s.get("xuanxue", 0),
                    wugong=s.get("wugong", 0), wugong_base=s.get("wugong_base", 0),
                    neijing=s.get("neijing", 0), fangyu=s.get("fangyu", 0),
                    huji=s.get("huji", 0), mingzhong=s.get("mingzhong", 0), shanduo=s.get("shanduo", 0),
                ),
                vitals=Vitals(
                    hp=s.get("hp", 0), hp_max=s.get("hp_max", 0),
                    mp=s.get("mp", 0), mp_max=s.get("mp_max", 0),
                    weight=s.get("weight", 0), weight_max=s.get("weight_max", 0),
                ),
                position=Position(map_name=s.get("map_name"), x=s.get("x", 0), y=s.get("y", 0)),
                autoclick=AutoClickStatus(running=False),
                inventory=self._latest_inv or None,
                warehouse=self._latest_wh or None,
            )

    # Worker callbacks ------------------------------------------------------

    def _on_state(self, s: str) -> None:
        with self._lock:
            self._state = s

    def _on_stats(self, rows: list[tuple[str, int]]) -> None:
        with self._lock:
            self._latest_stats = dict(rows)
            if "name" in self._latest_stats:
                self.name = str(self._latest_stats["name"])

    def _on_inv(self, items: list[tuple[int, int, str]]) -> None:
        with self._lock:
            self._latest_inv = [
                Item(item_id=iid, name=name, quantity=qty, source="inventory")
                for iid, qty, name in items
            ]

    def _on_wh(self, items: list[tuple[int, int, str]]) -> None:
        with self._lock:
            self._latest_wh = [
                Item(item_id=iid, name=name, quantity=qty, source="warehouse")
                for iid, qty, name in items
            ]
```

- [ ] **Step 4: Write a thin test (no real pymem)**

```python
# tests/test_worker_session.py
from services.char_session import CharSession


def test_link_lost_when_disconnected():
    sess = CharSession(pid=1234)
    assert sess.link == "lost"


def test_callbacks_update_state():
    sess = CharSession(pid=1234)
    sess._on_state("LOCATED")
    sess._on_stats([("level", 20), ("hp", 100), ("hp_max", 120),
                    ("mp", 50), ("mp_max", 60), ("weight", 30), ("weight_max", 200),
                    ("x", 1), ("y", 2)])
    sess.name = "無塵"
    sess.sect = "少林"
    row = sess.row()
    assert row is not None
    assert row.link == "ok"
    assert row.vitals.hp == 100
```

- [ ] **Step 5: Run test (passes)**

```bash
uv run pytest tests/test_worker_session.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/worker.py services/char_session.py tests/test_worker_session.py
git commit -m "refactor: port worker to non-Qt callbacks; add CharSession adapter"
```

---

### Task 18: Move auto_click / fake_active / process_detector

**Files:**
- Create: `services/auto_click.py` (logic only — no Qt UI)
- Create: `services/fake_active.py` (move as-is)
- Create: `services/process_detector.py` (move as-is)
- Modify (later): delete `gui/auto_click_tab.py`, `gui/fake_active.py`, `gui/process_detector.py`

- [ ] **Step 1: Read current files to identify Qt-coupled pieces**

```bash
cat gui/auto_click_tab.py gui/fake_active.py gui/process_detector.py | head -200
```

- [ ] **Step 2: Move `fake_active.py` and `process_detector.py` verbatim**

```bash
cp gui/fake_active.py services/fake_active.py
cp gui/process_detector.py services/process_detector.py
```

If either imports from `PySide6` or `gui.*`, replace with stdlib / `services.*` equivalents.

- [ ] **Step 3: Extract auto-click logic from `gui/auto_click_tab.py`**

Pull only the click-loop and Win32 PostMessageW logic into `services/auto_click.py`. The Qt widget code is dropped. Define:

```python
# services/auto_click.py
"""Auto-click manager: per-PID background thread that issues Win32 PostMessageW
clicks on the game window for the merchant-summon button."""
from __future__ import annotations

import threading
import time

from services.api_types import AutoClickConfig, AutoClickStatus


class _Job:
    def __init__(self, pid: int, config: AutoClickConfig) -> None:
        self.pid = pid
        self.config = config
        self.started_at = time.time()
        self.last_click_at: float | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> AutoClickStatus:
        return AutoClickStatus(
            running=self._thread.is_alive() and not self._stop.is_set(),
            started_at=self.started_at,
            runtime_seconds=int(time.time() - self.started_at),
            last_click_at=self.last_click_at,
        )

    def _run(self) -> None:  # pragma: no cover — Win32 only
        # Port click loop from gui/auto_click_tab.py:
        #   1. find HWND for self.pid
        #   2. compute scaled coordinates for self.config.merchant_idx
        #   3. PostMessageW(WM_LBUTTONDOWN/UP) on a self.config.interval_seconds tick
        # See gui/auto_click_tab.py for the original algorithm.
        raise NotImplementedError


class AutoClickManager:
    def __init__(self) -> None:
        self._jobs: dict[int, _Job] = {}

    def start(self, pid: int, config: AutoClickConfig) -> None:
        if pid in self._jobs:
            self._jobs[pid].stop()
        job = _Job(pid, config)
        self._jobs[pid] = job
        job.start()

    def stop(self, pid: int) -> None:
        job = self._jobs.pop(pid, None)
        if job:
            job.stop()

    def test_click(self, pid: int, merchant_idx: int) -> None:  # pragma: no cover
        # Single click without starting a recurring job
        raise NotImplementedError

    def status(self, pid: int) -> AutoClickStatus:
        job = self._jobs.get(pid)
        if job is None:
            return AutoClickStatus(running=False)
        return job.status()
```

- [ ] **Step 4: Port the actual click-loop body from `gui/auto_click_tab.py`**

Open `gui/auto_click_tab.py`, find the per-tick click logic (FindWindow / PostMessageW / coordinate scaling). Paste into `_Job._run`. Strip Qt signals.

- [ ] **Step 5: Commit**

```bash
git add services/auto_click.py services/fake_active.py services/process_detector.py
git commit -m "refactor: move auto_click/fake_active/process_detector to services/"
```

---

## Phase 6 — Wire real services into the API

### Task 19: WorkerManager facade

**Files:**
- Create: `services/worker_manager.py`
- Create: `tests/test_worker_manager.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_worker_manager.py
from unittest.mock import patch
from services.api_types import WorldSnapshot
from services.worker_manager import WorkerManager


def test_world_snapshot_empty_initially():
    wm = WorkerManager()
    snap = wm.world_snapshot()
    assert isinstance(snap, WorldSnapshot)
    assert snap.chars == []


@patch("services.worker_manager.find_tthol_processes")
def test_list_characters_returns_processes(mock_find):
    mock_find.return_value = [{"pid": 1234}, {"pid": 5678}]
    wm = WorkerManager()
    chars = wm.list_characters()
    assert len(chars) == 2
    assert chars[0].pid == 1234
    assert chars[0].link == "lost"
```

- [ ] **Step 2: Run (fails)**

```bash
uv run pytest tests/test_worker_manager.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implement WorkerManager**

```python
# services/worker_manager.py
from __future__ import annotations

import time

from services.api_types import (
    Character,
    CharacterDetail,
    ConnectRequest,
    ConnectResult,
    SaveSnapshotResult,
    WorldSnapshot,
)
from services.char_session import CharSession
from services.process_detector import find_tthol_processes


class WorkerManager:
    def __init__(self) -> None:
        self._sessions: dict[int, CharSession] = {}

    def list_characters(self) -> list[Character]:
        procs = find_tthol_processes()
        out: list[Character] = []
        for p in procs:
            pid = p["pid"]
            sess = self._sessions.get(pid)
            out.append(Character(
                pid=pid,
                name=sess.name if sess else None,
                sect=sess.sect if sess else None,
                level=None,
                link=sess.link if sess else "lost",
            ))
        return out

    def world_snapshot(self) -> WorldSnapshot:
        rows = []
        for sess in self._sessions.values():
            r = sess.row()
            if r is not None:
                rows.append(r)
        return WorldSnapshot(chars=rows, server_ts=time.time())

    def character_detail(self, pid: int) -> CharacterDetail:
        sess = self._sessions.get(pid)
        if sess is None:
            sess = CharSession(pid)
            self._sessions[pid] = sess
        return sess.detail()

    def connect(self, pid: int, body: ConnectRequest) -> ConnectResult:
        sess = self._sessions.get(pid)
        if sess is None:
            sess = CharSession(pid)
            self._sessions[pid] = sess
        sess.start(hp=body.hp, compat_mode=body.options.compat_mode)
        return ConnectResult(ok=True)

    def disconnect(self, pid: int) -> None:
        sess = self._sessions.pop(pid, None)
        if sess:
            sess.stop()

    def relocate(self, pid: int, hp: int) -> ConnectResult:
        sess = self._sessions.get(pid)
        if sess is None:
            return ConnectResult(ok=False, error="No session for pid")
        sess.stop()
        new_sess = CharSession(pid)
        new_sess.start(hp=hp)
        self._sessions[pid] = new_sess
        return ConnectResult(ok=True)

    def focus(self, pid: int) -> None:
        # Win32 SetForegroundWindow — implement in step 4 or stub for now
        pass

    def save_snapshot(self, pid: int, source: str) -> SaveSnapshotResult:
        # Wired in Task 21 once snapshot_db plumbing is set up
        return SaveSnapshotResult(saved=False)
```

- [ ] **Step 4: Run test (passes)**

```bash
uv run pytest tests/test_worker_manager.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/worker_manager.py tests/test_worker_manager.py
git commit -m "feat: add WorkerManager facade"
```

---

### Task 20: Wire real services into `build_app`

**Files:**
- Modify: `services/api/__init__.py`
- Modify: `services/_mock.py` (remove unused imports if they leak)
- Modify: `tests/test_characters_router.py` (add real-service test variant if useful)

- [ ] **Step 1: Replace `build_app` to accept and use real services**

```python
# services/api/__init__.py
from typing import Any

from fastapi import FastAPI

from services.api import (
    accounts as accounts_module,
    autoclick as autoclick_module,
    characters as characters_module,
    export as export_module,
    snapshots as snapshots_module,
    world_ws as world_ws_module,
)
from services.events import WorldStream


def build_app(services: dict[str, Any] | None = None) -> FastAPI:
    app = FastAPI(title="tthol-memory", version="0.7.2")
    services = dict(services or {})
    services.setdefault("world_stream", WorldStream())
    app.state.services = services

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    app.include_router(characters_module.router)
    app.include_router(snapshots_module.router)
    app.include_router(accounts_module.router)
    app.include_router(autoclick_module.router)
    app.include_router(export_module.router)
    app.include_router(world_ws_module.router)
    return app
```

(Routers already short-circuit to mocks when `services["worker_manager"]` is missing — they will use the real WM as soon as `app.py` instantiates one.)

- [ ] **Step 2: Re-run all router tests (still pass — services=None path stays mocked)**

```bash
uv run pytest tests/test_characters_router.py tests/test_snapshots_router.py tests/test_autoclick_router.py tests/test_world_ws.py -v
```
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add services/api/__init__.py
git commit -m "refactor(api): build_app accepts services dict for real-services injection"
```

---

### Task 21: Wire SnapshotDB-backed routes

**Files:**
- Modify: `services/worker_manager.py` (use SnapshotDB and item DB)
- Modify: `services/api/snapshots.py` (handlers already accept `db`)

- [ ] **Step 1: Update `WorkerManager.save_snapshot`**

```python
# services/worker_manager.py — add to __init__ args + body
from services.snapshot_db import SnapshotDB

class WorkerManager:
    def __init__(self, snapshot_db: SnapshotDB | None = None) -> None:
        self._sessions = {}
        self._db = snapshot_db

    def save_snapshot(self, pid: int, source: str) -> SaveSnapshotResult:
        if self._db is None:
            return SaveSnapshotResult(saved=False)
        sess = self._sessions.get(pid)
        if sess is None or not sess.name:
            return SaveSnapshotResult(saved=False)
        items_payload = (sess._latest_inv if source == "inventory" else sess._latest_wh)
        items = [{"item_id": i.item_id, "qty": i.quantity} for i in items_payload]
        sid = self._db.save(character=sess.name, source=source, items=items)
        return SaveSnapshotResult(saved=sid is not None, snapshot_id=sid)
```

- [ ] **Step 2: Verify `SnapshotDB.save` returns id-or-None already** (existing implementation)

```bash
uv run pytest tests/test_snapshot_db.py -v
```
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add services/worker_manager.py
git commit -m "feat: WorkerManager.save_snapshot writes via SnapshotDB"
```

---

### Task 22: Connect WorkerManager to WorldStream tick loop

**Files:**
- Modify: `services/worker_manager.py`

- [ ] **Step 1: Add tick loop**

```python
# services/worker_manager.py — add at bottom
import asyncio

from services.events import WorldStream


class WorkerManager:
    # ... existing code ...

    async def run_tick_loop(self, stream: WorldStream, interval: float = 1.5) -> None:
        """Coroutine: every `interval` seconds, publish current WorldSnapshot."""
        while True:
            try:
                await stream.publish(self.world_snapshot())
            except Exception as e:  # pragma: no cover
                print(f"[tick_loop] publish error: {e}")
            await asyncio.sleep(interval)
```

- [ ] **Step 2: Commit**

```bash
git add services/worker_manager.py
git commit -m "feat: WorkerManager.run_tick_loop publishes WorldSnapshot every 1.5s"
```

---

## Phase 7 — `app.py` and `bootstrap.py`

### Task 23: app.py — uvicorn thread + pywebview window

**Files:**
- Create: `app.py`
- Create: `webui/dist/.gitkeep` (so build dir is tracked)

- [ ] **Step 1: Write `app.py`**

```python
# app.py
"""Main app entry. Starts uvicorn in a daemon thread, then opens a
pywebview window pointed at the local server.
"""
from __future__ import annotations

import argparse
import asyncio
import socket
import sys
import threading
import time
from pathlib import Path

import uvicorn
import webview

from services.api import build_app
from services.snapshot_db import SnapshotDB
from services.worker_manager import WorkerManager
from services.auto_click import AutoClickManager

DEV_PORT_FILE = Path(".omc/.dev-port")


def _pick_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _build_services(dev: bool) -> dict:
    return {
        "worker_manager": WorkerManager(snapshot_db=SnapshotDB()),
        "snapshot_db": SnapshotDB(),
        "autoclick_manager": AutoClickManager(),
    }


async def _tick_runner(services: dict, stream) -> None:
    wm: WorkerManager = services["worker_manager"]
    await wm.run_tick_loop(stream)


def _serve(app, port: int) -> None:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    asyncio.set_event_loop(asyncio.new_event_loop())
    loop = asyncio.get_event_loop()
    # Run tick loop alongside server
    loop.create_task(_tick_runner(app.state.services, app.state.services["world_stream"]))
    loop.run_until_complete(server.serve())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dev", action="store_true", help="point window at Vite dev server")
    args = parser.parse_args()

    services = _build_services(args.dev)
    app = build_app(services=services)

    port = _pick_port()
    if args.dev:
        DEV_PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEV_PORT_FILE.write_text(str(port))

    # Mount static dist in production
    if not args.dev:
        from fastapi.staticfiles import StaticFiles
        dist = Path("webui/dist")
        if dist.exists():
            app.mount("/", StaticFiles(directory=str(dist), html=True), name="webui")

    server_thread = threading.Thread(target=_serve, args=(app, port), daemon=True)
    server_thread.start()

    # Wait briefly for the server to bind
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.2)
            s.close()
            break
        except OSError:
            time.sleep(0.05)

    target_url = f"http://127.0.0.1:5173" if args.dev else f"http://127.0.0.1:{port}"
    webview.create_window("御心鑒", target_url, width=1440, height=900)
    webview.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Smoke test (mock worker manager — no real game required for first boot)**

```bash
mkdir -p webui/dist
uv run python -c "import webui.dist" 2>/dev/null  # just touch
uv run app.py --dev &
# Manually: open http://localhost:5173 — Dashboard should display WorkerManager output (likely empty list with no game running)
# Kill the app
```

- [ ] **Step 3: Commit**

```bash
git add app.py webui/dist/.gitkeep
git commit -m "feat: app.py — uvicorn + pywebview entry point"
```

---

### Task 24: bootstrap.py + splash

**Files:**
- Create: `bootstrap.py`
- Create: `bootstrap_splash.html`

- [ ] **Step 1: Write splash HTML**

```html
<!-- bootstrap_splash.html -->
<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <title>御心鑒 — 啟動</title>
  <style>
    html, body { margin: 0; height: 100%; background: #14100e; color: #e8dcc8;
      font-family: "Noto Serif TC", serif; }
    body { display: flex; align-items: center; justify-content: center; flex-direction: column; gap: 16px; }
    .seal { width: 56px; height: 56px; background: #a02828; color: white; display: flex;
            align-items: center; justify-content: center; font-size: 28px; font-weight: 600; }
    .log { font-family: ui-monospace, monospace; font-size: 11px; color: #a89880; max-height: 100px;
           overflow-y: auto; width: 90%; padding: 8px; border: 1px solid #3a2f28; }
    .err { color: #c83838; }
    button { background: transparent; color: #e8dcc8; border: 1px solid #3a2f28; padding: 6px 16px;
             font-family: inherit; cursor: pointer; }
  </style>
</head>
<body>
  <div class="seal">御</div>
  <div id="status">準備更新…</div>
  <div class="log" id="log"></div>
  <div id="actions" style="display:none">
    <button onclick="window.pywebview.api.run_anyway()">繼續使用目前版本</button>
    <button onclick="window.pywebview.api.quit()">關閉</button>
  </div>
  <script>
    function setStatus(s) { document.getElementById('status').textContent = s; }
    function appendLog(line, isErr) {
      const el = document.getElementById('log');
      const span = document.createElement('div');
      if (isErr) span.className = 'err';
      span.textContent = line;
      el.appendChild(span);
      el.scrollTop = el.scrollHeight;
    }
    async function start() {
      try {
        const r = await window.pywebview.api.do_update();
        if (r.ok) {
          setStatus('啟動中…');
          await window.pywebview.api.launch_app();
        } else {
          setStatus('更新失敗');
          document.getElementById('actions').style.display = 'flex';
        }
      } catch (e) {
        setStatus('更新例外:' + e);
        document.getElementById('actions').style.display = 'flex';
      }
    }
    window.addEventListener('pywebviewready', start);
  </script>
</body>
</html>
```

- [ ] **Step 2: Write `bootstrap.py`**

```python
# bootstrap.py
"""Splash launcher: git pull + uv sync, then spawn app.py and exit."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import webview

REPO = Path(__file__).resolve().parent


class SplashApi:
    def __init__(self, window: webview.Window | None = None) -> None:
        self._window = window
        self._cancelled = False

    def _log(self, line: str, err: bool = False) -> None:
        if self._window is None:
            return
        line = line.replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ")
        self._window.evaluate_js(f"appendLog('{line}', {str(err).lower()})")

    def do_update(self) -> dict:
        try:
            self._log("git pull --ff-only ...")
            r = subprocess.run(
                ["git", "pull", "--ff-only"], cwd=REPO,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            self._log(r.stdout.strip() or "(empty)")
            if r.returncode != 0:
                self._log(r.stderr.strip(), err=True)
                return {"ok": False, "error": "git pull failed"}

            self._log("uv sync ...")
            r = subprocess.run(
                ["uv", "sync"], cwd=REPO,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            self._log(r.stdout.strip() or "(empty)")
            if r.returncode != 0:
                self._log(r.stderr.strip(), err=True)
                return {"ok": False, "error": "uv sync failed"}
            return {"ok": True}
        except Exception as e:
            self._log(str(e), err=True)
            return {"ok": False, "error": str(e)}

    def launch_app(self) -> None:
        subprocess.Popen([sys.executable, str(REPO / "app.py")], cwd=REPO)
        if self._window:
            self._window.destroy()

    def run_anyway(self) -> None:
        self.launch_app()

    def quit(self) -> None:
        if self._window:
            self._window.destroy()


def main() -> int:
    api = SplashApi()
    window = webview.create_window(
        "御心鑒 — 啟動",
        str(REPO / "bootstrap_splash.html"),
        js_api=api,
        width=460, height=260, frameless=False, resizable=False,
    )
    api._window = window
    webview.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Manual smoke test**

```bash
uv run bootstrap.py
# Expected: splash opens, runs git pull (will say "Already up to date"), uv sync, then spawns app.py
# Close the spawned app window when done
```

- [ ] **Step 4: Commit**

```bash
git add bootstrap.py bootstrap_splash.html
git commit -m "feat: bootstrap.py splash launcher with git pull + uv sync"
```

---

## Phase 8 — Cleanup

### Task 25: Delete old GUI

**Files:**
- Delete: entire `gui/` directory
- Delete: `gui_main.py`
- Delete: `launcher.py`
- Delete: `requirements.txt`
- Delete: Qt-coupled tests

- [ ] **Step 1: Verify no remaining references**

```bash
uv run python -c "import services.api, services.worker_manager, services.events"
```
Expected: imports succeed, no traceback.

- [ ] **Step 2: Delete directories and files**

```bash
git rm -r gui/
git rm gui_main.py launcher.py requirements.txt
```

- [ ] **Step 3: Delete Qt-coupled tests**

```bash
git rm tests/test_main_window.py tests/test_launcher_window.py tests/test_theme.py \
       tests/test_character_card.py tests/test_data_management_tab.py \
       tests/test_inventory_manager_tab.py tests/test_worker.py \
       tests/test_worker_filter.py tests/test_process_detector.py tests/test_config.py
```

(Keep `tests/test_reader_filter.py` and `tests/test_snapshot_db.py` — pure logic.)

- [ ] **Step 4: Run remaining test suite**

```bash
uv run pytest -v
```
Expected: PASS — every remaining test green.

- [ ] **Step 5: Commit**

```bash
git commit -m "chore: remove PySide6 GUI and Qt-coupled tests"
```

---

### Task 26: Type generation pipeline + production smoke test

**Files:**
- Modify: `webui/src/api/types.ts` (replace stub)

- [ ] **Step 1: Boot backend in dev mode**

```bash
uv run app.py --dev &
sleep 2
```

- [ ] **Step 2: Generate TS types**

```bash
cd webui
npm run gen-types
```
Expected: `webui/src/api/types.ts` overwritten with FastAPI-generated types.

- [ ] **Step 3: Build frontend**

```bash
npm run build
```
Expected: `webui/dist/index.html` and JS bundle produced; TypeScript compiles cleanly.

- [ ] **Step 4: Kill dev backend, then run production**

```bash
cd ..
# Kill the --dev process
uv run app.py
```
Expected: pywebview window opens, Dashboard renders, no errors in console.

- [ ] **Step 5: Commit generated types**

```bash
git add webui/src/api/types.ts
git commit -m "build: regenerate webui types from /openapi.json"
```

---

### Task 27: End-to-end manual verification

**Files:** none — verification only

- [ ] **Step 1: With at least one tthol game window open, launch via bootstrap**

```bash
uv run bootstrap.py
```

- [ ] **Step 2: Verify each path:**

- Dashboard shows the active game character with link dot, vitals bars, position
- WS push delivers updates (HP changes when you move/take damage in-game)
- Click character row → Detail page; switch between 根脈 / 行囊 / 輔助 / 行止 tabs
- 行囊 tab: click 掃描行囊 → items list populates
- 輔助 tab: 啟動 / 停止 toggles auto-click
- Treasury tab: summary numbers show
- Snapshots tab: previous snapshots listed; click to view detail
- Theme switch via Tweaks menu (if exposed) — colors update live
- Close app — process exits cleanly (no orphan uvicorn)

- [ ] **Step 3: Final commit if any docs/README updates emerged**

```bash
git status
# If only README.md or CLAUDE.md updates, add and commit
```

---

## Self-Review

Run this checklist after the plan is written:

**1. Spec coverage:**
- §1 Summary → Tasks 8 (scaffold), 23 (app.py)
- §3.1 Process model → Tasks 7 (WS), 23 (uvicorn thread)
- §3.2 Layout → all phases
- §4.1 Discovery/lifecycle → Task 4
- §4.2 Live data → Tasks 4, 7, 12
- §4.3 Snapshots/accounts → Tasks 5, 21
- §4.4 Auto-click → Tasks 6, 18
- §4.5 WS push → Task 7, Task 22 (tick loop)
- §5 Frontend pages → Tasks 11–15
- §6 Connection state → Task 17 (CharSession.link)
- §7 Bootstrap → Task 24
- §8 Build → Task 26
- §10 Tests → Tasks 2, 3, 7, 17, 19
- §11 Risks (port collision, WS reconnect, lifecycle) → Tasks 12 (WS reconnect), 23 (port pick / lifecycle)

**2. Placeholder scan:** the only `NotImplementedError` markers are in `worker.py::run` and `auto_click.py::_run` — both with explicit "port from gui/X.py" instructions naming the source lines. No "TBD" / "etc" / "similar to". OK.

**3. Type consistency:**
- `WorldSnapshot.chars: list[CharacterRow]` — used in Task 2 (definition), Task 4 (mock), Task 7 (WS), Task 12 (frontend hook). Consistent.
- `link: 'ok' | 'weak' | 'lost'` — consistent across Pydantic, frontend `LinkStatus`, `CharSession.link`.
- `AutoClickStatus` fields — consistent between definition, router, CharSession.
- `Item.source: 'inventory' | 'warehouse'` — consistent.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-03-tthol-ui-redesign.md`.** Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
