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
import struct
import threading
import time
from collections import deque
from typing import Callable

from services.api_types import (
    AttackSkillCandidate,
    CombatRule,
    TowerConfig,
    TowerFloor,
    TowerLogEntry,
    TowerRecord,
    TowerEstimate,
    TowerSettings,
    TowerStatus,
    TowerView,
)
from services.combat import (
    BUFF_PAUSE,
    RELOCK_AFTER,
    AttackSkill,
    Rotation,
    cast_sent,
    cast_taken,
    load_attack_skills,
    next_skill,
    mobbed_by,
    settle_cast,
    pick_target,
    retarget,
)
from services.game_input import leave_game
from services.guard import GuardManager, GuardStore, read_holdings, read_learned, read_stage_id
from services.hook_cmd import CommandChannel, NoReply, PipeBusy, PipeGone
from services.tower import (
    FOX_AVOID,
    FOX_STAGE,
    LEAVE,
    LOBBY_STAGE,
    ROOMS,
    TOO_LOW,
    YAN,
    YAN_AVOID,
    YAN_WANT,
    TowerStage,
    estimate_reach,
    floor_gates,
    load_tower,
    pick_option,
)

log = logging.getLogger("tthol.tower")

COMBAT_SECTION = "combat"
TOWER_SECTION = "tower"
RECORD_SECTION = "tower.record"
ATTACK_PACKET = 0x43  # an attack landing: target key, attacker key, skill, hits
# a cast starting: 10 <caster key> <u32 skill code> <tile x> <tile y> <target key> 01
CAST_START_PACKET = 0x10
TOWER_COMMANDS = ("status", "near", "walk", "attack", "cast", "talk", "dialog", "option", "next")
TILE_PX = 40
LOGOUT_WAIT = 6.0  # after clicking 登出遊戲, for the character to leave the game
POTION_EVERY = 1.0  # count the whitelisted potions this often while fighting
LOG_KEEP = 200

STEP = 0.05  # between two fight decisions (the puppet runs every 45 ms)
WAIT_BODIES = 0.3
WAIT_MAP = 0.5  # own character not built yet after a map change
IDLE_SWEEP = 3  # empty looks in a row before walking the spawn points
SWEEP_WAIT = 8.0  # per spawn point, for a monster to show up
EXIT_TRIES = 8
EXIT_DIALOG = 2.0  # after stepping on the exit, for its dialog to open
WALK_WAIT = 6.0
TALK_WAIT = 40.0
FOX_RETALK = 6.0  # after a fox talk, give the teleport this long before talking again
CAPS_EVERY = 30.0  # re-read the hook's command list this often for the view
BUFF_WAIT = 30.0  # at the 關 start, wait at most this long for the guard's buffs
PIPE_RETRIES = 5  # a command pipe that is briefly unavailable (map load) is retried


def own_tile(st: dict, objs: list[dict]) -> tuple[int, int]:
    """The character's tile from its own `near` entry (pixels / 40).

    `status`'s tile lags a teleport inside one map (九尾狐仙 -> room 1, the
    exits): it only moves on the next step, so it is the fallback only.
    """
    me = next((o for o in objs if o.get("h") == st.get("self")), None)
    if me is not None:
        return me["x"] // TILE_PX, me["y"] // TILE_PX
    return st["tile"][0], st["tile"][1]


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
    def __init__(self, reason: str, phase: str = "info") -> None:
        super().__init__(reason)
        self.reason = reason
        self.phase = phase


class _Line:
    def __init__(self, id_: int, ts: float, phase: str, text: str) -> None:
        self.id, self.ts, self.phase, self.text = id_, ts, phase, text


class _Run:
    def __init__(self, name: str, combat: CombatRule, config: TowerConfig) -> None:
        self.name = name
        self.combat = combat
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
        self.staged = False
        self.force_exit = False  # the spawn points were empty: try the exit anyway
        self.room_open = False  # `room` holds until its exit goes through
        self.idle = 0
        self.rot = Rotation()
        self.casts_seen = 0
        self.hits: dict[tuple[int, int], float] = {}  # target key -> clock of our last hit
        # target key -> magic id -> clock of our last hit with it (0 = basic attack)
        self.hit_skills: dict[tuple[int, int], dict[int, float]] = {}
        self.taken: dict[int, float] = {}  # magic id -> clock of our last cast start (0x10)
        self.last_taken: tuple[float, int] | None = None  # (clock, skill code), newest
        self.taken_seen: float = -1.0  # last_taken already folded into the rotation
        self.learned: dict[int, int] = {}
        self.fox_at = -FOX_RETALK  # clock of the last fox talk
        self.potions_at = -POTION_EVERY  # clock of the last potion count
        self.start_since: float | None = None  # clock we got to the 關 start
        self.navigated = False  # walked to 玄天之境 from elsewhere (once a run)


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
    ) -> None:
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
        self._caps: dict[int, tuple[float, frozenset[str]]] = {}  # pid -> (clock, commands)

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

    def start(self, pid: int) -> tuple[bool, str | None]:
        name = self._character_name(pid)
        if not name:
            return False, "角色還沒定位"
        combat = self._store.load_section(name, COMBAT_SECTION, CombatRule)
        if not combat.basic and not combat.opener and not combat.rotation:
            return False, "還沒設定攻擊方式：勾普攻，或選至少一個技能"
        if not self._store.load(name).potion.hp_items:
            return False, "補水的體力白名單是空的：塔裡不能回城，先在「輔助」設定補水"
        try:
            missing = self._missing_commands(pid)
        except PipeGone:
            return False, "這個遊戲視窗沒有 hook 指令通道"
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
            run = _Run(name, combat, self._store.load_section(name, TOWER_SECTION, TowerConfig))
            run.run_started = self._wall()
            self._runs[pid] = run
            run.thread = threading.Thread(
                target=self._loop, args=(pid, run), daemon=True, name=f"tower-{pid}"
            )
            run.thread.start()
        log.info("tower started pid=%d", pid, extra={"cat": "tower"})
        return True, None

    def stop(self, pid: int) -> None:
        with self._lock:
            run = self._runs.get(pid)
        if run is not None:
            run.stop.set()

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
        """0x43 (an attack landing): note our own hits on each target."""
        if own_key is None or len(raw) < 0x1B or raw[11:21] != own_key:
            return
        with self._lock:
            run = self._runs.get(pid)
        if run is None:
            return
        # Keyed by (npc id, instance): the kind word differs between `near` and
        # 0x43 in the tower (near lists monsters as kind 11).
        target = struct.unpack_from("<II", raw, 3)
        magic = struct.unpack_from("<I", raw, 0x15)[0] // 100  # magic * 100 + level
        now = self._clock()
        with run.lock:
            run.hits[target] = now
            run.hit_skills.setdefault(target, {})[magic] = now

    def on_cast_packet(self, pid: int, raw: bytes, _ts: float, own_key: bytes | None) -> None:
        """0x10 (a cast starting): the server took one of our casts."""
        if own_key is None or len(raw) < 15 or raw[1:11] != own_key:
            return
        with self._lock:
            run = self._runs.get(pid)
        if run is None:
            return
        code = struct.unpack_from("<I", raw, 11)[0]
        now = self._clock()
        with run.lock:
            run.taken[code // 100] = now
            run.last_taken = (now, code)
        log.debug("tower cast taken code=%d t=%.3f", code, now, extra={"cat": "tower"})

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
        reach = estimate_reach(hit, level, [s for s in stages if s], self._gates)
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
            missing_buffs=missing,
            applied=applied,
        )

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
        now = self._clock()
        cached = self._caps.get(pid)
        if cached is not None and now - cached[0] < max_age:
            have = cached[1]
        else:
            reply = self._channel.send(pid, "caps")
            cmds = reply.get("commands", []) if reply.get("ok") else []
            have = frozenset(c.get("cmd") for c in cmds)
            self._caps[pid] = (now, have)
        return [c for c in TOWER_COMMANDS if c not in have]

    def _hook_ready(self, pid: int) -> bool:
        """For the view, polled every second: cached, and a missing pipe is just "no"."""
        try:
            return self._missing_commands(pid, max_age=CAPS_EVERY) == []
        except (PipeGone, PipeBusy, NoReply):
            cached = self._caps.get(pid)
            return cached is not None and not [c for c in TOWER_COMMANDS if c not in cached[1]]

    def _skill_candidates(self, pid: int) -> list[AttackSkillCandidate]:
        try:
            learned = self._read_locked(pid, read_learned) or {}
        except Exception:
            learned = {}
        out = []
        for mid, level in sorted(learned.items()):
            d = self._defs().get((mid, level))
            if d is not None:
                out.append(
                    AttackSkillCandidate(
                        magic_id=mid,
                        level=level,
                        name=d.name,
                        mp=d.mp,
                        area=d.area,
                        gap_ms=d.gap_ms,
                    )
                )
        return out

    def _note(self, run: _Run, phase: str, text: str) -> None:
        with run.lock:
            run.next_id += 1
            run.log.append(_Line(run.next_id, self._wall(), phase, text))

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
        ended, phase = "已停止", "info"
        try:
            while True:
                self._wait(run, self._tick(pid, run))
        except _Done as done:
            ended, phase = done.reason, done.phase
        except Exception:
            log.exception("tower loop failed pid=%d", pid, extra={"cat": "tower"})
            ended, phase = "登塔出錯停止（詳見診斷紀錄）", "error"
        finally:
            run.stop.set()
            self._note(run, phase, f"登塔結束：{ended}")
            self._save_record(run, ended)
            log.info("tower stopped pid=%d: %s", pid, ended, extra={"cat": "tower"})

    def _save_record(self, run: _Run, ended: str) -> None:
        date = time.strftime("%Y-%m-%d", time.localtime(run.run_started or self._wall()))
        old = self._store.load_section(run.name, RECORD_SECTION, TowerRecord)
        top = max((f.floor for f in run.floors), default=0)
        if old.date == date:
            top = max(top, old.top_floor)
        self._store.save_section(
            run.name, RECORD_SECTION, TowerRecord(date=date, top_floor=top, ended=ended)
        )

    def _tick(self, pid: int, run: _Run) -> float:
        st = self._cmd(pid, run, "status")
        if not st.get("ok") or not isinstance(st.get("tile"), list):
            with run.lock:
                run.problem = "等角色載入（換地圖中）"
            return WAIT_MAP
        hp = st.get("hp") or [0, 0]
        if hp[0] <= 0:
            raise _Done("角色死亡", "error")
        try:
            stage = self._read_locked(pid, read_stage_id)
        except Exception:
            stage = None
        if stage is None:
            return WAIT_MAP
        stage_id, stage_name = stage
        if stage_id == LOBBY_STAGE:
            if run.cleared:
                raise _Done("被送出塔：等級不夠進下一層，或已經是最後一關")
            return self._enter_lobby(pid, run)
        tower = self._tower(stage_id)
        if tower is None:
            return self._go_to_lobby(pid, run, stage_name)
        if run.stage is None or run.stage.stage_id != stage_id:
            with run.lock:
                run.stage, run.stage_name, run.room = tower, stage_name, None
                run.room_open = False
            self._note(
                run, "info", f"進入{stage_name}（第 {tower.floor(1)}–{tower.floor(ROOMS)} 層）"
            )
        objs = self._near(pid, run)
        tile = own_tile(st, objs)
        if tower.at_start(tile):
            run.room_open = False
            return self._at_start(pid, run, tower)
        run.start_since = None
        return self._room_tick(pid, run, tower, st, tile, objs)

    def _go_to_lobby(self, pid: int, run: _Run, stage_name: str) -> float:
        """Started somewhere else: walk to 玄天之境 once, before any floor."""
        if self._navigator is None or run.navigated or run.floors:
            raise _Done(f"不在玄天之境或玄天塔裡（{stage_name}）", "error")
        run.navigated = True
        self._set_step(run, f"從{stage_name}前往玄天之境")
        self._note(run, "info", f"從{stage_name}導航到玄天之境")
        result = self._navigator.go(
            pid, LOBBY_STAGE, stop=run.stop, note=lambda text: self._set_step(run, text)
        )
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
        r = self._talk(pid, run, yan, YAN_WANT, YAN_AVOID)
        if r != "map":
            raise _Done(f"和燕飄風對話沒有進塔（{r}）", "error")
        self._note(run, "sent", "和燕飄風對話，進塔")
        return WAIT_MAP

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
            raise _Done(f"{short}，要登出但找不到遊戲視窗", "error")
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
        # Fixed from entering a room until its exit goes through (like tower.py):
        # by an exit another room's start can be nearer than this room's own.
        room = run.room if run.room_open else tower.room_of(tile)
        if room != run.room or not run.room_open:
            run.room_open = True
            with run.lock:
                run.room, run.killed, run.kills = room, set(), 0
                run.room_started = self._wall()
                run.staged, run.idle = False, 0
                run.rot = Rotation(margins=run.rot.margins)  # margins are per skill, not per room
                run.force_exit = False
                run.hits, run.hit_skills = {}, {}
            try:
                run.learned = self._read_locked(pid, read_learned) or run.learned
            except Exception:
                pass
            self._note(run, "info", f"第 {tower.floor(room)} 層（第 {room} 房）")
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
        if run.kills >= expect or run.force_exit:
            return self._finish_room(pid, run, tower, room, bool(mine))
        live = [o for o in mine if not o.get("dead") and (o["id"], o["inst"]) not in run.killed]
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
        rot = run.rot
        me = next((o for o in objs if o.get("h") == st.get("self")), None)
        target = next((o for o in live if o["h"] == rot.target), None)
        mx, my = (me["x"], me["y"]) if me else (0, 0)
        if target is not None and run.stage is not None:
            swap = mobbed_by(target, live, (mx, my), run.combat, run.stage.elites)
            if swap is not None:
                self._note(run, "info", "小怪圍過來了，先打小怪再回頭打菁英")
                target = swap
                retarget(rot, target["h"], now)
        if target is None:
            target = pick_target(
                live, (mx, my), run.combat, run.stage.strength() if run.stage else {}
            )
            retarget(rot, target["h"], now)
            log.debug(
                "tower target new=%s npc=%s t=%.3f",
                target["h"],
                target["id"],
                now,
                extra={"cat": "tower"},
            )
        casts = self._guard.cast_count(pid)
        if casts != run.casts_seen:
            run.casts_seen = casts
            rot.paused_until, rot.attacked = now + BUFF_PAUSE, False
        if now < rot.paused_until:
            return STEP
        key = (target["id"], target["inst"])
        with run.lock:
            hit = run.hits.get(key)
            by_skill = dict(run.hit_skills.get(key, {}))
        if hit is not None:
            rot.last_hit = max(rot.last_hit, hit)
        with run.lock:
            taken, last_taken = dict(run.taken), run.last_taken
        if last_taken is not None and last_taken[0] > run.taken_seen:
            run.taken_seen = last_taken[0]
            code = last_taken[1]
            cast_taken(rot, self._defs().get((code // 100, code % 100)), last_taken[0], code // 100)
        if rot.pending is not None:
            mid = rot.pending[0]
            seen = max(taken.get(mid, -1.0), by_skill.get(mid, -1.0))
            if settle_cast(rot, seen if seen >= 0 else None, now) == "gave_up":
                name = next((d.name for (m, _l), d in self._defs().items() if m == mid), mid)
                self._note(run, "unconfirmed", f"{name} 連續 4 次沒被伺服器接受，先放下一招")
        if now - rot.last_hit > RELOCK_AFTER:
            rot.attacked, rot.last_hit = False, now  # nothing landing: lock on again
        mp = (st.get("mp") or [0, 0])[0]
        pick = next_skill(run.combat, rot, run.learned, self._defs(), mp, now)
        if pick is not None:
            mid, level, d = pick
            r = self._cmd(pid, run, f"cast {mid * 100 + level} {target['h']}")
            log.debug(
                "tower cast sent code=%d t=%.3f ok=%s target=%s dist=%.1f state=%s retry=%d",
                mid * 100 + level,
                now,
                r.get("ok"),
                target["h"],
                (((target["x"] - mx) ** 2 + (target["y"] - my) ** 2) ** 0.5) / TILE_PX,
                st.get("state"),
                rot.drops,
                extra={"cat": "tower"},
            )
            if r.get("ok"):
                cast_sent(rot, mid, now)
            return STEP
        if run.combat.basic and not rot.attacked:
            if self._cmd(pid, run, f"attack {target['h']}").get("ok"):
                rot.attacked = True
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
        if not run.staged:
            run.staged = True
            self._set_step(run, "全部擊倒，等屍體消失")
            self._walk(pid, run, tower.staging(room), wait=False)
        if bodies:
            return WAIT_BODIES
        floor = tower.floor(room)
        leave = run.config.stop_floor is not None and floor >= run.config.stop_floor
        short = self._short_of_potions(pid, run, "leave")
        if short:
            leave = True
            self._note(run, "info", f"{short}，過完這層就離開塔")
        self._set_step(run, "走向出口")
        r = self._bump_exit(pid, run, tower, room, leave)
        if r in ("closed", "map", "left"):
            secs = self._wall() - (run.room_started or self._wall())
            with run.lock:
                run.floors.append(TowerFloor(floor=floor, secs=round(secs, 1)))
                run.cleared = True
                run.room_open = False  # the next room is read from where we land
            self._note(
                run, "confirmed", f"第 {floor} 層通過（{int(secs) // 60}:{int(secs) % 60:02d}）"
            )
            if r == "left":
                if short:
                    raise _Done(f"打到第 {floor} 層，{short}，離開塔")
                raise _Done(f"打到第 {floor} 層，照設定離開塔")
            if r == "map" and room < ROOMS:
                raise _Done(f"第 {floor} 層之後被送出塔（等級不夠進下一層？）")
            return WAIT_MAP
        if r == "low":
            raise _Done(f"第 {floor} 層之後等級不夠進下一層")
        if r == "no-choice":
            raise _Done("出口對話沒有選項：背包滿或超重，清出空間後再開始", "error")
        if r == "stuck":
            raise _Done("出口對話的選項認不得，先停下來", "error")
        self._note(run, "unconfirmed", "出口沒有反應，再試一次")
        run.staged = run.force_exit = False
        return STEP

    def _bump_exit(self, pid: int, run: _Run, tower: TowerStage, room: int, leave: bool) -> str:
        """Stand off, step onto each exit tile in turn; give each touch time to open the dialog."""
        stand = tower.staging(room)
        tiles = tower.exit_tiles(room)
        for attempt in range(EXIT_TRIES):
            self._walk(pid, run, tiles[attempt % len(tiles)])
            r = self._dialog(pid, run, (), frozenset({LEAVE}), EXIT_DIALOG, leave=leave)
            if r != "none":
                return r
            self._walk(pid, run, stand)
        return "none"

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

    def _talk(self, pid: int, run: _Run, npc: dict, want, avoid) -> str:
        self._cmd(pid, run, f"talk {npc['h']}")
        return self._dialog(pid, run, want, avoid, TALK_WAIT)

    def _dialog(self, pid: int, run: _Run, want, avoid, timeout: float, leave: bool = False) -> str:
        """Drive the dialog by jump id. map / closed / no-choice / low / stuck / none / timeout."""
        st = self._cmd(pid, run, "status")
        me0 = st.get("self")
        end = self._clock() + timeout
        seen = chose = False
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
                i = pick_option(options, want, avoid)
                if i is None:
                    return "stuck"
                self._cmd(pid, run, f"option {i}")
                chose = True
                continue
            if d.get("next") == TOO_LOW:
                return "low"
            self._cmd(pid, run, "next")
        return "timeout" if seen else "none"
