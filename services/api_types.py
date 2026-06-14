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


class KeepActiveStatus(_Base):
    running: bool
    started_at: float | None = None
    runtime_seconds: int | None = None
    last_send_at: float | None = None


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


# ---- Buffs (active status effects) --------------------------------------


class BuffInfo(_Base):
    """One active status on a character. The game stores the status `group`
    (not the exact status id), so `name` is the representative status name
    for that group (e.g. 護體 / 血契 / 靈契 / 中毒). `kind` distinguishes the
    source array: positive self-buffs (HP+0x288) vs debuffs (HP+0x4C4)."""

    group: int
    name: str
    kind: Literal["buff", "debuff"] = "buff"


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
    buffs: list[BuffInfo] = []


class CharacterDetail(_Base):
    pid: int
    name: str
    sect: str
    link: Literal["ok", "weak", "lost"]
    stats: CharacterStats
    vitals: Vitals
    position: Position
    autoclick: AutoClickStatus
    buffs: list[BuffInfo] = []
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


class RelocateRequest(_Base):
    hp: int | None = None


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


# ---- Backup / restore (system-level) -------------------------------------


class BackupImportResult(_Base):
    snapshots_added: int
    snapshots_skipped: int
    accounts_added: int
    characters_assigned: int
    account_conflicts: int


# ---- Auto-click ----------------------------------------------------------


class AutoClickConfig(_Base):
    interval_ms: int  # gap between merchant clicks; ms granularity matches game's tick rate
    merchant_idx: int
    # "off"     — merchant clicks only (legacy behavior)
    # "collect" — after clicks_per_round merchant clicks, press 全部收下 then 全部銷毀
    # "destroy" — after clicks_per_round merchant clicks, press 全部銷毀
    mode: Literal["off", "collect", "destroy"] = "off"
    clicks_per_round: int = 1


class AutoClickTestRequest(_Base):
    merchant_idx: int


# ---- Treasury / 帳房 ---------------------------------------------------


class TreasurySummary(_Base):
    total_kinds: int
    total_qty: int
    on_person: int
    in_warehouse: int


class TreasuryHolder(_Base):
    character: str
    source: Literal["inventory", "warehouse"]
    account: str | None = None
    qty: int


class TreasuryItem(_Base):
    item_id: int
    name: str
    item_type: str = ""
    total_qty: int
    on_person: int
    in_warehouse: int
    holders: list[TreasuryHolder]


# ---- Map / 行止 ---------------------------------------------------------


class StageInfo(_Base):
    stage_id: int
    name: str


class MapMonster(_Base):
    npc_id: int
    name: str | None = None
    level: int | None = None
    hp: int | None = None
    count: int
    drop_money_min: int | None = None
    drop_money_max: int | None = None
    drop_exp: int | None = None


class SpawnPoint(_Base):
    npc_id: int
    name: str | None = None
    x: int
    y: int
    distance: int | None = None  # Chebyshev distance from player position, if available


class MapWarp(_Base):
    dst_stage_id: int
    dst_name: str | None = None
    dst_tag: int | None = None


class MapInfo(_Base):
    stage: StageInfo
    player_x: int | None = None
    player_y: int | None = None
    monsters: list[MapMonster] = []
    warps: list[MapWarp] = []
    nearby: list[SpawnPoint] = []


# ---- Generic -------------------------------------------------------------


class OkResponse(_Base):
    ok: bool
    error: str | None = None
