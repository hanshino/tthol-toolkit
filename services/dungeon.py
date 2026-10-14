"""Dungeon runner: plays a dungeon script (services/dungeon_scripts.py) for one
character, as a 日常 module.

Each tick reads where the character is: away from the dungeon it walks to the
entrance (the navigator, once); on the entrance map it runs the script's entry
steps (or, after the last exit, its finish steps); inside it finds the area
from the own tile (services/map_areas.py) and runs that area's steps. The
dungeon maps are shared with other players, so a run can start or continue
from any state: an area is done when its exit works, and an exit someone else
already opened is simply taken.

Fighting is the shared Fighter (services/combat.py) with the character's one
CombatRule; potions, buffs and the hero transform are the guard's: the module
starts it. Walking, talking and dialogs are services/hook_actions.Actor.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable

from services import run_log
from services.api_types import (
    CombatRule,
    DailyMetric,
    DailySummary,
    DungeonConfig,
    DungeonRecord,
    DungeonSettings,
    DungeonStatus,
    DungeonView,
    TowerLogEntry,
)
from services.combat import (
    COMBAT_SECTION,
    AttackSkill,
    Fighter,
    attack_problem,
    load_attack_skills,
    skill_candidates,
)
from services.dungeon_scripts import (
    Area,
    Clear,
    Dungeon,
    Enter,
    FirstOpen,
    Hold,
    Shortcut,
    Step,
    Talk,
)
from services.guard import GuardManager, GuardStore, read_holdings, read_learned, read_stage_id
from services.hook_actions import TILE_PX, Actor, own_tile
from services.hook_caps import FEATURES, HookCaps
from services.hook_cmd import CommandChannel, NoReply, PipeBusy, PipeGone
from services.map_areas import MapAreas, Tile, load_areas
from services.nearby import read_nearby

log = logging.getLogger("tthol.dungeon")

LOG_KEEP = 200
STEP = 0.05  # between two fight decisions
WAIT_MAP = 0.5  # own character not built yet after a map change
WAIT_RETRY = 1.0  # every step of an area ran and the character did not move
STUCK_ROUNDS = 10  # area passes without a move before the log says so
DEATH_CONFIRM = 3.0  # HP 0 this long in a row is a death; a map load reads 0 a moment
ENTER_SETTLE = 3.0  # after reaching a portal tile, for the teleport to show
WALK_PER_TILE = 0.3  # walking time allowed per tile (plus 2 s)
STAGE_EVERY = 0.5  # re-read the map id this often inside step loops
BODIES_MAX = 15.0  # a portal works only once the bodies of its groups faded
STEP_OFF = 4  # tiles to step away from a portal zone before walking onto it
ARM_TILES = 5  # map events on this close to a portal zone (navigator.ARM_TILES)
WALK_STALL = 3  # reads (0.5 s apart) without moving: click the portal tile again
SWEEP_WAIT = 8.0  # per spawn point, for a monster to show up
CLEAR_MAX = 600.0  # one Clear step longer than this: something is wrong
HOLD_IDLE = 90.0  # nothing to fight this long while holding: go on (try the exit)
HOLD_MAX = 1800.0
FOLLOWERS_EVERY = 1.0
PIPE_RETRIES = 5
CAPS_EVERY = 30.0


class _Done(Exception):
    def __init__(self, reason: str, phase: str = "info", complete: bool = False) -> None:
        super().__init__(reason)
        self.reason, self.phase, self.complete = reason, phase, complete


class _Moved(Exception):
    """The character is somewhere else now: the next tick looks again."""


class _Run:
    def __init__(self, name: str, combat: CombatRule, config: DungeonConfig) -> None:
        self.name = name
        self.pid: int | None = None
        self.config = config
        self.fighter = Fighter(combat, log, cat="dungeon")
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.log: deque[TowerLogEntry] = deque(maxlen=LOG_KEEP)
        self.next_id = 0
        self.step: str | None = None
        self.problem: str | None = None
        self.warning: str | None = None
        self.area: Area | None = None
        self.step_index = 0
        self.rounds = 0  # passes of the current area without a move
        self.shortcuts: set[tuple[int, int]] = set()  # (stage, event) already tried
        self.talked: set[int] = set()  # id() of entry / finish Talk steps done
        self.opened: dict[int, int] = {}  # id() of a FirstOpen -> its portals taken
        self.inside = False  # reached a dungeon map in this run
        self.exiting = False  # stepping on the last exit
        self.walking_in = False  # ran the entry steps: the next dungeon map is a fresh entry
        self.cleared = False  # came out through the last exit
        self.navigated = False
        self.moving = False
        self.zero_hp_at: float | None = None
        self.stage_at = -STAGE_EVERY
        self.followers: set[tuple[int, int]] = set()
        self.followers_at = -FOLLOWERS_EVERY
        self.chests_before: dict[int, int] | None = None
        self.events_ok = True  # the hook has `mapevents`
        self.started: float | None = None
        self.ended: str | None = None
        self.outcome: str | None = None
        self.user_stop = False


class DungeonManager:
    """One script, many characters. Implements the 日常 module protocol."""

    def __init__(
        self,
        dungeon: Dungeon,
        guard: GuardManager,
        read_locked: Callable[[int, Callable], object],
        character_name: Callable[[int], str | None],
        channel: CommandChannel | None = None,
        store: GuardStore | None = None,
        navigator=None,  # services.navigator.Navigator
        hook_caps: HookCaps | None = None,
        attack_skills: Callable[[], dict[tuple[int, int], AttackSkill]] = load_attack_skills,
        areas: Callable[[str, int], MapAreas | None] = load_areas,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        wait: Callable[[threading.Event, float], bool] = lambda ev, secs: ev.wait(secs),
        busy: Callable[[int], str | None] = lambda _pid: None,
    ) -> None:
        self.dungeon = dungeon
        self.key = dungeon.key
        self.title = dungeon.title
        self._guard = guard
        self._read_locked = read_locked
        self._character_name = character_name
        self._channel = channel or CommandChannel()
        self._store = store or GuardStore()
        self._navigator = navigator
        self._hook_caps = hook_caps or HookCaps(self._channel, clock)
        self._load_attack_skills = attack_skills
        self._attack_skills: dict[tuple[int, int], AttackSkill] | None = None
        self._load_areas = areas
        self._clock = clock
        self._wall = wall
        self._sleep = wait
        self._busy = busy
        self._runs: dict[int, _Run] = {}
        self._lock = threading.Lock()
        self._index: dict[tuple[int, int], Area] | None = None

    @property
    def config_section(self) -> str:
        return f"dungeon.{self.key}"

    @property
    def record_section(self) -> str:
        return f"dungeon.{self.key}.record"

    @property
    def commands(self) -> tuple[str, ...]:
        return FEATURES[f"daily.{self.key}"]

    # -- API -----------------------------------------------------------------

    def view(self, pid: int) -> DungeonView:
        name = self._character_name(pid)
        base = DungeonView(
            key=self.key,
            title=self.title,
            floors=list(self.dungeon.floors),
            status=self.status(pid),
            hook_ready=self._hook_ready(pid),
        )
        if not name:
            return base
        record = self._store.load_section(name, self.record_section, DungeonRecord)
        return base.model_copy(
            update={
                "config": self._store.load_section(name, self.config_section, DungeonConfig),
                "combat": self._store.load_section(name, COMBAT_SECTION, CombatRule),
                "skills": skill_candidates(self._learned(pid), self._defs()),
                "record": record,
                "cooldown_until": self._cooldown_until(record),
            }
        )

    def save_settings(self, pid: int, settings: DungeonSettings) -> DungeonSettings | None:
        name = self._character_name(pid)
        if not name:
            return None
        self._store.save_section(name, COMBAT_SECTION, settings.combat)
        self._store.save_section(name, self.config_section, settings.config)
        with self._lock:
            run = self._runs.get(pid)
        if run is not None and run.name == name:
            run.config, run.fighter.rule = settings.config, settings.combat
        return settings

    def _learned(self, pid: int) -> dict[int, int]:
        try:
            return self._read_locked(pid, read_learned) or {}
        except Exception:
            return {}

    def config_problem(self, name: str) -> str | None:
        """Why `name` cannot run this dungeon now, or None (no game needed)."""
        problem = attack_problem(self._store.load_section(name, COMBAT_SECTION, CombatRule))
        return problem or self._cooldown_problem(name)

    def _cooldown_problem(self, name: str) -> str | None:
        record = self._store.load_section(name, self.record_section, DungeonRecord)
        until = self._cooldown_until(record)
        if until is None:
            return None
        return f"{self.title}還在冷卻，{time.strftime('%H:%M', time.localtime(until))} 後可再進"

    def start(self, pid: int) -> tuple[bool, str | None]:
        name = self._character_name(pid)
        if not name:
            return False, "角色還沒定位"
        busy = self._busy(pid)
        if busy:
            return False, busy
        problem = attack_problem(self._store.load_section(name, COMBAT_SECTION, CombatRule))
        if problem:
            return False, problem
        stage = self._stage(pid)
        if stage is None or stage[0] not in self.dungeon.stages:
            problem = self._cooldown_problem(name)  # inside: a restart, the timer is ours
            if problem:
                return False, problem
        try:
            have = self._hook_caps.get(pid) or frozenset()
        except PipeGone:
            return False, "這個遊戲視窗沒有 hook 指令通道"
        except (PipeBusy, NoReply):
            return False, "hook 暫時沒有回應（還在登入或換地圖？），稍後再開始"
        missing = [c for c in self.commands if c not in have]
        if missing:
            return False, f"這個 hook 缺少{self.title}要用的指令：{'、'.join(missing)}"
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
                self._store.load_section(name, self.config_section, DungeonConfig),
            )
            run.pid, run.started = pid, self._wall()
            if not self._store.load(name).potion.hp_items:
                run.warning = "補水的體力白名單是空的，守護不會喝水"
            self._runs[pid] = run
            run.thread = threading.Thread(
                target=self._loop, args=(pid, run), daemon=True, name=f"dungeon-{self.key}-{pid}"
            )
            run.thread.start()
        log.info("dungeon %s started pid=%d", self.key, pid, extra={"cat": "dungeon"})
        return True, None

    def stop(self, pid: int) -> None:
        with self._lock:
            run = self._runs.get(pid)
        if run is not None:
            if not run.stop.is_set():
                run.user_stop = True
            run.stop.set()

    def forget(self, pid: int) -> None:
        self.stop(pid)
        with self._lock:
            self._runs.pop(pid, None)

    def shutdown(self) -> None:
        with self._lock:
            runs = list(self._runs.values())
        for run in runs:
            run.stop.set()

    def running(self, pid: int) -> bool:
        with self._lock:
            run = self._runs.get(pid)
        return run is not None and run.thread is not None and run.thread.is_alive()

    def outcome(self, pid: int) -> tuple[str, str] | None:
        with self._lock:
            run = self._runs.get(pid)
        if run is None or self.running(pid):
            return None
        with run.lock:
            return (run.outcome, run.ended or "") if run.outcome else None

    def done_today(self, pid: int) -> bool:
        name = self._character_name(pid)
        return bool(name) and self.done_for(name)

    def done_for(self, name: str) -> bool:
        record = self._store.load_section(name, self.record_section, DungeonRecord)
        return record.done and record.date == self._today()

    def mark_done(self, name: str, done: bool) -> None:
        record = self._store.load_section(name, self.record_section, DungeonRecord)
        record = record.model_copy(update={"date": self._today(), "done": done})
        self._store.save_section(name, self.record_section, record)

    def status(self, pid: int) -> DungeonStatus:
        with self._lock:
            run = self._runs.get(pid)
        if run is None:
            return DungeonStatus(running=False, character=self._character_name(pid))
        running = self.running(pid) and not run.stop.is_set()
        with run.lock:
            return DungeonStatus(
                running=running,
                character=run.name,
                step=run.step if running else None,
                problem=run.problem if running else None,
                warning=run.warning,
                area=run.area.name if run.area else None,
                floor=self._floor_of(run.area),
                moving=run.moving,
                started=run.started,
                ended=None if running else run.ended,
                outcome=run.outcome if not running else None,
                log=list(run.log),
            )

    def summary(self, pid: int) -> DailySummary:
        name = self._character_name(pid)
        base = DailySummary(module=self.key, title=self.title, state="idle")
        if not name:
            return base
        done = self.done_for(name)
        record = self._store.load_section(name, self.record_section, DungeonRecord)
        st = self.status(pid)
        floors = list(self.dungeon.floors)
        if not st.running:
            state = (
                "done_today"
                if done
                else {"done": "done", "error": "stopped"}.get(st.outcome or "", "idle")
            )
            return base.model_copy(
                update={
                    "state": state,
                    "where": "今天打完了" if done else None,
                    "segments": ["done" if done else "empty" for _ in floors],
                    "step": st.ended or record.ended,
                    "result": f"寶箱 {record.chests}" if done else None,
                    "done_today": done,
                }
            )
        at = floors.index(st.floor) if st.floor in floors else -1
        segments = [
            "done" if i < at else "current" if i == at else "empty" for i in range(len(floors))
        ]
        metrics = [DailyMetric(label="開始", since=st.started)] if st.started else []
        return base.model_copy(
            update={
                "state": "moving" if st.moving else "running",
                "headline": st.floor,
                "where": st.area or ("前往入口" if st.moving else None),
                "segments": segments,
                "step": st.problem or st.step or "準備中",
                "metrics": metrics,
                "done_today": done,
            }
        )

    def on_attack_packet(self, pid: int, raw: bytes, _ts: float, own_key: bytes | None) -> None:
        with self._lock:
            run = self._runs.get(pid)
        if run is not None:
            run.fighter.on_attack(raw, own_key, self._clock())

    def on_cast_packet(self, pid: int, raw: bytes, _ts: float, own_key: bytes | None) -> None:
        with self._lock:
            run = self._runs.get(pid)
        if run is not None:
            run.fighter.on_cast(raw, own_key, self._clock())

    # -- helpers ---------------------------------------------------------------

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(self._wall()))

    def _cooldown_until(self, record: DungeonRecord) -> float | None:
        if record.entered_at is None:
            return None
        until = record.entered_at + self.dungeon.cooldown_h * 3600
        return until if until > self._wall() else None

    def _floor_of(self, area: Area | None) -> str | None:
        """The progress label: the area's own, else the nearest one before it."""
        if area is None:
            return None
        label = None
        for a in self.dungeon.areas:
            label = a.floor or label
            if a is area:
                return label
        return None

    def _defs(self) -> dict[tuple[int, int], AttackSkill]:
        if self._attack_skills is None:
            self._attack_skills = self._load_attack_skills()
        return self._attack_skills

    def _areas(self, stage: int) -> MapAreas:
        areas = self._load_areas("stage", stage)
        if areas is None:
            raise _Done(f"資料庫裡沒有地圖 #{stage} 的可走區域", "error")
        return areas

    def _area_index(self) -> dict[tuple[int, int], Area]:
        """(stage, map area) -> script area."""
        if self._index is None:
            index = {}
            for a in self.dungeon.areas:
                part = self._areas(a.stage).landing_area(a.landing)
                if part is not None:
                    index[(a.stage, part)] = a
            self._index = index
        return self._index

    def _hook_ready(self, pid: int) -> bool:
        try:
            have = self._hook_caps.get(pid, CAPS_EVERY)
        except (PipeGone, PipeBusy, NoReply):
            have = self._hook_caps.cached(pid)
        return have is not None and all(c in have for c in self.commands)

    def _note(self, run: _Run, phase: str, text: str) -> None:
        with run.lock:
            run.next_id += 1
            run.log.append(TowerLogEntry(id=run.next_id, ts=self._wall(), phase=phase, text=text))
        run_log.note("dungeon", run.pid, run.name, text, phase=phase, dungeon=self.key)

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
                self._wait(run, 1.0)  # gone for a moment while the client loads a map
            except (PipeBusy, NoReply):
                return {"ok": False, "error": "busy"}
        raise _Done("找不到 hook 指令通道（遊戲關了，或 hook 沒有注入）", "error")

    def _wait(self, run: _Run, secs: float) -> None:
        if self._sleep(run.stop, secs):
            raise _Done("已停止")

    def _actor(self, pid: int, run: _Run) -> Actor:
        return Actor(
            lambda line: self._cmd(pid, run, line), lambda s: self._wait(run, s), self._clock
        )

    def _stage(self, pid: int) -> tuple[int, str] | None:
        try:
            return self._read_locked(pid, read_stage_id)
        except Exception:
            return None

    def _bag(self, pid: int) -> dict[int, int] | None:
        try:
            held = self._read_locked(pid, read_holdings)
        except Exception:
            return None
        return dict(held[0]) if held else None

    # -- the loop ----------------------------------------------------------------

    def _loop(self, pid: int, run: _Run) -> None:
        self._note(run, "info", f"開始{self.title}")
        ended, phase, complete = "已停止", "info", False
        try:
            try:
                run.fighter.learned = self._read_locked(pid, read_learned) or {}
            except Exception:
                pass
            while True:
                try:
                    secs = self._tick(pid, run)
                except _Moved:
                    secs = WAIT_MAP
                self._wait(run, secs)
        except _Done as done:
            ended, phase, complete = done.reason, done.phase, done.complete
        except Exception:
            log.exception("dungeon loop failed pid=%d", pid, extra={"cat": "dungeon"})
            ended, phase = f"{self.title}出錯停止（詳見診斷紀錄）", "error"
        finally:
            outcome = (
                "done" if complete else "user" if run.user_stop and phase != "error" else "error"
            )
            with run.lock:
                run.ended, run.outcome, run.moving = ended, outcome, False
            run.stop.set()
            self._note(run, phase, f"{self.title}結束：{ended}")
            self._save_end(run, ended, complete)
            log.info(
                "dungeon %s stopped pid=%d: %s", self.key, pid, ended, extra={"cat": "dungeon"}
            )

    def _save_end(self, run: _Run, ended: str, complete: bool) -> None:
        old = self._store.load_section(run.name, self.record_section, DungeonRecord)
        update: dict = {"ended": ended}
        if complete:
            update.update({"date": self._today(), "done": True, "finished_at": self._wall()})
        self._store.save_section(run.name, self.record_section, old.model_copy(update=update))

    def _save_entered(self, run: _Run) -> None:
        old = self._store.load_section(run.name, self.record_section, DungeonRecord)
        self._store.save_section(
            run.name, self.record_section, old.model_copy(update={"entered_at": self._wall()})
        )

    def _tick(self, pid: int, run: _Run) -> float:
        st = self._cmd(pid, run, "status")
        if not st.get("ok"):
            with run.lock:
                run.problem = "等角色載入（換地圖中）"
            return WAIT_MAP
        self._check_hp(run, st)
        stage = self._stage(pid)
        if stage is None:
            return WAIT_MAP
        stage_id, stage_name = stage
        d = self.dungeon
        if stage_id == d.entry_stage:
            return self._at_entrance(pid, run)
        if stage_id not in d.stages:
            if run.inside:
                raise _Done(f"離開副本了（在{stage_name}）：死亡回城或被傳走", "error")
            return self._travel(pid, run, stage_name)
        if not run.inside:
            run.inside = True
            if run.walking_in:  # a restart mid-run keeps the first entry's timer
                self._save_entered(run)
            self._note(run, "info", f"進入{self.title}")
        objs = self._actor(pid, run).near()
        tile = own_tile(st, objs)
        areas = self._areas(stage_id)
        part = areas.area_of(tile)
        area = self._area_index().get((stage_id, part)) if part is not None else None
        if area is None:
            with run.lock:
                run.problem = f"不在劇本認得的區域（{stage_name} {tile[0]},{tile[1]}）"
            return WAIT_RETRY
        if area is not run.area:
            with run.lock:
                run.area, run.step_index, run.rounds = area, 0, 0
            run.fighter.reset()
            self._note(run, "info", f"到了{area.name}")
        return self._area_tick(pid, run, area, stage_id, part, areas)

    def _check_hp(self, run: _Run, st: dict) -> None:
        hp = (st.get("hp") or [0, 0])[0]
        if hp > 0:
            run.zero_hp_at = None
            return
        now = self._clock()
        if run.zero_hp_at is None:
            run.zero_hp_at = now
        if now - run.zero_hp_at >= DEATH_CONFIRM:
            raise _Done("角色死亡", "error")
        raise _Moved  # a map load reads 0 for a moment: look again

    def _travel(self, pid: int, run: _Run, stage_name: str) -> float:
        """Away from the dungeon: walk to the entrance once, next to the first NPC
        the entry talks to."""
        d = self.dungeon
        if self._navigator is None or run.navigated:
            raise _Done(f"不在{self.title}入口（在{stage_name}），導航沒有到", "error")
        run.navigated = True
        goal = self._entry_goal()
        self._set_step(run, f"從{stage_name}前往{self.title}入口")
        self._note(run, "info", f"從{stage_name}導航到{self.title}入口")
        with run.lock:
            run.moving = True
        try:
            with self._guard.quiet(pid):
                result = self._navigator.go(
                    pid, d.entry_stage, goal, stop=run.stop, note=lambda t: self._set_step(run, t)
                )
        finally:
            with run.lock:
                run.moving = False
        if result.reason == "stopped":
            raise _Done("已停止")
        if not result.ok:
            raise _Done(f"走不到{self.title}入口：{result.reason}", "error")
        return WAIT_MAP

    def _entry_goal(self) -> Tile | None:
        areas = self._areas(self.dungeon.entry_stage)
        for step in self.dungeon.entry:
            if isinstance(step, Talk) and areas.npcs.get(step.npc):
                return areas.npcs[step.npc][0]
        return None

    def _at_entrance(self, pid: int, run: _Run) -> float:
        d = self.dungeon
        areas = self._areas(d.entry_stage)
        if run.inside and not run.cleared:
            if not run.exiting:
                raise _Done(f"被送回{self.title}入口，副本沒有打完", "error")
            run.cleared = True
            self._note(run, "confirmed", f"{self.title}通關")
        steps = d.finish if run.cleared else d.entry
        run.walking_in = not run.cleared
        st = self._cmd(pid, run, "status")
        objs = self._actor(pid, run).near()
        part = areas.area_of(own_tile(st, objs))
        for step in steps:
            self._do(pid, run, step, d.entry_stage, part, areas)
        if run.cleared:
            self._count_chests(pid, run)
            raise _Done(f"{self.title}完成", complete=True)
        # Every entry step ran and we are still here: the entry portal is shut.
        raise _Done(
            f"進不去{self.title}：可能還在 {d.cooldown_h:g} 小時冷卻，或還沒交過入場道具", "error"
        )

    def _count_chests(self, pid: int, run: _Run) -> None:
        after = self._bag(pid)
        if after is None or run.chests_before is None:
            return
        got = sum(
            max(0, after.get(i, 0) - run.chests_before.get(i, 0)) for i in self.dungeon.chests
        )
        old = self._store.load_section(run.name, self.record_section, DungeonRecord)
        self._store.save_section(
            run.name, self.record_section, old.model_copy(update={"chests": got})
        )
        self._note(run, "confirmed", f"拿到寶箱 {got} 個")

    def _area_tick(
        self, pid: int, run: _Run, area: Area, stage: int, part: int, areas: MapAreas
    ) -> float:
        while run.step_index < len(area.steps):
            step = area.steps[run.step_index]
            self._do(pid, run, step, stage, part, areas)  # raises _Moved on a move
            run.step_index += 1
        run.step_index = 0
        run.rounds += 1
        if run.rounds == STUCK_ROUNDS:
            self._note(run, "unconfirmed", f"{area.name}的出口一直沒開，繼續等")
        with run.lock:
            run.problem = f"{area.name}：出口還沒開，再巡一次"
        return WAIT_RETRY

    # -- steps -------------------------------------------------------------------

    def _do(
        self, pid: int, run: _Run, step: Step, stage: int, part: int | None, areas: MapAreas
    ) -> None:
        if isinstance(step, Clear):
            self._clear(pid, run, step.groups, stage, part, areas)
        elif isinstance(step, Enter):
            self._enter(pid, run, step.event, stage, part, areas)
        elif isinstance(step, FirstOpen):
            # The rooms open in order: one taken means the ones before it are
            # done. Only a restart mid-run tries them all once again.
            start = run.opened.get(id(step), 0)
            for i, event in enumerate(step.events[start:], start):
                try:
                    self._enter(pid, run, event, stage, part, areas, tries=1)
                except _Moved:
                    run.opened[id(step)] = i + 1
                    raise
        elif isinstance(step, Shortcut):
            self._shortcut(pid, run, step.event, stage, part, areas)
        elif isinstance(step, Hold):
            self._hold(pid, run, step, stage, part, areas)
        elif isinstance(step, Talk):
            self._talk(pid, run, step, areas)

    def _look(self, pid: int, run: _Run, stage: int, part: int | None, areas: MapAreas):
        """status + near, with the death and move checks every step loop needs."""
        st = self._cmd(pid, run, "status")
        if not st.get("ok"):
            raise _Moved
        self._check_hp(run, st)
        if self._clock() - run.stage_at >= STAGE_EVERY:
            run.stage_at = self._clock()
            now = self._stage(pid)
            if now is not None and now[0] != stage:
                raise _Moved
        objs = self._actor(pid, run).near()
        tile = own_tile(st, objs)
        if part is not None and areas.area_of(tile) not in (part, None):
            raise _Moved
        return st, objs, tile

    def _targets(self, pid, run, objs, npc_ids, part, areas) -> list[dict]:
        self._refresh_followers(pid, run)
        out = []
        for o in objs:
            if o.get("dead") or o.get("id") not in npc_ids:
                continue
            if (o.get("id"), o.get("inst")) in run.followers:
                continue
            if areas.area_of((o["x"] // TILE_PX, o["y"] // TILE_PX)) != part:
                continue
            out.append(o)
        return out

    def _refresh_followers(self, pid: int, run: _Run) -> None:
        now = self._clock()
        if now - run.followers_at < FOLLOWERS_EVERY:
            return
        run.followers_at = now
        try:
            objects, _own = self._read_locked(pid, read_nearby) or ([], None)
        except Exception:
            return
        run.followers = {(o.npc_id, o.instance) for o in objects if o.tag}

    def _npcs_of(self, groups, areas: MapAreas) -> frozenset[int]:
        ids: set[int] = set()
        for g in groups:
            ids |= areas.group_npcs.get(g, frozenset())
        return frozenset(ids)

    def _elites(self, groups, areas: MapAreas) -> frozenset[int]:
        """npc ids of the groups that spawn one monster (bosses)."""
        ids: set[int] = set()
        for g in groups:
            if areas.group_count.get(g, 0) == 1:
                ids |= areas.group_npcs.get(g, frozenset())
        return frozenset(ids)

    def _fight(self, pid: int, run: _Run, st: dict, objs, live, elites) -> None:
        me = next((o for o in objs if o.get("h") == st.get("self")), None)
        run.fighter.tick(
            live,
            (me["x"], me["y"]) if me else (0, 0),
            {i: (1,) for i in elites},
            elites,
            self._defs(),
            (st.get("mp") or [0, 0])[0],
            self._guard.cast_count(pid),
            self._clock(),
            send=lambda line: self._cmd(pid, run, line),
            note=lambda phase, text: self._note(run, phase, text),
            state=st.get("state"),
        )

    def _clear(self, pid, run, groups, stage, part, areas) -> None:
        ids = self._npcs_of(groups, areas)
        elites = self._elites(groups, areas)
        end = self._clock() + CLEAR_MAX
        swept = False
        while self._clock() < end:
            st, objs, _tile = self._look(pid, run, stage, part, areas)
            live = self._targets(pid, run, objs, ids, part, areas)
            if live:
                self._set_step(run, f"{run.area.name if run.area else ''}：清怪，剩 {len(live)} 隻")
                self._fight(pid, run, st, objs, live, elites)
                swept = False
                self._wait(run, STEP)
                continue
            if swept:
                return
            swept = True
            if not self._sweep(pid, run, groups, ids, stage, part, areas):
                return
        raise _Done(
            f"{run.area.name if run.area else ''}清怪超過 {CLEAR_MAX / 60:.0f} 分鐘", "error"
        )

    def _sweep(self, pid, run, groups, ids, stage, part, areas) -> bool:
        """Walk the groups' spawn points in this area; True when a monster shows up."""
        actor = self._actor(pid, run)
        points = [
            t for g in groups for t in areas.group_tiles.get(g, []) if areas.area_of(t) == part
        ]
        if points:
            self._set_step(run, "畫面上沒有怪，去出怪點看看")
        for tile in points:
            actor.walk(tile, wait=False)
            end = self._clock() + SWEEP_WAIT
            while self._clock() < end:
                self._wait(run, 0.5)
                _st, objs, here = self._look(pid, run, stage, part, areas)
                if self._targets(pid, run, objs, ids, part, areas):
                    return True
                if abs(here[0] - tile[0]) <= 2 and abs(here[1] - tile[1]) <= 2:
                    break
        return False

    def _shortcut(self, pid, run, event, stage, part, areas) -> None:
        key = (stage, event)
        if key in run.shortcuts:
            return
        run.shortcuts.add(key)
        _st, objs, _tile = self._look(pid, run, stage, part, areas)
        # Only the groups the script fights here: the DB also places map-wide
        # hazards (凌霄閣 G0 毒氣 / 迷香, never dead) in one of the areas.
        ids = self._npcs_of(self._script_groups(run.area), areas)
        if self._targets(pid, run, objs, ids, part, areas):
            return  # monsters here: the exit is not open
        self._set_step(run, "先試試出口有沒有開")
        self._enter(pid, run, event, stage, part, areas)

    @staticmethod
    def _script_groups(area: Area | None) -> tuple[int, ...]:
        if area is None:
            return ()
        out: list[int] = []
        for step in area.steps:
            if isinstance(step, Clear):
                out += step.groups
            elif isinstance(step, Hold):
                out += [*step.fight, step.until_dead]
        return tuple(out)

    def _enter(self, pid, run, event, stage, part, areas, tries: int = 2) -> bool:
        """Step on portal `event`. Raises _Moved when it worked; False when not.
        `tries`: zone tiles to try (a FirstOpen candidate that is shut costs one)."""
        portal = areas.portals.get(event)
        if portal is None or not portal.zone:
            self._note(run, "error", f"劇本的傳點 {event} 不在這張地圖")
            return False
        last = event == self.dungeon.last_exit and stage != self.dungeon.entry_stage
        if last:
            run.exiting = True  # back on the entrance map next: cleared, not kicked out
            if run.chests_before is None:
                run.chests_before = self._bag(pid)
        ok = self._step_on(pid, run, portal, stage, part, areas, tries)  # _Moved: it worked
        if last:
            run.exiting = False  # shut: we stay inside
        return ok

    def _step_on(self, pid, run, portal, stage, part, areas, tries: int) -> bool:
        event = portal.event
        actor = self._actor(pid, run)
        self._wait_bodies(pid, run, portal.dead, stage, part, areas)
        _st, _objs, here = self._look(pid, run, stage, part, areas)
        zone = sorted(portal.zone, key=lambda t: max(abs(t[0] - here[0]), abs(t[1] - here[1])))
        self._set_step(run, f"{run.area.name if run.area else ''}：走上傳點 {event}")
        try:
            for tile in zone[:tries]:
                if max(abs(tile[0] - here[0]), abs(tile[1] - here[1])) <= 2:
                    away = self._step_off(tile, here, part, areas)
                    if away is not None:
                        self._map_events(pid, run, False)
                        actor.walk(away)
                self._walk_in(pid, run, tile, portal.zone, stage, part, areas)
                end = self._clock() + ENTER_SETTLE
                last_dialog = 0.0
                while self._clock() < end:
                    self._wait(run, 0.3)
                    self._look(pid, run, stage, part, areas)  # raises _Moved once it worked
                    if self._clock() - last_dialog >= 1.0:
                        last_dialog = self._clock()
                        d = self._cmd(pid, run, "dialog")
                        if d.get("open") and not d.get("options"):
                            self._cmd(pid, run, "next")  # a "not yet" message: close it
                _st, _objs, here = self._look(pid, run, stage, part, areas)
        finally:
            self._map_events(pid, run, False)  # no zone fires on the walks after this
        return False

    def _walk_in(self, pid, run, tile: Tile, cells, stage, part, areas) -> None:
        """Walk onto a portal tile the way the navigator does (_Run._walk_to):
        map events off on the way, on ARM_TILES from the zone so the step in
        fires it, and the target clicked again when a hit (毒氣) stopped the walk
        short. Raises _Moved once the portal worked."""
        actor = self._actor(pid, run)
        _st, _objs, here = self._look(pid, run, stage, part, areas)

        def close(pos: Tile) -> bool:
            return min(max(abs(pos[0] - c[0]), abs(pos[1] - c[1])) for c in cells) <= ARM_TILES

        armed = close(here)
        self._map_events(pid, run, armed)
        actor.walk(tile, wait=False)
        far = max(abs(tile[0] - here[0]), abs(tile[1] - here[1]))
        end = self._clock() + min(30.0, 4.0 + far * WALK_PER_TILE)
        still = 0
        while self._clock() < end:
            self._wait(run, 0.5)
            last = here
            _st, _objs, here = self._look(pid, run, stage, part, areas)
            if here in cells:
                return
            if (
                not areas.walkable(tile)
                and max(abs(here[0] - tile[0]), abs(here[1] - tile[1])) <= 1
            ):
                return  # a zone cell in a wall: beside it is as far as a walk goes
            if not armed and close(here):
                armed = True
                self._map_events(pid, run, True)
                actor.walk(tile, wait=False)  # walk on into the zone with events on
                still = 0
                continue
            still = still + 1 if here == last else 0
            if still >= WALK_STALL:
                actor.walk(tile, wait=False)
                still = 0

    def _map_events(self, pid: int, run: _Run, on: bool) -> None:
        """The client's map event switch (hook `mapevents`): zones fire only while
        it is 1. The navigator leaves it 0 after its walks, a mouse click sets it
        (live 2026-10-09: the entrance zone did nothing until the user clicked)."""
        if not run.events_ok:
            return
        r = self._cmd(pid, run, f"mapevents {1 if on else 0}")
        if not r.get("ok") and r.get("error") != "busy":
            run.events_ok = False  # an older hook without the command
            log.info("dungeon pid=%d no mapevents: %s", pid, r.get("error"))

    def _step_off(self, tile: Tile, here: Tile, part, areas: MapAreas) -> Tile | None:
        """A tile STEP_OFF away from a zone tile, in this area, on our side if we can."""
        dx = (here[0] > tile[0]) - (here[0] < tile[0])
        dy = (here[1] > tile[1]) - (here[1] < tile[1])
        dirs = [(dx, dy)] if (dx, dy) != (0, 0) else []
        dirs += [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)]
        for ddx, ddy in dirs:
            t = (tile[0] + ddx * STEP_OFF, tile[1] + ddy * STEP_OFF)
            if areas.area_of(t) == part:
                return t
        return None

    def _wait_bodies(self, pid, run, groups, stage, part, areas) -> None:
        """A portal opens only after the bodies of its groups faded (~5.5 s)."""
        if not groups:
            return
        ids = self._npcs_of(groups, areas)
        end = self._clock() + BODIES_MAX
        while self._clock() < end:
            _st, objs, _tile = self._look(pid, run, stage, part, areas)
            if not any(o.get("dead") and o.get("id") in ids for o in objs):
                return
            self._set_step(run, "等屍體消失")
            self._wait(run, 0.5)

    def _hold(self, pid, run, step: Hold, stage, part, areas) -> None:
        boss = self._npcs_of((step.until_dead,), areas)
        mobs = self._npcs_of(step.fight, areas)
        elites = boss | self._elites(step.fight, areas)
        seen_boss = swept = False
        start = idle_since = self._clock()
        while self._clock() - start < HOLD_MAX:
            st, objs, _tile = self._look(pid, run, stage, part, areas)
            live_boss = self._targets(pid, run, objs, boss, part, areas)
            live = live_boss + self._targets(pid, run, objs, mobs, part, areas)
            if live_boss:
                seen_boss = True
            elif seen_boss and any(o.get("dead") and o.get("id") in boss for o in objs):
                self._note(run, "confirmed", "王倒了")
                return
            if live:
                self._set_step(
                    run, "打王中" if live_boss else f"等王出現，先清小怪（{len(live)} 隻）"
                )
                self._fight(pid, run, st, objs, live, elites)
                idle_since = self._clock()
                swept = False
                self._wait(run, STEP)
                continue
            if not swept:
                # The hall is wider than the view (天市垣: spawns at x 113 and
                # 125, the exit at 104; live 2026-10-09 it stood at the exit
                # waiting): look at the spawn points before waiting.
                swept = True
                groups = (*step.fight, step.until_dead)
                if self._sweep(pid, run, groups, mobs | boss, stage, part, areas):
                    continue
            if self._clock() - idle_since >= HOLD_IDLE:
                return  # nothing here for a while: someone may have killed the boss
            self._set_step(run, "等怪重生")
            self._wait(run, 1.0)
        raise _Done(f"等王超過 {HOLD_MAX / 60:.0f} 分鐘", "error")

    def _talk(self, pid, run, step: Talk, areas: MapAreas) -> None:
        if step.mission and not run.config.mission:
            return
        if id(step) in run.talked:
            return
        actor = self._actor(pid, run)
        tiles = areas.npcs.get(step.npc) or []
        npc = next((o for o in actor.near() if o.get("id") == step.npc), None)
        if npc is None and tiles:
            self._set_step(run, "走到 NPC 旁邊")
            actor.walk(tiles[0])
            npc = next((o for o in actor.near() if o.get("id") == step.npc), None)
        if npc is None:
            if step.optional:
                self._note(run, "unconfirmed", f"附近找不到 NPC {step.npc}，略過")
                run.talked.add(id(step))
                return
            raise _Done(f"附近找不到 NPC {step.npc}", "error")
        self._set_step(run, "和 NPC 對話")
        r = actor.talk(npc, step.want, frozenset(), once=True)
        run.talked.add(id(step))
        if r in ("closed", "map"):
            self._note(run, "confirmed", "對話完成")
        elif step.optional:
            self._note(run, "info", "對話沒有要選的選項（可能已經接過或回報過）")
        else:
            raise _Done(f"和 NPC {step.npc} 的對話沒有走完（{r}）", "error")
