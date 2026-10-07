"""分身交貨: one character stands as a warehouse (收貨) and takes whitelisted
items from other windows (送貨) by player trade, one round at a time.

The user's design (2026-10-07):

- the receiver keeps its whitelist; each entry says what to do once it came:
  keep it on the body, or store it at once;
- a round: the receiver tells its free slots, the sender puts exactly that many
  stacks (and no more than one trade takes), both lock and confirm, the
  receiver stores the round's 存倉 items, then the next round starts;
- the receiver accepts only a sender this app is running, never a stranger;
- there can be two receivers (two accounts): a sender serves them in the order
  they started, an item going to the first one that wants it.

Both windows are threads in this app, so they meet in this module (the hub),
not in game packets. The trade itself follows tthol-hook's 10-05 flow: invite
-> 1F 01 -> accept -> 1F 02 on both -> each put -> 1F 03 to the partner ->
lock -> 1F 05 to the partner -> both confirm -> 1F 08. Command replies only
mean "sent": every step waits for its 1F event, and any failure cancels the
trade so neither window is left in trade mode.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field

from services import run_log
from services.api_types import (
    HandoffBagItem,
    HandoffConfig,
    HandoffCount,
    HandoffLogEntry,
    HandoffPlanRow,
    HandoffReceiver,
    HandoffStatus,
    HandoffView,
)
from services.hook_caps import FEATURES
from services.hook_cmd import CommandChannel, NoReply, PipeBusy, PipeGone

log = logging.getLogger("tthol.handoff")

SECTION = "handoff"
HOST = "分身交貨 · 存倉"
HOST_WITHDRAW = "分身交貨 · 領倉"
MAX_PASSES = 100  # bag-loads from the warehouse in one sender run
TRADE_PACKET = 0x1F
EV_INVITE, EV_OPEN, EV_PUT, EV_LOCK, EV_CANCEL, EV_DONE = 1, 2, 3, 5, 6, 8
# Stacks in one trade for a hook without `capacity`: the window holds 10 (the
# server ignores an 11th put the client already moved: tthol-hook 2026-10-07);
# 3 is what went through first, on 2026-10-05.
TRADE_STACKS = 3
LOG_KEEP = 200
PIPE_RETRIES = 5
COMMANDS = FEATURES["handoff"]


@dataclass(frozen=True)
class Timing:
    invite: float = 15.0  # an invite (or the sender's batch) after a turn opens
    open: float = 8.0  # 1F 02 after the invite / accept
    step: float = 10.0  # each put, lock and confirm
    seen: float = 3.0  # the bag shows what the trade moved
    store: float = 900.0  # the sender waits out the receiver's store trip
    put_gap: float = 0.3
    poll: float = 0.2


# ---- the 1F events -----------------------------------------------------------


class TradeEvents:
    """The trade events (0x1F sub-types) each client got, numbered in order."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._cond = threading.Condition()
        self._seq: dict[int, int] = {}
        self._log: dict[int, deque[tuple[int, int]]] = {}
        self._clock = clock

    def on_packet(self, pid: int, raw: bytes, _ts: float, _own: bytes | None) -> None:
        if len(raw) < 2 or not 1 <= raw[1] <= 10:
            return
        self.push(pid, raw[1])

    def push(self, pid: int, sub: int) -> None:
        with self._cond:
            n = self._seq.get(pid, 0) + 1
            self._seq[pid] = n
            self._log.setdefault(pid, deque(maxlen=64)).append((n, sub))
            self._cond.notify_all()

    def mark(self, pid: int) -> int:
        with self._cond:
            return self._seq.get(pid, 0)

    def count(self, pid: int, after: int, sub: int) -> int:
        with self._cond:
            return sum(1 for n, s in self._log.get(pid, ()) if n > after and s == sub)

    def wait(
        self,
        pid: int,
        after: int,
        want: set[int],
        timeout: float,
        stop: threading.Event,
        enough: Callable[[], bool] | None = None,
    ) -> tuple[int, int] | None:
        """The first event in `want` after `after` as (seq, sub), or None on a
        timeout or a stop. With `enough`, wait until it holds instead (or a
        cancel comes) and return (mark, 0)."""
        end = self._clock() + timeout
        with self._cond:
            while True:
                for n, s in self._log.get(pid, ()):
                    if n > after and s in want and (enough is None or s == EV_CANCEL):
                        return n, s
                if enough is not None and enough():
                    return self._seq.get(pid, 0), 0
                left = end - self._clock()
                if stop.is_set() or left <= 0:
                    return None
                self._cond.wait(min(left, 0.2))


# ---- pure parts --------------------------------------------------------------


def bag_stacks(reply: dict) -> list[tuple[int, int]] | None:
    """[(item, count)] per bag slot from the hook's `bag` reply."""
    if not reply.get("ok") or not isinstance(reply.get("bag"), list):
        return None
    return [(int(s["item"]), int(s["count"])) for s in reply["bag"]]


def counts(stacks: list[tuple[int, int]]) -> dict[int, int]:
    out: dict[int, int] = {}
    for item, n in stacks:
        out[item] = out.get(item, 0) + n
    return out


def for_receiver(
    stacks: list[tuple[int, int]], wants: set[int], untradable: Callable[[int], bool]
) -> list[tuple[int, int]]:
    """The bag stacks a receiver takes, in bag order (`tradeput` takes the
    first stack of an item)."""
    return [(i, n) for i, n in stacks if i in wants and not untradable(i)]


def plan(
    stacks: list[tuple[int, int]],
    receivers: list[tuple[int, set[int]]],
    untradable: Callable[[int], bool],
) -> dict[int, list[tuple[int, int]]]:
    """receiver pid -> its stacks; an item goes to the first receiver that wants it."""
    out: dict[int, list[tuple[int, int]]] = {}
    taken: set[int] = set()
    for pid, wants in receivers:
        mine = set(wants) - taken
        out[pid] = for_receiver(stacks, mine, untradable)
        taken |= mine
    return out


def batch(
    stacks: list[tuple[int, int]], free: int, cap: int = TRADE_STACKS
) -> list[tuple[int, int]]:
    """One round: one stack per free slot (a stack can land as a new one), up to `cap`."""
    return stacks[: max(0, min(free, cap))]


def trade_cap(reply: dict) -> int:
    """Stacks one trade takes: the hook's `capacity` (newer hooks), else the
    3 measured on 2026-10-05; never more than that capacity."""
    cap = reply.get("capacity") if reply.get("ok") else None
    if isinstance(cap, int) and 0 < cap <= 64:
        return cap
    return TRADE_STACKS


def gains(before: dict[int, int], after: dict[int, int]) -> dict[int, int]:
    return {i: n - before.get(i, 0) for i, n in after.items() if n > before.get(i, 0)}


def losses(before: dict[int, int], after: dict[int, int]) -> dict[int, int]:
    return {i: n - after.get(i, 0) for i, n in before.items() if n > after.get(i, 0)}


# ---- runs and turns ----------------------------------------------------------


class _Line:
    def __init__(self, id_: int, ts: float, phase: str, text: str) -> None:
        self.id, self.ts, self.phase, self.text = id_, ts, phase, text


class _Stop(Exception):
    """Ends a run."""

    def __init__(self, reason: str, phase: str = "info") -> None:
        super().__init__(reason)
        self.reason, self.phase = reason, phase


class _Fail(Exception):
    """Ends a round (and, on the sender, this receiver)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Run:
    def __init__(self, role: str, pid: int, name: str, config: HandoffConfig) -> None:
        self.role = role  # "receive" / "send"
        self.pid, self.name, self.config = pid, name, config
        self.account: int | None = None
        self.started = 0.0
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.log: deque[_Line] = deque(maxlen=LOG_KEEP)
        self.next_id = 0
        self.step: str | None = None
        self.problem: str | None = None
        self.ended: str | None = None
        self.stopping = False  # receiver: stop once the round in hand is stored
        self.trading = False  # the trade window is open (a stop cancels it)
        # receiver, guarded by the manager's hub condition
        self.ready = False
        self.stage: int | None = None
        self.tile: tuple[int, int] | None = None
        self.key: tuple[int, int] | None = None  # (npc id, inst) on its map
        self.free: int | None = None
        self.queue: list[int] = []
        self.turn: _Turn | None = None
        self.received: dict[int, int] = {}
        # sender
        self.sent: dict[int, int] = {}
        self.refused: set[int] = set()  # tradeput said it cannot be traded
        self.now_with: str | None = None

    def wants(self) -> set[int]:
        return {e.item_id for e in self.config.items}

    def stores(self) -> set[int]:
        return {e.item_id for e in self.config.items if e.store}


@dataclass
class _Turn:
    """One round between a sender and a receiver; both move `state` on."""

    sender: _Run
    receiver: _Run
    key: tuple[int, int]  # the sender's (npc id, inst) on the receiver's map
    state: str = "asked"  # asked armed inviting open put locked received done / skip failed
    free: int = 0
    stacks: int = 0
    items: dict[int, int] = field(default_factory=dict)
    reason: str = ""


class HandoffManager:
    def __init__(
        self,
        guard,
        character_name: Callable[[int], str | None],
        channel: CommandChannel,
        store,
        navigator=None,
        supply=None,
        read_stage: Callable[[int], int | None] = lambda _pid: None,
        stage_name: Callable[[int], str | None] = lambda _sid: None,
        account_of: Callable[[str], int | None] = lambda _name: None,
        busy: Callable[[int], str | None] = lambda _pid: None,
        item_info: Callable[[int], tuple[str, bool, bool] | None] = lambda _i: None,
        hook_caps=None,
        timing: Timing = Timing(),
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self._guard = guard
        self._character_name = character_name
        self._channel = channel
        self._store = store
        self._navigator = navigator
        self._supply = supply
        if supply is not None:
            supply.add_host(HOST)
            supply.add_host(HOST_WITHDRAW)
        self._read_stage = read_stage
        self._stage_name = stage_name
        self._account_of = account_of
        self._busy = busy
        self._item_info = item_info
        self._hook_caps = hook_caps
        self.t = timing
        self._clock = clock
        self._wall = wall
        self.events = TradeEvents(clock)
        self._runs: dict[int, _Run] = {}
        self._lock = threading.Lock()
        self._hub = threading.Condition()  # receivers' hub fields and every turn

    # -- items -----------------------------------------------------------------

    def _name(self, item_id: int) -> str:
        info = self._item_info(item_id)
        return info[0] if info else f"#{item_id}"

    def _untradable(self, item_id: int) -> bool:
        info = self._item_info(item_id)
        return bool(info and info[1])

    # -- API -------------------------------------------------------------------

    def config(self, name: str) -> HandoffConfig:
        return self._store.load_section(name, SECTION, HandoffConfig)

    def save(self, pid: int, config: HandoffConfig) -> HandoffConfig | None:
        name = self._character_name(pid)
        if not name:
            return None
        self._store.save_section(name, SECTION, config)
        with self._lock:
            run = self._runs.get(pid)
        if run is not None and run.role == "receive" and run.name == name:
            with self._hub:
                run.config = config
                self._hub.notify_all()
        return config

    def view(self, pid: int) -> HandoffView:
        name = self._character_name(pid)
        if not name:
            return HandoffView(character=None, config=HandoffConfig(), status=self.status(pid))
        stacks: list[tuple[int, int]] | None = None
        try:
            stacks = bag_stacks(self._channel.send(pid, "bag"))
        except (PipeGone, PipeBusy, NoReply):
            pass
        account = self._account_of(name)
        receivers = self._open_receivers(pid)
        rows: list[HandoffReceiver] = []
        plan_rows: list[HandoffPlanRow] = []
        mine = plan(
            stacks or [],
            [(r.pid, r.wants()) for r in receivers if not self._same_account(account, r)],
            self._untradable,
        )
        with self._hub:
            for r in receivers:
                same = self._same_account(account, r)
                take = mine.get(r.pid, [])
                rows.append(
                    HandoffReceiver(
                        pid=r.pid,
                        character=r.name,
                        same_account=same,
                        stage_name=self._stage_name(r.stage) if r.stage is not None else None,
                        tile=list(r.tile) if r.tile else None,
                        free=r.free,
                        items=len(r.config.items),
                        state=self._receiver_state(r),
                        busy_with=r.turn.sender.name if r.turn else None,
                        queue=len(r.queue),
                        stacks=len(take),
                    )
                )
                for item, n in take:
                    plan_rows.append(
                        HandoffPlanRow(
                            item_id=item,
                            name=self._name(item),
                            count=n,
                            receiver_pid=r.pid,
                            receiver=r.name,
                            store=item in r.stores(),
                        )
                    )
        bag: list[HandoffBagItem] = []
        if stacks is not None:
            slots: dict[int, int] = {}
            for item, _n in stacks:
                slots[item] = slots.get(item, 0) + 1
            for item, n in counts(stacks).items():
                info = self._item_info(item)
                bag.append(
                    HandoffBagItem(
                        item_id=item,
                        name=info[0] if info else f"#{item}",
                        count=n,
                        stacks=slots[item],
                        no_trade=bool(info and info[1]),
                        no_store=bool(info and info[2]),
                    )
                )
        return HandoffView(
            character=name,
            config=self.config(name),
            status=self.status(pid),
            receivers=rows,
            plan=plan_rows,
            bag=bag,
            bag_used=len(stacks) if stacks is not None else None,
        )

    def status(self, pid: int) -> HandoffStatus:
        with self._lock:
            run = self._runs.get(pid)
        if run is None:
            return HandoffStatus(running=False, character=self._character_name(pid))
        running = run.thread is not None and run.thread.is_alive() and not run.stop.is_set()
        with self._hub:
            queue = [self._runs[p].name for p in run.queue if p in self._runs]
            turn_with = run.turn.sender.name if run.turn else run.now_with
            free = run.free
        with run.lock:
            got = run.received if run.role == "receive" else run.sent
            stores = run.stores()
            return HandoffStatus(
                running=running,
                role=run.role,
                character=run.name,
                step=run.step if running else None,
                problem=run.problem if running else None,
                stopping=run.stopping and running,
                ended=run.ended,
                free=free if run.role == "receive" else None,
                queue=queue,
                turn_with=turn_with if running else None,
                moved=[
                    HandoffCount(
                        item_id=i,
                        name=self._name(i),
                        count=n,
                        store=(i in stores) if run.role == "receive" else None,
                    )
                    for i, n in sorted(got.items())
                ],
                log=[
                    HandoffLogEntry(id=line.id, ts=line.ts, phase=line.phase, text=line.text)
                    for line in reversed(run.log)
                ],
            )

    def start(self, pid: int, role: str) -> tuple[bool, str | None]:
        name = self._character_name(pid)
        if not name:
            return False, "角色還沒定位"
        problem = self._busy(pid)
        if problem:
            return False, problem
        if self._hook_caps is not None:
            try:
                names = self._hook_caps.get(pid, 60.0) or frozenset()
            except PipeGone:
                return False, "這個遊戲視窗沒有 hook 指令通道"
            except (PipeBusy, NoReply):
                return False, "hook 暫時沒有回應（還在登入或換地圖？），稍後再開始"
            missing = [c for c in COMMANDS if c not in names]
            if missing:
                return False, f"這個 hook 缺少交貨要用的指令：{'、'.join(missing)}"
        config = self.config(name)
        if role == "receive" and not config.items:
            return False, "還沒設定要收哪些東西"
        with self._lock:
            old = self._runs.get(pid)
            if old is not None and old.thread is not None and old.thread.is_alive():
                if old.role == role and not old.stop.is_set():
                    return True, None
                return False, "這隻角色的交貨還沒停下"
            run = _Run(role, pid, name, config)
            run.account = self._account_of(name)
            run.started = self._clock()
            self._runs[pid] = run
            target = self._receive if role == "receive" else self._send
            run.thread = threading.Thread(
                target=self._loop, args=(run, target), daemon=True, name=f"handoff-{role}-{pid}"
            )
            run.thread.start()
        log.info("handoff %s started pid=%d", role, pid, extra={"cat": "handoff"})
        return True, None

    def stop(self, pid: int) -> None:
        """A receiver stops once the round in hand is stored (again: at once)."""
        with self._lock:
            run = self._runs.get(pid)
        if run is None:
            return
        with self._hub:
            if run.role == "receive" and run.turn is not None and not run.stopping:
                run.stopping = True
                self._hub.notify_all()
                return
            run.stop.set()
            self._hub.notify_all()

    def forget(self, pid: int) -> None:
        with self._lock:
            run = self._runs.get(pid)
        if run is not None:
            run.stop.set()
            with self._hub:
                self._hub.notify_all()
        with self._lock:
            self._runs.pop(pid, None)

    def shutdown(self) -> None:
        with self._lock:
            runs = list(self._runs.values())
        for run in runs:
            run.stop.set()
        with self._hub:
            self._hub.notify_all()

    def running(self, pid: int) -> bool:
        with self._lock:
            run = self._runs.get(pid)
        return run is not None and run.thread is not None and run.thread.is_alive()

    def on_trade_packet(self, pid: int, raw: bytes, ts: float, own: bytes | None) -> None:
        self.events.on_packet(pid, raw, ts, own)

    # -- helpers ---------------------------------------------------------------

    def _same_account(self, account: int | None, r: _Run) -> bool:
        # An account not assigned (None) is not taken as the same one.
        return account is not None and account == r.account

    def _receiver_state(self, r: _Run) -> str:
        if r.stop.is_set():
            return "stopped"
        if r.turn is not None:
            return "storing" if r.turn.state == "received" else "trading"
        return "waiting" if r.ready else "busy"

    def _open_receivers(self, pid: int) -> list[_Run]:
        with self._lock:
            runs = [
                r
                for r in self._runs.values()
                if r.role == "receive" and r.pid != pid and not r.stop.is_set()
            ]
        return sorted(runs, key=lambda r: r.started)

    def _note(self, run: _Run, phase: str, text: str) -> None:
        with run.lock:
            run.next_id += 1
            run.log.append(_Line(run.next_id, self._wall(), phase, text))
        run_log.note("handoff", run.pid, run.name, text, phase=phase, role=run.role)

    def _set_step(self, run: _Run, step: str) -> None:
        with run.lock:
            run.step, run.problem = step, None

    def _cmd(self, run: _Run, line: str) -> dict:
        for _ in range(PIPE_RETRIES):
            if run.stop.is_set():
                raise _Stop("已停止")
            try:
                return self._channel.send(run.pid, line)
            except PipeGone:
                self._wait(run, 1.0)
            except (PipeBusy, NoReply):
                return {"ok": False, "error": "busy"}
        raise _Stop("找不到 hook 指令通道（遊戲關了，或 hook 沒有注入）", "error")

    def _wait(self, run: _Run, secs: float) -> None:
        if run.stop.wait(secs):
            raise _Stop("已停止")

    def _bag(self, run: _Run) -> list[tuple[int, int]]:
        for _ in range(10):
            stacks = bag_stacks(self._cmd(run, "bag"))
            if stacks is not None:
                return stacks
            self._wait(run, 0.3)
        raise _Stop("讀不到背包", "error")

    def _here(self, run: _Run) -> tuple[int | None, tuple[int, int], tuple[int, int]]:
        """(stage id, tile, own (npc id, inst)) now."""
        for _ in range(20):
            st = self._cmd(run, "status")
            near = self._cmd(run, "near")
            tile = st.get("tile")
            if st.get("ok") and isinstance(tile, list) and near.get("ok"):
                me = next((o for o in near.get("near") or [] if o.get("h") == st.get("self")), None)
                if me is not None:
                    return self._read_stage(run.pid), (tile[0], tile[1]), (me["id"], me["inst"])
            self._wait(run, 0.5)
        raise _Stop("讀不到角色位置（換地圖中？）", "error")

    def _close_panels(self, run: _Run) -> None:
        r = self._cmd(run, "closepanel")
        if not r.get("ok") and r.get("error") not in (None, "busy", "no panel open"):
            log.info("closepanel pid=%d: %s", run.pid, r, extra={"cat": "handoff"})

    def _cancel(self, run: _Run) -> None:
        if not run.trading:
            return
        run.trading = False
        try:
            self._channel.send(run.pid, "tradecancel")
        except (PipeGone, PipeBusy, NoReply):
            pass

    def _await(self, run: _Run, turn: _Turn, states: set[str], timeout: float, what: str) -> str:
        """Wait for the turn to reach one of `states` (or fail, or either side stop)."""
        end = self._clock() + timeout
        with self._hub:
            while True:
                if turn.state in states:
                    return turn.state
                if turn.state == "failed":
                    raise _Fail(turn.reason)
                other = turn.receiver if run is turn.sender else turn.sender
                if run.stop.is_set():
                    raise _Stop("已停止")
                if other.stop.is_set():
                    raise _Fail(f"{other.name} 停止了")
                left = end - self._clock()
                if left <= 0:
                    raise _Fail(f"等太久：{what}")
                self._hub.wait(min(left, 0.2))

    def _move(self, turn: _Turn, state: str, **fields) -> None:
        with self._hub:
            if turn.state == "failed":
                raise _Fail(turn.reason)
            for k, v in fields.items():
                setattr(turn, k, v)
            turn.state = state
            self._hub.notify_all()

    def _fail_turn(self, turn: _Turn, reason: str) -> None:
        with self._hub:
            if turn.state not in ("done", "skip", "failed"):
                turn.state, turn.reason = "failed", reason
            self._hub.notify_all()

    def _event(self, run: _Run, after: int, want: set[int], timeout: float, what: str) -> int:
        got = self.events.wait(run.pid, after, want | {EV_CANCEL}, timeout, run.stop)
        if got is None:
            if run.stop.is_set():
                raise _Stop("已停止")
            raise _Fail(f"等太久：{what}")
        if got[1] == EV_CANCEL:
            run.trading = False
            raise _Fail("交易被取消了")
        return got[0]

    def _came(
        self, run: _Run, before: dict[int, int], expect: dict[int, int], sign: int
    ) -> dict[int, int]:
        """What the trade moved by the bag (gains for +1, losses for -1); polls
        a moment until all of `expect` shows."""
        end = self._clock() + self.t.seen
        while True:
            now = counts(self._bag(run))
            moved = gains(before, now) if sign > 0 else losses(before, now)
            if all(moved.get(i, 0) >= n for i, n in expect.items()) or self._clock() >= end:
                return moved
            self._wait(run, self.t.poll)

    # -- the loop --------------------------------------------------------------

    def _loop(self, run: _Run, body: Callable[[_Run], str]) -> None:
        self._note(run, "info", "開始收貨" if run.role == "receive" else "開始送貨")
        ended, phase = "已停止", "info"
        try:
            with ExitStack() as hold:
                hold.enter_context(self._guard.quiet(run.pid))
                ended = body(run)
        except _Stop as s:
            ended, phase = s.reason, s.phase
        except Exception:
            log.exception("handoff failed pid=%d", run.pid, extra={"cat": "handoff"})
            ended, phase = "交貨出錯停止（詳見診斷紀錄）", "error"
        finally:
            self._cancel(run)
            with self._hub:
                run.ready = False
                if run.turn is not None:
                    turn, run.turn = run.turn, None
                    if turn.state not in ("done", "skip", "failed"):
                        turn.state, turn.reason = "failed", f"{run.name} 停止了"
                for r in self._open_receivers(-1) + [run]:
                    if run.pid in r.queue:
                        r.queue.remove(run.pid)
                self._hub.notify_all()
            with run.lock:
                run.ended = ended
            run.stop.set()
            title = "收貨結束" if run.role == "receive" else "送貨結束"
            self._note(run, phase, f"{title}：{ended}")
            log.info(
                "handoff %s stopped pid=%d: %s", run.role, run.pid, ended, extra={"cat": "handoff"}
            )

    # -- receiver --------------------------------------------------------------

    def _receive(self, run: _Run) -> str:
        self._close_panels(run)
        stage, tile, _key = self._here(run)
        home = (stage, tile)
        self._ready(run)
        self._note(run, "info", f"站在 {tile}，白名單 {len(run.config.items)} 項，空 {run.free} 格")
        while True:
            turn = self._next_turn(run)
            if turn is None:
                return "已停止"
            try:
                self._serve(run, turn, home)
            except _Fail as f:
                self._cancel(run)
                self._fail_turn(turn, f.reason)
                self._note(run, "error", f"跟 {turn.sender.name} 這輪沒完成：{f.reason}")
            finally:
                with self._hub:
                    run.turn = None
                    if run.queue and run.queue[0] == turn.sender.pid:
                        run.queue.pop(0)
                    self._hub.notify_all()
            if run.stopping:
                return "已停止"
            self._ready(run)

    def _ready(self, run: _Run) -> None:
        """Waiting again: free slots and own key, read now."""
        self._set_step(run, "等送貨的人")
        stage, tile, key = self._here(run)
        free = run.config.bag_slots - len(self._bag(run))
        with self._hub:
            run.stage, run.tile, run.key, run.free = stage, tile, key, free
            run.ready = True
            self._hub.notify_all()

    def _next_turn(self, run: _Run) -> _Turn | None:
        with self._hub:
            while True:
                if run.stop.is_set() or run.stopping:
                    return None
                if run.turn is not None and run.turn.state == "asked":
                    run.ready = False
                    return run.turn
                self._hub.wait(0.5)

    def _serve(self, run: _Run, turn: _Turn, home: tuple[int | None, tuple[int, int]]) -> None:
        sender = turn.sender
        before = counts(self._bag(run))
        free = run.config.bag_slots - len(self._bag(run))
        mark = self.events.mark(run.pid)
        self._move(turn, "armed", free=free)
        if (
            self._await(run, turn, {"inviting", "skip"}, self.t.invite, "送貨方決定這輪放什麼")
            == "skip"
        ):
            return
        self._set_step(run, f"等 {sender.name} 邀請交易")
        after = mark
        end = self._clock() + self.t.invite
        while True:
            after = self._event(
                run, after, {EV_INVITE}, max(0.0, end - self._clock()), "對方的交易邀請"
            )
            tr = self._cmd(run, "trade")
            peer = tr.get("peer") or {}
            if (peer.get("id"), peer.get("inst")) == turn.key:
                break
            self._note(run, "unconfirmed", "有人邀請交易（不是送貨中的角色），不理")
        opened = self.events.mark(run.pid)
        r = self._cmd(run, "tradeaccept")
        if not r.get("ok"):
            raise _Fail(f"接受交易沒送出：{r.get('error')}")
        self._event(run, opened, {EV_OPEN}, self.t.open, "交易視窗打開")
        run.trading = True
        self._move(turn, "open")
        self._set_step(run, f"跟 {sender.name} 交易中")
        self._await(run, turn, {"put"}, self.t.step * (turn.stacks + 2), "對方放東西")
        got = self.events.wait(
            run.pid,
            opened,
            {EV_CANCEL},
            self.t.step,
            run.stop,
            # The sender locks after its last put, so its 1F 05 comes after
            # every 1F 03; the bag after 1F 08 is what counts what came.
            enough=lambda: self.events.count(run.pid, opened, EV_LOCK) >= 1,
        )
        if got is None:
            raise _Stop("已停止") if run.stop.is_set() else _Fail("等太久：對方放東西、鎖定")
        if got[1] == EV_CANCEL:
            run.trading = False
            raise _Fail("交易被取消了")
        if not self._cmd(run, "tradelock").get("ok"):
            raise _Fail("鎖定沒送出")
        self._move(turn, "locked")
        if not self._cmd(run, "tradeconfirm").get("ok"):
            raise _Fail("確認沒送出")
        self._event(run, opened, {EV_DONE}, self.t.step, "交易完成")
        run.trading = False
        came = self._came(run, before, turn.items, +1)
        what = "、".join(f"{self._name(i)} ×{n}" for i, n in came.items()) or "（背包沒看到新東西）"
        short = {i: n - came.get(i, 0) for i, n in turn.items.items() if came.get(i, 0) < n}
        phase = "confirmed" if not short else "unconfirmed"
        self._note(run, phase, f"收到 {sender.name}：{what}")
        if short:
            self._note(
                run,
                "error",
                "少了：" + "、".join(f"{self._name(i)} ×{n}" for i, n in short.items()),
            )
        with run.lock:
            for i, n in came.items():
                run.received[i] = run.received.get(i, 0) + n
        self._move(turn, "received")
        want = {i: n for i, n in came.items() if i in run.stores()}
        if want:
            self._store_trip(run, want)
        self._go_home(run, home)
        self._move(turn, "done")

    def _store_trip(self, run: _Run, want: dict[int, int]) -> None:
        if self._supply is None:
            raise _Stop("沒有補給模組，不能存倉", "error")
        self._set_step(run, "存倉")
        result = self._supply.run(
            run.pid,
            run.stop,
            note=lambda text: self._set_step(run, text),
            host=HOST,
            store_only=want,
        )
        if not getattr(result, "ok", False):
            raise _Stop(f"存倉沒完成：{getattr(result, 'detail', '')}", "error")
        self._note(
            run, "confirmed", "存倉：" + "、".join(f"{self._name(i)} ×{n}" for i, n in want.items())
        )

    def _go_home(self, run: _Run, home: tuple[int | None, tuple[int, int]]) -> None:
        stage, tile = home
        here, pos, _key = self._here(run)
        if here == stage and max(abs(pos[0] - tile[0]), abs(pos[1] - tile[1])) <= 1:
            return
        if self._navigator is None or stage is None:
            raise _Stop("存完倉回不到原位（沒有導航）", "error")
        self._set_step(run, "回原位")
        result = self._navigator.go(
            run.pid, stage, goal=tile, stop=run.stop, note=lambda t: self._set_step(run, t)
        )
        if not result.ok:
            raise _Stop(f"回不到原位：{result.detail or result.reason}", "error")

    # -- sender ----------------------------------------------------------------

    def _send(self, run: _Run) -> str:
        self._close_panels(run)
        receivers = [
            r for r in self._open_receivers(run.pid) if not self._same_account(run.account, r)
        ]
        if not receivers:
            raise _Stop("沒有開著的倉庫（先在收貨角色按「開始收貨」）", "error")
        # The bag first; then, with from_warehouse, what the own warehouse
        # holds, a bag-load at a time, until it has none or nobody takes more.
        done_with: set[int] = set()  # receivers full, stopped or failed
        from_warehouse = run.config.from_warehouse and self._supply is not None
        warehouse_left = 0
        for _ in range(MAX_PASSES):
            self._pass(run, receivers, done_with)
            if not from_warehouse:
                break
            takers = [r for r in receivers if r.pid not in done_with and not r.stop.is_set()]
            cannot = self._cannot(run)
            wants = {i for r in takers for i in r.wants() if not cannot(i)}
            if not wants:
                break
            free = run.config.bag_slots - len(self._bag(run))
            if free <= 0:
                self._note(run, "unconfirmed", "背包滿了，沒辦法再從倉庫領")
                break
            got, warehouse_left = self._withdraw(run, wants, free)
            if not got:
                break
        left = plan(self._bag(run), [(r.pid, r.wants()) for r in receivers], self._cannot(run))
        n = sum(len(v) for v in left.values())
        parts = [f"背包還有 {n} 格沒交出去"] if n else []
        if warehouse_left:
            parts.append(f"倉庫還有 {warehouse_left} 堆沒領")
        return "，".join(parts) if parts else "全部交完"

    def _pass(self, run: _Run, receivers: list[_Run], done_with: set[int]) -> None:
        """What the bag holds, to each receiver in order: the first one that wants
        an item gets it; what it had no room for goes on to the next one."""
        for r in receivers:
            if r.pid in done_with:
                continue
            wants = r.wants()
            if not for_receiver(self._bag(run), wants, self._cannot(run)):
                continue
            with self._hub:
                run.now_with = r.name
            try:
                if not self._deliver(run, r, wants):
                    done_with.add(r.pid)
            except _Fail as f:
                self._cancel(run)
                done_with.add(r.pid)
                self._note(run, "error", f"交給 {r.name} 沒完成：{f.reason}")
            finally:
                with self._hub:
                    run.now_with = None
                    if run.pid in r.queue:
                        r.queue.remove(run.pid)
                    self._hub.notify_all()

    def _withdraw(self, run: _Run, wants: set[int], free: int) -> tuple[int, int]:
        """(items taken out, wanted stacks left there) from the own warehouse."""
        self._set_step(run, "去倉庫領東西")
        result = self._supply.run(
            run.pid,
            run.stop,
            note=lambda text: self._set_step(run, text),
            host=HOST_WITHDRAW,
            withdraw=(frozenset(wants), free),
        )
        if run.stop.is_set():
            raise _Stop("已停止")
        if not getattr(result, "ok", False):
            self._note(run, "error", f"領倉沒完成：{getattr(result, 'detail', '')}")
            return 0, 0
        got = getattr(result, "withdrawn", 0)
        left = getattr(result, "left", 0)
        if got:
            self._note(
                run, "confirmed", f"從倉庫領出 {got} 個" + (f"（還剩 {left} 堆）" if left else "")
            )
        else:
            self._note(run, "info", "倉庫裡沒有倉庫要收的東西了")
        return got, left

    def _cannot(self, run: _Run) -> Callable[[int], bool]:
        # The DB's no_trade misses many (賞善 / quest items: tthol-hook
        # 2026-10-07), so what tradeput refused is skipped for the rest of the run.
        return lambda item: self._untradable(item) or item in run.refused

    def _deliver(self, run: _Run, r: _Run, wants: set[int]) -> bool:
        """Everything it wants from the bag (True), or it is full (False)."""
        with self._hub:
            stage, tile = r.stage, r.tile
        if stage is None or tile is None:
            raise _Fail(f"不知道 {r.name} 在哪")
        here, pos, _key = self._here(run)
        if here != stage or max(abs(pos[0] - tile[0]), abs(pos[1] - tile[1])) > 2:
            if self._navigator is None:
                raise _Fail("沒有導航，走不過去")
            self._set_step(run, f"走到 {r.name} 旁邊")
            result = self._navigator.go(
                run.pid, stage, goal=tile, stop=run.stop, note=lambda t: self._set_step(run, t)
            )
            if not result.ok:
                raise _Fail(f"走不到 {r.name} 旁邊：{result.detail or result.reason}")
        rounds = 0
        while True:
            mine = for_receiver(self._bag(run), wants, self._cannot(run))
            if not mine:
                self._note(run, "confirmed", f"交給 {r.name} 的都交完了（{rounds} 輪）")
                return True
            turn = self._take_turn(run, r)
            try:
                if not self._round(run, turn, mine):
                    self._note(run, "unconfirmed", f"{r.name} 背包滿了，換下一個倉庫")
                    return False
            except _Fail as f:
                self._cancel(run)
                self._fail_turn(turn, f.reason)
                raise
            rounds += 1

    def _take_turn(self, run: _Run, r: _Run) -> _Turn:
        self._set_step(run, f"排隊等 {r.name}")
        with self._hub:
            if run.pid not in r.queue:
                r.queue.append(run.pid)
            self._hub.notify_all()
        _stage, _tile, key = self._here(run)
        end = self._clock() + self.t.store * 4
        with self._hub:
            while True:
                if run.stop.is_set():
                    raise _Stop("已停止")
                if r.stop.is_set() or r.stopping:
                    raise _Fail(f"{r.name} 停止收貨了")
                if r.queue and r.queue[0] == run.pid and r.turn is None and r.ready:
                    turn = _Turn(sender=run, receiver=r, key=key)
                    r.turn = turn
                    self._hub.notify_all()
                    return turn
                if self._clock() >= end:
                    raise _Fail(f"排隊等 {r.name} 太久")
                self._hub.wait(0.5)

    def _round(self, run: _Run, turn: _Turn, mine: list[tuple[int, int]]) -> bool:
        """One trade; False when the receiver has no slot for it."""
        r = turn.receiver
        self._await(run, turn, {"armed"}, self.t.invite, f"{r.name} 準備好")
        take = batch(mine, turn.free)
        if not take:
            self._move(turn, "skip")
            return False
        expect = counts(take)
        self._move(turn, "inviting", stacks=len(take), items=expect)
        with self._hub:
            key = r.key
        near = self._cmd(run, "near")
        target = next(
            (o for o in near.get("near") or [] if key and (o.get("id"), o.get("inst")) == key), None
        )
        if target is None:
            raise _Fail(f"旁邊看不到 {r.name}")
        self._set_step(run, f"邀請 {r.name} 交易")
        before = counts(self._bag(run))
        mark = self.events.mark(run.pid)
        reply = self._cmd(run, f"tradeinvite {target['h']}")
        if not reply.get("ok"):
            raise _Fail(f"邀請沒送出：{reply.get('error')}")
        self._event(run, mark, {EV_OPEN}, self.t.invite, "對方接受交易（太遠，或對方開著視窗？）")
        run.trading = True
        self._await(run, turn, {"open"}, self.t.open, "對方打開交易視窗")
        # No more than the window holds: past it the server drops the put the
        # client already made (10 slots), past 40 the client dies.
        take = batch(mine, turn.free, trade_cap(self._cmd(run, "trade")))
        self._set_step(run, "放東西")
        placed: list[tuple[int, int]] = []
        for item, n in take:
            if item in run.refused:
                continue
            reply = self._cmd(run, f"tradeput {item}")
            if not reply.get("ok"):
                error = str(reply.get("error") or "")
                if "cannot be traded" in error:
                    run.refused.add(item)
                    self._note(run, "unconfirmed", f"{self._name(item)} 不能交易，跳過")
                    continue
                if "offer full" in error:
                    break
                raise _Fail(f"放 {self._name(item)} 沒成功：{error}")
            placed.append((item, n))
            self._wait(run, self.t.put_gap)
        expect = counts(placed)
        if not expect:
            # Nothing this round could go: close the empty trade, the next
            # round picks again without the refused items.
            self._cancel(run)
            self._fail_turn(turn, "這輪的東西都不能交易")
            return True
        with self._hub:
            turn.stacks, turn.items = len(placed), expect
        offer = self._cmd(run, "trade").get("offer") or []
        put = counts([(int(o["item"]), int(o["count"])) for o in offer])
        if put != expect:
            raise _Fail("交易欄裡的東西跟要放的不一樣")
        if not self._cmd(run, "tradelock").get("ok"):
            raise _Fail("鎖定沒送出")
        self._move(turn, "put")
        self._await(run, turn, {"locked", "received", "done"}, self.t.step, "對方鎖定")
        self._event(run, mark, {EV_LOCK}, self.t.step, "對方鎖定")
        if not self._cmd(run, "tradeconfirm").get("ok"):
            raise _Fail("確認沒送出")
        self._event(run, mark, {EV_DONE}, self.t.step, "交易完成")
        run.trading = False
        gone = self._came(run, before, expect, -1)
        what = "、".join(f"{self._name(i)} ×{n}" for i, n in expect.items())
        if any(gone.get(i, 0) < n for i, n in expect.items()):
            self._note(run, "unconfirmed", f"交易完成，但背包還看得到部分東西：{what}")
        else:
            self._note(run, "confirmed", f"交給 {r.name}：{what}")
        with run.lock:
            for i, n in expect.items():
                run.sent[i] = run.sent.get(i, 0) + n
        self._set_step(run, f"等 {r.name} 存倉")
        self._await(run, turn, {"done"}, self.t.store, f"{r.name} 存倉")
        return True
