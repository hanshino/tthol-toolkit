"""Walk a hooked character to a map (and a spot on it), for the 日常 modules.

No UI of its own: a module calls `Navigator.go` from its worker thread before
its own work (the tower module walks to 玄天之境 first). The route comes from
services/route_plan.py; this file drives it with hook commands and checks each
step against what the game reports:

- walk: `walk` to the exit zone, then wait for the map to change. A walk
  that stalls short of its target is sent again (user rule, 2026-10-06: a
  refused / stopped walk usually means "click the target again").
- door: same map, the zone teleports; done when the position jumps.
- touch: a zone that fires on a click (crane statues): `objects` lists the
  map objects `near` leaves out, `touch` clicks the one on the zone.
- town / family: talk to the NPC and pick, at each menu, the option whose
  DB jump leads to the destination. The 家族總管 sends to the family's own
  manor; there the 家族馬夫 that has the destination stands in a room behind
  a door.

The position is always `near`'s own pixels / 40: `status.tile` keeps the
pre-teleport tile after a teleport inside a map.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from services import route_plan as rp
from services.hook_cmd import NoReply, PipeBusy, PipeGone

log = logging.getLogger("tthol.navigator")

Tile = tuple[int, int]

TILE_PX = 40
POLL = 0.4
PIPE_RETRIES = 6
WALK_TRIES = 12  # `walk` is refused for a moment right after a map change
WALK_TIMEOUT = 120.0
STALL_POLLS = 5  # no movement for this many polls: send the target again
RESENDS = 5
ARRIVE_SLACK = 2
JUMP = 5  # tiles: further than this in one poll is a teleport
MAP_WAIT = 15.0  # after reaching an exit zone, for the map to change
DIALOG_WAIT = 20.0
NPC_VIEW = 8  # tiles: closer than this, the NPC should be in `near`
TOUCH_RADIUS = 3  # tiles between a click zone and its map object
MAX_STEPS = 40
SETTLE = 1.5  # after a map change, before reading `near` again


@dataclass
class NavResult:
    ok: bool
    reason: (
        str  # arrived / no-route / stuck / stopped / no-hook / dead / ... (user words in `detail`)
    )
    detail: str = ""
    stage: int | None = None
    tile: Tile | None = None


class _Stop(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def read_level(pm, hp_addr, _compat_mode) -> int | None:
    """等級 (HP-36) for WorkerManager.read_locked."""
    level = pm.read_int(hp_addr - 36)
    return level if 0 < level < 1000 else None


class Navigator:
    def __init__(
        self,
        channel,
        read_locked: Callable[[int, Callable], object],
        read_stage: Callable,  # read(pm, hp_addr, compat) -> (stage id, name) | None
        manor: Callable[[int], int | None],  # pid -> family manor sestage id
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[threading.Event | None, float], bool] | None = None,
    ) -> None:
        self._channel = channel
        self._read_locked = read_locked
        self._read_stage = read_stage
        self._manor = manor
        self._clock = clock
        self._sleep = sleep or (lambda ev, s: ev.wait(s) if ev is not None else time.sleep(s))

    # -- entry -------------------------------------------------------------------

    def go(
        self,
        pid: int,
        dest: int,
        goal: Tile | None = None,
        stop: threading.Event | None = None,
        note: Callable[[str], None] | None = None,
    ) -> NavResult:
        """Walk `pid` to map `dest` (and the space holding `goal`, then onto it)."""
        run = _Run(self, pid, stop, note or (lambda _t: None))
        try:
            return run.go(dest, goal)
        except _Stop as s:
            stage, tile = run.where()
            return NavResult(False, s.reason, s.detail, stage, tile)


class _Run:
    def __init__(self, nav: Navigator, pid: int, stop, note) -> None:
        self.nav = nav
        self.pid = pid
        self.stop = stop
        self.note = note
        level = nav._read_locked(pid, read_level)
        self.level = level if isinstance(level, int) else 1
        self.manor = nav._manor(pid)
        self.script = rp._Script(rp._tables(), self.level, self.manor)
        self.names = rp._tables().stages

    # -- plumbing ----------------------------------------------------------------

    def wait(self, secs: float) -> None:
        if self.nav._sleep(self.stop, secs):
            raise _Stop("stopped", "已停止")

    def cmd(self, line: str) -> dict:
        for _ in range(PIPE_RETRIES):
            if self.stop is not None and self.stop.is_set():
                raise _Stop("stopped", "已停止")
            try:
                return self.nav._channel.send(self.pid, line)
            except PipeGone:
                self.wait(1.0)  # gone for a moment while a map loads
            except (PipeBusy, NoReply):
                self.wait(0.3)
        raise _Stop("no-hook", "找不到 hook 指令通道（遊戲關了，或 hook 沒有注入）")

    def stage(self) -> int | None:
        try:
            s = self.nav._read_locked(self.pid, self.nav._read_stage)
        except Exception:
            return None
        return s[0] if s else None

    def me(self) -> Tile | None:
        st = self.cmd("status")
        hp = st.get("hp") or [1, 1]
        if hp[0] <= 0:
            raise _Stop("dead", "角色死亡")
        for o in self.cmd("near").get("objects") or []:
            if o.get("h") == st.get("self"):
                return o["x"] // TILE_PX, o["y"] // TILE_PX
        return None

    def where(self) -> tuple[int | None, Tile | None]:
        try:
            return self.stage(), self.me()
        except _Stop:
            return None, None

    def settle(self) -> tuple[int, Tile]:
        """Stage and position once both read (a map change blanks them a moment)."""
        end = self.nav._clock() + MAP_WAIT
        last: tuple[int, Tile] | None = None
        while self.nav._clock() < end:
            s, p = self.stage(), self.me()
            if s is not None and p is not None:
                # Right after a map change `near` can still give the old map's
                # position once: trust it when two reads agree.
                if last and last[0] == s and math.dist(last[1], p) <= 2:
                    return s, p
                last = (s, p)
            self.wait(0.3)
        raise _Stop("lost", "讀不到角色位置（換地圖太久）")

    def name(self, stage: int | None) -> str:
        return self.names.get(stage, f"#{stage}")

    # -- the route ---------------------------------------------------------------

    def go(self, dest: int, goal: Tile | None) -> NavResult:
        for _ in range(MAX_STEPS):
            here, pos = self.settle()
            if here == self.manor and here != dest:
                self.leave_manor(dest)
                continue
            graph = rp.cached_graph(self.level, self.manor)
            route = rp.plan(graph, here, dest, pos, goal)
            if route is None:
                raise _Stop("no-route", f"找不到從{self.name(here)}到{self.name(dest)}的路")
            if not route.steps:
                if goal is not None and not self.reach(goal):
                    raise _Stop("stuck", f"走不到目標格 {goal}")
                stage, tile = self.where()
                return NavResult(True, "arrived", f"抵達{self.name(dest)}", stage, tile)
            step = route.steps[0]
            self.note(self.describe(step))
            self.take(step, here)
        raise _Stop("stuck", "換圖次數太多，停止導航")

    def describe(self, step: rp.Step) -> str:
        to = self.name(step.dst)
        return {
            "walk": f"走出口到{to}",
            "door": "走傳點",
            "town": f"找 NPC 前往{to}",
            "family": f"找家族總管，經莊園家族馬夫前往{to}",
        }[step.kind] + ("（點擊）" if step.touch else "")

    def take(self, step: rp.Step, here: int) -> None:
        if step.kind in ("walk", "door"):
            if step.touch and self.touch_zone(step.at, here):
                return
            r = self.walk_to(step.at, here, slack=0)
            if r == "map":
                return
            if step.kind == "door":
                if r == "jump" or self.jumped(self.me()):
                    return
                raise _Stop("stuck", f"傳點 {step.at} 沒有傳送")
            if self.wait_map(here, MAP_WAIT) is None:
                raise _Stop("stuck", f"走到出口 {step.at} 沒有換圖")
        elif step.kind == "town":
            self.ride(step.npc_id, step.at, step.dst, here)
        else:
            self.ride(rp.STEWARD, step.at, "manor", here)
            self.settle()
            self.leave_manor(step.dst, step.horse)

    def leave_manor(self, dest: int, horse: int | None = None) -> None:
        """In the family manor: through its door to the 家族馬夫 that has `dest`."""
        manor = self.manor
        if horse is None:
            horse = next((h for d, _t, h in rp._family_dests(self.script) if d == dest), None)
        tile = _manor_npc_tile(manor, horse) if horse else None
        if horse is None or tile is None:
            raise _Stop("no-route", f"莊園的家族馬夫不能去{self.name(dest)}")
        pos = self.me()
        hops = rp.room_doors("sestage", manor, pos, tile) if pos else None
        for zone, _touch in hops or []:
            if self.walk_to(zone, None, slack=0) != "jump" and not self.jumped(self.me()):
                raise _Stop("stuck", "莊園裡的門沒有傳送")
        self.ride(horse, tile, dest, manor)

    # -- moves -------------------------------------------------------------------

    def walk_to(self, tile: Tile, src: int | None, slack: int = ARRIVE_SLACK) -> str:
        """arrived / jump (teleported on this map) / map (the stage changed) / stuck.
        A stalled walk is sent again."""
        line = f"walk {tile[0] * TILE_PX + TILE_PX // 2} {tile[1] * TILE_PX + TILE_PX // 2}"
        for _ in range(WALK_TRIES):
            r = self.cmd(line)
            if r.get("ok") and r.get("path"):
                break
            self.wait(0.5)
        end = self.nav._clock() + WALK_TIMEOUT
        last, still, resends = None, 0, 0
        while self.nav._clock() < end:
            self.wait(POLL)
            if src is not None and self.stage() not in (src, None):
                return "map"
            pos = self.me()
            if pos and last and math.dist(pos, last) > JUMP:
                return "jump"  # a door / zone teleported us: the old target is stale
            if pos and max(abs(pos[0] - tile[0]), abs(pos[1] - tile[1])) <= max(slack, 0):
                return "arrived"
            if pos and slack == 0 and max(abs(pos[0] - tile[0]), abs(pos[1] - tile[1])) <= 1:
                return "arrived"  # zone cells are often blocked: one off is on it
            still = still + 1 if pos == last else 0
            last = pos
            if still >= STALL_POLLS:
                resends += 1
                if resends > RESENDS:
                    log.info("walk stuck pid=%d at %s for %s", self.pid, pos, tile)
                    return "stuck"
                self.cmd(line)
                still = 0
        return "stuck"

    def reach(self, goal: Tile) -> bool:
        """Onto `goal` (within ARRIVE_SLACK); a "jump" right after a door is a stale read."""
        pos = self.me()
        if pos and max(abs(pos[0] - goal[0]), abs(pos[1] - goal[1])) <= ARRIVE_SLACK:
            return True
        for _ in range(3):
            r = self.walk_to(goal, None)
            if r == "arrived":
                return True
            if r == "stuck":
                return False
        return False

    def jumped(self, before: Tile | None) -> bool:
        """Wait for a teleport on this map: a jump between two reads (walking is
        2 tiles a poll at most; a door moves us across the map)."""
        last = before
        for _ in range(int(MAP_WAIT / 0.3)):
            p = self.me()
            if p and last and math.dist(p, last) > JUMP:
                return True
            last = p or last
            self.wait(0.3)
        return False

    def wait_map(self, src: int, timeout: float) -> int | None:
        end = self.nav._clock() + timeout
        while self.nav._clock() < end:
            s = self.stage()
            if s is not None and s != src:
                self.wait(SETTLE)
                return s
            self.wait(0.3)
        return None

    def touch_zone(self, zone: Tile, here: int) -> bool:
        """Click the map object on a click zone. False when none is near it."""
        self.walk_to(zone, here, slack=1)
        objs = self.cmd("objects").get("objects") or []
        near = [
            o
            for o in objs
            if math.dist((o["x"] // TILE_PX, o["y"] // TILE_PX), zone) <= TOUCH_RADIUS
        ]
        if not near:
            return False
        obj = min(near, key=lambda o: math.dist((o["x"] // TILE_PX, o["y"] // TILE_PX), zone))
        last = self.me()
        self.cmd(f"touch {obj['h']}")  # walks up to the object first when not next to it
        end = self.nav._clock() + MAP_WAIT
        while self.nav._clock() < end:
            self.wait(0.3)
            if self.stage() not in (here, None):
                self.wait(SETTLE)
                return True
            p = self.me()
            if p and last and math.dist(p, last) > JUMP:
                return True
            last = p or last
        raise _Stop("stuck", f"點了 {zone} 的物件沒有傳送")

    # -- NPCs --------------------------------------------------------------------

    def find(self, npc_id: int) -> dict | None:
        pos = self.me()
        best = None
        for o in self.cmd("near").get("objects") or []:
            if o.get("id") != npc_id:
                continue
            d = math.dist(pos, (o["x"] // TILE_PX, o["y"] // TILE_PX)) if pos else 0
            if best is None or d < best[0]:
                best = (d, o)
        return best[1] if best else None

    def ride(self, npc_id: int, at: Tile | None, dest, here: int) -> None:
        """Talk to `npc_id` and pick the options that lead to `dest` (a stage, or "manor")."""
        npc = self.find(npc_id)
        pos = self.me()
        if npc is None or (at and pos and math.dist(pos, at) > NPC_VIEW):
            if at:
                for _ in range(3):  # a "jump" here can be a stale read: walk on
                    if self.walk_to(at, here, slack=3) in ("arrived", "map", "stuck"):
                        break
            npc = self.find(npc_id)
        if npc is None:
            raise _Stop("stuck", f"找不到 NPC {npc_id}")
        self.cmd(f"talk {npc['h']}")
        r = self.dialog(npc_id, dest, here)
        if r == "map":
            return
        if self.wait_map(here, 6) is None:
            raise _Stop("stuck", f"和 NPC {npc_id} 對話後沒有傳送（{r}）")

    def leads(self, jump: int, dest) -> bool:
        warps = self.script.warps_from_msg(jump)
        if dest == "manor":
            return any(d == self.manor for d, _t in warps)
        return any(d == dest for d, _t in warps)

    def dialog(self, npc_id: int, dest, here: int) -> str:
        end = self.nav._clock() + DIALOG_WAIT
        seen = False
        while self.nav._clock() < end:
            self.wait(0.25)
            if self.stage() not in (here, None):
                return "map"
            d = self.cmd("dialog")
            if not d.get("open"):
                if seen:
                    return "closed"
                continue
            if (d.get("npc") or {}).get("id") != npc_id:
                continue  # still the previous NPC's dialog
            seen = True
            if d.get("waiting"):
                continue
            options = d.get("options") or []
            if options:
                pick = next((i for i, j in enumerate(options) if j and self.leads(j, dest)), None)
                if pick is None:
                    return "no-option"
                self.cmd(f"option {pick}")
                continue
            self.cmd("next")
        return "timeout"


def _manor_npc_tile(manor: int | None, npc_id: int) -> Tile | None:
    if manor is None:
        return None
    con = rp._connect(None)
    try:
        row = con.execute(
            "SELECT tile_x, tile_y FROM map_placements WHERE stage_kind = 'sestage'"
            " AND stage_id = ? AND category = 'npc' AND npc_id = ? LIMIT 1",
            (manor, npc_id),
        ).fetchone()
    finally:
        con.close()
    return (row[0], row[1]) if row else None
