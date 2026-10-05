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


class PositionFrame(_Base):
    """One /ws/pos frame: positions that changed since the last frame, by pid."""

    pos: dict[int, Position]


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
    """One active status on a character.

    source "hook" (services.buff_tracker): `code` is the skill code (magic.id
    * 100 + level) or items.id that gave it, `name` / `level` come from that,
    and `expires_at` is when it ends (None when only the hook's `buffs` list
    showed it). source "memory": only the status `group` is known (HP+0x288
    buffs, HP+0x4C4 debuffs), so `name` is the group's representative name.
    kind "hero" is a hero transform (shown as transformed, not which hero).
    """

    group: int
    name: str
    kind: Literal["buff", "debuff", "hero"] = "buff"
    code: int | None = None
    level: int | None = None
    expires_at: float | None = None  # unix seconds
    source: Literal["hook", "memory"] = "memory"


class SkillInfo(_Base):
    """One learned skill (magic id + level), read from the CCharObject and
    described from the magic tables."""

    magic_id: int
    level: int
    name: str
    max_level: int  # highest learnable level (magic_learn)
    group: str  # clan code, or general / bonus / meridian
    group_label: str  # 火狐 / 神武 / 通用 · 生活 ...
    passive: bool  # 自動使用 skills, stat bonuses and meridians
    mp_cost: int = 0
    description: str = ""  # help text of the current level
    icon_url: str | None = None


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


class HookInfo(_Base):
    """A hook pipe this app is reading for the character (protocol from its hello)."""

    proto: int


class ChatMessage(_Base):
    seq: int
    ts: float  # epoch seconds
    # normal / whisper / party / family / area / shout, or the raw code as digits;
    # system = a client system line (hook packet 0xFD), name empty
    channel: str
    # The sender's own copy; for a whisper `name` is then the target.
    echo: bool
    own: bool  # sent by this character (family / shout carry no sender key: always False)
    name: str
    text: str


class ChatLog(_Base):
    connected: bool
    proto: int | None = None
    last_seq: int
    messages: list[ChatMessage] = []


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
    hook: HookInfo | None = None  # set only while a hook pipe is connected for this pid


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
    # Learned skills in ascending magic id order; None until first read.
    skills: list[SkillInfo] | None = None
    # Flat bonuses the skills' help text adds (體力上限, 真氣上限, 物攻 ...).
    skill_caps: list[ItemStat] = []
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


class NearbyEntity(_Base):
    """A live character object the client holds near the player (not the player itself)."""

    # follower: a player's hero / summon (npc row flagged is_monster, but it has an owner)
    kind: Literal["player", "follower", "monster", "npc"]
    handle: int  # client object handle; only meaningful inside this pid
    npc_id: int
    instance: int
    name: str | None = None
    level: int | None = None  # monsters / NPCs from the npc table
    hp_pct: int | None = None  # monsters only, 0..100
    stalling: bool = False  # players seated at a stall
    family: str | None = None  # players
    owner: str | None = None  # followers: the owning player's name
    # Map pixels (bottom-left origin, Minimap space) and the tile they fall in.
    px: int
    py: int
    x: int
    y: int
    distance: int | None = None  # tiles (Chebyshev) from the player; None before the first step


class Nearby(_Base):
    entities: list[NearbyEntity] = []


class StatSimExport(_Base):
    """TTHOL1 string for genbu's stat simulator, and the link that imports it."""

    code: str
    url: str


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


class WalkPoint(_Base):
    x: int
    y: int


class WalkPlan(_Base):
    """A click-to-walk plan in game coordinates (origin bottom-left, y up)."""

    start: WalkPoint | None = None  # tiles: where the walk starts (snapped)
    hops: list[WalkPoint]  # tiles, one click each, in order
    goal: WalkPoint | None = None  # tiles: the target after snapping to walkable ground
    reason: str | None = None  # why there is no plan, or why it stops short


class WalkRequest(_Base):
    x: int  # target tile, game coordinates
    y: int


class WalkStatus(_Base):
    state: Literal["idle", "walking", "done", "failed", "stopped"]
    goal: WalkPoint | None = None
    legs: int = 0  # clicks sent so far
    message: str | None = None  # why it failed or stopped


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


# ---- Market survey (市集調查) ----------------------------------------------

MarketMode = Literal["auto", "on", "off"]
PriceKind = Literal["silver", "coin", "negotiate"]


class MarketPrice(_Base):
    price: int  # raw stall price
    price_kind: PriceKind
    coins: int = 0  # 百萬官幣 count when price_kind == "coin"
    silver: int | None = None  # ask in silver; None for a haggle-only listing


class MarketStallRow(MarketPrice):
    """One listing in the stall being viewed: identical lots summed."""

    item_id: int
    count: int
    plus: int
    stats: list[ItemStat]  # the instance's own stats (inlays applied), labelled
    inlays: list[Inlay]
    status: Literal["new", "unchanged", "changed"]
    old_count: int | None = None
    suspect: bool = False  # bait price: under 1/10 of the item's value


class MarketGoneRow(MarketPrice):
    item_id: int


class MarketCurrentStall(_Base):
    seller: str
    sign: str
    # Game tiles: where the stall sits (None when its sprite was not found)
    # and where the character stood reading it.
    x: int | None = None
    y: int | None = None
    viewer_x: int | None = None
    viewer_y: int | None = None
    open: bool
    opened_at: float
    recorded_at: float
    settle_s: float
    rows: list[MarketStallRow]
    gone: list[MarketGoneRow]
    new: int
    unchanged: int
    changed: int


class MarketStallInView(_Base):
    seller: str
    sign: str
    x: int | None = None  # game tile the stall sits on
    y: int | None = None
    last_recorded: float | None = None


class MarketLogEntry(_Base):
    t: float
    kind: str
    seller: str | None = None
    text: str
    refresh: bool = False


class MarketSession(_Base):
    stalls: int
    new: int
    reads: int


class MarketStatus(_Base):
    mode: MarketMode
    active: bool
    # recording | not_market | off | no_character | waiting
    reason: str
    stage_id: int | None = None
    map_name: str | None = None
    stalls: list[MarketStallInView]
    current: MarketCurrentStall | None = None
    log: list[MarketLogEntry]
    session: MarketSession


class MarketModeRequest(_Base):
    mode: MarketMode


class MarketTotals(_Base):
    listings: int
    stalls: int
    negotiate: int
    visits: int
    last_seen: float | None = None


class MarketItemSummary(_Base):
    item_id: int
    name: str
    listings: int
    sellers: int
    negotiate: int
    flagged: int  # suspected bait or marked 不採計: left out of min / median / max
    min: int | None = None  # silver-value stats over priced listings
    median: int | None = None
    max: int | None = None
    last_seen: float


class MarketListing(MarketPrice):
    id: int
    suspect: bool  # bait price: under 1/10 of the item's value
    excluded: Literal["seller", "listing"] | None = None  # the player's 不採計 mark
    seller: str
    sign: str  # stall sign at the last read
    item_id: int
    count: int
    plus: int
    stats: list[ItemStat]  # the instance's own stats, inlays applied
    enhance: list[ItemStat] = []  # bonus of the +N level, on top of stats
    enhance_extra: list[ItemStat] = []  # milestone bonuses unlocked at or below +N
    inlays: list[Inlay]
    stage_id: int | None = None
    map: str
    # Game tiles at the last read: the stall (None when its sprite was not
    # found) and where the character stood.
    x: int | None = None
    y: int | None = None
    viewer_x: int | None = None
    viewer_y: int | None = None
    first_seen: float
    last_seen: float
    ended_at: float | None = None


class MarketExcludeRequest(_Base):
    excluded: bool


class MarketSellerExcludeRequest(_Base):
    seller: str
    excluded: bool


class MarketGotoRequest(_Base):
    pid: int  # the character to walk; must be on the listing's map


class MarketGotoResult(_Base):
    goal: WalkPoint | None = None  # the tile beside the stall it walks to
    walk: WalkStatus | None = None  # None when no walk was started
    # 'already there' when it stands beside the stall; 'viewer position' when
    # the stall's own tile is unknown and it walks to where it was seen from.
    note: str | None = None


# ---- Damage capture (/api/characters/{pid}/damage) ----


class DamageTarget(_Base):
    handle: int
    npc_id: int
    instance: int
    hp_pct: int | None = None
    debuffs: list[int] = []  # monster status groups (15 = 卸冑)
    # Sampled just before the hit (the post-hit read is cleared on a kill).
    debuffs_before: list[int] | None = None
    hp_pct_before: int | None = None
    before_age_ms: float | None = None
    name: str | None = None
    level: int | None = None
    defense: int | None = None  # npc.extra_def
    mdefense: int | None = None  # npc.magic_def
    # 'packet' = named by the result itself. Skill results name no target:
    # 'selected' = the selected target, 'recent_attack' = the latest normal
    # attack's target when nothing is selected.
    source: Literal["packet", "selected", "recent_attack"]


class DamageSelected(_Base):
    npc_id: int
    instance: int


class DamageSkill(_Base):
    cast_effect: int
    frame_key: list[int]
    magic_id: int | None = None
    level: int | None = None
    name: str | None = None
    method: Literal["learned", "ambiguous", "unique", "unknown"]
    candidates: list[int] = []
    candidate_names: list[str | None] = []


class DamageWeapon(_Base):
    slot: str
    item_id: int
    name: str | None = None
    plus: int
    zhenjie: int


class DamageBuff(_Base):
    group: int
    name: str | None = None


class DamageSnapshot(_Base):
    level: int
    sect: int
    sect_masks: list[int] = []  # raw CCharObject +0x4C/+0x54/+0x5C
    panel: dict[str, int]
    attrs: list[int]
    weapons: list[DamageWeapon]
    buffs: list[DamageBuff]


class DamageEvent(_Base):
    seq: int
    kind: Literal["hit", "snapshot"]
    t: float  # seconds of recording time (pauses excluded)
    poll_gap_ms: float | None = None  # the result arrived within this window before t
    path: Literal["normal", "skill"] | None = None
    rel: int | None = None
    segments: list[list[int]] = []  # [type, value]; 0 hit, 1 crit, 4 heal, others no damage
    damage: int | None = None
    target: DamageTarget | None = None
    selected: DamageSelected | None = None
    skill: DamageSkill | None = None
    # A skill result whose cast effect no learned skill has (likely a party
    # member's); left out of the summary.
    not_mine_suspect: bool = False
    # Status-only skill result: its damage result was most likely overwritten
    # before a poll could read it.
    damage_lost_suspect: bool = False
    snapshot: DamageSnapshot | None = None


class DamageSummary(_Base):
    total_damage: int
    hits: int
    segments: int
    misses: int
    miss_rate: float
    crit_rate: float
    debuffed_share: float
    combat_seconds: float
    elapsed_seconds: float
    combat_dps: float
    overall_dps: float
    gap_seconds: float


class DamageStatus(_Base):
    status: Literal["idle", "recording", "paused", "waiting"]
    note: str | None = None
    elapsed: float
    seq: int
    events: list[DamageEvent]
    summary: DamageSummary
    snapshot: DamageSnapshot | None = None


# ---- Guard / 常駐守護 ----------------------------------------------------


class GuardPotionRule(_Base):
    """Drink from a whitelist when HP / MP drops below a share of its max.

    Each whitelist is ordered: the first item the bag holds is used. An empty
    whitelist never drinks.
    """

    hp_pct: int = 70  # the 2026-10-04 tower runs used 0.70 / 0.30
    mp_pct: int = 30
    hp_items: list[int] = []
    mp_items: list[int] = []


class GuardCureRule(_Base):
    """Cure items to use as soon as the debuff they clear shows up.

    Each item clears exactly one status group (its extra_status). No order: a
    debuff is cured with whichever ticked item for it the bag holds.
    """

    items: list[int] = []


class GuardConfig(_Base):
    """Saved per character name (pid changes on every game restart)."""

    potion: GuardPotionRule = GuardPotionRule()
    cure: GuardCureRule = GuardCureRule()


class GuardLogEntry(_Base):
    id: int  # stable while the line is updated in place (bag confirmation)
    ts: float
    rule: Literal["potion", "cure", "guard"]
    text: str
    # sent: the hook accepted the command; confirmed: the bag count dropped for
    # every drink in the line (cure: the debuff went away); unconfirmed: some
    # never showed up in time (cure: still there after every try);
    # error / info: no command effect.
    phase: Literal["sent", "confirmed", "unconfirmed", "error", "info"]


class GuardVitals(_Base):
    hp: int
    hp_max: int
    mp: int
    mp_max: int


class GuardStatus(_Base):
    running: bool
    hook_cmd: bool  # a command pipe exists and its manifest (if read yet) has `use`
    character: str | None = None
    problem: str | None = None  # why the guard is idle or backing off, in user words
    drinks: int = 0
    cures: int = 0
    debuffs: list[str] = []  # debuffs on the character now, by name
    log: list[GuardLogEntry] = []
    config: GuardConfig = GuardConfig()
    vitals: GuardVitals | None = None  # current HP / MP, for the threshold sliders


class GuardStartResult(_Base):
    ok: bool
    reason: str | None = None


class PotionCandidate(_Base):
    """A potion the character holds, for the whitelist picker."""

    item_id: int
    name: str
    restores: Literal["hp", "mp", "both"]
    bag: int
    pet: int
    icon_url: str | None = None


class CureCandidate(_Base):
    """A cure item the character holds, for the 解狀態 picker."""

    item_id: int
    name: str
    group: int  # the status group it clears
    status: str  # that group's name, e.g. 中毒
    bag: int
    pet: int
    icon_url: str | None = None
