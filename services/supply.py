"""補給: one trip to a town NPC that sells the 道具處置 "sell" items, stores the
"store" items, and buys the 補貨清單 up to its targets.

A module runs it at a point of its own life cycle (神武玄天塔: before it heads
for 玄天之境, every run) by calling `SupplyManager.run` from its own worker
thread, the same way it calls `Navigator.go`: one hook command pipe per pid, so
no second thread drives the character. The 現在補給 button runs the same trip
on a thread of its own.

Order inside a trip: the 錢莊伙計 first when there is something to store or
the carried 銀兩 is outside gold_low..gold_high (store frees bag slots, and
silver comes out of the 錢莊 before the buying needs it; the warehouse window
holds both), then the shop: sell, then buy. Each hook action's `ok` only means
the packet went out, so every sale, store, buy, pet-bag put and 錢莊 move is
confirmed by a bag / pet bag / 銀兩 count moving; a buy that never shows up is
"no room / no money" and that row stops there. Without the hook's warehouse
commands (store / bank*), those steps are noted and skipped.

Where: the fixed points of services/supply_points.py. A family with a manor
buys at the nearest 家族道具商 only (its shop tier follows the manor and a
特貢令), and a list item that shop does not sell stops the trip before any
walk. Without a family, the town shopkeeper that sells the most of the list,
nearest by route cost; items it does not sell are skipped, or (extra_stop)
the trip goes on to the next shop.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field, replace

from reader import read_inventory, read_money, read_pet_inventory
from services import route_plan as rp
from services import shop_catalog
from services.supply_points import FAMILY_MENUS, POINTS, SupplyPoint
from services.api_types import (
    GuardStartResult,
    ItemRules,
    SupplyBuyable,
    SupplyConfig,
    SupplyItem,
    SupplyLoad,
    SupplyLogEntry,
    SupplyMoveRow,
    SupplyRow,
    SupplyStatus,
    SupplyStop,
    SupplyView,
)
from services.guard import load_pet_items, read_holdings, read_stage_id
from services.hook_cmd import NoReply, PipeBusy, PipeGone
from services.item_rules import SELL, STORE, ItemFact

log = logging.getLogger("tthol.supply")

SUPPLY_SECTION = "supply"
# What a trip cannot do without (walking comes from the navigator's own needs).
# closepanel: a shop or warehouse window is closed before walking on (user: a
# player never walks off with one open, 2026-10-07).
SUPPLY_COMMANDS = (
    "status",
    "near",
    "walk",
    "talk",
    "dialog",
    "option",
    "next",
    "shop",
    "closepanel",
)
PET_COMMANDS = ("pet", "petput")
STORE_COMMANDS = ("warehouse", "store")
BANK_COMMANDS = ("warehouse", "bank", "bankin", "bankout")
STORE_MAX = 255  # most one hook `store` moves
SUMMON_COMMANDS = ("petsummon", "petdismiss")
STACK = 200  # most a bag stack holds (the snapshots never show more): one buy at most this many
# Slots: the bag holds 40 (the fullest snapshot, 止戰詩園 2026-10-02), the pet bag 8 (user).
BAG_SLOTS = 40
PET_SLOTS = 8
MAX_STOPS = 3
MAX_ROUNDS = 60  # buys + puts for one row (a 9999 target is 50 stacks)
OPEN_WAIT = 20.0  # talk -> shop window
CONFIRM_WAIT = 3.0  # a sale / buy / put showing up in the bag
# The warehouse list fills a moment after the window opens (the server sends
# it then): read at once, a warehouse not opened since the game started read
# empty and a withdraw found nothing (live 2026-10-07, 晨曦破空). Read until two
# reads agree, no sooner than WAREHOUSE_SETTLE; an empty one waits out
# WAREHOUSE_WAIT before it counts as empty.
WAREHOUSE_SETTLE = 0.8
WAREHOUSE_WAIT = 4.0
POLL = 0.25
# A summoned pet takes a moment to come out; a put sent before it is out is
# dropped (2026-10-07: a put 1.3 s after petsummon never landed, the same put
# with the pet out landed within 0.5 s). Wait for it in the pet bar, then a beat.
SUMMON_WAIT = 6.0
SUMMON_SETTLE = 1.0
PUT_TRIES = 2  # an unconfirmed put is sent once more before the pet bag is given up
PIPE_RETRIES = 6
NPC_REACH = 4  # tiles: talk from this close (the game walks the rest)
TILE_PX = 40
TYPE_LABELS = {"POTION": "藥品", "RETURN_SCROLL": "捲軸"}
FAMILY_TIERS = {27: "初級", 28: "中級", 29: "高級", 63: "特貢"}
FAMILY_WAIT = 2.0  # for the 0x31 a `family` ask brings

Tile = tuple[int, int]


# ---- pure parts --------------------------------------------------------------


def sell_list(
    rules: ItemRules, bag: dict[int, int], facts: Callable[[int], ItemFact | None], action: str
) -> list[tuple[int, int, int, int]]:
    """(item, qty, keep, have) for every bag item 道具處置 marks `action` (sell / store)."""
    out = []
    for item_id, rule in sorted(rules.items.items()):
        if rule.action != action:
            continue
        have = bag.get(item_id, 0)
        qty = have - max(rule.keep, 0)
        if qty <= 0:
            continue
        fact = facts(item_id)
        allowed = fact is not None and (fact.can_sell if action == SELL else fact.can_store)
        if allowed:
            out.append((item_id, qty, rule.keep, have))
    return out


def store_rows(
    want: dict[int, int], bag: dict[int, int], facts: Callable[[int], ItemFact | None]
) -> list[tuple[int, int, int, int]]:
    """sell_list's rows for a host's own store list: what the bag holds of it and may store."""
    out = []
    for item_id, qty in sorted(want.items()):
        have = bag.get(item_id, 0)
        fact = facts(item_id)
        if min(qty, have) > 0 and fact is not None and fact.can_store:
            out.append((item_id, min(qty, have), 0, have))
    return out


def buy_needs(
    items: list[SupplyItem], bag: dict[int, int], pet: dict[int, int]
) -> list[tuple[int, int, int]]:
    """(item, for the bag, for the pet bag) still missing, in list order."""
    out = []
    seen: set[int] = set()
    for row in items:
        if row.item_id in seen:
            continue
        seen.add(row.item_id)
        b = max(row.bag - bag.get(row.item_id, 0), 0)
        p = max(row.pet - pet.get(row.item_id, 0), 0)
        if b or p:
            out.append((row.item_id, b, p))
    return out


def pet_move(bag_now: int, bag_target: int, pet_left: int) -> int:
    """How many to put in the pet bag now: what the bag holds over its target."""
    return max(min(pet_left, bag_now - bag_target), 0)


def affordable(gold: int | None, keep_gold: int, price: int | None, want: int) -> int:
    if price is None or price <= 0:
        return want
    if gold is None:
        return 0
    return max(min(want, (gold - keep_gold) // price), 0)


def bank_action(gold: int | None, cfg: SupplyConfig) -> tuple[str, int] | None:
    """("withdraw" / "deposit", amount) that brings 銀兩 back to gold_target."""
    if gold is None:
        return None
    if cfg.gold_low is not None and gold < cfg.gold_low and cfg.gold_target > gold:
        return "withdraw", cfg.gold_target - gold
    if cfg.gold_high is not None and gold > cfg.gold_high and gold > cfg.gold_target:
        return "deposit", gold - cfg.gold_target
    return None


def bank_text(action: tuple[str, int] | None) -> str | None:
    if action is None:
        return None
    verb = "從錢莊領" if action[0] == "withdraw" else "存進錢莊"
    return f"{verb} {action[1]:,}"


def _stacks(n: int) -> int:
    return -(-max(n, 0) // STACK)


def estimate_load(
    bag: list[tuple[int, int]],
    pet: list[tuple[int, int]],
    weight: int,
    weight_max: int,
    sells: list[tuple[int, int, int, int]],
    items: list[SupplyItem],
    weights: dict[int, int],
) -> SupplyLoad:
    """Bag weight and slots after selling and buying every row to its targets.

    Touched items end up packed in stacks of STACK; the rest keep their slots.
    The peak is the final weight plus the largest stack that waits in the bag
    on its way to the pet bag.
    """
    bag_n: dict[int, int] = {}
    bag_s: dict[int, int] = {}
    for i, q in bag:
        bag_n[i] = bag_n.get(i, 0) + max(q, 0)
        bag_s[i] = bag_s.get(i, 0) + 1
    pet_n: dict[int, int] = {}
    pet_s: dict[int, int] = {}
    for i, q in pet:
        pet_n[i] = pet_n.get(i, 0) + max(q, 0)
        pet_s[i] = pet_s.get(i, 0) + 1
    after = dict(bag_n)
    pet_after = dict(pet_n)
    w = weight
    for i, qty, _keep, _have in sells:
        after[i] = after.get(i, 0) - qty
        w -= qty * weights.get(i, 0)
    peak_extra = 0
    for row in items:
        have = after.get(row.item_id, 0)
        final = max(have, row.bag)
        w += (final - have) * weights.get(row.item_id, 0)
        after[row.item_id] = final
        pet_have = pet_after.get(row.item_id, 0)
        pet_add = max(row.pet - pet_have, 0)
        pet_after[row.item_id] = pet_have + pet_add
        if pet_add:
            peak_extra = max(peak_extra, min(pet_add, STACK) * weights.get(row.item_id, 0))
    touched_bag = {i for i, _q, _k, _h in sells} | {r.item_id for r in items}
    touched_pet = {r.item_id for r in items if r.pet}
    slots = len(bag)
    slots_after = slots + sum(_stacks(after.get(i, 0)) - bag_s.get(i, 0) for i in touched_bag)
    pet_slots = len(pet)
    pet_slots_after = pet_slots + sum(
        _stacks(pet_after.get(i, 0)) - pet_s.get(i, 0) for i in touched_pet
    )
    return SupplyLoad(
        weight=weight,
        weight_max=weight_max,
        weight_after=w,
        weight_peak=w + peak_extra,
        slots=slots,
        slots_after=slots_after,
        slots_max=BAG_SLOTS,
        pet_slots=pet_slots,
        pet_slots_after=pet_slots_after,
        pet_slots_max=PET_SLOTS,
    )


def read_gold(pm, hp_addr, _compat_mode) -> int | None:
    return read_money(pm, hp_addr)


def read_level(pm, hp_addr, _compat_mode) -> int:
    return pm.read_int(hp_addr - 36)


def read_load(pm, hp_addr, _compat_mode):
    """(bag stacks, pet stacks, weight, weight max) for WorkerManager.read_locked."""
    try:
        bag = read_inventory(pm, hp_addr)
    except ValueError:
        return None
    if bag is None:
        return None
    try:
        pet = read_pet_inventory(pm, hp_addr) or []
    except ValueError:
        pet = []
    return bag, pet, pm.read_int(hp_addr + 24), pm.read_int(hp_addr + 28)


def same_npc(key: dict | None, obj: dict) -> bool:
    """A `shop` / `dialog` npc key against a `near` object: id and instance."""
    if not key or key.get("id") != obj.get("id"):
        return False
    inst = key.get("instance", key.get("inst"))
    return inst is None or obj.get("inst") is None or inst == obj.get("inst")


def pick_stop(
    npcs: list,
    shops_of: Callable[[object], frozenset[int]],
    sold_by: Callable[[frozenset[int]], set[int]],
    wanted: set[int],
    cost: Callable[[object], float | None],
    skip: set[tuple[int, int]],
    any_shop: bool,
    in_town: Callable[[object], bool] = lambda _npc: False,
) -> tuple[object, set[int]] | None:
    """The shop selling the most of `wanted`; then one in the town we stand in
    (user, 2026-10-08: in 成都, the 成都道具商, not a horse to 曼陀羅城 that
    the planner reckons a little cheaper); then the cheapest to reach.

    `any_shop`: there is something to sell, so a shop selling none of `wanted`
    still serves. Reachability is checked best-first: one route search each.
    """
    scored = []
    for npc in npcs:
        if (npc.npc_id, npc.stage) in skip or not shops_of(npc):
            continue
        covered = sold_by(shops_of(npc)) & wanted
        if covered or any_shop:
            scored.append(((-len(covered), not in_town(npc)), npc, covered))
    if not scored:
        return None
    best: tuple[tuple[int, bool], float, object, set[int]] | None = None
    for rank, npc, covered in sorted(scored, key=lambda s: s[0]):
        if best is not None and rank > best[0]:
            break  # covers fewer, or out of town, than a reachable shop already found
        c = cost(npc)
        if c is None:
            continue
        if best is None or c < best[1]:
            best = (rank, c, npc, covered)
    return (best[2], best[3]) if best else None


# ---- a trip ------------------------------------------------------------------


@dataclass
class SupplyResult:
    ok: bool
    reason: str  # done / nothing / short / unsold / stopped / busy / no-hook / error
    detail: str = ""
    sold: int = 0
    bought: int = 0
    put: int = 0
    stored: int = 0
    withdrawn: int = 0  # a withdraw trip: items taken out
    left: int = 0  # a withdraw trip: wanted stacks still in the warehouse


@dataclass
class Market:
    """Where this character buys: the 家族道具商 (a family with a manor) or the
    town shopkeepers (no family)."""

    family: bool
    label: str
    points: list[SupplyPoint]
    script: rp._Script
    family_shop: int | None = None

    def shop_of(self, point: SupplyPoint) -> int | None:
        return self.family_shop if point.kind == "family" else point.shop


def unsold_items(market: Market, items: list[SupplyItem], sells: dict[int, dict[int, int]]):
    """List items the family shop does not sell (user: stop and say so)."""
    if not market.family or market.family_shop is None:
        return []
    stock = sells.get(market.family_shop, {})
    return [r.item_id for r in items if r.item_id not in stock]


class _Abort(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


@dataclass
class _Log:
    lines: list[SupplyLogEntry] = field(default_factory=list)
    next_id: int = 0


class _ManualRun:
    def __init__(self) -> None:
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None


class SupplyManager:
    def __init__(
        self,
        guard,
        read_locked: Callable[[int, Callable], object],
        character_name: Callable[[int], str | None],
        channel,
        store,
        navigator,
        hook_caps,
        facts: Callable[[int], ItemFact | None],
        catalog: Callable[[], shop_catalog.ShopCatalog] = shop_catalog.catalog,
        icon_url: Callable[[int], str | None] = lambda _i: None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        sleep: Callable[[threading.Event | None, float], bool] | None = None,
        pet_items: Callable[[], frozenset[int]] = load_pet_items,
        manor: Callable[[int], int | None] = lambda _pid: None,  # family manor sestage id
        ask_family: Callable[[int], dict | None] | None = None,  # hook `family` (0x31 -> manor)
        points: tuple[SupplyPoint, ...] = POINTS,
    ) -> None:
        self._manor = manor
        self._ask_family = ask_family
        self._points = points
        self._guard = guard
        self._read_locked = read_locked
        self._character_name = character_name
        self._channel = channel
        self._store = store
        self._navigator = navigator
        self._hook_caps = hook_caps
        self._facts = facts
        self._catalog = catalog
        self._icon_url = icon_url
        self._clock = clock
        self._wall = wall
        self._sleep = sleep or (lambda ev, s: ev.wait(s) if ev is not None else time.sleep(s))
        self._pet_items_loader = pet_items
        self._pet_items: frozenset[int] | None = None
        self._lock = threading.Lock()
        self._logs: dict[int, _Log] = {}
        self._steps: dict[int, str | None] = {}
        self._hosts_running: dict[int, str | None] = {}  # pid -> host while a trip runs
        self._ended: dict[int, str] = {}
        self._manual: dict[int, _ManualRun] = {}
        self._hosts: list[str] = []

    # -- settings / view -------------------------------------------------------

    def add_host(self, text: str) -> None:
        """A module that runs 補給 first (shown on the panel), e.g. "神武玄天塔 · 出發前"."""
        self._hosts.append(text)

    def config(self, name: str) -> SupplyConfig:
        return self._store.load_section(name, SUPPLY_SECTION, SupplyConfig)

    def save(self, pid: int, cfg: SupplyConfig) -> SupplyConfig | None:
        name = self._character_name(pid)
        if not name:
            return None
        self._store.save_section(name, SUPPLY_SECTION, cfg)
        return cfg

    def buyables(
        self, query: str = "", limit: int = 30, pid: int | None = None
    ) -> list[SupplyBuyable]:
        """Items to add; with `pid`, a family character sees its family shop's only."""
        q = query.strip()
        cat = self._catalog()
        items = list(cat.items.values())
        if pid is not None:
            held = self._holdings(pid)
            market = self.market(pid, held[0] if held else {})
            if market.family and market.family_shop is not None:
                stock = cat.sells.get(market.family_shop, {})
                items = [replace(b, price=stock[b.item_id]) for b in items if b.item_id in stock]
        hits = [b for b in items if q in b.name] if q else items
        hits.sort(key=lambda b: (not b.name.startswith(q), -b.shops, b.price, b.item_id))
        return [
            SupplyBuyable(
                item_id=b.item_id,
                name=b.name,
                type_label=TYPE_LABELS.get(b.type_name),
                price=b.price,
                shops=b.shops,
                icon_url=self._icon_url(b.item_id),
            )
            for b in hits[:limit]
        ]

    def view(self, pid: int) -> SupplyView:
        name = self._character_name(pid)
        status = self.status(pid)
        if not name:
            return SupplyView(character=None, status=status, hosts=list(self._hosts))
        cfg = self.config(name)
        held = self._holdings(pid)
        bag, pet = held if held else ({}, {})
        cat = self._catalog()
        market = self.market(pid, bag)
        unsold = set(unsold_items(market, cfg.items, cat.sells))
        rows = []
        needs = {i: (b, p) for i, b, p in buy_needs(cfg.items, bag, pet)}
        for row in cfg.items:
            buy = cat.items.get(row.item_id)
            b, p = needs.get(row.item_id, (0, 0))
            rows.append(
                SupplyRow(
                    item_id=row.item_id,
                    name=buy.name if buy else self._name(row.item_id),
                    icon_url=self._icon_url(row.item_id),
                    type_label=TYPE_LABELS.get(buy.type_name) if buy else None,
                    price=buy.price if buy else None,
                    shops=buy.shops if buy else 0,
                    bag=bag.get(row.item_id, 0),
                    pet=pet.get(row.item_id, 0),
                    need=b + p,
                    unsold=row.item_id in unsold,
                )
            )
        rules = self._store.load_items(name)
        sells = [self._move_row(*s) for s in sell_list(rules, bag, self._facts, SELL)]
        stores = [self._move_row(*s) for s in sell_list(rules, bag, self._facts, STORE)]
        caps = self._caps(pid)
        gold = self._gold(pid)
        store_ok = caps is not None and all(c in caps for c in STORE_COMMANDS)
        bank_ok = caps is not None and all(c in caps for c in BANK_COMMANDS)
        at_warehouse = [f"存 {r.name} ×{r.qty}" for r in stores] if store_ok else []
        bank = bank_text(bank_action(gold, cfg))
        if bank_ok and bank:
            at_warehouse.append(bank)
        return SupplyView(
            character=name,
            config=cfg,
            rows=rows,
            sells=sells,
            stores=stores,
            store_supported=store_ok,
            bank_supported=bank_ok,
            bank=bank,
            load=self._load(pid, rules, cfg),
            hook_ready=caps is not None and all(c in caps for c in SUPPLY_COMMANDS),
            gold=gold,
            plan=self._preview(pid, market, cfg, bag, pet, bool(sells), at_warehouse),
            merchant=market.label,
            family=market.family,
            hosts=list(self._hosts),
            status=status,
        )

    def _load(self, pid: int, rules: ItemRules, cfg: SupplyConfig) -> SupplyLoad | None:
        try:
            got = self._read_locked(pid, read_load)
        except Exception:
            return None
        if not isinstance(got, tuple):
            return None
        bag, pet, weight, weight_max = got
        if not 0 <= weight <= weight_max:
            return None
        counts = {}
        for i, q in bag:
            counts[i] = counts.get(i, 0) + max(q, 0)
        sells = sell_list(rules, counts, self._facts, SELL)
        return estimate_load(
            bag, pet, weight, weight_max, sells, cfg.items, shop_catalog.item_weights()
        )

    def _move_row(self, item_id: int, qty: int, keep: int, have: int) -> SupplyMoveRow:
        return SupplyMoveRow(
            item_id=item_id,
            name=self._name(item_id),
            icon_url=self._icon_url(item_id),
            have=have,
            keep=keep,
            qty=qty,
        )

    def _preview(
        self,
        pid: int,
        market: Market,
        cfg: SupplyConfig,
        bag: dict[int, int],
        pet: dict[int, int],
        sells: bool,
        at_warehouse: list[str],
    ) -> list[SupplyStop]:
        needs = buy_needs(cfg.items, bag, pet)
        if not needs and not sells and not at_warehouse:
            return []
        try:
            stage = self._read_locked(pid, read_stage_id)
        except Exception:
            stage = None
        if not stage:
            return []
        graph = rp.cached_graph(market.script.level, market.script.manor)
        here = stage[0]
        out: list[SupplyStop] = []
        wanted = {i: b + p for i, b, p in needs}
        skip: set[tuple[int, int]] = set()
        first = bool(needs or sells)
        shops: list[SupplyPoint] = []
        while first or (cfg.extra_stop and wanted and shops and len(out) < MAX_STOPS):
            pick = self._pick(market, graph, here, set(wanted), skip, sells and first)
            if pick is None:
                break
            npc, covered = pick
            shops.append(npc)
            skip.add((npc.npc_id, npc.stage))
            buys = [f"{self._name(i)} ×{wanted[i]}" for i in wanted if i in covered]
            for i in covered:
                wanted.pop(i, None)
            out.append(
                SupplyStop(
                    npc=npc.name,
                    stage_name=npc.stage_name,
                    tile=npc.tile,
                    buys=buys,
                    missing=[self._name(i) for i in wanted],
                )
            )
            here, first = npc.stage, False
            if not covered:
                break
        if at_warehouse:
            keeper = self._pick_warehouse(graph, stage[0], shops[0] if shops else None)
            if keeper is not None:
                stop = SupplyStop(
                    npc=keeper.name,
                    stage_name=keeper.stage_name,
                    tile=keeper.tile,
                    actions=at_warehouse,
                )
                out.insert(0, stop)
        return out

    # -- manual runs -------------------------------------------------------------

    def start(self, pid: int) -> GuardStartResult:
        name = self._character_name(pid)
        if not name:
            return GuardStartResult(ok=False, reason="角色還沒定位")
        with self._lock:
            if pid in self._hosts_running:
                return GuardStartResult(ok=False, reason="補給已經在跑")
            self._hosts_running[pid] = None  # claimed here: a module's trip waits its turn
            run = _ManualRun()
            self._manual[pid] = run

        def body() -> None:
            self.run(pid, run.stop, host=None, claimed=True)

        run.thread = threading.Thread(target=body, daemon=True, name=f"supply-{pid}")
        run.thread.start()
        return GuardStartResult(ok=True)

    def stop(self, pid: int) -> None:
        with self._lock:
            run = self._manual.get(pid)
        if run is not None:
            run.stop.set()

    def forget(self, pid: int) -> None:
        self.stop(pid)
        with self._lock:
            self._logs.pop(pid, None)
            self._ended.pop(pid, None)
            self._steps.pop(pid, None)

    def running(self, pid: int) -> bool:
        with self._lock:
            return pid in self._hosts_running

    def status(self, pid: int) -> SupplyStatus:
        with self._lock:
            lg = self._logs.get(pid)
            return SupplyStatus(
                running=pid in self._hosts_running,
                step=self._steps.get(pid),
                host=self._hosts_running.get(pid),
                ended=self._ended.get(pid),
                log=list(lg.lines[-60:]) if lg else [],
            )

    # -- the trip ----------------------------------------------------------------

    def run(
        self,
        pid: int,
        stop: threading.Event | None,
        note: Callable[[str], None] | None = None,
        host: str | None = None,
        claimed: bool = False,
        store_only: dict[int, int] | None = None,
        withdraw: tuple[frozenset[int], int] | None = None,
        seated: bool = False,
    ) -> SupplyResult:
        """One trip. `note` gets the step text (a host module shows it as its own step).

        `store_only` (item -> qty): a trip to the warehouse that stores these and
        nothing else (no 道具處置 sells / stores, no buying, no 錢莊), for a host
        tidying what it brought in (寶箱整理).

        `withdraw` (items, free bag slots): a trip to the warehouse that takes
        out whole stacks of these, one per free slot, and nothing else (分身交貨
        hands them on).

        `seated`: open the warehouse sitting where the character is when the
        錢莊伙計 is on screen, instead of walking up to him."""
        with self._lock:
            if pid in self._hosts_running and not claimed:
                return SupplyResult(False, "busy", "另一趟補給正在跑")
            self._hosts_running[pid] = host
            self._logs[pid] = _Log()
            self._ended.pop(pid, None)
        trip = _Trip(
            self,
            pid,
            stop or threading.Event(),
            note or (lambda _t: None),
            store_only,
            withdraw,
            seated,
        )
        try:
            with ExitStack() as hold:
                hold.enter_context(self._guard.quiet(pid))
                hold.enter_context(self._guard.hold_pets(pid))
                result = trip.go()
        except _Abort as a:
            result = SupplyResult(
                False, a.reason, a.detail, trip.sold, trip.bought, trip.put, trip.stored
            )
        except Exception:
            log.exception("supply failed pid=%d", pid, extra={"cat": "supply"})
            result = SupplyResult(
                False,
                "error",
                "補給出錯（詳見診斷紀錄）",
                trip.sold,
                trip.bought,
                trip.put,
                trip.stored,
            )
        finally:
            trip.dismiss()
        self._line(pid, "info" if result.ok else "error", f"補給結束：{result.detail}")
        with self._lock:
            self._hosts_running.pop(pid, None)
            self._steps.pop(pid, None)
            self._ended[pid] = result.detail
            self._manual.pop(pid, None)
        log.info("supply pid=%d %s: %s", pid, result.reason, result.detail, extra={"cat": "supply"})
        return result

    # -- plumbing ------------------------------------------------------------------

    def _line(self, pid: int, phase: str, text: str) -> SupplyLogEntry:
        with self._lock:
            lg = self._logs.setdefault(pid, _Log())
            lg.next_id += 1
            entry = SupplyLogEntry(id=lg.next_id, ts=self._wall(), phase=phase, text=text)
            lg.lines.append(entry)
            return entry

    def _set_step(self, pid: int, text: str | None) -> None:
        with self._lock:
            self._steps[pid] = text

    def _holdings(self, pid: int) -> tuple[dict[int, int], dict[int, int]] | None:
        try:
            held = self._read_locked(pid, read_holdings)
        except Exception:
            return None
        return held if isinstance(held, tuple) else None

    def _gold(self, pid: int) -> int | None:
        try:
            gold = self._read_locked(pid, read_gold)
        except Exception:
            return None
        return gold if isinstance(gold, int) and gold >= 0 else None

    def _level(self, pid: int) -> int:
        try:
            level = self._read_locked(pid, read_level)
        except Exception:
            level = None
        return level if isinstance(level, int) and 0 < level < 1000 else 1

    def _caps(self, pid: int) -> frozenset[str] | None:
        if self._hook_caps is None:
            return None
        try:
            return self._hook_caps.get(pid, 60.0)
        except (PipeGone, PipeBusy, NoReply):
            return self._hook_caps.cached(pid)

    def _name(self, item_id: int) -> str:
        buy = self._catalog().items.get(item_id)
        if buy:
            return buy.name
        fact = self._facts(item_id)
        return fact.name if fact else f"#{item_id}"

    def _pets(self) -> frozenset[int]:
        if self._pet_items is None:
            self._pet_items = self._pet_items_loader()
        return self._pet_items

    def market(self, pid: int, bag: dict[int, int], ask: bool = False) -> Market:
        """Family first (user, 2026-10-07): a character whose family has a manor
        buys only at the 家族道具商, whose shop follows the manor and a 特貢令
        in the bag. No family: the town shopkeepers."""
        level = self._level(pid)
        manor = self._manor(pid)
        if manor is None and ask:
            manor = self._learn_manor(pid)
        script = rp._Script(rp._tables(), level, manor, bag)
        if manor is not None:
            shops = [s for m in reversed(FAMILY_MENUS) for s in script.shops_from_msg(m)]
            if shops:
                shop = shops[0]  # 特貢 menu first: without a 特貢令 it falls back to the plain one
                return Market(
                    family=True,
                    label=f"家族商人・{FAMILY_TIERS.get(shop, '')}商店",
                    points=[p for p in self._points if p.kind == "family"],
                    script=script,
                    family_shop=shop,
                )
        return Market(
            family=False,
            label="一般商人（沒有家族莊園）",
            points=[p for p in self._points if p.kind == "general"],
            script=script,
        )

    def _learn_manor(self, pid: int) -> int | None:
        if self._ask_family is None:
            return None
        try:
            reply = self._ask_family(pid)
        except Exception:
            return None
        if not reply or not reply.get("ok"):
            return None
        end = self._clock() + FAMILY_WAIT
        while self._clock() < end:
            manor = self._manor(pid)
            if manor is not None:
                return manor
            if self._sleep(None, 0.1):
                break
        return None

    def _pick_warehouse(self, graph, here: int, shop: SupplyPoint | None) -> SupplyPoint | None:
        """The 錢莊伙計 cheapest to reach from `here` and then on to `shop`."""
        best: tuple[float, SupplyPoint] | None = None
        for keeper in (p for p in self._points if p.kind == "warehouse"):
            first = rp.plan(graph, here, keeper.stage, None, keeper.tile)
            if first is None:
                continue
            cost = first.cost
            if shop is not None and shop.stage != keeper.stage:
                then = rp.plan(graph, keeper.stage, shop.stage, keeper.tile, shop.tile)
                if then is None:
                    continue
                cost += then.cost
            if best is None or cost < best[0]:
                best = (cost, keeper)
        return best[1] if best else None

    def _pick(self, market: Market, graph, here: int, wanted, skip, any_shop):
        cat = self._catalog()

        def cost(point: SupplyPoint) -> float | None:
            route = rp.plan(graph, here, point.stage, None, point.tile)
            return None if route is None else route.cost

        def shops_of(point: SupplyPoint) -> frozenset[int]:
            shop = market.shop_of(point)
            return frozenset() if shop is None else frozenset({shop})

        groups = rp.stage_groups()
        town = groups.get(here)

        def in_town(point: SupplyPoint) -> bool:
            return town is not None and groups.get(point.stage) == town

        return pick_stop(
            market.points, shops_of, cat.sold_by, wanted, cost, skip, any_shop, in_town
        )


class _Trip:
    def __init__(
        self,
        mgr: SupplyManager,
        pid: int,
        stop: threading.Event,
        note,
        store_only: dict[int, int] | None = None,
        withdraw: tuple[frozenset[int], int] | None = None,
        seated: bool = False,
    ) -> None:
        self.m = mgr
        self.pid = pid
        self.stop = stop
        self.note = note
        self.store_only = store_only
        self.withdraw = withdraw
        self.withdrawn = 0
        self.seated = seated
        self.sat = False  # this trip sat down
        self.sold = 0
        self.bought = 0
        self.put = 0
        self.stored = 0
        self.banked = 0  # + withdrawn / - deposited
        self.summoned: int | None = None
        self.pet_out = False  # a pet is out to put items in its bag
        self.caps: frozenset[str] = frozenset()

    # -- plumbing ----------------------------------------------------------------

    def step(self, text: str) -> None:
        self.m._set_step(self.pid, text)
        self.note(text)

    def line(self, phase: str, text: str) -> SupplyLogEntry:
        return self.m._line(self.pid, phase, text)

    def wait(self, secs: float) -> None:
        if self.m._sleep(self.stop, secs):
            raise _Abort("stopped", "已停止")

    def cmd(self, line: str) -> dict:
        for _ in range(PIPE_RETRIES):
            if self.stop.is_set():
                raise _Abort("stopped", "已停止")
            try:
                return self.m._channel.send(self.pid, line)
            except PipeGone:
                self.wait(1.0)
            except (PipeBusy, NoReply):
                self.wait(0.3)
        raise _Abort("no-hook", "找不到 hook 指令通道（遊戲關了，或 hook 沒有注入）")

    def held(self) -> tuple[dict[int, int], dict[int, int]]:
        for _ in range(8):
            h = self.m._holdings(self.pid)
            if h is not None:
                return h
            self.wait(0.3)
        raise _Abort("error", "讀不到背包")

    def confirm(self, check: Callable[[dict[int, int], dict[int, int]], bool]) -> bool:
        end = self.m._clock() + CONFIRM_WAIT
        while True:
            bag, pet = self.held()
            if check(bag, pet):
                return True
            if self.m._clock() >= end:
                return False
            self.wait(POLL)

    def name(self, item_id: int) -> str:
        return self.m._name(item_id)

    # -- the trip ----------------------------------------------------------------

    def go(self) -> SupplyResult:
        name = self.m._character_name(self.pid)
        if not name:
            raise _Abort("error", "角色還沒定位")
        cfg = self.m.config(name)
        rules = self.m._store.load_items(name)
        bag, pet = self.held()
        if self.withdraw is not None:
            return self.withdraw_trip(bag)
        if self.store_only is not None:
            sells, needs, bank = [], [], None
            stores = store_rows(self.store_only, bag, self.m._facts)
        else:
            sells = sell_list(rules, bag, self.m._facts, SELL)
            stores = sell_list(rules, bag, self.m._facts, STORE)
            needs = buy_needs(cfg.items, bag, pet)
            bank = bank_action(self.m._gold(self.pid), cfg)
        if not sells and not needs and not stores and bank is None:
            return SupplyResult(True, "nothing", "沒有要賣、要存或要買的東西")
        caps = self.m._caps(self.pid) or frozenset()
        self.caps = caps
        if stores and not all(c in caps for c in STORE_COMMANDS):
            self.note_skip_store(stores)
            stores = []
        if bank is not None and not all(c in caps for c in BANK_COMMANDS):
            self.line("info", f"錢莊跳過：hook 還沒有錢莊指令（要{bank_text(bank)}）")
            bank = None
        if not sells and not needs and not stores and bank is None:
            return SupplyResult(
                True, "done", self.summary([]), self.sold, self.bought, self.put, self.stored
            )
        if not caps:
            raise _Abort("no-hook", "讀不到 hook 的指令清單（舊版 hook，或還在登入）")
        missing = [c for c in SUPPLY_COMMANDS if c not in caps]
        if needs and "buy" not in caps:
            missing.append("buy")
        if sells and "sell" not in caps:
            missing.append("sell")
        if missing:
            raise _Abort("no-hook", f"這個 hook 缺少補給要用的指令：{'、'.join(missing)}")
        if self.m._navigator is None:
            raise _Abort("error", "沒有導航模組")
        self.close_windows()  # one the user (or a last trip) left open

        market = self.m.market(self.pid, bag, ask=True)
        if needs:
            unsold = unsold_items(market, cfg.items, self.m._catalog().sells)
            if unsold:
                names = "、".join(self.name(i) for i in unsold)
                raise _Abort(
                    "unsold",
                    f"{market.label}沒賣：{names}（從補貨清單移除，或換成家族商人有賣的）",
                )
        graph = rp.cached_graph(market.script.level, market.script.manor)
        if stores or bank is not None:
            # The warehouse before the shop: stored items free bag slots, and
            # 錢莊 silver comes out before the buying needs it.
            self.warehouse_stop(market, graph, stores, bank, bool(sells or needs))
        if not sells and not needs:
            self.close_windows()
            return SupplyResult(
                True, "done", self.summary([]), self.sold, self.bought, self.put, self.stored
            )
        self.line("info", f"在{market.label}補給")
        graph = rp.cached_graph(market.script.level, market.script.manor)
        wanted = {i for i, _b, _p in needs}
        pet_checked = False
        visited: set[tuple[int, int]] = set()
        short: list[str] = []
        for n in range(MAX_STOPS):
            try:
                stage = self.m._read_locked(self.pid, read_stage_id)
            except Exception:
                stage = None
            here = stage[0] if stage else None
            if here is None:
                raise _Abort("error", "讀不到目前地圖")
            pick = self.m._pick(market, graph, here, wanted, visited, bool(sells))
            if pick is None:
                if n == 0:
                    raise _Abort("error", f"找不到走得到、又有賣清單道具的{market.label}")
                break
            npc, covered = pick
            visited.add((npc.npc_id, npc.stage))
            self.visit(npc, market)
            if not pet_checked:
                # At the shop, not before the walk: a summoned pet would trail
                # the character across the maps.
                pet_checked = True
                if not self.pet_ready(needs):
                    needs = [(i, b, 0) for i, b, _p in needs if b]
            if sells:
                self.sell_all(sells)
                sells = []
            rest = [(i, b, p) for i, b, p in needs if i in covered]
            short += self.buy_all(rest, cfg)
            self.close_windows()
            wanted -= covered
            needs = [(i, b, p) for i, b, p in needs if i not in covered]
            if not needs:
                break
            names = "、".join(self.name(i) for i, _b, _p in needs)
            if not cfg.extra_stop:
                self.line("info", f"{npc.name}沒賣：{names}（照設定跳過）")
                short += [self.name(i) for i, _b, _p in needs]
                break
            self.line("info", f"{npc.name}沒賣：{names}，再找下一間")
        else:
            short += [self.name(i) for i, _b, _p in needs]
        self.close_windows()
        detail = self.summary(short)
        if short and cfg.stop_when_short:
            return SupplyResult(False, "short", detail, self.sold, self.bought, self.put)
        return SupplyResult(True, "done", detail, self.sold, self.bought, self.put, self.stored)

    def open_windows(self) -> list[str]:
        out = []
        if self.cmd("shop").get("open"):
            out.append("商店")
        if "warehouse" in self.caps and self.cmd("warehouse").get("open"):
            out.append("倉庫")
        return out

    def close_windows(self) -> None:
        """Close the shop / warehouse window before walking on (or handing the
        character back to the module), and see it close."""
        left = self.open_windows()
        if not left:
            return
        self.step(f"關閉{'、'.join(left)}視窗")
        r = self.cmd("closepanel")
        if not r.get("ok") and r.get("error") == "no window open":
            # The hook's warehouse flag can stay set with no window on screen
            # (live 2026-10-07, 晨曦破空): closepanel's word wins.
            log.info(
                "closepanel: no window open, though %s read open", left, extra={"cat": "supply"}
            )
            return
        end = self.m._clock() + CONFIRM_WAIT
        while True:
            self.wait(POLL)
            left = self.open_windows()
            if not left:
                return
            if self.m._clock() >= end:
                detail = r.get("error") or "送出後視窗還開著"
                raise _Abort("error", f"{'、'.join(left)}視窗關不掉（{detail}），先停下不走")

    def summary(self, short: list[str]) -> str:
        parts = []
        if self.sold:
            parts.append(f"賣 {self.sold} 個")
        if self.bought:
            parts.append(f"買 {self.bought} 個")
        if self.put:
            parts.append(f"放進寵物背包 {self.put} 個")
        if self.stored:
            parts.append(f"存倉 {self.stored} 個")
        if self.banked:
            parts.append(
                bank_text(("withdraw" if self.banked > 0 else "deposit", abs(self.banked)))
            )
        text = "、".join(parts) or "沒有買賣"
        if short:
            text += f"；沒補齊：{'、'.join(dict.fromkeys(short))}"
        return text

    # -- store -------------------------------------------------------------------

    def note_skip_store(self, stores: list[tuple[int, int, int, int]]) -> None:
        names = "、".join(f"{self.name(i)} ×{q}" for i, q, _k, _h in stores)
        self.line("info", f"存倉跳過：hook 還沒有存倉指令（{names}）")

    # -- the warehouse -----------------------------------------------------------

    def warehouse_stop(self, market, graph, stores, bank, shop_next: bool) -> None:
        """Store items and settle the 錢莊 silver at the nearest 錢莊伙計 (on the
        way to the shop when one follows)."""
        try:
            stage = self.m._read_locked(self.pid, read_stage_id)
        except Exception:
            stage = None
        if not stage:
            raise _Abort("error", "讀不到目前地圖")
        shop = None
        if shop_next:
            wanted = {
                r.item_id for r in self.m.config(self.m._character_name(self.pid) or "").items
            }
            pick = self.m._pick(market, graph, stage[0], wanted, set(), True)
            shop = pick[0] if pick else None
        keeper = self.m._pick_warehouse(graph, stage[0], shop)
        if keeper is None:
            self.line("error", "找不到走得到的錢莊伙計，存倉和錢莊這次跳過")
            return
        self.reach_warehouse(keeper, market.script)
        self.store_all(stores)
        if bank is not None:
            self.settle_bank(bank)
        self.close_windows()
        self.stand()

    def reach_warehouse(self, keeper: SupplyPoint, script) -> None:
        """Open the 錢莊伙計's warehouse. A seated trip (分身交貨) first sits and
        talks from where it is: seated, any NPC on screen answers (user,
        2026-10-07); if that does not open it, it walks over as usual."""
        if self.seated and self.sit_and_open(keeper, script):
            return
        self.stand()
        self.go_to(keeper)
        self.open_warehouse(keeper, script)

    def sit_and_open(self, keeper: SupplyPoint, script) -> bool:
        if "sit" not in self.caps:
            return False
        try:
            stage = self.m._read_locked(self.pid, read_stage_id)
        except Exception:
            stage = None
        if not stage or stage[0] != keeper.stage or self.find(keeper.npc_id) is None:
            return False
        if not self.set_sitting(True):
            return False
        try:
            self.open_warehouse(keeper, script)
        except _Abort as a:
            if a.reason == "stopped":
                raise
            self.line("info", f"坐著叫不開{keeper.name}的倉庫，改走過去")
            return False
        return True

    def set_sitting(self, want: bool) -> bool:
        """Sit down / stand up (the hook's `sit` toggles) and see the pose change."""

        def sitting() -> bool:
            return self.cmd("status").get("pose") == "Sit"

        if sitting() == want:
            self.sat = self.sat or want
            return True
        self.step("坐下" if want else "站起來")
        self.cmd("sit")
        end = self.m._clock() + CONFIRM_WAIT
        while True:
            self.wait(POLL)
            if sitting() == want:
                self.sat = want
                return True
            if self.m._clock() >= end:
                return False

    def stand(self) -> None:
        """Up again if this trip sat down (the module goes on walking or trading)."""
        if self.sat:
            self.set_sitting(False)

    def open_warehouse(self, npc: SupplyPoint, script) -> None:
        self.step(f"和{npc.name}對話")
        obj = self.find(npc.npc_id)
        if obj is None:
            raise _Abort("error", f"附近找不到{npc.name}")
        self.cmd(f"talk {obj['h']}")
        end = self.m._clock() + OPEN_WAIT
        while self.m._clock() < end:
            self.wait(POLL)
            if self.cmd("warehouse").get("open"):
                self.line("info", f"打開{npc.name}的倉庫")
                return
            d = self.cmd("dialog")
            if not d.get("open") or not same_npc(d.get("npc"), obj):
                continue
            if d.get("waiting"):
                continue
            options = d.get("options") or []
            if options:
                pick = next(
                    (i for i, j in enumerate(options) if j and script.opens_warehouse(j)), None
                )
                if pick is None:
                    raise _Abort("error", f"{npc.name}的對話裡沒有開倉庫的選項")
                self.cmd(f"option {pick}")
                continue
            self.cmd("next")
        raise _Abort("error", f"{npc.name}的倉庫沒有打開")

    def store_all(self, stores: list[tuple[int, int, int, int]]) -> None:
        for item_id, qty, _keep, _have in stores:
            name = self.name(item_id)
            left = qty
            while left > 0:
                chunk = min(left, STORE_MAX)
                self.step(f"存 {name} ×{chunk}")
                bag, _pet = self.held()
                before = bag.get(item_id, 0)
                r = self.cmd(f"store {item_id} {chunk}")
                if not r.get("ok"):
                    self.line("error", f"存 {name} ×{chunk} 沒送出：{r.get('error') or '不明原因'}")
                    break
                entry = self.line("sent", f"存 {name} ×{chunk}")
                if not self.confirm(lambda b, _p: b.get(item_id, 0) <= before - chunk):
                    entry.phase = "unconfirmed"
                    entry.text += "（背包數量沒有減少，倉庫可能滿了）"
                    break
                entry.phase = "confirmed"
                self.stored += chunk
                left -= chunk

    def withdraw_trip(self, bag: dict[int, int]) -> SupplyResult:
        """To the nearest 錢莊伙計, take out the wanted stacks that fit, back."""
        wants, room = self.withdraw or (frozenset(), 0)
        if not wants or room <= 0:
            return SupplyResult(True, "nothing", "沒有要領的東西或背包沒空格")
        caps = self.m._caps(self.pid) or frozenset()
        self.caps = caps
        missing = [c for c in (*SUPPLY_COMMANDS, "warehouse", "withdraw") if c not in caps]
        if missing:
            raise _Abort("no-hook", f"這個 hook 缺少領倉要用的指令：{'、'.join(missing)}")
        if self.m._navigator is None:
            raise _Abort("error", "沒有導航模組")
        self.close_windows()
        market = self.m.market(self.pid, bag, ask=True)
        graph = rp.cached_graph(market.script.level, market.script.manor)
        try:
            stage = self.m._read_locked(self.pid, read_stage_id)
        except Exception:
            stage = None
        if not stage:
            raise _Abort("error", "讀不到目前地圖")
        keeper = self.m._pick_warehouse(graph, stage[0], None)
        if keeper is None:
            raise _Abort("error", "找不到走得到的錢莊伙計")
        self.reach_warehouse(keeper, market.script)
        items = self.warehouse_items()
        stacks = [(int(i["item"]), int(i["count"])) for i in items if int(i["item"]) in wants]
        self.line("info", f"倉庫裡 {len(items)} 堆，要領的 {len(stacks)} 堆")
        take = stacks[:room]
        left = len(stacks) - len(take)
        for item_id, count in take:
            if not self.withdraw_stack(item_id, count):
                left += 1
        self.close_windows()
        self.stand()
        detail = f"領出 {self.withdrawn} 個" if self.withdrawn else "倉庫裡沒有要領的東西"
        return SupplyResult(True, "done", detail, withdrawn=self.withdrawn, left=left)

    def warehouse_items(self) -> list[dict]:
        """The open warehouse's stacks, once the list has settled."""
        start = self.m._clock()
        last: list[tuple[int, int]] | None = None
        while True:
            items = self.cmd("warehouse").get("items") or []
            key = sorted((int(i["item"]), int(i["count"])) for i in items)
            waited = self.m._clock() - start
            if waited >= WAREHOUSE_WAIT:
                return items
            if key == last and waited >= WAREHOUSE_SETTLE and items:
                return items
            last = key
            self.wait(POLL)

    def withdraw_stack(self, item_id: int, count: int) -> bool:
        """One warehouse stack into the bag, STORE_MAX at a time."""
        name = self.name(item_id)
        left = count
        while left > 0:
            chunk = min(left, STORE_MAX)
            self.step(f"領 {name} ×{chunk}")
            bag, _pet = self.held()
            before = bag.get(item_id, 0)
            r = self.cmd(f"withdraw {item_id} {chunk}")
            if not r.get("ok"):
                self.line("error", f"領 {name} ×{chunk} 沒送出：{r.get('error') or '不明原因'}")
                return False
            entry = self.line("sent", f"領 {name} ×{chunk}")
            if not self.confirm(lambda b, _p: b.get(item_id, 0) >= before + chunk):
                entry.phase = "unconfirmed"
                entry.text += "（背包數量沒有增加，背包可能滿了）"
                return False
            entry.phase = "confirmed"
            self.withdrawn += chunk
            left -= chunk
        return True

    def settle_bank(self, bank: tuple[str, int]) -> None:
        verb, amount = bank
        if verb == "withdraw":
            balance = self.cmd("bank").get("balance")
            if isinstance(balance, int):
                amount = min(amount, balance)
            if amount <= 0:
                self.line("error", "錢莊裡沒有銀兩可以領")
                return
        text = bank_text((verb, amount))
        self.step(text)
        before = self.m._gold(self.pid)
        r = self.cmd(f"{'bankout' if verb == 'withdraw' else 'bankin'} {amount}")
        if not r.get("ok"):
            self.line("error", f"{text} 沒送出：{r.get('error') or '不明原因'}")
            return
        entry = self.line("sent", text)
        sign = 1 if verb == "withdraw" else -1
        end = self.m._clock() + CONFIRM_WAIT
        while True:
            gold = self.m._gold(self.pid)
            if before is not None and gold is not None and (gold - before) * sign >= amount:
                entry.phase = "confirmed"
                self.banked = sign * amount
                return
            if self.m._clock() >= end:
                entry.phase = "unconfirmed"
                entry.text += "（身上銀兩沒有變）"
                return
            self.wait(POLL)

    # -- moving --------------------------------------------------------------------

    def visit(self, npc: SupplyPoint, market: Market) -> None:
        self.go_to(npc)
        self.open_shop(npc, market)

    def go_to(self, npc: SupplyPoint) -> None:
        stage_name = npc.stage_name
        self.step(f"前往{stage_name}找{npc.name}")
        self.line("info", f"前往{stage_name}找{npc.name}（{npc.tile[0]}, {npc.tile[1]}）")
        result = self.m._navigator.go(self.pid, npc.stage, npc.tile, stop=self.stop, note=self.step)
        if result.reason == "stopped":
            raise _Abort("stopped", "已停止")
        if not result.ok:
            raise _Abort("error", f"走不到{npc.name}：{result.detail}")

    def find(self, npc_id: int) -> dict | None:
        st = self.cmd("status")
        objs = self.cmd("near").get("objects") or []
        me = next((o for o in objs if o.get("h") == st.get("self")), None)
        best = None
        for o in objs:
            if o.get("id") != npc_id:
                continue
            d = math.dist((me["x"], me["y"]), (o["x"], o["y"])) if me else 0
            if best is None or d < best[0]:
                best = (d, o)
        return best[1] if best else None

    def open_shop(self, npc: SupplyPoint, market: Market) -> None:
        self.step(f"和{npc.name}對話")
        obj = self.find(npc.npc_id)
        if obj is None:
            raise _Abort("error", f"附近找不到{npc.name}")
        script, want = market.script, market.shop_of(npc)
        before = self.cmd("shop")
        stale = (before.get("npc") or {}).get("id") if before.get("open") else None
        self.cmd(f"talk {obj['h']}")
        end = self.m._clock() + OPEN_WAIT
        while self.m._clock() < end:
            self.wait(POLL)
            shop = self.cmd("shop")
            # There is no close command: the last stop's (or the user's) shop
            # window may still be open. Only this NPC's counts.
            owner = (shop.get("npc") or {}).get("id")
            if shop.get("open") and same_npc(shop.get("npc"), obj):
                if shop.get("mode") == 3:
                    raise _Abort("error", f"{npc.name}的商店是這個 hook 不支援的種類")
                got = shop.get("shop_id")
                if want is not None and isinstance(got, int) and got and got != want:
                    self.line("info", f"打開{npc.name}的商店：{got} 號店（預期 {want} 號）")
                else:
                    self.line("info", f"打開{npc.name}的商店")
                return
            if shop.get("open") and owner != stale:
                stale = owner
                log.info(
                    "supply pid=%d shop open for npc %s, waiting for %d",
                    self.pid,
                    owner,
                    npc.npc_id,
                    extra={"cat": "supply"},
                )
            d = self.cmd("dialog")
            if not d.get("open") or not same_npc(d.get("npc"), obj):
                continue
            if d.get("waiting"):
                continue
            options = d.get("options") or []
            if options:
                # The option that opens this character's shop (the 家族道具商
                # has a plain and a 特貢 menu); any shop option as a fallback.
                leads = [(i, script.shops_from_msg(j)) for i, j in enumerate(options) if j]
                pick = next((i for i, shops in leads if want in shops), None)
                if pick is None:
                    pick = next((i for i, shops in leads if shops), None)
                if pick is None:
                    raise _Abort("error", f"{npc.name}的對話裡沒有開商店的選項")
                self.cmd(f"option {pick}")
                continue
            self.cmd("next")
        raise _Abort("error", f"{npc.name}的商店沒有打開")

    # -- sell / buy ------------------------------------------------------------------

    def sell_all(self, sells: list[tuple[int, int, int, int]]) -> None:
        for item_id, qty, _keep, _have in sells:
            name = self.name(item_id)
            self.step(f"賣 {name} ×{qty}")
            bag, _pet = self.held()
            before = bag.get(item_id, 0)
            r = self.cmd(f"sell {item_id} {qty}")
            if not r.get("ok"):
                self.line("error", f"賣 {name} ×{qty} 沒送出：{r.get('error') or '不明原因'}")
                continue
            entry = self.line("sent", f"賣 {name} ×{qty}")
            if self.confirm(lambda b, _p: b.get(item_id, 0) <= before - qty):
                entry.phase = "confirmed"
                self.sold += qty
            else:
                entry.phase = "unconfirmed"
                entry.text += "（背包數量沒有減少，商人可能不收）"

    def buy_all(self, needs: list[tuple[int, int, int]], cfg: SupplyConfig) -> list[str]:
        """Buy each row up to its targets; the rows not filled, by name."""
        cat = self.m._catalog()
        short: list[str] = []
        for item_id, _b, _p in needs:
            row = next((r for r in cfg.items if r.item_id == item_id), None)
            if row is None:
                continue
            if not self.buy_row(row, cat.items.get(item_id), cfg):
                short.append(self.name(item_id))
        return short

    def buy_row(self, row: SupplyItem, info: shop_catalog.Buyable | None, cfg) -> bool:
        """True when both targets are met."""
        item_id, name = row.item_id, self.name(row.item_id)
        price = info.price if info else None
        for _ in range(MAX_ROUNDS):
            bag, pet = self.held()
            have_bag, have_pet = bag.get(item_id, 0), pet.get(item_id, 0)
            pet_left = max(row.pet - have_pet, 0) if self.can_put() else 0
            move = pet_move(have_bag, row.bag, pet_left)
            if move:
                if not self.put_pet(item_id, move):
                    return False
                continue
            want = max(row.bag - have_bag, 0) + pet_left
            if want <= 0:
                return True
            qty = affordable(self.m._gold(self.pid), cfg.keep_gold, price, min(want, STACK))
            if qty <= 0:
                self.line("error", f"{name}：銀兩不夠（至少要留 {cfg.keep_gold:,}）")
                return False
            self.step(f"買 {name} ×{qty}")
            r = self.cmd(f"buy {item_id} {qty}")
            if not r.get("ok"):
                self.line("error", f"買 {name} ×{qty} 沒送出：{r.get('error') or '不明原因'}")
                return False
            entry = self.line("sent", f"買 {name} ×{qty}")
            if not self.confirm(lambda b, _p: b.get(item_id, 0) >= have_bag + qty):
                entry.phase = "unconfirmed"
                entry.text += "（背包沒有增加：背包滿、負重不夠，或這間沒賣）"
                return False
            entry.phase = "confirmed"
            self.bought += qty
        self.line("error", f"{name}：買了太多輪還沒補齊，先停下")
        return False

    # -- the pet bag -------------------------------------------------------------------

    def can_put(self) -> bool:
        return self.pet_out

    def pet_ready(self, needs: list[tuple[int, int, int]]) -> bool:
        """A pet is out for the pet-bag targets (summoned when allowed)."""
        if not any(p for _i, _b, p in needs):
            return True
        if not all(c in self.caps for c in PET_COMMANDS):
            self.line("info", "這個 hook 沒有放寵物背包的指令，寵物背包的目標先不補")
            return False
        reply = self.cmd("pet")
        slots = reply.get("slots") if reply.get("ok") else None
        if isinstance(slots, list) and any(slots):
            self.pet_out = True
            return True
        name = self.m._character_name(self.pid) or ""
        if not self.m.config(name).pet_summon:
            self.line("info", "沒有召喚寵物（設定不自動召喚），寵物背包的目標先不補")
            return False
        if not all(c in self.caps for c in SUMMON_COMMANDS):
            self.line("info", "這個 hook 沒有召喚寵物的指令，寵物背包的目標先不補")
            return False
        bag, _pet = self.held()
        pets = self.m._pets()
        item = next((i for i, n in bag.items() if n > 0 and i in pets), None)
        if item is None:
            self.line("info", "背包裡沒有寵物可以召喚，寵物背包的目標先不補")
            return False
        r = self.cmd(f"petsummon {item}")
        if not r.get("ok"):
            self.line("error", f"召喚 {self.name(item)} 沒送出：{r.get('error') or '不明原因'}")
            return False
        self.summoned = item
        entry = self.line("sent", f"召喚 {self.name(item)}（放完收回）")
        end = self.m._clock() + SUMMON_WAIT
        while True:
            slots = self.cmd("pet").get("slots")
            if isinstance(slots, list) and any(slots):
                break
            if self.m._clock() >= end:
                entry.phase = "unconfirmed"
                entry.text += "（寵物沒有出來，寵物背包的目標先不補）"
                return False
            self.wait(POLL)
        self.wait(SUMMON_SETTLE)
        entry.phase = "confirmed"
        self.pet_out = True
        return True

    def put_pet(self, item_id: int, qty: int) -> bool:
        name = self.name(item_id)
        self.step(f"放 {name} ×{qty} 進寵物背包")
        _bag, pet = self.held()
        before = pet.get(item_id, 0)
        entry = None
        for _ in range(PUT_TRIES):
            r = self.cmd(f"petput {item_id} {qty}")
            if not r.get("ok"):
                self.line("error", f"放 {name} 進寵物背包沒送出：{r.get('error') or '不明原因'}")
                self.pet_out = False
                return False
            if entry is None:
                entry = self.line("sent", f"放 {name} ×{qty} 進寵物背包")
            if self.confirm(lambda _b, p: p.get(item_id, 0) >= before + qty):
                entry.phase = "confirmed"
                self.put += qty
                return True
            # Not seen after CONFIRM_WAIT: a late landing would show by now, so
            # sending it again does not put it twice.
        entry.phase = "unconfirmed"
        entry.text += "（放了兩次寵物背包都沒有增加，可能滿了）"
        self.pet_out = False  # the rest stays in the bag
        return True

    def dismiss(self) -> None:
        """Put away a pet this trip summoned (the user's own pet stays out)."""
        if self.summoned is None:
            return
        try:
            self.m._channel.send(self.pid, "petdismiss")
            self.line("sent", f"收回 {self.name(self.summoned)}")
        except (PipeGone, PipeBusy, NoReply):
            self.line("error", f"收回 {self.name(self.summoned)} 沒送出")
        self.summoned = None
