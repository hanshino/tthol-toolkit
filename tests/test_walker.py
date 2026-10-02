"""Tests for the click-to-walk runner against a simulated game."""

from services import walk_path as wp
from services.walk_path import MapGrid
from services.walker import RELEASE_FAILED, WalkJob, WalkManager

OPEN = MapGrid(40, 30, {(x, y) for x in range(40) for y in range(30)}, [], [])


class FakeGame:
    """Character that walks one tile per sample toward its move target."""

    def __init__(self, x, y, stage=53):
        self.stage, self.x, self.y = stage, x, y
        self.tx, self.ty = x, y
        self.t = 0.0
        self.clicks = []
        self.on_click = None  # hook to misbehave
        self.frozen = False  # walking somewhere the move target does not show

    def sample(self):
        if not self.frozen and (self.x, self.y) != (self.tx, self.ty):
            self.x += (self.tx > self.x) - (self.tx < self.x)
            self.y += (self.ty > self.y) - (self.ty < self.y)
        return self.stage, self.x, self.y, self.tx * 40 + 20, self.ty * 40 + 19

    def _dest(self, sx, sy):
        return self.x + (sx - 400) // 40, self.y - (sy - 300) // 40

    def click(self, sx, sy):
        self.clicks.append((sx, sy))
        dest = self._dest(sx, sy)
        if self.on_click is None or self.on_click(dest) is not False:
            self.tx, self.ty = dest
        return True

    def press(self, sx, sy):
        self.presses = getattr(self, "presses", 0) + 1
        self.held = True
        return self.click(sx, sy)

    def drag(self, sx, sy):
        assert self.held
        self.drags = getattr(self, "drags", 0) + 1
        self.tx, self.ty = self._dest(sx, sy)
        return True

    def end_drag(self, sx, sy):
        self.held = False
        self.ended = getattr(self, "ended", 0) + 1
        self.tx, self.ty = self.x, self.y  # the cancel click lands on the character
        return True

    def sleep(self, s):
        self.t += s

    def clock(self):
        return self.t


def job(game, goal, grid=OPEN, drag=False):
    return WalkJob(
        1,
        goal,
        sample=game.sample,
        mouse=game,
        plan=lambda _stage, a, b: wp.plan_on(grid, a, b),
        safe=lambda _stage, g: frozenset(grid.walkable - wp._teleport_cells(grid, g)),
        sleep=game.sleep,
        clock=game.clock,
        drag=drag,
    )


def test_walks_to_the_goal_over_several_clicks():
    g = FakeGame(2, 15)
    j = job(g, (37, 15))
    j.run()
    assert (j.state, j.message) == ("done", None)
    assert (g.x, g.y) == (37, 15)
    assert len(g.clicks) >= 4  # 35 tiles at <= 9 per click
    for sx, sy in g.clicks:
        assert wp.clickable(sx, sy)


def test_npc_hit_stops_without_retrying():
    g = FakeGame(2, 15)

    def npc(dest):  # walks next to an NPC instead; move target unchanged
        g.x += 1
        g.frozen = True
        return False

    g.on_click = npc
    j = job(g, (20, 15))
    j.run()
    assert j.state == "failed" and "NPC" in j.message
    assert len(g.clicks) == 1


def test_click_swallowed_by_ui_stops():
    g = FakeGame(2, 15)
    g.on_click = lambda dest: False
    j = job(g, (20, 15))
    j.run()
    assert j.state == "failed" and "not taken" in j.message


def test_teleport_mid_leg_stops():
    g = FakeGame(2, 15)

    def portal(dest):
        g.tx, g.ty = dest
        g.x, g.y = 30, 3  # sent across the map
        g.tx, g.ty = dest
        return False

    g.on_click = portal
    j = job(g, (20, 15))
    j.run()
    assert j.state == "failed" and "teleport" in j.message


def test_player_takes_over_stops():
    g = FakeGame(2, 15)

    def other(dest):
        g.tx, g.ty = 2, 25  # player clicked elsewhere right after us
        return False

    g.on_click = other
    j = job(g, (20, 15))
    j.run()
    assert j.state == "failed"


def test_map_change_and_unreachable():
    g = FakeGame(2, 15)
    g.on_click = lambda dest: setattr(g, "stage", 54)
    j = job(g, (20, 15))
    j.run()
    assert j.state == "failed" and "map" in j.message
    walled = MapGrid(20, 5, {(x, y) for x in range(20) for y in range(5) if x != 10}, [], [])
    j2 = job(FakeGame(2, 2), (15, 2), grid=walled)
    j2.run()
    assert j2.state == "failed" and j2.message == "target is not reachable on foot"


def test_stop_before_start():
    g = FakeGame(2, 15)
    j = job(g, (37, 15))
    j.stop()
    j.run()
    assert j.state == "stopped" and g.clicks == []


def test_drag_walks_most_of_the_way_then_clicks_finish():
    g = FakeGame(2, 15)
    j = job(g, (37, 15), drag=True)
    j.run()
    assert (j.state, j.message) == ("done", None)
    assert (g.x, g.y) == (37, 15)
    assert g.presses == 1 and g.drags > 5
    assert not g.held and g.ended == 1  # released and follow mode cancelled
    assert len(g.clicks) <= 3  # the press plus a short click finish


def test_drag_press_on_npc_stops():
    g = FakeGame(2, 15)

    def npc(dest):
        g.x += 1
        g.frozen = True
        return False

    g.on_click = npc
    j = job(g, (37, 15), drag=True)
    j.run()
    assert j.state == "failed" and "NPC" in j.message
    assert not g.held


def test_drag_stop_releases_the_button():
    g = FakeGame(2, 15)
    j = job(g, (37, 15), drag=True)
    original = g.drag

    def drag_then_stop(sx, sy):
        j.stop()
        return original(sx, sy)

    g.drag = drag_then_stop
    j.run()
    assert j.state == "stopped" and not g.held


def test_short_walk_skips_the_drag():
    g = FakeGame(10, 10)
    j = job(g, (12, 11), drag=True)
    j.run()
    assert j.state == "done" and getattr(g, "presses", 0) == 0


def test_failed_release_is_retried_and_reported():
    g = FakeGame(2, 15)
    calls = []

    def flaky(sx, sy):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("window busy")
        return False

    g.end_drag = flaky
    j = job(g, (37, 15), drag=True)
    j.run()
    assert len(calls) == 3
    assert j.state == "failed" and RELEASE_FAILED in j.message


def test_teleport_during_drag_still_ends_the_drag():
    g = FakeGame(2, 15)
    original = g.drag
    n = []

    def drag(sx, sy):
        n.append(1)
        if len(n) == 4:
            g.x, g.y = 30, 3
            g.tx, g.ty = 30, 3
        return original(sx, sy)

    g.drag = drag
    j = job(g, (37, 15), drag=True)
    j.run()
    assert j.state == "failed" and j.message == "teleported"
    assert g.ended == 1 and not g.held


def test_teleport_between_click_legs_stops():
    g = FakeGame(2, 15)
    original = g.click

    def click(sx, sy):
        if g.clicks:  # second leg: the character was moved since the last one
            g.x, g.y = 30, 3
            g.tx, g.ty = 30, 3
        return original(sx, sy)

    g.click = click
    j = job(g, (37, 15))
    j.run()
    assert j.state == "failed"


def test_drag_cursor_stays_out_of_teleport_rings():
    door = [(20, 17, 9)]  # beside the straight line along y=15
    grid = MapGrid(40, 30, {(x, y) for x in range(40) for y in range(30)}, door, [])
    ring = wp._teleport_cells(grid, (37, 15))
    g = FakeGame(2, 15)
    aims = []
    original = g.drag

    def drag(sx, sy):
        aims.append(g._dest(sx, sy))
        return original(sx, sy)

    g.drag = drag
    j = job(g, (37, 15), grid=grid, drag=True)
    j.run()
    assert j.state == "done"
    assert aims and not [a for a in aims if a in ring]


class _Mice:
    def __init__(self):
        self.games = {}

    def __call__(self, pid):
        return self.games[pid]


def test_manager_replaces_a_running_walk_and_shuts_down():
    import threading

    gate = threading.Event()
    g = FakeGame(2, 15)
    sample = g.sample

    def slow_sample(_pid):
        gate.wait(0.01)
        return sample()

    mice = _Mice()
    mice.games[1] = g
    mgr = WalkManager(sample=slow_sample, mouse=mice)
    first = mgr.start(1, (37, 15))
    assert first.state == "walking"
    second = mgr.start(1, (5, 15))  # waits the first one out
    assert second.goal.x == 5
    mgr.shutdown()
    assert mgr.status(1).state in ("done", "stopped", "failed")
    assert not getattr(g, "held", False)
