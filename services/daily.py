"""日常 queue: each character runs its own ordered list of daily modules.

A module is anything with the small interface below (services/tower_run.py's
TowerManager is the first). The queue starts one item, waits for it to end,
and moves on; an item already done today is skipped, and an error stops the
whole queue. Pressing start again picks up from the item that stopped.

The overview renders only DailyStatus: the queue's chips plus one card, which
is the running (or stopped) module's own summary, or a summary of the queue.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Protocol

from services.api_types import (
    DailyItemState,
    DailyMetric,
    DailyModuleInfo,
    DailyQueueConfig,
    DailyQueueItem,
    DailySegment,
    DailyStatus,
    DailySummary,
)

log = logging.getLogger("tthol.daily")

QUEUE_SECTION = "daily.queue"
POLL = 1.0  # how often a running item is checked


class DailyModule(Protocol):
    key: str
    title: str

    def start(self, pid: int) -> tuple[bool, str | None]: ...
    def stop(self, pid: int) -> None: ...
    def running(self, pid: int) -> bool: ...
    # (done | error | user, reason) of the last run; None while running or never run.
    def outcome(self, pid: int) -> tuple[str, str] | None: ...
    def done_today(self, pid: int) -> bool: ...
    def summary(self, pid: int) -> DailySummary: ...
    # By character name, with no game: the batch dispatch asks before a login.
    def done_for(self, name: str) -> bool: ...
    def config_problem(self, name: str) -> str | None: ...
    # By hand: finished outside the toolkit (in game, another PC), or undone.
    def mark_done(self, name: str, done: bool) -> None: ...


class _Item:
    def __init__(self, key: str) -> None:
        self.key = key
        self.state: DailyItemState = "pending"
        self.reason: str | None = None  # why it stopped (error)
        self.attempted = False  # started in this pass: done_today no longer skips it


class _Pass:
    """One go through the list; kept after it ends so start can resume it."""

    def __init__(self, name: str, keys: list[str], day: str) -> None:
        self.name = name
        self.day = day
        self.items = [_Item(k) for k in keys]
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.finished_at: float | None = None  # unix s, when every item was done

    @property
    def keys(self) -> list[str]:
        return [i.key for i in self.items]

    def alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


class DailyQueueManager:
    def __init__(
        self,
        modules: list[DailyModule],
        character_name: Callable[[int], str | None],
        store,  # GuardStore: per-character sections
        wall: Callable[[], float] = time.time,
        wait: Callable[[threading.Event, float], bool] = lambda ev, secs: ev.wait(secs),
    ) -> None:
        self._modules = {m.key: m for m in modules}
        self._character_name = character_name
        self._store = store
        self._wall = wall
        self._wait = wait
        self._passes: dict[int, _Pass] = {}
        self._lock = threading.Lock()

    # -- config ------------------------------------------------------------------

    def modules(self) -> list[DailyModuleInfo]:
        return [DailyModuleInfo(key=m.key, title=m.title) for m in self._modules.values()]

    def config(self, name: str) -> DailyQueueConfig:
        config = self._store.load_section(name, QUEUE_SECTION, DailyQueueConfig)
        return DailyQueueConfig(modules=self._known(config.modules))

    def save_config(self, pid: int, config: DailyQueueConfig) -> DailyQueueConfig | None:
        name = self._character_name(pid)
        if not name:
            return None
        config = DailyQueueConfig(modules=self._known(config.modules))
        self._store.save_section(name, QUEUE_SECTION, config)
        return config

    def _known(self, keys: list[str]) -> list[str]:
        out: list[str] = []
        for k in keys:
            if k in self._modules and k not in out:
                out.append(k)
        return out

    # -- run ---------------------------------------------------------------------

    def start(self, pid: int) -> tuple[bool, str | None]:
        name = self._character_name(pid)
        if not name:
            return False, "角色還沒定位"
        keys = self.config(name).modules
        if not keys:
            return False, "日常清單是空的：先在「日常」分頁排好要跑的項目"
        with self._lock:
            old = self._passes.get(pid)
            if old is not None and old.alive():
                return True, None
            if self._resumable(old, name, keys):
                p = old
                p.stop = threading.Event()
                for item in p.items:
                    if item.state in ("error", "halted", "pending"):
                        item.state, item.reason = "pending", None
            else:
                p = _Pass(name, keys, self._today())
            self._passes[pid] = p
            p.thread = threading.Thread(
                target=self._loop, args=(pid, p), daemon=True, name=f"daily-{pid}"
            )
            p.thread.start()
        log.info("daily queue started pid=%d %s", pid, keys, extra={"cat": "daily"})
        return True, None

    def mark_done(self, name: str, done: bool) -> bool:
        """Mark every module on `name`'s list done today (or not). False: the
        list is empty."""
        keys = self.config(name).modules
        for key in keys:
            self._modules[key].mark_done(name, done)
        log.info("daily marked %s done=%s %s", name, done, keys, extra={"cat": "daily"})
        return bool(keys)

    def precheck(self, name: str) -> tuple[str, str | None]:
        """Before logging `name` in: ("run", None), ("done", why) when every
        listed module is done today, or ("blocked", why) when one cannot start."""
        keys = self.config(name).modules
        if not keys:
            return "blocked", "日常清單是空的"
        todo = [k for k in keys if not self._modules[k].done_for(name)]
        if not todo:
            return "done", "今日都做完了"
        for key in todo:
            problem = self._modules[key].config_problem(name)
            if problem:
                return "blocked", f"{self._modules[key].title}：{problem}"
        return "run", None

    def _resumable(self, p: _Pass | None, name: str, keys: list[str]) -> bool:
        """Same character, same list, today, and something is left to run."""
        return (
            p is not None
            and p.name == name
            and p.keys == keys
            and p.day == self._today()
            and any(i.state in ("error", "halted", "pending") for i in p.items)
        )

    def stop(self, pid: int) -> None:
        with self._lock:
            p = self._passes.get(pid)
        if p is None or not p.alive():
            return
        p.stop.set()
        for item in p.items:
            if item.state in ("running", "moving"):
                module = self._modules.get(item.key)
                if module is not None:
                    module.stop(pid)

    def forget(self, pid: int) -> None:
        """Another character on this window: end the old one's pass and drop it."""
        self.stop(pid)
        with self._lock:
            self._passes.pop(pid, None)

    def stop_all(self) -> None:
        with self._lock:
            pids = list(self._passes)
        for pid in pids:
            self.stop(pid)

    shutdown = stop_all

    def _loop(self, pid: int, p: _Pass) -> None:
        try:
            for item in p.items:
                if item.state in ("done", "skipped"):
                    continue
                if p.stop.is_set():
                    break
                if not self._run_item(pid, p, item):
                    break
            else:
                p.finished_at = self._wall()
        except Exception:
            log.exception("daily queue failed pid=%d", pid, extra={"cat": "daily"})
            for item in p.items:
                if item.state in ("running", "moving"):
                    item.state, item.reason = "error", "日常清單出錯停止（詳見診斷紀錄）"
        log.info("daily queue ended pid=%d", pid, extra={"cat": "daily"})

    def _run_item(self, pid: int, p: _Pass, item: _Item) -> bool:
        """Runs one item to its end; False stops the queue."""
        module = self._modules[item.key]
        if not item.attempted and module.done_today(pid):
            item.state = "skipped"
            return True
        item.attempted = True
        ok, reason = module.start(pid)
        if not ok:
            return self._halt(p, item, reason or "無法開始")
        item.state = "running"
        stopping = False
        while module.running(pid):
            if stopping:
                self._wait(threading.Event(), POLL / 4)  # the module winds down
            elif self._wait(p.stop, POLL):
                stopping = True
                module.stop(pid)
        outcome = module.outcome(pid)
        kind, why = outcome if outcome else ("error", "沒有回報結果")
        if kind == "done":
            item.state = "done"
            return True
        if kind == "user" or p.stop.is_set():
            item.state = "pending"  # stopped by hand: start again runs it
            return False
        return self._halt(p, item, why)

    def _halt(self, p: _Pass, item: _Item, reason: str) -> bool:
        item.state, item.reason = "error", reason
        later = p.items[p.items.index(item) + 1 :]
        for rest in later:
            if rest.state == "pending":
                rest.state = "halted"
        return False

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(self._wall()))

    # -- status ------------------------------------------------------------------

    def status(self, pid: int) -> DailyStatus:
        name = self._character_name(pid)
        if not name:
            return DailyStatus(running=False, card=DailySummary(title="今日清單", state="idle"))
        keys = self.config(name).modules
        with self._lock:
            p = self._passes.get(pid)
        if p is not None and (p.name != name or p.day != self._today()):
            p = None
        running = p is not None and p.alive()
        if p is not None and (running or p.keys == keys):
            items = self._pass_items(pid, p)
        else:
            items = self._config_items(pid, keys)
        card = self._card(pid, p, items)
        error = None
        if card.state == "stopped" and card.module is not None:
            error = f"{card.title}停下：{card.step}" if card.step else f"{card.title}停下"
        return DailyStatus(running=running, items=items, card=card, error=error)

    def _pass_items(self, pid: int, p: _Pass) -> list[DailyQueueItem]:
        out = []
        alive = p.alive()
        for item in p.items:
            module = self._modules[item.key]
            state = item.state
            if not alive and state != "done":
                # The pass is over, but the module may since have run from its
                # own tab: its live state wins over what the pass last saw.
                if module.running(pid):
                    state = "running"
                elif state in ("error", "halted", "pending") and module.done_today(pid):
                    state = "done"
            if state == "running" and module.summary(pid).state == "moving":
                state = "moving"
            result = None
            if state == "done":
                result = module.summary(pid).result
            elif state == "skipped":
                result = "今日已做"
            out.append(
                DailyQueueItem(module=item.key, title=module.title, state=state, result=result)
            )
        return out

    def _config_items(self, pid: int, keys: list[str]) -> list[DailyQueueItem]:
        """No pass today: each module's own state (it may run from its tab)."""
        out = []
        for k in keys:
            module = self._modules[k]
            s = module.summary(pid)
            state: DailyItemState = {
                "moving": "moving",
                "running": "running",
                "done": "done",
                "done_today": "done",
                "stopped": "error",
            }.get(s.state, "pending")
            out.append(DailyQueueItem(module=k, title=module.title, state=state, result=s.result))
        return out

    def _card(self, pid: int, p: _Pass | None, items: list[DailyQueueItem]) -> DailySummary:
        for item in items:
            if item.state in ("running", "moving"):
                return self._modules[item.module].summary(pid)
        for item in items:
            if item.state == "error":
                s = self._modules[item.module].summary(pid)
                reason = next(
                    (i.reason for i in (p.items if p else []) if i.key == item.module and i.reason),
                    None,
                )
                # The module's own stop is the newer one (it may have run again
                # from its tab); a start that was refused never reached it.
                step = s.step if s.state == "stopped" and s.step else reason or s.step
                return s.model_copy(update={"state": "stopped", "step": step})
        if not items:
            return DailySummary(title="今日清單", state="idle", where="「日常」分頁還沒排清單")
        finished = [i for i in items if i.state in ("done", "skipped")]
        segments: list[DailySegment] = [
            "done" if i.state in ("done", "skipped") else "empty" for i in items
        ]
        headline = f"{len(finished)} / {len(items)}"
        if len(finished) == len(items):
            done = sum(1 for i in items if i.state == "done")
            skipped = len(items) - done
            at = p.finished_at if p is not None else None
            step = (
                f"{time.strftime('%H:%M', time.localtime(at))} 全部完成"
                if at
                else "今天的項目都做過了"
            )
            metrics = [DailyMetric(label="完成", value=f"{done} 項")]
            if skipped:
                metrics.append(DailyMetric(label="略過", value=f"{skipped} 項"))
            return DailySummary(
                title="今日清單",
                state="done",
                headline=headline,
                where="今天的清單全部做完",
                segments=segments,
                step=step,
                metrics=metrics,
                done_today=True,
            )
        nxt = next(i for i in items if i.state not in ("done", "skipped"))
        stopped = p is not None and any(i.attempted for i in p.items)
        return DailySummary(
            title="今日清單",
            state="idle",
            headline=headline,
            where=f"已停止，按開始從{nxt.title}接著跑" if stopped else "今天還沒開始",
            segments=segments,
            step="清單：" + " → ".join(i.title for i in items),
        )
