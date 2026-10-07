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
  a door. Started inside a manor, the map tells the manor (the family's 0x31
  may not have come in), and the 家族馬夫 rides to whichever of its places
  is closest to the destination.

A walk that lands somewhere it was not going (a warp zone on the way to an
NPC, a door into another room) is not an error: the route is planned again
from where the character is.

The position is always `near`'s own pixels / 40: `status.tile` keeps the
pre-teleport tile after a teleport inside a map.
"""

from __future__ import annotations

import logging
import math
import random
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
# A teleport inside a map is told by the walkable region changing: walking
# never crosses regions, and a door always does (its graph edge joins two).
# Distance alone misleads both ways: a fast character (疾風身法) covers 5+
# tiles a poll (live 2026-10-06), while some doors move only 2-4 tiles.
# JUMP is the fallback on maps without walkability data.
JUMP = 12  # tiles in one poll
MAP_WAIT = 15.0  # after reaching an exit zone, for the map to change
DIALOG_WAIT = 20.0
NPC_VIEW = 8  # tiles: closer than this, the NPC should be in `near`
TOUCH_RADIUS = 3  # tiles between a click zone and its map object
MAX_STEPS = 40
FAMILY_WAIT = 2.0  # for the 0x31 a `family` ask brings (it lands in ~30 ms)
EXIT_TRIES = 3  # walks to an exit zone before giving up on it
# Click the target again every few seconds (random, like a player) even while
# moving: a cast or a hit stops the walk short without it looking stalled yet.
RECLICK = (2.5, 5.0)
SETTLE = 1.5  # after a map change, before reading `near` again
# HP 0 this long in a row is a death. A map load reads 0 for a moment (live
# 2026-10-07: 聖火狂狐 stopped as dead entering 探幽曲徑 at full health).
DEATH_CONFIRM = 3.0
FREE_TRIES = 12  # reads (0.3 s apart) to get a window or dialog out of the way before a walk


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


class _Replan(Exception):
    """The character ended up off the planned route: plan again from there."""


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
        rng: random.Random | None = None,
        # pid -> the hook's `family` reply ({ok} / {ok: false, error}), or None
        # when the client cannot ask; the 0x31 it brings lands in `manor`.
        ask_family: Callable[[int], dict | None] | None = None,
    ) -> None:
        self._channel = channel
        self._ask_family = ask_family
        self._read_locked = read_locked
        self._read_stage = read_stage
        self._manor = manor
        self._clock = clock
        self._rng = rng or random.Random()
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
        self.zero_hp_at: float | None = None  # clock HP first read 0 (see DEATH_CONFIRM)

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
            now = self.nav._clock()
            if self.zero_hp_at is None:
                self.zero_hp_at = now
            if now - self.zero_hp_at >= DEATH_CONFIRM:
                raise _Stop("dead", "角色死亡")
            return None  # a map load, until it holds: position unknown for now
        self.zero_hp_at = None
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

    def learn_manor(self) -> None:
        """No manor known yet: ask the hook for the family before planning, or a
        family character takes the long road (live 2026-10-06: 檀泉別苑 rode to
        天外天境 and walked six maps; the 0x31 with manor 1121 came 24 s later)."""
        if self.manor is not None or self.nav._ask_family is None:
            return
        try:
            reply = self.nav._ask_family(self.pid)
        except Exception as e:
            log.info(
                "navigator pid=%d family ask failed: %s", self.pid, e, extra={"cat": "navigator"}
            )
            return
        if not reply or not reply.get("ok"):
            return  # no family, or the client cannot ask
        deadline = self.nav._clock() + FAMILY_WAIT
        while self.nav._clock() < deadline:
            manor = self.nav._manor(self.pid)
            if manor is not None:
                self.manor = manor
                self.script = rp._Script(rp._tables(), self.level, manor)
                return
            self.wait(0.1)
        log.info(
            "navigator pid=%d no 0x31 after the family ask", self.pid, extra={"cat": "navigator"}
        )

    def go(self, dest: int, goal: Tile | None) -> NavResult:
        self.learn_manor()
        for _ in range(MAX_STEPS):
            here, pos = self.settle()
            if here != dest and here in rp.manor_stages():
                self.adopt_manor(here)
                try:
                    self.leave_manor(dest)
                except _Replan:
                    self.note("走到別的地方了，重新找路")
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
            try:
                self.take(step, here)
            except _Replan:
                self.note("走到別的地方了，重新找路")
        raise _Stop("stuck", "換圖次數太多，停止導航")

    def adopt_manor(self, here: int) -> None:
        """Standing in a manor: it is the family's (0x31 may be missing or old)."""
        if self.manor == here:
            return
        log.info("navigator pid=%d takes manor %s from the map", self.pid, here)
        self.manor = here
        self.script = rp._Script(rp._tables(), self.level, here)

    def region(self, stage: int | None, tile: Tile | None) -> int | None:
        if stage is None or tile is None:
            return None
        kind = "sestage" if stage in rp.manor_stages() else "stage"
        return rp._regions()(stage, tile, kind)

    def off_route(self, stage: int, target: Tile) -> None:
        """After a teleport on the way to `target`: replan unless still in its space."""
        here, pos = self.settle()
        if here != stage:
            raise _Replan()
        mine, theirs = self.region(here, pos), self.region(stage, target)
        if mine is not None and theirs is not None and mine != theirs:
            raise _Replan()

    def describe(self, step: rp.Step) -> str:
        to = self.name(step.dst)
        return {
            "walk": f"走出口到{to}",
            "door": "走傳點",
            "town": f"找 NPC 前往{to}",
            "family": f"找家族總管，經莊園家族馬夫前往{to}",
        }[step.kind] + ("（點擊）" if step.touch else "")

    def take(self, step: rp.Step, here: int) -> None:
        if step.kind == "door":
            if step.touch and self.touch_zone(step.at, here):
                return
            r = self.walk_to(step.at, here, slack=0)
            if r == "map" or r == "jump" or self.jumped(self.me()):
                return
            raise _Stop("stuck", f"傳點 {step.at} 沒有傳送")
        if step.kind == "walk":
            if step.touch and self.touch_zone(step.at, here):
                return
            for _ in range(EXIT_TRIES):
                r = self.walk_to(step.at, here, slack=0)
                if r == "map":
                    return  # the exit, or another one on the way: the next plan sorts it out
                if r == "jump":
                    self.off_route(here, step.at)
                    continue  # a stale read: still in the exit's space
                if self.wait_map(here, MAP_WAIT) is not None:
                    return
                # Short of the zone (a bump stopped us): walk on.
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
        ride_to = dest
        if horse is None:
            ride_to, horse = self.nearest_ride(dest) or (dest, None)
        tile = _manor_npc_tile(manor, horse) if horse else None
        if horse is None or tile is None:
            raise _Stop("no-route", f"莊園的家族馬夫到不了{self.name(dest)}附近")
        if ride_to != dest:
            self.note(f"莊園的家族馬夫先送到{self.name(ride_to)}")
        pos = self.me()
        hops = rp.room_doors("sestage", manor, pos, tile) if pos else None
        for zone, _touch in hops or []:
            if self.walk_to(zone, None, slack=0) != "jump" and not self.jumped(self.me()):
                raise _Stop("stuck", "莊園裡的門沒有傳送")
        self.ride(horse, tile, ride_to, manor)

    def nearest_ride(self, dest: int) -> tuple[int, int] | None:
        """(place, 家族馬夫) in this manor whose ride leaves the least route to `dest`."""
        t = rp._tables()
        graph = rp.cached_graph(self.level, self.manor)
        best: tuple[float, int, int] | None = None
        for d, tag, horse in rp._family_dests(self.script):
            if _manor_npc_tile(self.manor, horse) is None:
                continue
            if d == dest:
                cost = 0.0
            else:
                route = rp.plan(graph, d, dest, t.middle(d, "trigger", tag))
                if route is None:
                    continue
                cost = route.cost
            if best is None or cost < best[0]:
                best = (cost, d, horse)
        return (best[1], best[2]) if best else None

    # -- moves -------------------------------------------------------------------

    def ensure_free(self) -> None:
        """No walking while a shop / warehouse window or an NPC dialog is open:
        the character cannot move then (user, 2026-10-07), and a walk packet
        sent anyway leaves the client and the server disagreeing. Close the
        window (hook closepanel), push a dialog without options to its end, or
        stop."""
        for _ in range(FREE_TRIES):
            shop, ware = self.cmd("shop"), self.cmd("warehouse")
            panel = bool(shop.get("open")) or bool(ware.get("open"))
            d = self.cmd("dialog")
            if not panel and not d.get("open"):
                return
            if panel:
                r = self.cmd("closepanel")
                if not r.get("ok") and str(r.get("error") or "") != "no window open":
                    raise _Stop("busy", "商店或倉庫視窗開著，這個 hook 關不掉（沒有 closepanel）")
            elif d.get("options") and not d.get("waiting"):
                raise _Stop("busy", "NPC 對話還開著而且要選選項，先不走路")
            elif not d.get("waiting"):
                self.cmd("next")
            self.wait(0.3)
        raise _Stop("busy", "視窗或 NPC 對話一直關不掉，先不走路")

    def walk_to(self, tile: Tile, src: int | None, slack: int = ARRIVE_SLACK) -> str:
        """arrived / jump (teleported on this map) / map (the stage changed) / stuck.
        The target is clicked again every few seconds, and at once when the walk stalls."""
        self.ensure_free()
        line = f"walk {tile[0] * TILE_PX + TILE_PX // 2} {tile[1] * TILE_PX + TILE_PX // 2}"
        start = self.me()  # so a zone on the very first steps still reads as a jump
        space = src if src is not None else self.stage()
        for _ in range(WALK_TRIES):
            r = self.cmd(line)
            if r.get("ok") and r.get("path"):
                break
            self.wait(0.5)
        end = self.nav._clock() + WALK_TIMEOUT
        last, still, resends = start, 0, 0
        reclick = self.nav._clock() + self.nav._rng.uniform(*RECLICK)
        while self.nav._clock() < end:
            self.wait(POLL)
            if src is not None and self.stage() not in (src, None):
                return "map"
            pos = self.me()
            if self.teleported(space, last, pos):
                log.info(
                    "walk jump pid=%d %s -> %s (%.1f tiles)",
                    self.pid,
                    last,
                    pos,
                    math.dist(last, pos),
                    extra={"cat": "navigator"},
                )
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
            elif self.nav._clock() >= reclick:
                self.cmd(line)
            if self.nav._clock() >= reclick:
                reclick = self.nav._clock() + self.nav._rng.uniform(*RECLICK)
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

    def teleported(self, stage: int | None, a: Tile | None, b: Tile | None) -> bool:
        """Did going from `a` to `b` on `stage` take a door rather than steps?"""
        if a is None or b is None or a == b:
            return False
        ra, rb = self.region(stage, a), self.region(stage, b)
        if ra is not None and rb is not None:
            return ra != rb
        return math.dist(a, b) > JUMP

    def jumped(self, before: Tile | None) -> bool:
        """Wait for a teleport on this map: a jump between two reads (walking is
        2 tiles a poll at most; a door moves us across the map)."""
        last = before
        space = self.stage()
        for _ in range(int(MAP_WAIT / 0.3)):
            p = self.me()
            if self.teleported(space, last, p):
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
        self.ensure_free()
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
            if self.teleported(here, last, p):
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
                for _ in range(3):
                    r = self.walk_to(at, here, slack=3)
                    if r == "map":
                        raise _Replan()  # stepped on a warp on the way
                    if r != "jump":
                        break
                    self.off_route(here, at)  # a door into another room, or a stale read
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
