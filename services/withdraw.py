"""領倉白名單: take the listed items out of the account's warehouse onto this
character, as many whole stacks as the bag has free slots.

The trip itself is 補給's withdraw trip (the one 分身交貨 uses), which also
records the warehouse it leaves behind; afterwards the bag is recorded too, so
the 寶庫 totals follow the move without the user saving snapshots by hand.

Typical use: one character of an account collects everything into the shared
warehouse (分身交貨 收貨), then another character of the same account (天外天)
takes its school's books out so the warehouse stays small.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from services.api_types import (
    GuardStartResult,
    WithdrawConfig,
    WithdrawRow,
    WithdrawView,
)
from services.handoff import bag_stacks, counts, held_rows
from services.hook_caps import FEATURES
from services.hook_cmd import NoReply, PipeBusy, PipeGone

log = logging.getLogger("tthol.withdraw")

SECTION = "withdraw"
HOST = "領倉白名單"


class _Run:
    def __init__(self) -> None:
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None


class WithdrawManager:
    def __init__(
        self,
        supply,
        character_name: Callable[[int], str | None],
        channel,
        store,
        hook_caps=None,
        # item -> (name, no_trade, no_store, ItemMeta.category)
        item_info: Callable[[int], tuple[str, bool, bool, str] | None] = lambda _i: None,
        icon_url: Callable[[int], str | None] = lambda _i: None,
        # character -> {character, scanned_at, items: [{item_id, qty}]} of its account
        warehouse_snapshot: Callable[[str], dict | None] = lambda _n: None,
        account_name: Callable[[str], str | None] = lambda _n: None,
        # (character, stacks [{item_id, qty}]): the bag after a trip
        record_bag: Callable[[str, list[dict]], None] = lambda _n, _i: None,
        busy: Callable[[int], str | None] = lambda _pid: None,
    ) -> None:
        self._supply = supply
        supply.add_host(HOST)
        self._character_name = character_name
        self._channel = channel
        self._store = store
        self._hook_caps = hook_caps
        self._item_info = item_info
        self._icon_url = icon_url
        self._warehouse_snapshot = warehouse_snapshot
        self._account_name = account_name
        self._record_bag = record_bag
        self._busy = busy
        self._lock = threading.Lock()
        self._runs: dict[int, _Run] = {}

    # -- settings / view -------------------------------------------------------

    def config(self, name: str) -> WithdrawConfig:
        return self._store.load_section(name, SECTION, WithdrawConfig)

    def save(self, pid: int, cfg: WithdrawConfig) -> WithdrawConfig | None:
        name = self._character_name(pid)
        if not name:
            return None
        # Order kept, duplicates dropped.
        cfg = cfg.model_copy(update={"items": list(dict.fromkeys(cfg.items))})
        self._store.save_section(name, SECTION, cfg)
        return cfg

    def _send(self, pid: int, line: str) -> dict | None:
        try:
            reply = self._channel.send(pid, line)
        except (PipeGone, PipeBusy, NoReply):
            return None
        return reply if isinstance(reply, dict) else None

    def _bag(self, pid: int) -> list[tuple[int, int]] | None:
        reply = self._send(pid, "bag")
        return bag_stacks(reply) if reply is not None else None

    def _live_warehouse(self, pid: int) -> list[tuple[int, int]]:
        """The warehouse as the hook last saw it open; [] before it was opened."""
        reply = self._send(pid, "warehouse")
        if not reply or not reply.get("ok"):
            return []
        return [(int(i["item"]), int(i["count"])) for i in reply.get("items") or []]

    def _name(self, item_id: int) -> str:
        info = self._item_info(item_id)
        return info[0] if info else f"#{item_id}"

    def _hook_ready(self, pid: int) -> bool:
        if self._hook_caps is None:
            return False
        names = self._hook_caps.cached(pid)
        if names is None:
            try:
                names = self._hook_caps.get(pid, 60.0)
            except (PipeGone, PipeBusy, NoReply):
                return False
        return set(FEATURES["withdraw"]) <= set(names)

    def view(self, pid: int) -> WithdrawView:
        status = self._supply.status(pid)
        name = self._character_name(pid)
        if not name:
            return WithdrawView(character=None, status=status)
        cfg = self.config(name)
        stacks = self._bag(pid)
        # The account's newest recorded warehouse first: it is dated, and
        # another character of the account may have stored since this window
        # last saw the warehouse open (the hook keeps that list until restart).
        snap = self._warehouse_snapshot(name)
        live = [] if snap is not None else self._live_warehouse(pid)
        if snap is not None:
            wh: list[tuple[int, int]] | None = [
                (int(i["item_id"]), int(i["qty"])) for i in snap["items"]
            ]
            source = "snapshot"
        elif live:
            wh, source = live, "live"
        else:
            wh, source = None, None
        bag = counts(stacks or [])
        wh_n = counts(wh) if wh is not None else {}
        wh_s: dict[int, int] = {}
        for item, _n in wh or []:
            wh_s[item] = wh_s.get(item, 0) + 1
        rows = [
            WithdrawRow(
                item_id=i,
                name=self._name(i),
                icon_url=self._icon_url(i),
                bag=bag.get(i, 0),
                warehouse=wh_n.get(i, 0) if wh is not None else None,
                stacks=wh_s.get(i, 0),
            )
            for i in cfg.items
        ]
        return WithdrawView(
            character=name,
            config=cfg,
            rows=rows,
            bag=held_rows(stacks or [], self._item_info),
            bag_used=len(stacks) if stacks is not None else None,
            warehouse=held_rows(wh or [], self._item_info),
            warehouse_from=source,
            warehouse_holder=snap["character"] if snap else None,
            warehouse_at=snap["scanned_at"] if snap else None,
            account=self._account_name(name),
            hook_ready=self._hook_ready(pid),
            status=status,
        )

    # -- the run -----------------------------------------------------------------

    def start(self, pid: int) -> GuardStartResult:
        name = self._character_name(pid)
        if not name:
            return GuardStartResult(ok=False, reason="角色還沒定位")
        cfg = self.config(name)
        if not cfg.items:
            return GuardStartResult(ok=False, reason="白名單是空的")
        problem = self._busy(pid)
        if problem:
            return GuardStartResult(ok=False, reason=problem)
        if self._supply.running(pid):
            return GuardStartResult(ok=False, reason="補給正在跑")
        stacks = self._bag(pid)
        if stacks is None:
            return GuardStartResult(ok=False, reason="讀不到背包（hook 沒有回應）")
        free = cfg.bag_slots - len(stacks)
        if free <= 0:
            return GuardStartResult(ok=False, reason="背包沒有空格")
        with self._lock:
            if pid in self._runs:
                return GuardStartResult(ok=False, reason="領倉已經在跑")
            run = _Run()
            self._runs[pid] = run

        def body() -> None:
            try:
                self._supply.run(
                    pid,
                    run.stop,
                    host=HOST,
                    withdraw=(frozenset(cfg.items), free),
                    seated=True,
                )
                self._after(pid, name)
            except Exception:
                log.exception("withdraw failed pid=%d", pid, extra={"cat": "supply"})
            finally:
                with self._lock:
                    self._runs.pop(pid, None)

        run.thread = threading.Thread(target=body, daemon=True, name=f"withdraw-{pid}")
        run.thread.start()
        return GuardStartResult(ok=True)

    def _after(self, pid: int, name: str) -> None:
        """Record the bag the trip filled (the warehouse is recorded by the trip)."""
        if self._character_name(pid) != name:
            return
        stacks = self._bag(pid)
        if stacks is not None:
            self._record_bag(name, [{"item_id": i, "qty": n} for i, n in stacks])

    def stop(self, pid: int) -> None:
        with self._lock:
            run = self._runs.get(pid)
        if run is not None:
            run.stop.set()

    def forget(self, pid: int) -> None:
        self.stop(pid)

    def running(self, pid: int) -> bool:
        with self._lock:
            return pid in self._runs
