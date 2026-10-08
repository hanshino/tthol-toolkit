"""打怪 module: fight the monsters around a spot on any map until stopped.

The spot is where the character stands at the start; monsters within `radius`
tiles of it are fair game (optionally only some npc ids). With nothing to
fight it walks back to the spot and waits for respawns. Fighting is the shared
battle-puppet fighter (services/combat.py) with the character's one
CombatRule; potions, buffs and the hero transform are the guard's: the module
starts it.

It stops on a stop, a death, or a map change (a death or a teleport took the
character away: walking "back" on another map makes no sense).

Not fair game: players, followers / summons (owner tag read from memory,
matched by packet key), npcs flagged no_attack or not monsters, and map
hazards with 999999 HP (凌霄閣's 毒氣 / 迷香).
"""

from __future__ import annotations

import functools
import logging
import sqlite3
import threading
import time
from collections import deque
from collections.abc import Callable

from services import run_log
from services._paths import bundled
from services.api_types import (
    CombatRule,
    GrindConfig,
    GrindMonster,
    GrindSettings,
    GrindStatus,
    GrindView,
    TowerLogEntry,
)
from services.combat import (
    COMBAT_SECTION,
    TILE_PX,
    AttackSkill,
    Fighter,
    attack_problem,
    load_attack_skills,
    skill_candidates,
)
from services.guard import GuardManager, GuardStore, read_learned, read_stage_id
from services.hook_caps import FEATURES, HookCaps
from services.hook_cmd import CommandChannel, NoReply, PipeBusy, PipeGone
from services.nearby import PLAYER_NPC_IDS, read_nearby

log = logging.getLogger("tthol.grind")

SECTION = "grind"
GRIND_COMMANDS = FEATURES["grind"]
HAZARD_HP = 999999  # map hazards (毒氣, 迷香): monsters nobody kills
LOG_KEEP = 200

STEP = 0.05  # between two fight decisions (the puppet runs every 45 ms)
WAIT_IDLE = 0.5  # nothing to fight: look again
WAIT_MAP = 0.5  # own character not built yet after a map change
DEATH_CONFIRM = 3.0  # HP 0 this long in a row is a death; a map load reads 0 a moment
FOLLOWERS_EVERY = 1.0  # re-read the owner tags (memory) this often
WALK_EVERY = 2.0  # walking back to the spot: resend the walk at most this often
PIPE_RETRIES = 5
CAPS_EVERY = 30.0


@functools.lru_cache(maxsize=1)
def npc_table() -> dict[int, tuple[str, int | None, bool]]:
    """npc.id -> (name, level, attackable monster), loaded once."""
    path = bundled("tthol.sqlite")
    if not path.exists():
        return {}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.text_factory = lambda b: b.decode("utf-8", errors="replace")
    try:
        rows = con.execute("SELECT id, name, level, is_monster, no_attack, hp FROM npc").fetchall()
    finally:
        con.close()
    return {
        i: (name or f"#{i}", level, bool(monster) and not no_attack and (hp or 0) < HAZARD_HP)
        for i, name, level, monster, no_attack, hp in rows
    }


def tile_of(o: dict) -> tuple[int, int]:
    return o["x"] // TILE_PX, o["y"] // TILE_PX


def fair_game(
    objs: list[dict],
    npcs: dict[int, tuple[str, int | None, bool]],
    followers: set[tuple[int, int]],
    anchor: tuple[int, int],
    radius: int,
    only: list[int],
) -> list[dict]:
    """The live monsters (`near` entries) this run may fight."""
    out = []
    for o in objs:
        nid = o.get("id")
        if o.get("dead") or nid in PLAYER_NPC_IDS or (nid, o.get("inst")) in followers:
            continue
        info = npcs.get(nid)
        if info is None or not info[2] or (only and nid not in only):
            continue
        x, y = tile_of(o)
        if max(abs(x - anchor[0]), abs(y - anchor[1])) <= radius:
            out.append(o)
    return out


class _Done(Exception):
    def __init__(self, reason: str, phase: str = "info") -> None:
        super().__init__(reason)
        self.reason, self.phase = reason, phase


class _Run:
    def __init__(self, name: str, combat: CombatRule, config: GrindConfig) -> None:
        self.name = name
        self.pid: int | None = None  # for the run record
        self.fighter = Fighter(combat, log, cat="grind")
        self.config = config
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.log: deque[TowerLogEntry] = deque(maxlen=LOG_KEEP)
        self.next_id = 0
        self.step: str | None = None
        self.problem: str | None = None
        self.warning: str | None = None
        self.target: str | None = None
        self.stage: int | None = None
        self.anchor: tuple[int, int] | None = None
        self.kills = 0
        self.counted: set[tuple[int, int]] = set()  # counted bodies still in view
        self.followers: set[tuple[int, int]] = set()
        self.followers_at = -FOLLOWERS_EVERY
        self.walked_at = -WALK_EVERY
        self.zero_hp_at: float | None = None
        self.started: float | None = None
        self.ended: str | None = None
        self.user_stop = False


class GrindManager:
    def __init__(
        self,
        guard: GuardManager,
        read_locked: Callable[[int, Callable], object],
        character_name: Callable[[int], str | None],
        channel: CommandChannel | None = None,
        store: GuardStore | None = None,
        attack_skills: Callable[[], dict[tuple[int, int], AttackSkill]] = load_attack_skills,
        npcs: Callable[[], dict[int, tuple[str, int | None, bool]]] = npc_table,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        wait: Callable[[threading.Event, float], bool] = lambda ev, secs: ev.wait(secs),
        hook_caps: HookCaps | None = None,
        busy: Callable[[int], str | None] = lambda _pid: None,
    ) -> None:
        self._guard = guard
        self._read_locked = read_locked
        self._character_name = character_name
        self._channel = channel or CommandChannel()
        self._store = store or GuardStore()
        self._load_attack_skills = attack_skills
        self._attack_skills: dict[tuple[int, int], AttackSkill] | None = None
        self._npcs = npcs
        self._clock = clock
        self._wall = wall
        self._sleep = wait
        self._hook_caps = hook_caps or HookCaps(self._channel, clock)
        self._busy = busy
        self._runs: dict[int, _Run] = {}
        self._lock = threading.Lock()

    # -- API -----------------------------------------------------------------

    def view(self, pid: int) -> GrindView:
        name = self._character_name(pid)
        status = self.status(pid)
        if not name:
            return GrindView(status=status)
        config = self._store.load_section(name, SECTION, GrindConfig)
        return GrindView(
            status=status,
            combat=self._store.load_section(name, COMBAT_SECTION, CombatRule),
            config=config,
            skills=skill_candidates(self._learned(pid), self._defs()),
            monsters=self._monsters(pid, config.only),
            hook_ready=self._hook_ready(pid),
        )

    def save_settings(self, pid: int, settings: GrindSettings) -> GrindSettings | None:
        name = self._character_name(pid)
        if not name:
            return None
        self._store.save_section(name, COMBAT_SECTION, settings.combat)
        self._store.save_section(name, SECTION, settings.config)
        with self._lock:
            run = self._runs.get(pid)
        if run is not None and run.name == name:
            run.fighter.rule, run.config = settings.combat, settings.config
        return settings

    def start(self, pid: int) -> tuple[bool, str | None]:
        name = self._character_name(pid)
        if not name:
            return False, "角色還沒定位"
        busy = self._busy(pid)
        if busy:
            return False, busy
        combat = self._store.load_section(name, COMBAT_SECTION, CombatRule)
        problem = attack_problem(combat)
        if problem:
            return False, problem
        try:
            have = self._hook_caps.get(pid) or frozenset()
        except PipeGone:
            return False, "這個遊戲視窗沒有 hook 指令通道"
        except (PipeBusy, NoReply):
            return False, "hook 暫時沒有回應（還在登入或換地圖？），稍後再開始"
        missing = [c for c in GRIND_COMMANDS if c not in have]
        if missing:
            return False, f"這個 hook 缺少打怪要用的指令：{'、'.join(missing)}"
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
            run = _Run(name, combat, self._store.load_section(name, SECTION, GrindConfig))
            run.pid, run.started = pid, self._wall()
            if not self._store.load(name).potion.hp_items:
                # On a normal map the character can walk to town: run anyway.
                run.warning = "補水的體力白名單是空的，守護不會喝水"
            self._runs[pid] = run
            run.thread = threading.Thread(
                target=self._loop, args=(pid, run), daemon=True, name=f"grind-{pid}"
            )
            run.thread.start()
        log.info("grind started pid=%d", pid, extra={"cat": "grind"})
        return True, None

    def stop(self, pid: int) -> None:
        with self._lock:
            run = self._runs.get(pid)
        if run is not None:
            if not run.stop.is_set():
                run.user_stop = True
            run.stop.set()

    def forget(self, pid: int) -> None:
        """Another character on this window: stop the old one's run, drop its log."""
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
        return (
            run is not None
            and run.thread is not None
            and run.thread.is_alive()
            and not run.stop.is_set()
        )

    def status(self, pid: int) -> GrindStatus:
        with self._lock:
            run = self._runs.get(pid)
        if run is None:
            return GrindStatus(running=False, character=self._character_name(pid))
        running = self.running(pid)
        with run.lock:
            return GrindStatus(
                running=running,
                character=run.name,
                step=run.step if running else None,
                problem=run.problem if running else None,
                warning=run.warning,
                target=run.target if running else None,
                kills=run.kills,
                anchor=list(run.anchor) if run.anchor else None,
                started=run.started,
                ended=None if running else run.ended,
                log=list(run.log),
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

    # -- helpers ---------------------------------------------------------------

    def _defs(self) -> dict[tuple[int, int], AttackSkill]:
        if self._attack_skills is None:
            self._attack_skills = self._load_attack_skills()
        return self._attack_skills

    def _learned(self, pid: int) -> dict[int, int]:
        try:
            return self._read_locked(pid, read_learned) or {}
        except Exception:
            return {}

    def _hook_ready(self, pid: int) -> bool:
        """For the view, polled every few seconds: cached, a missing pipe is just "no"."""
        try:
            have = self._hook_caps.get(pid, CAPS_EVERY)
        except (PipeGone, PipeBusy, NoReply):
            have = self._hook_caps.cached(pid)
        return have is not None and all(c in have for c in GRIND_COMMANDS)

    def _monsters(self, pid: int, only: list[int]) -> list[GrindMonster]:
        """Monster kinds in view (from memory, no hook needed), plus the picked ones."""
        npcs = self._npcs()
        counts: dict[int, int] = {}
        try:
            objects, _own = self._read_locked(pid, read_nearby) or ([], None)
        except Exception:
            objects = []
        for o in objects:
            info = npcs.get(o.npc_id)
            if o.is_self or o.tag or o.npc_id in PLAYER_NPC_IDS or info is None or not info[2]:
                continue
            counts[o.npc_id] = counts.get(o.npc_id, 0) + 1
        for nid in only:
            counts.setdefault(nid, 0)
        out = [
            GrindMonster(
                npc_id=nid,
                name=npcs.get(nid, (f"#{nid}", None, False))[0],
                level=npcs.get(nid, (None, None, False))[1],
                count=n,
            )
            for nid, n in counts.items()
        ]
        return sorted(out, key=lambda m: (-m.count, m.level or 0, m.npc_id))

    def _note(self, run: _Run, phase: str, text: str) -> None:
        with run.lock:
            run.next_id += 1
            run.log.append(TowerLogEntry(id=run.next_id, ts=self._wall(), phase=phase, text=text))
        run_log.note("grind", run.pid, run.name, text, phase=phase)

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

    # -- the loop ----------------------------------------------------------------

    def _loop(self, pid: int, run: _Run) -> None:
        self._note(run, "info", "開始打怪")
        ended, phase = "已停止", "info"
        try:
            run.fighter.learned = self._learned(pid)
            while True:
                self._wait(run, self._tick(pid, run))
        except _Done as done:
            ended, phase = done.reason, done.phase
        except Exception:
            log.exception("grind loop failed pid=%d", pid, extra={"cat": "grind"})
            ended, phase = "打怪出錯停止（詳見診斷紀錄）", "error"
        finally:
            with run.lock:
                run.ended = ended
            run.stop.set()
            self._note(run, phase, f"打怪結束：{ended}（擊殺 {run.kills}）")
            log.info("grind stopped pid=%d: %s", pid, ended, extra={"cat": "grind"})

    def _tick(self, pid: int, run: _Run) -> float:
        st = self._cmd(pid, run, "status")
        if not st.get("ok"):
            with run.lock:
                run.problem = "等角色載入（換地圖中）"
            return WAIT_MAP
        now = self._clock()
        hp = st.get("hp") or [0, 0]
        if hp[0] <= 0:
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
        if run.stage is None:
            run.stage = stage[0]
        elif stage[0] != run.stage:
            raise _Done(f"換到別張地圖了（{stage[1]}），停止打怪", "error")
        objs = self._cmd(pid, run, "near").get("objects") or []
        me = next((o for o in objs if o.get("h") == st.get("self")), None)
        # `status`'s tile goes stale after a same-map teleport; near's pixels do not.
        here = tile_of(me) if me else tuple(st.get("tile") or (0, 0))[:2]
        if run.anchor is None:
            with run.lock:
                run.anchor = (here[0], here[1])
            self._note(
                run, "info", f"以 ({here[0]}, {here[1]}) 為中心，打 {run.config.radius} 格內的怪"
            )
        self._refresh_followers(pid, run, now)
        self._count_kills(run, objs)
        live = fair_game(
            objs, self._npcs(), run.followers, run.anchor, run.config.radius, run.config.only
        )
        if live:
            run.fighter.tick(
                live,
                (me["x"], me["y"]) if me else (here[0] * TILE_PX, here[1] * TILE_PX),
                {},
                frozenset(),
                self._defs(),
                (st.get("mp") or [0, 0])[0],
                self._guard.cast_count(pid),
                now,
                send=lambda line: self._cmd(pid, run, line),
                note=lambda phase, text: self._note(run, phase, text),
                state=st.get("state"),
            )
            target = next((o for o in live if o["h"] == run.fighter.rot.target), None)
            name = self._npcs().get(target["id"], ("?",))[0] if target else None
            with run.lock:
                run.target = name
            self._set_step(run, f"打怪中：{name}" if name else "打怪中")
            return STEP
        with run.lock:
            run.target = None
        ax, ay = run.anchor
        if max(abs(here[0] - ax), abs(here[1] - ay)) > max(2, run.config.radius // 2):
            self._set_step(run, "附近沒有怪，走回起點")
            if now - run.walked_at >= WALK_EVERY:
                run.walked_at = now
                self._cmd(
                    pid, run, f"walk {ax * TILE_PX + TILE_PX // 2} {ay * TILE_PX + TILE_PX // 2}"
                )
        else:
            self._set_step(run, "等怪出現")
        return WAIT_IDLE

    def _refresh_followers(self, pid: int, run: _Run, now: float) -> None:
        """Followers and summons look like monsters to `near`; their owner tag is
        only in memory. Matched by packet key (npc id, instance)."""
        if now - run.followers_at < FOLLOWERS_EVERY:
            return
        run.followers_at = now
        try:
            objects, _own = self._read_locked(pid, read_nearby) or ([], None)
        except Exception:
            return
        run.followers = {(o.npc_id, o.instance) for o in objects if o.tag}

    def _count_kills(self, run: _Run, objs: list[dict]) -> None:
        """A dead monster we hit counts once. Its hits are dropped then, and a
        body is remembered only while it is in view: a respawn may reuse the key."""
        dead = {(o.get("id"), o.get("inst")) for o in objs if o.get("dead")}
        with run.fighter.lock:
            for key in dead - run.counted:
                if run.fighter.hits.pop(key, None) is not None:
                    run.fighter.hit_skills.pop(key, None)
                    run.counted.add(key)
                    run.kills += 1
        run.counted &= dead
