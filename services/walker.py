"""Click-to-walk runner: walks a character to a tile with background mouse input.

One job per PID, in two phases:

1. Drag. Press the left button on the plan's first hop and keep posting
   WM_MOUSEMOVE with the button held, the cursor DRAG_AHEAD tiles ahead toward
   the next waypoint (services/walk_path.py). Live 2026-10-02: every such move
   re-targets the character, so it walks without the stop a click leg ends
   with, and dragging the cursor over an NPC does not open it (only a press
   can). Holding ~1 s switches the game into a follow-the-cursor mode that
   stays on after the button is released and is cancelled by one more click;
   so a drag always ends with a release AND a cancel click on the character
   itself, which neither moves it nor opens anything.
2. Clicks. The walk ends with click legs: re-plan from where the character
   is, click the first hop, check the move target, repeat until arrived. The
   click phase also takes over whenever the drag stalls.

The move target (HP+636/+640) is the check for every input: after a click or
the press it must become the hop, while dragging it must stay near the
cursor. The runner stops at the first sign of trouble rather than guessing:

- target unchanged after a click or press: it hit an NPC, a player or a UI
  panel. An NPC dialog is left open on purpose. Esc with nothing open brings
  up the log-out menu, so it is never sent blindly.
- the stage changes or the tile jumps (within a leg or between legs): a teleport.
- the target goes somewhere we did not point: the player took over.
- no progress for a few legs: stuck (or tiles frozen after a teleport).
"""

from __future__ import annotations

import ctypes
import threading
import time
from collections.abc import Callable
from typing import Protocol

from services import walk_path
from services.api_types import WalkPoint, WalkStatus

Sample = tuple[int, int, int, int, int]  # stage_id, x, y, target_px, target_py
Tile = tuple[int, int]

TILE_PX = 40
CLICK_TAKE_S = 0.6  # the move target updates within ~50 ms of a click
STEP_S = 0.25  # one tile takes ~215 ms walking
ARRIVE_SLACK_S = 1.5
STALL_S = 1.0  # no tile change for this long: the leg (or the drag) is over
MAX_LEGS = 120
MAX_IDLE_LEGS = 3  # click legs in a row that did not get closer to the goal
JUMP_TILES = 3  # moving further than this between two samples is a teleport
POLL_S = 0.03

DRAG_AHEAD = 2  # tiles between the character and the cursor while dragging
DRAG_MOVE_S = 0.1  # how often the held cursor is re-posted
DRAG_HANDOFF = 3  # end the drag once the goal is this close; clicks finish it
DRAG_OFF_TILES = 3  # target this far from the cursor means it is not ours
DRAG_OFF_S = 0.5  # ...for this long
DRAG_MAX_S = 180.0
END_DRAG_TRIES = 3

# Where the mouse rests, and where the cancel click lands: the character itself
# (the camera keeps it centred), away from the bottom bar whose panels expand
# on hover.
REST_POINT = (walk_path.CENTER_X, walk_path.CENTER_Y)

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001

RELEASE_FAILED = "could not end the drag: follow-the-cursor mode may still be on"


class Mouse(Protocol):
    """Background mouse input in the game's 800x600 client space."""

    def click(self, x: int, y: int) -> bool: ...
    def press(self, x: int, y: int) -> bool: ...
    def drag(self, x: int, y: int) -> bool: ...
    def end_drag(self, x: int, y: int) -> bool:
        """Release the button at (x, y), then click the character to leave follow mode."""
        ...


class Win32Mouse:  # pragma: no cover - Win32 only
    """PostMessage input to one game window.

    The window is looked up once (at the first input) and kept, so the release
    and cancel of a drag reach the same window as its press even if the game
    opens another top-level window in between.
    """

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.hwnd = 0

    def _post(self, steps: list[tuple[int, int, int, int]]) -> bool:
        from services.auto_click import _resolve_hwnd, _scale_coord

        if not self.hwnd:
            self.hwnd = _resolve_hwnd(self.pid)
        if not self.hwnd:
            return False
        user32 = ctypes.windll.user32
        ok = True
        for msg, wparam, x, y in steps:
            sx, sy = _scale_coord(self.hwnd, x, y)
            ok = (
                bool(user32.PostMessageW(self.hwnd, msg, wparam, (sy << 16) | (sx & 0xFFFF))) and ok
            )
            time.sleep(0.03)
        return ok

    def click(self, x: int, y: int) -> bool:
        return self._post(
            [
                (WM_MOUSEMOVE, 0, x, y),
                (WM_LBUTTONDOWN, MK_LBUTTON, x, y),
                (WM_LBUTTONUP, 0, x, y),
                (WM_MOUSEMOVE, 0, *REST_POINT),
            ]
        )

    def press(self, x: int, y: int) -> bool:
        return self._post([(WM_MOUSEMOVE, 0, x, y), (WM_LBUTTONDOWN, MK_LBUTTON, x, y)])

    def drag(self, x: int, y: int) -> bool:
        return self._post([(WM_MOUSEMOVE, MK_LBUTTON, x, y)])

    def end_drag(self, x: int, y: int) -> bool:
        return self._post(
            [
                (WM_LBUTTONUP, 0, x, y),
                (WM_MOUSEMOVE, 0, *REST_POINT),
                (WM_LBUTTONDOWN, MK_LBUTTON, *REST_POINT),
                (WM_LBUTTONUP, 0, *REST_POINT),
            ]
        )


def _target_tile(s: Sample) -> Tile:
    return s[3] // TILE_PX, s[4] // TILE_PX


def _cheb(a: Tile, b: Tile) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def _ahead(here: Tile, toward: Tile, tiles: int) -> Tile:
    """The tile `tiles` steps from `here` on the straight line to `toward` (or `toward`)."""
    dx, dy = toward[0] - here[0], toward[1] - here[1]
    if _cheb(here, toward) <= tiles:
        return toward
    f = tiles / max(abs(dx), abs(dy))
    return here[0] + round(dx * f), here[1] + round(dy * f)


class WalkJob:
    def __init__(
        self,
        pid: int,
        goal: Tile,
        sample: Callable[[], Sample | None],
        mouse: Mouse,
        plan: Callable[[int, Tile, Tile], walk_path.WalkPlanResult] = walk_path.plan,
        safe: Callable[[int, Tile], frozenset[Tile]] = walk_path.safe_cells,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        drag: bool = True,
    ) -> None:
        self.pid = pid
        self.goal = goal
        self._sample = sample
        self._mouse = mouse
        self._plan = plan
        self._safe = safe
        self._sleep = sleep
        self._clock = clock
        self._use_drag = drag
        self._stop = threading.Event()
        self.state = "walking"
        self.message: str | None = None
        self.legs = 0
        self._cursor = REST_POINT  # last client point the held button was moved to
        self._last: Tile | None = None  # last position seen, to catch jumps between legs
        self._thread = threading.Thread(target=self.run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float) -> None:
        if self._thread.is_alive():
            self._thread.join(timeout)

    def status(self) -> WalkStatus:
        state, message = self.state, self.message
        return WalkStatus(
            state=state,
            goal=WalkPoint(x=self.goal[0], y=self.goal[1]),
            legs=self.legs,
            message=message,
        )

    def _end(self, state: str, message: str | None = None) -> None:
        self.message = message  # message first: a poll in between must not see a bare state
        self.state = state

    def run(self) -> None:
        try:
            self._walk()
        except Exception as exc:  # never let the thread die silently
            self._end("failed", f"error: {exc}")

    def _moved_too_far(self, here: Tile) -> bool:
        jumped = self._last is not None and _cheb(here, self._last) > JUMP_TILES
        self._last = here
        return jumped

    def _walk(self) -> None:
        first = self._sample()
        if first is None:
            return self._end("failed", "character position is not readable")
        stage = first[0]
        self._last = (first[1], first[2])
        if self._use_drag:
            problem = self._drag_phase(stage)
            if problem is not None:
                return self._end("stopped" if problem == "stopped" else "failed", problem)
        best = None  # closest Chebyshev distance to the goal seen so far
        idle = 0
        while self.legs < MAX_LEGS:
            if self._stop.is_set():
                return self._end("stopped", "stopped")
            s = self._sample()
            if s is None:
                return self._end("failed", "character position is not readable")
            if s[0] != stage:
                return self._end("failed", "map changed")
            here = (s[1], s[2])
            if self._moved_too_far(here):
                return self._end("failed", "teleported")
            dist = _cheb(here, self.goal)
            if best is None or dist < best:
                best, idle = dist, 0
            else:
                idle += 1
                if idle >= MAX_IDLE_LEGS:
                    return self._end("failed", "stuck: not getting closer")
            plan = self._plan(stage, here, self.goal)
            if not plan.hops:
                if plan.reason is None:
                    return self._end("done")
                return self._end("failed", plan.reason)
            hop = plan.hops[0]
            before = _target_tile(s)
            if not self._mouse.click(*walk_path.click_point(here, hop)):
                return self._end("failed", "game window not found")
            self.legs += 1
            outcome = self._follow(stage, here, hop, before)
            if outcome is not None:
                return self._end("failed", outcome)
        return self._end("failed", "too many steps")

    # ---- drag phase -------------------------------------------------------

    def _drag_phase(self, stage: int) -> str | None:
        """Drag along the plan's waypoints until close to the goal.

        None when the click phase should take over (handed off, stalled, or
        nothing worth dragging); otherwise why the walk must stop.
        """
        s = self._sample()
        if s is None:
            return "character position is not readable"
        here = (s[1], s[2])
        plan = self._plan(stage, here, self.goal)
        if not plan.hops or _cheb(here, self.goal) <= DRAG_HANDOFF:
            return None
        waypoints = list(plan.hops)
        # The press is a click, so it goes where a click leg would: the first
        # hop is already clear of NPC sprites, the UI and teleport rings.
        aim = waypoints[0]
        before = _target_tile(s)
        self._cursor = walk_path.click_point(here, aim)
        if not self._mouse.press(*self._cursor):
            return "game window not found"
        self.legs += 1
        problem: str | None = None
        try:
            problem = self._drag_follow(stage, here, aim, before, waypoints)
        finally:
            if not self._end_drag():
                problem = RELEASE_FAILED if problem is None else f"{problem}; {RELEASE_FAILED}"
        if problem is None:
            self._settle()
        return problem

    def _end_drag(self) -> bool:
        for _ in range(END_DRAG_TRIES):
            try:
                if self._mouse.end_drag(*self._cursor):
                    return True
            except Exception:
                pass
            self._sleep(0.1)
        return False

    def _drag_aim(self, here: Tile, waypoint: Tile, safe: frozenset[Tile]) -> Tile | None:
        """A cursor tile toward `waypoint` that is walkable and outside teleport rings."""
        for k in range(DRAG_AHEAD, 0, -1):
            aim = _ahead(here, waypoint, k)
            if not safe or aim in safe:
                return aim
        return None

    def _drag_follow(self, stage, start, aim, before, waypoints) -> str | None:
        t0 = self._clock()
        while self._clock() - t0 < CLICK_TAKE_S:
            s = self._sample()
            if (
                s is not None
                and _target_tile(s) == aim
                and (aim != before or (s[1], s[2]) != start)
            ):
                break
            self._sleep(POLL_S)
        else:
            s = self._sample()
            if s is not None and (s[1], s[2]) != start and _target_tile(s) != aim:
                return "clicked an NPC or player instead of the ground"
            if s is None or _target_tile(s) != aim:
                return "click was not taken (UI in the way?)"
        safe = self._safe(stage, self.goal)
        last, last_change = start, self._clock()
        off_since = None
        next_move = self._clock()
        while self._clock() - t0 < DRAG_MAX_S:
            if self._stop.is_set():
                return "stopped"
            s = self._sample()
            if s is None:
                return "character position is not readable"
            if s[0] != stage:
                return "teleported to another map"
            here = (s[1], s[2])
            if self._moved_too_far(here):
                return "teleported"
            if here != last:
                last, last_change = here, self._clock()
            elif self._clock() - last_change > STALL_S:
                return None  # stalled: clicks take over from here
            while len(waypoints) > 1 and _cheb(here, waypoints[0]) <= 1:
                waypoints.pop(0)
            if _cheb(here, self.goal) <= DRAG_HANDOFF:
                return None
            if _cheb(_target_tile(s), aim) > DRAG_OFF_TILES:
                off_since = off_since if off_since is not None else self._clock()
                if self._clock() - off_since > DRAG_OFF_S:
                    return "the player clicked somewhere else"
            else:
                off_since = None
            if self._clock() >= next_move:
                nxt = self._drag_aim(here, waypoints[0], safe)
                if nxt is None:
                    return None  # no safe cursor tile: clicks take over
                aim = nxt
                self._cursor = walk_path.click_point(here, aim)
                if not self._mouse.drag(*self._cursor):
                    return "game window not found"
                next_move = self._clock() + DRAG_MOVE_S
            self._sleep(POLL_S)
        return None

    def _settle(self) -> None:
        """After the drag ends the character may finish a step; wait for it to stop."""
        s = self._sample()
        if s is None:
            return
        last, last_change = (s[1], s[2]), self._clock()
        deadline = self._clock() + ARRIVE_SLACK_S + DRAG_OFF_TILES * STEP_S
        while self._clock() < deadline:
            s = self._sample()
            if s is None:
                return
            here = (s[1], s[2])
            if here == _target_tile(s):
                return
            if here != last:
                last, last_change = here, self._clock()
            elif self._clock() - last_change > STALL_S:
                return
            self._sleep(POLL_S)

    # ---- click legs -------------------------------------------------------

    def _follow(self, stage, start, hop, before) -> str | None:
        """Watch one click leg. None when it ended normally (arrived or stalled)."""
        t0 = self._clock()
        taken = False
        while self._clock() - t0 < CLICK_TAKE_S:
            s = self._sample()
            if s is not None and _target_tile(s) == hop:
                taken = True
                break
            self._sleep(POLL_S)
        if not taken:
            s = self._sample()
            if s is not None and (s[1], s[2]) != start:
                return "clicked an NPC or player instead of the ground"
            if s is not None and _target_tile(s) != before:
                return "the game went somewhere else (camera offset?)"
            return "click was not taken (UI in the way?)"
        deadline = t0 + _cheb(start, hop) * STEP_S + ARRIVE_SLACK_S
        last, last_change = start, self._clock()
        while self._clock() < deadline:
            if self._stop.is_set():
                return None
            s = self._sample()
            if s is None:
                return "character position is not readable"
            if s[0] != stage:
                return "teleported to another map"
            here = (s[1], s[2])
            if self._moved_too_far(here):
                return "teleported"
            if _target_tile(s) != hop:
                return "the player clicked somewhere else"
            if here == hop:
                return None
            if here != last:
                last, last_change = here, self._clock()
            elif self._clock() - last_change > STALL_S:
                return None
            self._sleep(POLL_S)
        return None


class WalkManager:
    """One walk job per PID. Starting a walk ends (and waits out) the previous one."""

    JOIN_S = 5.0  # a drag ends within one poll plus the release; this is generous

    def __init__(
        self,
        sample: Callable[[int], Sample | None],
        mouse: Callable[[int], Mouse] = Win32Mouse,
    ) -> None:
        self._sample = sample
        self._mouse = mouse
        self._jobs: dict[int, WalkJob] = {}
        self._lock = threading.Lock()

    def start(self, pid: int, goal: Tile) -> WalkStatus:
        with self._lock:
            old = self._jobs.get(pid)
            if old is not None:
                old.stop()
                old.join(self.JOIN_S)
            job = WalkJob(pid, goal, sample=lambda: self._sample(pid), mouse=self._mouse(pid))
            self._jobs[pid] = job
            job.start()
            return job.status()

    def stop(self, pid: int) -> None:
        with self._lock:
            job = self._jobs.get(pid)
        if job is not None:
            job.stop()

    def status(self, pid: int) -> WalkStatus:
        with self._lock:
            job = self._jobs.get(pid)
        if job is None:
            return WalkStatus(state="idle")
        return job.status()

    def shutdown(self) -> None:
        """Stop every walk and wait, so each drag gets its release and cancel click."""
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            job.stop()
        for job in jobs:
            job.join(self.JOIN_S)
