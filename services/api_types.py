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
    waigong: int  # 外功
    neili: int  # 內力
    genggu: int  # 根骨
    shenfa: int  # 身法
    jiqiao: int  # 技巧
    xuanxue: int  # 玄學
    wugong: int  # 物攻
    wugong_base: int  # 物攻(基礎)
    neijing: int  # 內勁
    fangyu: int  # 防禦
    huji: int  # 護勁
    mingzhong: int  # 命中
    shanduo: int  # 閃躲


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
