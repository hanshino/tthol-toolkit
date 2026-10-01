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
    stage_id: int | None = None  # stages.id; None when only the scan fallback found the map
    # Game coordinates: origin bottom-left, y grows upward (same as
    # map_placements.tile_x/tile_y). Tiles read -1 right after a map change
    # until the first step.
    x: int
    y: int
    # Map pixels of the tile centre, same space as Minimap; None until placed.
    px: int | None = None
    py: int | None = None


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
    source: Literal["inventory", "pet", "warehouse"]


class ItemStat(_Base):
    label: str
    value: int


class ItemMeta(_Base):
    """Static item data from tthol.sqlite, fetched by id via GET /api/items."""

    item_id: int
    name: str
    type_label: str = ""
    # Coarse group for the items page; see services.item_catalog.category_for.
    category: Literal["potion", "gear", "book", "pet", "event", "misc"]
    level: int = 0
    description: str = ""
    icon_url: str | None = None
    no_trade: bool = False
    no_store: bool = False
    no_drop: bool = False
    # Duration of the item's timed effect, 0 when it has none.
    effect_seconds: int = 0
    stats: list[ItemStat] = []


# ---- Buffs (active status effects) --------------------------------------


class BuffInfo(_Base):
    """One active status on a character. The game stores the status `group`
    (not the exact status id), so `name` is the representative status name
    for that group (e.g. 護體 / 血契 / 靈契 / 中毒). `kind` distinguishes the
    source array: positive self-buffs (HP+0x288) vs debuffs (HP+0x4C4)."""

    group: int
    name: str
    kind: Literal["buff", "debuff"] = "buff"


# ---- Equipment -------------------------------------------------------------


class Inlay(_Base):
    """真元 / 魂石 set into a piece of gear, grouped by kind."""

    item_id: int  # the 真元 / 魂石 item (for its icon and name)
    name: str
    count: int
    effect: str  # compounds.help first line, e.g. 防禦+27 or 閃躲+25~70


EquipSlotKey = Literal[
    "CAP",
    "BODY",
    "FOOT",
    "WING",
    "HORSE",
    "ORNAMENT_1",
    "ORNAMENT_2",
    "ORNAMENT_3",
    "HAND_L",
    "HAND_R",
]


class EquipSlot(_Base):
    """One worn-gear slot; item_id is None when the slot is empty."""

    slot: EquipSlotKey
    item_id: int | None = None
    name: str | None = None
    plus: int = 0  # enhancement level (+N); 0 when not enhanced
    # The item's own stats with 真元 inlays applied, read from the instance.
    stats: list[ItemStat] = []
    # Bonus of the current enhancement level (strong_formula), on top of `stats`.
    enhance: list[ItemStat] = []
    # Milestone bonuses unlocked at or below the current level (the tooltip's
    # "(+x)"), on top of `stats` and `enhance`.
    enhance_extra: list[ItemStat] = []
    inlays: list[Inlay] = []


# ---- Avatar (paper-doll head) --------------------------------------------


class DollLayer(_Base):
    """One sprite layer; (anchor_x, anchor_y) is the attach point in the image."""

    src: str  # GET /api/doll/... frame image
    width: int
    height: int
    anchor_x: int
    anchor_y: int


class Avatar(_Base):
    """Head portrait: layers bottom to top, all sharing one anchor point.

    `mirror` means the art is the opposite direction's frame: flip each layer
    left-right about its anchor (drawn left = origin - (width - anchor_x)).
    """

    mirror: bool
    layers: list[DollLayer]


# ---- Character views -----------------------------------------------------


class ErrorInfo(_Base):
    """Last error reported by a session's worker, surfaced on the character row.

    `code` is the stable identifier (services.diag_events.ErrorCode); `message`
    is prose and may be reworded, so consumers should switch on `code`.
    """

    ts: float
    message: str
    cat: str
    code: str | None = None


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
    avatar: Avatar | None = None  # None until read, or when the DB has no art for it
    last_error: ErrorInfo | None = None


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
    pet_inventory: list[Item] | None = None
    warehouse: list[Item] | None = None
    money: int | None = None
    # Worn gear in reader.EQUIP_SLOTS order; None until first read.
    equipment: list[EquipSlot] | None = None
    # Epoch seconds of the last successful read; None until the first one.
    inventory_updated_at: float | None = None
    warehouse_updated_at: float | None = None
    # True while the warehouse window is open in game (read live each poll).
    warehouse_open: bool = False
    last_error: ErrorInfo | None = None


# ---- Diagnostics ---------------------------------------------------------


class DiagEventModel(_Base):
    """Wire form of services.diag_events.DiagEvent."""

    v: int
    ts: float
    level: str
    logger: str
    pid: int | None = None
    char: str | None = None
    cat: str
    code: str | None = None
    message: str
    detail: dict | None = None


class DiagSummary(_Base):
    environment: dict
    sessions: list[dict]
    counts: dict[str, int]
    events_path: str | None = None
    verbose: bool


class VerboseState(_Base):
    verbose: bool


class ClientErrorRequest(_Base):
    message: str
    url: str | None = None
    stack: str | None = None
    component: str | None = None
    ua: str | None = None


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


# ---- Minimap -------------------------------------------------------------
# All coordinates are game map pixels: origin bottom-left, y grows upward, the
# same space as Position.px / Position.py. The image is drawn top-down, so a
# point sits at image row (height_px - y).


class MinimapExitOption(_Base):
    label: str | None = None  # dialogue-menu text; None for a direct warp
    stage_id: int
    name: str
    instance: bool  # enters a dungeon instance (A64)
    landed: bool  # the destination's landing point is known


class MinimapExit(_Base):
    """A walk-on exit (genbu getPortalExits): one cluster of a map_event tag's cells."""

    key: str  # "<event_tag>-<part>"
    event_tag: int
    part: int  # which zone of the tag (1-based); a tag can cover separate zones
    parts: int
    x: int  # centroid of the zone's walk-on cells
    y: int
    prompt: str | None = None  # dialogue-menu question, when the exit opens one
    options: list[MinimapExitOption]  # one for a direct warp, several for a menu


class MinimapNpc(_Base):
    npc_id: int
    name: str | None = None
    x: int
    y: int


class MinimapSpawn(_Base):
    npc_id: int
    name: str | None = None
    level: int | None = None
    x: int
    y: int


class MinimapRegion(_Base):
    """Bounding box of one walkable space (a map can pack a city plus interiors)."""

    x0: int  # left
    y0: int  # bottom
    x1: int  # right
    y1: int  # top


class Minimap(_Base):
    stage: StageInfo
    width_px: int  # full map size in pixels (tiles x tile_px)
    height_px: int
    tile_px: int
    image_url: str | None = None  # app-relative; None when the DB has no image for the map
    image_origin: str | None = None  # 'img' (official art) or 'composed' (rebuilt from tiles)
    exits: list[MinimapExit] = []
    npcs: list[MinimapNpc] = []
    spawns: list[MinimapSpawn] = []


# ---- Generic -------------------------------------------------------------


class OkResponse(_Base):
    ok: bool
    error: str | None = None
