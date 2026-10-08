"""神武玄天塔 module: climbs the tower through hook commands until the game sends
the character out (or a set floor is cleared).

The flow is tthol-hook's local/scripts/tower.py + tower_enter.py (route B, no
battle puppet): 燕飄風 in 玄天之境 -> 九尾狐仙 at the 關 start -> per room, kill
everything (`near`'s dead flag), wait for the bodies to fade, stand off the
exit, step on it and pick a "continue" option -> room 10 changes the map to the
next 關. Fighting follows the battle puppet (services/combat.py). Potions,
buffs and the hero transform are the guard's: the module starts it.

Only one thing reads the hook's event pipe (hook_hub): own hits come in through
`on_attack_packet`.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Callable

from services.api_types import (
    AttackSkillCandidate,
    CombatRule,
    DailyMetric,
    DailySegment,
    DailySummary,
    ItemRule,
    TowerConfig,
    TowerFloor,
    TowerLogEntry,
    TowerRecord,
    TowerAttackReach,
    TowerEstimate,
    TowerSettings,
    TowerStatus,
    TowerView,
)
from services.combat import (
    COMBAT_SECTION,
    AttackSkill,
    Fighter,
    attack_problem,
    load_attack_skills,
    skill_candidates,
)
from services.game_input import leave_game
from services.guard import GuardManager, GuardStore, read_holdings, read_learned, read_stage_id
from services import run_log
from services.box_tidy import BoxTidy, Loot, load_loot
from services.item_rules import KEEP, USE_PERIODIC, load_item_facts
from services.hook_caps import FEATURES, HookCaps
from services.hook_cmd import CommandChannel, NoReply, PipeBusy, PipeGone
from services.tower import (
    FOX_AVOID,
    FOX_STAGE,
    LEAVE,
    LOBBY_STAGE,
    ORB,
    ROOMS,
    ENTER,
    SKIP_BACK,
    SKIP_LEVEL,
    SKIP_OFFER,
    SKIP_ORBS,
    YAN_CHALLENGE,
    skip_confirm,
    skip_pick,
    TOO_LOW,
    YAN,
    YAN_AVOID,
    YAN_WANT,
    TowerStage,
    attack_reach,
    floor_gates,
    load_tower,
    pick_option,
)

log = logging.getLogger("tthol.tower")

TOWER_SECTION = "tower"
RECORD_SECTION = "tower.record"
ATTACK_PACKET = 0x43  # an attack landing: target key, attacker key, skill, hits
# a cast starting: 10 <caster key> <u32 skill code> <tile x> <tile y> <target key> 01
CAST_START_PACKET = 0x10
TOWER_COMMANDS = FEATURES["daily.tower"]
TILE_PX = 40
LOGOUT_WAIT = 6.0  # after clicking 登出遊戲, for the character to leave the game
POTION_EVERY = 1.0  # count the whitelisted potions this often while fighting
LOG_KEEP = 200

STEP = 0.05  # between two fight decisions (the puppet runs every 45 ms)
WAIT_BODIES = 0.3
WAIT_MAP = 0.5  # own character not built yet after a map change
IDLE_SWEEP = 3  # empty looks in a row before walking the spawn points
SWEEP_WAIT = 8.0  # per spawn point, for a monster to show up
EXIT_TRIES = 8  # exit bumps (one a tick) before giving the room another sweep
EXIT_DIALOG = 2.0  # after stepping on the exit, for its dialog to open
EXIT_SETTLE = 6.0  # after the exit dialog closed, for the teleport to show
DEATH_CONFIRM = 3.0  # HP 0 this long in a row is a death; a map load reads 0 a moment
BODIES_MAX = 15.0  # a body that has not faded by then is not waited for
WALK_WAIT = 6.0
TALK_WAIT = 40.0
FOX_RETALK = 6.0  # after a fox talk, give the teleport this long before talking again
CAPS_EVERY = 30.0  # re-read the hook's command list this often for the view
BUFF_WAIT = 30.0  # at the 關 start, wait at most this long for the guard's buffs
PIPE_RETRIES = 5  # a command pipe that is briefly unavailable (map load) is retried
# missions 18913-18919 (神武玄天塔-辰星關 ...): sestage 1704-1710 in order.
STAGE_NAMES = ("辰星關", "太白關", "熒惑關", "歲星關", "鎮星關", "冽星關", "颶星關")
TITLE = "神武玄天塔"


def own_tile(st: dict, objs: list[dict]) -> tuple[int, int]:
    """The character's tile from its own `near` entry (pixels / 40).

    `status`'s tile lags a teleport inside one map (九尾狐仙 -> room 1, the
    exits): it only moves on the next step, so it is the fallback only.
    """
    me = next((o for o in objs if o.get("h") == st.get("self")), None)
    if me is not None:
        return me["x"] // TILE_PX, me["y"] // TILE_PX
    return st["tile"][0], st["tile"][1]


def _stage_name(floor: int) -> str:
    i = (floor - 1) // ROOMS
    return STAGE_NAMES[i] if 0 <= i < len(STAGE_NAMES) else TITLE


def _ladder(
    top: int, floor: int | None, state: str | None, stop_floor: int | None, skipped: int = 0
) -> list[DailySegment]:
    """The ten floors of the 關 being climbed (or last cleared): cleared up to
    `top` (passed with 狐光靈珠 up to `skipped`), the current floor, the stop
    floor; where it stopped on an error."""
    ref = floor or (top + 1 if state == "stopped" else top) or None
    if ref is None:
        return []
    first = (ref - 1) // ROOMS * ROOMS + 1
    cells: list[DailySegment] = []
    for f in range(first, first + ROOMS):
        if f <= skipped:
            cells.append("skipped")
        elif f <= top:
            cells.append("done")
        elif floor is not None and f == floor:
            cells.append("current")
        elif state == "stopped" and f == top + 1:
            cells.append("error")
        elif f == stop_floor:
            cells.append("target")
        else:
            cells.append("empty")
    return cells


def _target_metric(top: int, stop_floor: int | None) -> DailyMetric:
    return DailyMetric(label="通過 / 目標", value=f"{top} / {stop_floor or '—'}", highlight=True)


HIT_OFFSET = 92  # 命中, from the HP base (knowledge.json)
LEVEL_OFFSET = -36  # 等級


def read_hit_level(pm, hp_addr, _compat_mode) -> tuple[int, int] | None:
    """(命中, 等級) for WorkerManager.read_locked (both outside the swapped HP/MP words)."""
    hit = pm.read_int(hp_addr + HIT_OFFSET)
    level = pm.read_int(hp_addr + LEVEL_OFFSET)
    if not (0 < hit < 1_000_000 and 0 < level < 1000):
        return None
    return hit, level


class _Done(Exception):
    """Ends a run. `complete`: the climb is over for the day (stop floor, sent
    out); anything else (an error, a potion stop) is picked up again later."""

    def __init__(self, reason: str, phase: str = "info", complete: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.phase = phase
        self.complete = complete


def _hp_summary(run: _Run) -> str:
    """ "，最低血 39%，單下最多 -9,512（29%）" for the floor so far (run_log), or ""."""
    s = run_log.floor_stats(run.pid) if run.pid is not None else None
    if s is None:
        return ""
    text = f"，最低血 {s.low_pct}%"
    if s.max_drop:
        text += f"，單下最多 -{s.max_drop:,}（{s.max_drop_pct}%）"
    return text


class _Line:
    def __init__(self, id_: int, ts: float, phase: str, text: str) -> None:
        self.id, self.ts, self.phase, self.text = id_, ts, phase, text


class _Run:
    def __init__(self, name: str, combat: CombatRule, config: TowerConfig) -> None:
        self.name = name
        self.pid: int | None = None  # for the run record
        self.fighter = Fighter(combat, log, cat="tower")
        self.config = config
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.log: deque[_Line] = deque(maxlen=LOG_KEEP)
        self.next_id = 0
        self.step: str | None = None
        self.problem: str | None = None
        self.stage: TowerStage | None = None
        self.stage_name: str | None = None
        self.room: int | None = None
        self.killed: set[tuple[int, int]] = set()
        self.kills = 0
        self.room_started: float | None = None
        self.run_started: float | None = None
        self.floors: list[TowerFloor] = []
        self.cleared = False  # cleared at least one floor this run
        self.passed_room: int | None = None  # the room last logged as passed
        self.staged = False
        self.force_exit = False  # the spawn points were empty: try the exit anyway
        self.room_seen: int | None = None  # another room seen once: confirmed on the next look
        self.exit_try = 0  # exit bumps in this room
        self.exit_closed_at: float | None = None  # clock the exit dialog went through
        self.exit_leave: tuple[bool, str | None] | None = None  # (leave, potions short)
        self.bodies_since: float | None = None  # clock the wait for bodies to fade began
        self.idle = 0
        self.zero_hp_at: float | None = None  # clock HP first read 0
        self.fox_at = -FOX_RETALK  # clock of the last fox talk
        self.potions_at = -POTION_EVERY  # clock of the last potion count
        self.start_since: float | None = None  # clock we got to the 關 start
        self.navigated = False  # walked to 玄天之境 from elsewhere (once a run)
        self.supplied = False  # 補給 ran (or was skipped) before the first floor
        self.skip_tried = False  # 狐光靈珠 skips looked at (once a run, before entering)
        self.moving = False  # the navigator is walking us to 玄天之境
        self.user_stop = False  # stop() was called (the 日常 tab or the queue)
        self.ended: str | None = None  # why it stopped, once it has
        # done: the climb is over for the day; error: stopped on a problem
        # (potions short too); user: stopped by hand.
        self.outcome: str | None = None

    @property
    def combat(self) -> CombatRule:
        return self.fighter.rule

    @combat.setter
    def combat(self, rule: CombatRule) -> None:
        self.fighter.rule = rule


class TowerManager:
    def __init__(
        self,
        guard: GuardManager,
        read_locked: Callable[[int, Callable], object],
        character_name: Callable[[int], str | None],
        channel: CommandChannel | None = None,
        store: GuardStore | None = None,
        attack_skills: Callable[[], dict[tuple[int, int], AttackSkill]] = load_attack_skills,
        tower: Callable[[int], TowerStage | None] = load_tower,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        wait: Callable[[threading.Event, float], bool] = lambda ev, secs: ev.wait(secs),
        leave_game: Callable[[int], bool] = leave_game,
        navigator=None,  # services.navigator.Navigator: walks to 玄天之境 from elsewhere
        hook_caps: HookCaps | None = None,  # shared with the other modules
        supply=None,  # services.supply.SupplyManager: a 補給 trip before each run
        loot: Callable[[], Loot] = load_loot,  # 寶箱整理: what the 關 boxes hold
    ) -> None:
        self._supply = supply
        if supply is not None:
            supply.add_host("神武玄天塔 · 出發前")
            supply.add_host("神武玄天塔 · 寶箱整理")
        self._load_loot = loot
        self._loot: Loot | None = None
        self._item_facts = None  # services.item_rules.load_item_facts, loaded on first use
        self._leave_game = leave_game
        self._navigator = navigator
        self._guard = guard
        self._sleep = wait
        self._read_locked = read_locked
        self._character_name = character_name
        self._channel = channel or CommandChannel()
        self._store = store or GuardStore()
        self._load_attack_skills = attack_skills
        self._attack_skills: dict[tuple[int, int], AttackSkill] | None = None
        self._load_tower = tower
        self._towers: dict[int, TowerStage | None] = {}
        self._gates: list[int] | None = None
        self._clock = clock
        self._wall = wall
        self._runs: dict[int, _Run] = {}
        self._lock = threading.Lock()
        self._hook_caps = hook_caps or HookCaps(self._channel, clock)

    # -- API -----------------------------------------------------------------

    def view(self, pid: int) -> TowerView:
        name = self._character_name(pid)
        if not name:
            return TowerView(status=TowerStatus(running=False))
        return TowerView(
            status=self.status(pid),
            combat=self._store.load_section(name, COMBAT_SECTION, CombatRule),
            config=self._store.load_section(name, TOWER_SECTION, TowerConfig),
            record=self._store.load_section(name, RECORD_SECTION, TowerRecord),
            skills=self._skill_candidates(pid),
            hook_ready=self._hook_ready(pid),
        )

    def save_settings(self, pid: int, settings: TowerSettings) -> TowerSettings | None:
        name = self._character_name(pid)
        if not name:
            return None
        self._store.save_section(name, COMBAT_SECTION, settings.combat)
        self._store.save_section(name, TOWER_SECTION, settings.config)
        with self._lock:
            run = self._runs.get(pid)
        if run is not None and run.name == name:
            with run.lock:
                run.combat, run.config = settings.combat, settings.config
        return settings

    def config_problem(self, name: str) -> str | None:
        """Why `name`'s saved settings cannot climb, or None (no game needed:
        the batch dispatch asks before logging the character in)."""
        problem = attack_problem(self._store.load_section(name, COMBAT_SECTION, CombatRule))
        if problem:
            return problem
        if not self._store.load(name).potion.hp_items:
            return "補水的體力白名單是空的：塔裡不能回城，先在「輔助」設定補水"
        return None

    def done_for(self, name: str) -> bool:
        """`name` finished today's tower (done_today by name)."""
        return self._done_today(self._today_record(name))

    def mark_done(self, name: str, done: bool) -> None:
        """By hand: today's tower done (finished in game, or on another PC), or
        not. The floors already recorded today are kept."""
        record = self._today_record(name) or TowerRecord(date=self._today())
        record = record.model_copy(
            update={"done": done, "ended": "手動標記今日完成" if done else "取消手動標記"}
        )
        self._store.save_section(name, RECORD_SECTION, record)

    def start(self, pid: int) -> tuple[bool, str | None]:
        name = self._character_name(pid)
        if not name:
            return False, "角色還沒定位"
        problem = self.config_problem(name)
        if problem:
            return False, problem
        try:
            missing = self._missing_commands(pid)
        except PipeGone:
            return False, "這個遊戲視窗沒有 hook 指令通道"
        except (PipeBusy, NoReply):
            return False, "hook 暫時沒有回應（還在登入或換地圖？），稍後再開始"
        if missing:
            return False, f"這個 hook 缺少登塔要用的指令：{'、'.join(missing)}"
        if not self._guard.running(pid):
            g = self._guard.start(pid)
            if not g.ok:
                return False, f"常駐守護開不起來：{g.reason}"
        with self._lock:
            run = self._runs.get(pid)
            if run is not None and run.thread is not None and run.thread.is_alive():
                if not run.stop.is_set():
                    return True, None
                run.thread.join(timeout=2.0)
            run = _Run(
                name,
                self._store.load_section(name, COMBAT_SECTION, CombatRule),
                self._store.load_section(name, TOWER_SECTION, TowerConfig),
            )
            run.run_started = self._wall()
            run.pid = pid
            self._runs[pid] = run
            run.thread = threading.Thread(
                target=self._loop, args=(pid, run), daemon=True, name=f"tower-{pid}"
            )
            run.thread.start()
        log.info("tower started pid=%d", pid, extra={"cat": "tower"})
        return True, None

    def tidy_now(self, pid: int) -> tuple[bool, str | None]:
        """寶箱整理 by hand, without a climb: the same tidy a run that ended the
        normal way does. Its lines show in the tower log; 停止 stops it."""
        name = self._character_name(pid)
        if not name:
            return False, "角色還沒定位"
        try:
            missing = [
                c
                for c in ("status", "bag", "use")
                if c not in (self._hook_caps.get(pid, 60.0) or ())
            ]
        except PipeGone:
            return False, "這個遊戲視窗沒有 hook 指令通道"
        except (PipeBusy, NoReply):
            return False, "hook 暫時沒有回應（還在登入或換地圖？），稍後再試"
        if missing:
            return False, f"這個 hook 缺少整理要用的指令：{'、'.join(missing)}"
        with self._lock:
            run = self._runs.get(pid)
            if run is not None and run.thread is not None and run.thread.is_alive():
                return False, "登塔或整理還在進行（登塔正常結束時，勾了自動整理就會整理）"
            run = _Run(
                name,
                self._store.load_section(name, COMBAT_SECTION, CombatRule),
                self._store.load_section(name, TOWER_SECTION, TowerConfig),
            )
            run.run_started = self._wall()
            run.pid = pid
            self._runs[pid] = run
            run.thread = threading.Thread(
                target=self._tidy_only, args=(pid, run), daemon=True, name=f"tidy-{pid}"
            )
            run.thread.start()
        return True, None

    def _tidy_only(self, pid: int, run: _Run) -> None:
        try:
            self._tidy(pid, run)
        finally:
            with run.lock:
                run.ended = "寶箱整理結束"
                run.outcome = "user"
            run.stop.set()

    def stop(self, pid: int) -> None:
        with self._lock:
            run = self._runs.get(pid)
        if run is not None:
            if not run.stop.is_set():
                run.user_stop = True
            run.stop.set()

    def forget(self, pid: int) -> None:
        """Another character on this window: stop the old one's climb, drop its log."""
        self.stop(pid)
        with self._lock:
            self._runs.pop(pid, None)

    def shutdown(self) -> None:
        with self._lock:
            runs = list(self._runs.values())
        for run in runs:
            run.stop.set()

    def status(self, pid: int) -> TowerStatus:
        with self._lock:
            run = self._runs.get(pid)
        if run is None:
            return TowerStatus(running=False, character=self._character_name(pid))
        running = run.thread is not None and run.thread.is_alive() and not run.stop.is_set()
        with run.lock:
            stage = run.stage
            return TowerStatus(
                running=running,
                character=run.name,
                step=run.step if running else None,
                problem=run.problem if running else None,
                stage_id=stage.stage_id if stage else None,
                stage_name=run.stage_name,
                floor=stage.floor(run.room) if stage and run.room else None,
                room=run.room,
                kills=run.kills,
                expect=stage.expect.get(run.room, 0) if stage and run.room else 0,
                room_started=run.room_started,
                run_started=run.run_started,
                floors=list(run.floors),
                log=[
                    TowerLogEntry(id=line.id, ts=line.ts, phase=line.phase, text=line.text)
                    for line in reversed(run.log)
                ],
            )

    def on_attack_packet(self, pid: int, raw: bytes, _ts: float, own_key: bytes | None) -> None:
        """0x43 (an attack landing): our own hits, for the running fighter."""
        with self._lock:
            run = self._runs.get(pid)
        if run is not None:
            run.fighter.on_attack(raw, own_key, self._clock())

    def on_cast_packet(self, pid: int, raw: bytes, _ts: float, own_key: bytes | None) -> None:
        """0x10 (a cast starting): the server took one of our casts."""
        with self._lock:
            run = self._runs.get(pid)
        if run is not None:
            run.fighter.on_cast(raw, own_key, self._clock())

    def estimate(self, pid: int, apply: bool = True) -> TowerEstimate:
        """Expected top floor from the current hit and level; with `apply`, it
        becomes the stop floor (the user's "stop before the floor you lose on")."""
        name = self._character_name(pid)
        if not name:
            return TowerEstimate(ok=False, reason="角色還沒定位")
        try:
            hit_level = self._read_locked(pid, read_hit_level)
        except Exception:
            hit_level = None
        if not hit_level:
            return TowerEstimate(ok=False, reason="讀不到角色的命中")
        hit, level = hit_level
        if self._gates is None:
            self._gates = floor_gates()
        stages = [self._tower(s) for s in sorted(set(FOX_STAGE.values()))]
        attacks = self._attacks(pid, name)
        reach, each = attack_reach(hit, level, [s for s in stages if s], self._gates, attacks)
        missing = self._guard.missing_buffs(pid)
        applied = False
        if apply and reach.max_floor > 0:
            config = self._store.load_section(name, TOWER_SECTION, TowerConfig)
            config = config.model_copy(update={"stop_floor": reach.max_floor})
            self._store.save_section(name, TOWER_SECTION, config)
            with self._lock:
                run = self._runs.get(pid)
            if run is not None and run.name == name:
                with run.lock:
                    run.config = config
            applied = True
        return TowerEstimate(
            ok=True,
            hit=hit,
            level=level,
            max_floor=reach.max_floor,
            blocker=reach.blocker,
            attacks=[
                TowerAttackReach(
                    name=a.name, rate=a.rate, hit=a.hit, max_floor=a.max_floor, blocker=a.blocker
                )
                for a in sorted(each, key=lambda a: (-a.max_floor, -a.rate))
            ]
            if attacks
            else [],
            missing_buffs=missing,
            applied=applied,
        )

    def _attacks(self, pid: int, name: str) -> list[tuple[str, float]]:
        """(name, hit multiplier) of each attack the combat settings use, at the
        learned level; a skill not learned is left out."""
        combat = self._store.load_section(name, COMBAT_SECTION, CombatRule)
        try:
            learned = self._read_locked(pid, read_learned) or {}
        except Exception:
            learned = {}
        out: list[tuple[str, float]] = [("普攻", 1.0)] if combat.basic else []
        for mid in [combat.opener, *combat.rotation]:
            level = learned.get(mid) if mid else None
            skill = self._defs().get((mid, level)) if level else None
            if skill is not None and all(n != skill.name for n, _ in out):
                out.append((skill.name, skill.hit))
        return out

    # -- 日常 module (services/daily.py) ----------------------------------------

    key = "tower"
    title = TITLE

    def running(self, pid: int) -> bool:
        with self._lock:
            run = self._runs.get(pid)
        return run is not None and run.thread is not None and run.thread.is_alive()

    def outcome(self, pid: int) -> tuple[str, str] | None:
        """How the last run ended, (done | error | user, reason); None while it
        runs or when there was none."""
        with self._lock:
            run = self._runs.get(pid)
        if run is None or self.running(pid):
            return None
        with run.lock:
            return (run.outcome, run.ended or "") if run.outcome else None

    def done_today(self, pid: int) -> bool:
        name = self._character_name(pid)
        return bool(name) and self.done_for(name)

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(self._wall()))

    def _today_record(self, name: str) -> TowerRecord | None:
        record = self._store.load_section(name, RECORD_SECTION, TowerRecord)
        return record if record.date == self._today() else None

    @staticmethod
    def _done_today(record: TowerRecord | None) -> bool:
        if record is None:
            return False
        return record.done if record.done is not None else record.top_floor > 0

    def summary(self, pid: int) -> DailySummary:
        """The overview card for this character's tower."""
        name = self._character_name(pid)
        base = DailySummary(module=self.key, title=self.title, state="idle")
        if not name:
            return base
        record = self._today_record(name)
        stop_floor = self._store.load_section(name, TOWER_SECTION, TowerConfig).stop_floor
        top = record.top_floor if record else 0
        skipped = record.skipped_to if record else 0
        done_today = self._done_today(record)
        with self._lock:
            run = self._runs.get(pid)
        live = run is not None and run.name == name and self.running(pid)
        if run is not None and run.name == name and not live and run.outcome is None:
            run = None  # never got going
        if run is not None and (run.name != name or self._day(run.run_started) != self._today()):
            run = None  # another character in this client, or yesterday's
        if run is None:
            state = "done_today" if done_today else "idle"
            return base.model_copy(
                update={
                    "state": state,
                    "headline": f"第 {top} 層" if top else None,
                    "where": (
                        "今天打完了"
                        if done_today
                        else f"今天打到第 {top} 層，可接續"
                        if top
                        else None
                    ),
                    "segments": _ladder(top, None, None, stop_floor, skipped),
                    "step": record.ended if record else None,
                    "metrics": [_target_metric(top, stop_floor)] if top else [],
                    "result": f"{top} 層" if top else None,
                    "done_today": done_today,
                }
            )
        with run.lock:
            stage, room = run.stage, run.room
            floor = stage.floor(room) if stage and room else None
            top = max([top, *(f.floor for f in run.floors)])
            skipped = max([skipped, *(f.floor for f in run.floors if f.skipped)])
            if live:
                state = "moving" if run.moving else "running"
                step = run.problem or run.step or "準備中"
                kills, expect = run.kills, stage.expect.get(room, 0) if stage and room else 0
                room_started = run.room_started
            else:
                state = {"done": "done", "error": "stopped"}.get(run.outcome, "idle")
                step = run.ended
        where = None
        if live and run.moving:
            where = "前往玄天之境"
        elif live and floor is not None:
            where = f"{_stage_name(floor)} · 第 {room} 房"
        elif live:
            where = "玄天之境"
        elif top:
            where = f"{_stage_name(top)} · 通過第 {top} 層"
        metrics = []
        if live and floor is not None:
            metrics = [
                DailyMetric(label="擊倒", value=f"{kills} / {expect}"),
                DailyMetric(label="這一房", since=room_started),
            ]
        if top or stop_floor:
            metrics.append(_target_metric(top, stop_floor))
        shown = floor if live and floor is not None else top
        return base.model_copy(
            update={
                "state": state,
                "headline": f"第 {shown} 層" if shown else None,
                "where": where,
                "segments": _ladder(top, floor if live else None, state, stop_floor, skipped),
                "step": step,
                "metrics": metrics,
                "result": f"{top} 層" if top else None,
                "done_today": done_today,
            }
        )

    @staticmethod
    def _day(ts: float | None) -> str | None:
        return time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else None

    # -- helpers ---------------------------------------------------------------

    def _defs(self) -> dict[tuple[int, int], AttackSkill]:
        if self._attack_skills is None:
            self._attack_skills = self._load_attack_skills()
        return self._attack_skills

    def _tower(self, stage_id: int) -> TowerStage | None:
        if stage_id not in self._towers:
            self._towers[stage_id] = self._load_tower(stage_id)
        return self._towers[stage_id]

    def _missing_commands(self, pid: int, max_age: float = 0.0) -> list[str]:
        """Commands the module needs that the hook's `caps` does not list.

        Raises PipeGone / PipeBusy / NoReply when the hook cannot be asked.
        """
        have = self._hook_caps.get(pid, max_age) or frozenset()
        return [c for c in TOWER_COMMANDS if c not in have]

    def _hook_ready(self, pid: int) -> bool:
        """For the view, polled every second: cached, and a missing pipe is just "no"."""
        try:
            return self._missing_commands(pid, max_age=CAPS_EVERY) == []
        except (PipeGone, PipeBusy, NoReply):
            cached = self._hook_caps.cached(pid)
            return cached is not None and not [c for c in TOWER_COMMANDS if c not in cached]

    def _skill_candidates(self, pid: int) -> list[AttackSkillCandidate]:
        try:
            learned = self._read_locked(pid, read_learned) or {}
        except Exception:
            learned = {}
        return skill_candidates(learned, self._defs())

    def _note(self, run: _Run, phase: str, text: str) -> None:
        with run.lock:
            run.next_id += 1
            run.log.append(_Line(run.next_id, self._wall(), phase, text))
            floor = run.stage.floor(run.room) if run.stage and run.room else None
        run_log.note("tower", run.pid, run.name, text, phase=phase, floor=floor)

    def _set_step(self, run: _Run, step: str) -> None:
        with run.lock:
            run.step, run.problem = step, None

    def _cmd(self, pid: int, run: _Run, line: str) -> dict:
        for _ in range(PIPE_RETRIES):
            if run.stop.is_set():
                raise _Done("已停止")
            try:
                return self._channel.send(pid, line)
            except PipeGone:
                # Gone for a moment while the client loads a map; for good when
                # the game closed.
                self._wait(run, 1.0)
            except (PipeBusy, NoReply):
                return {"ok": False, "error": "busy"}
        raise _Done("找不到 hook 指令通道（遊戲關了，或 hook 沒有注入）", "error")

    def _wait(self, run: _Run, secs: float) -> None:
        if self._sleep(run.stop, secs):
            raise _Done("已停止")

    # -- the loop ----------------------------------------------------------------

    def _loop(self, pid: int, run: _Run) -> None:
        self._note(run, "info", "開始登塔")
        ended, phase, complete = "已停止", "info", False
        try:
            while True:
                self._wait(run, self._tick(pid, run))
        except _Done as done:
            ended, phase, complete = done.reason, done.phase, done.complete
            if complete and run.config.tidy_boxes:
                self._tidy(pid, run)
        except Exception:
            log.exception("tower loop failed pid=%d", pid, extra={"cat": "tower"})
            ended, phase = "登塔出錯停止（詳見診斷紀錄）", "error"
        finally:
            if complete:
                outcome = "done"
            elif run.user_stop and phase != "error":
                outcome = "user"
            else:
                outcome = "error"
            with run.lock:
                run.ended, run.outcome, run.moving = ended, outcome, False
            run.stop.set()
            # A floor left unfinished (a death, a stop): how hard it hit so far.
            hp = _hp_summary(run) if run.room is not None and run.passed_room != run.room else ""
            self._note(run, phase, f"登塔結束：{ended}" + (f"（這層{hp[1:]}）" if hp else ""))
            self._save_record(run, ended)
            log.info("tower stopped pid=%d: %s", pid, ended, extra={"cat": "tower"})

    def _save_record(self, run: _Run, ended: str) -> None:
        date = time.strftime("%Y-%m-%d", time.localtime(run.run_started or self._wall()))
        old = self._store.load_section(run.name, RECORD_SECTION, TowerRecord)
        top = max((f.floor for f in run.floors), default=0)
        skipped = max((f.floor for f in run.floors if f.skipped), default=0)
        done = run.outcome == "done"
        if old.date == date:
            top = max(top, old.top_floor)
            skipped = max(skipped, old.skipped_to)
            done = done or bool(old.done)
        self._store.save_section(
            run.name,
            RECORD_SECTION,
            TowerRecord(date=date, top_floor=top, ended=ended, done=done, skipped_to=skipped),
        )

    def _tick(self, pid: int, run: _Run) -> float:
        st = self._cmd(pid, run, "status")
        if not st.get("ok") or not isinstance(st.get("tile"), list):
            with run.lock:
                run.problem = "等角色載入（換地圖中）"
            return WAIT_MAP
        hp = st.get("hp") or [0, 0]
        if hp[0] <= 0:
            now = self._clock()
            if run.zero_hp_at is None:
                run.zero_hp_at = now
            if now - run.zero_hp_at >= DEATH_CONFIRM:
                raise _Done("角色死亡", "error")
            return WAIT_MAP  # a map load reads 0 for a moment: look again
        run.zero_hp_at = None
        try:
            stage = self._read_locked(pid, read_stage_id)
        except Exception:
            stage = None
        if stage is None:
            return WAIT_MAP
        stage_id, stage_name = stage
        if not run.supplied:
            run.supplied = True
            if self._supply is not None and self._tower(stage_id) is None:
                # Not mid-climb: a run starts here.
                self._resupply(pid, run)
                return WAIT_MAP
        if stage_id == LOBBY_STAGE:
            if run.cleared:
                raise _Done("被送出塔：等級不夠進下一層，或已經是最後一關", complete=True)
            return self._enter_lobby(pid, run)
        tower = self._tower(stage_id)
        if tower is None:
            return self._go_to_lobby(pid, run, stage_name)
        if run.stage is None or run.stage.stage_id != stage_id:
            with run.lock:
                run.stage, run.stage_name, run.room = tower, stage_name, None
            self._note(
                run, "info", f"進入{stage_name}（第 {tower.floor(1)}–{tower.floor(ROOMS)} 層）"
            )
        objs = self._near(pid, run)
        tile = own_tile(st, objs)
        if tower.at_start(tile):
            return self._at_start(pid, run, tower)
        run.start_since = None
        return self._room_tick(pid, run, tower, st, tile, objs)

    def _tidy(self, pid: int, run: _Run) -> None:
        """寶箱整理 after a run that ended the normal way (services.box_tidy).
        A stop or a failure here does not undo the run: it stays done."""
        self._set_step(run, "寶箱整理")
        self._note(run, "info", "開始整理寶箱")
        try:
            if self._loot is None:
                self._loot = self._load_loot()
            potion = self._store.load(run.name).potion
            result = BoxTidy(
                cmd=lambda line: self._cmd(pid, run, line),
                wait=lambda secs: self._wait(run, secs),
                clock=self._clock,
                note=lambda phase, text: self._note(run, phase, text),
                step=lambda text: self._set_step(run, text),
                loot=self._loot,
                supplies=frozenset(potion.hp_items) | frozenset(potion.mp_items),
                store_trip=self._store_trip(pid, run) if self._supply is not None else None,
                auto_use=lambda items: self._auto_use(pid, run, items),
            ).run()
        except _Done as done:
            self._note(run, "error", f"寶箱整理中斷：{done.reason}")
            return
        except Exception:
            log.exception("box tidy failed pid=%d", pid, extra={"cat": "tower"})
            self._note(run, "error", "寶箱整理出錯（詳見診斷紀錄）")
            return
        loot = self._loot
        opened = sum(result.opened.values())
        parts = [f"開了 {opened} 個寶箱"] if opened else ["沒有寶箱可開"]
        if result.collected:
            parts.append("蒐藏 " + "、".join(loot.name(i) for i in result.collected))
        if result.auto:
            parts.append("設成自動使用 " + "、".join(loot.name(i) for i in result.auto))
        if result.stored:
            parts.append(f"存倉 {result.stored} 個")
        summary = "，".join(parts)
        if result.problem:
            self._note(run, "error", f"寶箱整理停下：{result.problem}（{summary}）")
        else:
            self._note(run, "confirmed", f"寶箱整理完成：{summary}")

    def _auto_use(self, pid: int, run: _Run, items: list[int]) -> list[int]:
        """道具處置 自動使用 (use_periodic) for these box potions, where the item
        allows it and the user set no rule of their own; the running guard
        picks the rules up at once."""
        if self._item_facts is None:
            self._item_facts = load_item_facts()
        rules = self._store.load_items(run.name)
        new: dict[int, ItemRule] = {}
        for item_id in items:
            fact = self._item_facts(item_id)
            if fact is None or USE_PERIODIC not in fact.actions:
                continue
            old = rules.items.get(item_id)
            if old is not None and old.action != KEEP:
                continue
            new[item_id] = ItemRule(action=USE_PERIODIC)
        if new:
            saved = rules.model_copy(update={"items": {**rules.items, **new}})
            if self._guard.set_items(pid, saved) is None:
                self._store.save_items(run.name, saved)
        return list(new)

    def _store_trip(self, pid: int, run: _Run):
        def trip(want: dict[int, int]):
            with run.lock:
                run.moving = True
            try:
                return self._supply.run(
                    pid,
                    run.stop,
                    note=lambda text: self._set_step(run, text),
                    host="神武玄天塔 · 寶箱整理",
                    store_only=want,
                )
            finally:
                with run.lock:
                    run.moving = False

        return trip

    def _resupply(self, pid: int, run: _Run) -> None:
        """補給 before the climb, every run (user, 2026-10-07): sell, store, buy."""
        self._set_step(run, "補給")
        self._note(run, "info", "出發前先補給")
        with run.lock:
            run.moving = True
        try:
            result = self._supply.run(
                pid, run.stop, note=lambda text: self._set_step(run, text), host="神武玄天塔"
            )
        finally:
            with run.lock:
                run.moving = False
        if result.reason == "stopped":
            raise _Done("已停止")
        if result.reason == "nothing":
            return
        if result.reason == "short":
            raise _Done(f"補給沒補齊：{result.detail}", "error")
        if result.reason == "unsold":
            raise _Done(f"補給停下：{result.detail}", "error")
        if not result.ok:
            # A failed trip (no shop reachable, an old hook) does not block the
            # climb: the potion floors still guard it.
            self._note(run, "error", f"補給沒完成，照常登塔：{result.detail}")
            return
        self._note(run, "confirmed", f"補給完成：{result.detail}")

    def _go_to_lobby(self, pid: int, run: _Run, stage_name: str) -> float:
        """Started somewhere else: walk to 玄天之境 once, before any floor."""
        if self._navigator is None or run.navigated or run.floors:
            raise _Done(f"不在玄天之境或玄天塔裡（{stage_name}）", "error")
        run.navigated = True
        self._set_step(run, f"從{stage_name}前往玄天之境")
        self._note(run, "info", f"從{stage_name}導航到玄天之境")
        with run.lock:
            run.moving = True
        try:
            # A buff cast stops the walk short of the exit: none while walking.
            with self._guard.quiet(pid):
                result = self._navigator.go(
                    pid, LOBBY_STAGE, stop=run.stop, note=lambda text: self._set_step(run, text)
                )
        finally:
            with run.lock:
                run.moving = False
        if result.reason == "stopped":
            raise _Done("已停止")
        if not result.ok:
            raise _Done(f"走不到玄天之境：{result.detail}", "error")
        self._note(run, "info", "抵達玄天之境")
        return WAIT_MAP

    def _at_start(self, pid: int, run: _Run, tower: TowerStage) -> float:
        """By 九尾狐仙: let the guard put its buffs up, then talk into room 1."""
        now = self._clock()
        if run.start_since is None:
            run.start_since = now
        if now - run.fox_at < FOX_RETALK:
            return WAIT_MAP  # the last talk may still be teleporting us
        if self._guard.buffs_pending(pid) and now - run.start_since < BUFF_WAIT:
            self._set_step(run, "等守護補完 buff 再找九尾狐仙")
            return WAIT_MAP
        fox = next((o for o in self._near(pid, run) if o.get("id") in FOX_STAGE), None)
        if fox is None:
            self._set_step(run, "走向九尾狐仙")
            self._walk(pid, run, tower.fox)
            return WAIT_MAP
        self._set_step(run, "和九尾狐仙對話，進第一房")
        run.fox_at = self._clock()
        r = self._talk(pid, run, fox, (), FOX_AVOID)
        self._note(run, "sent" if r in ("closed", "map") else "error", f"和九尾狐仙對話：{r}")
        return WAIT_MAP

    def _near(self, pid: int, run: _Run) -> list[dict]:
        r = self._cmd(pid, run, "near")
        return r.get("objects") or [] if r.get("ok") else []

    def _enter_lobby(self, pid: int, run: _Run) -> float:
        self._set_step(run, "在玄天之境找燕飄風進塔")
        yan = next((o for o in self._near(pid, run) if o.get("id") == YAN), None)
        if yan is None:
            self._walk(pid, run, (31, 19))  # 燕飄風 stands about here
            yan = next((o for o in self._near(pid, run) if o.get("id") == YAN), None)
            if yan is None:
                raise _Done("玄天之境找不到燕飄風", "error")
        r = None
        if not run.skip_tried:
            run.skip_tried = True
            r = self._skip(pid, run, yan)
        if r != "map":
            self._set_step(run, "和燕飄風對話進塔")
            r = self._talk(pid, run, yan, YAN_WANT, YAN_AVOID)
        if r != "map":
            if any(f.skipped for f in run.floors):
                # Skipped up to a 關 the level does not open: nothing left to climb.
                top = max(f.floor for f in run.floors)
                raise _Done(f"用狐光靈珠略過到第 {top} 層，等級不夠進下一關", complete=True)
            raise _Done(f"和燕飄風對話沒有進塔（{r}）", "error")
        self._note(run, "sent", "和燕飄風對話，進塔")
        return WAIT_MAP

    def _orbs(self, pid: int) -> int | None:
        """狐光靈珠 in the bag (what the dialog's item check counts)."""
        try:
            held = self._read_locked(pid, read_holdings)
        except Exception:
            held = None
        return held[0].get(ORB, 0) if held else None

    def _skip(self, pid: int, run: _Run, yan: dict) -> str | None:
        """狐光靈珠: skip 關 up to config.skip_to before the day's first entry,
        one talk per 關, as far as level and orbs allow (user, 2026-10-06); the
        normal entry follows. "map" when a talk already took us in."""
        target = run.config.skip_to
        if not target:
            return None
        record = self._today_record(run.name)
        passed = record.top_floor if record else 0
        if passed % ROOMS:
            return None  # a 關 is started today: the game skips only unstarted ones
        for k in range(passed // ROOMS, min(target, len(SKIP_LEVEL))):
            name = STAGE_NAMES[k]
            try:
                hit_level = self._read_locked(pid, read_hit_level)
            except Exception:
                hit_level = None
            if hit_level and hit_level[1] < SKIP_LEVEL[k]:
                self._note(
                    run,
                    "info",
                    f"等級 {hit_level[1]} 不到 {SKIP_LEVEL[k]}，不能略過{name}，從這關開始打",
                )
                return None
            orbs = self._orbs(pid)
            if orbs is None:
                self._note(run, "info", "讀不到背包裡的狐光靈珠，這次不略過")
                return None
            if orbs < SKIP_ORBS[k]:
                self._note(
                    run,
                    "info",
                    f"狐光靈珠剩 {orbs} 顆，略過{name}要 {SKIP_ORBS[k]} 顆，從這關開始打",
                )
                return None
            self._set_step(run, f"用狐光靈珠略過{name}")
            want = (YAN_CHALLENGE, SKIP_OFFER, skip_pick(k), skip_confirm(k))
            # A refused skip loops back to the 關 list, which has no way out but
            # a 關: pick it again, "reconsider", then "let me in".
            back = (SKIP_BACK, ENTER, skip_pick(k))
            r = self._talk(pid, run, yan, want, YAN_AVOID, once=True, fallback=back)
            if r == "map":
                self._note(run, "unconfirmed", f"略過{name}沒有成功，直接進塔從這關開始打")
                return r
            self._wait(run, 1.0)
            after = self._orbs(pid)
            if after is None or after >= orbs:
                self._note(
                    run,
                    "unconfirmed",
                    f"略過{name}沒有成功（{r}）：背包要 1 格空位、負重剩 5 以上，"
                    "或這關今天已經開始打；從這關開始打",
                )
                return None
            first = k * ROOMS + 1
            with run.lock:
                run.floors.extend(
                    TowerFloor(floor=f, secs=0, skipped=True) for f in range(first, first + ROOMS)
                )
            self._note(
                run, "confirmed", f"用狐光靈珠略過{name}（第 {first}–{first + ROOMS - 1} 層）"
            )
        return None

    # -- a room --------------------------------------------------------------------

    def _potion_totals(self, pid: int, run: _Run) -> tuple[int, int] | None:
        """Whitelisted HP / MP potions in the bag plus the pet bag."""
        rule = self._store.load(run.name).potion
        try:
            held = self._read_locked(pid, read_holdings)
        except Exception:
            held = None
        if held is None:
            return None
        bag, pet = held

        def total(items):
            return sum(bag.get(i, 0) + pet.get(i, 0) for i in set(items))

        return total(rule.hp_items), total(rule.mp_items)

    def _short_of_potions(self, pid: int, run: _Run, which: str) -> str | None:
        """Why the potions are short of the leave (or logout) floors, or None."""
        c = run.config
        if which == "leave":
            hp_floor, mp_floor, under = c.leave_hp_below, c.leave_mp_below, True
        else:
            hp_floor, mp_floor, under = (
                (c.logout_hp_at if c.logout else None),
                (c.logout_mp_at if c.logout else None),
                False,
            )
        if hp_floor is None and mp_floor is None:
            return None
        totals = self._potion_totals(pid, run)
        if totals is None:
            return None
        hp, mp = totals

        def low(have, floor):
            return floor is not None and (have < floor if under else have <= floor)

        if low(hp, hp_floor):
            return f"體力藥只剩 {hp}"
        if low(mp, mp_floor):
            return f"真氣藥只剩 {mp}"
        return None

    def _check_logout(self, pid: int, run: _Run, now: float) -> None:
        """Mid-floor, potions (bag + pet bag) at the logout floor: leave the game."""
        if not run.config.logout or now - run.potions_at < POTION_EVERY:
            return
        run.potions_at = now
        short = self._short_of_potions(pid, run, "logout")
        if not short:
            return
        self._note(run, "error", f"{short}，寵物背包也補不了，登出遊戲")
        if not self._leave_game(pid):
            raise _Done(f"{short}，要登出但叫不出登出選單（或找不到遊戲視窗）", "error")
        # Logged out = the own character is gone from the hook's `status`.
        end = self._clock() + LOGOUT_WAIT
        while self._clock() < end:
            if self._sleep(run.stop, 0.5):
                break
            try:
                st = self._channel.send(pid, "status")
            except (PipeGone, PipeBusy, NoReply):
                break
            if not st.get("ok"):
                break
        else:
            raise _Done(f"{short}，按了登出但角色還在遊戲裡，請手動處理", "error")
        raise _Done(f"{short}，寵物背包也補不了，已登出遊戲", "error")

    def _room_tick(
        self, pid: int, run: _Run, tower: TowerStage, st: dict, tile: tuple[int, int], objs
    ) -> float:
        now = self._clock()
        self._check_logout(pid, run, now)
        # The room is wherever the character stands, read every tick: an exit
        # that goes through unseen (live 2026-10-06, floor 31: no dialog, and
        # the bot kept bumping room 1's exit from room 2) is caught on the next look.
        room = tower.room_of(tile)
        if room != run.room:
            if run.room is not None and run.room_seen != room:
                run.room_seen = room  # one odd read is not a move: look again
                return STEP
            old = run.room
            if old is not None and room == old + 1 and self._room_done(run, tower, old):
                self._passed(run, tower, old)
            elif old is not None:
                self._note(run, "info", f"從第 {old} 房移到第 {room} 房")
            with run.lock:
                run.room, run.killed, run.kills = room, set(), 0
                run.room_started = self._wall()
                run.staged, run.idle = False, 0
                run.force_exit = False
                run.exit_try, run.exit_closed_at, run.exit_leave = 0, None, None
                run.bodies_since = None
            run.fighter.reset()
            try:
                run.fighter.learned = self._read_locked(pid, read_learned) or run.fighter.learned
            except Exception:
                pass
            if run.pid is not None:
                run_log.floor_reset(run.pid)  # the floor's HP numbers start here
            self._note(run, "info", f"第 {tower.floor(room)} 層（第 {room} 房）")
        run.room_seen = None  # this read agrees with the room (or moved it)
        ids = tower.monsters.get(room, frozenset())
        mine = [o for o in objs if o.get("id") in ids]
        with run.lock:
            for o in mine:
                if o.get("dead") and (o["id"], o["inst"]) not in run.killed:
                    run.killed.add((o["id"], o["inst"]))
                    log.debug(
                        "tower dead seen h=%s npc=%s t=%.3f",
                        o["h"],
                        o["id"],
                        now,
                        extra={"cat": "tower"},
                    )
            run.kills = len(run.killed)
        expect = tower.expect[room]
        live = [o for o in mine if not o.get("dead") and (o["id"], o["inst"]) not in run.killed]
        if run.force_exit and live and run.kills < expect:
            # The sweep missed it; it walked into view later (live 2026-10-06,
            # floor 33: 48 min waiting for this "body" to fade, 8 / 9).
            self._note(run, "info", f"又看到怪了（{run.kills} / {expect}），先打完再走出口")
            run.force_exit = run.staged = False
            run.exit_try, run.exit_closed_at, run.exit_leave = 0, None, None
            run.bodies_since = None
        if run.kills >= expect or run.force_exit:
            return self._finish_room(pid, run, tower, room, self._bodies(run, mine))
        if not live:
            run.idle += 1
            if run.idle < IDLE_SWEEP:
                return WAIT_BODIES
            self._sweep(pid, run, tower, room)
            run.idle = 0
            return STEP
        run.idle = 0
        self._set_step(run, f"清怪中，{run.kills} / {expect}")
        return self._fight(pid, run, st, objs, live, now)

    def _fight(self, pid: int, run: _Run, st: dict, objs, live, now: float) -> float:
        me = next((o for o in objs if o.get("h") == st.get("self")), None)
        stage = run.stage
        run.fighter.tick(
            live,
            (me["x"], me["y"]) if me else (0, 0),
            stage.strength() if stage else {},
            stage.elites if stage else frozenset(),
            self._defs(),
            (st.get("mp") or [0, 0])[0],
            self._guard.cast_count(pid),
            now,
            send=lambda line: self._cmd(pid, run, line),
            note=lambda phase, text: self._note(run, phase, text),
            state=st.get("state"),
        )
        return STEP

    def _sweep(self, pid: int, run: _Run, tower: TowerStage, room: int) -> None:
        """Nothing in view but kills are short: walk the spawn points (elite first)."""
        self._set_step(run, "畫面上沒有怪，去出怪點找")
        ids = tower.monsters.get(room, frozenset())
        for pt in tower.spawns.get(room, []):
            self._cmd(
                pid, run, f"walk {pt[0] * TILE_PX + TILE_PX // 2} {pt[1] * TILE_PX + TILE_PX // 2}"
            )
            end = self._clock() + SWEEP_WAIT
            while self._clock() < end:
                self._wait(run, 0.5)
                objs = self._near(pid, run)
                if any(
                    o.get("id") in ids
                    and not o.get("dead")
                    and (o["id"], o["inst"]) not in run.killed
                    for o in objs
                ):
                    return
        self._note(run, "info", f"出怪點都沒有怪（{run.kills} / {tower.expect[room]}），試出口")
        run.force_exit = True  # the exit opens only when the room is clear: it will tell

    def _finish_room(
        self, pid: int, run: _Run, tower: TowerStage, room: int, bodies: bool
    ) -> float:
        """One step toward the next room a tick: stand off, wait out the bodies,
        then one bump on the exit. The tick in between reads where we are, so a
        teleport is seen whether or not its dialog was."""
        if not run.staged:
            run.staged = True
            self._set_step(run, "全部擊倒，等屍體消失")
            self._walk(pid, run, tower.staging(room), wait=False)
        if bodies:
            return WAIT_BODIES
        now = self._clock()
        if run.exit_closed_at is not None:
            if now - run.exit_closed_at < EXIT_SETTLE:
                return WAIT_MAP  # the dialog went through: the teleport shows next
            run.exit_closed_at = None
        floor = tower.floor(room)
        if run.exit_leave is None:
            leave = run.config.stop_floor is not None and floor >= run.config.stop_floor
            short = self._short_of_potions(pid, run, "leave")
            if short:
                leave = True
                self._note(run, "info", f"{short}，過完這層就離開塔")
            run.exit_leave = (leave, short)
        leave, short = run.exit_leave
        self._set_step(run, "走向出口")
        r = self._bump_exit(pid, run, tower, room, leave)
        if r == "closed" and not leave and room < ROOMS:
            run.exit_closed_at = now
            return WAIT_MAP
        if r in ("closed", "map", "left"):
            # Logged now: past room 10 (a new 關) or out of the tower, the
            # next tick reads another map and the room is gone.
            self._passed(run, tower, room)
            if r == "closed" and leave:
                r = "left"
            if r == "left":
                if short:
                    # Not the day's end: restock and the tower picks up from here.
                    raise _Done(f"打到第 {floor} 層，{short}，離開塔補給")
                raise _Done(f"打到第 {floor} 層，照設定離開塔", complete=True)
            if r == "map" and room < ROOMS:
                raise _Done(f"第 {floor} 層之後被送出塔（等級不夠進下一層？）", complete=True)
            return WAIT_MAP
        if r == "low":
            raise _Done(f"第 {floor} 層之後等級不夠進下一層", complete=True)
        if r == "no-choice":
            raise _Done("出口對話沒有選項：背包滿或超重，清出空間後再開始", "error")
        if r == "stuck":
            raise _Done("出口對話的選項認不得，先停下來", "error")
        run.exit_try += 1
        if run.exit_try % EXIT_TRIES == 0:
            self._note(run, "unconfirmed", "出口沒有反應，再試一次")
            run.staged = run.force_exit = False
        return STEP

    def _bodies(self, run: _Run, mine: list) -> bool:
        """Bodies of this room still fading (live monsters do not count), for at
        most BODIES_MAX: a body that stays must not hold the room forever."""
        if not any(o.get("dead") for o in mine):
            run.bodies_since = None
            return False
        now = self._clock()
        if run.bodies_since is None:
            run.bodies_since = now
        return now - run.bodies_since < BODIES_MAX

    def _room_done(self, run: _Run, tower: TowerStage, room: int) -> bool:
        return (
            run.kills >= tower.expect.get(room, 0)
            or run.force_exit
            or run.exit_closed_at is not None
        )

    def _passed(self, run: _Run, tower: TowerStage, room: int) -> None:
        """Room `room` is behind us: log its floor."""
        floor = tower.floor(room)
        secs = self._wall() - (run.room_started or self._wall())
        with run.lock:
            run.floors.append(TowerFloor(floor=floor, secs=round(secs, 1)))
            run.cleared = True
            run.passed_room = room
        self._note(
            run,
            "confirmed",
            f"第 {floor} 層通過（{int(secs) // 60}:{int(secs) % 60:02d}{_hp_summary(run)}）",
        )

    def _bump_exit(self, pid: int, run: _Run, tower: TowerStage, room: int, leave: bool) -> str:
        """Step onto one exit tile (the next one each try) and give it time to
        open the dialog; back to the stand-off tile when it did not."""
        tiles = tower.exit_tiles(room)
        self._walk(pid, run, tiles[run.exit_try % len(tiles)])
        r = self._dialog(pid, run, (), frozenset({LEAVE}), EXIT_DIALOG, leave=leave)
        if r == "none":
            self._walk(pid, run, tower.staging(room))
        return r

    # -- moving and talking ------------------------------------------------------------

    def _walk(self, pid: int, run: _Run, tile: tuple[int, int], wait: bool = True) -> None:
        line = f"walk {tile[0] * TILE_PX + TILE_PX // 2} {tile[1] * TILE_PX + TILE_PX // 2}"
        for _ in range(6):  # refused (path false) while the last walk still runs
            if self._cmd(pid, run, line).get("path"):
                break
            self._wait(run, 0.3)
        if not wait:
            return
        end = self._clock() + WALK_WAIT
        while self._clock() < end:
            self._wait(run, 0.5)
            st = self._cmd(pid, run, "status")
            if not st.get("ok"):
                continue
            t = own_tile(st, self._near(pid, run))
            if abs(t[0] - tile[0]) <= 1 and abs(t[1] - tile[1]) <= 1:
                return

    def _talk(self, pid: int, run: _Run, npc: dict, want, avoid, **kw) -> str:
        self._cmd(pid, run, f"talk {npc['h']}")
        return self._dialog(pid, run, want, avoid, TALK_WAIT, **kw)

    def _dialog(
        self,
        pid: int,
        run: _Run,
        want,
        avoid,
        timeout: float,
        leave: bool = False,
        once: bool = False,
        fallback: tuple[int, ...] = (),
    ) -> str:
        """Drive the dialog by jump id. map / closed / no-choice / low / stuck / none / timeout.

        `once`: each wanted id is chosen at most once; when the dialog offers
        one again (it looped back), only `fallback` ids are chosen from then on.
        """
        st = self._cmd(pid, run, "status")
        me0 = st.get("self")
        end = self._clock() + timeout
        seen = chose = False
        picked: set[int] = set()
        fell_back = False
        while self._clock() < end:
            self._wait(run, 0.2)
            st = self._cmd(pid, run, "status")
            if not st.get("ok") or st.get("self") != me0:
                self._wait(run, 2.0)
                return "map"
            d = self._cmd(pid, run, "dialog")
            if not d.get("open"):
                if seen:
                    return "closed" if chose else "no-choice"
                continue
            seen = True
            if d.get("waiting"):
                continue
            options = d.get("options") or []
            if options:
                if leave and LEAVE in options:
                    self._cmd(pid, run, f"option {options.index(LEAVE)}")
                    self._wait(run, 2.0)
                    return "left"
                if once:
                    if not fell_back and any(o in picked for o in options):
                        want, fell_back = fallback, True  # looped back: refused
                    i = next((options.index(w) for w in want if w in options), None)
                else:
                    i = pick_option(options, want, avoid)
                if i is None:
                    return "stuck"
                picked.add(options[i])
                self._cmd(pid, run, f"option {i}")
                chose = True
                continue
            if d.get("next") == TOO_LOW:
                return "low"
            self._cmd(pid, run, "next")
        return "timeout" if seen else "none"
